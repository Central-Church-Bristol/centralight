"""M14 item 6: which weeks of the month a Days of the week condition is for ("1st Wed", "1st & 3rd Sun", "Last Fri"):
months starting on different days, five-week months, Last vs 4th, the year's end, the overnight rule, the summary,
checking files, the week schedule and Coming up, and the buttons in the automation's editor."""

from __future__ import annotations

import calendar
import unittest
from datetime import date, datetime

from centralight.automations import AutomationRunner, Situation, check_condition, describe_condition, in_weeks, upcoming
from centralight.model import Show
from centralight.schedule import week_schedule
from tests.test_automations import automation


def wednesdays(year: int, month: int) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, days + 1) if date(year, month, d).weekday() == 2]


class WeeksTests(unittest.TestCase):
    def matches(self, conditions: list, local: datetime) -> bool:
        return AutomationRunner().matches(automation("a", conditions, []), Situation(now=0.0, local=local))[0]

    def test_the_nth_and_last_in_months_that_start_on_every_day(self) -> None:
        months = [(2026, m) for m in range(1, 13)] + [(2027, 1), (2027, 2)]
        self.assertEqual(len({date(y, m, 1).weekday() for y, m in months}), 7)  # (every first day of the week is here)
        for year, month in months:
            weds = wednesdays(year, month)
            for i, day in enumerate(weds):
                names = [w for w in ("1st", "2nd", "3rd", "4th", "last") if in_weeks([w], day)]
                expected = (["1st", "2nd", "3rd", "4th"][i:i + 1] if i < 4 else []) + (["last"] if day == weds[-1] else [])
                self.assertEqual(names, expected, (year, month, day))

    def test_last_and_4th_in_a_five_week_month(self) -> None:
        weds = wednesdays(2026, 12)  # (2, 9, 16, 23 and 30 December)
        self.assertEqual(len(weds), 5)
        self.assertTrue(in_weeks(["4th"], weds[3]) and not in_weeks(["last"], weds[3]))
        self.assertTrue(in_weeks(["last"], weds[4]) and not in_weeks(["4th"], weds[4]))
        feb = wednesdays(2027, 2)  # (February 2027: exactly four of each day, so the 4th is the last)
        self.assertEqual(len(feb), 4)
        self.assertTrue(in_weeks(["4th"], feb[3]) and in_weeks(["last"], feb[3]))
        self.assertTrue(in_weeks([], weds[2]))  # (no weeks: every week)

    def test_first_wednesday_through_the_automations(self) -> None:
        first_wed = [{"type": "days", "days": ["wed"], "weeks": ["1st"]}]
        self.assertTrue(self.matches(first_wed, datetime(2026, 11, 4, 19, 0)))
        self.assertFalse(self.matches(first_wed, datetime(2026, 11, 11, 19, 0)))
        self.assertFalse(self.matches(first_wed, datetime(2026, 11, 5, 19, 0)))  # (a Thursday)
        self.assertTrue(self.matches([{"type": "days", "days": ["sun"], "weeks": ["1st", "3rd"]}], datetime(2026, 11, 15, 10, 0)))

    def test_the_years_end(self) -> None:
        last_thu = [{"type": "days", "days": ["thu"], "weeks": ["last"]}]
        first_fri = [{"type": "days", "days": ["fri"], "weeks": ["1st"]}]
        self.assertTrue(self.matches(last_thu, datetime(2026, 12, 31, 12, 0)))
        self.assertTrue(self.matches(first_fri, datetime(2027, 1, 1, 12, 0)))
        self.assertFalse(self.matches(first_fri, datetime(2026, 12, 25, 12, 0)))  # (the 4th and last Friday of 2026)

    def test_the_overnight_rule_is_unchanged(self) -> None:
        # 23:00-01:00 on the 1st Wednesday: after midnight it's still Wednesday's range (the day it started)
        late = [{"type": "days", "days": ["wed"], "weeks": ["1st"]}, {"type": "time", "from": "23:00", "to": "01:00"}]
        self.assertTrue(self.matches(late, datetime(2026, 11, 4, 23, 30)))
        self.assertTrue(self.matches(late, datetime(2026, 11, 5, 0, 30)))  # (Thursday 00:30: Wednesday's range)
        self.assertFalse(self.matches(late, datetime(2026, 11, 12, 0, 30)))  # (the 2nd Wednesday's)
        self.assertFalse(self.matches(late, datetime(2026, 11, 5, 23, 30)))

    def test_the_summary(self) -> None:
        self.assertEqual(describe_condition({"type": "days", "days": ["wed"], "weeks": ["1st"]}), "1st Wed")
        self.assertEqual(describe_condition({"type": "days", "days": ["sun"], "weeks": ["3rd", "1st"]}), "1st & 3rd Sun")
        self.assertEqual(describe_condition({"type": "days", "days": ["fri"], "weeks": ["last"]}), "Last Fri")
        self.assertEqual(describe_condition({"type": "days", "days": ["fri"]}), "Fri")  # (every week, as before)

    def test_checking_files(self) -> None:
        problems = check_condition
        good = {"type": "days", "days": ["wed"], "weeks": ["1st", "last"]}
        self.assertEqual(problems(good), [])
        self.assertEqual(problems({"type": "days", "days": ["wed"], "weeks": []}), [])  # (none: every week)
        self.assertTrue(any("weeks" in p for p in problems({"type": "days", "days": ["wed"], "weeks": ["5th"]})))
        self.assertTrue(any("weeks" in p for p in problems({"type": "days", "days": ["wed"], "weeks": "1st"})))

    def test_the_week_schedule_and_coming_up(self) -> None:
        evening = automation("first-wed", [{"type": "days", "days": ["wed"], "weeks": ["1st"]},
                                           {"type": "time", "from": "19:00", "to": "21:00"}], [{"do": "load_show", "show": "evening"}])
        shows = {"evening": "Evening"}
        blocks, _unplaced = week_schedule([evening], shows, date(2026, 11, 2))
        self.assertEqual([(b.start, b.end) for b in blocks], [(datetime(2026, 11, 4, 19, 0), datetime(2026, 11, 4, 21, 0))])
        later, _unplaced = week_schedule([evening], shows, date(2026, 11, 9))
        self.assertEqual(later, [])  # (no 1st Wednesday that week)
        december, _unplaced = week_schedule([evening], shows, date(2026, 11, 30))
        self.assertEqual([b.start for b in december], [datetime(2026, 12, 2, 19, 0)])
        container = Show(name="Global").with_automations({evening.id: evening}, (evening.id,))
        coming = upcoming(container, datetime(2026, 11, 28, 12, 0), days=7)
        self.assertEqual([moment for moment, _automation, _ends in coming][:1], [datetime(2026, 12, 2, 19, 0)])


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def test_the_weeks_buttons(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.automations import add_automation, update_automation
        from centralight.new_show import show_from_patch
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine = FakeEngine(show_from_patch())
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(tmp.name) / "fixture_map.csv")
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        try:
            window.show_page("automation")
            page = window.automation_page
            show, new_id = add_automation(window.document.show, "Prayer meeting")
            window.document.apply(show, "New automation")
            window.document.apply(update_automation(window.document.show, new_id, conditions=[{"type": "days", "days": ["wed"]}]),
                                  "days")
            page.selected = new_id
            page._build_editor(force=True)
            process(self.app)

            def condition():
                from centralight.show_io import thaw

                return thaw(window.document.show.automations[new_id].conditions[0])

            row = page.rows["conditions"][0]
            self.assertTrue(row._every_week.isChecked())
            row._week_buttons["1st"].click()
            process(self.app)
            self.assertEqual(condition(), {"type": "days", "days": ["wed"], "weeks": ["1st"]})
            from centralight.automations import summary

            self.assertIn("1st Wed", summary(window.document.show.automations[new_id]))
            row = page.rows["conditions"][0]
            self.assertFalse(row._every_week.isChecked())
            row._week_buttons["3rd"].click()
            process(self.app)
            self.assertEqual(condition()["weeks"], ["1st", "3rd"])
            row = page.rows["conditions"][0]
            row._every_week.click()
            process(self.app)
            self.assertEqual(condition(), {"type": "days", "days": ["wed"]})  # (every week: left out of the file, as before)
            row = page.rows["conditions"][0]
            self.assertTrue(row._every_week.isChecked() and not any(b.isChecked() for b in row._week_buttons.values()))
            row._every_week.click()  # (it stays on until a week is picked)
            process(self.app)
            self.assertTrue(page.rows["conditions"][0]._every_week.isChecked())
        finally:
            window._shut_down = True
            window.document.close()
            window.close()
            window.deleteLater()
            process(self.app)


if __name__ == "__main__":
    unittest.main()
