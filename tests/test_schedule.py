"""The show schedule (M13): the read-only week of shows on the Automation tab, worked out from the global automations'
"Load a show" actions and their time, day, date and sunrise / sunset conditions. It's checked against the automations
runner itself (stepped through the days under UK time, with the clock changes), and the grid is driven like a person
would: blocks where their times are, clicking one, the week arrows, the now line, and nothing to edit."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta
from unittest import mock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QVBoxLayout, QWidget

from centralight.automations import AutomationRunner, Situation, sun_threshold
from centralight.model import Automation, Show
from centralight.schedule import describe_block, monday_of, skipped_times, week_schedule
from tests.qt_support import process, qt_app
from tests.test_automations import automation, uk_epoch, uk_local

SHOWS = {"morning": "Morning", "evening": "Evening", "christmas": "Christmas", "youth": "Youth night"}
MONDAY = date(2026, 11, 2)  # Mon 2 – Sun 8 Nov 2026: no clock change
SUNDAY = datetime(2026, 11, 8)
SPRING = date(2026, 3, 23)  # the week the clocks go forward (Sun 29 Mar, 01:00 -> 02:00)
AUTUMN = date(2026, 10, 19)  # the week they go back (Sun 25 Oct, 02:00 -> 01:00)
SUNDAY_MORNING = [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "13:00"}]
NDI = {"type": "ndi", "source": "Media Background Pane"}


def load(show: str, **extra) -> dict:
    return {"do": "load_show", "show": show, **extra}


def at(day: date | datetime, hhmm: str) -> datetime:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day if isinstance(day, date) and not isinstance(day, datetime) else day.date(), time()) + timedelta(hours=hours, minutes=minutes)


def spans(blocks) -> list[tuple[datetime, datetime, str]]:
    return [(b.start, b.end, b.automation_id) for b in blocks]


def runner_loads(automations: list[Automation], start: datetime, end: datetime, step: int = 60,
                 lead_in: timedelta = timedelta(hours=6)) -> set[tuple[datetime, str, str]]:
    """(minute, automation id, show) of each "Load a show" the automations runner itself does from start to end,
    checked every `step` seconds under UK time with its clock changes, as the engine checks. It starts looking a while
    before, so what was on already then (caught up at the first check) isn't counted."""
    container = Show(name="Global automations").with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))
    runner = AutomationRunner()
    wall, stop, mono = uk_epoch(start - lead_in), uk_epoch(end), 1000.0
    found = set()
    while wall < stop:
        local = uk_local(wall)
        for automation_id, action in runner.check(container, Situation(now=wall, local=local), mono) + runner.due(mono):
            if action.get("do") == "load_show" and local >= start:
                found.add((local.replace(second=0), automation_id, action["show"]))
        wall += step
        mono += step
    return found


def schedule_loads(blocks, start: datetime, end: datetime) -> set[tuple[datetime, str, str]]:
    """The same, from the schedule: each block's start (unless it began earlier) and each "when it stops" moment."""
    return {(b.start.replace(second=0), b.automation_id, b.show) for b in blocks
            if not b.continues_before and start <= b.start < end}


class ScheduleTestCase(unittest.TestCase):
    def week(self, automations: list[Automation], monday: date = MONDAY, **kwargs):
        return week_schedule(automations, SHOWS, monday, **kwargs)

    def blocks(self, automations: list[Automation], monday: date = MONDAY, **kwargs):
        return self.week(automations, monday, **kwargs)[0]


