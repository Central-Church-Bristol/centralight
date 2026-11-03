"""Sunrise and sunset in Bristol (the "Sunrise / sunset" automation condition)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from centralight.sun import sun_minutes, sun_times


def hhmm(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def utc_offset_minutes(day: date) -> int:
    """This PC's offset from UTC at noon on `day` (60 in British Summer Time, 0 in winter)."""
    noon = datetime(day.year, day.month, day.day, 12)
    utc = datetime.fromtimestamp(noon.timestamp(), timezone.utc).replace(tzinfo=None)
    return round((noon - utc).total_seconds() / 60)


class SunTests(unittest.TestCase):
    # Published times for Bristol, in UTC (so the test passes whatever the PC's time zone)
    KNOWN_UTC = {
        date(2026, 11, 3): ("07:07", "16:40"),
        date(2026, 12, 21): ("08:13", "16:04"),
        date(2026, 6, 21): ("03:56", "20:32"),
        date(2027, 3, 20): ("06:13", "18:22"),
    }

    def test_known_days(self) -> None:
        for day, (rise, set_) in self.KNOWN_UTC.items():
            times = sun_times(day)
            self.assertIsNotNone(times)
            offset = utc_offset_minutes(day)
            for got, want in zip(times, (rise, set_)):
                want_minutes = int(want[:2]) * 60 + int(want[3:]) + offset
                self.assertLessEqual(abs(hhmm(got) - want_minutes), 4, f"{day}: {got:%H:%M} vs {want} UTC")
                self.assertEqual(got.date(), day)

    def test_sunrise_before_sunset_all_year(self) -> None:
        day = date(2026, 1, 1)
        while day.year == 2026:
            rise, set_ = sun_times(day)
            self.assertLess(rise, set_)
            self.assertTrue(6 * 60 <= set_.hour * 60 + set_.minute - (rise.hour * 60 + rise.minute) <= 18 * 60)
            day = date.fromordinal(day.toordinal() + 1)

    def test_minutes(self) -> None:
        day = date(2026, 11, 3)
        rise, set_ = sun_times(day)
        self.assertEqual(sun_minutes(day, "sunrise"), hhmm(rise))
        self.assertEqual(sun_minutes(day, "sunset"), hhmm(set_))

    def test_no_sunset_in_the_arctic_summer(self) -> None:
        self.assertIsNone(sun_times(date(2026, 6, 21), 78.2, 15.6))  # Svalbard: the sun never sets


if __name__ == "__main__":
    unittest.main()
