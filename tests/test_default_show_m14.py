"""M14 item 5: a default show. It opens when the PC starts (instead of the last show), and loads again when the global
automation that loaded a show stops matching, with the countdown, unless someone has loaded a show by hand since,
that automation's own "when it stops matching" loads a show, or another automation that loads a show matches then.
The week schedule shows it in the gaps, lighter. And two shows asked for in the same second both load, in order."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from centralight.automations import WEEKS, AutomationRunner, Situation, schedules_a_show
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import Show
from centralight.new_show import create_show
from centralight.schedule import describe_block, week_schedule
from centralight.settings import Settings
from tests.fakes import FakeOutput
from tests.test_automations import AutomationEngineTestCase, automation, uk_local
from tests.test_shows import FilesCase, WindowCase

SUNDAY = date(2026, 11, 8)
EVERY_DAY = {"type": "days", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}


def morning(**extra):
    return automation("sunday-morning", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                      [{"do": "load_show", "show": "morning"}], **extra)


def container(*automations) -> Show:
    return Show(name="Global").with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = AutomationRunner()
        self.runner.default_show = "central-church"
        self.mono = 1000.0

    def check(self, global_automations: Show, hhmm: str, day: date = SUNDAY) -> list[dict]:
        hours, minutes = (int(x) for x in hhmm.split(":"))
        local = datetime.combine(day, datetime.min.time()) + timedelta(hours=hours, minutes=minutes)
        self.mono += 60.0
        actions = self.runner.check(global_automations, Situation(now=local.timestamp(), local=local), self.mono)
        actions += self.runner.due(self.mono)
        return [action for _who, action in actions]

    def loads(self, actions: list[dict]) -> list[str]:
        return [a["show"] for a in actions if a.get("do") == "load_show"]

    def test_the_default_loads_when_the_schedule_ends(self) -> None:
        shows = container(morning())
        self.assertEqual(self.loads(self.check(shows, "10:00")), ["morning"])
        self.assertEqual(self.runner.loader, "sunday-morning")
        self.assertEqual(self.check(shows, "11:00"), [])
        ended = self.check(shows, "12:31")
        self.assertEqual(self.loads(ended), ["central-church"])
        self.assertTrue(ended[0].get("_default"))
        self.assertIsNone(self.runner.loader)
        self.assertEqual(self.check(shows, "13:00"), [])  # (once)

    def test_no_default_show_no_change(self) -> None:
        self.runner.default_show = ""
        shows = container(morning())
        self.check(shows, "10:00")
        self.assertEqual(self.check(shows, "12:31"), [])

    def test_manual_wins(self) -> None:
        shows = container(morning())
        self.check(shows, "10:00")
        self.runner.manual_load()  # (someone loaded Evening from the Show menu)
        self.assertEqual(self.check(shows, "12:31"), [])

    def test_its_own_when_it_stops_matching_wins(self) -> None:
        shows = container(morning(on_end=[{"do": "load_show", "show": "evening"}]))
        self.check(shows, "10:00")
        self.assertEqual(self.loads(self.check(shows, "12:31")), ["evening"])

    def test_another_schedule_that_matches_keeps_its_show(self) -> None:
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:00", "to": "14:00"}],
                           [{"do": "load_show", "show": "youth"}])
        shows = container(morning(), youth)
        self.check(shows, "10:00")
        self.assertEqual(self.loads(self.check(shows, "12:01")), ["youth"])  # (Youth starts while Morning still matches)
        self.assertEqual(self.check(shows, "12:31"), [])  # (Morning ends, but Youth matches: its show stays)
        self.assertEqual(self.loads(self.check(shows, "14:01")), ["central-church"])  # (Youth loaded the show; it ends)

    def test_a_moment_isnt_a_schedule(self) -> None:
        started = automation("on-start", [{"type": "app_started"}], [{"do": "load_show", "show": "evening"}])
        self.assertFalse(schedules_a_show(started))
        self.assertTrue(schedules_a_show(morning()))
        self.assertIsNone(self.runner.loader)

    def test_the_countdown_and_a_show_loaded_by_hand_meanwhile(self) -> None:
        shows = container(morning())
        self.check(shows, "10:00")
        self.runner.countdown = 15.0  # (the default show's load gets the usual countdown)
        self.runner.counted = lambda action: action.get("do") == "load_show"
        self.assertEqual(self.check(shows, "12:31"), [])  # (it waits for its countdown)
        self.assertTrue(any(p[3].get("_default") for p in self.runner.pending))
        self.runner.manual_load()  # (someone loads a show during the countdown: the default doesn't come)
        self.assertFalse(any(p[3].get("_default") for p in self.runner.pending))

    def test_back_to_back_schedules_in_either_order(self) -> None:
        """(Review: in the 12:30 check the new schedule made itself the loader, then the old one's end cleared it, so
        the default never came after 14:00. It depended on their order in the list.)"""
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:30", "to": "14:00"}],
                           [{"do": "load_show", "show": "youth"}])
        for shows in (container(morning(), youth), container(youth, morning())):
            self.setUp()
            self.check(shows, "10:00")
            self.assertEqual(self.loads(self.check(shows, "12:30")), ["youth"])
            self.assertEqual(self.runner.loader, "youth")
            self.assertEqual(self.loads(self.check(shows, "14:01")), ["central-church"])

    def test_the_longer_schedule_gets_its_show_back(self) -> None:
        """A schedule inside a longer one ends: the longer one's show comes back, and the default waits for it."""
        sunday = automation("sunday", [{"type": "days", "days": ["sun"]}], [{"do": "load_show", "show": "sunday"}])
        evening = automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                             [{"do": "load_show", "show": "evening"}])
        shows = container(sunday, evening)
        self.assertEqual(self.loads(self.check(shows, "10:00")), ["sunday"])
        self.assertEqual(self.loads(self.check(shows, "18:00")), ["evening"])
        back = self.check(shows, "20:00")
        self.assertEqual(self.loads(back), ["sunday"])
        self.assertTrue(back[0].get("_default"))  # (so a show loaded by hand meanwhile, or a new schedule, wins)
        self.assertEqual(self.runner.loader, "sunday")
        self.assertEqual(self.loads(self.check(shows, "00:01", SUNDAY + timedelta(days=1))), ["central-church"])

    def test_a_waiting_default_goes_when_another_schedule_starts(self) -> None:
        """(Review: someone pressed Wait 10 min on the default's countdown, Youth started meanwhile, and the default
        then loaded over Youth.)"""
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:35", "to": "14:00"}],
                           [{"do": "load_show", "show": "youth"}])
        shows = container(morning(), youth)
        self.check(shows, "10:00")
        self.runner.countdown = 600.0
        self.runner.counted = lambda action: action.get("do") == "load_show"
        self.assertEqual(self.check(shows, "12:31"), [])
        self.assertTrue(any(p[3].get("_default") for p in self.runner.pending))
        self.check(shows, "12:35")
        self.assertFalse(any(p[3].get("_default") for p in self.runner.pending))
        self.assertTrue(any(p[3].get("show") == "youth" for p in self.runner.pending))  # (Youth's, after its countdown)
        self.assertEqual(self.runner.loader, "youth")

    def test_a_default_due_just_as_another_schedule_starts_doesnt_load(self) -> None:
        early = automation("early", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:29"}],
                           [{"do": "load_show", "show": "morning"}])
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:30", "to": "14:00"}],
                           [{"do": "load_show", "show": "youth"}])
        shows = container(early, youth)
        self.check(shows, "10:00")
        self.runner.countdown = 60.0
        self.runner.counted = lambda action: action.get("do") == "load_show"
        self.assertEqual(self.check(shows, "12:29"), [])  # (the default waits for its minute...)
        self.assertNotIn("central-church", self.loads(self.check(shows, "12:30")))  # (...which is up as Youth starts)
        self.assertFalse(any(p[3].get("_default") for p in self.runner.pending))

    def test_only_steady_conditions_hand_back_to_the_default(self) -> None:
        """(Review: "mood above 0.5 → Load Evening", where Evening's mood is 0, flipped back and forth with the default
        every few seconds: its own load made it stop matching.)"""
        moody = automation("moody", [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                           [{"do": "load_show", "show": "evening"}])
        self.assertFalse(schedules_a_show(moody))
        for kind in ({"type": "input_live", "input": "x"}, {"type": "loaded_show", "show": "x"}, {"type": "colour_black"}):
            self.assertFalse(schedules_a_show(automation("other", [kind], [{"do": "load_show", "show": "evening"}])), kind)
        feed = automation("feed", [{"type": "ndi", "source": "ProPresenter", "invert": True}],
                          [{"do": "load_show", "show": "backup"}])
        self.assertTrue(schedules_a_show(feed))
        shows = container(moody)
        local = datetime.combine(SUNDAY, datetime.min.time()) + timedelta(hours=10)
        loads = []
        for mood in (0.7, 0.0, 0.7, 0.0):
            self.mono += 60.0
            local += timedelta(minutes=1)
            actions = self.runner.check(shows, Situation(now=local.timestamp(), local=local, variables={"mood": mood}), self.mono)
            loads.append(self.loads([a for _who, a in actions]))
        self.assertEqual(loads, [["evening"], [], ["evening"], []])  # (no default show in between)
        self.assertIsNone(self.runner.loader)

    def test_run_now_counts_as_a_person(self) -> None:
        """(Review: Run now on a global "Load Evening" during Morning, then at 12:30 the default replaced Evening.)"""
        evening = automation("evening-now", [{"type": "days", "days": ["mon"]}], [{"do": "load_show", "show": "evening"}])
        shows = container(morning(), evening)
        self.check(shows, "10:00")
        self.assertEqual(self.runner.loader, "sunday-morning")
        self.assertEqual(self.loads([a for _who, a in self.runner.run_now(evening, self.mono, 0.0)]), ["evening"])
        self.assertIsNone(self.runner.loader)
        self.assertEqual(self.check(shows, "12:31"), [])  # (Morning ends: Evening, chosen by a person, stays)

    def test_a_restart_remembers_which_automation_loaded_it(self) -> None:
        shows = container(morning())
        self.check(shows, "10:00")
        saved = self.runner.to_dict()
        again = AutomationRunner()
        again.restore(saved, datetime.combine(SUNDAY, datetime.min.time()).timestamp() + 11 * 3600)
        self.assertEqual(again.loader, "sunday-morning")


class EngineTests(unittest.TestCase):
    def test_settings_and_loads_by_hand_reach_the_runner(self) -> None:
        from centralight.commands import LoadShow, SetDefaultShow
        from tests.fakes import FakeEngine

        engine = FakeEngine()
        engine.submit(SetDefaultShow(" central-church "))
        self.assertEqual(engine._global_runner.default_show, "central-church")
        engine._global_runner.loader = "sunday-morning"
        engine.submit(LoadShow(engine.show, automation=True))
        self.assertEqual(engine._global_runner.loader, "sunday-morning")  # (an automation's load: still its schedule)
        engine.submit(LoadShow(engine.show))
        self.assertIsNone(engine._global_runner.loader)  # (a person's: manual wins)


class RestartTests(AutomationEngineTestCase):
    """The engine's side (M14 review): the default show is known from the first check, and when the PC restarts
    mid-service and the default show opens, the schedule under way loads its show again."""

    MORNING = automation("sunday-morning", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                         [{"do": "load_show", "show": "morning"}])

    def until(self, hhmm: str) -> LiveState:
        """The first run, from 08:59 on Sunday (Morning loads at 09:00), until hhmm; what it saved then."""
        import json

        hours, minutes = (int(x) for x in hhmm.split(":"))
        self.make(datetime(2026, 11, 8, 8, 59), [], global_automations=[self.MORNING])
        self.run_until(datetime(2026, 11, 8, hours, minutes))
        live = LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))
        live.saved_at = self.clocks.wall  # (as LiveState.save() stamps it)
        return live

    def reopen(self, live: LiveState, after_minutes: float, **kwargs) -> list[dict]:
        """CentraLight opens again that many minutes later; the shows its first checks ask the window for."""
        self.clocks.advance(after_minutes * 60.0)
        engine = Engine(self.show, FakeOutput(), live, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                        local_time=uk_local, uptime=lambda: self.clocks.uptime_value,
                        global_automations=self.global_automations, **kwargs)
        self.engine = engine
        for _ in range(3):
            self.clocks.advance(1.0)
            engine.tick()
        requests = []
        while not engine.gui_requests.empty():
            requests.append(engine.gui_requests.get()[1])
        return [r.get("show") for r in requests if r.get("do") == "load_show"]

    def test_a_pc_restart_mid_service_brings_the_schedules_show_back(self) -> None:
        live = self.until("10:00")
        self.clocks.uptime_value = 60.0  # (the PC restarted at 10:15)
        self.assertEqual(self.reopen(live, 15, pc_start=True, default_show="central-church", default_opened=True), ["morning"])
        self.assertEqual(self.engine._global_runner.loader, "sunday-morning")

    def test_without_the_default_show_a_restart_doesnt_run_it_again(self) -> None:
        live = self.until("10:00")
        self.clocks.uptime_value = 60.0
        self.assertEqual(self.reopen(live, 15, pc_start=True, default_show="central-church"), [])  # (the last show opened)

    def test_a_quick_reopen_after_a_crash_knows_the_default_at_once(self) -> None:
        """(Review: the window told the engine the default show only after its first check, so a crash at 12:25 and a
        reopen at 12:33, after Morning ended, left Morning on.)"""
        live = self.until("12:25")
        self.assertEqual(self.reopen(live, 8, default_show="evening"), ["evening"])  # (the test's show is Central Church)