class BlockTests(ScheduleTestCase):
    def test_time_and_days(self) -> None:
        blocks, unplaced = self.week([automation("sunday", SUNDAY_MORNING, [load("morning")])])
        self.assertEqual(spans(blocks), [(at(SUNDAY, "08:30"), at(SUNDAY, "13:00"), "sunday")])
        block = blocks[0]
        self.assertEqual((block.show, block.show_label, block.show_id), ("morning", "Morning", "morning"))
        self.assertFalse(block.clash or block.on_end or block.continues_before or block.continues_after)
        self.assertEqual((block.conditional, unplaced), ("", []))
        weekdays = automation("weekdays", [{"type": "days", "days": ["mon", "tue", "wed", "thu", "fri"]},
                                           {"type": "time", "from": "18:00", "to": "19:00"}], [load("Evening")])
        found = self.blocks([weekdays])
        self.assertEqual([(b.start, b.end) for b in found],
                         [(at(MONDAY + timedelta(days=d), "18:00"), at(MONDAY + timedelta(days=d), "19:00")) for d in range(5)])
        self.assertEqual({(b.show, b.show_label, b.show_id) for b in found}, {("Evening", "Evening", "evening")})  # by name

    def test_overnight_ranges(self) -> None:
        # Saturday night: the days condition follows the night it began, so it runs on into Sunday morning
        late = automation("late", [{"type": "days", "days": ["sat"]}, {"type": "time", "from": "22:00", "to": "02:00"}], [load("youth")])
        self.assertEqual(spans(self.blocks([late])), [(at(SUNDAY - timedelta(days=1), "22:00"), at(SUNDAY, "02:00"), "late")])
        # every night: the week starts with Sunday night's carrying on, and ends with one going on into next week
        nightly = automation("nightly", [{"type": "time", "from": "23:00", "to": "01:00"}], [load("youth")])
        blocks = self.blocks([nightly])
        self.assertEqual(len(blocks), 8)
        first, last = blocks[0], blocks[-1]
        self.assertEqual((first.start, first.end, first.continues_before), (at(MONDAY, "00:00"), at(MONDAY, "01:00"), True))
        self.assertEqual((last.start, last.end, last.continues_after), (at(SUNDAY, "23:00"), at(SUNDAY + timedelta(days=1), "00:00"), True))
        self.assertTrue(all(b.end - b.start == timedelta(hours=2) for b in blocks[1:-1]))
        following = self.blocks([nightly], MONDAY + timedelta(days=7))
        self.assertEqual((following[0].start, following[0].end, following[0].continues_before),
                         (at(MONDAY + timedelta(days=7), "00:00"), at(MONDAY + timedelta(days=7), "01:00"), True))

    def test_a_date_range_shows_only_in_its_week(self) -> None:
        christmas = automation("christmas", [{"type": "dates", "from": "12-24", "to": "12-26"}], [load("christmas")])
        self.assertEqual(self.blocks([christmas]), [])
        self.assertEqual(spans(self.blocks([christmas], date(2026, 12, 21))),
                         [(datetime(2026, 12, 24), datetime(2026, 12, 27), "christmas")])  # Thu 24 – Sat 26 Dec, all day
        self.assertEqual(self.blocks([christmas], date(2026, 12, 28)), [])
        this_year = automation("this-year", [{"type": "dates", "from": "12-24", "to": "12-26", "year": 2026}], [load("christmas")])
        self.assertEqual(len(self.blocks([this_year], date(2026, 12, 21))), 1)
        self.assertEqual(self.blocks([this_year], date(2027, 12, 20)), [])
        new_year = automation("new-year", [{"type": "dates", "from": "12-31", "to": "01-01"},
                                           {"type": "time", "from": "18:00", "to": "02:00"}], [load("youth")])
        self.assertEqual(spans(self.blocks([new_year], date(2026, 12, 28))), [
            (datetime(2026, 12, 31, 18, 0), datetime(2027, 1, 1, 2, 0), "new-year"),
            (datetime(2027, 1, 1, 18, 0), datetime(2027, 1, 2, 2, 0), "new-year"),
        ])

    def test_sunrise_and_sunset_move_with_the_date(self) -> None:
        dusk = {"type": "sun", "when": "after", "event": "sunset", "minutes": -30}
        starts = {}
        for monday in (date(2026, 6, 15), MONDAY, date(2026, 12, 14)):
            blocks = self.blocks([automation("dusk", [dusk], [load("evening")])], monday)
            self.assertEqual(len(blocks), 7)
            for block in blocks:
                day = block.start.date()
                self.assertEqual(block.start, datetime.combine(day, time()) + timedelta(minutes=sun_threshold(dusk, day)))
                self.assertEqual(block.end, datetime.combine(day + timedelta(days=1), time()))  # it lasts until midnight
            starts[monday] = blocks[0].start.time()
        self.assertGreater(starts[date(2026, 6, 15)], starts[MONDAY])
        self.assertLess(starts[date(2026, 12, 14)], starts[MONDAY])
        dawn = {"type": "sun", "when": "before", "event": "sunrise", "minutes": 15}
        for block in self.blocks([automation("dawn", [dawn], [load("morning")])]):
            day = block.start.date()
            self.assertEqual((block.start.time(), block.continues_before), (time(), False))
            self.assertEqual(block.end, datetime.combine(day, time()) + timedelta(minutes=sun_threshold(dawn, day)))

    def test_invert(self) -> None:
        not_working = automation("not-working", [{"type": "days", "days": ["mon"]},
                                                 {"type": "time", "from": "09:00", "to": "17:00", "invert": True}], [load("youth")])
        self.assertEqual(spans(self.blocks([not_working])), [
            (at(MONDAY, "00:00"), at(MONDAY, "09:00"), "not-working"),
            (at(MONDAY, "17:00"), at(MONDAY + timedelta(days=1), "00:00"), "not-working"),
        ])
        self.assertFalse(self.blocks([not_working])[0].continues_before)  # (Sunday wasn't Monday)
        not_sunday = automation("not-sunday", [{"type": "days", "days": ["sun"], "invert": True}], [load("youth")])
        self.assertEqual(spans(self.blocks([not_sunday])), [(at(MONDAY, "00:00"), at(SUNDAY, "00:00"), "not-sunday")])

    def test_any_and_all(self) -> None:
        weekend = automation("weekend", [{"type": "days", "days": ["sat"]}, {"type": "days", "days": ["sun"]}], [load("youth")], logic="any")
        [block] = self.blocks([weekend])
        self.assertEqual((block.start, block.end), (at(SUNDAY - timedelta(days=1), "00:00"), at(SUNDAY + timedelta(days=1), "00:00")))
        self.assertFalse(block.continues_after)  # it ends just as the week does
        both = automation("both", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:00"}], [load("morning")])
        self.assertEqual(spans(self.blocks([both])), [(at(SUNDAY, "09:00"), at(SUNDAY, "12:00"), "both")])
        either = automation("either", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:00"}],
                            [load("morning")], logic="any")
        self.assertEqual([(b.start.date(), b.start.time(), b.end.time()) for b in self.blocks([either])][:2],
                         [(MONDAY, time(9), time(12)), (MONDAY + timedelta(days=1), time(9), time(12))])
        self.assertEqual(spans(self.blocks([either]))[-1], (at(SUNDAY, "00:00"), at(SUNDAY + timedelta(days=1), "00:00"), "either"))

    def test_untimed_conditions(self) -> None:
        # All: the block says what else it needs, and nothing is listed under the grid
        with_feed = automation("with-feed", SUNDAY_MORNING + [NDI], [load("morning")])
        blocks, unplaced = self.week([with_feed])
        self.assertEqual(spans(blocks), [(at(SUNDAY, "08:30"), at(SUNDAY, "13:00"), "with-feed")])
        self.assertEqual((blocks[0].conditional, unplaced), ("if Media Background Pane is sending", []))
        not_evening = automation("not-evening", SUNDAY_MORNING + [{"type": "loaded_show", "show": "evening", "invert": True}], [load("morning")])
        self.assertEqual(self.blocks([not_evening])[0].conditional, "if not (Evening is loaded)")
        lit = automation("lit", SUNDAY_MORNING + [{"type": "colour_black", "invert": True}, {"type": "app_started", "invert": True}], [load("morning")])
        self.assertEqual(self.blocks([lit])[0].conditional, "if not (the colour lights are black) and not (CentraLight starts)")
        # Any: the feed could start it at any time, so it's listed too; at 18:00 it starts unless the feed already did
        or_feed = automation("or-feed", [{"type": "time", "from": "18:00", "to": "19:00"}, NDI], [load("evening")], logic="any")
        blocks, unplaced = self.week([or_feed])
        self.assertEqual(len(blocks), 7)
        self.assertEqual({b.conditional for b in blocks}, {"unless Media Background Pane is sending"})
        self.assertEqual([(u.automation_id, u.trigger, u.show_label) for u in unplaced],
                         [("or-feed", "When Media Background Pane is sending", "Evening")])

    def test_triggers_that_cant_go_on_a_calendar(self) -> None:
        automations = [
            automation("app", [{"type": "app_started"}], [load("Morning")]),
            automation("pc", [{"type": "pc_startup"}], [load("morning")]),
            automation("woke", [{"type": "pc_woke"}, {"type": "app_started"}], [load("evening")], logic="any"),
            automation("lost", [{**NDI, "invert": True}], [load("christmas")], on_end=[load("evening")]),
            automation("sunday-start", [{"type": "pc_startup"}, {"type": "days", "days": ["sun"]}], [load("morning")]),
            automation("by-hand", [], [load("youth")], on_end=[load("evening")]),
            automation("typo", [{"type": "app_started"}], [load("Evning")]),
        ]
        blocks, unplaced = self.week(automations)
        self.assertEqual(blocks, [])  # (with All, "The PC starts" lasts a moment: Sunday can't say when)
        self.assertEqual([(u.automation_id, u.trigger, u.show_label, u.show_id, u.missing) for u in unplaced], [
            ("app", "When CentraLight starts", "Morning", "morning", False),
            ("pc", "When the PC starts", "Morning", "morning", False),
            ("woke", "When the PC wakes from sleep or CentraLight starts", "Evening", "evening", False),
            ("lost", "When not (Media Background Pane is sending)", "Christmas", "christmas", False),
            ("lost", "When it stops matching: not (Media Background Pane is sending)", "Evening", "evening", False),
            ("sunday-start", "When the PC starts and Sun", "Morning", "morning", False),
            ("by-hand", "Only when run by hand", "Youth night", "youth", False),  # (Run now never does "when it stops")
            ("typo", "When CentraLight starts", "Evning", "", True),  # no such show
        ])

    def test_when_the_shows_cant_be_listed(self) -> None:
        # (nothing is called missing then: the action's own words are shown)
        blocks, unplaced = week_schedule([automation("sunday", SUNDAY_MORNING, [load("Evning")]),
                                          automation("app", [{"type": "app_started"}], [load("morning")])], {}, MONDAY)
        self.assertEqual([(b.show_label, b.show_id, b.missing) for b in blocks], [("Evning", "", False)])
        self.assertEqual([(u.show_label, u.show_id, u.missing) for u in unplaced], [("morning", "", False)])
        self.assertNotIn("no show called", describe_block(blocks[0]))

    def test_when_it_stops_matching(self) -> None:
        handover = automation("sunday", SUNDAY_MORNING, [load("morning")], on_end=[load("evening")])
        blocks = self.blocks([handover])
        self.assertEqual([(b.start, b.end, b.show, b.on_end) for b in blocks], [
            (at(SUNDAY, "08:30"), at(SUNDAY, "13:00"), "morning", False),
            (at(SUNDAY, "13:00"), at(SUNDAY, "13:00"), "evening", True),
        ])
        self.assertTrue(blocks[1].moment)
        # a stretch that goes on into next week stops there: its moment is next week's
        nightly = automation("nightly", [{"type": "time", "from": "23:00", "to": "01:00"}], [], on_end=[load("morning")])
        ends = [b.start for b in self.blocks([nightly])]
        self.assertEqual(ends, [at(MONDAY + timedelta(days=d), "01:00") for d in range(7)])
        self.assertTrue(all(b.on_end for b in self.blocks([nightly])))

    def test_clashes(self) -> None:
        morning = automation("morning", SUNDAY_MORNING, [load("morning")])
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "09:30"}], [load("youth")])
        blocks = self.blocks([morning, youth])
        self.assertEqual([(b.automation_id, b.clash, b.clash_with) for b in blocks],
                         [("morning", True, ("Youth",)), ("youth", True, ("Morning",))])
        # a handover (one starts while the other is on) isn't a clash
        later = automation("later", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:00", "to": "15:00"}], [load("christmas")])
        self.assertFalse(any(b.clash for b in self.blocks([morning, later])))
        # nor is the same show, named by its id in one and its name in the other
        same = automation("same", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "10:00"}], [load("Morning")])
        self.assertFalse(any(b.clash for b in self.blocks([morning, same])))
        # "when it stops" at the same minute another starts
        after = automation("after", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "13:00", "to": "14:00"}], [load("youth")])
        ending = automation("ending", SUNDAY_MORNING, [], on_end=[load("evening")])
        self.assertEqual({(b.automation_id, b.clash) for b in self.blocks([ending, after])}, {("ending", True), ("after", True)})
        # one that began before the week loaded its show then, so it can't clash with a load at Monday midnight
        all_weekend = automation("all-weekend", [{"type": "days", "days": ["sun", "mon"]}], [load("morning")])
        monday = automation("monday", [{"type": "days", "days": ["mon"]}], [load("evening")])
        blocks = self.blocks([all_weekend, monday])
        self.assertEqual([(b.automation_id, b.start, b.continues_before, b.clash) for b in blocks][:2],
                         [("all-weekend", at(MONDAY, "00:00"), True, False), ("monday", at(MONDAY, "00:00"), False, False)])

    def test_disabled_and_other_automations_are_left_out(self) -> None:
        off = automation("off", SUNDAY_MORNING, [load("morning")], enabled=False)
        no_load = automation("no-load", SUNDAY_MORNING, [{"do": "fade_black", "fade": 2}])
        empty = automation("empty", [{"type": "app_started"}], [load("  ")])
        self.assertEqual(self.week([off, no_load, empty]), ([], []))

    def test_waits_first(self) -> None:
        later = automation("later", SUNDAY_MORNING, [load("morning", delay=60)])
        self.assertEqual(spans(self.blocks([later])), [(at(SUNDAY, "08:31"), at(SUNDAY, "13:00"), "later")])
        # a wait longer than the stretch: still loads (only the clock moved on)...
        long_wait = automation("long", SUNDAY_MORNING, [load("morning", delay=6 * 3600)])
        self.assertEqual(spans(self.blocks([long_wait])), [(at(SUNDAY, "14:30"), at(SUNDAY, "14:30"), "long")])
        # ...unless it has a "when it stops", which drops what's waiting
        dropped = automation("dropped", SUNDAY_MORNING, [load("morning", delay=6 * 3600)], on_end=[load("evening")])
        self.assertEqual([(b.start, b.on_end) for b in self.blocks([dropped])], [(at(SUNDAY, "13:00"), True)])

    def test_tooltip_words(self) -> None:
        with_feed = automation("with-feed", SUNDAY_MORNING + [NDI], [load("Evning")])
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "09:30"}], [load("youth")])
        block = next(b for b in self.blocks([with_feed, youth]) if b.automation_id == "with-feed")
        text = describe_block(block)
        for words in ("Load Evning", "Sun 08 Nov 08:30 – 13:00", "Automation: With Feed", "If Media Background Pane is sending",
                      "There's no show called “Evning”", "Clash: “Youth” loads a different show at the same minute",
                      "Click to open the automation"):
            self.assertIn(words, text)
        self.assertEqual(monday_of(date(2026, 11, 8)), MONDAY)
        self.assertEqual(monday_of(MONDAY), MONDAY)


