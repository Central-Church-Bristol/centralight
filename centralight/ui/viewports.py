"""The Editor's two viewports.

- Selected: the selected element on its own, over a checkerboard (so see-through parts
  show). Layer / Mask: the mask view tints whatever the element's mask hides in red.
  For a Paint element its shapes have handles: drag the middle to move, a corner to
  resize (Shift keeps the proportions), the round knob to rotate (Shift snaps to 15°).
  The Rectangle and Ellipse tools draw new shapes (Shift: square / circle). The painting tools
  (brush, eraser, bucket, gradient, light colour) paint on a Paint element's strokes. Mask
  mode has the same Select, Rectangle and Ellipse for the mask's shapes (each shows or hides
  the element), the Brush (hides) and Eraser (rubs that out) for its painting, and the Light
  tool to tick or untick a light (M14). The eyedropper picks the brush colour. An
  Image, Video or NDI element's picture has a gizmo: drag it to move, a corner to scale, the
  knob to turn it.
- Result: the whole input, with a strip underneath showing each light's colour,
  worked out exactly as the engine does it.

Both have a Lights toggle that shows or hides the dots and names. With an Image, Video or NDI
element selected, the Selected viewport also draws each light's patch: the part of the picture
it averages at the element's Area (lights are points: this is the only place patches show).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..model import Show
from ..output import RGB, to_byte
from ..assets import ASSET_H, ASSET_W
from ..params import evaluate, expression_of
from ..step_names import element_name, lower_first, mask_shape_name, shape_name
from . import icons, theme
from .light_view import rgb_colour
from .widgets import Fader, FlowLayout

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
        self.patches: tuple = ()  # (light id, x0, y0, x1, y1): the patch each light averages (Selected, pictures only)
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

    def set_patches(self, patches: tuple) -> None:
        if patches != self.patches:
            self.patches = patches
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
        for light_id, x0, y0, x1, y1 in self.patches:  # what each light averages of the selected picture
            box = QRectF(self.to_widget(x0, y0), self.to_widget(x1, y1))
            edge = QPen(QColor(theme.DANGER if light_id in self.excluded else "#C8D2E0"), 1, Qt.PenStyle.DashLine)
            edge.setCosmetic(True)
            painter.setPen(edge)
            painter.setBrush(QColor(255, 255, 255, 18))
            painter.drawRect(box)
        for fx in sorted(self._show.colour_fixtures(), key=lambda f: (f.map.y, f.map.x) if f.map else (0, 0)):
            if fx.map is None:
                continue
            centre = self.to_widget(fx.map.x, fx.map.y)
            excluded = fx.id in self.excluded
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


PAINT_TOOLS = ("brush", "eraser", "bucket", "gradient", "light")  # paint on a Paint element's strokes
MASK_TOOLS = ("show", "hide")  # paint an element's mask
MASK_BRUSHES = {"brush": "hide", "eraser": "show"}  # Mask mode: the Brush hides, the Eraser rubs that out (M14)
MASK_MODE_TOOLS = ("select", "rect", "ellipse", "brush", "eraser", "light")  # no bucket, gradient or eyedropper there
TOOL_TIPS = {  # (in Layer mode, in Mask mode)
    "select": ("Select, move, resize and rotate shapes (or the picture of an Image, Video or NDI element)",
               "Select, move, resize and rotate the mask's shapes"),
    "rect": ("Draw a rectangle (Shift: square)",
             "Draw a rectangle on the mask (Shift: square): it hides the element there, or shows it if the mask starts "
             "hidden everywhere"),
    "ellipse": ("Draw an ellipse (Shift: circle)",
                "Draw an ellipse on the mask (Shift: circle): it hides the element there, or shows it if the mask starts "
                "hidden everywhere"),
    "brush": ("Brush: paint on this Paint element", "Brush: paint where this element is hidden"),
    "eraser": ("Eraser: rub out painting", "Eraser: rub out the mask's painting, so the element shows there again"),
    "light": ("Light colour: click a light's dot to fill the patch it follows",
              "Light: click a light's dot to tick or untick it (an unticked light is left alone by this element)"),
}
BRUSH_TOOLS = ("brush", "eraser", "show", "hide")
PAINT_VERBS = {"brush": "brush stroke", "eraser": "eraser", "bucket": "bucket fill", "gradient": "gradient",
               "light": "light colour", "show": "mask: show", "hide": "mask: hide"}


class SelectedViewport(ViewportCanvas):
    """The selected element alone, with shape handles, the shape and painting tools, and (for pictures) a gizmo to
    move, scale and turn them."""

    shape_selected = Signal(str)
    shapes_changing = Signal(str, str, dict)  # element id, shape id, {setting: value} (live while dragging)
    shapes_changed = Signal(str)  # undo text, when a drag ends
    shape_drawn = Signal(str, dict)  # shape type, {x, y, w, h}
    drag_started = Signal()
    painted = Signal(str, object, str)  # "strokes" or "mask", the painting.Canvas, what was done (for undo)
    colour_picked = Signal(object)  # (r, g, b) from the eyedropper
    transform_changing = Signal(str, dict)  # element id, {"transform.x": ...} (live while dragging the gizmo)
    shape_remove_requested = Signal(str)  # shape id: Delete or Backspace with a shape selected here (M14)

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(show, checkerboard=True, parent=parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # (clicked: it takes the keys, so Delete reaches it)
        self.mask_mode = False
        self.tool = "select"
        self.element: Mapping | None = None
        self.selected_shape = ""
        self.time = 0.0  # the element's own time at the playhead (keyframed shapes are drawn there)
        self.folder = ""  # the show folder (for painted pictures)
        self.brush_size = 24.0  # radius, in the 640x360 picture's pixels
        self.brush_softness = 0.5
        self.brush_colour: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self.load_picture = lambda _what: None  # the Editor gives: "strokes" / "mask" -> the picture there now
        self._drag: dict | None = None
        self._draw: tuple[QPointF, QPointF] | None = None
        self._session: dict | None = None  # painting: {"what", "tool", "canvas", "start"}
        self._preview: QImage | None = None
        self._preview_is_mask = False
        self._preview_at = 0.0
        self._preview_until = 0.0
        self._pointer: QPointF | None = None
        self._preview_ctx = None
        self.setMouseTracking(True)

    def set_element(self, element: Mapping | None, selected_shape: str | None) -> None:
        if self._session is not None and (element is None or self.element is None or element.get("id") != self.element.get("id")):
            self._session = None
            self._preview = None
        self.element = element
        self.selected_shape = selected_shape or ""
        mask = element.get("mask") if element is not None else None
        self.excluded = set(mask.get("exclude", ()) or ()) if hasattr(mask, "get") else set()
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Delete or Backspace removes the selected shape, a Paint shape or (in Mask mode) a mask shape (never the
        element: that's the timeline's Delete)."""
        if (event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_shape
                and self._drag is None and self._draw is None
                and any(str(shape.get("id")) == self.selected_shape for shape in self.shapes())):
            self.shape_remove_requested.emit(self.selected_shape)
            event.accept()
            return
        super().keyPressEvent(event)

    def _is_paint(self) -> bool:
        return self.element is not None and self.element.get("kind") == "paint"

    def _shapes_here(self) -> bool:
        """Can shapes be drawn and edited now? A Paint element's, in Layer mode; any element's mask shapes, in Mask mode."""
        return self.element is not None and (self.mask_mode or self._is_paint())

    def shapes(self) -> list[Mapping]:
        """The shapes being edited: the Paint element's, or (in Mask mode) the mask's."""
        if not self._shapes_here():
            return []
        if self.mask_mode:
            mask = self.element.get("mask")
            raw = mask.get("shapes") if hasattr(mask, "get") else None
        else:
            raw = self.element.get("shapes")
        return [s for s in (raw or ()) if hasattr(s, "get")]

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

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._pointer = None
        self.update()
        super().leaveEvent(event)

    def paint_overlay(self, painter: QPainter, canvas: QRectF) -> None:
        if self._preview is not None:  # what's being painted, until the renderer catches up
            painter.save()
            painter.setClipRect(canvas)
            if self._checker is not None:  # start clean: the renderer's own (older) picture and tint are under here
                painter.fillRect(canvas, self._checker)
            if self._preview_is_mask:
                if self._image is not None and not self._image.isNull():
                    painter.drawImage(canvas, self._image)
                painter.drawImage(canvas, self._preview)
            else:
                painter.drawImage(canvas, self._preview)
            if self._lights:
                self._paint_lights(painter, canvas)
            painter.restore()
        tool = self.painting_tool()
        if tool in BRUSH_TOOLS and self._pointer is not None and canvas.contains(self._pointer):
            radius = self.brush_size * canvas.width() / ASSET_W
            pen = QPen(QColor("#FFFFFF"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._pointer, radius, radius)
        if self._session is not None and self._session["tool"] == "gradient" and "end" in self._session:
            pen = QPen(QColor(theme.ACCENT), 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            start, end = self._session["start"], self._session["end"]
            if self._session.get("radial"):
                radius = math.hypot(end.x() - start.x(), end.y() - start.y())
                painter.drawEllipse(start, radius, radius)
            painter.drawLine(start, end)
        if self._is_picture() and self.tool == "select" and not self.mask_mode:
            points = self.picture_corners()
            fill = QColor(theme.ACCENT if self._picture_editable() else theme.TEXT_3)
            pen = QPen(fill, 1.4)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([points[k] for k in ("tl", "tr", "br", "bl")]))
            top_mid = (points["tl"] + points["tr"]) / 2
            painter.drawLine(top_mid, points["rotate"])
            painter.setPen(QPen(QColor(theme.ACCENT_TEXT), 1))
            painter.setBrush(fill)
            for key in ("tl", "tr", "br", "bl"):
                p = points[key]
                painter.drawRect(QRectF(p.x() - HANDLE / 2 - 1, p.y() - HANDLE / 2 - 1, HANDLE + 2, HANDLE + 2))
            painter.drawEllipse(points["rotate"], HANDLE * 0.9, HANDLE * 0.9)
        for shape in self.shapes():
            centre, hw, hh, rotation = self.shape_geometry(shape)
            selected = shape.get("id") == self.selected_shape
            painter.save()
            painter.setTransform(self._transform(centre, rotation), True)
            # (in Mask mode a Hide shape's outline is red, like what the mask hides; a Show shape's is white)
            outline = theme.DANGER if self.mask_mode and shape.get("mode", "hide") != "show" else "#FFFFFF"
            pen = QPen(QColor(theme.ACCENT if selected else outline), 1.4 if selected else 1.0, Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine)
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

    # ------------------------------------------------------------ painting

    def painting_tool(self) -> str:
        """The tool that paints right now ("" = none): strokes on a Paint element, or the mask's brushes (in Mask mode
        the Brush paints where the element is hidden, and the Eraser rubs that out)."""
        if self.element is None:
            return ""
        if self.mask_mode:
            return MASK_BRUSHES.get(self.tool, "")
        return self.tool if self.tool in PAINT_TOOLS and self._is_paint() else ""

    def to_picture(self, point: QPointF, what: str) -> tuple[float, float]:
        """A point on the view -> pixels of the 640x360 picture being painted. Masks are on the screen; strokes
        are the element's own picture, so its move / scale / turn is undone first."""
        fx, fy = self.to_fraction(point)
        x, y = fx * ASSET_W, fy * ASSET_H
        if what == "mask" or self.element is None:
            return x, y
        t = self.time
        cx = evaluate((self.element.get("transform") or {}).get("x", 0.5), t, 0.5) * ASSET_W
        cy = evaluate((self.element.get("transform") or {}).get("y", 0.5), t, 0.5) * ASSET_H
        scale = max(1e-4, evaluate((self.element.get("transform") or {}).get("scale", 1.0), t, 1.0))
        angle = math.radians(evaluate((self.element.get("transform") or {}).get("rotation", 0.0), t, 0.0))
        dx, dy = x - cx, y - cy
        cos_a, sin_a = math.cos(-angle), math.sin(-angle)
        rx, ry = dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a
        return rx / scale + ASSET_W / 2, ry / scale + ASSET_H / 2

    def _start_painting(self, tool: str, point: QPointF) -> None:
        from ..painting import Canvas

        what = "mask" if tool in MASK_TOOLS else "strokes"
        start = self.load_picture(what)
        self._session = {"what": what, "tool": tool, "canvas": Canvas(mask=what == "mask", start=start), "start": point}
        canvas = self._session["canvas"]
        x, y = self.to_picture(point, what)
        if tool in BRUSH_TOOLS:
            mode = {"brush": "paint", "eraser": "erase"}.get(tool, tool)
            canvas.stroke_to(x, y, self.brush_size, self.brush_softness, self.brush_colour, mode=mode)
        elif tool == "bucket":
            canvas.bucket(x, y, self.brush_colour)
            self._finish_painting()
            return
        elif tool == "light":
            area = self._light_area(point)
            if area is None:
                self._session = None
                return
            canvas.fill_area(area, self.brush_colour)
            self._finish_painting()
            return
        self._update_preview(force=True)

    def _light_area(self, point: QPointF):
        """A dab round the dot of the light nearest the pointer, in the element's own picture, or None if no light is
        near. Paint is read exactly at a light's dot, so the dab only has to cover it: it stays clear of the next
        light (at most 8 % of the width across)."""
        import cv2
        import numpy as np

        fx, fy = self.to_fraction(point)
        spots = [(fixture.map.x, fixture.map.y) for fixture in self._show.colour_fixtures() if fixture.map is not None]
        if not spots:
            return None
        aspect = ASSET_H / ASSET_W
        distances = [math.hypot(x - fx, (y - fy) * aspect) for x, y in spots]
        nearest = min(range(len(spots)), key=distances.__getitem__)
        if distances[nearest] > 0.15:
            return None
        lx, ly = spots[nearest]
        others = [math.hypot(x - lx, (y - ly) * aspect) for i, (x, y) in enumerate(spots) if i != nearest]
        radius = min(0.04, 0.45 * min(others, default=1.0))
        area = np.zeros((ASSET_H, ASSET_W), dtype=np.uint8)
        cv2.circle(area, (round(lx * ASSET_W), round(ly * ASSET_H)), max(2, round(radius * ASSET_W)), 1, -1)
        transform = self.element.get("transform") if self.element is not None else None
        if hasattr(transform, "get") and transform:  # the dab, seen in the element's own picture
            t = self.time
            matrix = cv2.getRotationMatrix2D((ASSET_W / 2.0, ASSET_H / 2.0), -evaluate(transform.get("rotation", 0.0), t, 0.0),
                                             max(1e-4, evaluate(transform.get("scale", 1.0), t, 1.0)))
            matrix[0, 2] += (evaluate(transform.get("x", 0.5), t, 0.5) - 0.5) * ASSET_W
            matrix[1, 2] += (evaluate(transform.get("y", 0.5), t, 0.5) - 0.5) * ASSET_H
            area = cv2.warpAffine(area, cv2.invertAffineTransform(matrix), (ASSET_W, ASSET_H), flags=cv2.INTER_NEAREST)
        return area > 0

    def _continue_painting(self, point: QPointF, modifiers) -> None:
        session = self._session
        tool = session["tool"]
        if tool in BRUSH_TOOLS:
            x, y = self.to_picture(point, session["what"])
            mode = {"brush": "paint", "eraser": "erase"}.get(tool, tool)
            session["canvas"].stroke_to(x, y, self.brush_size, self.brush_softness, self.brush_colour, mode=mode)
            self._update_preview()
        elif tool == "gradient":
            session["end"] = point
            session["radial"] = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            self.update()

    def _finish_painting(self, point: QPointF | None = None, modifiers=None) -> None:
        session = self._session
        if session is None:
            return
        canvas = session["canvas"]
        if session["tool"] == "gradient":
            end = point if point is not None else session.get("end", session["start"])
            x0, y0 = self.to_picture(session["start"], "strokes")
            x1, y1 = self.to_picture(end, "strokes")
            if math.hypot(x1 - x0, y1 - y0) < 2:
                self._session = None
                self._preview = None
                self.update()
                return
            radial = bool(modifiers & Qt.KeyboardModifier.ShiftModifier) if modifiers is not None else session.get("radial", False)
            canvas.gradient(x0, y0, x1, y1, self.brush_colour, radial=radial)
        canvas.end_stroke()
        if canvas.changed:
            # show the finished picture until the renderer has drawn it from the new file
            self._update_preview(force=True)
            self._preview_until = time.monotonic() + 0.4
            self._session = None
            self.painted.emit(session["what"], canvas, PAINT_VERBS.get(session["tool"], "painting"))
        else:
            self._session = None
            self._preview = None
        self.update()

    def drop_preview(self) -> None:
        """The view now has a picture drawn from the show as it is (e.g. drawn on the spot): no preview needed."""
        if self._session is None:
            self._preview = None
            self._preview_until = 0.0
            self.update()

    def _update_preview(self, force: bool = False) -> None:
        """Show the picture being painted straight away (at most ~30 times a second)."""
        import numpy as np

        now = time.monotonic()
        if not force and now - self._preview_at < 1 / 30:
            return
        self._preview_at = now
        session = self._session
        if session is None or self.element is None:
            return
        canvas = session["canvas"]
        self._preview_is_mask = session["what"] == "mask"
        if session["what"] == "mask":  # (with the mask's shapes and Start with, as the renderer's tint has them)
            from ..elements import RenderContext, element_keep

            height, width = canvas.data.shape[:2]
            ctx = RenderContext(width, height)
            ctx.folder = self.folder
            ctx.time = self.time
            keep = element_keep(self.element, ctx, painting=canvas.data)
            self._preview = mask_tint(np.clip((canvas.data if keep is None else keep) * 255.0 + 0.5, 0, 255).astype(np.uint8))
        else:
            from ..elements import RenderContext, finish_layer, render_paint

            if self._preview_ctx is None:
                self._preview_ctx = RenderContext(ASSET_W, ASSET_H)
            ctx = self._preview_ctx
            ctx.folder = self.folder
            ctx.time = self.time
            strokes = np.ascontiguousarray(canvas.data.transpose(2, 0, 1))
            layer = render_paint(self.element, ctx, strokes_layer=strokes)
            if layer is not None:
                layer = finish_layer(self.element, layer, ctx)
                rgba = np.clip(layer.transpose(1, 2, 0) * 255.0 + 0.5, 0, 255).astype(np.uint8)
                self._preview = rgba_image(np.ascontiguousarray(rgba))
        self.update()

    def set_image(self, image: QImage | None, tint: QImage | None = None) -> None:
        if self._session is None and time.monotonic() >= self._preview_until:
            self._preview = None  # the renderer has caught up with the painting
        super().set_image(image, tint)

    def _pick_colour(self, point: QPointF) -> None:
        image = self._preview or self._image
        if image is None or image.isNull():
            return
        fx, fy = self.to_fraction(point)
        x = int(min(image.width() - 1, max(0, fx * image.width())))
        y = int(min(image.height() - 1, max(0, fy * image.height())))
        colour = image.pixelColor(x, y)
        if colour.alpha() == 0:
            return
        alpha = colour.alphaF()  # the view's pictures are premultiplied: straight colour, please
        self.colour_picked.emit(tuple(min(1.0, c / alpha) for c in (colour.redF(), colour.greenF(), colour.blueF())))

    # ------------------------------------------------------------ the picture gizmo (Image, Video, NDI)

    def _is_picture(self) -> bool:
        return self.element is not None and self.element.get("kind") in ("image", "video", "ndi")

    def picture_corners(self) -> dict[str, QPointF]:
        """The picture's outline on the view (after its move / scale / turn), its corners and its turn handle."""
        canvas = self.canvas_rect()
        transform = self.element.get("transform") if self.element is not None else None
        transform = transform if hasattr(transform, "get") else {}
        t = self.time
        cx = canvas.left() + evaluate(transform.get("x", 0.5), t, 0.5) * canvas.width()
        cy = canvas.top() + evaluate(transform.get("y", 0.5), t, 0.5) * canvas.height()
        scale = evaluate(transform.get("scale", 1.0), t, 1.0)
        rotation = evaluate(transform.get("rotation", 0.0), t, 0.0)
        hw, hh = canvas.width() * scale / 2, canvas.height() * scale / 2
        mapping = self._transform(QPointF(cx, cy), rotation)
        points = {key: mapping.map(QPointF(sx * hw, sy * hh)) for key, (sx, sy) in
                  {"tl": (-1, -1), "tr": (1, -1), "br": (1, 1), "bl": (-1, 1)}.items()}
        points["rotate"] = mapping.map(QPointF(0.0, -hh - ROTATE_GAP))
        points["centre"] = QPointF(cx, cy)
        return points

    def _picture_editable(self) -> bool:
        transform = self.element.get("transform") if self.element is not None else None
        transform = transform if hasattr(transform, "get") else {}
        return not any(expression_of(transform.get(key)) for key in ("x", "y", "scale", "rotation"))

    def _begin_picture_drag(self, point: QPointF) -> bool:
        if not self._is_picture() or self.tool != "select" or self.mask_mode:
            return False
        points = self.picture_corners()
        mode = None
        for key in ("rotate", "tl", "tr", "br", "bl"):
            p = points[key]
            if math.hypot(p.x() - point.x(), p.y() - point.y()) <= HANDLE + 4:
                mode = key
                break
        if mode is None:
            outline = QPolygonF([points[k] for k in ("tl", "tr", "br", "bl")])
            if not outline.containsPoint(point, Qt.FillRule.OddEvenFill):
                return False
            mode = "move"
        if not self._picture_editable():
            QToolTip.showText(self.mapToGlobal(point.toPoint()), "A formula places this picture: change it in the details panel", self)
            return True
        transform = self.element.get("transform") if hasattr(self.element.get("transform"), "get") else {}
        t = self.time
        self._drag = {"picture": True, "mode": mode, "start": point, "centre": points["centre"],
                      "x": evaluate(transform.get("x", 0.5), t, 0.5), "y": evaluate(transform.get("y", 0.5), t, 0.5),
                      "scale": evaluate(transform.get("scale", 1.0), t, 1.0),
                      "distance": max(4.0, math.hypot(point.x() - points["centre"].x(), point.y() - points["centre"].y()))}
        self.drag_started.emit()
        return True

    def _picture_values(self, point: QPointF, shift: bool) -> dict:
        drag = self._drag
        canvas = self.canvas_rect()
        centre: QPointF = drag["centre"]
        if drag["mode"] == "move":
            delta = point - drag["start"]
            return {"transform.x": drag["x"] + delta.x() / canvas.width(), "transform.y": drag["y"] + delta.y() / canvas.height()}
        if drag["mode"] == "rotate":
            angle = math.degrees(math.atan2(point.y() - centre.y(), point.x() - centre.x())) + 90.0
            if shift:
                angle = round(angle / 15.0) * 15.0
            return {"transform.rotation": round(((angle + 180.0) % 360.0) - 180.0, 2)}
        distance = math.hypot(point.x() - centre.x(), point.y() - centre.y())
        return {"transform.scale": round(min(4.0, max(0.05, drag["scale"] * distance / drag["distance"])), 4)}

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        if self.tool == "dropper" and self.element is not None and not self.mask_mode:
            self._pick_colour(point)
            return
        tool = self.painting_tool()
        if tool:
            self._start_painting(tool, point)
            return
        if self._begin_picture_drag(point):
            return
        if self.mask_mode and self.tool == "light":  # (Mask mode's Light: tick or untick the light whose dot this is)
            light = self.light_at(point)
            if light is not None:
                self.light_clicked.emit(light)
            return
        if not self._shapes_here():
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
        self._pointer = point
        if self._session is not None:
            self._continue_painting(point, event.modifiers())
            return
        if self.painting_tool() in BRUSH_TOOLS:
            self.update()  # the brush's outline follows the pointer
        if self._drag is not None and self._drag.get("picture"):
            self.transform_changing.emit(str(self.element.get("id")), self._picture_values(point, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)))
            return
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
        if self._session is not None:
            self._finish_painting(event.position(), event.modifiers())
            return
        if self._drag is not None and self._drag.get("picture"):
            mode = self._drag["mode"]
            self._drag = None
            name = element_name(self.element) if self.element is not None else "picture"
            self.shapes_changed.emit(f"{'Move' if mode == 'move' else 'Turn' if mode == 'rotate' else 'Resize'} {name}")
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
            name = (lower_first(mask_shape_name(shape)) if self.mask_mode else shape_name(shape)) if shape is not None else "shape"
            self.shapes_changed.emit(f"{verb} {name}")

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
        if self.painting_tool() or self.tool == "dropper":
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._is_picture() and self.tool == "select" and not self.mask_mode:
            points = self.picture_corners()
            for key in ("rotate", "tl", "tr", "br", "bl"):
                p = points[key]
                if math.hypot(p.x() - point.x(), p.y() - point.y()) <= HANDLE + 4:
                    self.setCursor(Qt.CursorShape.CrossCursor if key == "rotate" else Qt.CursorShape.SizeFDiagCursor)
                    return
            outline = QPolygonF([points[k] for k in ("tl", "tr", "br", "bl")])
            self.setCursor(Qt.CursorShape.SizeAllCursor if outline.containsPoint(point, Qt.FillRule.OddEvenFill) else Qt.CursorShape.ArrowCursor)
            return
        if self.mask_mode and self.tool == "light":
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.light_at(point) else Qt.CursorShape.ArrowCursor)
            return
        if self.tool in ("rect", "ellipse") and self._shapes_here():
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
        self.lights = _toggle("Lights", "Show or hide the lights' dots and names", True)
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


