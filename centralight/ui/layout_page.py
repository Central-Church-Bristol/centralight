"""The Layout page: where the lights are, and which light is which.

- Map: drag a light's dot to where it is in the room (the map is 16:9, like the screen).
  Click a dot to flash that light. A light is just its dot: how much of a picture it
  averages is each Image, Video or NDI element's Area, in the Editor. A photo of the room
  can go behind the map.
- Saved layouts: "Layout: Main ▾" at the top switches between named arrangements of the
  map (positions and the room photo); Save as, Rename and Delete are there too.
- Undo / Redo / History at the top right cover everything on this page.
- Lights: names, groups, DMX addresses (overlaps are flagged) and a note of where each
  light is, saved to fixture_map.csv (the same file usb_dmx_patch_test.py uses).
- Identify: every light shows its own colour, the same colour as its dot.
- Test: red / green / blue / white / off on the selected lights, or all of them.
Identify and test colours switch off when you leave this page.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fixtures import PROFILES

from ..commands import FlashLights, SetIdentify, SetTestColour
from ..elements import media_path
from ..links import carry_record as _carry_record
from ..engine import EngineFrame
from ..files import write_atomic
from ..layouts import (
    delete_layout,
    import_layout,
    import_summary,
    rename_layout,
    save_layout_as,
    set_backdrop,
    set_backdrop_opacity,
    sorted_layouts,
    switch_layout,
)
from ..log import log_exception
from ..model import ROLE_COLOUR, MapSpot, Show
from ..notes import load_notes, save_notes
from ..show_io import ShowError
from ..show_io import load_show as read_show
from ..output import RGB
from ..lights import EDITOR_H, EDITOR_W, identify_colours
from ..show_edit import (
    add_colour_light,
    channel_conflicts,
    move_lights,
    profile_label,
    remove_lights,
    rename_light,
    set_address,
    set_group,
    set_identify_seed,
    set_profile,
)
from . import icons, theme
from .dashboard import Swatch
from .document import ShowDocument, folder_key
from .light_view import rgb_colour
from .undo_bar import UndoBar
from .widgets import ElidedLabel, Panel, ResettableSplitter, ToggleSwitch, fader_row, discard

SCENE_W = EDITOR_W  # 640
SCENE_H = EDITOR_H  # 360
CORE = 7.0  # dot radius in scene units
LABEL_MAX_PX = 150  # longer light names are shortened with "…" on the map
SNAP = 10.0  # snap grid in scene units (1/64 of the width)
LAYOUT_NAME_MAX_PX = 300  # longer layout names are shortened with "…" on the dropdown
ALIGN_CHOICES = (
    ("Line up left edges", "left"), ("Line up centres (across)", "hcentre"), ("Line up right edges", "right"),
    ("Line up tops", "top"), ("Line up middles (down)", "vmiddle"), ("Line up bottoms", "bottom"),
    ("Space evenly across", "hspace"), ("Space evenly down", "vspace"),
)
TEST_COLOURS = (
    ("Red", (1.0, 0.0, 0.0)),
    ("Green", (0.0, 1.0, 0.0)),
    ("Blue", (0.0, 0.0, 1.0)),
    ("White", (1.0, 1.0, 1.0)),
    ("Off", (0.0, 0.0, 0.0)),
)


# ---------------------------------------------------------------- the map


class LightItem(QGraphicsObject):
    """One colour light on the map: its dot and its name."""

    def __init__(self, page: LayoutPage, fixture_id: str, name: str, x: float, y: float) -> None:
        super().__init__()
        self.page = page
        self.fixture_id = fixture_id
        self.colour = QColor(0, 0, 0)
        self.tint = QColor(theme.TEXT_3)
        self._pressed_at: QPointF | None = None
        self._dragged = False
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.label = QGraphicsSimpleTextItem(name, self)
        font = theme.ui_font(12)
        self.label.setFont(font)
        self.label.setBrush(QColor(theme.TEXT))
        self.label.setZValue(3)
        # names stay a readable size however small the map is drawn
        self.label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.full_name = name
        self.setPos(x, y)
        self.set_name(name)

    def set_name(self, name: str) -> None:
        from PySide6.QtGui import QFontMetrics

        self.full_name = name
        shown = QFontMetrics(self.label.font()).elidedText(name, Qt.TextElideMode.ElideRight, LABEL_MAX_PX)
        self.label.setText(shown)
        self.setToolTip(f"{name}\nDrag to move · click to flash")
        self.place_label(1.0)

    def place_label(self, scale: float) -> None:
        """Centre the name under the dot (the name itself isn't scaled with the map)."""
        rect = self.label.boundingRect()
        self.label.setPos(-rect.width() / 2 / max(scale, 0.01), CORE + 3 / max(scale, 0.01))

    def set_colours(self, colour: QColor, tint: QColor) -> None:
        if colour != self.colour or tint != self.tint:
            self.colour = colour
            self.tint = tint
            self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        extent = CORE + 6
        return QRectF(-extent, -extent, extent * 2, extent * 2)

    def shape(self):  # noqa: D401 (Qt name)
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), CORE + 4, CORE + 4)  # (a little bigger than the dot: easier to grab)
        return path

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()
        if selected:  # a ring round the dot shows it's selected
            ring = QPen(QColor(theme.ACCENT), 1.6)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), CORE + 4, CORE + 4)
        painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.TEXT), 1.5))
        painter.setBrush(self.colour)
        painter.drawEllipse(QPointF(0, 0), CORE, CORE)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            x = min(SCENE_W, max(0.0, value.x()))
            y = min(SCENE_H, max(0.0, value.y()))
            if self.page.snap:
                x, y = round(x / SNAP) * SNAP, round(y / SNAP) * SNAP
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self._pressed_at is not None:
            self._dragged = True
            self.page.light_moving(self)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
            self.page.map_selection_changed()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_at = event.scenePos()
            self._dragged = False
            self.page.begin_drag()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._pressed_at is None:
            return
        moved = self._dragged
        self._pressed_at = None
        self._dragged = False
        if moved:
            self.page.end_drag("move")
        else:
            self.page.cancel_drag()
            self.page.flash([self.fixture_id])

    def spot(self) -> MapSpot:
        return MapSpot(self.pos().x() / SCENE_W, self.pos().y() / SCENE_H)


