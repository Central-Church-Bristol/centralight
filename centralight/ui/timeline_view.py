"""The Editor's timeline: four tracks, Track 4 on top (it's drawn over the others).

In infinite mode (the default; timed sequences arrive in M5) each track holds one element,
shown as a bar across the whole track. Click a bar to select it, drop an element from the
palette onto a track to add it (replacing what's there, after asking), drop a transition
anywhere to set how the input appears, right-click for more, Delete to remove.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QSizePolicy, QVBoxLayout, QWidget

from ..input_edit import add_element, find_element, move_element, remove_element, set_transition
from ..model import TRACK_COUNT, Input
from ..schema import TRANSITION_CHOICES
from ..step_names import element_name
from . import theme
from .document import ShowDocument
from .element_palette import ELEMENT_MIME, TRANSITION_MIME

KIND_COLOURS = {"paint": theme.ACCENT, "image": theme.OVERLAY, "video": theme.WARN, "ndi": theme.OK}
KIND_TITLES = {"paint": "Paint", "image": "Image", "video": "Video", "ndi": "NDI"}


def summary(element) -> str:
    """A short description of an element for its bar."""
    kind = str(element.get("kind"))
    title = KIND_TITLES.get(kind, kind.title())
    if kind == "paint":
        fill = element.get("fill") or {}
        fill_type = fill.get("type", "colour") if hasattr(fill, "get") else "none"
        parts = []
        if fill_type == "colour":
            colour = fill.get("colour")
            parts.append(colour if isinstance(colour, str) else "colour (moving)")
        elif fill_type in ("linear", "radial"):
            parts.append("gradient")
        shapes = element.get("shapes") or ()
        if shapes:
            parts.append(f"{len(shapes)} shape{'s' if len(shapes) != 1 else ''}")
        detail = ", ".join(parts) or "empty"
    elif kind == "ndi":
        detail = str(element.get("source") or "Media Background Pane")
    elif kind in ("image", "video"):
        detail = str(element.get("file") or "no file chosen").split("/")[-1]
    else:
        detail = "unknown kind (draws nothing)"
    mask = element.get("mask") or {}
    if hasattr(mask, "get") and mask.get("exclude"):
        detail += f" · skips {len(mask.get('exclude'))} light{'s' if len(mask.get('exclude')) != 1 else ''}"
    return f"{title} · {detail}"


class TrackRow(QFrame):
    clicked = Signal(int)
    dropped_element = Signal(int, str)  # track, kind
    dropped_transition = Signal(str)
    menu_requested = Signal(int, QPoint)

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrackRow")
        self.index = index
        self.element = None
        self.selected = False
        self._drop_hover = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, self.fontMetrics().height() * 2 + 14)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.fontMetrics().height() * 2 + 14)

    def set_element(self, element, selected: bool) -> None:
        self.element = element
        self.selected = selected
        self.setToolTip(summary(element) if element is not None else "Empty track: drop an element here")
        self.update()

    def _label_width(self) -> int:
        return self.fontMetrics().horizontalAdvance("Track 4") + 20

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        label_w = self._label_width()
        painter.setPen(QColor(theme.TEXT_2))
        painter.setFont(theme.ui_font(12))
        painter.drawText(QRectF(8, 0, label_w - 8, rect.height()), Qt.AlignmentFlag.AlignVCenter, f"Track {self.index + 1}")
        bar = QRectF(label_w, 4, rect.width() - label_w - 6, rect.height() - 8)
        if self.element is None:
            pen = QPen(QColor(theme.ACCENT if self._drop_hover else theme.BORDER_STRONG), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(theme.ACCENT_SOFT) if self._drop_hover else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bar, 6, 6)
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, "Drop an element here")
        else:
            kind_colour = QColor(KIND_COLOURS.get(str(self.element.get("kind")), theme.TEXT_3))
            painter.setPen(QPen(QColor(theme.ACCENT if (self.selected or self._drop_hover) else theme.BORDER_STRONG), 2 if self.selected else 1))
            painter.setBrush(QColor(theme.CARD_HOVER if self.selected else theme.CARD))
            painter.drawRoundedRect(bar, 6, 6)
            stripe = QRectF(bar.left() + 1, bar.top() + 1, 6, bar.height() - 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(kind_colour)
            painter.drawRoundedRect(stripe, 3, 3)
            painter.setPen(QColor(theme.TEXT))
            font = theme.ui_font(13)
            painter.setFont(font)
            text_rect = bar.adjusted(16, 0, -10, 0)
            text = QFontMetrics(font).elidedText(summary(self.element), Qt.TextElideMode.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menu_requested.emit(self.index, event.globalPos())

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(ELEMENT_MIME) or event.mimeData().hasFormat(TRANSITION_MIME):
            self._drop_hover = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_hover = False
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._drop_hover = False
        self.update()
        data = event.mimeData()
        if data.hasFormat(ELEMENT_MIME):
            self.dropped_element.emit(self.index, bytes(data.data(ELEMENT_MIME)).decode("utf-8"))
        elif data.hasFormat(TRANSITION_MIME):
            self.dropped_transition.emit(bytes(data.data(TRANSITION_MIME)).decode("utf-8"))
        event.acceptProposedAction()


class TimelineView(QFrame):
    element_selected = Signal(str)  # element id ("" = none: the input's own settings)

    def __init__(self, document: ShowDocument, ndi_default: str = "Media Background Pane", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.document = document
        self.ndi_default = ndi_default
        self.input_id = ""
        self.selected_id = ""
        title = QLabel("TIMELINE")
        title.setObjectName("PanelTitle")
        self.infinite = QCheckBox("Infinite")
        self.infinite.setChecked(True)
        self.infinite.setEnabled(False)
        self.infinite.setToolTip("Infinite: each track holds one element that plays until the input is replaced.\nTimed sequences arrive in a later update.")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.infinite)
        self.rows: list[TrackRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)
        layout.addLayout(header)
        for index in reversed(range(TRACK_COUNT)):  # Track 4 at the top
            row = TrackRow(index)
            row.clicked.connect(self._row_clicked)
            row.dropped_element.connect(self.drop_element)
            row.dropped_transition.connect(self.set_transition_kind)
            row.menu_requested.connect(self._row_menu)
            layout.addWidget(row)
            self.rows.append(row)
        self.hint = QLabel("Track 4 is drawn on top. Click empty space in a track to see the input's own settings.")
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        document.changed.connect(lambda _show: self.refresh())

    def current_input(self) -> Input | None:
        return self.document.show.inputs.get(self.input_id) if self.input_id else None

    def set_input(self, input_id: str) -> None:
        self.input_id = input_id
        self.selected_id = ""
        self.refresh()

    def set_selected(self, element_id: str) -> None:
        self.selected_id = element_id or ""
        self.refresh()

    def refresh(self) -> None:
        inp = self.current_input()
        for row in self.rows:
            track = inp.tracks[row.index] if inp is not None and row.index < len(inp.tracks) else ()
            element = track[0] if track else None
            row.set_element(element, element is not None and element.get("id") == self.selected_id)
            row.setEnabled(inp is not None)

    def element_on(self, track: int):
        inp = self.current_input()
        if inp is None or track >= len(inp.tracks) or not inp.tracks[track]:
            return None
        return inp.tracks[track][0]

    def _row_clicked(self, track: int) -> None:
        element = self.element_on(track)
        self.selected_id = str(element.get("id")) if element is not None else ""
        self.refresh()
        self.element_selected.emit(self.selected_id)

    def drop_element(self, track: int, kind: str, confirm: bool = True) -> str:
        """Add an element of this kind to a track. Returns its id ("" if cancelled)."""
        inp = self.current_input()
        if inp is None or kind not in KIND_TITLES or kind == "video":
            return ""
        existing = self.element_on(track)
        if existing is not None and confirm:
            answer = QMessageBox.question(self, "Replace element", f"Track {track + 1} already has {summary(existing)}.\nReplace it with a new {KIND_TITLES[kind]} element?")
            if answer != QMessageBox.StandardButton.Yes:
                return ""
        show, element_id = add_element(self.document.show, self.input_id, track, kind, self.ndi_default)
        if self.document.apply(show, f"Add {KIND_TITLES[kind]} {element_id}"):
            self.selected_id = element_id
            self.refresh()
            self.element_selected.emit(element_id)
            return element_id
        return ""

    def add_to_first_empty(self, kind: str) -> str:
        inp = self.current_input()
        if inp is None:
            return ""
        for track in range(TRACK_COUNT):
            if self.element_on(track) is None:
                return self.drop_element(track, kind, confirm=False)
        QMessageBox.information(self, "All tracks are full", "All four tracks have an element. Drag it onto a track to replace one.")
        return ""

    def set_transition_kind(self, kind: str) -> None:
        inp = self.current_input()
        if inp is not None:
            names = dict(TRANSITION_CHOICES)
            self.document.apply(set_transition(self.document.show, self.input_id, kind=kind),
                                f"Transition {names.get(inp.transition.kind, inp.transition.kind)} → {names.get(kind, kind)}")

    def remove_selected(self) -> None:
        inp = self.current_input()
        found = find_element(inp, self.selected_id) if inp is not None and self.selected_id else None
        if found is not None:
            element_id, self.selected_id = self.selected_id, ""
            self.document.apply(remove_element(self.document.show, self.input_id, element_id), f"Remove {element_name(found[2])}")
            self.element_selected.emit("")

    def _row_menu(self, track: int, where: QPoint) -> None:
        element = self.element_on(track)
        if element is None:
            return
        element_id = str(element.get("id"))
        menu = QMenu(self)
        menu.addAction("Remove", lambda: self._remove(element_id))
        if track < TRACK_COUNT - 1:
            menu.addAction("Move up a track (more on top)", lambda: self._move(element_id, track + 1))
        if track > 0:
            menu.addAction("Move down a track", lambda: self._move(element_id, track - 1))
        menu.exec(where)

    def _remove(self, element_id: str) -> None:
        self.selected_id = element_id
        self.remove_selected()

    def _move(self, element_id: str, to_track: int) -> None:
        inp = self.current_input()
        found = find_element(inp, element_id) if inp is not None else None
        name = element_name(found[2]) if found else "element"
        self.document.apply(move_element(self.document.show, self.input_id, element_id, to_track), f"Move {name} to track {to_track + 1}")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_id:
            self.remove_selected()
            event.accept()
            return
        super().keyPressEvent(event)
