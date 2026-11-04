"""M13: the countdown before automatic show loads and Cut / Fade to black (FakeClock throughout)."""

from __future__ import annotations

import unittest
from datetime import datetime

from centralight.commands import (
    CountdownAction,
    LoadShow,
    RunAutomation,
    SetAutomationsPaused,
    SetCountdownLength,
    SetVariable,
)
from centralight.engine import Engine
from centralight.model import GlobalVar
from tests.fakes import FakeOutput
from tests.test_automations import Clocks, automation, uk_local
from tests.test_shows import EVENING, MORNING, a_show, globals_of, solid

RED, GREEN = "#FF0000", "#00FF00"
LOAD_EVENING = automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                          [{"do": "load_show", "show": "evening"}, {"do": "house", "label": "Key lights", "level": 0.7}])
FADE_AT_NOON = automation("noon", [{"type": "time", "from": "12:00", "to": "12:05"}], [{"do": "fade_black", "fade": 2.0}])
FADE_WHEN_MOOD = automation("mood", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                            [{"do": "fade_black", "fade": 1.0}])


class CountdownCase(unittest.TestCase):
    """An engine with fake clocks, and ways to drive it."""

    def make(self, start: datetime, show_automations=(), global_automations=(), seconds: float | None = None) -> Engine:
        morning = a_show("Morning", MORNING, [solid("red", RED)], list(show_automations),
                         variables=(GlobalVar("mood", "number", 0.0, 0.0, 1.0),))
        self.clocks = Clocks(start)
        self.clocks.uptime_value = 86400.0
        self.engine = Engine(morning, FakeOutput(), clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                             local_time=uk_local, uptime=lambda: self.clocks.uptime_value,
                             global_automations=globals_of(*global_automations))
        if seconds is not None:
            self.send(SetCountdownLength(seconds))
        self.engine.tick()
        return self.engine

    def send(self, command) -> None:
        self.engine.submit(command)
        self.engine.tick()

    def run_for(self, seconds: float, step: float = 0.25) -> None:
        for _ in range(max(1, int(round(seconds / step)))):
            self.clocks.advance(step)
            self.engine.tick()

    def run_until(self, until: datetime, step: float = 0.25) -> None:
        while self.clocks.local() < until:
            self.clocks.advance(step)
            self.engine.tick()

    def requests(self) -> list[dict]:
        found = []
        while not self.engine.gui_requests.empty():
            found.append(self.engine.gui_requests.get_nowait()[1])
        return found

    def banner(self):
        return self.engine.frames.get().countdown

    def light(self):
        self.send_base("red")

    def send_base(self, input_id: str) -> None:
        from centralight.commands import SendToBase

        self.send(SendToBase(input_id, cut=True))