class MapScene(QGraphicsScene):
    def __init__(self, parent=None) -> None:
        super().__init__(0, 0, SCENE_W, SCENE_H, parent)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        painter.fillRect(rect, QColor(theme.WINDOW))
        painter.fillRect(self.sceneRect(), QColor(theme.WELL))
        pen = QPen(QColor("#141B28"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for i in range(1, 16):
            x = SCENE_W * i / 16
            painter.drawLine(QPointF(x, 0), QPointF(x, SCENE_H))
        for i in range(1, 9):
            y = SCENE_H * i / 9
            painter.drawLine(QPointF(0, y), QPointF(SCENE_W, y))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        pen = QPen(QColor(theme.BORDER_STRONG), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.sceneRect())


class MapView(QGraphicsView):
    resized = Signal()

    def __init__(self, scene: MapScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setObjectName("MapView")
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor(theme.WINDOW))
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(640, 360)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fitInView(QRectF(-8, -8, SCENE_W + 16, SCENE_H + 16), Qt.AspectRatioMode.KeepAspectRatio)
        self.resized.emit()


# ---------------------------------------------------------------- the lights table


class ProfileDelegate(QStyledItemDelegate):
    def createEditor(self, parent, _option, _index):  # noqa: N802
        combo = QComboBox(parent)
        for profile in PROFILES:
            combo.addItem(profile_label(profile), profile)
        return combo

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.ItemDataRole.UserRole))))

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        model.setData(index, editor.currentData(), Qt.ItemDataRole.UserRole)
        model.setData(index, editor.currentText(), Qt.ItemDataRole.DisplayRole)


class AddressDelegate(QStyledItemDelegate):
    def createEditor(self, parent, _option, _index):  # noqa: N802
        spin = QSpinBox(parent)
        spin.setRange(1, 512)
        return spin

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        editor.setValue(int(index.data(Qt.ItemDataRole.EditRole) or 1))

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class LightsTable(QTableWidget):
    """A table of lights. edited(fixture id, column key, value) fires when a cell changes."""

    edited = Signal(str, str, object)

    def __init__(self, columns: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(0, len(columns), parent)
        self.columns = columns
        self.setHorizontalHeaderLabels([label for _key, label in columns])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(50)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 10)
        starting_widths = {"name": 170, "group": 110, "profile": 190, "address": 80, "channels": 80}
        for index, (key, _label) in enumerate(columns[:-1]):
            if key in starting_widths:
                self.setColumnWidth(index, starting_widths[key])
        self._building = False
        self.itemChanged.connect(self._changed)
        keys = [key for key, _label in columns]
        if "profile" in keys:
            self.setItemDelegateForColumn(keys.index("profile"), ProfileDelegate(self))
        if "address" in keys:
            self.setItemDelegateForColumn(keys.index("address"), AddressDelegate(self))

    def column(self, key: str) -> int:
        return [k for k, _label in self.columns].index(key)

    def fill(self, rows: list[dict], read_only: bool) -> None:
        self._building = True
        try:
            selected = self.selected_ids()
            self.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, (key, _label) in enumerate(self.columns):
                    value = row.get(key, "")
                    item = QTableWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole + 1, row["id"])
                    if key == "profile":
                        item.setData(Qt.ItemDataRole.UserRole, value)
                        item.setText(profile_label(value))
                    elif key == "address":
                        item.setData(Qt.ItemDataRole.EditRole, int(value))
                    else:
                        item.setText(str(value))
                    editable = key in row.get("editable", ()) and not read_only
                    flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                    if editable:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    tip = row.get("tips", {}).get(key)
                    if tip:
                        item.setToolTip(tip)
                    elif key == "profile":
                        item.setToolTip(profile_label(value))
                    elif isinstance(value, str) and value:
                        item.setToolTip(value)
                    if key in row.get("bad", ()):
                        item.setBackground(QBrush(QColor("#4A1626")))
                        item.setForeground(QBrush(QColor(theme.DANGER)))
                    self.setItem(r, c, item)
            self.select_ids(selected)
        finally:
            self._building = False

    def row_id(self, row: int) -> str:
        item = self.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole + 1)) if item is not None else ""

    def selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.selectedIndexes()})
        return [self.row_id(r) for r in rows]

    def select_ids(self, ids) -> None:
        wanted = set(ids)
        blocked = self.blockSignals(True)
        self.clearSelection()
        mode = self.selectionMode()
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for r in range(self.rowCount()):
            if self.row_id(r) in wanted:
                self.selectRow(r)
        self.setSelectionMode(mode)
        self.blockSignals(blocked)
        self.viewport().update()

    def _changed(self, item: QTableWidgetItem) -> None:
        if self._building:
            return
        key = self.columns[item.column()][0]
        fixture_id = str(item.data(Qt.ItemDataRole.UserRole + 1))
        if key == "profile":
            value = item.data(Qt.ItemDataRole.UserRole)
        elif key == "address":
            value = int(item.data(Qt.ItemDataRole.EditRole))
        else:
            value = item.text()
        self.edited.emit(fixture_id, key, value)


# ---------------------------------------------------------------- the page


