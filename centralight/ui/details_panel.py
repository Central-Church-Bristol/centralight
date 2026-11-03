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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..expressions import Env, ExpressionError, compile_expression, describe_value
from ..input_edit import (
    add_shape,
    copy_media,
    find_element,
    move_shape,
    remove_shape,
    set_element_value,
    set_excluded,
    set_house_level,
    set_house_param,
    set_shape_value,
    set_transition,
    update_input,
)
from ..model import Input, Show
from ..params import base_value, expression_of, with_base_value, with_expression
from ..schema import SHAPE, TRANSITION, Spec, element_specs, get_path, visible
from ..step_names import change_name, describe, element_name, level_name, lights_change_name, lower_first, shape_name
from ..variables import AUDIO_SILENT, evaluate_locals
from .document import ShowDocument
from .editor_widgets import ChoiceField, ColourField, NumberField, SourceField
from .expression_edit import Evaluator, ExprButton, ExpressionDialog
from .globals_panel import GlobalsPanel, LocalsPanel
from .widgets import ElidedLabel, FlowLayout, Panel

KIND_TITLES = {"paint": "Paint", "image": "Image", "video": "Video", "ndi": "NDI feed"}
MIN_WIDTH = 340
SPECIAL_NAMES = {"t", "time", "value", "pi"}
RESERVED_HOTKEYS = {"Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z", "Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+,"}
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
        self._tab = 0
        self.locals_panel: LocalsPanel | None = None
        self.globals_panel: GlobalsPanel | None = None
        self.hotkey: QKeySequenceEdit | None = None
        self.hotkey_problem: QLabel | None = None
        self.dialog: ExpressionDialog | None = None
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
        self.input_id, self.element_id, self.shape_id = input_id, element_id, shape_id
        self.refresh(self.show_data)

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
            return ("input", inp.id, visible_transition, house, tuple(fx.id for fx in self.show_data.colour_fixtures()))
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
        self.setWidget(content)
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

    def _with_expr(self, field: QWidget, key: str, on_click: Callable[[], None]) -> QWidget:
        """field with an "=" button after it (for settings that can have an expression)."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(field, 1)
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
        mode.setChecked(True)
        mode.setEnabled(False)
        mode.setToolTip("Timed sequences, with a length and keyframes, arrive in a later update")
        form.addRow(mode)
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
        tabs.addTab(globals_page, "Globals")
        tabs.setCurrentIndex(self._tab)
        tabs.currentChanged.connect(self._tab_switched)
        return tabs

    def _tab_switched(self, index: int) -> None:
        self._tab = index
        self.tab_changed.emit(index)

    def globals_open(self) -> bool:
        """True while the Globals tab is showing (the Editor's undo then uses the show's history)."""
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
        for spec in specs:
            if not visible(spec, element, specs):
                continue
            field = self._field_for(spec, lambda value, final, path=spec.path: self._set_element(path, value))
            if spec.type == "file":
                field.choose_requested.connect(lambda kind=kind: self._choose_file(kind))
            key = f"element:{spec.path}"
            self._fields[key] = field
            if spec.animatable and spec.type in ("number", "colour"):
                field = self._with_expr(field, key, lambda k=key: self.edit_expression(k))
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
            field = self._field_for(spec, lambda value, final, path=spec.path: self._set_shape(path, value))
            key = f"shape:{spec.path}"
            self._fields[key] = field
            if spec.animatable and spec.type in ("number", "colour"):
                field = self._with_expr(field, key, lambda k=key: self.edit_expression(k))
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
            for spec in specs:
                field = self._fields.get(f"element:{spec.path}")
                if field is not None:
                    self._show_value(field, spec, get_path(element, spec.path, spec.default), self._exprs.get(f"element:{spec.path}"))
            shape = self.current_shape()
            if shape is not None:
                for spec in SHAPE:
                    field = self._fields.get(f"shape:{spec.path}")
                    if field is not None:
                        self._show_value(field, spec, get_path(shape, spec.path, spec.default), self._exprs.get(f"shape:{spec.path}"))
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

    def _show_value(self, field, spec: Spec, raw: Any, expr_button: ExprButton | None = None) -> None:
        keyed = hasattr(raw, "get") and "keys" in raw
        value = base_value(raw, 0.0, spec.default, "colour" if spec.type == "colour" else "number" if spec.type == "number" else "raw")
        if hasattr(field, "set_value"):
            field.set_value(value)
        elif isinstance(field, QLineEdit) and not field.hasFocus():
            field.setText(str(value))
        field.setEnabled(not keyed)  # an expression keeps the value editable: it's the expression's `value`
        if keyed:
            field.setToolTip("Keyframed: keyframes can be edited from the timeline in a later update")
        if expr_button is not None:
            text = expression_of(raw)
            expr_button.set_state(text, self.problem_with(text))

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
            title, kind = spec.label, "colour" if spec.type == "colour" else "number"
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
        """Copy this input's Stream Deck URLs (Base, cut, overlays 1-4) to the clipboard."""
        inp = self.current_input()
        if inp is None:
            return ""
        base = f"{self._url_base()}/api/input/{inp.id}"
        lines = [f"{inp.name}: Stream Deck URLs (Website action, tick “GET request in background”)",
                 f"Send to Base:  {base}", f"Cut to Base:   {base}/cut"]
        lines += [f"Overlay {n} on / off:  {base}/overlay/{n}" for n in (1, 2, 3, 4)]
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        if button is not None:
            button.setText("Copied ✓")
            QTimer.singleShot(2000, lambda: button.setText("Copy Stream Deck URLs") if button is not None else None)
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

    def _set_element(self, path: str, value: Any) -> None:
        element = self.current_element()
        if element is None:
            return
        spec = next((s for s in element_specs(str(element.get("kind"))) if s.path == path), None)
        raw = get_path(element, path, None)
        text = change_name(element_name(element), spec, get_path(element, path, spec.default), value) if spec else f"Change {element_name(element)}"
        self._apply(set_element_value(self.show_data, self.input_id, self.element_id, path, with_base_value(raw, value)),
                    text, f"{self.input_id}/{self.element_id}/{path}")

    def _set_shape(self, path: str, value: Any) -> None:
        shape = self.current_shape()
        if shape is None:
            return
        spec = next((s for s in SHAPE if s.path == path), None)
        raw = get_path(shape, path, None)
        text = change_name(shape_name(shape), spec, get_path(shape, path, spec.default), value) if spec else f"Change {shape_name(shape)}"
        self._apply(set_shape_value(self.show_data, self.input_id, self.element_id, self.shape_id, path, with_base_value(raw, value)),
                    text, f"{self.input_id}/{self.element_id}/{self.shape_id}/{path}")

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

    def _remove_shape(self) -> None:
        shape = self.current_shape()
        if shape is not None:
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
        if self._updating:
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
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", str(Path.home() / "Pictures"), pattern)
        if path:
            self.use_file(Path(path))

    def use_file(self, source: Path) -> None:
        """Copy a picture (or video) into the show's media folder and use it."""
        relative = copy_media(self.show_data.folder, source)
        self._set_element("file", relative)


class FileField(QWidget):
    edited = Signal(object, bool)
    choose_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = ElidedLabel("(none)")
        self.path.setObjectName("RowDetail")
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_requested.emit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.path, 1)
        layout.addWidget(choose)

    def set_value(self, value: Any) -> None:
        self.path.setText(str(value) if value else "(none)")
