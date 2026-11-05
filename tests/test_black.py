"""M11: Cut to black and Fade to black, one-off actions in place of the Blackout latch."""

from __future__ import annotations

import unittest

from centralight.automations import ACTIONS, CONDITIONS, action_command, describe_action
from centralight.commands import SendToBase, SendToOverlay, SetHouse, ToBlack
from centralight.engine import Engine
from centralight.live_state import HouseSetting, LiveState
from centralight.model import Input, Transition
from centralight.new_show import show_from_patch
from centralight.settings import Settings
from centralight.show_io import automation_from_dict, freeze
from tests.fakes import FakeClock, FakeOutput

PILLAR = 50  # SlimPAR 106 (a pillar light): its first channel, red
LEFT = 41  # SlimPAR 105 (not a pillar): its first channel, red


def colour(input_id: str, hex_colour: str, exclude=()) -> Input:
    element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": hex_colour}}
    if exclude:
        element["mask"] = {"exclude": list(exclude)}
    return Input(id=input_id, name=input_id, transition=Transition("cut", 0.0), tracks=freeze([[element], [], [], []]))


class EngineBlackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.output = FakeOutput()
        show = show_from_patch()
        others = [fx.id for fx in show.colour_fixtures() if fx.id not in ("slimpar-106", "slimpar-107")]
        inputs = {"blue": colour("blue", "#0000FF"), "pillars": colour("pillars", "#FF0000", exclude=others)}
        self.engine = Engine(show.with_inputs(inputs, tuple(inputs)), self.output, LiveState(house={"dimmer-4": HouseSetting(1.0, True)}),
                             clock=self.clock)

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.025))):
            self.clock.advance(0.025)
            self.engine.tick()

    def test_a_fade_takes_the_overlays_with_it_as_one_look(self) -> None:
        self.engine.submit(SendToBase("blue", cut=True))
        self.engine.submit(SendToOverlay("pillars"))
        self.run_for(0.1)
        self.assertEqual(self.output.last[PILLAR - 1], 255)  # the pillars red over blue
        self.engine.submit(ToBlack(1.0))
        self.run_for(0.5)
        self.assertTrue(100 <= self.output.last[PILLAR - 1] <= 155)  # still red, half faded (not blue)
        self.assertEqual(self.output.last[PILLAR + 1], 0)
        self.assertEqual(self.engine.frames.get().overlay.copies, ())  # the Overlay is cleared: its look is in the fade
        self.run_for(0.6)
        self.assertEqual(self.output.last[PILLAR - 1], 0)
        self.assertEqual(self.output.last[LEFT + 1], 0)
        self.assertTrue(self.engine.frames.get().colour_black)
        self.assertEqual(self.output.last[3], 255)  # (a click: the key lights stay on)

    def test_a_black_mid_fade_and_a_new_input_mid_fade(self) -> None:
        self.engine.submit(SendToBase("blue", cut=True))
        self.run_for(0.1)
        self.engine.submit(ToBlack(1.0))
        self.run_for(0.5)
        self.engine.submit(SendToBase("blue", transition=Transition("fade", 1.0)))  # sent while it fades out
        self.run_for(1.1)
        self.assertEqual(self.output.last[LEFT + 1], 255)  # it comes back in full
        self.assertFalse(self.engine.frames.get().colour_black)

    def test_it_ends_show_on_lights_too(self) -> None:
        from centralight.commands import SetSolo

        self.engine.submit(SetSolo("blue"))  # the Editor's "Show on lights"
        self.run_for(0.05)
        self.assertEqual(self.output.last[LEFT + 1], 255)
        self.engine.submit(ToBlack(0.0))
        self.run_for(0.05)
        self.assertEqual(self.output.last[LEFT + 1], 0)  # black means black
        self.assertEqual(self.engine.frames.get().solo, "")

    def test_house_light_mirror_is_only_for_lights_it_switched_off(self) -> None:
        self.engine.submit(SetHouse("dimmer-1", on=False))  # the festoon was already off
        self.engine.submit(ToBlack(2.0, everything=True))
        self.run_for(2.1)
        self.engine.submit(SetHouse("dimmer-1", on=True))  # switching it on: the usual 0.5 s, not 2 s
        self.run_for(0.55)
        self.assertEqual(self.output.last[0], 255)
        self.engine.submit(SetHouse("dimmer-4", level=0.5))  # a fader drag on the key lights (not a plain switch on)...
        self.engine.submit(SetHouse("dimmer-4", on=True))
        self.run_for(0.55)
        self.assertLess(self.output.last[3], 128)  # ...the switch still mirrors the 2 s fade (half way at 0.5 s: < 50 %)


