"""The light view: the 16:9 map on black, with a glowing dot per colour light.

Each dot shows the colour actually being sent (in Simulated mode: what *would* be
sent). House lights (festoon, key lights) aren't on the map; they're shown as small
bulbs along the bottom with their level. Light names that would bump into each other
are hidden; hovering over a dot always shows its details.

Everything is worked out from the widget's size on every paint, and the 16:9 map is
letterboxed so it keeps its shape at any size.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..model import Fixture, Show
from ..output import RGB, to_byte
from . import icons, theme

HOUSE_STRIP = 30  # logical px under the map for the house-light bulbs
HOUSE_GAP = 22  # logical px between one house light's legend and the next


def rgb_colour(rgb: RGB) -> QColor:
    return QColor(to_byte(rgb[0]), to_byte(rgb[1]), to_byte(rgb[2]))


class LightView(QWidget):
    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LightView")
        self._show = show
        self._colours: Mapping[str, RGB] = {}
        self._house: Mapping[str, float] = {}
        self._picture: QImage | None = None
        self._labels = True
        self.setMouseTracking(True)
        self.setMinimumSize(240, 135 + HOUSE_STRIP)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # ------------------------------------------------------------ data

    def set_show(self, show: Show) -> None:
        self._show = show
        self.update()

    def set_levels(self, colours: Mapping[str, RGB], house: Mapping[str, float]) -> None:
        if colours != self._colours or house != self._house:
            self._colours = colours
            self._house = house
            self.update()

    def set_picture(self, image: QImage | None) -> None:
        self._picture = image
        self.update()

    def set_labels_visible(self, visible: bool) -> None:
        self._labels = visible
        self.update()

    # ------------------------------------------------------------ geometry

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return round(width * 9 / 16) + self._strip_height()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 360 + self._strip_height())

    def _strip_height(self) -> int:
        return HOUSE_STRIP if self._show.house else 0

    def canvas_rect(self) -> QRectF:
        """The letterboxed 16:9 area inside the widget."""
        area = QRectF(self.rect()).adjusted(0, 0, 0, -self._strip_height())
        if area.width() <= 0 or area.height() <= 0:
            return QRectF()
        width = min(area.width(), area.height() * 16 / 9)
        height = width * 9 / 16
        return QRectF(area.left() + (area.width() - width) / 2, area.top() + (area.height() - height) / 2, width, height)

    def dot_geometry(self, fx: Fixture, canvas: QRectF) -> tuple[QPointF, float]:
        spot = fx.map
        if spot is None:
            return QPointF(), 0.0
        centre = QPointF(canvas.left() + spot.x * canvas.width(), canvas.top() + spot.y * canvas.height())
        return centre, max(3.0, spot.r * canvas.width())

    # ------------------------------------------------------------ painting

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        canvas = self.canvas_rect()
        if canvas.isEmpty():
            painter.end()
            return
        # Only the 16:9 picture is black; the letterbox around it is the panel's own colour
        painter.setClipRect(canvas)
        painter.fillRect(canvas, QColor(theme.WELL))
        if self._picture is not None and not self._picture.isNull():
            painter.setOpacity(0.35)
            painter.drawImage(canvas, self._picture)
            painter.setOpacity(1.0)
        else:
            self._paint_grid(painter, canvas)
        dots = [(fx, *self.dot_geometry(fx, canvas)) for fx in self._show.colour_fixtures() if fx.map is not None]
        for fx, centre, radius in dots:
            self._paint_dot(painter, fx, centre, radius)
        if self._labels:
            self._paint_labels(painter, dots, canvas)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(canvas.adjusted(0.5, 0.5, -0.5, -0.5))
        self._paint_house(painter, canvas)
        painter.end()

    def _paint_grid(self, painter: QPainter, canvas: QRectF) -> None:
        pen = QPen(QColor("#141B28"), 1)
        painter.setPen(pen)
        for i in range(1, 8):
            x = canvas.left() + canvas.width() * i / 8
            painter.drawLine(QPointF(x, canvas.top()), QPointF(x, canvas.bottom()))
        for i in range(1, 4):
            y = canvas.top() + canvas.height() * i / 4.5
            painter.drawLine(QPointF(canvas.left(), y), QPointF(canvas.right(), y))

    def _paint_dot(self, painter: QPainter, fx: Fixture, centre: QPointF, radius: float) -> None:
        rgb = self._colours.get(fx.id, (0.0, 0.0, 0.0))
        colour = rgb_colour(rgb)
        brightness = max(rgb)
        if brightness > 0.002:
            glow = QRadialGradient(centre, radius * 1.5)
            inner = QColor(colour)
            inner.setAlphaF(min(1.0, 0.55 + 0.45 * brightness))
            outer = QColor(colour)
            outer.setAlphaF(0.0)
            glow.setColorAt(0.0, inner)
            glow.setColorAt(0.45, QColor(colour.red(), colour.green(), colour.blue(), round(110 * brightness)))
            glow.setColorAt(1.0, outer)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(centre, radius * 1.5, radius * 1.5)
        core = max(2.5, radius * 0.32)
        painter.setBrush(colour)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG if brightness < 0.15 else theme.TEXT), 1.2))
        painter.drawEllipse(centre, core, core)
        ring = QPen(QColor(theme.BORDER_STRONG), 1, Qt.PenStyle.DotLine)
        painter.setPen(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius, radius)

    def _paint_labels(self, painter: QPainter, dots, canvas: QRectF) -> None:
        font = theme.ui_font(11)
        painter.setFont(font)
        fm = QFontMetrics(font)
        taken: list[QRectF] = []
        for fx, centre, radius in sorted(dots, key=lambda d: (d[1].y(), d[1].x())):
            core = max(2.5, radius * 0.32)
            # long names shorten to about the dot's width; hovering shows the full name
            text = fm.elidedText(fx.name, Qt.TextElideMode.ElideRight, round(max(90.0, radius * 2.2)))
            width = fm.horizontalAdvance(text) + 6
            box = QRectF(centre.x() - width / 2, centre.y() + core + 3, width, fm.height())
            if box.bottom() > canvas.bottom():
                box.moveBottom(centre.y() - core - 3)
            box.moveLeft(min(max(box.left(), canvas.left() + 2), canvas.right() - box.width() - 2))
            if any(box.intersects(other) for other in taken) or box.width() > canvas.width():
                continue  # would collide: hover shows the name instead
            taken.append(box)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))  # keeps the name readable over a bright glow
            painter.drawRoundedRect(box.adjusted(-2, 0, 2, 0), 4, 4)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_house(self, painter: QPainter, canvas: QRectF) -> None:
        if not self._show.house:
            return
        strip = QRectF(canvas.left(), self.height() - HOUSE_STRIP, max(0.0, self.width() - canvas.left()), HOUSE_STRIP)
        font = theme.ui_font(12)
        painter.setFont(font)
        fm = QFontMetrics(font)
        x = strip.left() + 2
        icon_side = HOUSE_STRIP - 10
        for item, text, room in zip(self._show.house, *self.house_texts(strip.width() - 2, fm)):
            level = self._house.get(item.fixture, 0.0)
            on = level > 0.002
            colour = theme.mix(theme.TEXT_3, theme.ACCENT, min(1.0, level)) if on else QColor(theme.TEXT_3)
            icons.draw_bulb(painter, QRectF(x, strip.top() + 5, icon_side, icon_side), colour, on)
            painter.setPen(QColor(theme.TEXT if on else theme.TEXT_3))
            painter.drawText(QRectF(x + icon_side + 6, strip.top(), room + 2, strip.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += icon_side + 6 + room + HOUSE_GAP

    def house_texts(self, width: float, fm: QFontMetrics | None = None) -> tuple[list[str], list[float]]:
        """What each house light's legend says, and the room its text gets, in this width. When the full names
        don't fit, the lights share the width and long names are shortened with "…" (the level always shows)."""
        fm = fm or QFontMetrics(theme.ui_font(12))
        icon_side = HOUSE_STRIP - 10
        house = self._show.house
        levels = [f"{round(self._house.get(item.fixture, 0.0) * 100)}%" for item in house]
        full = [f"{item.label} {level}" for item, level in zip(house, levels)]
        natural = sum(icon_side + 6 + fm.horizontalAdvance(text) for text in full) + HOUSE_GAP * (len(house) - 1)
        if natural <= width:
            return full, [float(fm.horizontalAdvance(text)) for text in full]
        share = max(0.0, (width - HOUSE_GAP * (len(house) - 1)) / max(1, len(house)) - icon_side - 6)
        texts = []
        for item, level in zip(house, levels):
            name = fm.elidedText(item.label, Qt.TextElideMode.ElideRight, max(0, int(share - fm.horizontalAdvance(f" {level}"))))
            texts.append(f"{name} {level}" if name and name != "…" else level)
        return texts, [share] * len(house)

    # ------------------------------------------------------------ hover

    def fixture_at(self, point: QPointF) -> Fixture | None:
        canvas = self.canvas_rect()
        best: tuple[float, Fixture] | None = None
        for fx in self._show.colour_fixtures():
            if fx.map is None:
                continue
            centre, radius = self.dot_geometry(fx, canvas)
            distance = ((centre.x() - point.x()) ** 2 + (centre.y() - point.y()) ** 2) ** 0.5
            if distance <= max(radius, 8) and (best is None or distance < best[0]):
                best = (distance, fx)
        return best[1] if best else None

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            fx = self.fixture_at(event.position() if hasattr(event, "position") else QPointF(event.pos()))
            if fx is not None:
                r, g, b = (to_byte(v) for v in self._colours.get(fx.id, (0.0, 0.0, 0.0)))
                group = f" · {fx.group}" if fx.group else ""
                QToolTip.showText(event.globalPos(), f"{fx.name}{group}\nDMX {fx.channel_range()}\nRed {r}  Green {g}  Blue {b}", self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)
