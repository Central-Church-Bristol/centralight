"""A QApplication for window tests.

It uses Qt's offscreen platform, so no window ever appears on the PC's screens (or on
ProPresenter's output). That platform only knows the fonts it's given, so this loads the
brand fonts (or deliberately not, to test the fallback) plus Segoe UI from C:\\Windows\\Fonts.
QT_SCALE_FACTOR must be set before this is first called.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WINDOWS_FONTS = ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf")
_app = None


def qt_app(brand_fonts: bool = True):
    global _app
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    if _app is not None:
        return _app
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _app = QApplication.instance() or QApplication(["centralight-tests"])
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in WINDOWS_FONTS:
        if (windows_fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(windows_fonts / name))
    from centralight.ui import theme

    if not brand_fonts:
        theme.load_fonts(Path(tempfile.mkdtemp(prefix="no-fonts-")))
    _app.setStyle("Fusion")
    theme.apply_theme(_app)
    return _app


def process(app, rounds: int = 3) -> None:
    for _ in range(rounds):
        app.processEvents()
