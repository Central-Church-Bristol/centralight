"""M18, batch 3 (6 Nov): a Dates condition's day, month and year, each with a tick (off: any), one value and a range on
demand; From … to … kept for a span across a month end. Old files work exactly as before: the user's Saturday
automation (7 Nov 2026) above all."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from centralight.automations import (
    GLOBAL,
    AutomationRunner,
    Situation,
    _dates_true,
    check_condition,
    date_fields,
    default_condition,
    describe_condition,
    parse_day_month,
    summary,
    upcoming,
)
from centralight.global_automations import GLOBAL_DIR, load_global_automations
from centralight.model import Show
from centralight.new_show import create_show
from centralight.schedule import monday_of, week_schedule
from centralight.show_io import automation_from_dict, automation_to_dict
from tests.qt_support import process
from tests.test_automations import automation
from tests.test_shows import WindowCase

# the user's file, exactly (Sat 7 Nov 2026)
SATURDAY = """{
  "format": "centralight-automation",
  "version": 1,
  "id": "new-global-automation",
  "name": "Dioscesioan Event",
  "logic": "all",
  "conditions": [
    {
      "type": "dates",
      "from": "11-07",
      "to": "11-07",
      "year": 2026
    }
  ],
  "actions": [
    {
      "do": "load_show",
      "show": "central-church"
    }
  ]
}
"""
SHOWS = {"central-church": "Default"}


def old_dates_true(cond, today: date) -> bool:
    """The Dates check before M18, word for word (an old condition must match exactly as it did)."""
    start, end = parse_day_month(cond.get("from")), parse_day_month(cond.get("to"))
    if not start or not end:
        return False
    key = (today.month, today.day)
    wraps = start > end
    inside = (start <= key or key <= end) if wraps else (start <= key <= end)
    year = cond.get("year")
    if inside and isinstance(year, int):
        started = today.year if not wraps or key >= start else today.year - 1
        return started == year
    return inside


def dates(**fields) -> dict:
    return {"type": "dates", **fields}


def matches(cond: dict, moment: datetime) -> bool:
    return AutomationRunner().matches(automation("probe", [cond], []), Situation(now=0.0, local=moment))[0]


class ConversionTests(unittest.TestCase):
    def test_a_single_day(self) -> None:
        self.assertEqual(date_fields(dates(**{"from": "11-07", "to": "11-07", "year": 2026})),
                         {"day": (7, 7), "month": (11, 11), "year": (2026, 2026)})
        self.assertEqual(date_fields(dates(**{"from": "12-25", "to": "12-25"})), {"day": (25, 25), "month": (12, 12)})

    def test_a_span_within_a_month(self) -> None:
        self.assertEqual(date_fields(dates(**{"from": "12-24", "to": "12-26"})), {"day": (24, 26), "month": (12, 12)})
        self.assertEqual(date_fields(dates(**{"from": "12-24", "to": "12-26", "year": 2026})),
                         {"day": (24, 26), "month": (12, 12), "year": (2026, 2026)})
        self.assertEqual(date_fields(dates(**{"from": "12-01", "to": "12-31"})), {"month": (12, 12)})  # all of it: any day
        self.assertEqual(date_fields(dates(**{"from": "02-01", "to": "02-29"})), {"month": (2, 2)})
        self.assertEqual(date_fields(dates(**{"from": "02-01", "to": "02-28"})), {"day": (1, 28), "month": (2, 2)})  # (not 29 Feb)

    def test_a_span_across_a_month_end_stays_from_to(self) -> None:
        for cond in (dates(**{"from": "12-24", "to": "01-06"}), dates(**{"from": "12-24", "to": "01-06", "year": 2026}),
                     dates(**{"from": "03-31", "to": "04-02"}), dates(**{"from": "11-20", "to": "11-05"})):
            self.assertIsNone(date_fields(cond), cond)

    def test_the_new_form(self) -> None:
        self.assertEqual(date_fields(dates(day=[1, 7], year=2026)), {"day": (1, 7), "year": (2026, 2026)})
        self.assertIsNone(date_fields(dates(day="7")))


class SummaryTests(unittest.TestCase):
    def test_summaries(self) -> None:
        for cond, wanted in (
            (dates(day=7, month=11, year=2026), "7 Nov 2026"),
            (dates(**{"from": "11-07", "to": "11-07", "year": 2026}), "7 Nov 2026"),
            (dates(day=25, month=12), "every 25 Dec"),
            (dates(month=12), "every December"),
            (dates(**{"from": "12-01", "to": "12-31"}), "every December"),
            (dates(day=[1, 7]), "1st–7th of each month"),
            (dates(day=1), "1st of each month"),
            (dates(day=[24, 26], month=12), "every 24–26 Dec"),
            (dates(day=[24, 26], month=12, year=2026), "24–26 Dec 2026"),
            (dates(month=12, year=2026), "December 2026"),
            (dates(month=[6, 8]), "every Jun–Aug"),
            (dates(day=7, month=[6, 8]), "7th of Jun–Aug"),
            (dates(day=[22, 23], year=2026), "22nd–23rd of each month in 2026"),
            (dates(day=7, month=11, year=[2026, 2028]), "7 Nov in 2026–2028"),
            (dates(year=2026), "all of 2026"),
            (dates(day=[7, 7], month=[11, 11]), "every 7 Nov"),  # (a range with both ends the same is one day)
            (dates(**{"from": "12-24", "to": "01-06"}), "24 Dec – 6 Jan"),
            (dates(**{"from": "12-24", "to": "01-06", "year": 2026}), "24 Dec 2026 – 6 Jan 2027"),
            (dates(**{"from": "03-31", "to": "04-02", "year": 2026}), "31 Mar – 2 Apr 2026"),
            (dates(), "no day, month or year ticked"),
            ({**dates(month=12), "invert": True}, "not (every December)"),
        ):
            self.assertEqual(describe_condition(cond), wanted, cond)


class MatchingTests(unittest.TestCase):
    def test_fields_with_ranges_and_any(self) -> None:
        first_week = dates(day=[1, 7])
        self.assertTrue(_dates_true(first_week, date(2026, 11, 1)))
        self.assertTrue(_dates_true(first_week, date(2027, 2, 7)))
        self.assertFalse(_dates_true(first_week, date(2026, 11, 8)))
        christmas = dates(day=25, month=12)
        self.assertTrue(_dates_true(christmas, date(2026, 12, 25)))
        self.assertTrue(_dates_true(christmas, date(2031, 12, 25)))
        self.assertFalse(_dates_true(christmas, date(2026, 12, 24)))
        summer = dates(month=[6, 8], year=[2026, 2027])
        self.assertTrue(_dates_true(summer, date(2026, 6, 1)))
        self.assertTrue(_dates_true(summer, date(2027, 8, 31)))
        self.assertFalse(_dates_true(summer, date(2027, 9, 1)))
        self.assertFalse(_dates_true(summer, date(2028, 7, 1)))
        self.assertFalse(_dates_true(dates(), date(2026, 11, 7)))  # nothing ticked: never (as before: not valid)
        self.assertFalse(_dates_true(dates(day="7"), date(2026, 11, 7)))  # (a wrong value is simply not true)

    def test_old_conditions_match_exactly_as_before(self) -> None:
        olds = [dates(**{"from": a, "to": b}) for a, b in (("11-07", "11-07"), ("12-01", "01-06"), ("12-24", "12-26"),
                                                           ("02-29", "02-29"), ("11-20", "11-05"), ("01-01", "12-31"))]
        olds += [{**cond, "year": 2026} for cond in olds] + [dates(**{"from": "12-01"}), dates(**{"from": "13-01", "to": "12-01"})]
        day = date(2025, 1, 1)
        while day <= date(2028, 12, 31):
            for cond in olds:
                self.assertEqual(_dates_true(cond, day), old_dates_true(cond, day), (cond, day))
            day += timedelta(days=1)

    def test_checks(self) -> None:
        for cond in (dates(day=7, month=11, year=2026), dates(day=[1, 7]), dates(month=12), dates(year=[2026, 2027]),
                     dates(**{"from": "12-24", "to": "01-06", "year": 2026}), dates(day=[28, 31], month=2)):
            self.assertEqual(check_condition(cond, GLOBAL), [], cond)
        for cond in (dates(), dates(day=0), dates(day=[7, 1]), dates(day=32), dates(month=13), dates(year=1999),
                     dates(day=True), dates(day=[1, 2, 3]), dates(day=31, month=2), dates(day=31, month=4),
                     dates(**{"from": "12-24", "to": "01-06", "day": 3}), dates(**{"from": "02-30", "to": "03-01"})):
            self.assertTrue(check_condition(cond, GLOBAL), cond)

    def test_a_new_one_starts_today(self) -> None:
        self.assertEqual(default_condition("dates", Show(), today=date(2026, 11, 6)),
                         {"type": "dates", "day": 6, "month": 11, "year": 2026})
        before = date.today()
        fresh = default_condition("dates", Show())
        self.assertIn((fresh["day"], fresh["month"], fresh["year"]), {(d.day, d.month, d.year) for d in (before, date.today())})

    def test_the_new_form_goes_through_a_file_as_it_is(self) -> None:
        made = automation("carols", [dates(day=[1, 24], month=12)], [{"do": "load_show", "show": "carols"}])
        again, warnings = automation_from_dict(automation_to_dict(made), "carols", scope="global")
        self.assertEqual(warnings, [])
        self.assertEqual(dict(again.conditions[0]), {"type": "dates", "day": (1, 24), "month": 12})  # (frozen: a tuple)
        self.assertEqual(automation_to_dict(again)["conditions"], [{"type": "dates", "day": [1, 24], "month": 12}])
        _broken, warnings = automation_from_dict({**automation_to_dict(made), "conditions": [dates(day=40)]}, "carols",
                                                 scope="global")
        self.assertTrue(any("condition 1" in w for w in warnings))  # (switched off until it's fixed, as before)


class SaturdayTests(WindowCase):
    """The user's automation for Sat 7 Nov 2026, from its file as it is."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.root / GLOBAL_DIR
        self.folder.mkdir()
        self.file = self.folder / "new-global-automation.json"
        self.file.write_bytes(SATURDAY.encode("utf-8"))
        self.original = self.file.read_bytes()

    def load(self) -> Show:
        container = load_global_automations(self.folder)
        self.assertEqual(container.warnings, ())
        return container

    def check_matching(self, container: Show) -> None:
        event = container.automations["new-global-automation"]
        self.assertTrue(event.enabled)
        runner = AutomationRunner()
        for moment, wanted in ((datetime(2026, 11, 6, 23, 59, 59), False), (datetime(2026, 11, 7, 0, 0), True),
                               (datetime(2026, 11, 7, 9, 30), True), (datetime(2026, 11, 7, 23, 59, 59), True),
                               (datetime(2026, 11, 8, 0, 0), False), (datetime(2027, 11, 7, 12, 0), False),
                               (datetime(2025, 11, 7, 12, 0), False)):
            self.assertEqual(runner.matches(event, Situation(now=0.0, local=moment))[0], wanted, moment)

    def test_the_saturday_file(self) -> None:
        container = self.load()
        event = container.automations["new-global-automation"]
        self.assertEqual(describe_condition(event.conditions[0]), "7 Nov 2026")
        self.assertEqual(summary(event, container, GLOBAL, SHOWS), "7 Nov 2026 → load the show Default")
        self.check_matching(container)
        # the week schedule: all of Saturday, nothing else
        blocks, unplaced = week_schedule(container, SHOWS, monday_of(date(2026, 11, 7)))
        self.assertEqual([(b.start, b.end, b.show) for b in blocks],
                         [(datetime(2026, 11, 7), datetime(2026, 11, 8), "central-church")])
        self.assertEqual(unplaced, [])
        self.assertEqual(week_schedule(container, SHOWS, date(2026, 11, 9))[0], [])
        self.assertEqual(week_schedule(container, SHOWS, monday_of(date(2027, 11, 7)))[0], [])
        # the Upcoming agenda: Saturday at midnight
        found = upcoming(container, datetime(2026, 11, 6, 19, 50))
        self.assertEqual([(moment, a.id, others) for moment, a, others in found],
                         [(datetime(2026, 11, 7, 0, 0), "new-global-automation", False)])

    def test_opening_it_in_the_editor_changes_nothing(self) -> None:
        from centralight.ui import automation_page
        from centralight.ui.automation_page import DatePart
        from centralight.ui.document import SCOPE_AUTOMATIONS

        class Friday(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 6, 19, 50)

        create_show(self.root / "Central Church", "Default")
        show = create_show(self.root / "Morning", "Morning")
        window, _engine = self.make(show, global_automations=self.load())
        window.show_page("automation")
        process(self.app)
        page = window.automation_page
        page._select(GLOBAL, "new-global-automation")
        page._build_editor(force=True)
        process(self.app)
        row = page.rows["conditions"][0]
        parts = row._date_parts
        self.assertEqual([(k, p.tick.isChecked(), p.value(), p.to.isChecked()) for k, p in parts.items()],
                         [("day", True, 7, False), ("month", True, 11, False), ("year", True, 2026, False)])
        self.assertTrue(all(isinstance(p, DatePart) for p in parts.values()))
        self.assertIn("7 Nov 2026", page.summary.text())
        self.assertIn("7 Nov 2026", page.global_list.item(0).text())
        with mock.patch.object(automation_page, "datetime", Friday):
            page._fill_upcoming(force=True)
        self.assertIn("Sat 07 Nov 00:00  Global: Dioscesioan Event", [page.upcoming.item(i).text() for i in range(page.upcoming.count())])
        page.schedule.set_now(datetime(2026, 11, 6, 19, 50))
        page.schedule.show_week(date(2026, 11, 7))
        page.right_tabs.setCurrentWidget(page.schedule)
        process(self.app)
        self.assertEqual([(b.start, b.end, b.show_label) for b in page.schedule.blocks if not b.default],
                         [(datetime(2026, 11, 7), datetime(2026, 11, 8), "Default")])
        page.right_tabs.setCurrentIndex(0)
        process(self.app)
        # nothing changed: nothing to save, the file as it was, and it still matches the same
        self.assertEqual(dict(window.global_document.show.automations["new-global-automation"].conditions[0]),
                         {"type": "dates", "from": "11-07", "to": "11-07", "year": 2026})
        self.assertIs(window.global_document.show, window.global_document.saved)
        self.assertFalse(window.global_document.stack(SCOPE_AUTOMATIONS).canUndo())
        QTestWait(self.app, 1800)  # (longer than the autosave's wait)
        self.assertEqual(self.file.read_bytes(), self.original)
        self.check_matching(self.load())

    def test_a_change_saves_the_new_form(self) -> None:
        show = create_show(self.root / "Morning", "Morning")
        window, _engine = self.make(show, global_automations=self.load())
        window.show_page("automation")
        page = window.automation_page
        page._select(GLOBAL, "new-global-automation")
        page._build_editor(force=True)
        process(self.app)
        year = page.rows["conditions"][0]._date_parts["year"]
        year.first.setValue(2027)
        process(self.app)
        self.assertEqual(dict(window.global_document.show.automations["new-global-automation"].conditions[0]),
                         {"type": "dates", "day": 7, "month": 11, "year": 2027})
        self.assertIn("7 Nov 2027", page.summary.text())


