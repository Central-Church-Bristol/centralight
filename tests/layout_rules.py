"""The rules the layout check applies to a window (see tests/layout_check.py).

A widget can opt out of the overlap rule with the dynamic property
``layoutCheckSkip`` = True (for things that float on purpose, like badges).
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QHeaderView,
    QTreeView,
    QLabel,
    QLineEdit,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from centralight.output import CONNECTED, OutputStatus
from centralight.ui.widgets import STATE_INFO, STATE_OFF, STATE_WARN, ElidedLabel

SKIP = "layoutCheckSkip"
SHRINK_FLAG = 4  # QSizePolicy.PolicyFlag.ShrinkFlag


def describe(widget: QWidget) -> str:
    text = ""
    if isinstance(widget, (QLabel, QAbstractButton)):
        text = widget.text()
        if isinstance(widget, ElidedLabel):
            text = widget.full_text()
    name = widget.objectName()
    label = type(widget).__name__ + (f"#{name}" if name else "")
    if text:
        label += f" {text[:40]!r}"
    return label


def _policy_value(policy: QSizePolicy.Policy) -> int:
    return policy.value if hasattr(policy, "value") else int(policy)


def smart_min(widget: QWidget) -> QSize:
    """The smallest size a layout will normally give this widget (Qt's qSmartMinSize)."""
    hint, min_hint, explicit = widget.sizeHint(), widget.minimumSizeHint(), widget.minimumSize()
    policy = widget.sizePolicy()
    sizes = []
    for pol, h, mh, ex in (
        (policy.horizontalPolicy(), hint.width(), min_hint.width(), explicit.width()),
        (policy.verticalPolicy(), hint.height(), min_hint.height(), explicit.height()),
    ):
        value = 0
        if pol != QSizePolicy.Policy.Ignored:
            value = mh if _policy_value(pol) & SHRINK_FLAG else max(h, mh)
        if ex > 0:
            value = ex
        sizes.append(max(0, value))
    return QSize(sizes[0], sizes[1]).boundedTo(widget.maximumSize())


def in_layout(layout: QLayout | None, widget: QWidget) -> bool:
    if layout is None:
        return False
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is widget:
            return True
        if item.layout() is not None and in_layout(item.layout(), widget):
            return True
    return False


def _is_scroll_content(widget: QWidget) -> bool:
    parent = widget.parentWidget()
    area = parent.parentWidget() if parent is not None else None
    return isinstance(area, QAbstractScrollArea) and parent is area.viewport()


def check(root: QWidget, where: str) -> list[str]:
    problems: list[str] = []
    widgets = [w for w in root.findChildren(QWidget) if w.isVisibleTo(root) and not w.isWindow()]
    for widget in widgets:
        parent = widget.parentWidget()
        if parent is None:
            continue
        rect = widget.geometry()
        # 1. inside its parent (scroll-area content may be bigger: that's what scrolling is for)
        if not _is_scroll_content(widget):
            bounds = QRect(0, 0, parent.width(), parent.height()).adjusted(-1, -1, 1, 1)
            if not bounds.contains(rect):
                problems.append(f"{where}: {describe(widget)} {_r(rect)} sticks out of {describe(parent)} {parent.width()}x{parent.height()}")
        # 2. not squashed below its minimum size
        if in_layout(parent.layout(), widget):
            need = smart_min(widget)
            if widget.width() + 1 < need.width() or widget.height() + 1 < need.height():
                problems.append(f"{where}: {describe(widget)} squashed to {widget.width()}x{widget.height()} (needs {need.width()}x{need.height()})")
        # 3. text not cut off (in lists, tables and trees too)
        problems += _check_text(widget, where)
        if isinstance(widget, QAbstractItemView) and not isinstance(widget, QHeaderView):
            problems += _check_items(widget, where)
        # 4. scroll areas without a horizontal bar must fit their content's width
        if isinstance(widget, QScrollArea) and widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            content = widget.widget()
            if content is not None and content.isVisibleTo(root) and content.width() > widget.viewport().width() + 1:
                problems.append(f"{where}: {describe(widget)} content {content.width()} wider than its view {widget.viewport().width()} (clipped)")
    # 5. siblings don't overlap
    for parent in [root, *widgets]:
        if isinstance(parent, (QStackedWidget, QAbstractScrollArea)):
            continue
        children = [
            c
            for c in parent.children()
            if isinstance(c, QWidget) and c.isVisibleTo(root) and not c.isWindow() and not c.property(SKIP)
        ]
        for i, a in enumerate(children):
            for b in children[i + 1:]:
                overlap = a.geometry().intersected(b.geometry())
                if overlap.width() > 1 and overlap.height() > 1:
                    problems.append(f"{where}: {describe(a)} overlaps {describe(b)} in {describe(parent)}")
    return problems


def _check_text(widget: QWidget, where: str) -> list[str]:
    if isinstance(widget, ElidedLabel):
        if widget.is_elided() and widget.toolTip() != widget.full_text() and not widget.toolTip():
            return [f"{where}: {describe(widget)} is shortened with no tooltip"]
        if widget.is_elided() and widget.width() >= widget.sizeHint().width():
            return [f"{where}: {describe(widget)} is shortened although it has its full width"]
        return []
    if isinstance(widget, QLabel):
        text = widget.text()
        if not text or widget.wordWrap() or (widget.pixmap() is not None and not widget.pixmap().isNull()):
            return []
        if widget.textFormat() != Qt.TextFormat.PlainText and "<" in text:
            return []
        fm = widget.fontMetrics()
        lines = text.split("\n")
        need_w = max(fm.horizontalAdvance(line) for line in lines)
        need_h = fm.height() * len(lines)
        area = widget.contentsRect()
        if need_w > area.width() + 1 or need_h > area.height() + 2:
            return [f"{where}: {describe(widget)} text needs {need_w}x{need_h}, has {area.width()}x{area.height()} (cut off)"]
        return []
    if isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QAbstractSpinBox) and widget.text():
        need = widget.fontMetrics().horizontalAdvance(widget.text()) + 2
        if widget.width() < need:
            return [f"{where}: number box {widget.parentWidget().objectName() or 'spin box'} shows {widget.width()} px of text needing {need} (cut off)"]
        return []
    if isinstance(widget, QAbstractButton) and widget.text():
        if isinstance(widget, QToolButton) and widget.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            return []
        if widget.sizeHint().width() > widget.width() + 1:
            return [f"{where}: {describe(widget)} is {widget.width()} wide but its text needs {widget.sizeHint().width()} (cut off)"]
    return []


