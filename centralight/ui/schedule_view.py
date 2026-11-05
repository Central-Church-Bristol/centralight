"""The show schedule on the Automation tab: a read-only week of the shows the global automations load.

Monday to Sunday across and the hours down (scaled to fit), with a coloured block for each show (a show keeps its
colour), worked out by schedule.py from the "Load a show" actions and their time, day, date and sunrise / sunset
conditions:
- Hatched: it also needs something a calendar can't show (hover to see what). Grey with a dashed edge: there's no
  show by that name. A red edge and "!": another automation loads a different show at the same minute.
- A thin line with a ▸: a show loaded "when it stops matching". ▲ / ▼: it carries on from the week before / into
  the week after.
- A white line marks now (this week only). ‹ and › show other weeks (Christmas, say); This week comes back.
- Clicking a block (or a line under the grid) asks for its automation (automation_clicked).
- Under the grid: the "Load a show" triggers that can't go on a calendar ("When CentraLight starts → Load Morning").
Nothing here can be changed. While it's on screen, it looks at the clock once a minute to move the now line.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..automations import DAY_NAMES
from ..model import Automation, Show
from ..schedule import Block, Unplaced, describe_block, monday_of, week_schedule
from . import theme
from .widgets import ElidedLabel

DAYS_SHOWN = 7
SHOW_COLOURS = (theme.ACCENT, theme.OVERLAY, theme.OK, theme.WARN, theme.LOGO_SKY, theme.LIVE)  # (red means a clash)
NO_SHOW_COLOUR = theme.TEXT_3  # an action naming a show there isn't
GRID_PX = 11  # text size in the grid
MIN_COLUMN = 18.0  # px per day, at the narrowest
MIN_HOUR = 3.0  # px per hour, at the shortest
COMFY_HOUR = 18.0  # px per hour it would like
MIN_BLOCK = 4.0  # px: the shortest a block is drawn (a moment, say), so it can be seen and clicked
LINE_REACH = 4.0  # px above and below a "when it stops" line that still count as on it
WIDE = 420  # px: from this wide the hours say "06:00" (narrower: "06")
UNPLACED_ROWS = 3  # lines in the list under the grid before it scrolls
TICK_MS = 60_000  # the now line moves once a minute


def show_colours(keys: Iterable[str]) -> dict[str, str]:
    """Each show's colour: the theme's bright colours in turn, in show id order, so a show keeps its colour from week
    to week (and when automations change)."""
    return {key: SHOW_COLOURS[index % len(SHOW_COLOURS)] for index, key in enumerate(sorted(set(keys)))}


def _show_key(item: Block | Unplaced) -> str:
    return item.show_id or item.show.casefold()


def week_title(week: date, days: int = DAYS_SHOWN) -> str:
    """'2 – 8 Nov 2026', '26 Oct – 1 Nov 2026' or '28 Dec 2026 – 3 Jan 2027'."""
    end = week + timedelta(days=days - 1)
    if week.year != end.year:
        return f"{week.day} {week:%b %Y} – {end.day} {end:%b %Y}"
    if week.month != end.month:
        return f"{week.day} {week:%b} – {end.day} {end:%b %Y}"
    return f"{week.day} – {end.day} {end:%b %Y}"


class WeekGrid(QWidget):
    """The painted week: the days across, the hours down, the blocks, the "when it stops" lines and the now line."""

    def __init__(self, view: ScheduleView) -> None:
        super().__init__(view)
        self.view = view
        self.setObjectName("ScheduleGrid")
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._font = theme.ui_font(GRID_PX)
        self._bold = theme.ui_font(GRID_PX, QFont.Weight.DemiBold)
        self._placed: tuple[list, list] | None = None  # (block pieces, "when it stops" lines), worked out when needed
        self._pointing = False

    # ------------------------------------------------------------ geometry

    def gutter(self) -> float:
        """The hours' column on the left."""
        return float(QFontMetrics(self._font).horizontalAdvance("00:00" if self.width() >= WIDE else "00") + 8)

    def header_height(self) -> float:
        return float(2 * QFontMetrics(self._font).height() + 6)

    def column_width(self) -> float:
        return max(1.0, (self.width() - self.gutter() - 2) / DAYS_SHOWN)

    def hour_height(self) -> float:
        return max(0.5, (self.height() - self.header_height() - 2) / 24)

    def x_of(self, day: int) -> float:
        return self.gutter() + day * self.column_width()

    def y_of(self, minutes: float) -> float:
        """Where a time of day is (minutes after midnight)."""
        return self.header_height() + minutes / 60 * self.hour_height()

    def now_y(self) -> float | None:
        """Where the now line is, or None when now isn't in the week shown."""
        now = self.view.now()
        if not 0 <= (now.date() - self.view.week_start).days < DAYS_SHOWN:
            return None
        return self.y_of(now.hour * 60 + now.minute + now.second / 60)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._font)
        return QSize(fm.horizontalAdvance("00:00") + 10 + DAYS_SHOWN * 64, round(self.header_height() + 24 * COMFY_HOUR))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._font)
        return QSize(round(fm.horizontalAdvance("00") + 10 + DAYS_SHOWN * MIN_COLUMN), round(self.header_height() + 24 * MIN_HOUR + 2))

    # ------------------------------------------------------------ where things go

    def invalidate(self) -> None:
        """The blocks or the week changed: place them again."""
        self._placed = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._placed = None
        super().resizeEvent(event)

    def placed(self) -> tuple[list[tuple[Block, QRectF, bool, bool]], list[tuple[Block, QRectF]]]:
        """Each block's pieces (one per day it's on, side by side where they overlap) as (block, rect, carries on from
        the week before, carries on into the week after), and each "when it stops" line as (block, rect)."""
        if self._placed is None:
            self._placed = self._place()
        return self._placed

    def _place(self) -> tuple[list, list]:
        first = datetime.combine(self.view.week_start, time())
        column = self.column_width()
        by_day: list[list[list]] = [[] for _ in range(DAYS_SHOWN)]
        lines = []
        for block in self.view.blocks:
            for day in range(DAYS_SHOWN):
                day_start = first + timedelta(days=day)
                day_end = day_start + timedelta(days=1)
                if block.start == block.end:  # a moment
                    if not day_start <= block.start < day_end:
                        continue
                    top = self.y_of((block.start - day_start).total_seconds() / 60)
                    if block.on_end:
                        lines.append((block, QRectF(self.x_of(day) + 1, top - 1, column - 2, 2)))
                        continue
                    bottom = top + MIN_BLOCK
                elif block.start < day_end and block.end > day_start:
                    top = self.y_of((max(block.start, day_start) - day_start).total_seconds() / 60)
                    bottom = max(top + MIN_BLOCK, self.y_of((min(block.end, day_end) - day_start).total_seconds() / 60))
                else:
                    continue
                up = block.continues_before and day == 0 and block.start <= day_start
                down = block.continues_after and day == DAYS_SHOWN - 1 and block.end >= day_end
                by_day[day].append([block, top, bottom, up, down, 0])
        pieces = []
        for day, items in enumerate(by_day):
            items.sort(key=lambda item: (item[1], -item[2]))
            group: list[list] = []
            reach = float("-inf")
            for item in items:
                if group and item[1] >= reach - 0.01:  # nothing above reaches down to it: a new group
                    pieces += self._side_by_side(group, day, column)
                    group, reach = [], float("-inf")
                group.append(item)
                reach = max(reach, item[2])
            pieces += self._side_by_side(group, day, column)
        return pieces, lines

    def _side_by_side(self, group: list[list], day: int, column: float) -> list[tuple[Block, QRectF, bool, bool]]:
        """Blocks that overlap share their day's column: each takes the first lane that's free at its top."""
        lanes: list[float] = []
        for item in group:
            for lane, bottom in enumerate(lanes):
                if item[1] >= bottom - 0.01:
                    lanes[lane], item[5] = item[2], lane
                    break
            else:
                lanes.append(item[2])
                item[5] = len(lanes) - 1
        width = column / max(1, len(lanes))
        left = self.x_of(day)
        return [(block, QRectF(left + lane * width + 1, top, max(1.0, width - 2), bottom - top), up, down)
                for block, top, bottom, up, down, lane in group]

    def block_rects(self) -> list[tuple[Block, QRectF]]:
        """Where each block is drawn (a piece per day it's on) and each "when it stops" line, in this widget."""
        pieces, lines = self.placed()
        return [(block, rect) for block, rect, _up, _down in pieces] + list(lines)

    def block_at(self, point: QPointF) -> Block | None:
        pieces, lines = self.placed()
        for block, rect in reversed(lines):  # (thin, on top: a little room around them counts)
            if rect.adjusted(0, -LINE_REACH, 0, LINE_REACH).contains(point):
                return block
        for block, rect, _up, _down in reversed(pieces):
            if rect.contains(point):
                return block
        return None

    # ------------------------------------------------------------ painting

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fm = QFontMetrics(self._font)
        first, today = self.view.week_start, self.view.now().date()
        column, top, bottom = self.column_width(), self.header_height(), self.y_of(24 * 60)
        left, right = self.gutter(), self.x_of(DAYS_SHOWN)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QColor(theme.WINDOW))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        if 0 <= (today - first).days < DAYS_SHOWN:  # today's column
            tint = QColor(theme.ACCENT_SOFT)
            tint.setAlpha(150)
            painter.fillRect(QRectF(self.x_of((today - first).days), top, column, bottom - top), tint)
        # the hours: a line and its time every few hours (as many as fit), fainter lines between
        step = next((s for s in (1, 2, 3, 4, 6, 12) if s * self.hour_height() >= fm.height()), 24)
        faint = QColor(theme.BORDER)
        faint.setAlpha(110)
        painter.setFont(self._font)
        for hour in range(25):
            y = self.y_of(hour * 60)
            if hour % step == 0:
                painter.setPen(QPen(QColor(theme.BORDER), 1))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
                if hour < 24 and y + fm.height() <= bottom + 1:
                    painter.setPen(QColor(theme.TEXT_3))
                    painter.drawText(QRectF(2, y + 1, left - 6, fm.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                                     f"{hour:02d}:00" if self.width() >= WIDE else f"{hour:02d}")
            elif self.hour_height() >= 8:
                painter.setPen(QPen(faint, 1))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
        # the days: a line between each, and its name and date on top ("M" and "2" when "Mon" or "2 Nov" won't fit)
        bold = QFontMetrics(self._bold)
        dates = [first + timedelta(days=day) for day in range(DAYS_SHOWN)]
        short_names = max(bold.horizontalAdvance(name) for name in DAY_NAMES) > column - 4
        short_dates = max(bold.horizontalAdvance(f"{when.day} {when:%b}") for when in dates) > column - 4
        for day, when in enumerate(dates):
            x = self.x_of(day)
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.drawLine(QPointF(x, 4 if day else top), QPointF(x, bottom))
            name = DAY_NAMES[when.weekday()][:1 if short_names else 3]
            number = str(when.day) if short_dates else f"{when.day} {when:%b}"
            painter.setFont(self._bold if when == today else self._font)
            painter.setPen(QColor(theme.ACCENT if when == today else theme.TEXT_2))
            painter.drawText(QRectF(x, 3, column, fm.height()), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)
            painter.drawText(QRectF(x, 3 + fm.height(), column, fm.height()), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, number)
        pieces, lines = self.placed()
        painter.save()
        painter.setClipRect(QRectF(left, top, right - left, bottom - top + 1))
        for block, rect, up, down in pieces:
            self._paint_block(painter, block, rect, up, down)
        for block, rect in lines:
            self._paint_line(painter, block, rect)
        y = self.now_y()
        if y is not None:  # now: across today's column, with a dot
            x = self.x_of((today - first).days)
            painter.setPen(QPen(QColor(theme.TEXT), 2))
            painter.drawLine(QPointF(x, y), QPointF(x + column, y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.TEXT))
            painter.drawEllipse(QPointF(x + 1, y), 3.5, 3.5)
        painter.restore()
        if not pieces and not lines:
            painter.setFont(self._font)
            painter.setPen(QColor(theme.TEXT_3))
            area = QRectF(left, top, right - left, bottom - top)
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter,
                             fm.elidedText("No show loads at a set time this week", Qt.TextElideMode.ElideRight, max(0, int(area.width()) - 8)))
        painter.end()

    def _paint_block(self, painter: QPainter, block: Block, rect: QRectF, up: bool, down: bool) -> None:
        colour = QColor(self.view.colour_of(block))
        radius = min(4.0, rect.width() / 2, rect.height() / 2)
        missing = block.missing
        if missing:  # there's no such show: grey, with a dashed edge
            fill, ink = QColor(theme.CARD_HOVER), QColor(theme.TEXT_2)
        elif block.default:  # the default show, between the automations' shows (M14): lighter
            fill, ink = theme.mix(theme.WINDOW, colour.name(), 0.35), QColor(theme.TEXT_2)
        elif block.conditional:  # it might not happen: hatched
            fill, ink = theme.mix(theme.WINDOW, colour.name(), 0.3), QColor(theme.TEXT)
        else:
            fill, ink = colour, QColor(theme.ACCENT_TEXT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
        if block.conditional and not missing:
            hatch = QColor(colour)
            hatch.setAlpha(150)
            painter.setBrush(QBrush(hatch, Qt.BrushStyle.BDiagPattern))
            painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if block.clash:
            painter.setPen(QPen(QColor(theme.DANGER), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
        elif missing or block.conditional:
            painter.setPen(QPen(QColor(theme.TEXT_3) if missing else colour, 1, Qt.PenStyle.DashLine if missing else Qt.PenStyle.SolidLine))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        inner = rect.adjusted(4, 2, -4, -2)
        badge = block.clash and rect.width() >= 16 and rect.height() >= 14
        if badge:  # a red "!" in the corner
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.DANGER))
            centre = QPointF(rect.right() - 8, rect.top() + 8)
            painter.drawEllipse(centre, 6, 6)
            painter.setPen(QColor(theme.TEXT))
            painter.setFont(self._bold)
            painter.drawText(QRectF(centre.x() - 6, centre.y() - 7, 12, 14), Qt.AlignmentFlag.AlignCenter, "!")
            inner.setRight(inner.right() - 12)
        bold, plain = QFontMetrics(self._bold), QFontMetrics(self._font)
        if inner.width() >= 12 and inner.height() >= bold.height() - 3:  # its show, and its times if there's room
            painter.setPen(ink)
            painter.setFont(self._bold)
            painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), bold.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             bold.elidedText(block.show_label, Qt.TextElideMode.ElideRight, int(inner.width())))
            if inner.height() >= bold.height() + plain.height():
                painter.setFont(self._font)
                times = f"{block.start:%H:%M}" if block.moment else f"{block.start:%H:%M}–{block.end:%H:%M}"
                if block.continues_before or block.continues_after:
                    times = ("…" if block.continues_before else f"{block.start:%H:%M}") + "–" + ("…" if block.continues_after else f"{block.end:%H:%M}")
                painter.drawText(QRectF(inner.left(), inner.top() + bold.height(), inner.width(), plain.height()),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 plain.elidedText(times, Qt.TextElideMode.ElideRight, int(inner.width())))
        if (up or down) and rect.height() >= 10 and rect.width() >= 10:  # it carries on from / into another week
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            middle = rect.center().x()
            if up:
                painter.drawPolygon(QPolygonF([QPointF(middle - 4, rect.top() + 6), QPointF(middle + 4, rect.top() + 6), QPointF(middle, rect.top() + 2)]))
            if down:
                painter.drawPolygon(QPolygonF([QPointF(middle - 4, rect.bottom() - 6), QPointF(middle + 4, rect.bottom() - 6), QPointF(middle, rect.bottom() - 2)]))

    def _paint_line(self, painter: QPainter, block: Block, rect: QRectF) -> None:
        """A show loaded "when it stops matching": a thin line across the day, with a ▸ at its start."""
        colour = QColor(theme.DANGER if block.clash else self.view.colour_of(block))
        y = rect.center().y()
        dashed = bool(block.conditional) or block.missing
        painter.setPen(QPen(colour, 2, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
        painter.drawLine(QPointF(rect.left() + 5, y), QPointF(rect.right(), y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF([QPointF(rect.left(), y - 4), QPointF(rect.left() + 6, y), QPointF(rect.left(), y + 4)]))

    # ------------------------------------------------------------ the mouse

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            block = self.block_at(QPointF(event.pos()))
            if block is not None:
                QToolTip.showText(event.globalPos(), describe_block(block), self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        under = self.block_at(event.position())
        pointing = under is not None and not under.default  # (the default show has no automation to open)
        if pointing != self._pointing:
            self._pointing = pointing
            self.setCursor(Qt.CursorShape.PointingHandCursor if pointing else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        block = self.block_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if block is not None and not block.default:
            self.view.automation_clicked.emit(block.automation_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ScheduleView(QWidget):
    """The week of shows, with ‹ This week › on top and the triggers that can't go on a calendar underneath."""

    automation_clicked = Signal(str)  # an automation's id: its block (or its line in the list) was clicked

    def __init__(self, parent: QWidget | None = None, local_time: Callable[[float], datetime] = datetime.fromtimestamp) -> None:
        """local_time: the PC's clock (seconds since 1970 -> local time), for the night the clocks go forward."""
        super().__init__(parent)
        self.setObjectName("ScheduleView")
        self._local_time = local_time
        self._fixed_now: datetime | None = None
        self._automations: list[Automation] = []
        self._shows: dict[str, str] = {}
        self._default_show = ""  # (Settings' default show: it fills the gaps, M14)
        self._colours: dict[str, str] = {}
        self._week = monday_of(self.now().date())
        self._following = True  # showing this week, so it moves on to the next one with the clock
        self.blocks: list[Block] = []
        self.unplaced: list[Unplaced] = []

        self.previous_button = self._button("‹", "The week before")
        self.previous_button.clicked.connect(self.previous_week)
        self.this_week_button = self._button("This week", "Back to this week")
        self.this_week_button.clicked.connect(self.this_week)
        self.next_button = self._button("›", "The week after (date ranges such as Christmas show in their week)")
        self.next_button.clicked.connect(self.next_week)
        self.title = ElidedLabel("")
        self.title.setObjectName("Caption")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        for button in (self.previous_button, self.this_week_button, self.next_button):
            top.addWidget(button)
        top.addSpacing(4)
        top.addWidget(self.title, 1)

        self.grid = WeekGrid(self)
        self.unplaced_title = ElidedLabel("Not on the calendar")
        self.unplaced_title.setObjectName("Hint")
        self.unplaced_title.setToolTip("These automations load a show whenever their trigger happens, not at a set time")
        self.unplaced_list = QListWidget()
        self.unplaced_list.setObjectName("ScheduleUnplaced")
        self.unplaced_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.unplaced_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.unplaced_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.unplaced_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.unplaced_list.setUniformItemSizes(True)
        self.unplaced_list.setIconSize(QSize(10, 10))
        self.unplaced_list.itemClicked.connect(self._unplaced_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.unplaced_title)
        layout.addWidget(self.unplaced_list)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._recompute()

    @staticmethod
    def _button(text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("ToolToggle")
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    # ------------------------------------------------------------ what it shows

    @property
    def week_start(self) -> date:
        """The Monday of the week shown."""
        return self._week

    def now(self) -> datetime:
        return self._fixed_now if self._fixed_now is not None else datetime.now()

    def set_now(self, now: datetime | None) -> None:
        """Fix the clock (for tests); None goes back to the PC's clock."""
        self._fixed_now = now
        self._tick()

    def set_data(self, automations: Show | Iterable[Automation], shows: Mapping[str, str] | None,
                 default_show: str = "") -> None:
        """The global automations (their Show, or a list in run order), the shows there are (id -> name), and the
        default show's id (M14: it fills the gaps, lighter). It's cheap to call again: nothing is worked out unless
        they changed."""
        ordered = automations.ordered_automations() if isinstance(automations, Show) else list(automations)
        names = dict(shows or {})
        if ordered == self._automations and names == self._shows and default_show == self._default_show:
            return
        self._automations, self._shows, self._default_show = ordered, names, default_show
        self._recompute()

    def colour_of(self, item: Block | Unplaced) -> str:
        """The colour of a block's (or a list line's) show: grey for a show there isn't."""
        return NO_SHOW_COLOUR if item.missing else self._colours.get(_show_key(item), NO_SHOW_COLOUR)

    def block_rects(self) -> list[tuple[Block, QRectF]]:
        """Where each block and "when it stops" line is drawn, in the grid (self.grid)."""
        return self.grid.block_rects()

    # ------------------------------------------------------------ weeks

    def show_week(self, day: date) -> None:
        """Show the week `day` is in."""
        week = monday_of(day)
        self._following = week == monday_of(self.now().date())
        if week != self._week:
            self._week = week
            self._recompute()
        self._show_title()

    def previous_week(self) -> None:
        self.show_week(self._week - timedelta(days=7))

    def next_week(self) -> None:
        self.show_week(self._week + timedelta(days=7))

    def this_week(self) -> None:
        self.show_week(self.now().date())

    def _tick(self) -> None:
        """Once a minute (and when the clock is set): follow this week into the next, and move the now line."""
        this_week = monday_of(self.now().date())
        if self._following and this_week != self._week:
            self._week = this_week
            self._recompute()
        self._show_title()
        self.grid.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._tick()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()  # (nothing to move while it's not on screen)
        super().hideEvent(event)

    # ------------------------------------------------------------ filling it in

    def _recompute(self) -> None:
        self.blocks, self.unplaced = week_schedule(self._automations, self._shows, self._week, DAYS_SHOWN, self._local_time,
                                                   self._default_show)
        # (if the shows couldn't be listed, each show named by an action still gets its own colour)
        self._colours = show_colours(self._shows or [_show_key(item) for item in [*self.blocks, *self.unplaced]])
        self._fill_unplaced()
        self._show_title()
        self.grid.invalidate()

    def _show_title(self) -> None:
        self.title.setText(week_title(self._week))
        self.this_week_button.setEnabled(self._week != monday_of(self.now().date()))

    def _fill_unplaced(self) -> None:
        view = self.unplaced_list
        view.clear()
        for entry in self.unplaced:
            text = f"{entry.trigger} → Load {entry.show_label}"
            tip = f"{text}\nAutomation: {entry.automation_name}"
            if entry.missing:
                tip += f"\nThere's no show called “{entry.show}”, so nothing loads"
            item = QListWidgetItem(self._swatch(self.colour_of(entry)), text)
            item.setToolTip(tip + "\nClick to open the automation")
            item.setData(Qt.ItemDataRole.UserRole, entry.automation_id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # (it can be clicked, never changed)
            view.addItem(item)
        shown = bool(self.unplaced)
        self.unplaced_title.setVisible(shown)
        view.setVisible(shown)
        if shown:  # up to three lines (it scrolls past that), and down to one when there's little room
            view.ensurePolished()
            row = max(view.sizeHintForRow(0), view.fontMetrics().height() + 4)
            frame = 2 * view.frameWidth() + 2
            view.setMinimumHeight(row + frame)
            view.setMaximumHeight(min(UNPLACED_ROWS, view.count()) * row + frame)

    @staticmethod
    def _swatch(colour: str) -> QIcon:
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(0, 0, 10, 10), 2, 2)
        painter.end()
        return QIcon(pixmap)

    def _unplaced_clicked(self, item: QListWidgetItem) -> None:
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        if automation_id:
            self.automation_clicked.emit(str(automation_id))
