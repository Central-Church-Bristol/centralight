"""Start the CentraLight app (double-click run.bat, or: .venv\\Scripts\\pythonw.exe main.py).

Only one copy runs at a time: a second one asks the running copy to show its window, then exits.
--minimized starts in the tray (that's how Windows starts it at sign-in).
"""

import ctypes
import os
import sys
from ctypes import wintypes

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # numpy shouldn't grab every CPU core

ERROR_ALREADY_EXISTS = 183
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE

_name = "Local\\CentraLight.SingleInstance"
if os.environ.get("CENTRALIGHT_INSTANCE"):  # tests use their own name, so they never clash with the real app
    _name += "." + os.environ["CENTRALIGHT_INSTANCE"]
_SINGLETON = _kernel32.CreateMutexW(None, True, _name)
if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
    try:
        from PySide6.QtCore import QCoreApplication

        from centralight.ui.instance import ask_running_copy_to_show

        _app = QCoreApplication(sys.argv)
        ask_running_copy_to_show()
    except Exception:
        pass
    sys.exit(0)

from centralight.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
