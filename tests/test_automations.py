"""Automations, driven by a fake clock that follows UK time (with the clock changes):
when they run, catch-up, events, Invert / Any, overnight ranges, clock-change days, sleep and
wake, order, pausing, delays, the loop guard, the files and the "Upcoming" list."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from centralight.automations import (
    AutomationRunner,
    Situation,
    check_action,
    check_condition,
    describe_condition,
    summary,
    sun_threshold,
    upcoming,
)
from centralight.commands import PowerEvent, RunAutomation, SendToBase, SetAutomationsPaused, SetHouse, SwitchLayout, ToBlack
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import Automation, GlobalVar, Input, Show
from centralight.new_show import create_show, show_from_patch
from centralight.show_io import automation_from_dict, automation_to_dict, freeze, load_show, save_whole_show
from centralight.sun import sun_times
from tests.fakes import FakeOutput


def _last_sunday(year: int, month: int) -> datetime:
    day = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def uk_local(epoch: float) -> datetime:
    """UK local time: BST (UTC+1) from 01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October."""
    utc = datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)
    summer = _last_sunday(utc.year, 3).replace(hour=1) <= utc < _last_sunday(utc.year, 10).replace(hour=1)
    return utc + timedelta(hours=1 if summer else 0)


def uk_epoch(local: datetime) -> float:
    """The first moment UK clocks showed `local` (or, for a time the clocks skipped, just after)."""
    for offset in (1, 0):  # try BST first: when the clocks go back, that's the first time it's shown
        guess = (local - timedelta(hours=offset)).replace(tzinfo=timezone.utc).timestamp()
        if uk_local(guess) == local:
            return guess
    return (local - timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp()


class Clocks:
    """The engine's clock and the wall clock, moved together."""

    def __init__(self, start: datetime) -> None:
        self.mono = 1000.0
        self.wall = uk_epoch(start)
        self.uptime_value = 3600.0

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds
        self.uptime_value += seconds

    def jump_wall(self, seconds: float) -> None:
        self.wall += seconds  # the PC slept: the engine's clock stood still

    def local(self) -> datetime:
        return uk_local(self.wall)


def paint(input_id: str) -> Input:
    return Input(id=input_id, name=input_id.replace("-", " ").title(),
                 tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}}], [], [], []]))


def automation(automation_id: str, conditions: list, actions: list, logic: str = "all", on_end: list = (), enabled: bool = True) -> Automation:
    return Automation(automation_id, automation_id.replace("-", " ").title(), enabled, logic, freeze(list(conditions)), freeze(list(actions)), freeze(list(on_end)))


SUNDAY_MORNING = [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}]
WELCOME = [{"do": "base", "input": "warm-welcome"}, {"do": "house", "light": "dimmer-4", "level": 0.7}]


class AutomationEngineTestCase(unittest.TestCase):
    def make(self, start: datetime, automations: list[Automation], live: LiveState | None = None, uptime: float = 3600.0,
             global_automations: list[Automation] = ()) -> Engine:
        """uptime: how long Windows has been running (under 10 minutes: the PC has just started)."""
        inputs = {i.id: i for i in (paint("warm-welcome"), paint("evening"), paint("ping"), paint("pong"))}
        show = show_from_patch().with_inputs(inputs, tuple(inputs))
        show = show.with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))
        from dataclasses import replace

        self.show = replace(show, globals=(GlobalVar("mood", "number", 0.0, 0.0, 1.0),))
        self.global_automations = Show(name="Global automations").with_automations(
            {a.id: a for a in global_automations}, tuple(a.id for a in global_automations))
        self.clocks = Clocks(start)
        self.clocks.uptime_value = uptime
        self.output = ReconnectingOutput()
        self.engine = Engine(self.show, self.output, live, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                             local_time=uk_local, uptime=lambda: self.clocks.uptime_value,
                             global_automations=self.global_automations)
        return self.engine

    def run_until(self, until: datetime, step: float = 30.0) -> None:
        while self.clocks.local() < until:
            self.clocks.advance(step)
            self.engine.tick()

    def base(self) -> str:
        current = self.engine._base.current
        return current.input.id if current is not None else ""

    def runs(self, automation_id: str) -> list:
        return [h for h in self.engine._runner.history if h.automation == automation_id and h.what == "ran"]

    def global_runs(self, automation_id: str) -> list:
        return [h for h in self.engine._global_runner.history if h.automation == automation_id and h.what == "ran"]


