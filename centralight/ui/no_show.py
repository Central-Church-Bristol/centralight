"""No show loaded (M14): the card every tab shows (the Automation tab apart) while no show is loaded, as when the
Default show is None (lights off) and the PC has just started, or a schedule has ended. The lights are off; a show
from its list loads as any show does (its lights come up from black, its "When this show loads" automations run).
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..automations import upcoming
from ..model import Show
from ..shows import ShowInfo, find_show
from . import icons, theme
from .widgets import ElidedLabel

CARD_WIDTH = 760  # logical pixels, at most (as the agreed mock-up)
COLUMNS = 2  # the shows, side by side
LOGO_SIDE = 76
LOGO_OPACITY = 0.3  # the logo's circles, dimmed: the lights are off
ON, PAUSED, NONE = "on", "paused", "none"  # the global automations' line: its dot's colour
DOT_COLOURS = {ON: theme.OK, PAUSED: theme.WARN, NONE: theme.TEXT_3}


def automations_line(global_automations: Show | None, shows: list[ShowInfo], paused: bool, now: datetime) -> tuple[str, str]:
    """The card's last line, (state, text): the global automations are still on, and which will load a show next (in
    the next 7 days), or they're paused, or none of them will load a show."""
    if paused:
        return PAUSED, "Global automations are paused, so none of them will load a show until they're resumed."
    found = None
    if global_automations is not None:
        for moment, automation, others in upcoming(global_automations, now, days=7, limit=50):
            wanted = [a.get("show") for a in automation.actions if isinstance(a, Mapping) and a.get("do") == "load_show"]
            if wanted and str(wanted[-1] or "").strip():
                found = (moment, automation, others, str(wanted[-1]).strip())
                break
    if found is None:
        return NONE, "Global automations are still on, but none of them will load a show in the next 7 days."
    moment, automation, others, wanted = found
    info = find_show(Path("."), wanted, shows)
    name = info.name if info is not None else wanted
    maybe = " (if its other conditions are true then)" if others else ""
    return ON, f"Global automations are still on.  Next: {automation.name}, {moment:%a %H:%M} → {name}{maybe}"


