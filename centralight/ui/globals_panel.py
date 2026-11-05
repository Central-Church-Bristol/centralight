"""Variables in the Editor.

- GlobalsPanel (the details panel's Show variables tab): the show's variables. Each has a
  name, a type (number, colour or switch), the value it starts at, a range for numbers, and
  "Show on the dashboard". Every input's expressions can use them; the Dashboard and the
  Stream Deck (/api/var/<name>?value=…) set them while the show runs.
- LocalsPanel (on the Input tab): one input's own variables. Each is a number or a colour and
  can have an expression (=) of its own, e.g. width = 0.1 + audio.bass * 0.3.

Changes go through the show document, so they can be undone: globals in the show's history,
locals in the input's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..expressions import ExpressionError, compile_expression, describe_value
from ..input_edit import add_local, remove_local, rename_local, update_local
from ..model import GlobalVar, Show
from ..params import base_value, expression_of, with_base_value, with_expression
from ..show_edit import add_global, remove_global, rename_global, update_global
from ..variables import check_variable_name, local_order
from .document import ShowDocument
from .editor_widgets import ChoiceField, ColourField, NumberField
from .expression_edit import Evaluator, ExprButton, ExpressionDialog
from .widgets import Panel, discard

GLOBAL_TYPES = (("number", "Number"), ("colour", "Colour"), ("switch", "Switch"))
ON_LOAD_CHOICES = (("keep", "Keep its last value"), ("reset", "Go back to its starting value"))  # (M14)
LOCAL_TYPES = (("number", "Number"), ("colour", "Colour"))


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("RowDetail")
    return label


def _remove_button(tip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("RemoveButton")
    button.setText("✕")
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _row(*widgets, stretch_index: int = -1) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    for i, widget in enumerate(widgets):
        row.addWidget(widget, 1 if i == stretch_index else 0)
    return row


class _NameEdit(QLineEdit):
    """A variable's name: checked when you finish typing; a bad name shows why and goes back."""

    renamed = Signal(str, str)  # old, new

    def __init__(self, name: str, taken: Callable[[], set[str]], problem: QLabel) -> None:
        super().__init__(name)
        self._name = name
        self._taken = taken
        self._problem = problem
        self.setMaxLength(32)
        self.setCursorPosition(0)  # a long name shows its start
        self.editingFinished.connect(self._finished)

    def _finished(self) -> None:
        new = self.text().strip()
        if new == self._name:
            self._problem.setVisible(False)
            return
        problem = check_variable_name(new, self._taken() - {self._name})
        if problem:
            self._problem.setText(problem)
            self._problem.setVisible(True)
            self.setText(self._name)
            return
        self._problem.setVisible(False)
        self.renamed.emit(self._name, new)


# ---------------------------------------------------------------- globals


