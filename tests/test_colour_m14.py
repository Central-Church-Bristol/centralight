"""M14 item 9: a Colour element (a solid colour or a gradient), and Paint without a fill. A Colour element looks exactly
like a Paint element's fill did; a new Paint element is see-through; older inputs are brought across so the lights
look the same (a fill alone becomes a Colour element; a fill under shapes stays, as the Paint element's Background, and
can be split into Colour + Paint)."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

import numpy as np

from centralight.bus import Instance, render_input
from centralight.elements import RenderContext, render_element
from centralight.input_edit import background_track, default_element, split_background, split_changes
from centralight.lights import lights_for
from centralight.model import Input
from centralight.new_show import show_from_patch
from centralight.schema import element_specs, has_background
from centralight.show_io import freeze, input_from_dict, input_to_dict

FILLS = (
    {"type": "colour", "colour": "#FF6F59"},
    {"type": "linear", "from": "#FF0000", "to": "#0000FF", "angle": 30},
    {"type": "radial", "from": "#FFFFFF", "to": "#102040", "x": 0.3, "y": 0.6, "radius": 0.4},
    {"type": "colour", "colour": {"expr": "hsv(time / 10)"}},
)
EXTRAS = (
    {},
    {"opacity": 0.6, "blend": "screen"},
    {"transform": {"x": 0.3, "scale": 0.7, "rotation": 20}},
    {"mask": {"exclude": ["slimpar-101", "slimpar-105"]}},
)


class ColourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()
        self.lights = lights_for(self.show.fixtures)

    def lights_of(self, element, t: float = 2.0):
        ctx = RenderContext(0, 0, lights=self.lights, per_light=True)
        ctx.time = t
        return render_element(freeze(element), ctx)

    def picture_of(self, element, t: float = 2.0):
        ctx = RenderContext(160, 90)
        ctx.time = t
        return render_element(freeze(element), ctx)

    def test_a_colour_element_looks_exactly_like_paints_fill_did(self) -> None:
        for fill in FILLS:
            for extra in EXTRAS:
                paint = {"id": "E1", "kind": "paint", "fill": fill, **extra}
                colour = {**paint, "kind": "colour"}
                np.testing.assert_array_equal(self.lights_of(colour), self.lights_of(paint), str((fill, extra)))
                np.testing.assert_array_equal(self.picture_of(colour), self.picture_of(paint), str((fill, extra)))

    def test_a_colour_element_always_shows_something(self) -> None:
        for broken in ({"id": "E1", "kind": "colour"}, {"id": "E1", "kind": "colour", "fill": {"type": "none"}}):
            layer = self.lights_of(broken)
            self.assertTrue(np.all(layer[3] == 1.0), broken)  # (the default colour, solid)

    def test_a_new_paint_element_is_see_through(self) -> None:
        paint = default_element("paint", "E1")
        self.assertEqual(paint, {"id": "E1", "kind": "paint"})
        self.assertEqual(float(np.max(self.lights_of(paint)[3])), 0.0)
        self.assertEqual(float(np.max(self.picture_of(paint)[3])), 0.0)
        self.assertEqual(default_element("colour", "E2"), {"id": "E2", "kind": "colour", "fill": {"type": "colour", "colour": "#86D6FF"}})

    def test_settings(self) -> None:
        self.assertEqual([s.path for s in element_specs("colour")][:2], ["fill.type", "fill.colour"])
        self.assertNotIn("none", [v for v, _label in element_specs("colour")[0].choices])  # (Colour always shows something)
        self.assertNotIn("fill.type", [s.path for s in element_specs("paint", {"kind": "paint"})])
        legacy = {"kind": "paint", "fill": {"type": "colour"}, "shapes": [{"id": "S1"}]}
        self.assertTrue(has_background(legacy))
        self.assertEqual({s.group for s in element_specs("paint", legacy) if s.path.startswith("fill.")}, {"Background"})
        self.assertFalse(has_background({**legacy, "fill": {"type": "none"}}))


class OlderInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()
        self.lights = lights_for(self.show.fixtures)

    def on_the_lights(self, inp: Input, t: float = 1.5):
        ctx = RenderContext(0, 0, lights=self.lights, per_light=True)
        return render_input(Instance(inp, 0.0), t, ctx)

    def old_input(self, tracks) -> dict:
        return {"format": "centralight-input", "version": 1, "id": "old", "name": "Old", "tracks": tracks}

    def test_a_fill_alone_becomes_a_colour_element_that_looks_the_same(self) -> None:
        data = self.old_input([
            [{"id": "E1", "kind": "paint", "fill": {"type": "linear", "from": "#FF0000", "to": "#00FF00", "angle": 45},
              "opacity": 0.8, "mask": {"exclude": ["slimpar-103"]}}],
            [{"id": "E2", "kind": "paint", "fill": {"colour": "#2050FF"}, "blend": "multiply"}],  # (no type: a colour)
            [{"id": "E3", "kind": "paint", "fill": {"type": "none"}}], [],
        ])
        raw = Input(id="old", name="Old", tracks=tuple(freeze(track) for track in data["tracks"]))  # as it was
        loaded, problems = input_from_dict(json.loads(json.dumps(data)))
        self.assertEqual(problems, [])
        self.assertEqual([t[0]["kind"] for t in loaded.tracks[:3]], ["colour", "colour", "paint"])  # (an empty one stays)
        self.assertEqual(loaded.tracks[0][0]["opacity"], 0.8)
        np.testing.assert_array_equal(self.on_the_lights(loaded), self.on_the_lights(raw))
        saved = input_to_dict(loaded)
        self.assertEqual(saved["tracks"][0][0]["kind"], "colour")  # (saved as a Colour element from then on)

    def test_a_fill_under_shapes_stays_as_its_background(self) -> None:
        element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0A1A66"},
                   "shapes": [{"id": "S1", "type": "rect", "x": 0.3, "y": 0.5, "w": 0.2, "h": 1.0, "colour": "#FFFFFF"}]}
        loaded, _problems = input_from_dict(self.old_input([[element], [], [], []]))
        self.assertEqual(loaded.tracks[0][0]["kind"], "paint")
        self.assertTrue(has_background(loaded.tracks[0][0]))
        strokes = {**element, "fill": {"type": "colour", "colour": "#0A1A66"}, "shapes": [], "strokes": "paint/0123456789ab.png"}
        loaded, _problems = input_from_dict(self.old_input([[strokes], [], [], []]))
        self.assertEqual(loaded.tracks[0][0]["kind"], "paint")  # (brush painting too)

    def test_split_into_colour_and_paint_looks_the_same(self) -> None:
        element = {"id": "E2", "kind": "paint", "fill": {"type": "radial", "from": "#FFB347", "to": "#B0306A", "x": 0.4},
                   "shapes": [{"id": "S1", "type": "ellipse", "x": 0.7, "y": 0.4, "w": 0.3, "h": 0.5, "softness": 0.5,
                               "colour": "#FFFFFF"}], "mask": {"exclude": ["slimpar-108"]}}
        for track, timed in ((1, False), (0, False), (1, True)):
            tracks = [[], [], [], []]
            tracks[track] = [dict(element, **({"start": 0.0, "end": 4.0} if timed else {}))]
            if track == 1:
                tracks[2] = [{"id": "E9", "kind": "colour", "fill": {"type": "colour", "colour": "#00FF00"}, "opacity": 0.3,
                              **({"start": 0.0, "end": 4.0} if timed else {})}]
            inp = Input(id="glow", name="Glow", infinite=not timed, length=4.0, tracks=tuple(freeze(t) for t in tracks))
            show = self.show.with_inputs({"glow": inp}, ("glow",))
            before = self.on_the_lights(inp)
            split, colour_id = split_background(show, "glow", "E2")
            self.assertTrue(colour_id, (track, timed))
            after = split.inputs["glow"]
            kinds = {e["id"]: (i, e["kind"]) for i, t in enumerate(after.tracks) for e in t}
            self.assertEqual(kinds[colour_id][1], "colour")
            self.assertEqual(kinds["E2"][1], "paint")
            self.assertEqual(kinds[colour_id][0] + 1, kinds["E2"][0])  # (just under the painting)
            self.assertNotIn("fill", next(e for t in after.tracks for e in t if e["id"] == "E2"))
            np.testing.assert_allclose(self.on_the_lights(after), before, atol=1e-6)

    def test_the_users_own_inputs_split_with_the_tracks_above_moving_up(self) -> None:
        """As in the user's two shows: brush painting over a fill on track 1, Colour elements (once fill-only Paints)
        with masks on tracks 2 and 3, track 4 free. The Colour element goes directly under the painting, everything
        above it moves up a track, and at full opacity with Normal blend nothing changes on the lights."""
        import tempfile

        import cv2

        from centralight.assets import PAINT_DIR, save_asset

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        strokes = np.zeros((360, 640, 4), dtype=np.uint8)
        strokes[:, 260:, :3] = (255, 230, 180)
        strokes[:, 260:, 3] = 255
        strokes[:, :, 3] = cv2.GaussianBlur(strokes[:, :, 3], (0, 0), 25)  # (a soft edge across the middle lights)
        painted = save_asset(folder.name, PAINT_DIR, strokes)
        tracks = [
            [{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#2A3F8F"}, "strokes": painted,
              "transform": {"x": 0.55, "scale": 1.1}, "mask": {"exclude": ["slimpar-108"]}}],
            [{"id": "E2", "kind": "colour", "fill": {"type": "colour", "colour": "#FF8A2A"}, "mask": {"exclude": ["slimpar-101"]}}],
            [{"id": "E3", "kind": "colour", "fill": {"type": "colour", "colour": "#16E1C9"}, "opacity": 0.5,
              "mask": {"exclude": ["slimpar-102", "slimpar-103"]}}],
            [],
        ]
        inp = Input(id="new-input", name="New input", tracks=tuple(freeze(t) for t in tracks))
        show = replace(self.show, folder=folder.name).with_inputs({"new-input": inp}, ("new-input",))
        self.assertEqual(split_changes(inp.tracks[0][0]), [])
        self.assertEqual(background_track(inp, "E1"), (0, [3, 0, 1, 2]))
        split, colour_id = split_background(show, "new-input", "E1")
        after = split.inputs["new-input"]
        self.assertEqual([[e["id"] for e in t] for t in after.tracks], [[colour_id], ["E1"], ["E2"], ["E3"]])
        colour = after.tracks[0][0]
        self.assertEqual((colour["kind"], colour["transform"], colour["mask"]), ("colour", inp.tracks[0][0]["transform"],
                                                                                   inp.tracks[0][0]["mask"]))
        ctx = RenderContext(0, 0, lights=self.lights, per_light=True)
        ctx.folder = folder.name
        before = render_input(Instance(inp, 0.0), 1.5, ctx)
        ctx = RenderContext(0, 0, lights=self.lights, per_light=True)
        ctx.folder = folder.name
        np.testing.assert_allclose(render_input(Instance(after, 0.0), 1.5, ctx), before, atol=1e-6)

    def test_the_tracks_below_move_down_when_only_a_lower_track_is_empty(self) -> None:
        element = {"id": "E3", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"},
                   "shapes": [{"id": "S1", "type": "rect", "x": 0.5, "y": 0.5, "w": 0.4, "h": 0.4, "colour": "#FF0000"}]}
        tracks = ([], [{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#102040"}, "opacity": 0.4}],
                  [element], [{"id": "E4", "kind": "colour", "fill": {"type": "colour", "colour": "#00FF00"}, "opacity": 0.2}])
        inp = Input(id="x", name="X", tracks=tuple(freeze(t) for t in tracks))
        show = self.show.with_inputs({"x": inp}, ("x",))
        split, colour_id = split_background(show, "x", "E3")
        after = split.inputs["x"]
        self.assertEqual([[e["id"] for e in t] for t in after.tracks], [["E1"], [colour_id], ["E3"], ["E4"]])
        np.testing.assert_allclose(self.on_the_lights(after), self.on_the_lights(inp), atol=1e-6)

    def test_what_a_split_would_change_is_said(self) -> None:
        painted = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}, "strokes": "paint/0123456789ab.png"}
        self.assertEqual(split_changes(painted), [])
        self.assertEqual(split_changes({**painted, "opacity": 1.0, "blend": "normal", "mask": {"exclude": ["slimpar-101"]}}), [])
        self.assertEqual(split_changes({**painted, "strokes": None, "opacity": 0.5}), [])  # (nothing painted on it)
        for changed, word in (({"opacity": 0.8}, "opacity"), ({"opacity": {"keys": [[0, 1.0], [2, 0.5]]}}, "opacity"),
                              ({"opacity": {"value": 1.0, "expr": "wave(time)"}}, "opacity"), ({"blend": "screen"}, "Screen"),
                              ({"in": {"kind": "fade", "duration": 2.0}}, "comes in"),
                              ({"mask": {"paint": "masks/0123456789ab.png"}}, "mask")):
            notes = split_changes({**painted, **changed})
            self.assertEqual(len(notes), 1, changed)
            self.assertIn(word, notes[0])

    def test_split_says_what_a_crossing_or_random_would_change(self) -> None:
        """(Review: in a timed input the Paint element crosses the one after it on its track, but the Colour element on
        its own track wouldn't cross; and random() picks again for the new element. Both said "exactly the same".)"""
        painted = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0000FF"}, "shapes": [{"id": "S1"}],
                   "start": 0.0, "end": 5.0}
        after = {"id": "E2", "kind": "colour", "fill": {"type": "colour", "colour": "#FF0000"}, "start": 4.0, "end": 8.0}
        crossing = Input(id="x", name="X", infinite=False, length=8.0, tracks=(freeze([]), freeze([painted, after]), (), ()))
        notes = split_changes(crossing.tracks[1][0], crossing)
        self.assertEqual(len(notes), 1)
        self.assertIn("crosses", notes[0])
        apart = Input(id="x", name="X", infinite=False, length=10.0,
                      tracks=(freeze([]), freeze([painted, {**after, "start": 5.0, "end": 9.0}]), (), ()))
        self.assertEqual(split_changes(apart.tracks[1][0], apart), [])  # (one after the other: no crossing)
        lucky = {**painted, "fill": {"type": "colour", "colour": {"value": "#FFFFFF", "expr": "hsv(random(1))"}}}
        notes = split_changes(lucky)
        self.assertEqual(len(notes), 1)
        self.assertIn("random()", notes[0])
        wandering = {**painted, "transform": {"x": {"value": 0.5, "expr": "random(1)"}}}  # (the Colour element gets it too)
        self.assertEqual(len(split_changes(wandering)), 1)

    def test_a_fill_that_isnt_an_object(self) -> None:
        """(Review: an AI wrote "fill": "#FF0000"; the timeline's bar text raised an error and the check said nothing.)"""
        from centralight.ai_guide import check_input_data
        from centralight.ui.timeline_view import summary

        self.assertEqual(summary({"id": "E1", "kind": "colour", "fill": "#FF0000"}), "Colour · colour")
        self.assertEqual(summary({"id": "E1", "kind": "paint"}), "Paint · empty")  # (a new Paint element: see-through)
        self.assertEqual(summary({"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#FF0000"}}),
                         "Colour · #FF0000")
        data = {"format": "centralight-input", "version": 1, "id": "red", "name": "Red",
                "tracks": [[{"id": "E1", "kind": "colour", "fill": "#FF0000"}], [], [], []]}
        _inp, problems, _notes = check_input_data(data, self.show)
        self.assertTrue(any('"fill" must be an object' in p for p in problems), problems)
        for fill in (None, "none"):  # (a Paint element written so is see-through, as the app draws it: fine)
            see_through = dict(data, tracks=[[{"id": "E1", "kind": "paint", "fill": fill}], [], [], []])
            self.assertFalse(any('"fill"' in p for p in check_input_data(see_through, self.show)[1]), fill)

    def test_the_type_row_says_fill(self) -> None:
        """(Review: a Colour element had two rows called "Colour", and its undo step said "colour Colour → …".)"""
        labels = {s.path: s.label for s in element_specs("colour")}
        self.assertEqual((labels["fill.type"], labels["fill.colour"]), ("Fill", "Colour"))

    def test_no_free_track_no_split(self) -> None:
        busy = [[{"id": f"E{i}", "kind": "colour", "fill": {"type": "colour", "colour": "#101010"}}] for i in (1, 3)]
        element = {"id": "E2", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}, "shapes": [{"id": "S1"}]}
        inp = Input(id="x", name="X", tracks=(freeze(busy[0]), freeze([element]), freeze(busy[1]),
                                               freeze([{"id": "E4", "kind": "ndi"}])))
        show = self.show.with_inputs({"x": inp}, ("x",))
        self.assertIsNone(background_track(inp, "E2"))
        self.assertEqual(split_background(show, "x", "E2"), (show, ""))

    def test_the_ai_guide_and_palette_put_colour_first(self) -> None:
        from centralight.ai_guide import examples, guide_text
        from centralight.schema import ELEMENT_KINDS

        self.assertEqual(ELEMENT_KINDS[0][0], "colour")
        text = guide_text(self.show)
        self.assertIn('### Colour (`"kind": "colour"`)', text)
        self.assertIn("see-through", text)
        for _what, data in examples(self.show):
            for track in data["tracks"]:
                for element in track:
                    if element["kind"] == "paint":
                        self.assertNotIn("fill", element)  # (the examples show the new way)