def _check_items(view: QAbstractItemView, where: str) -> list[str]:
    """Lists, tables and trees: never scroll sideways, and an item whose text doesn't fit must
    have its full text as a tooltip (Qt shortens it with "…")."""
    problems = []
    bar = view.horizontalScrollBar()
    if view.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff and bar.maximum() > 0:
        problems.append(f"{where}: {describe(view)} scrolls sideways (shorten its text with … and a tooltip instead)")
    model = view.model()
    if model is None:
        return problems
    full_text_tips = bool(view.property("fullTextTooltips"))
    fm = view.fontMetrics()
    viewport = view.viewport().rect()

    def columns(parent) -> int:
        try:
            return model.columnCount(parent)
        except TypeError:  # list models keep it private: they have one column
            return 1

    def visit(parent, depth: int) -> None:
        for row in range(min(model.rowCount(parent), 300)):
            for column in range(min(columns(parent), 20)):
                index = model.index(row, column, parent)
                rect = view.visualRect(index)
                if rect.isEmpty() or not viewport.intersects(rect):
                    continue
                text = index.data(Qt.ItemDataRole.DisplayRole)
                if isinstance(text, str) and text:
                    lines = text.split("\n")
                    extra = 28 if index.flags() & Qt.ItemFlag.ItemIsUserCheckable else 0
                    need_w = max(fm.horizontalAdvance(line) for line in lines) + 8 + extra
                    need_h = fm.height() * len(lines)
                    first = lines[0][:40]
                    if need_h > rect.height() + 2:
                        problems.append(f"{where}: {describe(view)} item {first!r} has {len(lines)} lines but room for fewer")
                    elif need_w > rect.width() + 1 and not index.data(Qt.ItemDataRole.ToolTipRole) and not full_text_tips:
                        problems.append(f"{where}: {describe(view)} item {first!r} is cut off with no tooltip")
                if column == 0 and isinstance(view, QTreeView) and model.hasChildren(index) and view.isExpanded(index) and depth < 4:
                    visit(index, depth + 1)

    visit(view.rootIndex(), 0)
    return problems


def check_header(window, where: str) -> list[str]:
    header = window.header
    problems = []
    if header.one_row == header.row2.isVisible():
        problems.append(f"{where}: second header row visible={header.row2.isVisible()} but one_row={header.one_row}")
    tabs = list(header.tabs.values())
    for i, a in enumerate(tabs):
        for b in tabs[i + 1:]:
            if a.geometry().intersects(b.geometry()):
                problems.append(f"{where}: tabs {a.text()!r} and {b.text()!r} overlap")
    if header.height() > 140:
        problems.append(f"{where}: header is {header.height()} px tall")
    return problems


def show_all_chips(window, usb: bool = True) -> None:
    """Every chip on, with realistic text, so the header is at its fullest."""
    if usb:
        window.engine.fake_output.status_value = OutputStatus(kind="usb", state=CONNECTED, label="COM3", rate=40.0, sent=1234)
        window.engine.tick()
    window.header.set_chip("ndi", STATE_WARN, "NDI · no signal", "Media Background Pane isn't sending")
    window.header.set_chip("audio", STATE_OFF, "Audio off", "No audio source")
    window.header.set_chip("remote", STATE_INFO, "Remote :8746", "Stream Deck API")
    window.bridge.poll()


def _r(rect: QRect) -> str:
    return f"({rect.x()},{rect.y()} {rect.width()}x{rect.height()})"