class RunnerAgreementTests(ScheduleTestCase):
    """The schedule says when the automations runner itself would load each show, to the minute."""

    SAMPLES = [
        automation("sunday", SUNDAY_MORNING, [load("morning")], on_end=[load("evening")]),
        automation("late", [{"type": "days", "days": ["sat"]}, {"type": "time", "from": "22:00", "to": "02:00"}], [load("youth")]),
        automation("dusk", [{"type": "sun", "when": "after", "event": "sunset", "minutes": -30}], [load("evening")]),
        automation("night", [{"type": "sun", "when": "after", "event": "sunset"}, {"type": "sun", "when": "before", "event": "sunrise", "minutes": 20}],
                   [load("youth")], logic="any"),
        automation("not-working", [{"type": "time", "from": "09:00", "to": "17:00", "invert": True}, {"type": "days", "days": ["sun"]}], [load("christmas")]),
        automation("small-hours", [{"type": "time", "from": "01:30", "to": "01:45"}], [load("morning")], on_end=[load("evening")]),
    ]

    def check_agrees(self, monday: date, start: datetime, end: datetime, step: int = 60, lead_in: timedelta = timedelta(hours=6),
                     automations: list[Automation] | None = None) -> set:
        automations = automations or self.SAMPLES
        blocks = self.blocks(automations, monday, local_time=uk_local)
        expected = runner_loads(automations, start, end, step, lead_in)
        self.assertEqual(schedule_loads(blocks, start, end), expected)
        return expected

    def test_a_weekend(self) -> None:
        found = self.check_agrees(MONDAY, datetime(2026, 11, 7), datetime(2026, 11, 9))
        self.assertGreater(len(found), 8)  # (it did see the loads)

    def test_the_clocks_going_back(self) -> None:
        found = self.check_agrees(AUTUMN, datetime(2026, 10, 24, 12, 0), datetime(2026, 10, 26))
        self.assertIn((datetime(2026, 10, 25, 1, 30), "small-hours", "morning"), found)
        blocks = self.blocks([self.SAMPLES[5]], AUTUMN, local_time=uk_local)
        self.assertEqual([b.start for b in blocks if b.start.date() == date(2026, 10, 25)],
                         [datetime(2026, 10, 25, 1, 30), datetime(2026, 10, 25, 1, 45)])  # 01:30 came twice: once

    def test_the_clocks_going_forward(self) -> None:
        # 01:00-02:00 is skipped: 01:30 runs at 02:00, and stops matching straight after (checked each second)
        found = self.check_agrees(SPRING, datetime(2026, 3, 29, 0, 50), datetime(2026, 3, 29, 2, 10), step=1,
                                  lead_in=timedelta(minutes=5), automations=[self.SAMPLES[5]])
        self.assertEqual(found, {(datetime(2026, 3, 29, 2, 0), "small-hours", "morning"),
                                 (datetime(2026, 3, 29, 2, 0), "small-hours", "evening")})
        blocks = self.blocks([self.SAMPLES[5]], SPRING, local_time=uk_local)
        self.assertEqual([(b.start, b.end, b.on_end) for b in blocks if b.start.date() == date(2026, 3, 29)],
                         [(datetime(2026, 3, 29, 2, 0), datetime(2026, 3, 29, 2, 0), False),
                          (datetime(2026, 3, 29, 2, 0), datetime(2026, 3, 29, 2, 0), True)])
        self.check_agrees(SPRING, datetime(2026, 3, 28, 12, 0), datetime(2026, 3, 30), automations=self.SAMPLES[:5])