class EditorTests(unittest.TestCase):
    """The palette has Colour first; an older Paint element shows its Background and can be split (one undo step)."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def test_palette_and_split_button(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.gui_fixtures import with_inputs
        from tests.qt_support import process

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine = FakeEngine(with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(tmp.name) / "fixture_map.csv")
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        try:
            page = window.editor_page
            self.assertEqual(list(page.palette.items)[0], "colour")
            window.open_editor("stage-shapes")
            page.select_element("E2")  # (a gradient under two shapes: an older Paint element)
            process(self.app)
            self.assertIn("element:fill.type", page.details._fields)
            from PySide6.QtWidgets import QLabel, QPushButton

            split = next(b for b in page.details.findChildren(QPushButton) if b.text() == "Split into Colour + Paint")
            self.assertTrue(split.isEnabled())
            note = [label.text() for label in page.details.findChildren(QLabel) if "after a split" in label.text()]
            self.assertEqual(len(note), 1)  # (it blends as Screen, and its opacity follows an expression: it says so)
            self.assertIn("won't look quite the same", note[0])
            self.assertIn("Screen", note[0])
            steps = window.document.undo_stack.count()
            split.click()
            process(self.app)
            tracks = window.document.show.inputs["stage-shapes"].tracks
            self.assertEqual([[e["kind"] for e in t] for t in tracks], [["ndi"], ["colour"], ["paint"], []])
            self.assertEqual(window.document.undo_stack.count(), steps + 1)
            window.undo()
            tracks = window.document.show.inputs["stage-shapes"].tracks
            self.assertEqual([[e["kind"] for e in t] for t in tracks], [["ndi"], ["paint"], [], []])
        finally:
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
            process(self.app)


if __name__ == "__main__":
    unittest.main()