class CountdownTests(CountdownCase):
    # --- a scheduled load lands on time, after the banner

    def test_a_scheduled_load_lands_on_time_after_its_banner(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 55), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 44))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 17, 59, 46))
        banner = self.banner()
        self.assertIsNotNone(banner)
        self.assertEqual(banner.text, "Loading evening")
        self.assertEqual(banner.show, "evening")
        self.assertEqual(banner.automation, "Evening")
        self.assertAlmostEqual(banner.seconds, 14.0, delta=0.5)
        self.assertEqual(banner.clock, "0:14")
        self.run_until(datetime(2026, 11, 8, 17, 59, 59, 500000))
        self.assertEqual(self.requests(), [], "not before 18:00")
        self.run_until(datetime(2026, 11, 8, 18, 0, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        self.assertIsNone(self.banner())

    def test_the_rest_of_the_run_waits_for_the_load(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 18, 0, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        before = self.engine._house["dimmer-4"].level.value(self.clocks.mono)  # (Key lights: in Morning, untouched)
        self.send(LoadShow(a_show("Evening", EVENING, [solid("green", GREEN)])))
        self.run_for(1.0)
        self.assertNotEqual(before, 0.7)
        self.assertAlmostEqual(self.engine._house["dimmer-4"].level.value(self.clocks.mono), 0.7, places=2)  # (in Evening)

    def test_load_now(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.assertIsNotNone(self.banner())
        self.send(CountdownAction("now"))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 18, 0, 5))
        self.assertEqual(self.requests(), [], "its automation's run at 18:00 doesn't load it again")

    def test_skip_only_skips_this_run(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.send(CountdownAction("skip"))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 18, 0, 10))
        self.assertEqual(self.requests(), [])
        self.assertNotAlmostEqual(self.engine._house["dimmer-4"].level.value(self.clocks.mono), 0.7, places=2,
                                  msg="the rest of a skipped run is skipped too")
        # next Sunday it runs as normal (after its countdown)
        self.run_until(datetime(2026, 11, 15, 17, 59), step=60.0)
        self.run_until(datetime(2026, 11, 15, 18, 0, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])

    def test_wait_ten_minutes(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.send(CountdownAction("wait"))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 18, 9, 44))
        self.assertEqual(self.requests(), [])
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 18, 9, 46))
        self.assertIsNotNone(self.banner(), "the countdown comes again before it")
        self.run_until(datetime(2026, 11, 8, 18, 10, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])

    def test_zero_turns_it_off(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING], seconds=0)
        self.run_until(datetime(2026, 11, 8, 17, 59, 59))
        self.assertIsNone(self.banner())
        self.assertEqual(self.requests(), [])
        self.run_until(datetime(2026, 11, 8, 18, 0, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])

    def test_no_countdown_just_after_starting(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 59, 50), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 55))
        self.assertIsNone(self.banner(), "nobody's there yet: no banner")
        self.run_until(datetime(2026, 11, 8, 18, 0, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        self.assertIsNone(self.banner())
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[FADE_WHEN_MOOD])  # (CentraLight has just started again)
        self.send_base("red")  # (in its first minute, an automation's fade to black happens at once)
        self.send(SetVariable("mood", 0.9))
        self.run_for(2.0)
        self.assertIsNone(self.banner())
        self.assertTrue(self.engine.frames.get().colour_black)

    def test_no_countdown_for_run_now(self) -> None:
        self.make(datetime(2026, 11, 8, 14, 0), global_automations=[LOAD_EVENING])
        self.run_for(70.0, step=1.0)
        self.send(RunAutomation("evening", "global"))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        self.assertIsNone(self.banner())

    def test_pausing_drops_countdowns(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.assertIsNotNone(self.banner())
        self.send(SetAutomationsPaused(True))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 18, 0, 5))
        self.assertEqual(self.requests(), [])

    # --- Cut / Fade to black

    def test_a_scheduled_fade_to_black_lands_on_time(self) -> None:
        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[FADE_AT_NOON])
        self.send_base("red")
        self.run_until(datetime(2026, 11, 8, 11, 59, 50))
        banner = self.banner()
        self.assertIsNotNone(banner)
        self.assertEqual(banner.text, "Fading to black")
        self.run_until(datetime(2026, 11, 8, 11, 59, 59, 500000))
        self.assertFalse(self.engine.frames.get().colour_black)
        self.assertEqual(self.engine.frames.get().base, "Red")
        self.run_until(datetime(2026, 11, 8, 12, 0, 1))
        self.run_for(3.0)
        self.assertTrue(self.engine.frames.get().colour_black)

    def test_an_event_waits_for_its_countdown(self) -> None:
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[FADE_WHEN_MOOD])
        self.run_for(70.0, step=1.0)  # (past the start-up quiet)
        self.send_base("red")
        self.send(SetVariable("mood", 0.9))  # (something no clock could see coming)
        self.run_for(1.5)
        banner = self.banner()
        self.assertIsNotNone(banner)
        self.assertEqual(banner.text, "Fading to black")
        self.run_for(10.0)
        self.assertFalse(self.engine.frames.get().colour_black)
        self.run_for(8.0)
        self.assertTrue(self.engine.frames.get().colour_black)
        self.assertIsNone(self.banner())

    def test_already_black_needs_no_countdown(self) -> None:
        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[FADE_AT_NOON])
        self.run_until(datetime(2026, 11, 8, 11, 59, 50))
        self.assertIsNone(self.banner())  # (nothing on Base: the colour lights are black already)

    def test_a_shows_countdown_goes_with_the_show(self) -> None:
        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[FADE_AT_NOON])
        self.send_base("red")
        self.run_until(datetime(2026, 11, 8, 11, 59, 50))
        self.assertIsNotNone(self.banner())
        self.send(LoadShow(a_show("Evening", EVENING, [solid("green", GREEN)])))
        self.assertIsNone(self.banner())

    def test_after_n_seconds_steps_get_their_countdown_early(self) -> None:
        later = automation("later", [{"type": "time", "from": "12:00", "to": "12:30"}],
                           [{"do": "base", "input": "red", "transition": "cut"}, {"do": "cut_black", "delay": 60}])
        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[later])
        self.run_until(datetime(2026, 11, 8, 12, 0, 44))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 12, 0, 46))
        self.assertIsNotNone(self.banner())
        self.assertEqual(self.banner().text, "Cutting to black")
        self.run_until(datetime(2026, 11, 8, 12, 0, 59, 500000))  # (its run began at 12:00:00: the cut is due at 12:01:00)
        self.assertFalse(self.engine.frames.get().colour_black)
        self.run_until(datetime(2026, 11, 8, 12, 1, 1))
        self.assertTrue(self.engine.frames.get().colour_black)

    def test_a_countdown_whose_automation_doesnt_run_is_dropped(self) -> None:
        """Started early for 12:00, but by then the automation is switched off: nothing happens."""
        from dataclasses import replace

        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[FADE_AT_NOON])
        self.send_base("red")
        self.run_until(datetime(2026, 11, 8, 11, 59, 50))
        self.assertIsNotNone(self.banner())
        from centralight.commands import SwapShow

        show = self.engine.show
        self.send(SwapShow(show.with_automations({"noon": replace(FADE_AT_NOON, enabled=False)})))
        self.run_for(1.5)
        self.assertIsNone(self.banner(), "it goes at once, not at its zero")
        self.run_until(datetime(2026, 11, 8, 12, 0, 10))
        self.assertIsNone(self.banner())
        self.assertFalse(self.engine.frames.get().colour_black)

    def test_a_dropped_after_n_seconds_step_takes_its_countdown_with_it(self) -> None:
        """"After 60 s cut to black" while a variable says so: the variable changes back before then, so the waiting
        step is dropped (as the runner does) and its countdown goes too."""
        later = automation("later", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                           [{"do": "cut_black", "delay": 30}])
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[later])
        self.run_for(70.0, step=1.0)  # (past the start-up quiet)
        self.send_base("red")
        self.send(SetVariable("mood", 0.9))
        self.run_for(20.0)
        self.assertIsNotNone(self.banner())
        self.send(SetVariable("mood", 0.1))  # (the wait was a grace period: it's over)
        self.run_for(1.5)
        self.assertIsNone(self.banner())
        self.run_for(15.0)
        self.assertFalse(self.engine.frames.get().colour_black)

    def test_the_length_comes_from_settings(self) -> None:
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[LOAD_EVENING], seconds=30)
        self.run_until(datetime(2026, 11, 8, 17, 59, 29))
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 17, 59, 31))
        self.assertIsNotNone(self.banner())
        self.assertAlmostEqual(self.banner().seconds, 29.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()


class ReviewTests(CountdownCase):
    """The countdown waits in the automation runner's queue, so whatever drops a waiting step drops it too."""

    MOOD = (GlobalVar("mood", "number", 0.0, 0.0, 1.0),)

    def started(self, *show_automations, global_automations=()) -> None:
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=list(show_automations),
                  global_automations=list(global_automations))
        self.run_for(70.0, step=1.0)  # (past the start-up quiet)
        self.send_base("red")

    def test_switching_the_automation_off_stops_its_countdown(self) -> None:
        from dataclasses import replace

        from centralight.commands import SwapShow

        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.assertIsNotNone(self.banner())
        show = self.engine.show
        self.send(SwapShow(show.with_automations({"mood": replace(FADE_WHEN_MOOD, enabled=False)})))
        self.run_for(20.0)
        self.assertIsNone(self.banner())
        self.assertFalse(self.engine.frames.get().colour_black, "switched off: it doesn't fade at its zero")

    def test_a_trigger_that_clears_cancels_the_waiting_fade(self) -> None:
        """"Mood high → Fade to black; when it stops → Green": mood back down 3 s in, so Green, and no stale fade."""
        green = solid("green", "#00FF00")
        both = automation("mood", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                          [{"do": "fade_black", "fade": 1.0}], on_end=[{"do": "base", "input": "green", "transition": "cut"}])
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[both])
        from centralight.commands import SwapShow

        self.send(SwapShow(self.engine.show.with_inputs({**self.engine.show.inputs, "green": green})))
        self.run_for(70.0, step=1.0)
        self.send_base("red")
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.assertIsNotNone(self.banner())
        self.send(SetVariable("mood", 0.1))
        self.run_for(20.0)
        self.assertIsNone(self.banner())
        self.assertEqual(self.engine.frames.get().base, "Green")
        self.assertFalse(self.engine.frames.get().colour_black)

    def test_the_rest_of_the_run_keeps_its_order(self) -> None:
        """"Fade to black, then after 5 s Green" from an event: the fade at the countdown's zero, Green 5 s after it."""
        green = solid("green", "#00FF00")
        run = automation("mood", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                         [{"do": "fade_black", "fade": 1.0}, {"do": "base", "input": "green", "transition": "cut", "delay": 5}])
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[run])
        from centralight.commands import SwapShow

        self.send(SwapShow(self.engine.show.with_inputs({**self.engine.show.inputs, "green": green})))
        self.run_for(70.0, step=1.0)
        self.send_base("red")
        self.send(SetVariable("mood", 0.9))
        self.run_for(10.0)
        self.assertEqual(self.engine.frames.get().base, "Red", "Green waits with the fade")
        self.run_for(20.0)
        self.assertEqual(self.engine.frames.get().base, "Green", "...and comes after it")

    def test_skip_skips_the_later_steps_too(self) -> None:
        later = automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                           [{"do": "load_show", "show": "evening"}, {"do": "house", "label": "Key lights", "level": 0.7, "delay": 5}])
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[later])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.send(CountdownAction("skip"))
        self.run_until(datetime(2026, 11, 8, 18, 0, 10))
        self.assertEqual(self.requests(), [])
        self.assertNotAlmostEqual(self.engine._house["dimmer-4"].level.value(self.clocks.mono), 0.7, places=2)

    def test_wait_moves_the_later_steps_too(self) -> None:
        later = automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                           [{"do": "load_show", "show": "evening"}, {"do": "house", "label": "Key lights", "level": 0.7, "delay": 5}])
        self.make(datetime(2026, 11, 8, 17, 58), global_automations=[later])
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        self.send(CountdownAction("wait"))
        self.run_until(datetime(2026, 11, 8, 18, 0, 10))
        self.assertNotAlmostEqual(self.engine._house["dimmer-4"].level.value(self.clocks.mono), 0.7, places=2)
        self.run_until(datetime(2026, 11, 8, 18, 10, 1))
        self.assertEqual([r["do"] for r in self.requests()], ["load_show"])
        self.send(LoadShow(a_show("Evening", EVENING, [solid("green", "#00FF00")])))
        self.run_until(datetime(2026, 11, 8, 18, 10, 7))
        self.assertAlmostEqual(self.engine._house["dimmer-4"].level.value(self.clocks.mono), 0.7, places=2)

    def test_skip_on_a_waiting_run_skips_its_later_steps(self) -> None:
        """An event's run (nothing saw it coming), waiting for its countdown: Skip drops it from the fade on, Green
        (5 s after it) included."""
        green = solid("green", "#00FF00")
        run = automation("mood", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                         [{"do": "fade_black", "fade": 1.0}, {"do": "base", "input": "green", "transition": "cut", "delay": 5}])
        self.make(datetime(2026, 11, 8, 11, 0), show_automations=[run])
        from centralight.commands import SwapShow

        self.send(SwapShow(self.engine.show.with_inputs({**self.engine.show.inputs, "green": green})))
        self.run_for(70.0, step=1.0)
        self.send_base("red")
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.send(CountdownAction("skip"))
        self.run_for(30.0)
        self.assertEqual(self.engine.frames.get().base, "Red", "neither the fade nor Green")

    def test_wait_on_a_waiting_run_moves_it_ten_minutes(self) -> None:
        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.send(CountdownAction("wait"))
        self.run_for(30.0)
        self.assertFalse(self.engine.frames.get().colour_black, "not at its old zero")
        self.assertIsNone(self.banner())
        self.run_for(570.0, step=1.0)
        self.assertIsNotNone(self.banner(), "the countdown comes again before it")
        self.run_for(20.0)
        self.assertTrue(self.engine.frames.get().colour_black, "10 minutes later")

    def test_run_now_ends_the_countdown(self) -> None:
        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.assertIsNotNone(self.banner())
        self.send(RunAutomation("mood"))
        self.run_for(2.0)
        self.assertTrue(self.engine.frames.get().colour_black, "Run now: at once")
        self.assertIsNone(self.banner())
        self.send_base("red")  # (someone lights things again)
        self.run_for(20.0)
        self.assertFalse(self.engine.frames.get().colour_black, "the old countdown doesn't fade it again")

    def test_run_now_has_no_countdown_for_its_later_steps(self) -> None:
        later = automation("later", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                           [{"do": "cut_black", "delay": 5}])
        self.started(later)
        self.send(RunAutomation("later"))
        self.run_for(4.0)
        self.assertIsNone(self.banner())
        self.assertFalse(self.engine.frames.get().colour_black)
        self.run_for(1.5)
        self.assertTrue(self.engine.frames.get().colour_black, "on time: 5 s after Run now")

    def test_when_it_stops_matching_lands_on_time(self) -> None:
        red_then_fade = automation("service", [{"type": "time", "from": "11:00", "to": "12:30"}],
                                   [{"do": "base", "input": "red", "transition": "cut"}],
                                   on_end=[{"do": "fade_black", "fade": 1.0}])
        self.make(datetime(2026, 11, 8, 12, 25), show_automations=[red_then_fade])
        self.run_until(datetime(2026, 11, 8, 12, 29, 44))
        self.assertEqual(self.engine.frames.get().base, "Red")
        self.assertIsNone(self.banner())
        self.run_until(datetime(2026, 11, 8, 12, 29, 47))
        self.assertIsNotNone(self.banner(), "the banner starts early for the end of a time range too")
        self.run_until(datetime(2026, 11, 8, 12, 29, 59, 500000))
        self.assertFalse(self.engine.frames.get().colour_black)
        self.run_until(datetime(2026, 11, 8, 12, 30, 3))
        self.assertTrue(self.engine.frames.get().colour_black, "...and it fades at 12:30")

    def test_a_skip_survives_a_flicker(self) -> None:
        """Skip on an early banner is kept for that run, even if the banner goes and comes back meanwhile."""
        noon = automation("noon", [{"type": "time", "from": "12:00", "to": "12:05"},
                                   {"type": "variable", "name": "mood", "compare": "<", "value": 0.5}],
                          [{"do": "fade_black", "fade": 1.0}])
        self.make(datetime(2026, 11, 8, 11, 58), show_automations=[noon])
        self.send_base("red")
        self.run_until(datetime(2026, 11, 8, 11, 59, 48))
        self.assertIsNotNone(self.banner())
        self.send(CountdownAction("skip"))
        self.send(SetVariable("mood", 0.9))  # (the banner goes: it won't run now)
        self.run_for(2.0)
        self.send(SetVariable("mood", 0.1))  # (and comes back)
        self.run_until(datetime(2026, 11, 8, 12, 0, 10))
        self.assertFalse(self.engine.frames.get().colour_black, "still skipped")

    def test_waking_from_sleep_drops_a_countdown(self) -> None:
        from centralight.commands import PowerEvent

        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(3.0)
        self.send(CountdownAction("wait"))
        self.send(PowerEvent("woke"))
        self.run_for(700.0, step=1.0)
        self.assertFalse(self.engine.frames.get().colour_black, "a countdown from before the sleep is stale")

    def test_a_shorter_countdown_hurries_one_under_way(self) -> None:
        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(2.0)
        self.send(SetCountdownLength(0))
        self.run_for(2.5)
        self.assertTrue(self.engine.frames.get().colour_black, "0: no countdown, so it happens now")

    def test_two_countdowns_and_their_ids(self) -> None:
        both = [LOAD_EVENING]
        fade = automation("fade", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                          [{"do": "fade_black", "fade": 1.0}])
        self.make(datetime(2026, 11, 8, 17, 58), show_automations=[fade], global_automations=both)
        self.send_base("red")
        self.run_until(datetime(2026, 11, 8, 17, 59, 50))
        banner = self.banner()
        self.assertEqual(banner.more, 1)
        first = banner.id
        self.send(CountdownAction("skip", first))
        self.send(CountdownAction("skip", first))  # (a second tap on the same one: it's gone, nothing else is skipped)
        self.assertIsNotNone(self.banner())
        self.assertNotEqual(self.banner().id, first)

    def test_api_names_the_countdown_it_saw(self) -> None:
        from centralight.remote import command_for, command_from_json

        self.started(FADE_WHEN_MOOD)
        self.send(SetVariable("mood", 0.9))
        self.run_for(2.0)
        frame = self.engine.frames.get()
        self.assertEqual(command_for(["countdown", "skip"], {"id": "s:r99"}, self.engine.show, frame),
                         CountdownAction("skip", "s:r99"))
        self.assertEqual(command_from_json({"op": "countdown", "state": "wait"}, self.engine.show, frame),
                         CountdownAction("wait", frame.countdown.id))


