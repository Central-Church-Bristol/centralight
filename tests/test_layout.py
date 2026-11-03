"""Layout and scaling: every page at 1280x720 ... 2560x1440, at 100/125/150/200 % scaling.

Runs tests/layout_check.py once per scale factor (Qt's scale factor is fixed per
process). Screenshots go to tests/screens/latest (or $CENTRALIGHT_SCREENS).
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENS = Path(os.environ.get("CENTRALIGHT_SCREENS", ROOT / "tests" / "screens" / "latest"))


def run_check(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "tests" / "layout_check.py"), *args, "--out", str(SCREENS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
    )


class LayoutAndScalingTests(unittest.TestCase):
    def test_brand_fonts_at_every_scale(self) -> None:
        for scale in ("1", "1.25", "1.5", "2"):
            with self.subTest(scale=scale):
                result = run_check("--scale", scale)
                self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-3000:])

    def test_fallback_fonts(self) -> None:
        for scale in ("1", "1.5"):
            with self.subTest(scale=scale):
                result = run_check("--scale", scale, "--fallback-fonts", "--sizes", "1280x720,1920x1080")
                self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