def _tool_icon(draw, tip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("ToolIcon")
    button.setCheckable(True)
    button.setIcon(icons.line_icon(draw, theme.TEXT_2, theme.ACCENT_TEXT))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class SelectedPanel(ViewportPanel):
    mode_changed = Signal(bool)  # mask mode on?

    def __init__(self, canvas: SelectedViewport, parent: QWidget | None = None) -> None:
        super().__init__("Selected", canvas, parent)
        self.layer = _toggle("Layer", "The element itself", True)
        self.mask = _toggle("Mask", "The element's mask: what it hides is red. Draw rectangles and ellipses that show or "
                                    "hide it, paint with the Brush (hides) and Eraser, or tick and untick lights with "
                                    "the Light tool.")
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.layer)
        modes.addButton(self.mask)
        self.mask.toggled.connect(self._mask_mode)
        self.select_tool = _toggle("Select", TOOL_TIPS["select"][0], True)
        self.rect_tool = _toggle("Rectangle", TOOL_TIPS["rect"][0])
        self.ellipse_tool = _toggle("Ellipse", TOOL_TIPS["ellipse"][0])
        self.brush_tool = _tool_icon(icons.draw_brush, TOOL_TIPS["brush"][0])
        self.eraser_tool = _tool_icon(icons.draw_eraser, TOOL_TIPS["eraser"][0])
        self.bucket_tool = _tool_icon(icons.draw_bucket, "Bucket: fill an area of the same colour")
        self.gradient_tool = _tool_icon(icons.draw_gradient, "Gradient: drag from where the colour starts to where it's gone (Shift: round)")
        self.light_tool = _tool_icon(icons.draw_bulb, TOOL_TIPS["light"][0])
        self.dropper_tool = _tool_icon(icons.draw_dropper, "Eyedropper: pick the brush colour from the picture")
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        # the same tools in both modes (M14): Mask mode shows Select, Rectangle, Ellipse, Brush, Eraser and Light
        self._layer_tools = ((self.select_tool, "select"), (self.rect_tool, "rect"), (self.ellipse_tool, "ellipse"),
                             (self.brush_tool, "brush"), (self.eraser_tool, "eraser"), (self.bucket_tool, "bucket"),
                             (self.gradient_tool, "gradient"), (self.light_tool, "light"), (self.dropper_tool, "dropper"))
        for button, tool in self._layer_tools:
            self.tools.addButton(button)
            button.toggled.connect(lambda on, t=tool: on and self._tool(t))
        self.toolbar.insertWidget(1, self.layer)
        self.toolbar.insertWidget(2, self.mask)
        tools_box = QWidget()
        tools_row = FlowLayout(tools_box, spacing=6)  # wraps onto a second line when the panel is narrow
        for button, _tool in self._layer_tools:
            tools_row.addWidget(button)
        self.layout_.insertWidget(1, tools_box)
        # the brush's size, softness and colour (shown while a painting tool is picked)
        self.size = Fader()
        self.size.setRange(1, 120)
        self.size.setValue(round(canvas.brush_size))
        self.size.setToolTip("Brush size")
        self.size.setMinimumWidth(60)
        self.size.valueChanged.connect(lambda value: setattr(canvas, "brush_size", float(value)))
        self.softness = Fader()
        self.softness.setRange(0, 100)
        self.softness.setValue(round(canvas.brush_softness * 100))
        self.softness.setToolTip("How soft the brush's edge is")
        self.softness.setMinimumWidth(60)
        self.softness.valueChanged.connect(lambda value: setattr(canvas, "brush_softness", value / 100.0))
        self.colour = QToolButton()
        self.colour.setObjectName("BrushColour")
        self.colour.setToolTip("Brush colour")
        self.colour.clicked.connect(self._choose_colour)
        self.options = QWidget()
        options = QHBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(6)
        size_label = QLabel("Size")
        size_label.setObjectName("RowDetail")
        soft_label = QLabel("Soft")
        soft_label.setObjectName("RowDetail")
        options.addWidget(size_label)
        options.addWidget(self.size, 1)
        options.addWidget(soft_label)
        options.addWidget(self.softness, 1)
        options.addWidget(self.colour)
        self.layout_.insertWidget(2, self.options)
        canvas.colour_picked.connect(self.set_brush_colour)
        self.set_brush_colour(canvas.brush_colour)
        self._paint_element = False
        self._sync_tools()

    def set_brush_colour(self, rgb) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        canvas.brush_colour = tuple(float(c) for c in rgb)
        swatch = QPixmap(18, 18)
        swatch.fill(rgb_colour(canvas.brush_colour))
        self.colour.setIcon(QIcon(swatch))

    def _choose_colour(self) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        chosen = QColorDialog.getColor(rgb_colour(canvas.brush_colour), self, "Brush colour")
        if chosen.isValid():
            self.set_brush_colour((chosen.redF(), chosen.greenF(), chosen.blueF()))

    def _mask_mode(self, on: bool) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        canvas.mask_mode = on
        canvas.selected_shape = ""  # (each mode has its own shapes: the Editor gives this mode's selection)
        (self.light_tool if on else self.select_tool).setChecked(True)  # (Mask mode starts on ticking lights, as before)
        self._sync_tools()
        canvas.update()
        self.mode_changed.emit(on)

    def _tool(self, tool: str) -> None:
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        canvas.tool = tool
        self._sync_tools()
        canvas.update()

    def _sync_tools(self) -> None:
        mask = self.mask.isChecked()
        paint = self._paint_element
        for button, tool in self._layer_tools:
            button.setVisible(not mask or tool in MASK_MODE_TOOLS)
            button.setEnabled(mask or paint or tool in ("select", "dropper"))  # (any element has a mask)
            if tool in TOOL_TIPS:
                button.setToolTip(TOOL_TIPS[tool][1 if mask else 0])
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        brushes = ("brush", "eraser") if mask else BRUSH_TOOLS + ("bucket", "gradient", "light")
        self.options.setVisible(canvas.tool in brushes)
        self.colour.setVisible(not mask and canvas.tool in ("brush", "bucket", "gradient", "light"))

    def set_paint_tools(self, enabled: bool) -> None:
        """Shapes and painting are for Paint elements (Select and the eyedropper work for them all)."""
        self._paint_element = enabled
        canvas: SelectedViewport = self.canvas  # type: ignore[assignment]
        if not enabled and not self.mask.isChecked() and canvas.tool in ("rect", "ellipse") + PAINT_TOOLS:
            self.select_tool.setChecked(True)  # (Mask mode's tools work on any element)
        self._sync_tools()


class ResultPanel(ViewportPanel):
    def __init__(self, canvas: ViewportCanvas, show: Show, parent: QWidget | None = None) -> None:
        super().__init__("Result", canvas, parent)
        self.strip = LightStrip(show)
        self.strip.setToolTip("Each light's colour from this input (before Master)")
        self.layout_.addWidget(self.strip)

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return -1
