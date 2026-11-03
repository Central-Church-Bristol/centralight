"""The Editor's two viewports.

- Selected: the selected element on its own, over a checkerboard (so see-through parts
  show). Layer / Mask: the mask view tints whatever the element's mask hides in red.
  For a Paint element its shapes have handles: drag the middle to move, a corner to
  resize (Shift keeps the proportions), the round knob to rotate (Shift snaps to 15°).
  The Rectangle and Ellipse tools draw new shapes (Shift: square / circle).
- Result: the whole input, with a strip underneath showing each light's colour,
  worked out exactly as the engine does it.

Both have a Lights toggle that shows or hides the dots, rings and names.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from ..model import PATCH_RADIUS, Show
from ..output import RGB, to_byte
from ..params import evaluate, expression_of
from ..step_names import shape_name
from . import theme
from .light_view import rgb_colour

HANDLE = 6.0  # handle half-size in pixels
ROTATE_GAP = 22.0  # how far the rotation knob sits above the shape, in pixels


def rgb_image(data: np.ndarray) -> QImage:
    h, w = data.shape[:2]
    return QImage(data.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def rgba_image(data: np.ndarray) -> QImage:
    h, w = data.shape[:2]
    return QImage(data.data, w, h, w * 4, QImage.Format.Format_RGBA8888_Premultiplied).copy()


def mask_tint(keep: np.ndarray) -> QImage:
    """Red over whatever a mask hides (keep 0 = hidden)."""
    h, w = keep.shape
    tint = np.zeros((h, w, 4), dtype=np.uint8)
    alpha = ((255 - keep.astype(np.uint16)) * 150 // 255).astype(np.uint8)
    tint[:, :, 0] = alpha  # premultiplied: pure red at that strength
    tint[:, :, 3] = alpha
    return QImage(tint.data, w, h, w * 4, QImage.Format.Format_RGBA8888_Premultiplied).copy()


def _checkerboard() -> QPixmap:
    pix = QPixmap(16, 16)
    pix.fill(QColor("#1A2130"))
    painter = QPainter(pix)
    painter.fillRect(0, 0, 8, 8, QColor("#232C3F"))
    painter.fillRect(8, 8, 8, 8, QColor("#232C3F"))
    painter.end()
    return pix


class ViewportCanvas(QWidget):
    """A 16:9 picture, letterboxed, with the lights drawn over it if wanted."""

    light_clicked = Signal(str)

    def __init__(self, show: Show, checkerboard: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show = show
        self._image: QImage | None = None
        self._tint: QImage | None = None
        self._lights = True
        self._checker = QBrush(_checkerboard()) if checkerboard else None
        self.excluded: set[str] = set()
        self.setMouseTracking(True)
        self.setMinimumSize(240, 135)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(480, 270)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return round(width * 9 / 16)

    def set_show(self, show: Show) -> None:
        self._show = show
        self.update()

    def set_image(self, image: QImage | None, tint: QImage | None = None) -> None:
        self._image = image
        self._tint = tint
        self.update()

    def set_lights_visible(self, visible: bool) -> None:
        self._lights = visible
        self.update()

    def canvas_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        width = min(area.width(), area.height() * 16 / 9)
        height = width * 9 / 16
        return QRectF(area.left() + (area.width() - width) / 2, area.top() + (area.height() - height) / 2, width, height)

    def to_widget(self, fx: float, fy: float) -> QPointF:
        c = self.canvas_rect()
        return QPointF(c.left() + fx * c.width(), c.top() + fy * c.height())

    def to_fraction(self, point: QPointF) -> tuple[float, float]:
        c = self.canvas_rect()
        return ((point.x() - c.left()) / max(c.width(), 1.0), (point.y() - c.top()) / max(c.height(), 1.0))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        canvas = self.canvas_rect()
        painter.setClipRect(canvas)
        if self._checker is not None:
            painter.fillRect(canvas, self._checker)
        else:
            painter.fillRect(canvas, QColor(theme.WELL))
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(canvas, self._image)
        if self._tint is not None:
            painter.drawImage(canvas, self._tint)
        if self._lights:
            self._paint_lights(painter, canvas)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(canvas.adjusted(0.5, 0.5, -0.5, -0.5))
        # handles may reach past the picture (a bar taller than the screen still needs its corners)
        painter.setClipRect(QRectF(self.rect()))
        self.paint_overlay(painter, canvas)
        painter.end()

    def paint_overlay(self, painter: QPainter, canvas: QRectF) -> None:
        """For subclasses: handles and tools."""

    def _paint_lights(self, painter: QPainter, canvas: QRectF) -> None:
        font = theme.ui_font(11)
        painter.setFont(font)
        fm = QFontMetrics(font)
        taken: list[QRectF] = []
        for fx in sorted(self._show.colour_fixtures(), key=lambda f: (f.map.y, f.map.x) if f.map else (0, 0)):
            if fx.map is None:
                continue
            centre = self.to_widget(fx.map.x, fx.map.y)
            radius = PATCH_RADIUS * canvas.width()
            excluded = fx.id in self.excluded
            ring = QPen(QColor(theme.DANGER if excluded else "#C8D2E0"), 1, Qt.PenStyle.DashLine)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius, radius)
            painter.setPen(QPen(QColor("#000000"), 1.5))
            painter.setBrush(QColor(theme.DANGER if excluded else theme.TEXT))
            painter.drawEllipse(centre, 4.0, 4.0)
            text = fm.elidedText(fx.name, Qt.TextElideMode.ElideRight, 110)
            box = QRectF(centre.x() - fm.horizontalAdvance(text) / 2 - 3, centre.y() + 7, fm.horizontalAdvance(text) + 6, fm.height())
            if any(box.intersects(other) for other in taken) or not canvas.contains(box):
                continue
            taken.append(box)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def light_at(self, point: QPointF) -> str | None:
        best = None
        for fx in self._show.colour_fixtures():
            if fx.map is None:
                continue
            centre = self.to_widget(fx.map.x, fx.map.y)
            distance = math.hypot(centre.x() - point.x(), centre.y() - point.y())
            if distance <= 10 and (best is None or distance < best[0]):
                best = (distance, fx.id)
        return best[1] if best else None


class SelectedViewport(ViewportCanvas):
    """The selected element alone, with shape handles and the shape tools."""

    shape_selected = Signal(str)
    shapes_changing = Signal(str, str, dict)  # element id, shape id, {setting: value} (live while dragging)
    shapes_changed = Signal(str)  # undo text, when a drag ends
    shape_drawn = Signal(str, dict)  # shape type, {x, y, w, h}
    drag_started = Signal()

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(show, checkerboard=True, parent=parent)
        self.mask_mode = False
        self.tool = "select"
        self.element: Mapping | None = None
        self.selected_shape = ""
        self.time = 0.0  # the element's own time at the playhead (keyframed shapes are drawn there)
        self._drag: dict | None = None
        self._draw: tuple[QPointF, QPointF] | None = None

    def set_element(self, element: Mapping | None, selected_shape: str | None) -> None:
        self.element = element
        self.selected_shape = selected_shape or ""
        mask = element.get("mask") if element is not None else None
        self.excluded = set(mask.get("exclude", ()) or ()) if hasattr(mask, "get") else set()
        self.update()

    def _is_paint(self) -> bool:
        return self.element is not None and self.element.get("kind") == "paint"

    def shapes(self) -> list[Mapping]:
        if not self._is_paint():
            return []
        return [s for s in (self.element.get("shapes") or ()) if hasattr(s, "get")]

    def shape_geometry(self, shape: Mapping) -> tuple[QPointF, float, float, float]:
        """(centre, half width, half height, rotation in degrees) in widget pixels."""
        canvas = self.canvas_rect()
        t = self.time
        x = evaluate(shape.get("x", 0.5), t, 0.5)
        y = evaluate(shape.get("y", 0.5), t, 0.5)
        w = abs(evaluate(shape.get("w", 0.3), t, 0.3))
        h = abs(evaluate(shape.get("h", 0.3), t, 0.3))
        rotation = evaluate(shape.get("rotation", 0.0), t, 0.0)
        return self.to_widget(x, y), w * canvas.width() / 2, h * canvas.height() / 2, rotation

    @staticmethod
    def editable(shape: Mapping) -> bool:
        """Shapes can be dragged unless a formula sets where they are (keyframed ones get a key at the playhead)."""
        return not any(expression_of(shape.get(key)) for key in ("x", "y", "w", "h", "rotation"))

    def _transform(self, centre: QPointF, rotation: float) -> QTransform:
        transform = QTransform()
        transform.translate(centre.x(), centre.y())
        transform.rotate(rotation)
        return transform

    def handle_points(self, shape: Mapping) -> dict[str, QPointF]:
        centre, hw, hh, rotation = self.shape_geometry(shape)
        t = self._transform(centre, rotation)
        points = {
            "tl": t.map(QPointF(-hw, -hh)), "tr": t.map(QPointF(hw, -hh)),
            "br": t.map(QPointF(hw, hh)), "bl": t.map(QPointF(-hw, hh)),
            "rotate": t.map(QPointF(0.0, -hh - ROTATE_GAP)),
        }
        return points

    def contains(self, shape: Mapping, point: QPointF) -> bool:
        centre, hw, hh, rotation = self.shape_geometry(shape)
        local = self._transform(centre, rotation).inverted()[0].map(point)
        if shape.get("type", "rect") == "ellipse":
            return (local.x() / max(hw, 0.5)) ** 2 + (local.y() / max(hh, 0.5)) ** 2 <= 1.0
        return abs(local.x()) <= hw and abs(local.y()) <= hh

    # ------------------------------------------------------------ drawing

    def paint_overlay(self, painter: QPainter, canvas: QRectF) -> None:
        if self.mask_mode:
            return
        for shape in self.shapes():
            centre, hw, hh, rotation = self.shape_geometry(shape)
            selected = shape.get("id") == self.selected_shape
            painter.save()
            painter.setTransform(self._transform(centre, rotation), True)
            pen = QPen(QColor(theme.ACCENT if selected else "#FFFFFF"), 1.4 if selected else 1.0, Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            box = QRectF(-hw, -hh, hw * 2, hh * 2)
            if shape.get("type", "rect") == "ellipse":
                painter.drawEllipse(box)
                faint = QPen(QColor(theme.ACCENT if selected else "#FFFFFF"), 1, Qt.PenStyle.DotLine)
                faint.setCosmetic(True)
                painter.setPen(faint)
                painter.drawRect(box)
            else:
                painter.drawRect(box)
            painter.restore()
            if selected:
                points = self.handle_points(shape)
                editable = self.editable(shape)
                fill = QColor(theme.ACCENT if editable else theme.TEXT_3)
                painter.setPen(QPen(QColor(theme.ACCENT_TEXT), 1))
                painter.setBrush(fill)
                for key in ("tl", "tr", "br", "bl"):
                    p = points[key]
                    painter.drawRect(QRectF(p.x() - HANDLE / 2 - 1, p.y() - HANDLE / 2 - 1, HANDLE + 2, HANDLE + 2))
                top_mid = (points["tl"] + points["tr"]) / 2
                painter.setPen(QPen(fill, 1))
                painter.drawLine(top_mid, points["rotate"])
                painter.setPen(QPen(QColor(theme.ACCENT_TEXT), 1))
                painter.drawEllipse(points["rotate"], HANDLE * 0.9, HANDLE * 0.9)
        if self._draw is not None:
            start, end = self._draw
            pen = QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(134, 214, 255, 40))
            rect = QRectF(start, end).normalized()
            if self.tool == "ellipse":
                painter.drawEllipse(rect)
            else:
                painter.drawRect(rect)

    # ------------------------------------------------------------ mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        if self.mask_mode:
            light = self.light_at(point)
            if light is not None:
                self.light_clicked.emit(light)
            return
        if not self._is_paint():
            return
        if self.tool in ("rect", "ellipse"):
            self._draw = (point, point)
            self.update()
            return
        shape = self._shape(self.selected_shape)
        if shape is not None:
            points = self.handle_points(shape)
            for key, p in points.items():
                if math.hypot(p.x() - point.x(), p.y() - point.y()) <= HANDLE + 4:
                    self._begin(shape, key, point)
                    return
        for candidate in reversed(self.shapes()):  # the topmost shape under the pointer
            if self.contains(candidate, point):
                if candidate.get("id") != self.selected_shape:
                    self.selected_shape = str(candidate.get("id"))
                    self.shape_selected.emit(self.selected_shape)
                self._begin(candidate, "move", point)
                self.update()
                return
        if self.selected_shape:
            self.selected_shape = ""
            self.shape_selected.emit("")
            self.update()

    def _shape(self, shape_id: str) -> Mapping | None:
        return next((s for s in self.shapes() if s.get("id") == shape_id), None) if shape_id else None

    def _begin(self, shape: Mapping, mode: str, point: QPointF) -> None:
        if not self.editable(shape):
            QToolTip.showText(self.mapToGlobal(point.toPoint()), "A formula places this shape: change it in the details panel", self)
            return
        centre, hw, hh, rotation = self.shape_geometry(shape)
        self._drag = {"shape": str(shape.get("id")), "mode": mode, "start": point, "centre": centre, "hw": hw, "hh": hh, "rotation": rotation}
        self.drag_started.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = event.position()
        if self._draw is not None:
            self._draw = (self._draw[0], self._constrained(self._draw[0], point, event.modifiers()))
            self.update()
            return
        if self._drag is None:
            self._update_cursor(point)
            return
        values = self._drag_values(point, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        if values:
            self.shapes_changing.emit(str(self.element.get("id")), self._drag["shape"], values)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._draw is not None:
            start, end = self._draw
            self._draw = None
            rect = QRectF(start, end).normalized()
            canvas = self.canvas_rect()
            if rect.width() < 4 and rect.height() < 4:  # a click: a medium-sized shape here
                fx, fy = self.to_fraction(start)
                values = {"x": fx, "y": fy, "w": 0.2, "h": 0.3}
            else:
                fx, fy = self.to_fraction(rect.center())
                values = {"x": fx, "y": fy, "w": rect.width() / canvas.width(), "h": rect.height() / canvas.height()}
            self.shape_drawn.emit(self.tool, values)
            self.update()
            return
        if self._drag is not None:
            mode, shape = self._drag["mode"], self._shape(self._drag["shape"])
            self._drag = None
            verb = {"move": "Move", "rotate": "Rotate"}.get(mode, "Resize")
            self.shapes_changed.emit(f"{verb} {shape_name(shape)}" if shape is not None else f"{verb} shape")

    def _constrained(self, start: QPointF, point: QPointF, modifiers) -> QPointF:
        if not modifiers & Qt.KeyboardModifier.ShiftModifier:
            return point
        dx, dy = point.x() - start.x(), point.y() - start.y()
        side = max(abs(dx), abs(dy))
        return QPointF(start.x() + math.copysign(side, dx or 1), start.y() + math.copysign(side, dy or 1))

    def _drag_values(self, point: QPointF, shift: bool) -> dict:
        drag = self._drag
        canvas = self.canvas_rect()
        centre: QPointF = drag["centre"]
        if drag["mode"] == "move":
            new_centre = centre + (point - drag["start"])
            fx, fy = self.to_fraction(new_centre)
            return {"x": fx, "y": fy}
        if drag["mode"] == "rotate":
            angle = math.degrees(math.atan2(point.y() - centre.y(), point.x() - centre.x())) + 90.0
            if shift:
                angle = round(angle / 15.0) * 15.0
            return {"rotation": round(((angle + 180.0) % 360.0) - 180.0, 2)}
        # a corner: the opposite corner stays put
        opposite = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr"}[drag["mode"]]
        transform = self._transform(centre, drag["rotation"])
        signs = {"tl": (-1, -1), "tr": (1, -1), "br": (1, 1), "bl": (-1, 1)}
        ox, oy = signs[opposite]
        anchor = transform.map(QPointF(ox * drag["hw"], oy * drag["hh"]))
        rotate_back = QTransform().rotate(-drag["rotation"])
        local = rotate_back.map(point - anchor)
        width, height = abs(local.x()), abs(local.y())
        if shift and drag["hw"] > 0 and drag["hh"] > 0:
            ratio = drag["hw"] / drag["hh"]
            if width / max(height, 1e-6) > ratio:
                height = width / ratio
            else:
                width = height * ratio
        half = QPointF(math.copysign(width / 2, local.x() or 1), math.copysign(height / 2, local.y() or 1))
        new_centre = anchor + QTransform().rotate(drag["rotation"]).map(half)
        fx, fy = self.to_fraction(new_centre)
        return {"x": fx, "y": fy, "w": max(width, 2.0) / canvas.width(), "h": max(height, 2.0) / canvas.height()}

    def _update_cursor(self, point: QPointF) -> None:
        if self.mask_mode:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.light_at(point) else Qt.CursorShape.ArrowCursor)
            return
        if self.tool in ("rect", "ellipse") and self._is_paint():
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        shape = self._shape(self.selected_shape)
        if shape is not None:
            for key, p in self.handle_points(shape).items():
                if math.hypot(p.x() - point.x(), p.y() - point.y()) <= HANDLE + 4:
                    self.setCursor(Qt.CursorShape.CrossCursor if key == "rotate" else Qt.CursorShape.SizeFDiagCursor)
                    return
        if any(self.contains(s, point) for s in self.shapes()):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)


class LightStrip(QWidget):
    """Each light's colour, in a row (hover a square for its name and values)."""

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show = show
        self._colours: Mapping[str, RGB] = {}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, self.fontMetrics().height() + 10)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(80, self.fontMetrics().height() + 10)

    def set_show(self, show: Show) -> None:
        self._show = show
        self.update()

    def set_colours(self, colours: Mapping[str, RGB]) -> None:
        self._colours = colours
        self.update()

    def _boxes(self) -> list[tuple[str, QRectF]]:
        lights = [fx for fx in self._show.colour_fixtures()]
        if not lights:
            return []
        gap = 4.0
        width = (self.width() - gap * (len(lights) - 1)) / len(lights)
        return [(fx.id, QRectF(i * (width + gap), 2, max(2.0, width), self.height() - 4)) for i, fx in enumerate(lights)]

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        for light_id, box in self._boxes():
            painter.setBrush(rgb_colour(self._colours.get(light_id, (0.0, 0.0, 0.0))))
            painter.drawRoundedRect(box, 4, 4)
        painter.end()

    def event(self, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.ToolTip:
            for light_id, box in self._boxes():
                if box.contains(event.position() if hasattr(event, "position") else QPointF(event.pos())):
                    fx = self._show.fixture(light_id)
                    r, g, b = (to_byte(v) for v in self._colours.get(light_id, (0.0, 0.0, 0.0)))
                    QToolTip.showText(event.globalPos(), f"{fx.name if fx else light_id}\nRed {r}  Green {g}  Blue {b}", self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(event)


def _toggle(text: str, tip: str, checked: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("ToolToggle")
    button.setCheckable(True)
    button.setChecked(checked)
    button.setToolTip(tip)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return button


class ViewportPanel(QFrame):
    """A titled viewport with its toggles above it."""

    lights_toggled = Signal(bool)

    def __init__(self, title: str, canvas: ViewportCanvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.canvas = canvas
        self.title = QLabel(title.upper())
        self.title.setObjectName("PanelTitle")
        self.lights = _toggle("Lights", "Show or hide the lights' dots, rings and names", True)
        self.lights.toggled.connect(self._lights)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(6)
        self.toolbar.addWidget(self.title)
        self.toolbar.addStretch(1)
        self.toolbar.addWidget(self.lights)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(12, 10, 12, 12)
        self.layout_.setSpacing(8)
        self.layout_.addLayout(self.toolbar)
        self.layout_.addWidget(canvas, 1)

    def _lights(self, on: bool) -> None:
        self.canvas.set_lights_visible(on)
        self.lights_toggled.emit(on)


class SelectedPanel(ViewportPanel):
    mode_changed = Signal(bool)  # mask mode on?

    def __init__(self, canvas: SelectedViewport, parent: QWidget | None = None) -> None:
        super().__init__("Selected", canvas, parent)
        self.layer = _toggle("Layer", "The element itself", True)
        self.mask = _toggle("Mask", "What the element's mask hides (red). Click a light's dot to tick or untick it.")
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.layer)
        modes.addButton(self.mask)
        self.mask.toggled.connect(self._mask_mode)
        self.select_tool = _toggle("Select", "Select, move, resize and rotate shapes", True)
        self.rect_tool = _toggle("Rectangle", "Draw a rectangle (Shift: square)")
        self.ellipse_tool = _toggle("Ellipse", "Draw an ellipse (Shift: circle)")
        tools = QButtonGroup(self)
        tools.setExclusive(True)
        for button, tool in ((self.select_tool, "select"), (self.rect_tool, "rect"), (self.ellipse_tool, "ellipse")):
            tools.addButton(button)
            button.toggled.connect(lambda on, t=tool: on and self._tool(t))
        self.toolbar.insertWidget(1, self.layer)
        self.toolbar.insertWidget(2, self.mask)
        tools_row = QHBoxLayout()  # a second row, so the panel stays narrow enough for small windows
        tools_row.setSpacing(6)
        for button in (self.select_tool, self.rect_tool, self.ellipse_tool):
            tools_row.addWidget(button)
        tools_row.addStretch(1)
        self.layout_.insertLayout(1, tools_row)

    def _mask_mode(self, on: bool) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        canvas.mask_mode = on
        for button in (self.select_tool, self.rect_tool, self.ellipse_tool):
            button.setEnabled(not on and canvas._is_paint())
        canvas.update()
        self.mode_changed.emit(on)

    def _tool(self, tool: str) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        canvas.tool = tool

    def set_paint_tools(self, enabled: bool) -> None:
        for button in (self.select_tool, self.rect_tool, self.ellipse_tool):
            button.setEnabled(enabled and not self.mask.isChecked())
        if not enabled:
            self.select_tool.setChecked(True)


class ResultPanel(ViewportPanel):
    def __init__(self, canvas: ViewportCanvas, show: Show, parent: QWidget | None = None) -> None:
        super().__init__("Result", canvas, parent)
        self.strip = LightStrip(show)
        self.strip.setToolTip("Each light's colour from this input (before Master)")
        self.layout_.addWidget(self.strip)

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return -1
