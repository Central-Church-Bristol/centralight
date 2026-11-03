"""App settings and the session log."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from centralight import log
from centralight.settings import OUTPUT_SIMULATED, QUIT_ASK, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_defaults_are_safe(self) -> None:
        settings = Settings.load(self.path)
        self.assertEqual(settings.output, OUTPUT_SIMULATED)  # nothing is opened until you choose USB
        self.assertEqual(settings.quit_action, QUIT_ASK)
        self.assertEqual(settings.remote_port, 8746)

    def test_round_trip_and_bad_values(self) -> None:
        settings = Settings(output="usb", usb_port="COM3", geometry=[1, 2, 3, 4], splitters={"editor": [1, 2]})
        settings.save(self.path)
        self.assertEqual(Settings.load(self.path), settings)
        self.path.write_text(
            json.dumps({"output": "laser", "remote_port": 5, "geometry": "big", "quit_action": "?", "mystery": 1}),
            encoding="utf-8",
        )
        loaded = Settings.load(self.path)
        self.assertEqual(loaded.output, OUTPUT_SIMULATED)
        self.assertEqual(loaded.remote_port, 1024)
        self.assertIsNone(loaded.geometry)
        self.assertEqual(loaded.quit_action, QUIT_ASK)

    def test_broken_file_gives_defaults(self) -> None:
        self.path.write_text("{oops", encoding="utf-8")
        self.assertEqual(Settings.load(self.path), Settings())


class LogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "session.log"
        log._last_written.clear()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_writes_traceback_and_throttles_repeats(self) -> None:
        try:
            raise RuntimeError("tick died")
        except RuntimeError as exc:
            log.log_exception("engine tick", exc, path=self.path)
            log.log_exception("engine tick", exc, path=self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("RuntimeError: tick died"), 1)
        self.assertIn("engine tick", text)

    def test_info_lines_throttle_only_with_a_key(self) -> None:
        log.log_info("one", path=self.path)
        log.log_info("one", path=self.path)
        log.log_info("two", key="k", path=self.path)
        log.log_info("two", key="k", path=self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("one"), 2)
        self.assertEqual(text.count("two"), 1)

    def test_rotates_when_big(self) -> None:
        self.path.write_text("x" * (log._MAX_BYTES + 10), encoding="utf-8")
        log.log_info("fresh", path=self.path)
        self.assertTrue(self.path.with_name("session.log.1").exists())
        self.assertLess(self.path.stat().st_size, 200)

    def test_thread_crashes_are_logged(self) -> None:
        log.install_crash_logging()
        target = log.session_log_path()
        before = target.read_text(encoding="utf-8") if target.exists() else ""

        def crash() -> None:
            raise KeyError("thread crash marker")

        thread = threading.Thread(target=crash, name="crasher")
        thread.start()
        thread.join()
        self.assertIn("thread crash marker", target.read_text(encoding="utf-8")[len(before):])

    def test_logging_never_raises(self) -> None:
        from centralight.log import log_exception, log_info

        target = Path(self._tmp.name) / "odd.log"
        log_info("a file called bad\udcffname.json", path=target)  # a stray surrogate (from a mangled file name)
        try:
            raise ValueError("bad \udcff text")
        except ValueError as exc:
            log_exception("odd text test", exc, path=target)

        class Unprintable(Exception):
            def __str__(self) -> str:
                raise RuntimeError("no words")

        log_exception("unprintable test", Unprintable(), path=target)
        text = target.read_text(encoding="utf-8")
        self.assertIn("bad\\udcffname.json", text)
        self.assertIn("odd text test", text)
        self.assertIn("unprintable test", text)


if __name__ == "__main__":
    unittest.main()