class BannerTests(unittest.TestCase):
    """The window's banner and the Stream Deck / phone page routes."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            window.linked_media.close()
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self):
        from pathlib import Path

        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        show = a_show("Morning", MORNING, [solid("red", RED)], [FADE_WHEN_MOOD],
                      variables=(GlobalVar("mood", "number", 0.0, 0.0, 1.0),))
        engine = FakeEngine(show)
        settings = Settings()
        settings.countdown = 20
        window = MainWindow(settings, show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        engine.settle(61.0, step=0.5)  # (past the start-up quiet)
        from centralight.commands import SendToBase

        engine.submit(SendToBase("red", cut=True))
        engine.submit(SetVariable("mood", 0.9))
        engine.settle(1.5, step=0.25)
        window._on_frame(engine.frames.get())
        return window, engine

    def test_the_banner_counts_down_and_its_buttons_work(self) -> None:
        window, engine = self.make()
        self.assertEqual(engine.countdown_seconds, 20.0, "the length comes from Settings")
        banner = window.countdown_banner
        self.assertTrue(banner.isVisibleTo(window))
        self.assertIn("Fading to black in 0:", banner.text.text())
        self.assertEqual(banner.now_button.text(), "Do it now")
        banner.wait_button.click()
        engine.settle(0.5, step=0.25)
        window._on_frame(engine.frames.get())
        self.assertFalse(banner.isVisibleTo(window), "10 minutes later now")
        self.assertEqual(len(engine._runner.countdown_items()), 1, "still waiting, in the runner's queue")

    def test_skip_from_the_banner(self) -> None:
        window, engine = self.make()
        window.countdown_banner.skip_button.click()
        engine.settle(25.0, step=0.5)
        window._on_frame(engine.frames.get())
        self.assertFalse(window.countdown_banner.isVisibleTo(window))
        self.assertFalse(engine.frames.get().colour_black)

    def test_the_lock_leaves_wait_and_skip(self) -> None:
        window, _engine = self.make()
        window.set_locked(True)
        self.assertFalse(window.countdown_banner.now_button.isEnabled())
        self.assertTrue(window.countdown_banner.skip_button.isEnabled())

    def test_api_routes(self) -> None:
        from centralight.remote import BadRequest, command_for, state_of

        _window, engine = self.make()
        frame = engine.frames.get()
        command = command_for(["countdown", "now"], {}, engine.show, frame)
        self.assertEqual(command, CountdownAction("now", frame.countdown.id))
        self.assertEqual(command_for(["countdown", "skip"], {}, engine.show, frame).what, "skip")
        self.assertEqual(command_for(["countdown", "wait"], {}, engine.show, frame).what, "wait")
        with self.assertRaises(BadRequest):
            command_for(["countdown", "later"], {}, engine.show, frame)
        state = state_of(engine)
        self.assertEqual(state["countdown"]["text"], "Fading to black")
        engine.submit(command)
        engine.settle(0.5, step=0.25)
        self.assertIsNone(engine.frames.get().countdown)
        with self.assertRaises(BadRequest):
            command_for(["countdown", "now"], {}, engine.show, engine.frames.get())
        self.assertIsNone(state_of(engine)["countdown"])
