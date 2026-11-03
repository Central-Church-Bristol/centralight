"""AI inputs: the guide and schema (generated from schema.py), the prompts, reading pasted replies,
checking an input from an AI, the Claude skill export, and a realistic chat reply end to end."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.ai_guide import (
    AIReplyError,
    check_input_data,
    create_prompt,
    describe_changes,
    edit_prompt,
    examples,
    export_skill,
    fix_message,
    guide_text,
    input_schema,
    outside_media,
    parse_reply,
    with_media,
    write_guide_files,
)
from centralight.bus import Instance, render_input
from centralight.elements import RenderContext
from centralight.expressions import FUNCTIONS
from centralight.model import GlobalVar
from centralight.new_show import create_show, show_from_patch
from centralight.sampler import CANVAS_H, CANVAS_W, sampler_for
from centralight.schema import SHAPE, TRANSITION, element_specs
from tests.gui_fixtures import with_inputs


def show_with_globals():
    return replace(with_inputs(show_from_patch()), globals=(GlobalVar("accent", "colour", "#C04DFF"), GlobalVar("speed", "number", 1.0, 0.0, 4.0)))


class GuideTests(unittest.TestCase):
    def test_every_setting_and_function_is_documented(self) -> None:
        show = show_with_globals()
        guide = guide_text(show)
        for kind in ("paint", "image", "video", "ndi"):
            for spec in element_specs(kind):
                self.assertIn(f"`{spec.path}`", guide, f"{kind} {spec.path}")
        for spec in SHAPE[1:]:
            self.assertIn(f"`{spec.path}`", guide)
        for name in FUNCTIONS:
            self.assertIn(f"`{name}(", guide)
        for fx in show.colour_fixtures():
            self.assertIn(f"`{fx.id}`", guide)
        self.assertIn("`accent`", guide)
        self.assertIn("automations/<id>.json", guide)

    def test_schema(self) -> None:
        show = show_with_globals()
        schema = input_schema(show)
        json.dumps(schema)  # plain JSON
        paint = next(k for k in schema["properties"]["tracks"]["items"]["items"]["oneOf"] if k["properties"]["kind"]["const"] == "paint")
        self.assertIn("fill", paint["properties"])
        self.assertIn("colour", paint["properties"]["fill"]["properties"])
        self.assertEqual(paint["properties"]["mask"]["properties"]["exclude"]["items"]["enum"], [fx.id for fx in show.colour_fixtures()])
        for spec in TRANSITION:
            self.assertIn(spec.path, schema["properties"]["transition"]["properties"])

    def test_the_examples_are_valid_inputs(self) -> None:
        show = show_with_globals()
        for text, example in examples(show):
            with self.subTest(example=example["id"]):
                inp, problems, _notes = check_input_data(json.loads(json.dumps(example)), show)
                self.assertEqual(problems, [], text)
                self.assertTrue(inp.ai)

    def test_files_in_the_show_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            show = create_show(Path(folder) / "Show")
            self.assertEqual(sorted(write_guide_files(show)), ["AGENTS.md", "AI_GUIDE.md", "input.schema.json"])
            self.assertEqual(write_guide_files(show), [])  # unchanged: nothing written
            show = replace(show, globals=(GlobalVar("mood", "number"),))
            self.assertEqual(sorted(write_guide_files(show)), ["AGENTS.md", "AI_GUIDE.md", "input.schema.json"][:2])
            self.assertIn("`mood`", (Path(folder) / "Show" / "AI_GUIDE.md").read_text(encoding="utf-8"))

    def test_skill_export(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            skill = export_skill(show_with_globals(), Path(folder))
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: centralight-inputs\ndescription: "))
            self.assertTrue((skill / "input.schema.json").is_file())
            self.assertGreaterEqual(len(list((skill / "examples").glob("*.json"))), 4)


class PromptTests(unittest.TestCase):
    def test_create_prompt_has_the_layout_and_the_request(self) -> None:
        show = show_with_globals()
        prompt = create_prompt(show, "the pillars breathing purple, slowly", "Pillars breathe")
        self.assertIn("the pillars breathing purple, slowly", prompt)
        self.assertIn("Name it: Pillars breathe", prompt)
        self.assertIn("slimpar-106", prompt)
        self.assertIn("```json", prompt)

    def test_edit_prompt_has_the_input_and_keeps_its_id(self) -> None:
        show = show_with_globals()
        prompt = edit_prompt(show, show.inputs["warm-white"], "make it a bit pinker")
        self.assertIn('"id": "warm-white"', prompt)
        self.assertIn("make it a bit pinker", prompt)
        self.assertIn('keep its "id" ("warm-white")', prompt)

    def test_fix_message(self) -> None:
        message = fix_message(["track 1, paint E1: mask.exclude names \"slimpar-999\", which isn't a light."])
        self.assertIn("slimpar-999", message)
        self.assertIn("```json", message)


class ReplyTests(unittest.TestCase):
    def test_bare_json(self) -> None:
        self.assertEqual(parse_reply('{"id": "x", "name": "X"}'), {"id": "x", "name": "X"})

    def test_fenced_with_words_around(self) -> None:
        reply = 'Here you go!\n\n```json\n{"id": "x", "name": "X"}\n```\n\nLet me know if you want changes.'
        self.assertEqual(parse_reply(reply)["id"], "x")

    def test_forgives_comments_and_trailing_commas(self) -> None:
        reply = '```json\n{\n  "id": "x", // the id\n  "name": "X",\n  "tracks": [[], [], [], [],],\n}\n```'
        data = parse_reply(reply)
        self.assertEqual(data["tracks"], [[], [], [], []])
        self.assertEqual(parse_reply('{"name": "http://example.com/a//b"}')["name"], "http://example.com/a//b")

    def test_unfenced_object_in_prose(self) -> None:
        self.assertEqual(parse_reply('Sure. {"id": "x", "name": "X"} That should do it.')["id"], "x")

    def test_problems(self) -> None:
        for reply in ("", "   ", "I'm sorry, I can't help with that.", "```json\nnot json\n```"):
            with self.subTest(reply=reply):
                with self.assertRaises(AIReplyError):
                    parse_reply(reply)
        with self.assertRaises(AIReplyError) as caught:
            parse_reply('[{"id": "a"}, {"id": "b"}]')
        self.assertIn("one input at a time", str(caught.exception))


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_with_globals()

    def paint(self, **extra) -> dict:
        element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF0000"}, **extra}
        return {"id": "new-look", "name": "New look", "tracks": [[element], [], [], []]}

    def problems(self, data: dict) -> list[str]:
        return check_input_data(data, self.show)[1]

    def test_good(self) -> None:
        inp, problems, notes = check_input_data(self.paint(opacity={"expr": "wave(time, speed)"}), self.show)
        self.assertEqual(problems, [])
        self.assertEqual(inp.id, "new-look")
        self.assertTrue(inp.ai)

    def test_unknown_light(self) -> None:
        problems = self.problems(self.paint(mask={"exclude": ["slimpar-999"]}))
        self.assertTrue(any("slimpar-999" in p for p in problems))

    def test_bad_and_unknown_expressions(self) -> None:
        self.assertTrue(any("can't be read" in p for p in self.problems(self.paint(opacity={"expr": "sin("}))))
        self.assertTrue(any('"tempo"' in p for p in self.problems(self.paint(opacity={"expr": "tempo * 2"}))))
        data = self.paint()
        data["locals"] = [{"name": "tempo", "type": "number", "value": 0.5}]
        data["tracks"][0][0]["opacity"] = {"expr": "tempo * 2"}
        self.assertEqual(self.problems(data), [])  # a local of its own is fine

    def test_missing_media(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            show = create_show(Path(folder) / "Show")
            data = {"id": "photo", "name": "Photo", "tracks": [[{"id": "E1", "kind": "image", "file": "media/nowhere.jpg"}], [], [], []]}
            problems = check_input_data(data, show)[1]
            self.assertTrue(any("nowhere.jpg" in p for p in problems))
            outside = dict(data, tracks=[[{"id": "E1", "kind": "image", "file": "C:/Windows/win.ini"}], [], [], []])
            self.assertTrue(check_input_data(outside, show)[1])
            self.assertEqual(outside_media(outside, show), {})  # not a picture: never offered

    def test_pictures_from_elsewhere_can_be_copied_in(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            show = create_show(Path(folder) / "Show")
            picture = Path(folder) / "Downloads" / "sunrise.png"
            picture.parent.mkdir()
            picture.write_bytes(b"not really a png, but it's there")
            sneaky = "media/../../Downloads/sunrise.png"  # relative, but outside the show: never offered
            data = {"id": "photo", "name": "Photo", "tracks": [
                [{"id": "E1", "kind": "image", "file": str(picture)}],
                [{"id": "E2", "kind": "image", "file": sneaky}],
                [{"id": "E3", "kind": "video", "file": str(picture)}],  # a picture isn't a video
                []]}
            self.assertEqual(outside_media(data, show), {str(picture): picture})
            problems = check_input_data(data, show)[1]
            self.assertTrue(any("Copy into the show" in p for p in problems))
            moved = with_media(data, {str(picture): "media/sunrise.png"})
            self.assertEqual(moved["tracks"][0][0]["file"], "media/sunrise.png")
            self.assertEqual(moved["tracks"][1][0]["file"], sneaky)
            self.assertEqual(data["tracks"][0][0]["file"], str(picture))  # the original isn't changed
            self.assertEqual(outside_media(data, replace(show, folder="")), {})  # no show folder: nowhere to copy to

    def test_size_and_shape(self) -> None:
        huge = self.paint()
        huge["name"] = "x" * 2_100_000
        self.assertTrue(any("too big" in p for p in self.problems(huge)))
        two = self.paint()
        two["tracks"][0].append({"id": "E2", "kind": "paint"})
        self.assertTrue(any("at most one per track" in p for p in self.problems(two)))
        self.assertTrue(self.problems({"id": "x", "name": "X", "tracks": "lots"}))
        self.assertTrue(check_input_data([1, 2], self.show)[1])

    def test_house_lights(self) -> None:
        data = self.paint()
        data["house"] = {"dimmer-7": 0.5}
        self.assertTrue(any("dimmer-7" in p for p in self.problems(data)))

    def test_ids(self) -> None:
        data = self.paint()
        data["id"] = "warm-white"  # taken: a new one gets its own id
        inp, problems, notes = check_input_data(data, self.show)
        self.assertEqual(problems, [])
        self.assertEqual(inp.id, "warm-white-2")
        self.assertTrue(any("warm-white-2" in n for n in notes))
        edited, problems, notes = check_input_data(data, self.show, existing_id="worship-blue")  # an edit keeps its id
        self.assertEqual(edited.id, "worship-blue")
        data.pop("id")
        self.assertEqual(check_input_data(data, self.show)[0].id, "new-look")  # no id: from the name

    def test_unknown_kinds_draw_nothing(self) -> None:
        data = self.paint()
        data["tracks"][1] = [{"id": "E2", "kind": "laser"}]
        inp, problems, notes = check_input_data(data, self.show)
        self.assertEqual(problems, [])
        self.assertTrue(any("laser" in n for n in notes))

    def test_describe_changes(self) -> None:
        old = self.show.inputs["warm-white"]
        data = json.loads(json.dumps({"id": "warm-white", "name": "Warm pink", "colour": old.colour,
                                      "tracks": [[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF9AB0"}}], [], [], []]}))
        new = check_input_data(data, self.show, existing_id="warm-white")[0]
        changes = describe_changes(old, new)
        self.assertIn("Name: Warm white → Warm pink", changes)
        self.assertTrue(any(c.startswith("Paint E1: fill") for c in changes))


class RealisticReplyTests(unittest.TestCase):
    """A reply written the way chat AIs answer the Create prompt (words, a fenced block, comments)."""

    REPLY = '''Here's a gentle "breathing" look for the pillars. The pillar lights fade between deep purple
and a softer lilac every 6 seconds, and every other light is left alone so it can sit over Follow screen:

```json
{
  "format": "centralight-input",
  "version": 1,
  "id": "pillars-breathe",
  "name": "Pillars breathe",
  "colour": "#7A3CFF",
  "transition": { "kind": "fade", "duration": 2 },
  "tracks": [
    [
      {
        "id": "E1",
        "kind": "paint",
        "fill": {
          "type": "colour",
          "colour": { "expr": "mix(#4B1A99, #C9A6FF, wave(time, 1/6))" }
        },
        "mask": { "exclude": ["slimpar-101", "slimpar-102", "slimpar-103", "slimpar-104", "slimpar-105", "slimpar-108"] }, // pillars only
      }
    ],
    [],
    [],
    []
  ]
}
```

Send it to an overlay to have the pillars breathe over whatever is on Base.'''

    def test_it_makes_a_working_input(self) -> None:
        show = show_with_globals()
        inp, problems, notes = check_input_data(parse_reply(self.REPLY), show)
        self.assertEqual(problems, [])
        ctx = RenderContext(CANVAS_W, CANVAS_H)
        ctx.sampler = sampler_for(show.fixtures, None)
        colours = {}
        for moment in (0.0, 3.0):
            canvas = render_input(Instance(inp, 0.0), moment, ctx)
            colours[moment] = dict(zip(ctx.sampler.ids, ctx.sampler.sample(canvas)))
        self.assertGreater(colours[0.0]["slimpar-106"][2], 0.5)  # a pillar: purple
        self.assertLess(sum(colours[0.0]["slimpar-101"]), 0.01)  # not a pillar: left alone (clear)
        self.assertNotEqual(tuple(colours[0.0]["slimpar-106"]), tuple(colours[3.0]["slimpar-106"]))  # and it breathes


if __name__ == "__main__":
    unittest.main()