class GlobalCard(QFrame):
    """One show variable."""

    def __init__(self, panel: GlobalsPanel, var: GlobalVar) -> None:
        super().__init__()
        self.setObjectName("VariableCard")
        self.panel = panel
        self.name = var.name
        self.problem = QLabel("")
        self.problem.setObjectName("ExpressionProblem")
        self.problem.setWordWrap(True)
        self.problem.setVisible(False)
        self.name_edit = _NameEdit(var.name, panel.taken_names, self.problem)
        self.name_edit.setToolTip("The name expressions use (letters, digits and _)")
        self.name_edit.renamed.connect(panel.rename)
        self.kind = ChoiceField(GLOBAL_TYPES)
        self.kind.set_value(var.type)
        self.kind.edited.connect(lambda value, _f: panel.change(self.name, type=value))
        remove = _remove_button(f"Remove the show variable {var.name}")
        remove.clicked.connect(lambda: panel.remove(self.name))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(_row(self.name_edit, self.kind, remove, stretch_index=0))
        layout.addWidget(self.problem)

        if var.type == "number":
            self.default = NumberField(var.min, var.max, (var.max - var.min) / 100 if var.max > var.min else 0.01)
            self.default.edited.connect(lambda value, _f: panel.change(self.name, merge=True, default=round(float(value), 4)))
            self.low = self._spin(var.min, "The smallest value")
            self.high = self._spin(var.max, "The biggest value")
            self.low.valueChanged.connect(lambda value: panel.change(self.name, min=value))
            self.high.valueChanged.connect(lambda value: panel.change(self.name, max=value))
            to = QLabel("to")
            layout.addLayout(_row(_label("Starting value"), self.default, stretch_index=1))
            layout.addLayout(_row(_label("From"), self.low, to, self.high, stretch_index=-1))
        elif var.type == "colour":
            self.default = ColourField(live=False)  # (the starting value doesn't change the lights now: OK applies it)
            self.default.edited.connect(lambda value, _f: panel.change(self.name, default=str(value)))
            layout.addLayout(_row(_label("Starting value"), self.default, stretch_index=1))
        else:
            self.default = QCheckBox("Starting value: on")
            self.default.toggled.connect(lambda on: panel.change(self.name, default=bool(on)))
            layout.addWidget(self.default)
        # when the show loads (M14): it keeps its last value (as before), or goes back to its starting value
        self.on_load = ChoiceField(ON_LOAD_CHOICES)
        self.on_load.setToolTip("When this show is loaded (by you, the Stream Deck or a global automation, or as the PC "
                                "starts): keep the value it had, or go back to the starting value above. Reopening "
                                "CentraLight after a quit or a crash always keeps it.")
        self.on_load.edited.connect(lambda value, _f: panel.change(self.name, on_load=str(value)))
        layout.addWidget(_label("When the show loads"))  # (above it: side by side, it's too wide for the panel)
        layout.addWidget(self.on_load)

        self.dashboard = QCheckBox("On the dashboard")
        self.dashboard.setToolTip("Put a control for it in the Dashboard's Variables panel")
        self.dashboard.toggled.connect(lambda on: panel.change(self.name, dashboard=bool(on)))
        copy = QPushButton("Copy URL")
        copy.setToolTip("Copy a Stream Deck URL that sets this variable (change the value at the end)")
        copy.clicked.connect(lambda: panel.copy_url(self.name, copy))
        layout.addLayout(_row(self.dashboard, QWidget(), copy, stretch_index=1))
        self.show_values(var)

    @staticmethod
    def _spin(value: float, tip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName("NumberSpin")
        spin.setRange(-100000.0, 100000.0)
        spin.setDecimals(2)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        spin.setToolTip(tip)
        spin.setFixedWidth(spin.fontMetrics().horizontalAdvance("-100000.00") + 16)
        return spin

    def show_values(self, var: GlobalVar) -> None:
        widgets = [self.dashboard, self.default, self.on_load] + ([self.low, self.high] if var.type == "number" else [])
        blocked = [w.blockSignals(True) for w in widgets]
        self.dashboard.setChecked(var.dashboard)
        self.on_load.set_value(var.on_load)
        if var.type == "number":
            self.default.set_value(float(var.default))
            self.low.setValue(var.min)
            self.high.setValue(var.max)
        elif var.type == "colour":
            self.default.set_value(var.default)
        else:
            self.default.setChecked(bool(var.default))
        for w, b in zip(widgets, blocked):
            w.blockSignals(b)


class GlobalsPanel(QWidget):
    """The Show variables tab."""

    def __init__(self, document: ShowDocument, url_base: Callable[[], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self._url_base = url_base
        self.cards: dict[str, GlobalCard] = {}
        self._key: tuple = ()
        panel = Panel("Show variables")
        panel.body.addWidget(_hint(
            "Shared by every input's expressions. Set them from the Dashboard (tick “On the dashboard”), "
            "from the Stream Deck, or here."
        ))
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        panel.body.addLayout(self.list)
        self.empty = _hint("No show variables yet.")
        panel.body.addWidget(self.empty)
        self.add_button = QToolButton()
        self.add_button.setObjectName("MenuButton")
        self.add_button.setText("+ Add a show variable  ▾")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        for kind, label in GLOBAL_TYPES:
            menu.addAction(label, lambda k=kind: self.add(k))
        self.add_button.setMenu(menu)
        panel.body.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        layout.addStretch(1)
        self.refresh(document.show)

    @property
    def show_data(self) -> Show:
        return self.document.show

    def taken_names(self) -> set[str]:
        return {g.name for g in self.show_data.globals}

    def refresh(self, show: Show) -> None:
        key = tuple((g.name, g.type, g.min, g.max) for g in show.globals)
        if key != self._key:
            self._key = key
            for card in self.cards.values():
                self.list.removeWidget(card)
                discard(card)
            self.cards = {}
            for var in show.globals:
                card = GlobalCard(self, var)
                self.list.addWidget(card)
                self.cards[var.name] = card
        else:
            for var in show.globals:
                self.cards[var.name].show_values(var)
        self.empty.setVisible(not show.globals)
        read_only = show.read_only
        self.add_button.setEnabled(not read_only)
        for card in self.cards.values():
            card.setEnabled(not read_only)

    # --- changes (each is one undo step in the show's history)

    def add(self, kind: str) -> str:
        show, name = add_global(self.show_data, kind)
        return name if self.document.apply(show, f"Add show variable {name}") else ""

    def change(self, name: str, merge: bool = False, **changes: Any) -> None:
        old = next((g for g in self.show_data.globals if g.name == name), None)
        if old is None:
            return
        show = update_global(self.show_data, name, **changes)
        new = next((g for g in show.globals if g.name == name), old)
        if "type" in changes:
            text = f"Show variable {name}: type {old.type} → {new.type}"
        elif "dashboard" in changes:
            text = f"Show variable {name}: {'show on' if new.dashboard else 'hide from'} the dashboard"
        elif "on_load" in changes:
            if new.on_load == old.on_load:
                return
            text = (f"Show variable {name}: when the show loads, "
                    + ("go back to its starting value" if new.on_load == "reset" else "keep its last value"))
        elif "default" in changes:
            text = f"Show variable {name}: starting value {self._describe(old)} → {self._describe(new)}"
        else:
            text = f"Show variable {name}: range {old.min:g}–{old.max:g} → {new.min:g}–{new.max:g}"
        self.document.apply(show, text, f"global/{name}/default" if merge else None)

    @staticmethod
    def _describe(var: GlobalVar) -> str:
        if var.type == "switch":
            return "on" if var.default else "off"
        if var.type == "colour":
            return str(var.default)
        return f"{float(var.default):g}"

    def rename(self, old: str, new: str) -> None:
        self.document.apply(rename_global(self.show_data, old, new), f"Rename show variable {old} → {new}")

    def remove(self, name: str) -> None:
        self.document.apply(remove_global(self.show_data, name), f"Remove show variable {name}")

    def copy_url(self, name: str, button: QPushButton | None = None) -> str:
        var = next((g for g in self.show_data.globals if g.name == name), None)
        example = {"number": f"{float(var.default):g}" if var else "0.5", "colour": "FF6F59", "switch": "toggle"}.get(var.type if var else "number")
        url = f"{self._url_base()}/api/var/{name}?value={example}"
        QGuiApplication.clipboard().setText(url)
        if button is not None:
            button.setText("Copied ✓")
            QTimer.singleShot(2000, button, lambda: button.setText("Copy URL"))  # (cancelled if it's gone)
        return url


# ---------------------------------------------------------------- locals


class LocalRow(QFrame):
    """One local variable of the input being edited."""

    def __init__(self, panel: LocalsPanel, item, problem_text: str) -> None:
        super().__init__()
        self.setObjectName("VariableCard")
        self.panel = panel
        self.name = str(item.get("name"))
        self.kind_value = item.get("type", "number") if item.get("type") in ("number", "colour") else "number"
        self.problem = QLabel(problem_text)
        self.problem.setObjectName("ExpressionProblem")
        self.problem.setWordWrap(True)
        self.problem.setVisible(bool(problem_text))
        self.name_edit = _NameEdit(self.name, panel.taken_names, self.problem)
        self.name_edit.renamed.connect(panel.rename)
        self.kind = ChoiceField(LOCAL_TYPES)
        self.kind.set_value(self.kind_value)
        self.kind.edited.connect(lambda value, _f: panel.change_type(self.name, value))
        if self.kind_value == "colour":
            self.value = ColourField()
            # (a pick with Preview: each change shows at once, and OK makes it one undo step)
            self.value.edited.connect(lambda value, _f: panel.change_value(self.name, str(value)))
            self.value.drag = panel.document
        else:
            self.value = QDoubleSpinBox()
            self.value.setObjectName("NumberSpin")
            self.value.setRange(-100000.0, 100000.0)
            self.value.setDecimals(3)
            self.value.setSingleStep(0.01)
            self.value.setKeyboardTracking(False)
            self.value.valueChanged.connect(lambda value: panel.change_value(self.name, round(float(value), 4), merge=True))
        self.expr = ExprButton()
        self.expr.clicked.connect(lambda: panel.edit_expression(self.name))
        remove = _remove_button(f"Remove the local variable {self.name}")
        remove.clicked.connect(lambda: panel.remove(self.name))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(_row(self.name_edit, self.kind, remove, stretch_index=0))
        self.value.setToolTip("Its value (or the value its expression starts from)")
        layout.addLayout(_row(self.value, self.expr, stretch_index=0))
        layout.addWidget(self.problem)

    def show_value(self, item, problem: str) -> None:
        raw = item.get("value")
        kind = self.kind_value
        value = base_value(raw, 0.0, (1.0, 1.0, 1.0) if kind == "colour" else 0.0, kind)
        blocked = self.value.blockSignals(True)
        if kind == "colour":
            self.value.set_value(describe_value(value) if type(value) is tuple else value)
        elif not self.value.hasFocus():
            self.value.setValue(float(value))
        self.value.blockSignals(blocked)
        self.expr.set_state(expression_of(raw), problem)
        if problem and expression_of(raw):
            self.problem.setText(problem)
            self.problem.setVisible(True)


class LocalsPanel(Panel):
    """The Input tab's "Local variables"."""

    def __init__(self, document: ShowDocument, evaluator: Callable[[str, str, Any], Evaluator],
                 names: Callable[[], list[str]], parent: QWidget | None = None) -> None:
        super().__init__("Local variables", parent)
        self.document = document
        self._evaluator = evaluator  # (kind, local name, base raw) -> Evaluator
        self._names = names
        self.input_id = ""
        self.rows: dict[str, LocalRow] = {}
        self._key: tuple = ()
        self.body.addWidget(_hint("Only this input's expressions can use these. Each is worked out afresh at every moment."))
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        self.body.addLayout(self.list)
        self.add_button = QToolButton()
        self.add_button.setObjectName("MenuButton")
        self.add_button.setText("+ Add a local variable  ▾")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        for kind, label in LOCAL_TYPES:
            menu.addAction(label, lambda k=kind: self.add(k))
        self.add_button.setMenu(menu)
        self.body.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)

    def taken_names(self) -> set[str]:
        inp = self.document.show.inputs.get(self.input_id)
        return {str(item.get("name")) for item in inp.locals} if inp else set()

    def refresh(self, input_id: str) -> None:
        self.input_id = input_id
        inp = self.document.show.inputs.get(input_id)
        items = list(inp.locals) if inp is not None else []
        _order, errors = local_order(items)
        known = set(self._names())
        key = tuple((str(item.get("name")), item.get("type")) for item in items)
        if key != self._key:
            self._key = key
            for row in self.rows.values():
                self.list.removeWidget(row)
                discard(row)
            self.rows = {}
            for item in items:
                row = LocalRow(self, item, "")
                self.list.addWidget(row)
                self.rows[str(item.get("name"))] = row
        for item in items:
            name = str(item.get("name"))
            problem = errors.get(name, "") or self._unknown_names(expression_of(item.get("value")), known)
            self.rows[name].show_value(item, problem)

    @staticmethod
    def _unknown_names(text: str, known: set[str]) -> str:
        if not text:
            return ""
        try:
            missing = sorted(compile_expression(text).names - known - {"t", "time", "value"})
        except ExpressionError as exc:
            return str(exc)
        return f'there\'s no variable called "{missing[0]}"' if missing else ""

    def _item(self, name: str):
        inp = self.document.show.inputs.get(self.input_id)
        return next((item for item in inp.locals if item.get("name") == name), None) if inp else None

    # --- changes (one undo step each, in the input's history)

    def add(self, kind: str) -> str:
        show, name = add_local(self.document.show, self.input_id, kind)
        return name if self.document.apply(show, f"Add local {name}") else ""

    def rename(self, old: str, new: str) -> None:
        self.document.apply(rename_local(self.document.show, self.input_id, old, new), f"Rename local {old} → {new}")

    def change_type(self, name: str, kind: str) -> None:
        self.document.apply(update_local(self.document.show, self.input_id, name, type=kind), f"Local {name}: type → {kind}")

    def change_value(self, name: str, value: Any, merge: bool = False) -> None:
        item = self._item(name)
        if item is None:
            return
        raw = item.get("value")
        old = base_value(raw, 0.0, 0.0, "colour" if item.get("type") == "colour" else "number")
        old_text = describe_value(old) if type(old) is tuple or isinstance(old, float) else str(old)
        new_text = value if isinstance(value, str) else f"{value:g}"
        self.document.apply(update_local(self.document.show, self.input_id, name, value=with_base_value(raw, value)),
                            f"Local {name}: {old_text} → {new_text}", f"{self.input_id}/local/{name}" if merge else None)

    def remove(self, name: str) -> None:
        self.document.apply(remove_local(self.document.show, self.input_id, name), f"Remove local {name}")

    def edit_expression(self, name: str) -> ExpressionDialog | None:
        item = self._item(name)
        if item is None:
            return None
        kind = "colour" if item.get("type") == "colour" else "number"
        raw = item.get("value")
        names = [n for n in self._names() if n != name]
        dialog = ExpressionDialog(f"local {name}", expression_of(raw), names, self._evaluator(kind, name, raw), self.window())
        dialog.applied.connect(lambda text: self.document.apply(
            update_local(self.document.show, self.input_id, name, value=with_expression(raw, text, base_value(raw, 0.0, 0.0, kind))),
            f"Local {name}: = {text}" if text else f"Local {name}: remove the expression"))
        dialog.open()
        return dialog
