"""The test engine's clock (8 Nov). FakeEngine's automations see a fixed local time, a Wednesday afternoon, so the test
shows' automations can't run because of when the suite runs. On Sunday morning 15 tests failed: the sample show's
"Sunday welcome" (Sundays 09:00-12:30, while the colour lights are on) ran during them on the real clock."""

from __future__ import annotations

import time
import unittest
from datetime import datetime

from centralight.commands import SendToBase
from centralight.new_show import show_from_patch
from tests.fakes import FakeEngine, fixed_local_time
from tests.gui_fixtures import with_inputs


class FixedClockTests(unittest.TestCase):
    def test_a_wednesday_afternoon_whatever_the_real_clock_says(self) -> None:
        local = FakeEngine()._local_time(time.time())
        self.assertEqual((local.weekday(), local.hour), (2, 14))

    def test_the_sample_sunday_welcome_runs_only_when_a_test_asks_for_a_sunday(self) -> None:
        show = with_inputs(show_from_patch())
        for start, base in ((None, "pillars-purple"), (datetime(2026, 11, 8, 10, 0), "warm-white")):
            engine = FakeEngine(show, local_time=fixed_local_time(start))
            engine.submit(SendToBase("pillars-purple", cut=True))  # (lit: the welcome's "colour lights on" holds)
            engine.settle(5.0)
            self.assertEqual(engine.frames.get().base_id, base, start)


if __name__ == "__main__":
    unittest.main()
