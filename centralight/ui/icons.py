"""Icons.

- app_icon(): the CentraLight logo (three circles) for the window, taskbar and tray,
  from Assets\\CentraLight Logo 1024.png, or drawn here if that file is missing.
- line_icon(): small icons drawn in code, so they stay sharp at any display scaling.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QImage, QPainter, QPainterPath, QPen, QPixmap

from ..paths import logo_path
from . import theme

DrawFn = Callable[[QPainter, QRectF, QColor, bool], None]

_APP_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw_logo(painter: QPainter, rect: QRectF) -> None:
    """The three logo circles (coral, lime, sky) inside rect."""
    size = min(rect.width(), rect.height())
    r = size * 0.275
    cx, cy = rect.center().x(), rect.center().y()
    centres = (
        (QPointF(cx, cy - size * 0.17), theme.LOGO_CORAL),
        (QPointF(cx - size * 0.18, cy + size * 0.16), theme.LOGO_LIME),
        (QPointF(cx + size * 0.18, cy + size * 0.16), theme.LOGO_SKY),
    )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
    for centre, colour in centres:
        painter.setBrush(QColor(colour))
        painter.drawEllipse(centre, r, r)
    painter.restore()


def logo_pixmap(size: int, dpr: float = 1.0) -> QPixmap:
    side = max(1, round(size * dpr))
    image = QImage(str(logo_path()))
    if not image.isNull():
        pix = QPixmap.fromImage(
            image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
    else:
        pix = QPixmap(side, side)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        draw_logo(painter, QRectF(0, 0, side, side))
        painter.end()
    pix.setDevicePixelRatio(dpr)
    return pix


def app_icon() -> QIcon:
    icon = QIcon()
    for size in _APP_ICON_SIZES:
        icon.addPixmap(logo_pixmap(size))
    return icon


class _PaintedEngine(QIconEngine):
    def __init__(self, draw: DrawFn, colour: str, checked_colour: str | None) -> None:
        super().__init__()
        self._draw = draw
        self._colour = colour
        self._checked_colour = checked_colour or colour

    def _colour_for(self, mode: QIcon.Mode, state: QIcon.State) -> QColor:
        if mode == QIcon.Mode.Disabled:
            return QColor(theme.TEXT_3)
        return QColor(self._checked_colour if state == QIcon.State.On else self._colour)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw(painter, QRectF(rect), self._colour_for(mode, state), state == QIcon.State.On)
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        pix = QPixmap(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)))
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        self.paint(painter, QRect(0, 0, pix.width(), pix.height()), mode, state)
        painter.end()
        pix.setDevicePixelRatio(scale)
        return pix

    def clone(self) -> QIconEngine:
        return _PaintedEngine(self._draw, self._colour, self._checked_colour)


def line_icon(draw: DrawFn, colour: str = theme.TEXT_2, checked_colour: str | None = None) -> QIcon:
    return QIcon(_PaintedEngine(draw, colour, checked_colour))


def _pen(colour: QColor, rect: QRectF, width: float = 0.085) -> QPen:
    pen = QPen(colour, max(1.0, min(rect.width(), rect.height()) * width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _square(rect: QRectF, inset: float = 0.12) -> QRectF:
    side = min(rect.width(), rect.height())
    pad = side * inset
    return QRectF(rect.center().x() - side / 2 + pad, rect.center().y() - side / 2 + pad, side - 2 * pad, side - 2 * pad)


def draw_lock(painter: QPainter, rect: QRectF, colour: QColor, on: bool) -> None:
    """A padlock: closed when on (locked), open when off."""
    r = _square(rect)
    painter.setPen(_pen(colour, r))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body = QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.45, r.width() * 0.72, r.height() * 0.52)
    painter.drawRoundedRect(body, r.width() * 0.08, r.width() * 0.08)
    shackle = QPainterPath()
    left = r.left() + r.width() * 0.3
    right = r.left() + r.width() * 0.7
    top = r.top() + r.height() * 0.06
    bottom = body.top()
    if on:
        shackle.moveTo(left, bottom)
        shackle.lineTo(left, top + r.width() * 0.2)
        shackle.arcTo(QRectF(left, top, right - left, (right - left)), 180, -180)
        shackle.lineTo(right, bottom)
    else:
        shackle.moveTo(left, bottom)
        shackle.lineTo(left, top + r.width() * 0.2)
        shackle.arcTo(QRectF(left, top, right - left, (right - left)), 180, -180)
        shackle.lineTo(right, top + r.width() * 0.32)
    painter.drawPath(shackle)
    painter.setBrush(colour)
    painter.setPen(Qt.PenStyle.NoPen)
    dot = r.width() * 0.07
    painter.drawEllipse(QPointF(body.center().x(), body.center().y()), dot, dot)


def draw_gear(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.1)
    c = r.center()
    outer = r.width() * 0.48
    inner = r.width() * 0.36
    teeth = 8
    path = QPainterPath()
    for i in range(teeth * 2):
        angle_a = math.pi * 2 * (i / (teeth * 2))
        angle_b = math.pi * 2 * ((i + 1) / (teeth * 2))
        radius = outer if i % 2 == 0 else inner
        a = QPointF(c.x() + math.cos(angle_a) * radius, c.y() + math.sin(angle_a) * radius)
        b = QPointF(c.x() + math.cos(angle_b) * radius, c.y() + math.sin(angle_b) * radius)
        if i == 0:
            path.moveTo(a)
        else:
            path.lineTo(a)
        path.lineTo(b)
    path.closeSubpath()
    painter.setPen(_pen(colour, r, 0.07))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.drawEllipse(c, r.width() * 0.14, r.width() * 0.14)


def draw_pencil(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.16)
    painter.setPen(_pen(colour, r))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.1)
    path.lineTo(r.left() + r.width() * 0.16, r.bottom() - r.height() * 0.34)
    path.lineTo(r.right() - r.width() * 0.26, r.top() + r.height() * 0.08)
    path.lineTo(r.right() - r.width() * 0.08, r.top() + r.height() * 0.26)
    path.lineTo(r.left() + r.width() * 0.34, r.bottom() - r.height() * 0.16)
    path.closeSubpath()
    painter.drawPath(path)


def draw_sparkle(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.1)

    def star(cx: float, cy: float, size: float) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(cx, cy - size)
        path.quadTo(cx, cy, cx + size, cy)
        path.quadTo(cx, cy, cx, cy + size)
        path.quadTo(cx, cy, cx - size, cy)
        path.quadTo(cx, cy, cx, cy - size)
        return path

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawPath(star(r.left() + r.width() * 0.42, r.top() + r.height() * 0.56, r.width() * 0.4))
    painter.drawPath(star(r.right() - r.width() * 0.16, r.top() + r.height() * 0.18, r.width() * 0.17))


def draw_plus(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.2)
    painter.setPen(_pen(colour, r, 0.11))
    painter.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()))
    painter.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()))


def draw_clock(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.12)
    painter.setPen(_pen(colour, r))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(r)
    c = r.center()
    painter.drawLine(c, QPointF(c.x(), r.top() + r.height() * 0.22))
    painter.drawLine(c, QPointF(c.x() + r.width() * 0.22, c.y()))


def draw_wave(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    r = _square(rect, 0.12)
    painter.setPen(_pen(colour, r))
    heights = (0.3, 0.7, 1.0, 0.55, 0.8, 0.35)
    step = r.width() / (len(heights) - 1)
    for i, h in enumerate(heights):
        x = r.left() + i * step
        half = r.height() * h / 2
        painter.drawLine(QPointF(x, r.center().y() - half), QPointF(x, r.center().y() + half))


def draw_bulb(painter: QPainter, rect: QRectF, colour: QColor, on: bool) -> None:
    r = _square(rect, 0.08)
    painter.setPen(_pen(colour, r, 0.07))
    painter.setBrush(colour if on else Qt.BrushStyle.NoBrush)
    head = QRectF(r.left() + r.width() * 0.18, r.top(), r.width() * 0.64, r.height() * 0.64)
    painter.drawEllipse(head)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    base_top = head.bottom() - r.height() * 0.04
    painter.drawLine(QPointF(r.left() + r.width() * 0.36, base_top), QPointF(r.left() + r.width() * 0.36, r.bottom() - r.height() * 0.12))
    painter.drawLine(QPointF(r.right() - r.width() * 0.36, base_top), QPointF(r.right() - r.width() * 0.36, r.bottom() - r.height() * 0.12))
    painter.drawLine(QPointF(r.left() + r.width() * 0.36, r.bottom() - r.height() * 0.06), QPointF(r.right() - r.width() * 0.36, r.bottom() - r.height() * 0.06))


def _curved_arrow(painter: QPainter, rect: QRectF, colour: QColor, mirror: bool) -> None:
    """A hooked arrow pointing left (undo) or, mirrored, right (redo)."""
    r = _square(rect, 0.16)
    painter.save()
    if mirror:
        painter.translate(r.center().x() * 2, 0)
        painter.scale(-1, 1)
    painter.setPen(_pen(colour, r, 0.1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    start = QPointF(r.left() + r.width() * 0.1, r.top() + r.height() * 0.38)
    path.moveTo(start)
    path.lineTo(r.left() + r.width() * 0.62, r.top() + r.height() * 0.38)
    path.cubicTo(
        QPointF(r.right() + r.width() * 0.08, r.top() + r.height() * 0.38),
        QPointF(r.right() + r.width() * 0.08, r.bottom() - r.height() * 0.06),
        QPointF(r.left() + r.width() * 0.56, r.bottom() - r.height() * 0.06),
    )
    painter.drawPath(path)
    head = r.width() * 0.22
    painter.drawLine(start, QPointF(start.x() + head, start.y() - head))
    painter.drawLine(start, QPointF(start.x() + head, start.y() + head))
    painter.restore()


def draw_undo(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    _curved_arrow(painter, rect, colour, mirror=False)


def draw_redo(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    _curved_arrow(painter, rect, colour, mirror=True)


def draw_history(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    """A list of steps."""
    r = _square(rect, 0.16)
    painter.setPen(_pen(colour, r, 0.1))
    for i in range(3):
        y = r.top() + r.height() * (0.18 + 0.32 * i)
        painter.drawPoint(QPointF(r.left() + r.width() * 0.06, y))
        painter.drawLine(QPointF(r.left() + r.width() * 0.3, y), QPointF(r.right(), y))


def draw_warning(painter: QPainter, rect: QRectF, colour: QColor, _on: bool) -> None:
    """A triangle with an exclamation mark."""
    r = _square(rect, 0.1)
    path = QPainterPath()
    path.moveTo(r.center().x(), r.top())
    path.lineTo(r.right(), r.bottom())
    path.lineTo(r.left(), r.bottom())
    path.closeSubpath()
    painter.setPen(_pen(colour, r, 0.09))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    x = r.center().x()
    painter.drawLine(QPointF(x, r.top() + r.height() * 0.38), QPointF(x, r.top() + r.height() * 0.68))
    painter.drawPoint(QPointF(x, r.top() + r.height() * 0.84))