class StartTests(FilesCase):
    def test_the_default_show_opens_when_the_pc_starts(self) -> None:
        from centralight.app import show_to_open

        create_show(self.root / "Central Church", "Central Church")
        evening = create_show(self.root / "Evening", "Evening")
        live = LiveState(show=evening.folder)
        settings = Settings(default_show="central-church")
        self.assertEqual(show_to_open(settings, live, root=self.root, pc_start=True).name, "Central Church")
        self.assertEqual(show_to_open(settings, live, root=self.root, pc_start=False).name, "Evening")  # (a reopen carries on)
        self.assertEqual(show_to_open(Settings(), live, root=self.root, pc_start=True).name, "Evening")  # (none: as before)
        gone = Settings(default_show="christmas")
        self.assertEqual(show_to_open(gone, live, root=self.root, pc_start=True).name, "Evening")

    def test_settings(self) -> None:
        settings = Settings()
        settings.update({"default_show": " evening "})
        self.assertEqual(settings.default_show, "evening")
        settings.update({"default_show": 7})
        self.assertEqual(settings.default_show, "")


class ScheduleTests(unittest.TestCase):
    def test_the_default_show_fills_the_gaps(self) -> None:
        shows = {"morning": "Morning", "central-church": "Central Church"}
        monday = date(2026, 11, 2)
        blocks, _unplaced = week_schedule([morning()], shows, monday, default_show="central-church")
        scheduled = [b for b in blocks if not b.default]
        defaults = [b for b in blocks if b.default]
        self.assertEqual([(b.start, b.end) for b in scheduled], [(datetime(2026, 11, 8, 9, 0), datetime(2026, 11, 8, 12, 30))])
        self.assertEqual([(b.start, b.end) for b in defaults],
                         [(datetime(2026, 11, 2), datetime(2026, 11, 8, 9, 0)), (datetime(2026, 11, 8, 12, 30), datetime(2026, 11, 9))])
        self.assertTrue(all(b.show_label == "Central Church" and not b.clash for b in defaults))
        self.assertIn("default show", describe_block(defaults[0]))
        stops, _unplaced = week_schedule([morning(on_end=[{"do": "load_show", "show": "evening"}])],
                                         dict(shows, evening="Evening"), monday, default_show="central-church")
        self.assertEqual([(b.start, b.end) for b in stops if b.default],  # (a "when it stops" load wins: none after it)
                         [(datetime(2026, 11, 2), datetime(2026, 11, 8, 9, 0))])
        plain, _unplaced = week_schedule([morning()], shows, monday)
        self.assertEqual(len(plain), 1)  # (no default show: as before)
        empty, _unplaced = week_schedule([], shows, monday, default_show="central-church")
        self.assertEqual([(b.start, b.end, b.continues_before, b.continues_after) for b in empty],
                         [(datetime(2026, 11, 2), datetime(2026, 11, 9), True, True)])


