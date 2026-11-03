"""The look: Midnight colours with the church's pale blue, and the brand fonts.

Colours are named by what they're for, so a widget asks for ``ACCENT`` or ``LIVE``
rather than a hex code. style.qss uses the same names as @tokens.

Fonts: Futura PT for everything, including the CentraLight wordmark (Medium), loaded
from the fonts folder next to main.py (run.bat copies them from the church's brand
folder). If they're missing, Segoe UI is used instead. Fonts are never read from
Google Drive while the app runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from ..paths import fonts_dir

# --- colours (see the plan's Look table)
WINDOW = "#0B0F17"
PANEL = "#121926"
CARD = "#192233"
CARD_HOVER = "#212C42"
BORDER = "#26324A"
BORDER_STRONG = "#3A4A68"
TEXT = "#EEF3F8"
TEXT_2 = "#A3ADBD"
TEXT_3 = "#6E7A8F"  # disabled / hints
WELL = "#000000"  # behind light previews
ACCENT = "#86D6FF"  # church pale blue: actions, selection, toggles, focus, keyframes
ACCENT_TEXT = "#06202E"  # text on the accent colour
ACCENT_SOFT = "#1B3A4D"  # accent tint for hovered / selected backgrounds
LIVE = "#FF6F59"  # coral: on Base
OVERLAY = "#D6FF79"  # lime: in an overlay slot
OK = "#16E1C9"  # turquoise: connected
DANGER = "#FF3B5C"  # Cut / Fade to black, errors
WARN = "#FFB547"  # warnings (chips only)
LOGO_CORAL = "#FF6F59"
LOGO_LIME = "#D6FF79"
LOGO_SKY = "#00B7FF"

COLOURS = {
    "window": WINDOW,
    "panel": PANEL,
    "card": CARD,
    "card_hover": CARD_HOVER,
    "border": BORDER,
    "border_strong": BORDER_STRONG,
    "text": TEXT,
    "text_2": TEXT_2,
    "text_3": TEXT_3,
    "well": WELL,
    "accent": ACCENT,
    "accent_text": ACCENT_TEXT,
    "accent_soft": ACCENT_SOFT,
    "live": LIVE,
    "overlay": OVERLAY,
    "ok": OK,
    "danger": DANGER,
    "warn": WARN,
}

# --- fonts
UI_FAMILY = "Futura PT"
FALLBACK_FAMILY = "Segoe UI"
SYMBOL_FAMILY = "Segoe UI Symbol"
BRAND_FONT_FILES = (
    "FuturaCyrillicBook.ttf",
    "FuturaCyrillicMedium.ttf",
    "FuturaCyrillicDemi.ttf",
    "FuturaCyrillicBold.ttf",
)
BASE_PX = 14  # body text size in logical pixels


@dataclass(frozen=True)
class Fonts:
    ui: str  # family for the interface
    loaded: tuple[str, ...]  # font files that loaded
    missing: tuple[str, ...]  # font files that didn't


_fonts: Fonts | None = None


def load_fonts(folder: Path | None = None) -> Fonts:
    """Register the brand fonts with Qt (once). Safe to call before or after the window exists."""
    global _fonts
    if _fonts is not None:
        return _fonts
    folder = folder or fonts_dir()
    loaded: list[str] = []
    missing: list[str] = []
    families: set[str] = set()
    for name in BRAND_FONT_FILES:
        path = folder / name
        font_id = QFontDatabase.addApplicationFont(str(path)) if path.is_file() else -1
        if font_id < 0:
            missing.append(name)
            continue
        loaded.append(name)
        families.update(QFontDatabase.applicationFontFamilies(font_id))
    ui = UI_FAMILY if UI_FAMILY in families else FALLBACK_FAMILY
    _fonts = Fonts(ui=ui, loaded=tuple(loaded), missing=tuple(missing))
    return _fonts


def fonts() -> Fonts:
    return _fonts or load_fonts()


def ui_font(px: int = BASE_PX, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies([fonts().ui, FALLBACK_FAMILY, SYMBOL_FAMILY])
    font.setPixelSize(px)
    font.setWeight(weight)
    # Futura PT's own hinting puts gaps inside words ("Edit or") at some sizes; vertical-only
    # hinting keeps the spacing right and the text crisp
    font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    return font


def stylesheet() -> str:
    """style.qss with the @colour tokens and the font family filled in."""
    text = (Path(__file__).with_name("style.qss")).read_text(encoding="utf-8")
    replacements = {f"@{name}": value for name, value in COLOURS.items()}
    replacements["@uifont"] = f'"{fonts().ui}"'
    for token in sorted(replacements, key=len, reverse=True):  # @text_2 before @text
        text = text.replace(token, replacements[token])
    return text


def apply_theme(app: QApplication) -> Fonts:
    found = load_fonts()
    app.setFont(ui_font())
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: PANEL,
        QPalette.ColorRole.AlternateBase: CARD,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: CARD,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ACCENT_TEXT,
        QPalette.ColorRole.ToolTipBase: CARD,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT_3,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, colour in roles.items():
        palette.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_3))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
    return found


def mix(a: str, b: str, amount: float) -> QColor:
    """A colour part-way from a to b (amount 0-1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * amount),
        round(ca.green() + (cb.green() - ca.green()) * amount),
        round(ca.blue() + (cb.blue() - ca.blue()) * amount),
    )
