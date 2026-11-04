"""The Editor's details panel (on the right).

- With an element selected: its settings, built from schema.py. A Paint element also
  lists its shapes (add, remove, reorder; the selected shape's settings below), and every
  element has a Lights checklist: untick a light and the element leaves it alone.
- With nothing selected: the input's own settings (name, colour tag, hotkey, transition in,
  house-light levels, local variables, Stream Deck URLs) and the Globals tab.
- "=" next to a setting gives it an expression (a formula, e.g. wave(time)); see expression_edit.py.

Every change goes through the show document, so it can be undone; moving a slider is one
undo step however far it travels.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..expressions import Env, ExpressionError, compile_expression, describe_value
from ..colours import to_hex
from ..input_edit import (
    add_shape,
    copy_media,
    find_element,
    get_param,
    move_shape,
    param_path,
    remove_key,
    remove_shape,
    set_element_value,
    set_key,
    set_key_interpolation,
    set_length,
    set_param,
    set_excluded,
    set_house_level,
    set_house_param,
    set_shape_value,
    set_transition,
    update_input,
)
from ..model import END_BEHAVIOURS, END_HOLD, END_LOOP, END_STOP, Input, Show, element_span
from ..params import base_value, expression_of, is_keyframed, key_at, neighbour_key, with_base_value, with_expression
from ..schema import SHAPE, TRANSITION, Spec, element_specs, get_path, visible
from ..step_names import change_name, describe, element_name, level_name, lights_change_name, lower_first, shape_name
from ..variables import AUDIO_SILENT, evaluate_locals
from . import icons, theme
from .document import ShowDocument
from .editor_widgets import ChoiceField, ColourField, NumberField, SourceField
from .expression_edit import Evaluator, ExprButton, ExpressionDialog
from .globals_panel import GlobalsPanel, LocalsPanel
from .widgets import ElidedLabel, FlowLayout, Panel, discard

KIND_TITLES = {"paint": "Paint", "image": "Image", "video": "Video", "ndi": "NDI feed"}
MIN_WIDTH = 340
END_CHOICES = ((END_HOLD, "Hold the last moment"), (END_LOOP, "Loop"), (END_STOP, "Stop"))
_FILLED_KEY = None


def _filled_key_icon():
    """The ◆ when there's a keyframe at the playhead (made once)."""
    global _FILLED_KEY
    if _FILLED_KEY is None:
        _FILLED_KEY = icons.line_icon(lambda painter, rect, colour, _on: icons.draw_key(painter, rect, colour, True), theme.ACCENT)
    return _FILLED_KEY