class ReconnectingOutput(FakeOutput):
    def __init__(self) -> None:
        super().__init__()
        self.reconnects = 0

    def reconnect(self) -> None:
        self.reconnects += 1


class WhenTheyRunTests(AutomationEngineTestCase):
    def test_sunday_morning_runs_at_nine_and_not_again_until_next_sunday(self) -> None:
        engine = self.make(datetime(2026, 11, 8, 8, 58), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        engine.tick()
        self.assertEqual(self.base(), "")
        self.run_until(datetime(2026, 11, 8, 9, 0, 30))
        self.assertEqual(self.base(), "warm-welcome")
        self.assertEqual(self.output.last[3], 179)  # key lights at 70 %
        self.run_until(datetime(2026, 11, 8, 13, 0))
        self.assertEqual(len(self.runs("welcome")), 1)  # once, though it stayed true all morning
        self.clocks.advance(4 * 86400)  # Thursday (a jump: the engine sees it as waking)
        engine.tick()
        self.run_until(datetime(2026, 11, 12, 10, 0))
        self.assertEqual(len(self.runs("welcome")), 1)
        self.clocks.advance(3 * 86400 - 3600 * 1.5)  # next Sunday 08:30
        engine.tick()
        self.run_until(datetime(2026, 11, 15, 9, 0, 30))
        self.assertEqual(len(self.runs("welcome")), 2)

    def test_catches_up_when_the_app_starts_at_ten(self) -> None:
        self.make(datetime(2026, 11, 8, 10, 0), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        self.engine.tick()
        self.assertEqual(self.base(), "warm-welcome")
        # a quick restart (live state saved a minute ago) doesn't run it again
        saved = LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))
        self.clocks.advance(60)
        again = Engine(self.show, FakeOutput(), saved, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                       local_time=uk_local, uptime=lambda: 9999.0)
        again.tick()
        self.assertEqual([h.what for h in again._runner.history].count("ran"), 1)  # still just the first run
        # nor does a longer gap in the same morning (it would undo whatever someone chose since)
        self.clocks.advance(3600)
        later = Engine(self.show, FakeOutput(), saved, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                       local_time=uk_local, uptime=lambda: 9999.0)
        later.tick()
        self.assertEqual([h.what for h in later._runner.history].count("ran"), 1)
        # but if the PC was off until the next Sunday morning, that's a new occurrence: it catches up
        self.clocks.advance(7 * 86400 - 3600)
        week = Engine(self.show, FakeOutput(), saved, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                      local_time=uk_local, uptime=lambda: 9999.0)
        week.tick()
        self.assertEqual([h.what for h in week._runner.history].count("ran"), 2)

    def test_pc_startup_and_sunday_runs_once(self) -> None:
        # (a show's "The PC starts" is "When this show loads" now: it loads at PC start)
        both = automation("startup", [{"type": "show_loads"}, {"type": "days", "days": ["sun"]}], [{"do": "base", "input": "evening"}])
        self.make(datetime(2026, 11, 8, 18, 0), [both], uptime=120.0)
        self.engine.tick()
        self.assertEqual(self.base(), "evening")
        self.run_until(datetime(2026, 11, 8, 18, 30))
        self.assertEqual(len(self.runs("startup")), 1)
        self.make(datetime(2026, 11, 9, 18, 0), [both], uptime=120.0)  # a Monday
        self.engine.tick()
        self.assertEqual(self.base(), "")
        self.make(datetime(2026, 11, 8, 18, 0), [both], uptime=4000.0)  # Windows started over 10 minutes ago
        self.engine.tick()
        self.assertEqual(self.base(), "")

    def test_app_started_runs_on_every_start(self) -> None:
        hello = automation("hello", [{"type": "app_started"}], [{"do": "master", "level": 0.4}])  # (a global automation's)
        self.make(datetime(2026, 11, 9, 18, 0), [], uptime=99999.0, global_automations=[hello])
        self.engine.tick()
        self.assertAlmostEqual(self.engine._master.target, 0.4)
        self.assertEqual(len(self.global_runs("hello")), 1)

    def test_invert_and_any(self) -> None:
        weekend = automation("weekend", [{"type": "days", "days": ["sat"]}, {"type": "days", "days": ["sun"]}],
                             [{"do": "base", "input": "evening"}], logic="any")
        not_black = automation("lit", [{"type": "colour_black", "invert": True}, {"type": "time", "from": "18:00", "to": "19:00"}],
                               [{"do": "house", "light": "dimmer-1", "level": 0.5}])
        self.make(datetime(2026, 11, 7, 17, 59), [weekend, not_black])  # Saturday
        self.engine.tick()
        self.assertEqual(self.base(), "evening")
        self.engine.submit(ToBlack(0.0))  # the colour lights cut to black
        self.run_until(datetime(2026, 11, 7, 18, 5))
        self.assertEqual(self.runs("lit"), [])  # inverted: the colour lights are black, so no
        self.engine.submit(SendToBase("evening", cut=True))  # something on Base again
        self.clocks.advance(1)
        self.engine.tick()
        self.assertEqual(len(self.runs("lit")), 1)
        self.make(datetime(2026, 11, 9, 12, 0), [weekend])  # Monday: neither
        self.engine.tick()
        self.assertEqual(self.base(), "")

    def test_overnight_range(self) -> None:
        late = automation("late", [{"type": "time", "from": "22:00", "to": "02:00"}], [{"do": "base", "input": "evening"}],
                          on_end=[{"do": "base", "input": "ping"}])
        self.make(datetime(2026, 11, 9, 21, 59), [late])
        self.run_until(datetime(2026, 11, 9, 22, 1))
        self.assertEqual(self.base(), "evening")
        self.run_until(datetime(2026, 11, 10, 1, 59))  # through midnight: still the same night, no second run
        self.assertEqual(len(self.runs("late")), 1)
        self.run_until(datetime(2026, 11, 10, 2, 1))
        self.assertEqual(self.base(), "ping")  # "when it stops matching"
        self.assertEqual([h.what for h in self.engine._runner.history], ["ran", "stopped matching"])

    def test_spring_forward_runs_a_skipped_time_at_the_next_valid_minute(self) -> None:
        gone = automation("skipped", [{"type": "time", "from": "01:30", "to": "01:45"}], [{"do": "base", "input": "evening"}])
        self.make(datetime(2026, 3, 29, 0, 50), [gone])  # the clocks go forward at 01:00 that night
        self.run_until(datetime(2026, 3, 29, 2, 0, 20), step=20.0)
        self.assertEqual(self.base(), "evening")
        self.assertEqual(self.clocks.local().hour, 2)
        self.assertEqual(len(self.runs("skipped")), 1)

    def test_fall_back_runs_a_repeated_time_once(self) -> None:
        twice = automation("repeated", [{"type": "time", "from": "01:30", "to": "01:45"}], [{"do": "base", "input": "evening"}])
        self.make(datetime(2026, 10, 25, 0, 50), [twice])  # the clocks go back at 02:00 BST that night
        end = uk_epoch(datetime(2026, 10, 25, 3, 0))
        while self.clocks.wall < end:
            self.clocks.advance(30)
            self.engine.tick()
        self.assertEqual(len(self.runs("repeated")), 1)  # 01:30 came round twice, but it ran once

    def test_dates_across_new_year_and_a_set_year(self) -> None:
        christmas = automation("christmas", [{"type": "dates", "from": "12-01", "to": "01-06"}], [{"do": "base", "input": "evening"}])
        only_2026 = automation("this-year", [{"type": "dates", "from": "12-24", "to": "12-26", "year": 2026}], [{"do": "base", "input": "ping"}])
        runner = AutomationRunner()
        for day, wanted in ((date(2026, 12, 1), True), (date(2027, 1, 6), True), (date(2027, 1, 7), False), (date(2026, 11, 30), False)):
            situation = Situation(now=0.0, local=datetime.combine(day, datetime.min.time()))
            self.assertEqual(runner.matches(christmas, situation)[0], wanted, day)
        self.assertTrue(runner.matches(only_2026, Situation(now=0.0, local=datetime(2026, 12, 25)))[0])
        self.assertFalse(runner.matches(only_2026, Situation(now=0.0, local=datetime(2027, 12, 25)))[0])