class LayoutPage(QWidget):
    def __init__(self, document: ShowDocument, engine, parent: QWidget | None = None, notes_path: Path | None = None,
                 shows_provider=None) -> None:
        """shows_provider: the shows there are ([ShowInfo]), for "Import from another show…"."""
        super().__init__(parent)
        self.setObjectName("LayoutPage")
        self.document = document
        self.engine = engine
        self._shows = shows_provider or (lambda: [])
        self.snap = False
        self._notes_path = notes_path
        # by light id in memory, so a note follows its light through renames, undo and redo
        self.notes: dict[str, str] = {}
        self._other_notes: dict[str, str] = {}  # notes for lights this show hasn't got (kept in the file)
        self._noted_names: dict[str, str] = {}  # what each light was called when its note was last saved
        self._notes_folder = folder_key(document.show.folder)  # the show the notes are matched to
        self._match_notes(load_notes(notes_path) if notes_path else load_notes(), document.show)
        self._items: dict[str, LightItem] = {}
        self._shown_key: tuple | None = None  # what set_show last showed
        self._drag_spots: dict[str, MapSpot] = {}
        self._dragging = False
        self._syncing_selection = False
        self._identify_on = False
        self._last_frame: EngineFrame | None = None
        self._backdrop_key = None

        # --- map
        self.scene = MapScene(self)
        self.backdrop = QGraphicsPixmapItem()
        self.backdrop.setZValue(-3)
        self.backdrop.setOpacity(0.55)
        self.scene.addItem(self.backdrop)
        self.view = MapView(self.scene)
        self.view.resized.connect(self._update_labels)
        self.scene.selectionChanged.connect(self.map_selection_changed)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(50)  # ~20 updates a second while dragging
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._push_drag_preview)

        map_panel = Panel("Map")
        self.read_only_hint = QLabel("This show is read-only, so the layout can't be changed.")
        self.read_only_hint.setObjectName("Hint")
        self.read_only_hint.setWordWrap(True)
        map_panel.body.addWidget(self.read_only_hint)
        map_panel.body.addWidget(self.view, 1)
        hint = QLabel("Drag a dot to where the light is in the room · click a dot to flash its light. How much of a "
                      "picture each light averages is the Area of an Image, Video or NDI element, in the Editor.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        map_panel.body.addWidget(hint)

        # --- tables
        tables_panel = Panel("Lights")
        self.table = LightsTable([("name", "Light"), ("group", "Group"), ("profile", "Type"), ("address", "Address"), ("channels", "Channels"), ("where", "Where")])
        self.table.edited.connect(self._table_edited)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self.house_table = LightsTable([("name", "House light"), ("profile", "Type"), ("address", "Address"), ("channels", "Channels")])
        self.house_table.edited.connect(self._table_edited)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add a light")
        self.add_button.clicked.connect(self._add_light)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_lights)
        self.notes_status = ElidedLabel("")
        self.notes_status.setObjectName("Hint")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.notes_status, 1)
        tables_panel.body.addWidget(self.table, 3)
        tables_panel.body.addLayout(buttons)
        tables_panel.body.addWidget(self.house_table, 1)

        self.left = ResettableSplitter(Qt.Orientation.Vertical, self.default_left_split)
        self.left.addWidget(map_panel)
        self.left.addWidget(tables_panel)
        self.left.setStretchFactor(0, 3)
        self.left.setStretchFactor(1, 2)

        # --- tools
        identify_panel = Panel("Identify")
        identify_row = QHBoxLayout()
        self.identify_switch = ToggleSwitch()
        self.identify_switch.setAccessibleName("Identify")
        self.identify_switch.clicked.connect(lambda *_: self.set_identify(self.identify_switch.isChecked()))
        identify_label = QLabel("Each light shows its own colour")
        identify_label.setWordWrap(True)
        identify_row.addWidget(self.identify_switch)
        identify_row.addWidget(identify_label, 1)
        identify_panel.body.addLayout(identify_row)
        self.shuffle_button = QPushButton("Shuffle colours")
        self.shuffle_button.clicked.connect(self._shuffle)
        identify_panel.body.addWidget(self.shuffle_button)
        self.legend = QGridLayout()
        self.legend.setHorizontalSpacing(8)
        self.legend.setVerticalSpacing(4)
        identify_panel.body.addLayout(self.legend)

        test_panel = Panel("Test colours")
        test_hint = QLabel("On the selected lights, or all of them when none is selected.")
        test_hint.setObjectName("Hint")
        test_hint.setWordWrap(True)
        test_panel.body.addWidget(test_hint)
        test_grid = QGridLayout()
        test_grid.setSpacing(6)
        self.test_buttons: dict[str, QPushButton] = {}
        for i, (name, rgb) in enumerate(TEST_COLOURS):
            button = QPushButton(name)
            button.clicked.connect(lambda _c=False, colour=rgb: self.test_colour(colour))
            test_grid.addWidget(button, i // 3, i % 3)
            self.test_buttons[name] = button
        self.stop_test_button = QPushButton("Stop test")
        self.stop_test_button.clicked.connect(lambda: self.test_colour(None))
        test_grid.addWidget(self.stop_test_button, 1, 2)
        test_panel.body.addLayout(test_grid)
        self.flash_button = QPushButton("Flash selected")
        self.flash_button.clicked.connect(lambda: self.flash(self.selected_ids()))
        test_panel.body.addWidget(self.flash_button)

        selected_panel = Panel("Selected light")
        self.selected_name = ElidedLabel("Click a light on the map")
        self.selected_name.setObjectName("RowName")
        selected_panel.body.addWidget(self.selected_name)
        self.position_box = QWidget()
        form = QFormLayout(self.position_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        self.x_spin = self._percent_spin(0, 100, "Across the map (0 = left, 100 = right)")
        self.y_spin = self._percent_spin(0, 100, "Down the map (0 = top, 100 = bottom)")
        form.addRow("Across", self.x_spin)
        form.addRow("Down", self.y_spin)
        selected_panel.body.addWidget(self.position_box)
        self.position_box.setVisible(False)

        map_tools = Panel("Map tools")
        snap_row = QHBoxLayout()
        self.snap_switch = ToggleSwitch()
        self.snap_switch.setAccessibleName("Snap to grid")
        self.snap_switch.clicked.connect(lambda *_: setattr(self, "snap", self.snap_switch.isChecked()))
        snap_row.addWidget(self.snap_switch)
        snap_row.addWidget(QLabel("Snap to the grid"), 1)
        map_tools.body.addLayout(snap_row)
        self.align_button = QToolButton()
        self.align_button.setObjectName("MenuButton")
        self.align_button.setText("Line up selected  ▾")
        self.align_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.align_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.align_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        align_menu = QMenu(self.align_button)
        for label, how in ALIGN_CHOICES:
            align_menu.addAction(label, lambda h=how: self.align(h))
        self.align_button.setMenu(align_menu)
        map_tools.body.addWidget(self.align_button)
        photo_row = QHBoxLayout()
        self.photo_button = QPushButton("Room photo…")
        self.photo_button.setToolTip("Put a photo of the room behind the map, to place the lights on")
        self.photo_button.clicked.connect(self._choose_photo)
        self.clear_photo_button = QPushButton("Remove photo")
        self.clear_photo_button.clicked.connect(self._clear_photo)
        photo_row.addWidget(self.photo_button)
        photo_row.addWidget(self.clear_photo_button)
        map_tools.body.addLayout(photo_row)
        # the room photo's link to its original (M13): "Not checked yet · Allow", "Original not found: …"
        self.linked = None
        link_row = QHBoxLayout()
        self.photo_warning = QLabel()
        self.photo_warning.setPixmap(icons.line_icon(icons.draw_warning, theme.WARN).pixmap(16, 16))
        self.photo_warning.hide()
        self.photo_status = QLabel()
        self.photo_status.setObjectName("RowDetail")
        self.photo_status.setTextFormat(Qt.TextFormat.PlainText)
        self.photo_status.setWordWrap(True)
        self.photo_status.hide()
        self.photo_allow = QPushButton("Allow")
        self.photo_allow.setToolTip("Let CentraLight read this photo and copy in its changes")
        self.photo_allow.clicked.connect(self._allow_photo)
        self.photo_allow.hide()
        link_row.addWidget(self.photo_warning, 0, Qt.AlignmentFlag.AlignTop)
        link_row.addWidget(self.photo_status, 1)
        link_row.addWidget(self.photo_allow, 0, Qt.AlignmentFlag.AlignTop)
        map_tools.body.addLayout(link_row)
        strength_row, self.photo_strength, self.photo_strength_text = fader_row("Photo strength", "How strongly the room photo shows behind the map")
        self.photo_strength.moved.connect(self._photo_strength_moved)
        map_tools.body.addLayout(strength_row)

        column = QWidget()
        column.setObjectName("ScrollContent")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 4, 0)
        column_layout.setSpacing(12)
        for panel in (identify_panel, test_panel, selected_panel, map_tools):
            column_layout.addWidget(panel)
        column_layout.addStretch(1)
        tools = QScrollArea()
        tools.setWidgetResizable(True)
        tools.setWidget(column)
        tools.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tools.setMinimumWidth(290)

        self.splitter = ResettableSplitter(Qt.Orientation.Horizontal, self.default_split)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(tools)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.left.setMinimumWidth(480)

        # --- top bar: saved layouts on the left, undo on the right
        self.layout_button = QToolButton()
        self.layout_button.setObjectName("MenuButton")
        self.layout_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.layout_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.layout_menu = QMenu(self.layout_button)
        self.layout_menu.aboutToShow.connect(self.fill_layout_menu)
        self.layout_button.setMenu(self.layout_menu)
        layout_hint = ElidedLabel("A layout keeps where every light is, and the room photo")
        layout_hint.setObjectName("Hint")
        self.undo_bar = UndoBar(document.undo_group)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.layout_button)
        top.addWidget(layout_hint, 1)
        top.addWidget(self.undo_bar)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.splitter, 1)

        document.changed.connect(self.set_show)
        self.set_show(document.show)

    # ------------------------------------------------------------ splits

    def default_split(self) -> list[int]:
        total = max(1, self.splitter.width())
        right = min(380, max(300, total // 4))
        return [total - right, right]

    def default_left_split(self) -> list[int]:
        total = max(1, self.left.height())
        tables = max(190, round(total * 0.4))
        return [total - tables, tables]

    def apply_split(self, sizes: dict | None) -> None:
        sizes = sizes if isinstance(sizes, dict) else {}
        outer, left = sizes.get("outer"), sizes.get("left")
        self.splitter.apply_sizes(outer)
        self.left.apply_sizes(left)

    def splits(self) -> dict:
        return {"outer": self.splitter.sizes(), "left": self.left.sizes()}

    def _percent_spin(self, low: float, high: float, tip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" %")
        spin.setToolTip(tip)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(self._spins_changed)
        return spin

    # ------------------------------------------------------------ showing the show

    @property
    def show_data(self) -> Show:
        return self.document.show

    def set_show(self, show: Show) -> None:
        key = (show.folder, show.fixtures, show.house, show.identify_seed, show.layouts, show.active_layout, show.read_only)
        if key == self._shown_key:
            return  # nothing this tab shows has changed (e.g. an edit in the Editor)
        self._shown_key = key
        if folder_key(show.folder) != self._notes_folder:
            # another show: its lights may use the same ids for other lights (a light called "Pulpit" here may be
            # "Stage left" there), so the notes are matched to its lights by their names again, as the file has them
            self._notes_folder = folder_key(show.folder)
            self._match_notes(self._notes_by_name(), show)
        for fx in show.fixtures:  # (another show's lights, loaded: their notes are in the file under their names)
            if fx.id not in self.notes and fx.name in self._other_notes:
                self.notes[fx.id] = self._other_notes.pop(fx.name)
                self._noted_names.setdefault(fx.id, fx.name)
        read_only = show.read_only
        self.read_only_hint.setVisible(read_only)
        for widget in (self.add_button, self.remove_button, self.shuffle_button, self.align_button, self.photo_button, self.clear_photo_button):
            widget.setEnabled(not read_only)
        self._sync_layout_button(show)
        lights = [fx for fx in show.colour_fixtures() if fx.map is not None]
        wanted = {fx.id for fx in lights}
        for fixture_id in list(self._items):
            if fixture_id not in wanted:
                self.scene.removeItem(self._items.pop(fixture_id))
        for fx in lights:
            item = self._items.get(fx.id)
            x, y = fx.map.x * SCENE_W, fx.map.y * SCENE_H
            if item is None:
                item = LightItem(self, fx.id, fx.name, x, y)
                self.scene.addItem(item)
                self._items[fx.id] = item
            else:
                item.set_name(fx.name)
                if not self._dragging:
                    if (abs(item.pos().x() - x) > 0.01 or abs(item.pos().y() - y) > 0.01):
                        item.setPos(x, y)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not read_only)
        self._update_labels()
        renamed = [fx.id for fx in show.fixtures if self.notes.get(fx.id) and self._noted_names.get(fx.id) not in (None, fx.name)]
        if renamed:  # (undo / redo of a rename): the file follows, with the note under the light's name now
            self._save_notes(show)
        self._fill_tables(show)
        self._fill_legend(show)
        self._load_backdrop(show)
        self._sync_selected_panel()
        if self._last_frame is not None:
            self.set_frame(self._last_frame)

    def _fill_tables(self, show: Show) -> None:
        conflicts = channel_conflicts(show)
        rows = []
        for fx in show.colour_fixtures():
            bad = ("address",) if fx.id in conflicts else ()
            tips = {"address": "Overlaps " + ", ".join(conflicts[fx.id])} if fx.id in conflicts else {}
            rows.append({
                "id": fx.id, "name": fx.name, "group": fx.group, "profile": fx.profile, "address": fx.address,
                "channels": fx.channel_range(), "where": self.notes.get(fx.id, ""), "bad": bad, "tips": tips,
                "editable": ("name", "group", "profile", "address", "where"),
            })
        self.table.fill(rows, show.read_only)
        house_rows = []
        for fx in show.house_fixtures():
            bad = ("address",) if fx.id in conflicts else ()
            tips = {"address": "Overlaps " + ", ".join(conflicts[fx.id])} if fx.id in conflicts else {}
            house_rows.append({
                "id": fx.id, "name": show.house_label(fx.id), "profile": fx.profile, "address": fx.address,
                "channels": fx.channel_range(), "bad": bad, "tips": tips, "editable": ("name", "profile", "address"),
            })
        self.house_table.fill(house_rows, show.read_only)

    def _fill_legend(self, show: Show) -> None:
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget() is not None:
                discard(item.widget())
        colours = identify_colours([fx.id for fx in show.colour_fixtures()], show.identify_seed)
        for row, fx in enumerate(show.colour_fixtures()):
            name, rgb = colours[fx.id]
            swatch = Swatch()
            swatch.set_colour(rgb_colour(rgb))
            light = ElidedLabel(fx.name)
            colour_name = QLabel(name)
            colour_name.setObjectName("RowDetail")
            self.legend.addWidget(swatch, row, 0)
            self.legend.addWidget(light, row, 1)
            self.legend.addWidget(colour_name, row, 2)
        self.legend.setColumnStretch(1, 1)

    def _identify_colours(self, show: Show) -> dict[str, RGB]:
        return {k: rgb for k, (_n, rgb) in identify_colours([fx.id for fx in show.colour_fixtures()], show.identify_seed).items()}

    def _update_labels(self) -> None:
        """Hide names that would bump into each other (hovering a dot always shows it).

        Names are drawn at a fixed screen size, so this works in the view's pixels."""
        scale = self.view.transform().m11() or 1.0
        bounds = QRectF(self.view.mapFromScene(QPointF(0, 0)), self.view.mapFromScene(QPointF(SCENE_W, SCENE_H)))
        taken: list[QRectF] = []
        for item in sorted(self._items.values(), key=lambda i: (i.pos().y(), i.pos().x())):
            label = item.label.boundingRect()
            centre = self.view.mapFromScene(item.pos())
            rect = QRectF(centre.x() - label.width() / 2, centre.y() + CORE * scale + 3, label.width(), label.height())
            # slide a name that would poke out of the map back inside it
            shift = max(0.0, bounds.left() + 2 - rect.left()) - max(0.0, rect.right() - (bounds.right() - 2))
            rect.translate(shift, 0)
            if rect.bottom() > bounds.bottom():
                rect.moveBottom(centre.y() - CORE * scale - 3)  # no room below: put it above the dot
            item.label.setPos((rect.left() - centre.x()) / scale, (rect.top() - centre.y()) / scale)
            clash = any(rect.intersects(other) for other in taken) or rect.width() > bounds.width()
            item.label.setVisible(not clash)
            if not clash:
                taken.append(rect)

    def _photo_path(self, show: Show) -> Path | None:
        """The active layout's room photo, if it has one (always inside the show folder)."""
        layout = show.layout()
        if layout is None or not layout.backdrop:
            return None
        return media_path(show.folder, layout.backdrop)

    def set_linked(self, linked) -> None:
        """Linked pictures (M13): the room photo is linked to its original like a picture element."""
        self.linked = linked
        linked.changed.connect(self._show_photo_link)
        self._show_photo_link()

    def _show_photo_link(self) -> None:
        from ..links import file_name

        layout = self.show_data.layout()
        linked = self.linked
        original = layout.backdrop_original if layout is not None else ""
        key = ("", layout.id if layout is not None else "")
        copying = linked.copying.get(key, "") if linked is not None else ""
        problem = linked.problems.get(key, "") if linked is not None else ""
        state = linked.state(original) if linked is not None and original else None
        warn, text, allow = False, "", False
        if copying:
            text = f"Copying {file_name(copying)} into the show…"
        elif problem:
            warn, text = True, problem
        elif original and state is not None:
            if state.status == "unchecked":
                text, allow = f"Linked to {original}: not checked yet.", True
            elif state.status in ("missing", "unreachable", "bad", "refused"):
                warn = True
                text = (f"Original not found: {original}. CentraLight is using its own copy." if state.status == "missing"
                        else f"The original ({original}) can't be used{f': {state.detail}' if state.detail else ''}. "
                             "CentraLight is using its own copy.")
        self.photo_warning.setVisible(warn)
        self.photo_status.setText(text)
        self.photo_status.setVisible(bool(text))
        self.photo_allow.setVisible(allow)
        self.photo_button.setToolTip("Put a photo of the room behind the map, to place the lights on"
                                     + (f"\nLinked to: {original}" if original else ""))

    def _allow_photo(self) -> None:
        layout = self.show_data.layout()
        if self.linked is not None and layout is not None and layout.backdrop_original:
            self.linked.allow(layout.backdrop_original)

    def _load_backdrop(self, show: Show) -> None:
        if self.linked is not None:
            self._show_photo_link()
        layout = show.layout()
        opacity = layout.backdrop_opacity if layout is not None else 0.55
        self.backdrop.setOpacity(opacity)
        self.photo_strength.set_level(opacity)
        if not self.photo_strength.is_dragging():
            self.photo_strength_text.setText(f"{round(opacity * 100)}%")
        path = self._photo_path(show)
        has_photo = path is not None and path.is_file()
        self.clear_photo_button.setEnabled(has_photo and not show.read_only)
        self.photo_strength.setEnabled(has_photo and not show.read_only)
        try:
            key = (str(path), path.stat().st_mtime_ns) if has_photo else None
        except OSError:
            key = None
        if key == self._backdrop_key:
            return  # the same photo: don't read it again (this runs on every change)
        self._backdrop_key = key
        image = _read_photo(path) if key is not None else QImage()
        if image.isNull():
            self.backdrop.setPixmap(QPixmap())
            return
        scaled = image.scaled(SCENE_W, SCENE_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        x = (scaled.width() - SCENE_W) // 2
        y = (scaled.height() - SCENE_H) // 2
        self.backdrop.setPixmap(QPixmap.fromImage(scaled.copy(x, y, SCENE_W, SCENE_H)))

    # ------------------------------------------------------------ saved layouts

    def _sync_layout_button(self, show: Show) -> None:
        active = show.layout()
        full = f"Layout: {active.name if active is not None else '—'}"
        text = self.layout_button.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, LAYOUT_NAME_MAX_PX)
        self.layout_button.setText(f"{text}  ▾")
        self.layout_button.setToolTip(f"{full}\nSwitch to another saved layout, or save, rename or delete one")

    def fill_layout_menu(self) -> None:
        """The dropdown: every saved layout (the active one ticked), then Save as / Rename / Delete."""
        menu = self.layout_menu
        menu.clear()
        show = self.show_data
        group = QActionGroup(menu)
        group.setExclusive(True)
        for layout in sorted_layouts(show):
            action = menu.addAction(layout.name)
            action.setCheckable(True)
            action.setChecked(layout.id == show.active_layout)
            action.setEnabled(not show.read_only or layout.id == show.active_layout)
            action.triggered.connect(lambda _checked=False, layout_id=layout.id: self.switch_layout(layout_id))
            group.addAction(action)
        menu.addSeparator()
        save = menu.addAction("Save as a new layout…", self._ask_save_layout_as)
        rename = menu.addAction("Rename this layout…", self._ask_rename_layout)
        delete = menu.addAction("Delete this layout…", self._ask_delete_layout)
        menu.addSeparator()
        bring = menu.addAction("Import from another show…", self.ask_import)
        save.setEnabled(not show.read_only)
        rename.setEnabled(not show.read_only and show.layout() is not None)
        delete.setEnabled(not show.read_only and len(show.layouts) > 1)
        bring.setEnabled(not show.read_only and bool(show.folder))

    def switch_layout(self, layout_id: str) -> bool:
        layout = self.show_data.layout(layout_id)
        if layout is None or layout_id == self.show_data.active_layout:
            return False
        return self.document.apply(switch_layout(self.show_data, layout_id), f"Switch to layout {layout.name}")

    def save_layout_as(self, name: str) -> str:
        """Save the current positions and photo as a new layout, which becomes the active one."""
        show, layout_id = save_layout_as(self.show_data, name)
        return layout_id if self.document.apply(show, f"Save layout as {show.layout(layout_id).name}") else ""

    def rename_layout(self, layout_id: str, name: str) -> bool:
        old = self.show_data.layout(layout_id)
        show = rename_layout(self.show_data, layout_id, name)
        if old is None or show is self.show_data:
            return False
        return self.document.apply(show, f"Rename layout {old.name} → {show.layout(layout_id).name}")

    def delete_layout(self, layout_id: str) -> bool:
        layout = self.show_data.layout(layout_id)
        if layout is None:
            return False
        return self.document.apply(delete_layout(self.show_data, layout_id), f"Delete layout {layout.name}")

    def _ask_save_layout_as(self) -> None:
        current = self.show_data.layout()
        suggestion = f"{current.name} copy" if current is not None else "Layout"
        name, ok = QInputDialog.getText(self, "Save as a new layout", "Name for the new layout:", text=suggestion)
        if ok and name.strip():
            self.save_layout_as(name)

    def _ask_rename_layout(self) -> None:
        current = self.show_data.layout()
        if current is None:
            return
        name, ok = QInputDialog.getText(self, "Rename layout", "New name for this layout:", text=current.name)
        if ok and name.strip():
            self.rename_layout(current.id, name)

    def _ask_delete_layout(self) -> None:
        current = self.show_data.layout()
        if current is None or len(self.show_data.layouts) < 2:
            return
        answer = QMessageBox.question(
            self, "Delete layout",
            f"Delete the layout “{current.name}”?\n\nThe lights move to the next layout in the list. Ctrl+Z brings it back.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_layout(current.id)

    # ------------------------------------------------------------ importing a layout from another show

    def other_shows(self) -> list:
        """The shows to import from (not this one)."""
        from ..shows import same_folder

        try:
            shows = list(self._shows())
        except Exception:
            return []
        return [info for info in shows if not same_folder(info.folder, self.show_data.folder)]

    def ask_import(self) -> None:
        """Pick a show and one of its saved layouts; a summary of what changes comes first (Import / Cancel)."""
        if self.show_data.read_only:
            return
        shows = self.other_shows()
        if not shows:
            QMessageBox.information(self, "Import from another show", "There are no other shows to import from yet.")
            return
        dialog = ImportLayoutDialog(self, shows, self.show_data)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.other is not None and dialog.layout_id:
                self.import_from(dialog.other, dialog.layout_id)
        finally:
            dialog.deleteLater()

    def import_from(self, other: Show, layout_id: str) -> bool:
        """Bring the other show's lights and one of its layouts (and its room photo) here as a new saved layout.
        One undo step, in the layout's history."""
        show = self.show_data
        source = other.layout(layout_id)
        if source is None or show.read_only:
            return False
        backdrop = ""
        if source.backdrop:
            photo = media_path(other.folder, source.backdrop)
            if photo is not None and photo.is_file():
                try:
                    data = photo.read_bytes()
                    backdrop = f"media/room-{hashlib.sha1(data).hexdigest()[:12]}{photo.suffix.lower() or '.jpg'}"
                    target = Path(show.folder) / backdrop
                    if not target.is_file():
                        write_atomic(target, data)
                    _carry_record(other.folder, source.backdrop, show.folder, backdrop)
                except OSError as exc:
                    log_exception("copying the room photo", exc)
                    backdrop = ""
        new = import_layout(show, other, layout_id, backdrop)
        return self.document.apply(new, f"Import layout {source.name} from {other.name}")

    # ------------------------------------------------------------ frames from the engine

    def set_frame(self, frame: EngineFrame) -> None:
        self._last_frame = frame
        if self.identify_switch.isChecked() != frame.identify:
            self.identify_switch.setChecked(frame.identify)
        for fixture_id, item in self._items.items():
            rgb = frame.colours.get(fixture_id, (0.0, 0.0, 0.0))
            item.set_colours(rgb_colour(rgb), QColor(theme.TEXT_3))

    # ------------------------------------------------------------ dragging

    def begin_drag(self) -> None:
        if self.show_data.read_only:
            return
        self._dragging = True
        self._drag_spots.clear()
        self.document.begin_drag()

    def light_moving(self, item: LightItem) -> None:
        if not self._dragging:
            return
        for moving in [item, *[i for i in self._items.values() if i.isSelected() and i is not item]]:
            self._drag_spots[moving.fixture_id] = moving.spot()
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _push_drag_preview(self) -> None:
        if self._drag_spots:
            show = move_lights(self.show_data, dict(self._drag_spots))
            self.document.preview(show)

    def end_drag(self, kind: str = "move") -> None:
        """The end of a drag (moving lights). The whole drag is one undo step."""
        if not self._dragging:
            return
        self._preview_timer.stop()
        self._push_drag_preview()
        moved = list(self._drag_spots)
        self._dragging = False
        self._drag_spots.clear()
        self.document.end_drag(f"Move {self.lights_label(moved)}")
        self._update_labels()

    def lights_label(self, fixture_ids) -> str:
        """'SlimPAR 103' for one light, '3 lights' for more (for undo step names)."""
        names = [fx.name for fid in fixture_ids if (fx := self.show_data.fixture(fid)) is not None]
        return names[0] if len(names) == 1 else f"{len(names)} lights"

    def cancel_drag(self) -> None:
        self._dragging = False
        self._drag_spots.clear()
        self.document.end_drag("")

    # ------------------------------------------------------------ selection

    def selected_ids(self) -> list[str]:
        return [fixture_id for fixture_id, item in self._items.items() if item.isSelected()]

    def map_selection_changed(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            self.table.select_ids(self.selected_ids())
            self._sync_selected_panel()
        finally:
            self._syncing_selection = False

    def _table_selection_changed(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            wanted = set(self.table.selected_ids())
            for fixture_id, item in self._items.items():
                item.setSelected(fixture_id in wanted)
            self._sync_selected_panel()
        finally:
            self._syncing_selection = False

    def _sync_selected_panel(self) -> None:
        ids = self.selected_ids()
        spins = (self.x_spin, self.y_spin)
        if len(ids) != 1:
            self.selected_name.setText("Click a light on the map" if not ids else f"{len(ids)} lights selected")
            self.position_box.setVisible(False)
            return
        fx = self.show_data.fixture(ids[0])
        if fx is None or fx.map is None:
            return
        self.position_box.setVisible(True)
        self.selected_name.setText(f"{fx.name} · ch {fx.channel_range()}")
        for spin, value in zip(spins, (fx.map.x, fx.map.y)):
            blocked = spin.blockSignals(True)
            spin.setValue(round(value * 100, 1))
            spin.blockSignals(blocked)
            spin.setEnabled(not self.show_data.read_only)

    def _spins_changed(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        fx = self.show_data.fixture(ids[0])
        if fx is None or fx.map is None:
            return
        spot = MapSpot(self.x_spin.value() / 100, self.y_spin.value() / 100)
        self.document.apply(move_lights(self.show_data, {ids[0]: spot}), f"Move {fx.name}", merge_key=f"spin-Move-{ids[0]}")

    # ------------------------------------------------------------ editing

    def _table_edited(self, fixture_id: str, key: str, value) -> None:
        show = self.show_data
        fx = show.fixture(fixture_id)
        if fx is None:
            return
        if key == "where":
            self.notes[fixture_id] = str(value).strip()
            self._save_notes(show)
            return
        if key == "name":
            wanted = str(value).strip()
            if any(other.id != fixture_id and other.name.casefold() == wanted.casefold() for other in show.fixtures):
                self.notes_status.setText(f"Another light is already called {wanted}: pick another name")
                self._fill_tables(show)  # put the cell back
                return
            new = rename_light(show, fixture_id, wanted)
            text = f"Rename {fx.name} → {wanted}"
        elif key == "group":
            new = set_group(show, fixture_id, str(value))
            text = f"{fx.name}: group {fx.group or 'none'} → {str(value).strip() or 'none'}"
        elif key == "address":
            new = set_address(show, fixture_id, int(value))
            changed = new.fixture(fixture_id)
            text = f"{fx.name}: address {fx.address} → {changed.address if changed else value}"
        elif key == "profile":
            new = set_profile(show, fixture_id, str(value))
            text = f"{fx.name}: type {profile_label(fx.profile)} → {profile_label(str(value))}" if str(value) in PROFILES else ""
        else:
            return
        if not self.document.apply(new, text):
            self._fill_tables(show)  # put the cell back
        elif key == "name":
            self._save_notes(new)

    def _match_notes(self, by_name: dict[str, str], show: Show) -> None:
        """Notes by light name (as the file has them) to notes by this show's light ids."""
        names = {fx.name: fx.id for fx in show.fixtures}
        self.notes = {names[name]: where for name, where in by_name.items() if name in names}
        self._other_notes = {name: where for name, where in by_name.items() if name not in names}
        self._noted_names = {fx.id: fx.name for fx in show.fixtures}

    def _notes_by_name(self) -> dict[str, str]:
        """The notes by light name, as they were last saved (the names the lights had then)."""
        by_name = dict(self._other_notes)
        for fixture_id, where in self.notes.items():
            if where and fixture_id in self._noted_names:
                by_name[self._noted_names[fixture_id]] = where
        return by_name

    def _save_notes(self, show: Show) -> None:
        by_name = dict(self._other_notes)
        here = {fx.id for fx in show.fixtures}
        for fixture_id, where in self.notes.items():  # (lights taken out of the show: their notes stay, by name)
            if where and fixture_id not in here and fixture_id in self._noted_names:
                by_name.setdefault(self._noted_names[fixture_id], where)
        by_name.update({fx.name: self.notes[fx.id] for fx in show.fixtures if self.notes.get(fx.id)})
        self._noted_names = {**self._noted_names, **{fx.id: fx.name for fx in show.fixtures}}
        try:
            if self._notes_path:
                save_notes(show, by_name, self._notes_path)
            else:
                save_notes(show, by_name)
            self.notes_status.setText("Notes saved to fixture_map.csv")
        except OSError as exc:
            log_exception("saving fixture_map.csv", exc)
            self.notes_status.setText("Couldn't save fixture_map.csv. Is it open in Excel?")

    def _add_light(self) -> None:
        show, new_id = add_colour_light(self.show_data)
        if self.document.apply(show, f"Add {show.fixture(new_id).name}"):
            for fixture_id, item in self._items.items():
                item.setSelected(fixture_id == new_id)

    def _remove_lights(self) -> None:
        ids = self.selected_ids() or self.table.selected_ids()
        if not ids:
            return
        names = ", ".join(self.show_data.fixture(i).name for i in ids if self.show_data.fixture(i))
        answer = QMessageBox.question(self, "Remove lights", f"Remove {names}?\n\nCentraLight stops sending to it. Ctrl+Z brings it back.")
        if answer == QMessageBox.StandardButton.Yes:
            self.document.apply(remove_lights(self.show_data, set(ids)), f"Remove {self.lights_label(ids)}")

    def align(self, how: str) -> None:
        items = [self._items[i] for i in self.selected_ids()]
        if len(items) < 2:
            return
        xs = [i.pos().x() for i in items]
        ys = [i.pos().y() for i in items]
        spots = {}
        if how in ("hspace", "vspace") and len(items) >= 3:
            axis = 0 if how == "hspace" else 1
            ordered = sorted(items, key=lambda i: (i.pos().x(), i.pos().y())[axis])
            first, last = (ordered[0].pos().x(), ordered[-1].pos().x()) if axis == 0 else (ordered[0].pos().y(), ordered[-1].pos().y())
            for n, item in enumerate(ordered):
                value = first + (last - first) * n / (len(ordered) - 1)
                x, y = (value, item.pos().y()) if axis == 0 else (item.pos().x(), value)
                spots[item.fixture_id] = MapSpot(x / SCENE_W, y / SCENE_H)
        else:
            target = {"left": min(xs), "hcentre": sum(xs) / len(xs), "right": max(xs), "top": min(ys), "vmiddle": sum(ys) / len(ys), "bottom": max(ys)}.get(how)
            if target is None:
                return
            for item in items:
                x, y = item.pos().x(), item.pos().y()
                if how in ("left", "hcentre", "right"):
                    x = target
                else:
                    y = target
                spots[item.fixture_id] = MapSpot(x / SCENE_W, y / SCENE_H)
        title = dict((h, label) for label, h in ALIGN_CHOICES).get(how, "Line up")
        self.document.apply(move_lights(self.show_data, spots), f"{title} ({len(items)} lights)")

    def _choose_photo(self) -> None:
        folder = self.show_data.folder
        path, _ = QFileDialog.getOpenFileName(self, "Photo of the room", str(Path.home() / "Pictures"), "Images (*.jpg *.jpeg *.png *.bmp *.webp)")
        if path and self.show_data.folder == folder:  # (another show loaded meanwhile: the photo was for the other one)
            self.set_room_photo(Path(path))

    def set_room_photo(self, source: Path) -> bool:
        """Put a photo behind this layout's map: linked to the photo where it is, with CentraLight's own copy made on
        the links thread (the layout changes when the copy is ready; one undo step). Without linked media (some tests):
        copied here and now, its file named from its contents, so undo can bring back the old one."""
        show = self.show_data
        layout = show.layout()
        if self.linked is not None:
            if show.read_only or not show.folder or layout is None:
                return False
            self.linked.choose_photo(layout.id, str(source))
            return True
        image = QImage(str(source))
        if show.read_only or not show.folder or image.isNull():
            return False
        if image.width() > 1920:
            image = image.scaledToWidth(1920, Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPG", 85):
            return False
        data = bytes(buffer.data())
        name = f"media/room-{hashlib.sha1(data).hexdigest()[:12]}.jpg"
        target = Path(show.folder) / name
        try:
            if not target.is_file():
                write_atomic(target, data)
        except OSError as exc:
            log_exception("saving the room photo", exc)
            return False
        return self.document.apply(set_backdrop(show, name), "Room photo")

    def _clear_photo(self) -> None:
        # The file stays in media\, so Ctrl+Z can bring the photo back
        self.document.apply(set_backdrop(self.show_data, ""), "Remove room photo")

    def _photo_strength_moved(self, level: float) -> None:
        layout = self.show_data.layout()
        if layout is None:
            return
        self.photo_strength_text.setText(f"{round(level * 100)}%")
        show = set_backdrop_opacity(self.show_data, level)
        new = show.layout().backdrop_opacity
        self.document.apply(show, f"Photo strength {round(layout.backdrop_opacity * 100)} % → {round(new * 100)} %", merge_key="photo-strength")

    # ------------------------------------------------------------ identify and test

    def set_identify(self, on: bool) -> None:
        self._identify_on = on
        self.engine.submit(SetIdentify(on, self.show_data.identify_seed))
        if self.identify_switch.isChecked() != on:
            self.identify_switch.setChecked(on)

    def _shuffle(self) -> None:
        seed = random.Random().randrange(1, 10_000)
        if self.document.apply(set_identify_seed(self.show_data, seed), "Shuffle identify colours"):
            self.engine.submit(SetIdentify(self.identify_switch.isChecked(), seed))

    def test_colour(self, colour: RGB | None) -> None:
        self.engine.submit(SetTestColour(colour, tuple(self.selected_ids())))

    def flash(self, ids: list[str]) -> None:
        if ids:
            self.engine.submit(FlashLights(tuple(ids)))

    def on_leave(self) -> None:
        """Leaving the page: Identify and test colours go off, so the room's back to normal."""
        if self.identify_switch.isChecked() or self._identify_on:
            self.set_identify(False)
        self.engine.submit(SetTestColour(None))


def _read_photo(path: Path) -> QImage:
    """A room photo, read at no more than 1920 pixels wide (a linked original is the full-size file: a 24-megapixel
    photo read whole would hold the window up)."""
    from PySide6.QtGui import QImageReader

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > 1920:
        reader.setScaledSize(size.scaled(1920, 1920 * size.height() // max(1, size.width()),
                                         Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return image if not image.isNull() else QImage()


class ImportLayoutDialog(QDialog):
    """Import from another show: pick the show and one of its saved layouts; what changes here is shown before Import."""

    def __init__(self, parent: QWidget | None, shows: list, show: Show) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from another show")
        self.setObjectName("ImportLayoutDialog")
        self._show = show
        self._read_shows: dict[str, Show | None] = {}
        self.other: Show | None = None
        self.layout_id = ""
        intro = QLabel("This brings another show's lights (their names, types, addresses and groups, and the house "
                       "lights) here, at the positions of one of its saved layouts, with its room photo, as a new saved "
                       "layout. Ctrl+Z on the Layout tab puts this show's lights back.")
        intro.setWordWrap(True)
        self.show_pick = QComboBox()
        for info in shows:
            self.show_pick.addItem(info.name, info.folder)
        self.layout_pick = QComboBox()
        form = QFormLayout()
        form.addRow("Show", self.show_pick)
        form.addRow("Layout", self.layout_pick)
        self.summary = QLabel()
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(440)
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)
        self.show_pick.currentIndexChanged.connect(self._show_picked)
        self.layout_pick.currentIndexChanged.connect(self._layout_picked)
        self._show_picked()

    def _read(self, folder: str) -> Show | None:
        if folder not in self._read_shows:
            try:
                self._read_shows[folder] = read_show(Path(folder))
            except (ShowError, OSError) as exc:
                log_exception(f"reading the show in {folder}", exc)
                self._read_shows[folder] = None
        return self._read_shows[folder]

    def _show_picked(self, *_args) -> None:
        folder = self.show_pick.currentData()
        self.other = self._read(folder) if folder else None
        blocked = self.layout_pick.blockSignals(True)
        self.layout_pick.clear()
        if self.other is not None:
            for lay in sorted_layouts(self.other):
                self.layout_pick.addItem(lay.name, lay.id)
            self.layout_pick.setCurrentIndex(max(0, self.layout_pick.findData(self.other.active_layout)))
        self.layout_pick.blockSignals(blocked)
        self._layout_picked()

    def _layout_picked(self, *_args) -> None:
        other = self.other
        self.layout_id = self.layout_pick.currentData() or ""
        if other is None:
            self.summary.setText("That show couldn't be read (the session log says why).")
            self.import_button.setEnabled(False)
            return
        layout = other.layout(self.layout_id)
        lines = [f"• {line}" for line in import_summary(self._show, other)]
        if layout is not None:
            photo = "with its room photo" if layout.backdrop else "(it has no room photo)"
            lines.append(f"It becomes the saved layout “{layout.name} (from {other.name})” here, the one in use, {photo}.")
        self.summary.setText("What changes here:\n" + "\n".join(lines))
        self.import_button.setEnabled(layout is not None)
