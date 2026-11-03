"""The Editor's timeline: four tracks (Track 4 is drawn over the others), a ruler and a playhead.

Infinite inputs (the default): each track holds one element, shown as a bar across the whole track; the ruler
and playhead are there for keyframes (which count from when the input starts).

Timed inputs: the input has a length, and each element is a bar from its start to its end.
- Drag a bar to move it (up or down to another track), drag its ends to trim it, right-click to split it at the
  playhead, give it an in or out transition (the wedges at its ends), or remove it. Where two bars on a track
  overlap, the later one crosses in over the earlier (the X).
- Drop an element from the palette onto a track to add it there (at that time); drop a transition on the start
  or end of a bar to set its in or out transition (anywhere else: the input's own transition).

Both: the selected element's keyframes are diamonds on its bar (drag to retime; right-click for smooth / linear /
hold or delete; click one to move the playhead there). Click or drag in the ruler to move the playhead. Ctrl +
wheel zooms, the wheel scrolls. Space plays / pauses, S splits, Delete removes the selected element.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QScrollBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..input_edit import (
    MIN_ELEMENT_LENGTH,
    add_element,
    crowded_tracks,
    element_keys,
    find_element,
    move_element,
    move_element_to,
    move_key,
    remove_element,
    remove_key,
    set_element_transition,
    set_key_interpolation,
    set_length,
    set_timed,
    set_transition,
    split_element,
    trim_element,
)
from ..model import TRACK_COUNT, Input, element_span
from ..schema import TRANSITION_CHOICES
from ..step_names import element_name
from . import icons, theme
from .document import ShowDocument
from .element_palette import ELEMENT_MIME, TRANSITION_MIME

KIND_COLOURS = {"paint": theme.ACCENT, "image": theme.OVERLAY, "video": theme.WARN, "ndi": theme.OK}
KIND_TITLES = {"paint": "Paint", "image": "Image", "video": "Video", "ndi": "NDI"}
RULER_H = 22
EDGE_PX = 6  # how close to a bar's end counts as grabbing that end
SNAP_PX = 7
DIAMOND = 5.5  # half size of a keyframe diamond
INFINITE_SPAN = 30.0  # seconds shown for an infinite input (more if it has later keyframes)
TICK_STEPS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
TRANSITION_LENGTHS = (0.5, 1.0, 2.0, 3.0, 5.0)


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


def format_time(seconds: float, fine: bool = True) -> str:
    """0:03.2 (or 1:02:03.2 past an hour)."""
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60.0)
    hours, minutes = divmod(int(minutes), 60)
    text = f"{secs:04.1f}" if fine else f"{int(secs):02d}"
    return f"{hours}:{minutes:02d}:{text}" if hours else f"{minutes}:{text}"


class TimelineCanvas(QWidget):
    """The ruler, the four tracks, the bars, the transition wedges, the keyframe diamonds and the playhead."""

    view_changed = Signal()  # zoom or scroll

    def __init__(self, timeline: TimelineView) -> None:
        super().__init__(timeline)
        self.timeline = timeline
        self.view_start = 0.0
        self.pps = 30.0  # pixels per second
        self._fitted_for: tuple | None = None
        self._drag: dict | None = None
        self._drop: tuple[int, float] | None = None  # (track, time) while something is dragged over
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------ geometry

    def row_height(self) -> float:
        return max(24.0, (self.height() - RULER_H) / TRACK_COUNT)

    def gutter(self) -> float:
        return float(self.fontMetrics().horizontalAdvance("Track 4") + 18)

    def content_width(self) -> float:
        return max(40.0, self.width() - self.gutter() - 8)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, RULER_H + TRACK_COUNT * (self.fontMetrics().height() + 18))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(260, RULER_H + TRACK_COUNT * (self.fontMetrics().height() + 10))

    def x_of(self, t: float) -> float:
        return self.gutter() + (t - self.view_start) * self.pps

    def t_of(self, x: float) -> float:
        return self.view_start + (x - self.gutter()) / self.pps

    def row_rect(self, track: int) -> QRectF:
        """Track 4 (index 3) is the top row."""
        h = self.row_height()
        return QRectF(0, RULER_H + (TRACK_COUNT - 1 - track) * h, self.width(), h)

    def track_at(self, y: float) -> int | None:
        if y < RULER_H:
            return None
        row = int((y - RULER_H) // self.row_height())
        return TRACK_COUNT - 1 - row if 0 <= row < TRACK_COUNT else None

    def span(self) -> float:
        """The seconds the timeline covers: the input's length, or (infinite) 30 s or past the last keyframe."""
        inp = self.timeline.current_input()
        if inp is None:
            return INFINITE_SPAN
        if not inp.infinite:
            return max(inp.length, MIN_ELEMENT_LENGTH)
        last = 0.0
        for track in inp.tracks:
            for element in track:
                for keys in element_keys(element).values():
                    if keys:
                        last = max(last, keys[-1][0])
        return max(INFINITE_SPAN, last + 5.0)

    def fit(self, force: bool = False) -> None:
        """Zoom so the whole input fits (when the input or its length changes, or asked)."""
        inp = self.timeline.current_input()
        key = (inp.id if inp else "", round(self.span(), 3), self.content_width())
        if not force and key == self._fitted_for:
            return
        self._fitted_for = key
        self.pps = self.content_width() / self.span()
        self.view_start = 0.0
        self.view_changed.emit()
        self.update()

    def zoom(self, factor: float, around: float | None = None) -> None:
        around = self.timeline.playhead if around is None else around
        x = self.x_of(around)
        fitted = self.content_width() / self.span()
        self.pps = min(2000.0, max(fitted * 0.5, self.pps * factor))
        self.view_start = max(0.0, around - (x - self.gutter()) / self.pps)
        self.view_changed.emit()
        self.update()

    def visible_span(self) -> float:
        return self.content_width() / self.pps

    def bar_rect(self, track: int, element, inp: Input) -> QRectF:
        row = self.row_rect(track)
        top, height = row.top() + 3, row.height() - 6
        if inp.infinite:
            return QRectF(self.gutter(), top, self.content_width(), height)
        start, end = element_span(element, inp)
        left = self.x_of(start)
        return QRectF(left, top, max(4.0, self.x_of(end) - left), height)

    def bars(self) -> list[tuple[int, object, QRectF]]:
        inp = self.timeline.current_input()
        if inp is None:
            return []
        found = []
        for track in range(min(TRACK_COUNT, len(inp.tracks))):
            for element in (inp.tracks[track][:1] if inp.infinite else inp.tracks[track]):
                found.append((track, element, self.bar_rect(track, element, inp)))
        return found

    def diamonds(self) -> list[tuple[float, list[str], QPointF]]:
        """The selected element's keyframes: (time in the element, the settings keyed there, where)."""
        inp = self.timeline.current_input()
        found = find_element(inp, self.timeline.selected_id) if inp is not None and self.timeline.selected_id else None
        if found is None:
            return []
        track, _position, element = found
        start = element_span(element, inp)[0] if not inp.infinite else 0.0
        by_time: dict[float, list[str]] = {}
        for where, keys in element_keys(element).items():
            for kt, _value, _interp in keys:
                slot = next((t for t in by_time if abs(t - kt) < 1e-3), kt)
                by_time.setdefault(slot, []).append(where)
        rect = self.bar_rect(track, element, inp)
        y = rect.bottom() - DIAMOND - 2  # along the bottom of the bar, under its name
        return [(kt, wheres, QPointF(self.x_of(start + kt), y)) for kt, wheres in sorted(by_time.items())]

    # ------------------------------------------------------------ painting

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inp = self.timeline.current_input()
        font = theme.ui_font(12)
        painter.setFont(font)
        fm = QFontMetrics(font)
        gutter = self.gutter()
        # rows
        for track in range(TRACK_COUNT):
            row = self.row_rect(track)
            painter.fillRect(row, QColor(theme.PANEL if track % 2 else theme.WINDOW))
            painter.setPen(QColor(theme.TEXT_2 if inp is not None else theme.TEXT_3))
            painter.drawText(QRectF(8, row.top(), gutter - 10, row.height()), Qt.AlignmentFlag.AlignVCenter, f"Track {track + 1}")
            if self._drop is not None and self._drop[0] == track:
                painter.fillRect(QRectF(gutter, row.top() + 2, self.content_width(), row.height() - 4), QColor(theme.ACCENT_SOFT))
        if inp is not None:
            if not inp.infinite:  # after the end: shaded
                end_x = self.x_of(inp.length)
                if end_x < self.width():
                    painter.fillRect(QRectF(end_x, RULER_H, self.width() - end_x, self.height() - RULER_H), QColor(0, 0, 0, 90))
            painter.save()
            painter.setClipRect(QRectF(gutter, RULER_H, self.width() - gutter, self.height() - RULER_H))
            self._paint_bars(painter, inp, fm)
            self._paint_diamonds(painter)
            painter.restore()
            if not any(inp.tracks):
                painter.setPen(QColor(theme.TEXT_3))
                painter.drawText(QRectF(gutter, RULER_H, self.content_width(), self.height() - RULER_H),
                                 Qt.AlignmentFlag.AlignCenter, "Drop an element here")
        self._paint_ruler(painter, fm)
        self._paint_playhead(painter)
        painter.end()

    def _paint_bars(self, painter: QPainter, inp: Input, fm: QFontMetrics) -> None:
        selected = self.timeline.selected_id
        for track, element, rect in self.bars():
            is_selected = element.get("id") == selected
            kind_colour = QColor(KIND_COLOURS.get(str(element.get("kind")), theme.TEXT_3))
            painter.setPen(QPen(QColor(theme.ACCENT if is_selected else theme.BORDER_STRONG), 2 if is_selected else 1))
            painter.setBrush(QColor(theme.CARD_HOVER if is_selected else theme.CARD))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(kind_colour)
            painter.drawRoundedRect(QRectF(rect.left() + 1, rect.top() + 1, min(5.0, rect.width() - 2), rect.height() - 2), 2.5, 2.5)
            if not inp.infinite:
                self._paint_wedges(painter, inp, element, rect)
            text_rect = rect.adjusted(12, 0, -8, 0)
            keyed = is_selected and bool(element_keys(element))
            if keyed:  # its keyframes go along the bottom: the name moves up
                text_rect = QRectF(text_rect.left(), rect.top() + 1, text_rect.width(), fm.height() + 2)
            if text_rect.width() > 24:
                painter.setPen(QColor(theme.TEXT))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                                 fm.elidedText(summary(element), Qt.TextElideMode.ElideRight, int(text_rect.width())))
        if not inp.infinite:  # where two bars on a track overlap: the later crosses in (an X)
            painter.setPen(QPen(QColor(theme.TEXT_2), 1.2))
            for index in range(min(TRACK_COUNT, len(inp.tracks))):
                spans = sorted((element_span(e, inp), e) for e in inp.tracks[index]) if inp.tracks[index] else []
                for (a, _ea), (b, _eb) in zip(spans, spans[1:]):
                    if b[0] < a[1]:
                        row = self.row_rect(index)
                        box = QRectF(self.x_of(b[0]), row.top() + 4, self.x_of(min(a[1], b[1])) - self.x_of(b[0]), row.height() - 8)
                        painter.drawLine(box.topLeft(), box.bottomRight())
                        painter.drawLine(box.bottomLeft(), box.topRight())

    def _paint_wedges(self, painter: QPainter, inp: Input, element, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 46))
        for which in ("in", "out"):
            spec = element.get(which)
            if not hasattr(spec, "get") or not spec.get("duration"):
                continue
            width = min(rect.width(), float(spec.get("duration", 0)) * self.pps)
            if which == "in":
                points = [rect.bottomLeft(), QPointF(rect.left() + width, rect.top()), QPointF(rect.left() + width, rect.bottom())]
            else:
                points = [QPointF(rect.right() - width, rect.top()), rect.bottomRight(), QPointF(rect.right() - width, rect.bottom())]
            painter.drawPolygon(QPolygonF(points))

    def _paint_diamonds(self, painter: QPainter) -> None:
        playhead = self.timeline.playhead
        inp = self.timeline.current_input()
        found = find_element(inp, self.timeline.selected_id) if inp is not None and self.timeline.selected_id else None
        start = element_span(found[2], inp)[0] if found is not None and not inp.infinite else 0.0
        for kt, _wheres, point in self.diamonds():
            on = abs(start + kt - playhead) < 1e-3
            path = QPainterPath()
            path.moveTo(point.x(), point.y() - DIAMOND)
            path.lineTo(point.x() + DIAMOND, point.y())
            path.lineTo(point.x(), point.y() + DIAMOND)
            path.lineTo(point.x() - DIAMOND, point.y())
            path.closeSubpath()
            painter.setPen(QPen(QColor(theme.ACCENT_TEXT if on else theme.ACCENT), 1.2))
            painter.setBrush(QColor(theme.ACCENT) if on else QColor(theme.CARD))
            painter.drawPath(path)

    def _paint_ruler(self, painter: QPainter, fm: QFontMetrics) -> None:
        gutter = self.gutter()
        painter.fillRect(QRectF(0, 0, self.width(), RULER_H), QColor(theme.PANEL))
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(QPointF(0, RULER_H - 0.5), QPointF(self.width(), RULER_H - 0.5))
        label_w = fm.horizontalAdvance("0:00.0") + 14
        step = next((s for s in TICK_STEPS if s * self.pps >= label_w), TICK_STEPS[-1])
        first = math.floor(self.view_start / step) * step
        t = first
        end = self.t_of(self.width())
        while t <= end:
            x = self.x_of(t)
            if x >= gutter - 0.5:
                painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
                painter.drawLine(QPointF(x, RULER_H - 7), QPointF(x, RULER_H - 1))
                label = format_time(t, fine=step < 1)
                if x + 3 + fm.horizontalAdvance(label) <= self.width():
                    painter.setPen(QColor(theme.TEXT_2))
                    painter.drawText(QRectF(x + 3, 0, label_w, RULER_H - 4), Qt.AlignmentFlag.AlignVCenter, label)
            t += step

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self.x_of(self.timeline.playhead)
        if x < self.gutter() - 1 or x > self.width():
            return
        colour = QColor(theme.LIVE)
        painter.setPen(QPen(colour, 1.5))
        painter.drawLine(QPointF(x, 2), QPointF(x, self.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF([QPointF(x - 5, 2), QPointF(x + 5, 2), QPointF(x, 9)]))

    # ------------------------------------------------------------ hit testing

    def hit(self, pos: QPointF) -> dict:
        """What's under the pointer: the ruler, a keyframe, a bar's end or body, or a track."""
        if pos.y() < RULER_H:
            return {"what": "ruler"}
        inp = self.timeline.current_input()
        for kt, wheres, point in self.diamonds():
            if abs(pos.x() - point.x()) <= DIAMOND + 2 and abs(pos.y() - point.y()) <= DIAMOND + 2:
                return {"what": "key", "time": kt, "wheres": wheres}
        for track, element, rect in reversed(self.bars()):
            if rect.adjusted(-2, 0, 2, 0).contains(pos):
                if inp is not None and not inp.infinite:
                    if abs(pos.x() - rect.left()) <= EDGE_PX:
                        return {"what": "start", "track": track, "element": element}
                    if abs(pos.x() - rect.right()) <= EDGE_PX:
                        return {"what": "end", "track": track, "element": element}
                return {"what": "bar", "track": track, "element": element}
        track = self.track_at(pos.y())
        return {"what": "track", "track": track} if track is not None else {"what": "none"}

    def snap(self, t: float, skip_id: str = "") -> float:
        """t moved onto something nearby: the playhead, the start, the end, or another bar's ends."""
        inp = self.timeline.current_input()
        targets = [0.0, self.timeline.playhead]
        if inp is not None and not inp.infinite:
            targets.append(inp.length)
            for track in inp.tracks:
                for element in track:
                    if element.get("id") != skip_id:
                        targets.extend(element_span(element, inp))
        best = min(targets, key=lambda target: abs(target - t))
        return best if abs(best - t) * self.pps <= SNAP_PX else round(t, 2)

    # ------------------------------------------------------------ the mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self.hit(pos)
        inp = self.timeline.current_input()
        what = hit["what"]
        if what == "ruler":
            self._drag = {"kind": "scrub"}
            self.timeline.seek(max(0.0, self.t_of(pos.x())))
        elif what == "key":
            self._drag = {"kind": "key", "time": hit["time"], "wheres": hit["wheres"], "x": pos.x(), "moved": False,
                          "base": self.timeline.document.show}  # each step works from the show as it was at the press
            self.timeline.document.begin_drag()
        elif what in ("bar", "start", "end"):
            element = hit["element"]
            self.timeline.select(str(element.get("id")))
            if inp is not None and not inp.infinite:
                start, end = element_span(element, inp)
                kind = {"bar": "move", "start": "trim-start", "end": "trim-end"}[what]
                self._drag = {"kind": kind, "id": str(element.get("id")), "track": hit["track"], "start": start, "end": end,
                              "grab": self.t_of(pos.x()) - start, "moved": False, "base": self.timeline.document.show}
                self.timeline.document.begin_drag()
        elif what == "track":
            self.timeline.select("")
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        drag = self._drag
        if drag is None:
            hit = self.hit(pos)
            cursor = {"start": Qt.CursorShape.SizeHorCursor, "end": Qt.CursorShape.SizeHorCursor,
                      "key": Qt.CursorShape.PointingHandCursor, "ruler": Qt.CursorShape.IBeamCursor}.get(hit["what"])
            if hit["what"] == "bar":
                inp = self.timeline.current_input()
                cursor = Qt.CursorShape.SizeAllCursor if inp is not None and not inp.infinite else Qt.CursorShape.PointingHandCursor
            self.setCursor(cursor or Qt.CursorShape.ArrowCursor)
            self.setToolTip(self._tip(hit))
            return
        t = self.t_of(pos.x())
        timeline = self.timeline
        show = drag.get("base", timeline.document.show)
        if drag["kind"] == "scrub":
            timeline.seek(max(0.0, t))
            return
        if drag["kind"] == "key":
            if abs(pos.x() - drag["x"]) < 3 and not drag["moved"]:
                return
            drag["moved"] = True
            inp = show.inputs.get(timeline.input_id)
            found = find_element(inp, timeline.selected_id) if inp is not None else None
            if found is None:
                return
            start = element_span(found[2], inp)[0] if not inp.infinite else 0.0
            new_time = max(0.0, self.snap(t) - start)
            for where in drag["wheres"]:
                show = move_key(show, timeline.input_id, timeline.selected_id, where, drag["time"], new_time)
            drag["preview"] = new_time
            timeline.document.preview(show)
            return
        drag["moved"] = True
        element_id = drag["id"]
        if drag["kind"] == "move":
            length = drag["end"] - drag["start"]
            raw = max(0.0, t - drag["grab"])
            new_start = self.snap(raw, element_id)
            if new_start == round(raw, 2):  # the start didn't snap to anything: maybe the end does
                end = self.snap(raw + length, element_id)
                if end != round(raw + length, 2):
                    new_start = max(0.0, end - length)
            show = move_element_to(show, timeline.input_id, element_id, new_start)
            track = self.track_at(pos.y())
            if track is not None and track != drag["track"]:
                show = move_element(show, timeline.input_id, element_id, track)
        elif drag["kind"] == "trim-start":
            show = trim_element(show, timeline.input_id, element_id, start=self.snap(t, element_id))
        else:
            show = trim_element(show, timeline.input_id, element_id, end=self.snap(t, element_id))
        timeline.document.preview(show)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        drag, self._drag = self._drag, None
        if drag is None or event.button() != Qt.MouseButton.LeftButton:
            return
        timeline = self.timeline
        if drag["kind"] == "key":
            if drag["moved"]:
                timeline.document.end_drag(f"{timeline.element_label()}: move keyframe to {drag.get('preview', drag['time']):.2f} s")
            else:
                timeline.document.end_drag("")
                inp = timeline.current_input()
                found = find_element(inp, timeline.selected_id) if inp is not None else None
                start = element_span(found[2], inp)[0] if found is not None and not inp.infinite else 0.0
                timeline.seek(start + drag["time"])
        elif drag["kind"] in ("move", "trim-start", "trim-end"):
            if drag["moved"]:
                inp = timeline.current_input()
                found = find_element(inp, drag["id"]) if inp is not None else None
                name = element_name(found[2]) if found is not None else drag["id"]
                if drag["kind"] == "move" and found is not None:
                    start = element_span(found[2], inp)[0]
                    text = f"Move {name} to {start:.2f} s" + (f" (track {found[0] + 1})" if found[0] != drag["track"] else "")
                else:
                    text = f"Trim {name}"
                timeline.document.end_drag(text)
            else:
                timeline.document.end_drag("")
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.hit(event.position())["what"] == "ruler":
            self.fit(force=True)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.25 if delta > 0 else 0.8, self.t_of(event.position().x()))
        else:
            self.view_start = max(0.0, self.view_start - delta / 120.0 * self.visible_span() * 0.15)
            self.view_changed.emit()
            self.update()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        hit = self.hit(QPointF(event.pos()))
        timeline = self.timeline
        if hit["what"] == "key":
            menu = QMenu(self)
            for interp, label in (("smooth", "Smooth to the next key"), ("linear", "Straight to the next key"), ("hold", "Hold until the next key")):
                menu.addAction(label, lambda i=interp: timeline.set_key_interpolation(hit["time"], hit["wheres"], i))
            menu.addSeparator()
            menu.addAction("Delete this keyframe", lambda: timeline.remove_keys(hit["time"], hit["wheres"]))
            menu.exec(event.globalPos())
        elif hit["what"] in ("bar", "start", "end"):
            element = hit["element"]
            timeline.select(str(element.get("id")))
            timeline.element_menu(element, hit["track"], event.globalPos())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.timeline.selected_id:
            self.timeline.remove_selected()
        elif key == Qt.Key.Key_Space:
            self.timeline.play_toggled.emit(not self.timeline.playing)
        elif key == Qt.Key.Key_S and self.timeline.selected_id:
            self.timeline.split_selected()
        elif key == Qt.Key.Key_Home:
            self.timeline.seek(0.0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _tip(self, hit: dict) -> str:
        what = hit["what"]
        if what == "ruler":
            return "Click or drag to move the playhead · double-click: fit the whole input · Ctrl + wheel: zoom"
        if what == "key":
            return f"Keyframe ({', '.join(hit['wheres'])}): drag to move it, click to go there, right-click for more"
        if what in ("start", "end"):
            return "Drag to trim"
        if what == "bar":
            inp = self.timeline.current_input()
            tip = summary(hit["element"])
            if inp is not None and not inp.infinite:
                start, end = element_span(hit["element"], inp)
                tip += f"\n{format_time(start)} – {format_time(end)} · drag to move · right-click for more"
            return tip
        if what == "track":
            return "Drop an element here"
        return ""

    # ------------------------------------------------------------ dropping from the palette

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(ELEMENT_MIME) or event.mimeData().hasFormat(TRANSITION_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        track = self.track_at(pos.y())
        self._drop = (track, max(0.0, self.t_of(pos.x()))) if track is not None else None
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:  # noqa: N802
        self._drop = None
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._drop = None
        self.update()
        data = event.mimeData()
        pos = event.position()
        track = self.track_at(pos.y())
        if data.hasFormat(ELEMENT_MIME) and track is not None:
            kind = bytes(data.data(ELEMENT_MIME)).decode("utf-8")
            self.timeline.drop_element(track, kind, start=max(0.0, self.snap(self.t_of(pos.x()))))
        elif data.hasFormat(TRANSITION_MIME):
            kind = bytes(data.data(TRANSITION_MIME)).decode("utf-8")
            hit = self.hit(pos)
            inp = self.timeline.current_input()
            if hit["what"] in ("bar", "start", "end") and inp is not None and not inp.infinite:
                rect = self.bar_rect(hit["track"], hit["element"], inp)
                which = "in" if pos.x() < rect.center().x() else "out"
                self.timeline.set_element_transition(str(hit["element"].get("id")), which, kind)
            else:
                self.timeline.set_transition_kind(kind)
        event.acceptProposedAction()


class KeepDialog(QDialog):
    """Going infinite: each track can hold only one element, so pick which to keep."""

    def __init__(self, crowded: dict[int, list], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Make it infinite")
        layout = QVBoxLayout(self)
        intro = QLabel("An infinite input has one element on each track. Which should stay?")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.choices: dict[int, QComboBox] = {}
        for track, elements in sorted(crowded.items()):
            combo = QComboBox()
            for element in elements:
                combo.addItem(summary(element), str(element.get("id")))
            form.addRow(f"Track {track + 1}", combo)
            self.choices[track] = combo
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keep(self) -> dict[int, str]:
        return {track: str(combo.currentData()) for track, combo in self.choices.items()}


class TimelineView(QFrame):
    element_selected = Signal(str)  # element id ("" = none: the input's own settings)
    seek_requested = Signal(float)  # move the playhead (seconds of the input)
    play_toggled = Signal(bool)  # play (True) or pause

    def __init__(self, document: ShowDocument, ndi_default: str = "Media Background Pane", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.document = document
        self.ndi_default = ndi_default
        self.input_id = ""
        self.selected_id = ""
        self.playhead = 0.0
        self.playing = False
        self.ask_keep: Callable[[dict[int, list]], dict[int, str] | None] = self._ask_keep
        title = QLabel("TIMELINE")
        title.setObjectName("PanelTitle")
        self.play_button = QToolButton()
        self.play_button.setObjectName("HeaderIcon")
        self.play_button.setToolTip("Play / pause (Space)")
        self.play_button.clicked.connect(lambda: self.play_toggled.emit(not self.playing))
        self.start_button = QToolButton()
        self.start_button.setObjectName("HeaderIcon")
        self.start_button.setIcon(icons.line_icon(icons.draw_to_start, theme.TEXT_2, theme.ACCENT))
        self.start_button.setToolTip("Back to the start (Home)")
        self.start_button.clicked.connect(lambda: self.seek(0.0))
        self.time_label = QLabel("0:00.0")
        self.time_label.setObjectName("TimeDisplay")
        self.time_label.setMinimumWidth(self.time_label.fontMetrics().horizontalAdvance("0:00.0 / 0:00.0") + 6)
        self.length = QDoubleSpinBox()
        self.length.setRange(MIN_ELEMENT_LENGTH, 3600.0)
        self.length.setDecimals(1)
        self.length.setSuffix(" s")
        self.length.setKeyboardTracking(False)
        self.length.setToolTip("How long this timed input lasts")
        self.length.valueChanged.connect(self._length_changed)
        self.infinite = QCheckBox("Infinite")
        self.infinite.setToolTip("Infinite: each track holds one element that plays until the input is replaced.\n"
                                 "Untick it for a timed sequence, with a length and elements placed in time.")
        self.infinite.clicked.connect(lambda checked: self.set_timed(not checked))
        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(self.start_button)
        header.addWidget(self.play_button)
        header.addWidget(self.time_label)
        header.addStretch(1)
        header.addWidget(self.length)
        header.addWidget(self.infinite)
        self.canvas = TimelineCanvas(self)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        self.canvas.view_changed.connect(self._sync_scroll)
        self.hint = QLabel("Track 4 is drawn on top. Click empty space in a track to see the input's own settings.")
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.scroll)
        layout.addWidget(self.hint)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        document.changed.connect(lambda _show: self.refresh())
        self._sync_header()

    # ------------------------------------------------------------ state

    def current_input(self) -> Input | None:
        return self.document.show.inputs.get(self.input_id) if self.input_id else None

    def set_input(self, input_id: str) -> None:
        self.input_id = input_id
        self.selected_id = ""
        self.canvas.fit(force=True)
        self.refresh()

    def set_selected(self, element_id: str) -> None:
        self.selected_id = element_id or ""
        self.refresh()

    def select(self, element_id: str) -> None:
        """A click on a bar (or empty space): tell the Editor."""
        if element_id != self.selected_id:
            self.selected_id = element_id
            self.refresh()
            self.element_selected.emit(element_id)

    def set_playhead(self, t: float, playing: bool) -> None:
        """The Editor's playhead (it owns the clock); keeps it in view while playing."""
        changed = abs(t - self.playhead) > 1e-6 or playing != self.playing
        self.playhead, self.playing = t, playing
        if playing and self.canvas.x_of(t) > self.canvas.width() - 10:
            self.canvas.view_start = max(0.0, t - self.canvas.visible_span() * 0.1)
            self._sync_scroll()
        if changed:
            self._sync_header()
            self.canvas.update()

    def seek(self, t: float) -> None:
        inp = self.current_input()
        if inp is not None and not inp.infinite:
            t = min(t, inp.length)
        self.seek_requested.emit(max(0.0, t))

    def refresh(self) -> None:
        inp = self.current_input()
        if inp is not None and self.selected_id and find_element(inp, self.selected_id) is None:
            self.selected_id = ""
        self.canvas.setEnabled(inp is not None)
        self.canvas.fit()
        self._sync_header()
        self.canvas.update()

    def _sync_header(self) -> None:
        inp = self.current_input()
        timed = inp is not None and not inp.infinite
        blocked = self.infinite.blockSignals(True)
        self.infinite.setChecked(inp is None or inp.infinite)
        self.infinite.blockSignals(blocked)
        self.infinite.setEnabled(inp is not None)
        self.length.setVisible(timed)
        if timed and abs(self.length.value() - inp.length) > 1e-6:
            blocked = self.length.blockSignals(True)
            self.length.setValue(inp.length)
            self.length.blockSignals(blocked)
        self.play_button.setIcon(icons.line_icon(icons.draw_pause if self.playing else icons.draw_play, theme.TEXT_2, theme.ACCENT))
        self.play_button.setEnabled(inp is not None)
        self.start_button.setEnabled(inp is not None)
        total = f" / {format_time(inp.length)}" if timed else ""
        self.time_label.setText(format_time(self.playhead) + total)

    def _sync_scroll(self) -> None:
        span = self.canvas.span()
        visible = self.canvas.visible_span()
        blocked = self.scroll.blockSignals(True)
        self.scroll.setRange(0, max(0, int(round((span - visible) * 100))))
        self.scroll.setPageStep(max(1, int(visible * 100)))
        self.scroll.setValue(int(round(self.canvas.view_start * 100)))
        self.scroll.blockSignals(blocked)
        self.scroll.setVisible(span > visible + 1e-6)

    def _scrolled(self, value: int) -> None:
        self.canvas.view_start = value / 100.0
        self.canvas.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.canvas.fit()

    # ------------------------------------------------------------ editing

    def element_label(self) -> str:
        inp = self.current_input()
        found = find_element(inp, self.selected_id) if inp is not None and self.selected_id else None
        return element_name(found[2]) if found is not None else "element"

    def element_on(self, track: int):
        """The element on a track (infinite inputs; timed: the first)."""
        inp = self.current_input()
        if inp is None or track >= len(inp.tracks) or not inp.tracks[track]:
            return None
        return inp.tracks[track][0]

    def drop_element(self, track: int, kind: str, confirm: bool = True, start: float | None = None) -> str:
        """Add an element of this kind to a track (infinite: replacing what's there, after asking; timed: at
        `start`, or the playhead). Returns its id ("" if cancelled)."""
        inp = self.current_input()
        if inp is None or kind not in KIND_TITLES:
            return ""
        existing = self.element_on(track)
        if inp.infinite and existing is not None and confirm:
            answer = QMessageBox.question(self, "Replace element", f"Track {track + 1} already has {summary(existing)}.\nReplace it with a new {KIND_TITLES[kind]} element?")
            if answer != QMessageBox.StandardButton.Yes:
                return ""
        at = self.playhead if start is None else start
        show, element_id = add_element(self.document.show, self.input_id, track, kind, self.ndi_default, start=at)
        if self.document.apply(show, f"Add {KIND_TITLES[kind]} {element_id}"):
            self.select(element_id)
            return element_id
        return ""

    def add_to_first_empty(self, kind: str) -> str:
        """The palette's click: infinite inputs, the first empty track; timed, the first track free at the playhead."""
        inp = self.current_input()
        if inp is None:
            return ""
        for track in range(TRACK_COUNT):
            elements = inp.tracks[track] if track < len(inp.tracks) else ()
            busy = bool(elements) if inp.infinite else any(element_span(e, inp)[0] <= self.playhead < element_span(e, inp)[1] for e in elements)
            if not busy:
                return self.drop_element(track, kind, confirm=False)
        QMessageBox.information(self, "All tracks are full", "All four tracks are in use here. Drag the element onto a track instead.")
        return ""

    def set_transition_kind(self, kind: str) -> None:
        """The input's own transition (how it appears on Base or in an overlay)."""
        inp = self.current_input()
        if inp is not None:
            names = dict(TRANSITION_CHOICES)
            self.document.apply(set_transition(self.document.show, self.input_id, kind=kind),
                                f"Transition {names.get(inp.transition.kind, inp.transition.kind)} → {names.get(kind, kind)}")

    def set_element_transition(self, element_id: str, which: str, kind: str | None = None, duration: float | None = None) -> None:
        inp = self.current_input()
        found = find_element(inp, element_id) if inp is not None else None
        if found is None:
            return
        names = dict(TRANSITION_CHOICES)
        what = "none" if kind == "cut" else f"{names.get(kind, kind)}" if kind else f"{duration:g} s"
        self.document.apply(set_element_transition(self.document.show, self.input_id, element_id, which, kind, duration),
                            f"{element_name(found[2])}: {which} transition {what}")

    def remove_selected(self) -> None:
        inp = self.current_input()
        found = find_element(inp, self.selected_id) if inp is not None and self.selected_id else None
        if found is not None:
            element_id = self.selected_id
            self.select("")
            self.document.apply(remove_element(self.document.show, self.input_id, element_id), f"Remove {element_name(found[2])}")

    def split_selected(self) -> str:
        """Cut the selected element in two at the playhead. Returns the second part's id ("" if it can't)."""
        inp = self.current_input()
        found = find_element(inp, self.selected_id) if inp is not None and self.selected_id else None
        if found is None or inp.infinite:
            return ""
        show, second = split_element(self.document.show, self.input_id, self.selected_id, self.playhead)
        if second and self.document.apply(show, f"Split {element_name(found[2])} at {self.playhead:.2f} s"):
            return second
        return ""

    def remove_keys(self, t: float, wheres: list[str]) -> None:
        show = self.document.show
        for where in wheres:
            show = remove_key(show, self.input_id, self.selected_id, where, t)
        self.document.apply(show, f"{self.element_label()}: delete keyframe at {t:.2f} s")

    def set_key_interpolation(self, t: float, wheres: list[str], interp: str) -> None:
        show = self.document.show
        for where in wheres:
            show = set_key_interpolation(show, self.input_id, self.selected_id, where, t, interp)
        self.document.apply(show, f"{self.element_label()}: keyframe at {t:.2f} s {interp}")

    def set_timed(self, timed: bool, keep: dict[int, str] | None = None) -> bool:
        """Untick Infinite for a timed input (or tick it to go back, choosing what stays on crowded tracks)."""
        inp = self.current_input()
        if inp is None or timed == (not inp.infinite):
            self._sync_header()
            return False
        if not timed and keep is None:
            crowded = crowded_tracks(inp)
            if crowded:
                keep = self.ask_keep(crowded)
                if keep is None:
                    self._sync_header()
                    return False
        done = self.document.apply(set_timed(self.document.show, self.input_id, timed, keep),
                                   f"{inp.name}: {'timed' if timed else 'infinite'}")
        self.canvas.fit(force=True)
        self._sync_header()
        return done

    def _ask_keep(self, crowded: dict[int, list]) -> dict[int, str] | None:
        dialog = KeepDialog(crowded, self)
        return dialog.keep() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _length_changed(self, value: float) -> None:
        inp = self.current_input()
        if inp is not None and not inp.infinite and abs(value - inp.length) > 1e-6:
            self.document.apply(set_length(self.document.show, self.input_id, value), f"{inp.name}: length {value:g} s",
                                merge_key=f"length-{self.input_id}")

    def element_menu(self, element, track: int, where) -> None:
        inp = self.current_input()
        if inp is None:
            return
        element_id = str(element.get("id"))
        menu = QMenu(self)
        if not inp.infinite:
            split = menu.addAction(f"Split at the playhead ({format_time(self.playhead)})", self.split_selected)
            start, end = element_span(element, inp)
            split.setEnabled(start + MIN_ELEMENT_LENGTH <= self.playhead <= end - MIN_ELEMENT_LENGTH)
            for which, title in (("in", "In transition"), ("out", "Out transition")):
                sub = menu.addMenu(title)
                current = element.get(which) if hasattr(element.get(which), "get") else None
                sub.addAction("None", lambda w=which: self.set_element_transition(element_id, w, "cut"))
                for kind, label in TRANSITION_CHOICES:
                    if kind == "cut":
                        continue
                    action = sub.addAction(label, lambda w=which, k=kind: self.set_element_transition(element_id, w, k))
                    action.setCheckable(True)
                    action.setChecked(current is not None and current.get("kind") == kind)
                if current is not None:
                    sub.addSeparator()
                    for seconds in TRANSITION_LENGTHS:
                        action = sub.addAction(f"{seconds:g} s", lambda w=which, s=seconds: self.set_element_transition(element_id, w, duration=s))
                        action.setCheckable(True)
                        action.setChecked(abs(float(current.get("duration", 0)) - seconds) < 1e-6)
                    sub.addAction("Other length…", lambda w=which: self._other_length(element_id, w))
            menu.addSeparator()
        if track < TRACK_COUNT - 1:
            menu.addAction("Move up a track (more on top)", lambda: self._move(element_id, track + 1))
        if track > 0:
            menu.addAction("Move down a track", lambda: self._move(element_id, track - 1))
        menu.addAction("Remove", lambda: self._remove(element_id))
        menu.exec(where)

    def _other_length(self, element_id: str, which: str) -> None:
        seconds, ok = QInputDialog.getDouble(self, "Transition length", "Seconds:", 1.0, 0.1, 60.0, 1)
        if ok:
            self.set_element_transition(element_id, which, duration=seconds)

    def _remove(self, element_id: str) -> None:
        self.selected_id = element_id
        self.remove_selected()

    def _move(self, element_id: str, to_track: int) -> None:
        inp = self.current_input()
        found = find_element(inp, element_id) if inp is not None else None
        name = element_name(found[2]) if found else "element"
        self.document.apply(move_element(self.document.show, self.input_id, element_id, to_track), f"Move {name} to track {to_track + 1}")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.canvas.keyPressEvent(event)