class SleepWakeAndControlTests(AutomationEngineTestCase):
    def test_waking_runs_pc_woke_and_reconnects(self) -> None:
        woke = automation("woke", [{"type": "pc_woke"}], [{"do": "master", "level": 0.5}])  # (a global automation's)
        engine = self.make(datetime(2026, 11, 9, 20, 0), [], global_automations=[woke])
        engine.tick()
        self.assertAlmostEqual(engine._master.target, 1.0)
        self.clocks.jump_wall(2 * 3600)  # asleep for two hours
        self.clocks.advance(0.025)
        engine.tick()
        self.assertAlmostEqual(engine._master.target, 0.5)
        self.assertEqual(self.output.reconnects, 1)
        engine.submit(PowerEvent("woke"))  # Windows' "resumed" message for the same wake: still one run
        self.clocks.advance(0.025)
        engine.tick()
        self.assertEqual(len(self.global_runs("woke")), 1)
        self.clocks.advance(5.0)
        engine.tick()
        engine.submit(PowerEvent("woke"))  # Windows' second message for the same wake, a few seconds on: still one
        self.clocks.advance(0.025)
        engine.tick()
        self.assertEqual(len(self.global_runs("woke")), 1)
        self.clocks.advance(120.0)
        engine.tick()
        engine.submit(PowerEvent("woke"))  # a later wake, reported by Windows: runs again
        self.clocks.advance(0.025)
        engine.tick()
        self.assertEqual(len(self.global_runs("woke")), 2)
        self.assertEqual(self.output.reconnects, 2)

    def test_catch_up_after_waking(self) -> None:
        engine = self.make(datetime(2026, 11, 8, 8, 0), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        engine.tick()
        self.clocks.jump_wall(2 * 3600)  # slept from 08:00 to 10:00
        self.clocks.advance(0.025)
        engine.tick()
        self.assertEqual(self.base(), "warm-welcome")

    def test_order_later_ones_win(self) -> None:
        first = automation("first", [{"type": "show_loads"}], [{"do": "base", "input": "ping", "transition": "cut"}])
        second = automation("second", [{"type": "show_loads"}], [{"do": "base", "input": "pong", "transition": "cut"}])
        self.make(datetime(2026, 11, 9, 12, 0), [first, second], uptime=120.0)  # (the PC has just started: the show loads)
        self.engine.tick()
        self.assertEqual(self.base(), "pong")

    def test_manual_control_wins(self) -> None:
        engine = self.make(datetime(2026, 11, 8, 9, 30), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        engine.tick()
        engine.submit(SetHouse("dimmer-4", level=0.2))
        self.run_until(datetime(2026, 11, 8, 10, 0))
        self.assertEqual(self.output.last[3], 51)  # the automation didn't put it back

    def test_pause_and_resume(self) -> None:
        engine = self.make(datetime(2026, 11, 8, 8, 59), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        engine.tick()
        engine.submit(SetAutomationsPaused(True))
        self.run_until(datetime(2026, 11, 8, 9, 2))
        self.assertEqual(self.base(), "")
        self.assertTrue(engine.frames.get().automations.paused)
        engine.submit(SetAutomationsPaused(False))
        self.run_until(datetime(2026, 11, 8, 9, 5))
        self.assertEqual(self.base(), "")  # it became true while paused: resuming doesn't run it

    def test_delays(self) -> None:
        later = automation("later", [{"type": "show_loads"}], [{"do": "base", "input": "ping", "transition": "cut", "delay": 10}])
        self.make(datetime(2026, 11, 9, 12, 0), [later], uptime=120.0)
        self.engine.tick()
        self.run_until(datetime(2026, 11, 9, 12, 0, 9), step=1.0)
        self.assertEqual(self.base(), "")
        self.run_until(datetime(2026, 11, 9, 12, 0, 11), step=1.0)
        self.assertEqual(self.base(), "ping")

    def test_run_now_and_window_actions(self) -> None:
        both = automation("by-hand", [], [{"do": "base", "input": "pong", "transition": "cut"}, {"do": "layout", "layout": "main"}])
        engine = self.make(datetime(2026, 11, 9, 12, 0), [both])
        engine.tick()
        self.assertEqual(self.base(), "")  # no conditions: only by hand
        engine.submit(RunAutomation("by-hand"))
        engine.tick()
        self.assertEqual(self.base(), "pong")
        self.assertEqual(engine.gui_requests.get_nowait(), ("By Hand", {"do": "layout", "layout": "main", "_scope": "show"}))
        engine.submit(SwitchLayout("christmas"))  # from the Stream Deck
        engine.tick()
        self.assertEqual(engine.gui_requests.get_nowait(), ("Stream Deck", {"do": "layout", "layout": "christmas"}))

    def test_loop_guard_stops_a_pair_that_trigger_each_other(self) -> None:
        ping = automation("ping", [{"type": "input_live", "input": "ping", "where": "base"}], [{"do": "base", "input": "pong", "transition": "cut"}])
        pong = automation("pong", [{"type": "input_live", "input": "pong", "where": "base"}], [{"do": "base", "input": "ping", "transition": "cut"}])
        engine = self.make(datetime(2026, 11, 9, 12, 0), [ping, pong])
        engine.submit(RunAutomation("pong"))  # start the ping-pong
        for _ in range(200):
            self.clocks.advance(0.025)
            engine.tick()
        statuses = engine.frames.get().automations.statuses
        self.assertTrue(statuses["ping"].tripped or statuses["pong"].tripped)
        guarded = [h for h in engine._runner.history if h.what == "paused by the loop guard"]
        self.assertTrue(guarded)
        settled = self.base()
        for _ in range(80):
            self.clocks.advance(0.025)
            engine.tick()
        self.assertEqual(self.base(), settled)  # it has stopped


class DataTests(unittest.TestCase):
    def test_checks(self) -> None:
        self.assertEqual(check_condition({"type": "time", "from": "09:00", "to": "12:30"}), [])
        self.assertTrue(check_condition({"type": "time", "from": "9am", "to": "12:30"}))
        self.assertTrue(check_condition({"type": "days", "days": ["sunday"]}))
        self.assertTrue(check_condition({"type": "dates", "from": "02-30", "to": "03-01"}))
        self.assertTrue(check_condition({"type": "teleport"}))
        self.assertEqual(check_action({"do": "house", "light": "dimmer-4", "level": 0.7, "fade": 2}), [])
        self.assertTrue(check_action({"do": "overlay"}))  # (Send to Overlay needs an input)
        self.assertTrue(check_action({"do": "overlay_clear"}))
        self.assertEqual(check_action({"do": "overlay_clear_all"}), [])
        self.assertTrue(check_action({"do": "overlay_off", "slot": 1}))  # (before M14: converted as a file is read)
        self.assertTrue(check_action({"do": "base"}))

    def test_files_round_trip_and_bad_ones(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            show = create_show(Path(folder) / "Show")
            welcome = automation("welcome", SUNDAY_MORNING, WELCOME)
            show = show.with_automations({"welcome": welcome}, ("welcome",))
            save_whole_show(show)
            again = load_show(Path(folder) / "Show")
            self.assertEqual(again.automations["welcome"], welcome)
            self.assertEqual(again.automation_order, ("welcome",))
            data = automation_to_dict(welcome)
            data["conditions"].append({"type": "days", "days": ["funday"]})
            data["actions"].append({"do": "teleport"})
            broken, warnings = automation_from_dict(data)
            self.assertFalse(broken.enabled)  # a broken condition switches it off rather than dropping it
            self.assertEqual(len(broken.conditions), 3)
            self.assertEqual(len(broken.actions), 2)  # the broken action is left out
            self.assertEqual(len(warnings), 3)

    def test_summary(self) -> None:
        show = show_from_patch().with_inputs({"warm-welcome": paint("warm-welcome")}, ("warm-welcome",))
        text = summary(automation("welcome", SUNDAY_MORNING, WELCOME), show)
        self.assertEqual(text, "Sun and 09:00–12:30 → Warm Welcome to Base, Key lights 70 %")

    def test_upcoming(self) -> None:
        show = show_from_patch().with_automations(
            {"welcome": automation("welcome", SUNDAY_MORNING, WELCOME),
             "evening": automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "17:30", "to": "20:00"},
                                               {"type": "colour_black", "invert": True}], [])},
            ("welcome", "evening"))
        found = upcoming(show, datetime(2026, 11, 4, 15, 0))  # a Wednesday
        self.assertEqual([(when, a.id, others) for when, a, others in found], [
            (datetime(2026, 11, 8, 9, 0), "welcome", False),
            (datetime(2026, 11, 8, 17, 30), "evening", True),
        ])



class SunTests(unittest.TestCase):
    """Sunrise / sunset. Times are the PC's local times (like Windows' clock), so these compare against sun_times."""

    def true_at(self, conditions: list, local: datetime, logic: str = "all") -> bool:
        return AutomationRunner().matches(automation("sun", conditions, [], logic), Situation(now=0.0, local=local))[0]

    def test_after_sunset_and_before_sunrise(self) -> None:
        day = date(2026, 11, 8)
        rise, sunset = sun_times(day)
        after = [{"type": "sun", "when": "after", "event": "sunset", "minutes": 0}]
        self.assertFalse(self.true_at(after, sunset - timedelta(minutes=1)))
        self.assertTrue(self.true_at(after, sunset.replace(second=0)))
        self.assertTrue(self.true_at(after, datetime(2026, 11, 8, 23, 59)))
        self.assertFalse(self.true_at(after, datetime(2026, 11, 9, 0, 0)))  # it lasts until midnight
        before = [{"type": "sun", "when": "before", "event": "sunrise", "minutes": 0}]
        self.assertTrue(self.true_at(before, datetime(2026, 11, 8, 0, 0)))
        self.assertTrue(self.true_at(before, rise - timedelta(minutes=1)))
        self.assertFalse(self.true_at(before, rise.replace(second=0)))
        night = after + before  # all night, across midnight
        for moment in (sunset + timedelta(minutes=5), datetime(2026, 11, 8, 23, 59), datetime(2026, 11, 9, 0, 0),
                       datetime(2026, 11, 9, 3, 0)):
            self.assertTrue(self.true_at(night, moment, "any"), moment)
        self.assertFalse(self.true_at(night, datetime(2026, 11, 8, 12, 0), "any"))

    def test_minutes_before_and_after(self) -> None:
        day = date(2026, 6, 21)
        sunset = sun_times(day)[1].replace(second=0)
        half_hour_before = [{"type": "sun", "when": "after", "event": "sunset", "minutes": -30}]
        self.assertFalse(self.true_at(half_hour_before, sunset - timedelta(minutes=31)))
        self.assertTrue(self.true_at(half_hour_before, sunset - timedelta(minutes=30)))
        late = {"type": "sun", "when": "after", "event": "sunset", "minutes": 180}  # 00:32 tomorrow: clamped to midnight
        self.assertEqual(sun_threshold(late, day), 24 * 60)
        self.assertFalse(self.true_at([late], datetime(2026, 6, 21, 23, 59)))

    def test_on_a_clock_change_day(self) -> None:
        for day in (date(2026, 3, 29), date(2026, 10, 25)):  # the last Sundays of March and October
            rise, sunset = sun_times(day)
            self.assertLess(rise, sunset)
            self.assertTrue(6 <= rise.hour <= 8, rise)
            self.assertTrue(16 <= sunset.hour <= 20, sunset)

    def test_checks_and_words(self) -> None:
        good = {"type": "sun", "when": "before", "event": "sunrise", "minutes": 15}
        self.assertEqual(check_condition(good), [])
        self.assertEqual(check_condition({"type": "sun"}), [])  # defaults: after sunset
        self.assertTrue(check_condition({**good, "event": "noon"}))
        self.assertTrue(check_condition({**good, "when": "during"}))
        self.assertTrue(check_condition({**good, "minutes": 500}))
        self.assertTrue(check_condition({**good, "minutes": 1.5}))
        self.assertEqual(describe_condition(good), "before sunrise + 15 min")
        self.assertEqual(describe_condition({**good, "when": "after", "event": "sunset", "minutes": -30}), "after sunset − 30 min")
        self.assertEqual(describe_condition({"type": "sun", "when": "after", "event": "sunset", "minutes": 0}), "after sunset")

    def test_upcoming(self) -> None:
        cond = {"type": "sun", "when": "after", "event": "sunset", "minutes": -15}
        show = show_from_patch().with_automations({"dusk": automation("dusk", [cond], [])}, ("dusk",))
        start = datetime(2026, 11, 4, 12, 0)
        found = upcoming(show, start, days=3)
        expected = [datetime(2026, 11, 4) + timedelta(days=d, minutes=sun_threshold(cond, date(2026, 11, 4 + d))) for d in range(4)]
        self.assertEqual([when for when, _a, _others in found], [m for m in expected if start < m <= start + timedelta(days=3)])


if __name__ == "__main__":
    unittest.main()