class WindowTests(WindowCase):
    def test_two_shows_asked_for_in_the_same_second(self) -> None:
        """The workaround the plan mentions: "When CentraLight starts → Load a show" at the top, and a schedule under way
        that loads another, both at the first check. Both load, in the automations' order, and the second stays."""
        from unittest import mock

        from centralight.ui.main_window import MainWindow
        from tests.qt_support import process

        central = create_show(self.root / "Central Church", "Central Church")
        create_show(self.root / "Evening", "Evening")
        create_show(self.root / "Morning", "Morning")
        started = automation("on-start", [{"type": "app_started"}], [{"do": "load_show", "show": "evening"}])
        always = automation("every-day", [EVERY_DAY], [{"do": "load_show", "show": "morning"}])
        real, loaded = MainWindow.load_show, []

        def spy(window_self, folder, who="", automation=False):
            done = real(window_self, folder, who, automation)
            if done:
                loaded.append((Path(folder).name, automation))
            return done

        with mock.patch.object(MainWindow, "load_show", spy):
            window, engine = self.make(central, global_automations=container(started, always))
            for _ in range(4):
                engine.settle(0.2)
                window._do_engine_requests()
                process(self.app)
        self.assertEqual(loaded, [("Evening", True), ("Morning", True)])
        self.assertEqual(window.document.show.name, "Morning")
        self.assertEqual(engine.show.name, "Morning")
        self.assertEqual(engine._global_runner.loader, "every-day")

    def test_the_settings_dialog(self) -> None:
        from centralight.ui.settings_dialog import SettingsDialog

        central = create_show(self.root / "Central Church", "Central Church")
        create_show(self.root / "Evening", "Evening")
        window, engine = self.make(central)
        dialog = SettingsDialog(window.settings, central.folder, window, shows=window.list_shows())  # (the window's)
        names = [dialog.default_show.itemText(i) for i in range(dialog.default_show.count())]
        self.assertEqual(names, ["None: open the last show", "Central Church", "Evening"])
        dialog.default_show.setCurrentIndex(dialog.default_show.findData("evening"))
        dialog.apply_to(window.settings)
        self.assertEqual(window.settings.default_show, "evening")
        self.assertTrue(WEEKS)


if __name__ == "__main__":
    unittest.main()
