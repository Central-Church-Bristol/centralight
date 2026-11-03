"""Look preview: the palette, Futura PT at UI sizes and the main controls on one page,
for checking the design (Settings → Look preview…)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QRawFont
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..paths import fonts_dir
from . import theme
from .dashboard import Swatch
from .widgets import STATE_ERROR, STATE_OFF, STATE_OK, STATE_WARN, Fader, Panel, StatusChip, ToggleSwitch

PALETTE = (
    ("Window", theme.WINDOW),
    ("Panel", theme.PANEL),
    ("Card", theme.CARD),
    ("Border", theme.BORDER),
    ("Text", theme.TEXT),
    ("Secondary text", theme.TEXT_2),
    ("Accent (church pale blue)", theme.ACCENT),
    ("Text on accent", theme.ACCENT_TEXT),
    ("On Base (coral)", theme.LIVE),
    ("In an overlay (lime)", theme.OVERLAY),
    ("OK (turquoise)", theme.OK),
    ("Blackout / error", theme.DANGER),
    ("Warning (chips only)", theme.WARN),
)
FUTURA_STYLES = (("Book", 400), ("Medium", 500), ("Demi", 600), ("Bold", 700))
SAMPLE = "Festoon 80% · Key lights · Pillars purple — 0123456789 …"
UI_SYMBOLS = "…—–·×→←↑↓°%◆●"


def coverage(font_file: str, characters: str) -> str:
    """The characters a font file doesn't have."""
    path = fonts_dir() / font_file
    if not path.is_file():
        return characters
    raw = QRawFont(str(path), 20)
    return "".join(ch for ch in characters if ch != " " and not raw.supportsCharacter(ch))


class LookPreview(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Look preview")
        self.setObjectName("LookPreview")
        found = theme.fonts()

        palette = Panel("Palette")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, (name, colour) in enumerate(PALETTE):
            swatch = Swatch()
            swatch.set_colour(QColor(colour))
            grid.addWidget(swatch, row, 0)
            grid.addWidget(QLabel(name), row, 1)
            code = QLabel(colour)
            code.setObjectName("RowDetail")
            grid.addWidget(code, row, 2)
        grid.setColumnStretch(1, 1)
        palette.body.addLayout(grid)

        fonts_panel = Panel("Futura PT (interface)")
        report = QLabel(
            f"Interface font: {found.ui}\n"
            f"Loaded: {', '.join(found.loaded) or 'none'}"
            + (f"\nMissing (using Segoe UI instead): {', '.join(found.missing)}" if found.missing else "")
        )
        report.setObjectName("Hint")
        report.setWordWrap(True)
        fonts_panel.body.addWidget(report)
        for style, weight in FUTURA_STYLES:
            for px in (11, 12, 13, 14):
                label = QLabel(f"{style} {px}px — {SAMPLE}")
                font = theme.ui_font(px)
                font.setWeight(_weight(weight))
                label.setFont(font)
                fonts_panel.body.addWidget(label)
        missing_symbols = coverage("FuturaCyrillicBook.ttf", UI_SYMBOLS)
        fonts_panel.body.addWidget(
            _hint(f"Symbols Futura PT doesn't have (Windows fills them in from Segoe UI): {missing_symbols or 'none'}")
        )

        controls = Panel("Controls")
        row = QHBoxLayout()
        normal = QPushButton("Button")
        accent = QPushButton("Send to base")
        accent.setProperty("accent", True)
        danger = QPushButton("Turn lights off")
        danger.setProperty("danger", True)
        disabled = QPushButton("Disabled")
        disabled.setEnabled(False)
        for widget in (normal, accent, danger, disabled):
            row.addWidget(widget)
        row.addStretch(1)
        controls.body.addLayout(row)
        row2 = QHBoxLayout()
        on = ToggleSwitch()
        on.setChecked(True)
        row2.addWidget(on)
        row2.addWidget(ToggleSwitch())
        fader = Fader()
        fader.set_level(0.6)
        row2.addWidget(fader, 1)
        controls.body.addLayout(row2)
        row3 = QHBoxLayout()
        for state, text in ((STATE_OK, "COM3 · 40/s"), (STATE_WARN, "NDI · no signal"), (STATE_ERROR, "COM3 busy"), (STATE_OFF, "Audio off")):
            chip = StatusChip(state)
            chip.set_status(state, text)
            row3.addWidget(chip)
        row3.addStretch(1)
        controls.body.addLayout(row3)

        content = QWidget()
        content.setObjectName("ScrollContent")
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        for panel in (palette, fonts_panel, controls):
            layout.addWidget(panel)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)
        self.resize(900, 760)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _weight(value: int):
    from PySide6.QtGui import QFont

    return QFont.Weight(value)
