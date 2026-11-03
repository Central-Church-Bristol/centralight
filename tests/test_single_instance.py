"""Only one CentraLight at a time: a second copy exits straight away.

This starts the real app (offscreen, Simulated output, settings and shows in a
temporary folder), then starts it again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SingleInstanceTests(unittest.TestCase):
    def test_second_copy_exits_and_the_first_keeps_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(
                os.environ,
                QT_QPA_PLATFORM="offscreen",
                CENTRALIGHT_INSTANCE=f"single-test-{os.getpid()}",
                CENTRALIGHT_APPDATA=str(Path(tmp) / "appdata"),
                CENTRALIGHT_SHOWS=str(Path(tmp) / "shows"),
            )
            log = Path(tmp) / "appdata" / "session.log"
            first = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=env)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if log.exists() and "CentraLight started" in log.read_text(encoding="utf-8"):
                        break
                    if first.poll() is not None:
                        self.fail(f"the first copy exited early with code {first.returncode}")
                    time.sleep(0.2)
                else:
                    self.fail("the first copy never started")
                self.assertIn("output simulated", log.read_text(encoding="utf-8"))
                started = time.monotonic()
                second = subprocess.run([sys.executable, "main.py"], cwd=ROOT, env=env, timeout=30)
                self.assertEqual(second.returncode, 0)
                self.assertLess(time.monotonic() - started, 10)
                self.assertIsNone(first.poll(), "the first copy should still be running")
                self.assertTrue((Path(tmp) / "shows" / "Central Church" / "show.json").exists())
            finally:
                first.kill()
                first.wait(10)


if __name__ == "__main__":
    unittest.main()
