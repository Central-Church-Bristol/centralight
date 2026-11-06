"""Windows shutting down, for real (6 Nov): the first real shutdown with the shutdown automation did nothing, and no
restart that evening was ever written in the session log. CentraLight runs under the .venv launcher, which has no
window and keeps CentraLight in a job that ends with it, so Windows ending the launcher first ended CentraLight before
it heard a thing. CentraLight now asks Windows to deal with it first; and real Windows messages are checked through the
real path (a hidden window on the windows platform, in a child process)."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from centralight.ui import power

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "Windows only")
class ShutdownOrderTests(unittest.TestCase):
    def test_centralight_asks_windows_to_deal_with_it_first(self) -> None:
        self.assertTrue(power.shut_down_first())
        level, flags = ctypes.c_uint(0), ctypes.c_uint(0)
        self.assertTrue(ctypes.WinDLL("kernel32").GetProcessShutdownParameters(ctypes.byref(level), ctypes.byref(flags)))
        self.assertEqual(level.value, power.SHUTDOWN_LEVEL)  # (the highest an app may have: before the launcher)

    def test_the_filter_says_when_a_message_goes_wrong(self) -> None:
        from centralight.paths import session_log_path

        def broken(kind: str, hwnd: int) -> None:
            raise RuntimeError("in the way")

        message = power._MSG(hwnd=None, message=power.WM_QUERYENDSESSION, wParam=0, lParam=0)
        result = power.PowerEventFilter(broken).nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
        self.assertEqual(tuple(result), (False, 0))  # (Windows still gets Qt's answer)
        self.assertIn("a Windows power or shutdown message", session_log_path().read_text(encoding="utf-8"))


CHILD = r'''
import ctypes, json, os, sys, threading, time
from ctypes import wintypes
root, home, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
os.environ["CENTRALIGHT_TEST_HOME"] = home
os.environ.pop("QT_QPA_PLATFORM", None)  # (the real windows platform: the window is never shown)
from tests import guard
guard.install()
os.environ["CENTRALIGHT_INSTANCE"] = f"tests-shutdown-{os.getpid()}"
from PySide6.QtCore import QMetaObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
from centralight.ui import power
submitted = []
def submit(event):  # (as the engine: its fade takes 0.3 s, then the last frame is out)
    submitted.append(event.kind)
    if event.done is not None:
        threading.Timer(0.3, event.done.set).start()
down = power.PowerDown(submit)
app.installNativeEventFilter(filter_ := power.PowerEventFilter(down))
window = QWidget()
hwnd = int(window.winId())
user32 = ctypes.WinDLL("user32")
user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                       wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
answers = {}
def send(name, message, wparam, lparam):
    answer = ctypes.c_size_t(0)
    started = time.monotonic()
    ok = user32.SendMessageTimeoutW(hwnd, message, wparam, lparam, 0x0002, 8000, ctypes.byref(answer))
    answers[name] = [ok, answer.value, round(time.monotonic() - started, 3)]
def sender():
    time.sleep(0.5)
    send("sleep", 0x0218, 0x0004, 0)
    send("query", 0x0011, 0, 0x80000000)  # (a sign-out, as Fast Startup's shutdown does)
    send("cancelled", 0x0016, 0, 0x80000000)
    QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)
threading.Thread(target=sender, daemon=True).start()
QTimer.singleShot(20000, app.quit)
app.exec()
json.dump({"submitted": submitted, "answers": answers, "ending": down._ending}, open(out, "w"))
'''


@unittest.skipUnless(WINDOWS, "Windows only")
class RealMessageTests(unittest.TestCase):
    def test_real_messages_reach_it_and_windows_waits_for_the_fade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script, out = Path(tmp) / "child.py", Path(tmp) / "seen.json"
            script.write_text(CHILD, encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"}
            run = subprocess.run([sys.executable, str(script), str(ROOT), str(Path(tmp) / "home"), str(out)], env=env,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            seen = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(seen["submitted"], ["sleeping", "shutdown"])
        ok, answer, took = seen["answers"]["query"]
        self.assertEqual((ok, answer), (1, 1))  # (Windows may carry on...)
        self.assertGreaterEqual(took, 0.25)  # (...once the fade is done...)
        self.assertLess(took, power.SHUTDOWN_WAIT)  # (...and well within what Windows waits)
        self.assertEqual(seen["answers"]["sleep"][0], 1)
        self.assertFalse(seen["ending"])  # (the cancelled shutdown: CentraLight carries on)


if __name__ == "__main__":
    unittest.main()