class ClockChangeTests(ScheduleTestCase):
    def test_blocks_dont_move_or_double(self) -> None:
        for monday in (SPRING, AUTUMN):
            sunday = monday + timedelta(days=6)
            blocks = self.blocks([automation("sunday", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                                             [load("morning")])], monday, local_time=uk_local)
            self.assertEqual(spans(blocks), [(at(sunday, "09:00"), at(sunday, "12:30"), "sunday")], monday)
            evenings = self.blocks([automation("evenings", [{"type": "time", "from": "18:00", "to": "20:00"}], [load("evening")])],
                                   monday, local_time=uk_local)
            self.assertEqual([(b.start.time(), b.end.time()) for b in evenings], [(time(18), time(20))] * 7, monday)
            dusk = {"type": "sun", "when": "after", "event": "sunset"}
            for block in self.blocks([automation("dusk", [dusk], [load("evening")])], monday, local_time=uk_local):
                self.assertEqual(block.start.hour * 60 + block.start.minute, sun_threshold(dusk, block.start.date()))

    def test_skipped_times(self) -> None:
        start = datetime.combine(SPRING, time())
        self.assertEqual(skipped_times(start, start + timedelta(days=7), uk_local),
                         [(datetime(2026, 3, 29, 1, 0), datetime(2026, 3, 29, 2, 0))])
        autumn = datetime.combine(AUTUMN, time())
        self.assertEqual(skipped_times(autumn, autumn + timedelta(days=7), uk_local), [])  # (an hour repeats; none is skipped)
        normal = datetime.combine(MONDAY, time())
        self.assertEqual(skipped_times(normal, normal + timedelta(days=7), uk_local), [])