def QTestWait(app, ms: int) -> None:  # noqa: N802
    from PySide6.QtTest import QTest

    QTest.qWait(ms)
    process(app)


class EditorTests(WindowCase):
    def open(self, conditions: list):
        from centralight.automations import update_automation

        show = create_show(self.root / "Morning", "Morning")
        window, _engine = self.make(show)
        window.show_page("automation")
        page = window.automation_page
        page.new_automation()
        new_id = page.selected
        window.document.apply(update_automation(window.document.show, new_id, conditions=conditions), "dates")
        page._build_editor(force=True)
        process(self.app)
        self.window, self.page, self.id = window, page, new_id
        return page.rows["conditions"][0] if conditions else None

    def stored(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.window.document.show.automations[self.id].conditions[0].items()}

    def test_a_new_condition_starts_today(self) -> None:
        self.open([])
        today = date.today()
        self.page.add_rule("conditions", "dates")
        process(self.app)
        self.assertEqual(self.stored(), {"type": "dates", "day": today.day, "month": today.month, "year": today.year})
        parts = self.page.rows["conditions"][0]._date_parts
        self.assertEqual([p.value() for p in parts.values()], [today.day, today.month, today.year])
        self.assertTrue(all(p.tick.isChecked() and not p.to.isChecked() for p in parts.values()))  # one value each

    def test_ticks_and_a_range_on_demand(self) -> None:
        row = self.open([dates(day=7, month=11, year=2026)])
        day, month, year = (row._date_parts[k] for k in ("day", "month", "year"))
        self.assertFalse(day.last.isVisibleTo(day))  # one value to start with
        year.tick.setChecked(False)  # every year
        self.assertEqual(self.stored(), {"type": "dates", "day": 7, "month": 11})
        self.assertTrue(year.any.isVisibleTo(year))
        self.assertFalse(year.first.isVisibleTo(year))
        self.assertIn("every 7 Nov", self.page.summary.text())
        day.to.setChecked(True)  # a range: it starts as the one day
        self.assertTrue(day.last.isVisibleTo(day))
        self.assertEqual(self.stored()["day"], [7, 7])
        day.last.setValue(14)
        self.assertEqual(self.stored(), {"type": "dates", "day": [7, 14], "month": 11})
        self.assertIn("every 7–14 Nov", self.page.summary.text())
        day.first.setValue(20)  # past the end: the end follows
        self.assertEqual(self.stored()["day"], [20, 20])
        day.to.setChecked(False)  # back to one day
        self.assertEqual(self.stored()["day"], 20)
        day.tick.setChecked(False)
        month.tick.setChecked(False)
        self.assertEqual(self.stored(), {"type": "dates"})
        self.assertTrue(row._no_dates_note.isVisibleTo(row))  # nothing ticked: it says so
        month.tick.setChecked(True)
        self.assertFalse(row._no_dates_note.isVisibleTo(row))
        self.assertIn("every November", self.page.summary.text())

    def test_typing_a_range_end(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        row = self.open([dates(day=[7, 7], month=11)])
        day = row._date_parts["day"]
        self.assertTrue(day.to.isChecked())  # (a range in the file shows as one)
        day.last.lineEdit().selectAll()
        QTest.keyClicks(day.last, "14")  # ("1" on the way mustn't pull the start down to the 1st)
        QTest.keyClick(day.last, Qt.Key.Key_Return)
        self.assertEqual(self.stored()["day"], [7, 14])

    def test_the_day_stays_inside_its_month(self) -> None:
        row = self.open([dates(day=31, month=12)])
        row._date_parts["month"].first.setCurrentIndex(1)  # February
        self.assertEqual(self.stored(), {"type": "dates", "day": 29, "month": 2})

    def test_an_old_condition_is_shown_in_the_fields_and_saved_new_when_changed(self) -> None:
        row = self.open([dates(**{"from": "12-24", "to": "12-26"})])
        parts = row._date_parts
        self.assertEqual([(p.tick.isChecked(), p.value()) for p in parts.values()], [(True, [24, 26]), (True, 12), (False, None)])
        self.assertEqual(self.stored(), {"type": "dates", "from": "12-24", "to": "12-26"})  # (nothing saved yet)
        parts["year"].tick.setChecked(True)
        self.assertEqual(self.stored(), {"type": "dates", "day": [24, 26], "month": 12, "year": date.today().year})

    def test_from_to_for_a_span_across_a_month_end(self) -> None:
        from PySide6.QtWidgets import QComboBox, QPushButton

        row = self.open([dates(**{"from": "12-24", "to": "01-06", "year": 2026})])
        self.assertEqual(row._date_parts, {})  # From … to …
        switch = next(b for b in row.findChildren(QPushButton) if b.text() == "From … to …")
        self.assertTrue(switch.isChecked())
        months = [c for c in row.findChildren(QComboBox) if c is not row.kind]
        months[1].setCurrentIndex(months[1].findData(2))  # to … Feb
        self.assertEqual(self.stored(), {"type": "dates", "from": "12-24", "to": "02-06", "year": 2026})
        switch.setChecked(False)  # to the fields: its first day
        process(self.app)
        self.assertEqual(self.stored(), {"type": "dates", "day": 24, "month": 12, "year": 2026})
        row = self.page.rows["conditions"][0]
        self.assertEqual([p.value() for p in row._date_parts.values()], [24, 12, 2026])
        switch = next(b for b in row.findChildren(QPushButton) if b.text() == "From … to …")
        switch.setChecked(True)  # and back: from that day to that day, still From … to … here
        process(self.app)
        self.assertEqual(self.stored(), {"type": "dates", "from": "12-24", "to": "12-24", "year": 2026})
        row = self.page.rows["conditions"][0]
        self.assertEqual(row._date_parts, {})
        self.assertIn("24 Dec 2026", self.page.summary.text())


if __name__ == "__main__":
    unittest.main()