SPECIAL_NAMES = {"t", "time", "value", "pi"}
RESERVED_HOTKEYS = {"Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z", "Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+,", "Ctrl+V", "Ctrl+Shift+D"}
SHAPE_TITLES = {"rect": "Rectangle", "ellipse": "Ellipse"}


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class DetailsPanel(QScrollArea):
    shape_selected = Signal(str)  # shape id ("" = none)
    element_removed = Signal()
    tab_changed = Signal(int)  # 0 = the Input tab, 1 = Globals
    seek_requested = Signal(float)  # move the Editor's playhead (input seconds): the ◆ menu's previous / next key
    timed_requested = Signal(bool)  # the Input tab's Infinite box: True = make it timed (the timeline does it)

    def __init__(self, document: ShowDocument, sources_provider: Callable[[], list[str]] | None = None, parent: QWidget | None = None,
                 context: Callable[[], tuple[Mapping[str, Any], float]] | None = None,
                 url_base: Callable[[], str] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DetailsPanel")
        self.document = document
        self._sources_provider = sources_provider or (lambda: [])
        self._context = context or (lambda: ({}, 0.0))  # (global variables' values, the Editor's time)
        self._url_base = url_base or (lambda: "http://127.0.0.1:8746")
        self._exprs: dict[str, ExprButton] = {}
        self._keys: dict[str, QToolButton] = {}  # the ◆ next to each setting that can have keyframes
        self._tab = 0
        self._lights_pending = False
        self._gesture = 0  # bumped at the end of each drag or typed value: each is its own undo step
        self._gesture_t: dict[str, float] = {}  # setting -> the element time its drag began at
        self.locals_panel: LocalsPanel | None = None
        self.globals_panel: GlobalsPanel | None = None
        self.hotkey: QKeySequenceEdit | None = None
        self.hotkey_problem: QLabel | None = None
        self.dialog: ExpressionDialog | None = None
        self.linked = None  # LinkedMedia (linked_media.py): the File row's link to its original, and Choose…
        self.input_id = ""
        self.element_id: str | None = None
        self.shape_id: str | None = None
        self._structure: tuple = ()
        self._fields: dict[str, Any] = {}
        self._updating = False
        self._content: QWidget | None = None
        self.lights_tree: QTreeWidget | None = None
        self.shape_list: QListWidget | None = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(MIN_WIDTH)
        document.changed.connect(self.refresh)
        self._rebuild()

    # ------------------------------------------------------------ what's shown

    @property
    def show_data(self) -> Show:
        return self.document.show

    def current_input(self) -> Input | None:
        return self.show_data.inputs.get(self.input_id) if self.input_id else None

    def current_element(self):
        inp = self.current_input()
        if inp is None or not self.element_id:
            return None
        found = find_element(inp, self.element_id)
        return found[2] if found else None

    def current_shape(self):
        element = self.current_element()
        if element is None or not self.shape_id:
            return None
        for shape in element.get("shapes", ()) or ():
            if shape.get("id") == self.shape_id:
                return shape
        return None

    def set_target(self, input_id: str, element_id: str | None, shape_id: str | None = None) -> None:
        if (input_id, element_id, shape_id) == (self.input_id, self.element_id, self.shape_id):
            return
        self._finish_typing()  # an edit half typed belongs to what's showing now
        self._gesture_t.clear()  # (a drag that began on what was showing is over)
        self._gesture += 1
        self.input_id, self.element_id, self.shape_id = input_id, element_id, shape_id
        self.refresh(self.show_data)
        element = self.current_element()
        original = element.get("original") if element is not None else None
        if self.linked is not None and isinstance(original, str) and original:
            self.linked.check(original)  # (selecting a linked element checks its original, on the links thread)

    def set_linked(self, linked) -> None:
        """Linked pictures and videos (M13): the File row shows its link's state, and Choose… copies through it."""
        self.linked = linked
        linked.changed.connect(self._link_changed)

    def _link_changed(self) -> None:
        element = self.current_element()
        if element is not None and element.get("kind") in ("image", "video") and "element:file" in self._fields:
            self._fill_values()

    def _finish_typing(self) -> None:
        """If a field here has the keyboard (e.g. the NDI source box), finish its edit now, while the element it
        belongs to is still the one showing."""
        focus = QGuiApplication.focusObject()
        content = self.widget()
        if isinstance(focus, QWidget) and content is not None and content.isAncestorOf(focus):
            focus.clearFocus()

    def refresh(self, _show: Show | None = None) -> None:
        """Update the values in place; rebuild only when the set of rows changes."""
        if self.current_element() is None:
            self.element_id = None
            self.shape_id = None
        elif self.shape_id and self.current_shape() is None:
            self.shape_id = None
        structure = self._structure_key()
        if structure != self._structure:
            self._rebuild()
        else:
            self._fill_values()

    def _structure_key(self) -> tuple:
        inp = self.current_input()
        if inp is None:
            return ("none",)
        element = self.current_element()
        if element is None:
            visible_transition = tuple(s.path for s in TRANSITION if self._transition_visible(s, inp))
            house = tuple(h.fixture for h in self.show_data.house)
            return ("input", inp.id, inp.infinite, visible_transition, house, tuple(fx.id for fx in self.show_data.colour_fixtures()))
        specs = element_specs(str(element.get("kind")))
        rows = tuple(s.path for s in specs if visible(s, element, specs))
        shapes = tuple(str(s.get("id")) for s in element.get("shapes", ()) or ())
        lights = tuple((fx.id, fx.name, fx.group) for fx in self.show_data.colour_fixtures())
        return ("element", inp.id, self.element_id, element.get("kind"), rows, shapes, self.shape_id, lights)

    # ------------------------------------------------------------ building

    def _rebuild(self) -> None:
        self._structure = self._structure_key()
        self._fields = {}
        self._exprs = {}
        self._keys = {}
        self.lights_tree = None
        self.shape_list = None
        self.locals_panel = None
        self.globals_panel = None
        self.hotkey = None
        self.hotkey_problem = None
        content = QWidget()
        content.setObjectName("ScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)
        inp = self.current_input()
        if inp is None:
            panel = Panel("Details")
            panel.body.addWidget(hint("Open an input to edit it: the pencil on a Dashboard tile, or New input."))
            layout.addWidget(panel)
        elif self.current_element() is None:
            layout.addWidget(self._input_tabs(inp))
        else:
            self._element_sections(layout, inp)
        layout.addStretch(1)
        # The old rows go later, quietly: they may be in the middle of sending a signal (a choice that changed which
        # rows show), and deleting a focused text box straight away makes it send "editing finished" while half gone.
        old = self.takeWidget()
        self.setWidget(content)
        if old is not None:
            for child in old.findChildren(QObject):
                child.blockSignals(True)
            old.blockSignals(True)
            discard(old)
        self._content = content
        self._fill_values()

    def _form(self, panel: Panel) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        panel.body.addLayout(form)
        return form

    def _field_for(self, spec: Spec, on_edit: Callable[[Any, bool], None]):
        if spec.type == "number":
            field = NumberField(spec.minimum, spec.maximum, spec.step, spec.unit)
        elif spec.type == "colour":
            field = ColourField()
        elif spec.type == "choice":
            field = ChoiceField(spec.choices)
        elif spec.type == "source":
            field = SourceField()
            field.set_sources(self._sources_provider())
        elif spec.type == "file":
            field = FileField()
        else:
            field = QLineEdit()
        if spec.help:
            field.setToolTip(spec.help)
        if hasattr(field, "edited"):
            field.edited.connect(on_edit)
        return field

    def _with_expr(self, field: QWidget, key: str, on_click: Callable[[], None], keyable: bool = False) -> QWidget:
        """field with an "=" button after it (for settings that can have an expression), and a ◆ for keyframes."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(field, 1)
        if keyable:
            diamond = QToolButton()
            diamond.setObjectName("KeyButton")
            diamond.setAutoRaise(True)
            diamond.setCursor(Qt.CursorShape.PointingHandCursor)
            diamond.clicked.connect(lambda _checked=False, k=key: self.toggle_key(k))
            diamond.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            diamond.customContextMenuRequested.connect(lambda pos, k=key, b=diamond: self._key_menu(k, b.mapToGlobal(pos)))
            row.addWidget(diamond)
            self._keys[key] = diamond
        button = ExprButton()
        button.clicked.connect(on_click)
        row.addWidget(button)
        self._exprs[key] = button
        return holder

    def _label(self, text: str) -> QLabel:
        label = ElidedLabel(text)
        label.setMaximumWidth(100)
        return label

    # --- the input's own settings

    def _input_tabs(self, inp: Input) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setObjectName("DetailsTabs")
        input_page = QWidget()
        page = QVBoxLayout(input_page)
        page.setContentsMargins(0, 8, 0, 0)
        page.setSpacing(12)

        about = Panel("Input")
        form = self._form(about)
        name = QLineEdit()
        name.editingFinished.connect(lambda: self._rename_input(name.text()))
        self._fields["input:name"] = name
        form.addRow(self._label("Name"), name)
        colour = ColourField()
        colour.setToolTip("The colour of its tile on the Dashboard")
        colour.edited.connect(lambda value, _f: self._set_tile_colour(value))
        self._fields["input:colour"] = colour
        form.addRow(self._label("Tile colour"), colour)
        self.hotkey = QKeySequenceEdit()
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setClearButtonEnabled(True)
        self.hotkey.setToolTip("A key that sends this input to Base while CentraLight is in front (F1–F12 work well)")
        self.hotkey.editingFinished.connect(self._hotkey_finished)
        self.hotkey.keySequenceChanged.connect(lambda seq: seq.isEmpty() and self._hotkey_finished())
        form.addRow(self._label("Hotkey"), self.hotkey)
        self.hotkey_problem = QLabel("")
        self.hotkey_problem.setObjectName("ExpressionProblem")
        self.hotkey_problem.setWordWrap(True)
        self.hotkey_problem.setVisible(False)
        form.addRow(self.hotkey_problem)
        mode = QCheckBox("Infinite (plays until something replaces it)")
        mode.setChecked(inp.infinite)
        mode.setToolTip("Untick for a timed sequence: a length, with elements placed in time on the timeline")
        mode.clicked.connect(lambda checked: self._timed_clicked(mode, checked))
        form.addRow(mode)
        if not inp.infinite:
            length = QDoubleSpinBox()
            length.setRange(0.1, 3600.0)
            length.setDecimals(1)
            length.setSuffix(" s")
            length.setKeyboardTracking(False)
            length.setToolTip("How long it lasts")
            length.valueChanged.connect(self._set_length)
            form.addRow(self._label("Length"), length)
            self._fields["input:length"] = length
            end = ChoiceField(END_CHOICES)
            end.setToolTip("What happens when it gets to the end")
            end.edited.connect(lambda value, _final: self._set_end(value))
            form.addRow(self._label("At the end"), end)
            self._fields["input:end"] = end
        urls = QPushButton("Copy Stream Deck URLs")
        urls.setToolTip("Copy the URLs that send this input to Base or an overlay, for Stream Deck keys")
        urls.clicked.connect(lambda: self.copy_urls(urls))
        about.body.addWidget(urls, 0, Qt.AlignmentFlag.AlignLeft)
        page.addWidget(about)

        transition = Panel("Transition in")
        tform = self._form(transition)
        for spec in TRANSITION:
            if not self._transition_visible(spec, inp):
                continue
            field = self._field_for(spec, lambda value, final, s=spec: self._set_transition(s, value))
            self._fields[f"transition:{spec.path}"] = field
            tform.addRow(self._label(spec.label), field)
        transition.body.addWidget(hint("How it appears when it's sent to Base or switched on in an overlay."))
        page.addWidget(transition)

        house = Panel("House lights")
        house.body.addWidget(hint("While this input is on Base it sets these. Moving the fader in the header wins until the next input."))
        hform = self._form(house)
        for item in self.show_data.house:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            check = QCheckBox("Set")
            level = NumberField(0.0, 1.0, 0.01, "%")
            row_layout.addWidget(check)
            row_layout.addWidget(self._with_expr(level, f"house:{item.fixture}", lambda f=item.fixture: self.edit_expression(f"house:{f}")), 1)
            check.toggled.connect(lambda on, f=item.fixture, lv=level: self._set_house(f, lv.value() if on else None))
            level.edited.connect(lambda value, final, f=item.fixture: self._set_house(f, value, merge=True))
            self._fields[f"house:{item.fixture}"] = (check, level)
            hform.addRow(self._label(item.label), row)
        page.addWidget(house)
        self.locals_panel = LocalsPanel(self.document, self._local_evaluator, self._variable_names)
        page.addWidget(self.locals_panel)
        page.addStretch(1)

        globals_page = QWidget()
        globals_layout = QVBoxLayout(globals_page)
        globals_layout.setContentsMargins(0, 8, 0, 0)
        self.globals_panel = GlobalsPanel(self.document, self._url_base)
        globals_layout.addWidget(self.globals_panel)

        tabs.addTab(input_page, "Input")
        tabs.addTab(globals_page, "Show variables")
        tabs.setCurrentIndex(self._tab)
        tabs.currentChanged.connect(self._tab_switched)
        return tabs

    def _tab_switched(self, index: int) -> None:
        self._tab = index
        self.tab_changed.emit(index)

    def globals_open(self) -> bool:
        """True while the Show variables tab is showing (the Editor's undo then uses the show's history)."""
        return self._tab == 1 and self.current_input() is not None and self.current_element() is None

    def _transition_visible(self, spec: Spec, inp: Input) -> bool:
        if spec.show_if is None:
            return True
        _path, values = spec.show_if
        return inp.transition.kind in values

    # --- an element's settings

    def _element_sections(self, layout: QVBoxLayout, inp: Input) -> None:
        element = self.current_element()
        kind = str(element.get("kind"))
        specs = element_specs(kind)
        title = Panel(f"{KIND_TITLES.get(kind, kind.title())} element · {self.element_id}")
        form = self._form(title)
        group = ""
        for spec in specs:
            if not visible(spec, element, specs):
                continue
            if spec.group != group:
                group = spec.group
                if group:  # a small heading over settings that go together (e.g. Smoothing: Brighter, Darker, On cuts)
                    heading = QLabel(group)
                    heading.setObjectName("RowDetail")
                    form.addRow(heading)
            field = self._field_for(spec, lambda value, final, path=spec.path: self._set_element(path, value, final))
            if spec.type == "file":
                field.choose_requested.connect(lambda kind=kind: self._choose_file(kind))
                field.allow_requested.connect(self._allow_link)
                field.update_requested.connect(self._update_link)
            key = f"element:{spec.path}"
            self._fields[key] = field
            if spec.animatable and spec.type in ("number", "colour"):
                field = self._with_expr(field, key, lambda k=key: self.edit_expression(k), keyable=True)
            form.addRow(self._label(spec.label), field)
        remove = QPushButton("Remove this element")
        remove.setProperty("danger", True)
        remove.clicked.connect(self._remove_element)
        title.body.addWidget(remove)
        layout.addWidget(title)

        if kind == "paint":
            layout.addWidget(self._shapes_panel(element))
            shape = self.current_shape()
            if shape is not None:
                layout.addWidget(self._shape_panel(shape))
        layout.addWidget(self._lights_panel(element))

    def _shapes_panel(self, element) -> Panel:
        panel = Panel("Shapes")
        self.shape_list = QListWidget()
        self.shape_list.setObjectName("ShapeList")
        shapes = list(element.get("shapes", ()) or ())
        for shape in shapes:
            item = QListWidgetItem(f"{SHAPE_TITLES.get(shape.get('type', 'rect'), 'Shape')} {shape.get('id', '')}")
            item.setData(Qt.ItemDataRole.UserRole, shape.get("id"))
            self.shape_list.addItem(item)
            if shape.get("id") == self.shape_id:
                self.shape_list.setCurrentItem(item)
        rows = max(2, min(6, len(shapes)))
        self.shape_list.setFixedHeight(rows * (self.shape_list.fontMetrics().height() + 10) + 6)
        self.shape_list.currentItemChanged.connect(self._shape_clicked)
        panel.body.addWidget(self.shape_list)
        if not shapes:
            panel.body.addWidget(hint("No shapes yet. Add one here, or draw it on the Selected view."))
        buttons = QWidget()
        flow = FlowLayout(buttons, spacing=6)  # wraps onto a second line when the panel is narrow
        add_rect = QPushButton("+ Rectangle")
        add_rect.clicked.connect(lambda: self._add_shape("rect"))
        add_ellipse = QPushButton("+ Ellipse")
        add_ellipse.clicked.connect(lambda: self._add_shape("ellipse"))
        up = QPushButton("Forward")
        up.setToolTip("Draw this shape over the next one")
        up.clicked.connect(lambda: self._move_shape(1))
        down = QPushButton("Back")
        down.setToolTip("Draw this shape under the one before")
        down.clicked.connect(lambda: self._move_shape(-1))
        delete = QPushButton("Remove")
        delete.clicked.connect(self._remove_shape)
        for button in (add_rect, add_ellipse, up, down, delete):
            flow.addWidget(button)
        for button in (up, down, delete):
            button.setEnabled(bool(self.shape_id))
        panel.body.addWidget(buttons)
        return panel

    def _shape_panel(self, shape) -> Panel:
        panel = Panel(f"{SHAPE_TITLES.get(shape.get('type', 'rect'), 'Shape')} {shape.get('id', '')}")
        form = self._form(panel)
        for spec in SHAPE:
            field = self._field_for(spec, lambda value, final, path=spec.path: self._set_shape(path, value, final))
            key = f"shape:{spec.path}"
            self._fields[key] = field
            if spec.animatable and spec.type in ("number", "colour"):
                field = self._with_expr(field, key, lambda k=key: self.edit_expression(k), keyable=True)
            form.addRow(self._label(spec.label), field)
        return panel

    def _lights_panel(self, element) -> Panel:
        panel = Panel("Lights")
        panel.body.addWidget(hint("Untick a light and this element leaves it alone (its patch of the picture stays clear)."))
        self.lights_tree = QTreeWidget()
        self.lights_tree.setObjectName("LightsTree")
        self.lights_tree.setHeaderHidden(True)
        self.lights_tree.setRootIsDecorated(True)
        groups: dict[str, QTreeWidgetItem] = {}
        for fx in self.show_data.colour_fixtures():
            group_name = fx.group or "Other lights"
            group = groups.get(group_name)
            if group is None:
                group = QTreeWidgetItem([group_name])
                group.setToolTip(0, f"{group_name}: tick or untick them all")
                group.setFlags(group.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                self.lights_tree.addTopLevelItem(group)
                groups[group_name] = group
            item = QTreeWidgetItem([fx.name])
            item.setToolTip(0, fx.name)  # long names are shortened with "…"
            item.setData(0, Qt.ItemDataRole.UserRole, fx.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            group.addChild(item)
        self.lights_tree.expandAll()
        rows = len(groups) + len(self.show_data.colour_fixtures())
        self.lights_tree.setFixedHeight(min(16, max(3, rows)) * (self.lights_tree.fontMetrics().height() + 8) + 8)
        self.lights_tree.itemChanged.connect(self._lights_changed)
        panel.body.addWidget(self.lights_tree)
        buttons = QHBoxLayout()
        every = QPushButton("All lights")
        every.clicked.connect(lambda: self._set_excluded([]))
        buttons.addWidget(every)
        buttons.addStretch(1)
        panel.body.addLayout(buttons)
        return panel

    # ------------------------------------------------------------ values

    def _fill_values(self) -> None:
        self._updating = True
        try:
            inp = self.current_input()
            if inp is None:
                return
            element = self.current_element()
            if element is None:
                name = self._fields.get("input:name")
                if name is not None and not name.hasFocus():
                    name.setText(inp.name)
                if "input:colour" in self._fields:
                    self._fields["input:colour"].set_value(inp.colour)
                length = self._fields.get("input:length")
                if length is not None and not length.hasFocus() and abs(length.value() - inp.length) > 1e-6:
                    blocked = length.blockSignals(True)
                    length.setValue(inp.length)
                    length.blockSignals(blocked)
                if "input:end" in self._fields:
                    self._fields["input:end"].set_value(inp.end)
                if self.hotkey is not None and not self.hotkey.hasFocus():
                    self.hotkey.setKeySequence(QKeySequence(inp.hotkey))
                for spec in TRANSITION:
                    field = self._fields.get(f"transition:{spec.path}")
                    if field is not None:
                        field.set_value(getattr(inp.transition, spec.path))
                for item in self.show_data.house:
                    pair = self._fields.get(f"house:{item.fixture}")
                    if pair is None:
                        continue
                    check, level = pair
                    setting = inp.house.get(item.fixture)
                    blocked = check.blockSignals(True)
                    check.setChecked(setting is not None)
                    check.blockSignals(blocked)
                    level.setEnabled(setting is not None)
                    level.set_value(base_value(setting, 0.0, 1.0) if setting is not None else 1.0)
                    button = self._exprs.get(f"house:{item.fixture}")
                    if button is not None:
                        button.setEnabled(setting is not None)
                        text = expression_of(setting)
                        button.set_state(text, self.problem_with(text))
                if self.locals_panel is not None:
                    self.locals_panel.refresh(inp.id)
                if self.globals_panel is not None:
                    self.globals_panel.refresh(self.show_data)
                return
            specs = element_specs(str(element.get("kind")))
            t = self.element_time()
            for spec in specs:
                key = f"element:{spec.path}"
                field = self._fields.get(key)
                if field is not None:
                    self._show_value(field, spec, get_path(element, spec.path, spec.default), self._exprs.get(key), self._keys.get(key), t)
            file_field = self._fields.get("element:file")
            if isinstance(file_field, FileField):
                self._show_link(file_field, element)
            shape = self.current_shape()
            if shape is not None:
                for spec in SHAPE:
                    key = f"shape:{spec.path}"
                    field = self._fields.get(key)
                    if field is not None:
                        self._show_value(field, spec, get_path(shape, spec.path, spec.default), self._exprs.get(key), self._keys.get(key), t)
            if self.lights_tree is not None:
                excluded = set(get_path(element, "mask.exclude", ()) or ())
                blocked = self.lights_tree.blockSignals(True)
                for g in range(self.lights_tree.topLevelItemCount()):
                    group = self.lights_tree.topLevelItem(g)
                    for c in range(group.childCount()):
                        child = group.child(c)
                        state = Qt.CheckState.Unchecked if child.data(0, Qt.ItemDataRole.UserRole) in excluded else Qt.CheckState.Checked
                        child.setCheckState(0, state)
                self.lights_tree.blockSignals(blocked)
        finally:
            self._updating = False

    def _show_value(self, field, spec: Spec, raw: Any, expr_button: ExprButton | None = None,
                    key_button: QToolButton | None = None, t: float = 0.0) -> None:
        """A setting's value at the playhead. Keyframed settings stay editable: a change sets a key there."""
        keyed = is_keyframed(raw)
        value = base_value(raw, t, spec.default, "colour" if spec.type == "colour" else "number" if spec.type == "number" else "raw")
        if hasattr(field, "set_value"):
            field.set_value(value)
        elif isinstance(field, QLineEdit) and not field.hasFocus():
            field.setText(str(value))
        if expr_button is not None:
            text = expression_of(raw)
            expr_button.set_state(text, self.problem_with(text))
        if key_button is not None:
            here = key_at(raw, t) is not None
            state = "here" if here else "keyed" if keyed else "none"
            if key_button.property("keyState") != state:
                key_button.setProperty("keyState", state)
                colour = theme.ACCENT if keyed else theme.TEXT_3
                key_button.setIcon(icons.line_icon(icons.draw_key, colour, colour) if not here else _filled_key_icon())
                key_button.setToolTip(
                    "Keyframe here: click to remove it · right-click for more" if here else
                    "Keyframed: click to add a keyframe at the playhead · right-click for more" if keyed else
                    "Click to add a keyframe at the playhead (the setting then changes over time)")

    # ------------------------------------------------------------ keyframes

    def set_time(self) -> None:
        """The Editor's playhead moved: show values (and ◆) at the new time."""
        if self.current_element() is not None and not self._updating:
            self._fill_values()

    def element_time(self) -> float:
        """The selected element's own time at the playhead (its keyframes count from its start)."""
        inp, element = self.current_input(), self.current_element()
        t = self._context()[1]
        if inp is None or element is None or inp.infinite:
            return t
        return max(0.0, t - element_span(element, inp)[0])

    def _where(self, key: str) -> tuple[str, Spec | None]:
        """("opacity" or "shapes:S1:x", its spec) for a field key ("element:opacity", "shape:x")."""
        group, path = key.split(":", 1)
        if group == "shape" and self.shape_id:
            return param_path(self.shape_id, path), next((s for s in SHAPE if s.path == path), None)
        element = self.current_element()
        specs = element_specs(str(element.get("kind"))) if element is not None else ()
        return path, next((s for s in specs if s.path == path), None)

    def toggle_key(self, key: str) -> None:
        """◆: add a keyframe at the playhead (with the value there), or remove the one that's there."""
        element = self.current_element()
        if element is None:
            return
        where, spec = self._where(key)
        raw = get_param(element, where)
        t = self.element_time()
        kind = "colour" if spec is not None and spec.type == "colour" else "number"
        name = f"{element_name(element)}: {spec.title.lower() if spec else where}"
        if key_at(raw, t) is not None:
            self._apply(remove_key(self.show_data, self.input_id, self.element_id, where, t), f"{name}: remove keyframe at {t:.2f} s")
            return
        value = base_value(raw, t, spec.default if spec else 0.0, kind)
        value = to_hex(value) if kind == "colour" else round(float(value), 4)
        self._apply(set_key(self.show_data, self.input_id, self.element_id, where, t, value), f"{name}: keyframe at {t:.2f} s")

    def _key_menu(self, key: str, at) -> None:
        element = self.current_element()
        inp = self.current_input()
        if element is None or inp is None:
            return
        where, spec = self._where(key)
        raw = get_param(element, where)
        t = self.element_time()
        start = element_span(element, inp)[0] if not inp.infinite else 0.0
        menu = QMenu(self)
        before, after = neighbour_key(raw, t, -1), neighbour_key(raw, t, 1)
        back = menu.addAction("Go to the previous keyframe", lambda: self.seek_requested.emit(start + before))
        back.setEnabled(before is not None)
        forward = menu.addAction("Go to the next keyframe", lambda: self.seek_requested.emit(start + after))
        forward.setEnabled(after is not None)
        if key_at(raw, t) is not None:
            menu.addSeparator()
            for interp, label in (("smooth", "Smooth to the next key"), ("linear", "Straight to the next key"), ("hold", "Hold until the next key")):
                menu.addAction(label, lambda i=interp: self._apply(
                    set_key_interpolation(self.show_data, self.input_id, self.element_id, where, t, i),
                    f"{element_name(element)}: keyframe at {t:.2f} s {i}"))
        if is_keyframed(raw):
            menu.addSeparator()
            menu.addAction("Remove all keyframes (keep the value here)", lambda: self._unkey(where, spec, raw, t))
        menu.exec(at)

    def _unkey(self, where: str, spec: Spec | None, raw: Any, t: float) -> None:
        element = self.current_element()
        kind = "colour" if spec is not None and spec.type == "colour" else "number"
        value = base_value(raw, t, spec.default if spec else 0.0, kind)
        value = to_hex(value) if kind == "colour" else round(float(value), 4)
        data = {k: v for k, v in raw.items() if k != "keys"} if hasattr(raw, "items") else {}
        fixed = {**data, "value": value} if "expr" in data else value
        self._apply(set_param(self.show_data, self.input_id, self.element_id, where, fixed),
                    f"{element_name(element)}: no keyframes on {spec.title.lower() if spec else where}")

    # ------------------------------------------------------------ expressions

    def _variable_names(self) -> list[str]:
        """Names expressions in this input can use: its locals, then the globals."""
        inp = self.current_input()
        names = [str(item.get("name")) for item in inp.locals] if inp is not None else []
        return names + [g.name for g in self.show_data.globals if g.name not in names]

    def problem_with(self, text: str) -> str:
        """What's wrong with an expression that can be seen without running it ('' = nothing)."""
        if not text:
            return ""
        try:
            names = compile_expression(text).names
        except ExpressionError as exc:
            return str(exc)
        missing = sorted(names - set(self._variable_names()) - SPECIAL_NAMES)
        return f'there\'s no variable called "{missing[0]}"' if missing else ""

    def _evaluator(self, kind: str, element_key: str, raw: Any, skip_local: str = "") -> Evaluator:
        """Works out an expression the way the engine would right now, for the dialog's live value."""
        default = (1.0, 1.0, 1.0) if kind == "colour" else 0.0

        def evaluate(text: str) -> tuple[str, str]:
            globals_, t = self._context()
            inp = self.current_input()
            values, _errors = evaluate_locals(inp, t, globals_) if inp is not None else ({}, {})
            values.pop(skip_local, None)

            def lookup(name: str):
                return values[name] if name in values else globals_.get(name)

            try:
                result = compile_expression(text).run(Env(lookup, base_value(raw, t, default, kind), t, t, AUDIO_SILENT, element_key))
            except ExpressionError as exc:
                return "", str(exc)
            if kind == "number" and type(result) is tuple:
                return describe_value(result), "this setting needs a number, but the expression gives a colour"
            return describe_value(result), ""

        return evaluate

    def _local_evaluator(self, kind: str, name: str, raw: Any) -> Evaluator:
        return self._evaluator(kind, f"{self.input_id}/locals", raw, skip_local=name)

    def edit_expression(self, key: str) -> ExpressionDialog | None:
        """Open the expression dialog for "element:<path>", "shape:<path>" or "house:<light id>"."""
        inp = self.current_input()
        if inp is None:
            return None
        where, _, path = key.partition(":")
        if where == "house":
            raw = inp.house.get(path)
            if raw is None:
                return None
            title, kind, subject, element_key = self.show_data.house_label(path), "number", self.show_data.house_label(path), f"{inp.id}/house"
        else:
            target = self.current_element() if where == "element" else self.current_shape()
            specs = element_specs(str(self.current_element().get("kind"))) if where == "element" else SHAPE
            spec = next((s for s in specs if s.path == path), None)
            if target is None or spec is None:
                return None
            raw = get_path(target, path, spec.default)
            title, kind = spec.title, "colour" if spec.type == "colour" else "number"
            subject = element_name(self.current_element()) if where == "element" else shape_name(target)
            element_key = f"{inp.id}/{self.element_id}"
        default = base_value(raw, 0.0, (1.0, 1.0, 1.0) if kind == "colour" else 0.0, kind)
        if kind == "colour" and type(default) is tuple:
            default = describe_value(default)

        def applied(text: str) -> None:
            new_raw = with_expression(raw, text, default)
            label = f"{subject}: {lower_first(title)} = {text}" if text else f"{subject}: {lower_first(title)}: remove the expression"
            if where == "house":
                show = set_house_param(self.show_data, self.input_id, path, new_raw)
            elif where == "element":
                show = set_element_value(self.show_data, self.input_id, self.element_id, path, new_raw)
            else:
                show = set_shape_value(self.show_data, self.input_id, self.element_id, self.shape_id, path, new_raw)
            self.document.apply(show, label)

        self.dialog = ExpressionDialog(title, expression_of(raw), self._variable_names(), self._evaluator(kind, element_key, raw), self.window())
        self.dialog.applied.connect(applied)
        self.dialog.open()
        return self.dialog

    # ------------------------------------------------------------ hotkey and Stream Deck URLs

    def _hotkey_finished(self) -> None:
        inp = self.current_input()
        if inp is None or self.hotkey is None or self._updating:
            return
        text = self.hotkey.keySequence().toString()
        problem = ""
        if text in RESERVED_HOTKEYS:
            problem = f"{text} is already a CentraLight shortcut: choose another key"
        else:
            other = next((i for i in self.show_data.inputs.values() if i.id != inp.id and i.hotkey and QKeySequence(i.hotkey).toString() == text), None) if text else None
            if other is not None:
                problem = f"{text} already sends “{other.name}”"
        self.hotkey_problem.setText(problem)
        self.hotkey_problem.setVisible(bool(problem))
        if problem:
            blocked = self.hotkey.blockSignals(True)  # putting it back mustn't count as another change
            self.hotkey.setKeySequence(QKeySequence(inp.hotkey))
            self.hotkey.blockSignals(blocked)
            return
        if text != inp.hotkey:
            self._apply(update_input(self.show_data, self.input_id, hotkey=text), f"Hotkey → {text}" if text else "Remove the hotkey")

    def copy_urls(self, button: QPushButton | None = None) -> str:
        """Copy this input's Stream Deck URLs (Base, cut, Send to Overlay, Clear from Overlay) to the clipboard."""
        inp = self.current_input()
        if inp is None:
            return ""
        base = f"{self._url_base()}/api/input/{inp.id}"
        lines = [f"{inp.name}: Stream Deck URLs (Website action, tick “GET request in background”)",
                 f"Send to Base:  {base}", f"Cut to Base:   {base}/cut"]
        lines += [f"Send to Overlay:  {base}/overlay", f"Clear from Overlay:  {base}/overlay/clear"]
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        if button is not None:
            button.setText("Copied ✓")
            QTimer.singleShot(2000, button, lambda: button.setText("Copy Stream Deck URLs"))  # (cancelled if it's gone)
        return text

    # ------------------------------------------------------------ edits

    def _apply(self, show: Show, text: str, merge_key: str | None = None) -> None:
        if not self._updating:
            self.document.apply(show, text, merge_key)

    # Each change gets a name for the History panel, e.g. "Paint E2: opacity 80 % → 60 %"

    def _rename_input(self, name: str) -> None:
        inp = self.current_input()
        if inp is not None and name.strip() and name.strip() != inp.name:
            self._apply(update_input(self.show_data, self.input_id, name=name), f"Rename {inp.name} → {name.strip()}")

    def _set_tile_colour(self, value: Any) -> None:
        inp = self.current_input()
        if inp is not None:
            spec = Spec("colour", "Tile colour", "colour", inp.colour)
            self._apply(update_input(self.show_data, self.input_id, colour=value), change_name(inp.name, spec, inp.colour, value))

    def _set_length(self, value: float) -> None:
        inp = self.current_input()
        if inp is not None and not self._updating and abs(value - inp.length) > 1e-6:
            self._apply(set_length(self.show_data, self.input_id, value), f"{inp.name}: length {value:g} s", f"{self.input_id}/length")

    def _set_end(self, value: Any) -> None:
        inp = self.current_input()
        if inp is not None and value in END_BEHAVIOURS and value != inp.end:
            label = dict(END_CHOICES).get(value, value)
            self._apply(set_length(self.show_data, self.input_id, inp.length, value), f"{inp.name}: at the end, {label.lower()}")

    def _set_transition(self, spec: Spec, value: Any) -> None:
        inp = self.current_input()
        if inp is None:
            return
        old = getattr(inp.transition, spec.path, spec.default)
        text = f"Transition {describe(spec, old)} → {describe(spec, value)}" if spec.path == "kind" else change_name("Transition", spec, old, value)
        self._apply(set_transition(self.show_data, self.input_id, **{spec.path: value}), text, f"{self.input_id}/transition/{spec.path}")

    def _set_house(self, fixture_id: str, level: Any, merge: bool = False) -> None:
        inp = self.current_input()
        if inp is None:
            return
        text = f"{self.show_data.house_label(fixture_id)}: {level_name(inp.house.get(fixture_id))} → {level_name(level)}"
        self._apply(set_house_level(self.show_data, self.input_id, fixture_id, level), text,
                    f"{self.input_id}/house/{fixture_id}" if merge else None)

    def _timed_clicked(self, box: QCheckBox, checked: bool) -> None:
        self.timed_requested.emit(not checked)
        inp = self.current_input()
        if inp is not None:  # (asked "which to keep" and cancelled: the box shows what the input really is)
            QTimer.singleShot(0, box, lambda: box.setChecked(self.current_input().infinite if self.current_input() else checked))

    def _gesture_time(self, key: str) -> float:
        """The element time a drag of this setting began at (a keyframe goes there, not wherever the playing
        playhead has got to by each step of the drag)."""
        return self._gesture_t.setdefault(key, self.element_time())

    def _gesture_done(self, key: str, final: bool) -> None:
        if final:  # released, or Enter: the next change is a new undo step
            self._gesture_t.pop(key, None)
            self._gesture += 1

    def _set_element(self, path: str, value: Any, final: bool = True) -> None:
        element = self.current_element()
        if element is None:
            return
        spec = next((s for s in element_specs(str(element.get("kind"))) if s.path == path), None)
        raw = get_path(element, path, None)
        key = f"{self.input_id}/{self.element_id}/{path}"
        if is_keyframed(raw):  # a keyframed setting: the change is a key at the playhead
            t = self._gesture_time(key)
            self._apply(set_key(self.show_data, self.input_id, self.element_id, path, t, value),
                        f"{element_name(element)}: {spec.title.lower() if spec else path} keyframe at {t:.2f} s",
                        f"{key}/key/{t:.3f}/{self._gesture}")
        else:
            text = change_name(element_name(element), spec, get_path(element, path, spec.default), value) if spec else f"Change {element_name(element)}"
            self._apply(set_element_value(self.show_data, self.input_id, self.element_id, path, with_base_value(raw, value)),
                        text, f"{key}/{self._gesture}")
        self._gesture_done(key, final)

    def _set_shape(self, path: str, value: Any, final: bool = True) -> None:
        shape = self.current_shape()
        if shape is None:
            return
        spec = next((s for s in SHAPE if s.path == path), None)
        raw = get_path(shape, path, None)
        key = f"{self.input_id}/{self.element_id}/{self.shape_id}/{path}"
        if is_keyframed(raw):
            t = self._gesture_time(key)
            self._apply(set_key(self.show_data, self.input_id, self.element_id, param_path(self.shape_id, path), t, value),
                        f"{shape_name(shape)}: {spec.title.lower() if spec else path} keyframe at {t:.2f} s",
                        f"{key}/key/{t:.3f}/{self._gesture}")
        else:
            text = change_name(shape_name(shape), spec, get_path(shape, path, spec.default), value) if spec else f"Change {shape_name(shape)}"
            self._apply(set_shape_value(self.show_data, self.input_id, self.element_id, self.shape_id, path, with_base_value(raw, value)),
                        text, f"{key}/{self._gesture}")
        self._gesture_done(key, final)

    def _shape_clicked(self, current, _previous) -> None:
        if self._updating or current is None:
            return
        shape_id = str(current.data(Qt.ItemDataRole.UserRole))
        if shape_id != self.shape_id:
            self.shape_id = shape_id
            self.shape_selected.emit(shape_id)
            self.refresh()

    def select_shape(self, shape_id: str | None) -> None:
        if shape_id != self.shape_id:
            self.shape_id = shape_id
            self.refresh()

    def _add_shape(self, shape_type: str) -> None:
        show, shape_id = add_shape(self.show_data, self.input_id, self.element_id, shape_type)
        if shape_id and self.document.apply(show, f"Add {SHAPE_TITLES[shape_type].lower()} {shape_id}"):
            self.shape_id = shape_id
            self.shape_selected.emit(shape_id)
            self.refresh()

    def _move_shape(self, step: int) -> None:
        shape = self.current_shape()
        if shape is not None:
            self.document.apply(move_shape(self.show_data, self.input_id, self.element_id, self.shape_id, step),
                                f"Move {shape_name(shape)} {'up' if step > 0 else 'down'}")

    def remove_selected_shape(self) -> None:
        """Remove (the button), or Delete in the Selected view: the shape shown here goes, one undo step."""
        self._remove_shape()

    def _remove_shape(self) -> None:
        shape = self.current_shape()
        if shape is not None and not self.show_data.read_only:
            shape_id, self.shape_id = self.shape_id, None
            self.document.apply(remove_shape(self.show_data, self.input_id, self.element_id, shape_id), f"Remove {shape_name(shape)}")
            self.shape_selected.emit("")

    def _remove_element(self) -> None:
        from ..input_edit import remove_element

        element = self.current_element()
        if element is not None:
            element_id, self.element_id = self.element_id, None
            self.document.apply(remove_element(self.show_data, self.input_id, element_id), f"Remove {element_name(element)}")
            self.element_removed.emit()

    def _lights_changed(self, _item, _column) -> None:
        """Ticking a group changes each of its lights in a burst: they're applied together, as one undo step."""
        if self._updating or self._lights_pending:
            return
        self._lights_pending = True
        QTimer.singleShot(0, self, self._apply_lights)

    def _apply_lights(self) -> None:
        self._lights_pending = False
        if self._updating or self.lights_tree is None:
            return
        try:
            self.lights_tree.topLevelItemCount()
        except RuntimeError:  # rebuilt meanwhile
            return
        excluded = []
        for g in range(self.lights_tree.topLevelItemCount()):
            group = self.lights_tree.topLevelItem(g)
            for c in range(group.childCount()):
                child = group.child(c)
                if child.checkState(0) != Qt.CheckState.Checked:
                    excluded.append(str(child.data(0, Qt.ItemDataRole.UserRole)))
        self._set_excluded(excluded)

    def _set_excluded(self, excluded: list[str]) -> None:
        element = self.current_element()
        if element is None:
            return
        mask = element.get("mask")
        old = set(mask.get("exclude", ()) or ()) if hasattr(mask, "get") else set()
        names = {fx.id: fx.name for fx in self.show_data.colour_fixtures()}
        self._apply(set_excluded(self.show_data, self.input_id, self.element_id, excluded),
                    lights_change_name(element_name(element), old, set(excluded), names))

    def _choose_file(self, kind: str) -> None:
        folder = self.show_data.folder
        if not folder:
            QMessageBox.information(self, "No show folder", "This show isn't saved in a folder, so pictures can't be added.")
            return
        pattern = "Pictures (*.jpg *.jpeg *.png *.bmp *.webp *.gif)" if kind == "image" else "Videos (*.mp4 *.mov *.m4v *.avi *.mkv *.webm *.wmv)"
        start = str(Path.home() / ("Pictures" if kind == "image" else "Videos"))
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", start, pattern)
        if path and self.show_data.folder == folder:  # (another show loaded meanwhile: the file was for the other one)
            self.use_file(Path(path))

    def use_file(self, source: Path) -> None:
        """Use a picture (or video) from anywhere on this PC: the element links to it and plays CentraLight's own copy,
        made on the links thread (the element changes when the copy is ready; one undo step). Without linked media
        (some tests): copied here and now."""
        element = self.current_element()
        if element is None:
            return
        if self.linked is not None:
            self.linked.choose(self.input_id, self.element_id, str(source), str(element.get("kind")))
            return
        relative = copy_media(self.show_data.folder, source)
        self._set_element("file", relative)

    def _show_link(self, field: FileField, element) -> None:
        original = element.get("original")
        original = original if isinstance(original, str) else ""
        linked = self.linked
        key = (self.input_id, self.element_id)
        copying = linked.copying.get(key, "") if linked is not None else ""
        problem = linked.problems.get(key, "") if linked is not None else ""
        state = linked.state(original) if linked is not None and original else None
        field.set_link(original, state, copying, problem)

    def _allow_link(self) -> None:
        element = self.current_element()
        original = element.get("original") if element is not None else None
        if self.linked is not None and isinstance(original, str) and original:
            self.linked.allow(original)

    def _update_link(self) -> None:
        element = self.current_element()
        original = element.get("original") if element is not None else None
        if self.linked is not None and isinstance(original, str) and original:
            self.linked.update_now(original)


class FileField(QWidget):
    """An Image or Video element's File row: the original it's linked to (or, for a picture from before links, the
    show's own copy), a warning icon when the original can't be found, and a line about the link: "Not checked yet ·
    Allow", "Update ready: used when it's not playing · Update now", "Copying…"."""

    edited = Signal(object, bool)
    choose_requested = Signal()
    allow_requested = Signal()
    update_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file = ""
        self._link: tuple = ("", None, "", "")
        self.path = ElidedLabel("(none)")
        self.path.setObjectName("RowDetail")
        self.warning = QLabel()
        self.warning.setPixmap(icons.line_icon(icons.draw_warning, theme.WARN).pixmap(16, 16))
        self.warning.setToolTip("The original can't be found")
        self.warning.hide()
        choose = QPushButton("Choose…")
        choose.setToolTip("Link this element to a picture or video anywhere on this PC (CentraLight plays its own copy)")
        choose.clicked.connect(self.choose_requested.emit)
        self.status = QLabel()
        self.status.setObjectName("RowDetail")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        self.action = QPushButton()
        self.action.clicked.connect(self._action_clicked)
        self.action.hide()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self.warning)
        top.addWidget(self.path, 1)
        top.addWidget(choose)
        below = QHBoxLayout()
        below.setContentsMargins(0, 0, 0, 0)
        below.setSpacing(8)
        below.addWidget(self.status, 1)
        below.addWidget(self.action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addLayout(below)

    def set_value(self, value: Any) -> None:
        self._file = str(value) if isinstance(value, str) else ""
        self._redraw()

    def set_link(self, original: str, state, copying: str = "", problem: str = "") -> None:
        """state: the link's LinkState (linked_media.py), or None when there's no link."""
        self._link = (original, state, copying, problem)
        self._redraw()

    def _action_clicked(self) -> None:
        (self.allow_requested if self.action.property("does") == "allow" else self.update_requested).emit()

    def _redraw(self) -> None:
        from ..links import file_name

        original, state, copying, problem = self._link
        warn, text, action = False, "", ""
        if copying:
            self.path.setText(copying)
            self.path.setToolTip(copying)
            text = f"Copying {file_name(copying)} into the show…"
        elif original:
            self.path.setText(original)
            self.path.setToolTip(f"{original}\nCentraLight plays its own copy: {self._file or '(none yet)'}")
            status = state.status if state is not None else "ok"
            detail = state.detail if state is not None else ""
            if status == "unchecked":
                text, action = "Not checked yet: CentraLight reads a linked file only once someone here allows it.", "allow"
            elif status == "refused":
                warn, text = True, f"This link can't be used: {detail}."
            elif status == "checking":
                text = "Checking the original…"
            elif status == "update":
                text, action = "Update ready: used when it's not playing.", "update"
            elif status == "missing":
                warn, text = True, f"Original not found: {original}. CentraLight is using its own copy."
            elif status == "unreachable":
                warn = True
                text = f"Original can't be reached: {original}{f' ({detail})' if detail else ''}. CentraLight is using its own copy."
            elif status == "bad":
                warn, text = True, f"The original can't be used ({detail}). CentraLight is using its own copy."
        else:
            self.path.setText(self._file or "(none)")
            self.path.setToolTip(f"The show's own copy: {self._file}" if self._file else "")
        if problem and not copying:
            warn, text = True, problem
        self.warning.setVisible(warn)
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.action.setProperty("does", action)
        self.action.setText("Allow" if action == "allow" else "Update now" if action == "update" else "")
        self.action.setToolTip("Let CentraLight read this file and copy in its changes" if action == "allow" else
                               "Use the new version now, even if it's on the lights" if action == "update" else "")
        self.action.setVisible(bool(action))