class CostTests(ScheduleTestCase):
    def test_only_the_moments_that_can_change_are_checked(self) -> None:
        automations = [automation("sunday", SUNDAY_MORNING, [load("morning")]),
                       automation("dusk", [{"type": "sun", "when": "after", "event": "sunset"}, {"type": "days", "days": ["sat", "sun"]}], [load("evening")]),
                       automation("christmas", [{"type": "dates", "from": "12-24", "to": "12-26"}], [load("christmas")])]
        real = AutomationRunner.matches
        with mock.patch.object(AutomationRunner, "matches", autospec=True, side_effect=real) as matches:
            self.blocks(automations)
        self.assertLess(matches.call_count, 120)  # (a check every minute would be 3 x 10,080)


# ---------------------------------------------------------------- the grid


class ScheduleViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        from centralight.ui.schedule_view import ScheduleView
        from centralight.ui.widgets import Panel

        self.root = QWidget()
        self.root.setObjectName("Root")
        self.panel = Panel("Show schedule")
        self.view = ScheduleView()
        self.view.set_now(datetime(2026, 11, 4, 10, 0))  # a Wednesday
        self.panel.body.addWidget(self.view, 1)
        layout = QVBoxLayout(self.root)
        layout.addWidget(self.panel)
        self.clicked: list[str] = []
        self.view.automation_clicked.connect(self.clicked.append)
        self.automations = [
            automation("sunday", SUNDAY_MORNING, [load("morning")], on_end=[load("evening")]),
            automation("start", [{"type": "app_started"}], [load("Morning")]),
            automation("christmas", [{"type": "dates", "from": "12-24", "to": "12-26"}], [load("christmas")]),
            automation("youth", [{"type": "days", "days": ["fri"]}, {"type": "time", "from": "19:00", "to": "21:00"}, NDI], [load("youth")]),
        ]
        self.view.set_data(Show(name="Global automations").with_automations(
            {a.id: a for a in self.automations}, tuple(a.id for a in self.automations)), SHOWS)
        self.size(900, 640)

    def tearDown(self) -> None:
        self.root.close()
        self.root.deleteLater()
        process(self.app)

    def size(self, width: int, height: int) -> None:
        self.root.resize(width, height)
        self.root.show()
        process(self.app, 3)

    def rect_of(self, automation_id: str, on_end: bool = False):
        return next(rect for block, rect in self.view.block_rects() if block.automation_id == automation_id and block.on_end == on_end)

    def test_blocks_are_drawn_at_their_times_and_open_their_automation(self) -> None:
        grid = self.view.grid
        self.assertEqual(self.view.week_start, MONDAY)
        rect = self.rect_of("sunday")
        self.assertAlmostEqual(rect.top(), grid.y_of(8.5 * 60), delta=0.5)
        self.assertAlmostEqual(rect.bottom(), grid.y_of(13 * 60), delta=0.5)
        self.assertTrue(grid.x_of(6) <= rect.left() < rect.right() <= grid.x_of(7) + 0.5)  # in Sunday's column
        image = grid.grab().toImage()
        ratio = image.devicePixelRatio()
        middle = image.pixelColor(round(rect.center().x() * ratio), round(rect.center().y() * ratio))
        wanted = QColor(self.view.colour_of(next(b for b, _r in self.view.block_rects() if b.automation_id == "sunday")))
        self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(middle.getRgb()[:3], wanted.getRgb()[:3])), (middle.name(), wanted.name()))
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center().toPoint())
        self.assertEqual(self.clicked, ["sunday"])
        line = self.rect_of("sunday", on_end=True)  # the "when it stops" line, at 13:00
        self.assertAlmostEqual(line.center().y(), grid.y_of(13 * 60), delta=1.0)
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, (line.center() + QPointF(0, 2)).toPoint())
        self.assertEqual(self.clicked, ["sunday", "sunday"])
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(grid.x_of(1) + 5, grid.y_of(3 * 60)).toPoint())
        self.assertEqual(len(self.clicked), 2)  # an empty spot: nothing
        youth = next(b for b, _r in self.view.block_rects() if b.automation_id == "youth")
        self.assertEqual(youth.conditional, "if Media Background Pane is sending")
        self.assertIn("Automation: Youth", describe_block(youth))

    def test_the_arrows_show_other_weeks(self) -> None:
        self.assertEqual(self.view.title.full_text(), "2 – 8 Nov 2026")
        self.assertFalse(self.view.this_week_button.isEnabled())
        QTest.mouseClick(self.view.next_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.week_start, date(2026, 11, 9))
        self.assertEqual(self.view.title.full_text(), "9 – 15 Nov 2026")
        self.assertTrue(self.view.this_week_button.isEnabled())
        self.assertFalse(any(b.automation_id == "christmas" for b in self.view.blocks))
        for _ in range(6):
            QTest.mouseClick(self.view.next_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.week_start, date(2026, 12, 21))
        self.assertEqual(self.view.title.full_text(), "21 – 27 Dec 2026")
        process(self.app)
        christmas = [b for b, _rect in self.view.block_rects() if b.automation_id == "christmas"]
        self.assertEqual(len(christmas), 3)  # drawn on Thursday, Friday and Saturday
        QTest.mouseClick(self.view.next_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.title.full_text(), "28 Dec 2026 – 3 Jan 2027")
        QTest.mouseClick(self.view.previous_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.week_start, date(2026, 12, 21))
        QTest.mouseClick(self.view.this_week_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.week_start, MONDAY)
        self.assertFalse(self.view.this_week_button.isEnabled())

    def test_the_now_line(self) -> None:
        grid = self.view.grid
        self.assertAlmostEqual(grid.now_y(), grid.y_of(10 * 60))
        image = grid.grab().toImage()
        ratio = image.devicePixelRatio()
        pixel = image.pixelColor(round((grid.x_of(2) + grid.column_width() / 2) * ratio), int(grid.now_y() * ratio))  # Wednesday
        self.assertGreater(min(pixel.red(), pixel.green(), pixel.blue()), 200)  # (white)
        self.view.next_week()
        self.assertIsNone(grid.now_y())  # only this week has one
        self.view.this_week()
        self.view.set_now(datetime(2026, 11, 9, 0, 1))  # the clock moves on into next week: so does the grid
        self.assertEqual(self.view.week_start, date(2026, 11, 9))
        self.assertIsNotNone(grid.now_y())
        self.view.previous_week()
        self.view.set_now(datetime(2026, 11, 16, 0, 1))  # (unless someone's looking at another week)
        self.assertEqual(self.view.week_start, date(2026, 11, 2))
        self.assertEqual(self.view._timer.interval(), 60_000)
        self.assertTrue(self.view._timer.isActive())
        self.root.hide()
        self.assertFalse(self.view._timer.isActive())  # it only looks at the clock while it's on screen

    def test_triggers_that_cant_go_on_the_calendar_are_listed(self) -> None:
        listed = self.view.unplaced_list
        self.assertTrue(listed.isVisibleTo(self.root))
        self.assertEqual([listed.item(i).text() for i in range(listed.count())], ["When CentraLight starts → Load Morning"])
        self.assertIn("Automation: Start", listed.item(0).toolTip())
        QTest.mouseClick(listed.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, listed.visualItemRect(listed.item(0)).center())
        self.assertEqual(self.clicked, ["start"])
        self.view.set_data(self.automations[:1], SHOWS)
        process(self.app)
        self.assertFalse(listed.isVisibleTo(self.root))
        self.assertFalse(self.view.unplaced_title.isVisibleTo(self.root))

    def test_nothing_can_be_edited(self) -> None:
        for kind in (QLineEdit, QAbstractSpinBox, QComboBox, QCheckBox, QTextEdit, QPlainTextEdit):
            self.assertEqual(self.view.findChildren(kind), [], kind.__name__)
        buttons = [b.text() for b in self.view.findChildren(QAbstractButton) if b.isVisibleTo(self.root)]
        self.assertEqual(sorted(buttons), sorted(["‹", "This week", "›"]))
        listed = self.view.unplaced_list
        self.assertEqual(listed.editTriggers(), listed.EditTrigger.NoEditTriggers)
        flags = listed.item(0).flags()
        for flag in (Qt.ItemFlag.ItemIsEditable, Qt.ItemFlag.ItemIsUserCheckable, Qt.ItemFlag.ItemIsSelectable, Qt.ItemFlag.ItemIsDragEnabled):
            self.assertFalse(flags & flag, flag)
        self.assertFalse(self.view.acceptDrops() or self.view.grid.acceptDrops())
        before = list(self.automations)
        rect = self.rect_of("sunday")
        QTest.mouseDClick(self.view.grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center().toPoint())
        self.assertEqual(self.automations, before)

    def test_it_lays_out_at_any_width_with_long_names(self) -> None:
        from tests import layout_rules

        long_shows = {"morning": "Sunday morning service with the band, the choir and the children's talk",
                      "evening": "Evening worship by candlelight with a very long name indeed"}
        long_ones = [
            automation("a-very-long-automation-name-for-the-sunday-morning-service", SUNDAY_MORNING, [load("morning")], on_end=[load("evening")]),
            automation("clashing", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "09:00"}], [load("evening")]),
            automation("when-the-pc-starts-up-in-the-morning-load-the-morning-show", [{"type": "pc_startup"}], [load("morning")]),
            automation("when-the-media-background-pane-feed-is-lost-load-the-evening-show", [{**NDI, "invert": True}], [load("evening")]),
            automation("a-show-that-isnt-there", [{"type": "app_started"}], [load("A show that was deleted a long time ago")]),
            automation("and-one-more-to-make-the-list-scroll", [{"type": "pc_woke"}], [load("morning")]),
        ]
        self.view.set_data(long_ones, long_shows)
        for width, height in ((250, 300), (320, 420), (560, 380), (900, 640), (1400, 900)):
            self.size(width, height)
            self.assertEqual(layout_rules.check(self.root, f"{width}x{height}"), [])
        self.view.set_data([], {})  # nothing at all
        self.size(260, 260)
        self.assertEqual(layout_rules.check(self.root, "empty"), [])

    def test_each_show_keeps_its_colour(self) -> None:
        from centralight.ui.schedule_view import NO_SHOW_COLOUR, SHOW_COLOURS

        colours = {b.show_id: self.view.colour_of(b) for b in self.view.blocks}
        self.assertEqual(colours, {"morning": SHOW_COLOURS[2], "evening": SHOW_COLOURS[1], "youth": SHOW_COLOURS[3]})  # (by id)
        self.view.show_week(date(2026, 12, 24))
        self.assertEqual(self.view.colour_of(next(b for b in self.view.blocks if b.show_id == "morning")), SHOW_COLOURS[2])
        self.view.set_data([automation("typo", [{"type": "app_started"}], [load("Evning")])], SHOWS)
        self.assertEqual(self.view.colour_of(self.view.unplaced[0]), NO_SHOW_COLOUR)  # no such show: grey
        self.view.set_data([automation("a", SUNDAY_MORNING, [load("one")]), automation("b", SUNDAY_MORNING, [load("two")])], {})
        self.view.this_week()
        self.assertEqual(len({self.view.colour_of(b) for b in self.view.blocks}), 2)  # (shows not listed: still told apart)

    def test_set_data_again_does_nothing(self) -> None:
        from centralight.ui import schedule_view

        with mock.patch.object(schedule_view, "week_schedule", side_effect=schedule_view.week_schedule) as worked_out:
            self.view.set_data(list(self.automations), dict(SHOWS))
            self.assertEqual(worked_out.call_count, 0)
            self.view.set_data(self.automations[:2], SHOWS)
            self.assertEqual(worked_out.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class AutomationTabTests(unittest.TestCase):
    """The schedule on the Automation tab (M13): a tab beside the editor; a block opens its automation."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def test_on_the_automation_tab(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.model import Automation, Show
        from centralight.new_show import show_from_patch
        from centralight.settings import Settings
        from centralight.show_io import freeze
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        load = Automation("sunday-evening", "Sunday evening", True, "all",
                          freeze([{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}]),
                          freeze([{"do": "load_show", "show": "evening"}]))
        globals_ = Show(name="Global automations").with_automations({load.id: load}, (load.id,))
        with tempfile.TemporaryDirectory() as tmp:
            engine = FakeEngine(show_from_patch(), global_automations=globals_)
            window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                                notes_path=Path(tmp) / "fixture_map.csv", global_automations=globals_)
            try:
                window.resize(1366, 768)
                window.show()
                window.show_page("automation")
                page = window.automation_page
                page.right_tabs.setCurrentWidget(page.schedule)
                process(self.app, 3)
                self.assertEqual([b.automation_id for b in page.schedule.blocks], ["sunday-evening"])
                page.schedule.automation_clicked.emit("sunday-evening")
                process(self.app)
                self.assertEqual((page.scope, page.selected), ("global", "sunday-evening"))
                self.assertIs(page.right_tabs.currentWidget(), page.editor_scroll)
            finally:
                window.linked_media.close()
                window._shut_down = True
                window.editor_page.shutdown()
                window.document.close()
                window.close()
                window.deleteLater()
                process(self.app)