class AutomationBlackTests(unittest.TestCase):
    def test_the_actions_and_the_condition(self) -> None:
        self.assertIn("cut_black", ACTIONS)
        self.assertIn("fade_black", ACTIONS)
        self.assertNotIn("blackout", ACTIONS)
        self.assertEqual(CONDITIONS["colour_black"], "The colour lights are black")
        self.assertNotIn("blackout", CONDITIONS)
        show = show_from_patch()
        self.assertEqual(action_command({"do": "cut_black"}, show), ToBlack(0.0, False))
        self.assertEqual(action_command({"do": "fade_black", "fade": 3, "all": True}, show), ToBlack(3.0, True))
        self.assertEqual(action_command({"do": "fade_black"}, show), ToBlack(2.0, False))
        self.assertEqual(describe_action({"do": "fade_black", "fade": 3, "all": True}), "fade to black (3 s), Festoon and Key lights too")

    def test_an_old_blackout_file_is_switched_off_not_converted(self) -> None:
        """(From before Cut / Fade to black: no show of yours has one, so it's no longer converted. The general safety
        handling still applies: an unknown condition switches the automation off, an unknown action is left out.)"""
        data = {"id": "night", "name": "Night", "conditions": [{"type": "blackout"}],
                "actions": [{"do": "blackout", "on": True}, {"do": "base", "input": "x"}]}
        automation, warnings = automation_from_dict(data, expected_id="night")
        self.assertFalse(automation.enabled)
        self.assertEqual([dict(a) for a in automation.actions], [{"do": "base", "input": "x"}])
        self.assertTrue(warnings)


class ButtonAndSettingsTests(unittest.TestCase):
    def test_the_hold_button_from_the_keyboard(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from centralight.ui.widgets import HoldButton
        from tests.qt_support import qt_app

        qt_app()
        button = HoldButton("Cut to black", "Cut everything", "#FF3B5C")
        taps, holds = [], []
        button.tapped.connect(lambda: taps.append(1))
        button.held.connect(lambda: holds.append(1))
        QTest.keyClick(button, Qt.Key.Key_Space)
        QTest.keyClick(button, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual((len(taps), len(holds)), (1, 1))
        self.assertGreaterEqual(button.minimumSizeHint().width(), 0)
        self.assertGreater(button.widest_text(), button.fontMetrics().horizontalAdvance("Cut to black") - 1)

    def test_fade_time_in_settings(self) -> None:
        self.assertEqual(Settings().black_fade, 2.0)
        settings = Settings()
        settings.update({"black_fade": 4.5})
        self.assertEqual(settings.black_fade, 4.5)
        settings.update({"black_fade": "slow"})
        self.assertEqual(settings.black_fade, 2.0)  # a bad value: the default
        settings.update({"black_fade": 600})
        self.assertEqual(settings.black_fade, 2.0)

    def test_the_stream_decks_fade_takes_the_time_set_in_settings(self) -> None:
        from pathlib import Path

        from centralight.remote import RemoteServer, command_for

        settings = Settings()
        settings.black_fade = 4.5
        remote = RemoteServer.from_settings(None, settings)
        self.assertEqual(remote.black_fade(), 4.5)
        self.assertEqual(command_for(["black", "fade"], {}, show_from_patch(), None, remote.black_fade()), ToBlack(4.5, False))
        settings.black_fade = 1.0  # changed in Settings while running: it counts at once
        self.assertEqual(remote.black_fade(), 1.0)
        # ...and the app makes its remote that way
        app_source = (Path(__file__).resolve().parents[1] / "centralight" / "app.py").read_text(encoding="utf-8")
        self.assertIn("RemoteServer.from_settings(engine, settings)", app_source)


if __name__ == "__main__":
    unittest.main()
