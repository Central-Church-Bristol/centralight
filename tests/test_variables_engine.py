"""Variables in the engine: globals drive expressions live, locals and time work on the lights,
values survive a restart, and changes to the show keep what still fits."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace

from centralight.commands import SendToBase, SetVariable, SwapShow
from centralight.editor_render import EditorRenderer, EditorRequest
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import GlobalVar, Input
from centralight.new_show import show_from_patch
from centralight.show_io import freeze
from tests.fakes import FakeClock, FakeOutput

SLIMPAR_105 = 41  # channel of its red


def painted(input_id: str, colour, **kwargs) -> Input:
    element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": colour}}
    return Input(id=input_id, name=input_id.title(), tracks=freeze([[element], [], [], []]), **kwargs)


class EngineVariableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.globals = (
            GlobalVar("accent", "colour", "#FF0000", dashboard=True),
            GlobalVar("level", "number", 0.5, 0.0, 1.0, True),
            GlobalVar("party", "switch", False, dashboard=True),
        )
        inputs = {
            "accent": painted("accent", {"expr": "accent"}),
            "dimmed": painted("dimmed", {"expr": "brightness(#FFFFFF, level)"}),
            "party": painted("party", {"expr": "#00FF00 if party else #0000FF"}),
            "clock": painted("clock", {"expr": "rgb(min(time / 4, 1), 0, 0)"}),
            "local": painted("local", {"expr": "tint"}, locals=freeze([{"name": "tint", "type": "colour", "value": {"expr": "mix(#000000, accent, 0.5)"}}])),
            "keylit": painted("keylit", "#000000", house=freeze({"dimmer-4": {"expr": "level"}})),
        }
        self.show = replace(show_from_patch(), globals=self.globals).with_inputs(inputs, tuple(inputs))
        self.clock = FakeClock()
        self.output = FakeOutput()
        self.engine = Engine(self.show, self.output, None, clock=self.clock)

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.025))):
            self.clock.advance(0.025)
            self.engine.tick()

    def rgb(self) -> tuple:
        return tuple(self.output.last[SLIMPAR_105 - 1:SLIMPAR_105 + 2])

    def test_a_global_drives_the_lights_live(self) -> None:
        self.engine.submit(SendToBase("accent", cut=True))
        self.engine.tick()
        self.assertEqual(self.rgb(), (255, 0, 0))
        self.engine.submit(SetVariable("accent", "00FF00"))  # as the Stream Deck sends it
        self.engine.tick()
        self.assertEqual(self.rgb(), (0, 255, 0))
        self.assertEqual(self.engine.frames.get().variables["accent"], (0.0, 1.0, 0.0))

    def test_numbers_and_switches(self) -> None:
        self.engine.submit(SendToBase("dimmed", cut=True))
        self.engine.tick()
        self.assertEqual(self.rgb(), (128, 128, 128))
        self.engine.submit(SetVariable("level", 0.2))
        self.engine.tick()
        self.assertEqual(self.rgb(), (51, 51, 51))
        self.engine.submit(SetVariable("level", "7"))  # kept in its range
        self.engine.tick()
        self.assertEqual(self.rgb(), (255, 255, 255))
        self.engine.submit(SendToBase("party", cut=True))
        self.engine.tick()
        self.assertEqual(self.rgb(), (0, 0, 255))
        self.engine.submit(SetVariable("party", "toggle"))
        self.engine.tick()
        self.assertEqual(self.rgb(), (0, 255, 0))

    def test_unknown_names_and_bad_values_are_ignored(self) -> None:
        self.engine.submit(SetVariable("nothing", 1))
        self.engine.submit(SetVariable("accent", "purple"))
        self.engine.submit(SetVariable("level", "loud"))
        self.engine.tick()
        values = self.engine.frames.get().variables
        self.assertEqual(values["accent"], (1.0, 0.0, 0.0))
        self.assertEqual(values["level"], 0.5)
        self.assertNotIn("nothing", values)

    def test_time_and_locals(self) -> None:
        self.engine.submit(SendToBase("clock", cut=True))
        self.run_for(2.0)
        red, green, blue = self.rgb()
        self.assertAlmostEqual(red, 127, delta=2)  # about half way to red after 2 of 4 seconds
        self.assertEqual((green, blue), (0, 0))
        self.engine.submit(SendToBase("local", cut=True))
        self.engine.tick()
        self.assertEqual(self.rgb(), (128, 0, 0))  # tint = half of the accent (red)

    def test_a_house_level_from_an_expression(self) -> None:
        self.engine.submit(SendToBase("keylit", cut=True))
        self.run_for(0.2)
        self.assertEqual(self.output.last[3], 128)  # key lights at "level" = 50 %
        self.engine.submit(SetVariable("level", 0.2))
        self.run_for(0.2)
        self.assertEqual(self.output.last[3], 51)  # and following it

    def test_values_survive_a_restart(self) -> None:
        self.engine.submit(SetVariable("accent", "#0000FF"))
        self.engine.submit(SetVariable("level", 0.75))
        self.engine.tick()
        saved = LiveState.from_dict(self.engine.live_state().to_dict())
        self.assertEqual(saved.globals, {"accent": "#0000FF", "level": 0.75, "party": 0.0})
        again = Engine(self.show, FakeOutput(), saved, clock=FakeClock())
        again.tick()
        self.assertEqual(again.frames.get().variables["accent"], (0.0, 0.0, 1.0))
        self.assertEqual(again.frames.get().variables["level"], 0.75)

    def test_show_changes_keep_what_still_fits(self) -> None:
        self.engine.submit(SetVariable("level", 0.9))
        changed = replace(self.show, globals=(GlobalVar("level", "number", 0.5, 0.0, 0.5), GlobalVar("speed", "number", 0.3)))
        self.engine.submit(SwapShow(changed))
        self.engine.tick()
        self.assertEqual(dict(self.engine.frames.get().variables), {"level": 0.5, "speed": 0.3})

    def test_the_editor_draws_with_the_engines_values(self) -> None:
        renderer = EditorRenderer(None)
        green = {"accent": (0.0, 1.0, 0.0), "level": 0.5, "party": 0.0}
        picture = renderer.render_now(EditorRequest(self.show, "accent", "E1", time.monotonic(), green))
        self.assertEqual(tuple(picture.result[180, 320]), (0, 255, 0))
        self.assertEqual(tuple(picture.selected[180, 320][:3]), (0, 255, 0))


if __name__ == "__main__":
    unittest.main()
