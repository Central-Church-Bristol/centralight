"""The Dashboard's Variables panel: the show variables marked "show on dashboard", as a
slider (numbers), a colour (colours) or a switch (switches). Moving one changes the lights
straight away (inputs whose expressions use it follow), the same as the Stream Deck's
/api/var URLs. The values shown come back from the engine, so a Stream Deck change shows here too.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QFormLayout, QHBoxLayout, QLabel, QPushButton, QWidget

from ..expressions import describe_value
from ..model import GlobalVar
from .editor_widgets import ColourField, NumberField
from .widgets import ElidedLabel, Panel, ToggleSwitch

HOLD_AFTER_EDIT = 0.6  # seconds: the engine's value doesn't move a control you've just moved


class VariablesPanel(Panel):
    changed = Signal(str, object)  # name, value (a number, "#RRGGBB", or "on" / "off")
    edit_requested = Signal()  # Edit: the Show variables window (8 Nov)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Variables", parent)
        # (8 Nov: Edit beside the title, so the show variables are a click away, even with none shown here yet)
        self.edit_button = QPushButton("Edit")
        self.edit_button.setToolTip("Add or change the show variables. Tick “On the dashboard” for the ones to show "
                                    "here.")
        self.edit_button.clicked.connect(self.edit_requested.emit)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        if self.title_label is not None:
            self.body.removeWidget(self.title_label)
            title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        title_row.addWidget(self.edit_button)
        self.body.insertLayout(0, title_row)
        self.hint = QLabel("None here yet.")
        self.hint.setToolTip("Show variables ticked “On the dashboard” appear here. Edit opens them all.")
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(8)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)  # a long name puts its control underneath
        self.body.addLayout(self.form)
        self.controls: dict[str, Any] = {}
        self._kinds: dict[str, str] = {}
        self._key: tuple = ()
        self._touched: dict[str, float] = {}

    def set_globals(self, globals_: tuple[GlobalVar, ...]) -> None:
        shown = tuple(g for g in globals_ if g.dashboard)
        key = tuple((g.name, g.type, g.min, g.max) for g in shown)
        if key == self._key:
            return
        self._key = key
        while self.form.rowCount():
            self.form.removeRow(0)
        self.controls.clear()
        self._kinds.clear()
        for var in shown:
            control = self._control(var)
            label = ElidedLabel(var.name)
            label.setMaximumWidth(110)
            label.setToolTip(f"{var.name} ({var.type})")
            self.form.addRow(label, control)
            self.controls[var.name] = control
            self._kinds[var.name] = var.type
        self.hint.setVisible(not shown)

    def _control(self, var: GlobalVar):
        name = var.name
        if var.type == "number":
            step = (var.max - var.min) / 100 if var.max > var.min else 0.01
            field = NumberField(var.min, var.max, step)
            field.edited.connect(lambda value, _final, n=name: self._user_changed(n, float(value)))
            return field
        if var.type == "colour":
            field = ColourField()
            field.edited.connect(lambda value, _final, n=name: self._user_changed(n, str(value)))
            return field
        switch = ToggleSwitch()
        switch.setAccessibleName(name)
        switch.clicked.connect(lambda *_a, n=name, s=switch: self._user_changed(n, "on" if s.isChecked() else "off"))
        holder = QWidget()
        holder.switch = switch  # type: ignore[attr-defined]
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(switch)
        row.addStretch(1)
        return holder

    def _user_changed(self, name: str, value: Any) -> None:
        self._touched[name] = time.monotonic()
        self.changed.emit(name, value)

    def set_values(self, values) -> None:
        """The engine's current values (from its frames)."""
        now = time.monotonic()
        for name, control in self.controls.items():
            if name not in values or now - self._touched.get(name, -1e9) < HOLD_AFTER_EDIT:
                continue
            value = values[name]
            kind = self._kinds.get(name)
            if kind == "number":
                if not control.isAncestorOf(QApplication.focusWidget()):  # not while you're typing in it
                    control.set_value(float(value))
            elif kind == "colour" and type(value) is tuple:
                control.set_value(describe_value(value))
            elif kind == "switch":
                switch = control.switch
                if switch.isChecked() != bool(value):
                    blocked = switch.blockSignals(True)
                    switch.setChecked(bool(value))
                    switch.blockSignals(blocked)