class NoShowPage(QWidget):
    """"No show loaded": the lights are off. A Load button for each show, New show… and Import a show…, and whether the
    global automations will load one."""

    load_requested = Signal(str)  # a show's folder
    new_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoShowPage")
        self._shows_key: tuple | None = None
        self._logo_key: tuple | None = None
        self._automations_text = ""
        self.load_buttons: dict[str, QPushButton] = {}  # each show's Load button, by folder

        self.card = QFrame()
        self.card.setObjectName("Panel")
        self.card.setMaximumWidth(CARD_WIDTH)
        self.card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout(self.card)
        body.setContentsMargins(40, 32, 40, 28)
        body.setSpacing(10)

        self.logo = QLabel()
        self.logo.setObjectName("NoShowLogo")
        self.logo.setFixedSize(LOGO_SIDE, LOGO_SIDE)
        self.logo.setAccessibleName("CentraLight logo, dimmed: the lights are off")
        body.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addSpacing(8)
        self.title = QLabel("No show loaded")
        self.title.setObjectName("NoShowTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        body.addWidget(self.title)
        self.message = QLabel("The lights are off. Load a show to bring them up.")
        self.message.setObjectName("NoShowText")
        self.message.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.message.setWordWrap(True)
        body.addWidget(self.message)
        body.addSpacing(18)

        shows_title = QLabel("SHOWS")
        shows_title.setObjectName("PanelTitle")
        body.addWidget(shows_title)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(12)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)
        body.addLayout(self.grid)
        self.none_yet = QLabel("No shows yet: make one with New show…, or import one.")
        self.none_yet.setObjectName("Hint")
        self.none_yet.setWordWrap(True)
        self.none_yet.setVisible(False)
        body.addWidget(self.none_yet)

        self.new_button = QPushButton("New show…")
        self.new_button.setToolTip("A new show with the church's lights and layouts, and “Follow screen”")
        self.new_button.clicked.connect(self.new_requested.emit)
        self.import_button = QPushButton("Import a show…")
        self.import_button.setToolTip("Import a show from a .clshow file (it comes in as a new show, not loaded)")
        self.import_button.clicked.connect(self.import_requested.emit)
        for button in (self.new_button, self.import_button):
            button.setObjectName("NoShowButton")
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 10, 0, 0)
        buttons.setSpacing(14)
        buttons.addStretch(1)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.import_button)
        buttons.addStretch(1)
        body.addLayout(buttons)

        line = QFrame()
        line.setObjectName("NoShowLine")
        line.setFixedHeight(1)
        body.addSpacing(14)
        body.addWidget(line)
        body.addSpacing(10)
        self.automations = QLabel("")  # "● Global automations are still on.  Next: …" (the dot's colour says which)
        self.automations.setObjectName("NoShowText")
        self.automations.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.automations.setWordWrap(True)
        self.automations.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self.automations)
        self.set_automations(NONE, "")

        content = QWidget()
        content.setObjectName("ScrollContent")
        centre = QHBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addStretch(1)
        centre.addWidget(self.card, 100)
        centre.addStretch(1)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)
        outer.addLayout(centre)
        outer.addStretch(3)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("NoShowScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

    # ------------------------------------------------------------ what it shows

    def set_shows(self, shows: list[ShowInfo]) -> None:
        """A card for each show: its name and a Load button."""
        key = tuple((s.folder, s.name, s.notes) for s in shows)
        if key == self._shows_key:
            return
        self._shows_key = key
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.load_buttons = {}
        for index, info in enumerate(shows):
            self.grid.addWidget(self._show_card(info), index // COLUMNS, index % COLUMNS)
        self.none_yet.setVisible(not shows)

    def _show_card(self, info: ShowInfo) -> QFrame:
        card = QFrame()
        card.setObjectName("NoShowRow")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 12, 14, 12)
        layout.setSpacing(10)
        name = ElidedLabel(info.name)
        name.setObjectName("NoShowName")
        if info.notes:
            name.setToolTip(f"{info.name}\n\n{info.notes}")
        layout.addWidget(name, 1)
        button = QPushButton("Load")
        button.setProperty("accent", True)
        button.setObjectName("NoShowLoad")
        button.setAccessibleName(f"Load {info.name}")
        button.setToolTip(f"Load {info.name}: its lights come up from black, and its “When this show loads” automations run")
        button.clicked.connect(lambda _checked=False, folder=info.folder: self.load_requested.emit(folder))
        layout.addWidget(button)
        self.load_buttons[info.folder] = button
        return card

    def set_automations(self, state: str, text: str) -> None:
        """The last line: whether the global automations will load a show (see automations_line)."""
        self._automations_text = text
        dot = DOT_COLOURS.get(state, theme.TEXT_3)
        words = html.escape(text).replace("  ", "&nbsp; ")  # (the two spaces before "Next" stay two)
        self.automations.setText(f'<span style="color:{dot}">●</span>&nbsp;&nbsp;{words}' if text else "")
        self.automations.setToolTip({ON: "The global automations are on", PAUSED: "The global automations are paused",
                                     NONE: "No global automation will load a show"}.get(state, "") if text else "")
        self.automations.setVisible(bool(text))

    def automations_text(self) -> str:
        return self._automations_text

    # ------------------------------------------------------------ the logo

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_logo()

    def _update_logo(self) -> None:
        dpr = self.devicePixelRatioF()
        key = (LOGO_SIDE, dpr)
        if key == self._logo_key:
            return
        self._logo_key = key
        source = icons.logo_pixmap(LOGO_SIDE, dpr).toImage()
        source.setDevicePixelRatio(1.0)  # (drawn pixel for pixel; the result says its ratio)
        dim = QPixmap(source.size())
        dim.fill(Qt.GlobalColor.transparent)
        painter = QPainter(dim)
        painter.setOpacity(LOGO_OPACITY)
        painter.drawImage(0, 0, source)
        painter.end()
        dim.setDevicePixelRatio(dpr)
        self.logo.setPixmap(dim)
