"""CentraLight.exe runs Python inside itself (8 Nov). Windows saw a Python process: a second taskbar button beside the
pin, "Python" on the tray's notification and in Task Manager. The launcher (launcher/Program.cs) now loads the venv's
own Python (pyvenv.cfg's home) with the Python Configuration API of Python 3.14 and runs main.py in its own process;
if it can't, it starts .venv\\Scripts\\pythonw.exe as before, saying why. The launcher tests build a test copy into a
temporary folder with a stand-in main.py and a venv (a junction to the one running the tests, or a pretend one)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from centralight import startup

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
IN_A_VENV = (Path(sys.prefix) / "pyvenv.cfg").is_file()

STAND_IN = '''"""What the Python that CentraLight.exe runs sees, in found.json."""
import ctypes, json, os, sys

buffer = ctypes.create_unicode_buffer(32768)
ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, 32768)
found = {"process": buffer.value, "executable": sys.executable, "prefix": sys.prefix, "argv": sys.argv[1:],
         "path0": sys.path[0], "separate": os.environ.get("CENTRALIGHT_SEPARATE"), "no_stdout": sys.stdout is None}
try:
    import numpy, PySide6.QtCore
    found["packages"] = True
except Exception as exc:
    found["packages"] = repr(exc)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "found.json"), "w", encoding="utf-8") as f:
    json.dump(found, f)
sys.exit(7)
'''


@unittest.skipUnless(CSC.is_file() and IN_A_VENV, "needs the C# compiler that comes with Windows, and a venv")
class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="centralight-launcher-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "main.py").write_text(STAND_IN, encoding="utf-8")
        sys.path.insert(0, str(ROOT / "tools"))
        import build_launcher

        self.exe = self.folder / "CentraLight.exe"
        build_launcher.compile_exe(self.exe, ROOT / "launcher" / "icon.ico")

    def found(self, wait: float = 0.0) -> dict:
        path = self.folder / "found.json"
        deadline = time.monotonic() + wait
        while not path.is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_python_runs_inside_it_with_the_venv(self) -> None:
        link = self.folder / ".venv"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), sys.prefix], check=True, capture_output=True)
        self.addCleanup(os.rmdir, link)  # (the junction only, never what it points at; before the folder goes)
        result = subprocess.run([str(self.exe), "--minimized", "two words"], cwd=self.folder, timeout=60)
        self.assertEqual(result.returncode, 7)  # (main.py's exit code)
        found = self.found()
        self.assertEqual(Path(found["process"]), self.exe)  # (Windows sees CentraLight.exe, not pythonw.exe)
        self.assertEqual(Path(found["executable"]), link / "Scripts" / "pythonw.exe")
        self.assertEqual(Path(found["prefix"]), link)  # (the venv...)
        self.assertIs(found["packages"], True)  # (...with its packages)
        self.assertEqual(found["argv"], ["--minimized", "two words"])
        self.assertEqual(Path(found["path0"]), self.folder)  # (main.py's folder first, for the centralight package)
        self.assertIsNone(found["separate"])
        self.assertTrue(found["no_stdout"])  # (no console, as with pythonw.exe)

    def test_with_a_python_it_cant_run_it_starts_pythonw_saying_why(self) -> None:
        """A Python without the Configuration API (older than 3.14): the venv's pythonw.exe, as before 8 Nov."""
        base = Path(sys.base_prefix)
        old = self.folder / "old python"
        old.mkdir()
        shutil.copy(base / "python3.dll", old / "python39.dll")  # (a Python 3.9 DLL, as far as it can tell: no API)
        scripts = self.folder / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (self.folder / ".venv" / "pyvenv.cfg").write_text(f"home = {old}\n", encoding="utf-8")
        dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        for name in ("pythonw.exe", dll, "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            if (base / name).is_file():
                shutil.copy(base / name, scripts / name)  # (a pythonw.exe that runs, with this Python's own library)
        env = dict(os.environ, PYTHONHOME=str(base))
        result = subprocess.run([str(self.exe), "--minimized"], cwd=self.folder, env=env, timeout=60)
        self.assertEqual(result.returncode, 0)  # (it started the other one)
        found = self.found(wait=30.0)
        self.assertEqual(Path(found["process"]), scripts / "pythonw.exe")
        self.assertEqual(found["argv"], ["--minimized"])
        self.assertIn("python39.dll has no Python Configuration API", found["separate"])


class SourceTests(unittest.TestCase):
    def test_the_source_runs_python_inside_and_falls_back(self) -> None:
        source = (ROOT / "launcher" / "Program.cs").read_text(encoding="utf-8")
        for text in ("PyInitConfig_Create", "Py_InitializeFromInitConfig", "Py_RunMain", "pyvenv.cfg", "pythonw.exe",
                     "CENTRALIGHT_SEPARATE", "run.bat", "[STAThread]"):  # (STA: Qt's drag and drop and clipboard)
            self.assertIn(text, source)

    def test_the_committed_exe_is_the_one_that_runs_python_inside(self) -> None:
        data = (ROOT / "CentraLight.exe").read_bytes()
        self.assertIn("PyInitConfig_Create".encode("utf-16-le"), data)
        header = int.from_bytes(data[0x3C:0x40], "little")
        self.assertEqual(int.from_bytes(data[header + 24:header + 26], "little"), 0x20B)  # (64-bit, as Python)
        self.assertIn(b"8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a", data)  # (pythonw.exe's manifest: Windows 10)


class IdentityTests(unittest.TestCase):
    def test_how_it_tells_and_what_the_log_says(self) -> None:
        with mock.patch.object(startup, "process_file", return_value=r"C:\Centralight\CentraLight.exe"):
            self.assertTrue(startup.inside_launcher())
            self.assertEqual(startup.how_started(), "Python runs inside CentraLight.exe")
        with mock.patch.object(startup, "process_file", return_value=r"C:\Python\pythonw.exe"):
            self.assertFalse(startup.inside_launcher())
            with mock.patch.dict(os.environ, {"CENTRALIGHT_SEPARATE": "no python3NN.dll in C:\\Old"}):
                self.assertIn("couldn't run Python inside itself (no python3NN.dll in C:\\Old)", startup.how_started())
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CENTRALIGHT_SEPARATE", None)
                self.assertTrue(startup.how_started().startswith("Started with "))
        self.assertTrue(startup.process_file())  # (the test runner's python.exe)

    def test_an_explicit_app_id_only_outside_centralight_exe(self) -> None:
        """Inside CentraLight.exe Windows knows the process as the program its pin starts: an explicit ID would make
        it another app, with a button of its own. In a pythonw.exe of its own the ID keeps it apart from Python."""
        import ctypes

        from centralight import app

        for inside, calls in ((True, []), (False, [mock.call("CentraLight.App.1")])):
            with mock.patch.object(startup, "inside_launcher", return_value=inside), \
                    mock.patch.object(ctypes.windll.shell32, "SetCurrentProcessExplicitAppUserModelID") as set_id:
                app._prepare_windows_shell()
            self.assertEqual(set_id.call_args_list, calls)


if __name__ == "__main__":
    unittest.main()
