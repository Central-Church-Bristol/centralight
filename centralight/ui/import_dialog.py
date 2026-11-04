"""Import from another show… for inputs (the Dashboard) and show automations (the Automation tab), M13.

Like the Layout tab's import: pick a show, tick what to bring, read the summary of what changes here, then Import or
Cancel (show_import.py does the work). The other show isn't loaded and nothing is sent to the lights.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QPlainTextEdit,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..ai_guide import links_of
from ..links import AllowedLinks
from ..log import log_exception
from ..model import Show
from ..show_import import ImportPlan, plan_import, summary
from ..show_io import ShowError
from ..show_io import load_show as read_show

INPUTS = "inputs"
AUTOMATIONS = "automations"


class NotesDialog(QDialog):
    """Notes about a show (M13): a few lines of plain text, shown in the show menu, on the Dashboard, on the phone page
    and in the AI guide."""

    def __init__(self, parent: QWidget | None, name: str, notes: str) -> None:
        from ..model import NOTES_LIMIT

        super().__init__(parent)
        self.setWindowTitle(f"Notes about “{name}”")
        self.setObjectName("NotesDialog")
        intro = QLabel("A few lines about this show: what it's for, what to send first, anything to remember. They show "
                       "in the Show menu, on the Dashboard and on the phone page, and AI tools read them too.")
        intro.setWordWrap(True)
        self.editor = QPlainTextEdit(notes)
        self.editor.setPlaceholderText("e.g. Sunday morning service. Send Warm welcome as people arrive.")
        self.editor.setMinimumSize(420, 160)
        self.count = QLabel()
        self.count.setObjectName("Hint")
        self._limit = NOTES_LIMIT
        self.editor.textChanged.connect(self._counted)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.count)
        layout.addWidget(buttons)
        self._counted()

    def text(self) -> str:
        return self.editor.toPlainText()

    def _counted(self) -> None:
        used = len(self.editor.toPlainText())
        self.count.setText(f"{used} of {self._limit} characters" + (" (the rest is left out)" if used > self._limit else ""))


class ImportItemsDialog(QDialog):
    """Pick a show and tick some of its inputs (or show automations); a summary comes first (Import / Cancel)."""

    def __init__(self, parent: QWidget | None, shows: list, show: Show, kind: str = INPUTS,
                 live: frozenset[str] = frozenset(), allowed: AllowedLinks | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._show = show
        self._live = live
        self._allowed = allowed
        self._read_shows: dict[str, Show | None] = {}
        self.other: Show | None = None
        self.plan: ImportPlan | None = None
        what = "inputs" if kind == INPUTS else "automations"
        self.setWindowTitle("Import from another show")
        self.setObjectName("ImportItemsDialog")
        intro = QLabel(f"This copies {what} from another show into this one, with the pictures and videos they use. "
                       "They keep their ids, so a Stream Deck key works in whichever show is loaded. The other show isn't "
                       "loaded and nothing is sent to the lights; Ctrl+Z undoes it.")
        intro.setWordWrap(True)
        self.show_pick = QComboBox()
        for info in shows:
            self.show_pick.addItem(info.name, info.folder)
        self.items = QListWidget()
        self.items.setObjectName("ImportList")
        self.items.setMinimumHeight(160)
        form = QFormLayout()
        form.addRow("Show", self.show_pick)
        form.addRow("Inputs" if kind == INPUTS else "Automations", self.items)
        self.bring_inputs = QCheckBox("Import the inputs they send too, if this show hasn't got them")
        self.bring_inputs.setChecked(True)
        self.bring_inputs.setVisible(kind == AUTOMATIONS)
        self.replace_choice = QRadioButton("Replace this show's ones that have the same id")
        self.keep_both = QRadioButton("Keep both (the imported ones get new ids)")
        self.replace_choice.setChecked(True)
        self.summary = QLabel()
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # (names come from another show's files)
        self.summary.setMinimumWidth(460)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.bring_inputs)
        layout.addWidget(self.replace_choice)
        layout.addWidget(self.keep_both)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)
        self.show_pick.currentIndexChanged.connect(self._show_picked)
        self.items.itemChanged.connect(lambda _item: self._update())
        for box in (self.bring_inputs, self.replace_choice, self.keep_both):
            box.toggled.connect(lambda _on: self._update())
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
        blocked = self.items.blockSignals(True)
        self.items.clear()
        if self.other is not None:
            things = self.other.ordered_inputs() if self.kind == INPUTS else self.other.ordered_automations()
            for thing in things:
                item = QListWidgetItem(thing.name)
                item.setData(Qt.ItemDataRole.UserRole, thing.id)
                item.setToolTip(f"id: {thing.id}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.items.addItem(item)
        self.items.blockSignals(blocked)
        self._update()

    def ticked(self) -> list[str]:
        return [self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())
                if self.items.item(i).checkState() == Qt.CheckState.Checked]

    def tick(self, ids) -> None:
        """Tick these (by id): for tests and keyboard users."""
        for i in range(self.items.count()):
            item = self.items.item(i)
            if item.data(Qt.ItemDataRole.UserRole) in ids:
                item.setCheckState(Qt.CheckState.Checked)

    @property
    def keeping_both(self) -> bool:
        return self.keep_both.isChecked()

    def _update(self) -> None:
        other = self.other
        if other is None:
            self.plan = None
            self.summary.setText("That show couldn't be read (the session log says why).")
            self.import_button.setEnabled(False)
            for box in (self.replace_choice, self.keep_both):
                box.hide()
            return
        ids = self.ticked()
        if self.kind == INPUTS:
            plan = plan_import(self._show, other, inputs=ids, live=self._live)
        else:
            plan = plan_import(self._show, other, automations=ids, bring_inputs=self.bring_inputs.isChecked(), live=self._live)
        self.plan = plan
        for box in (self.replace_choice, self.keep_both):
            box.setVisible(bool(plan.clashes))
        if not ids:
            self.summary.setText(f"Tick the {'inputs' if self.kind == INPUTS else 'automations'} to bring here.")
            self.import_button.setEnabled(False)
            return
        lines = summary(plan, self._show, other, self.keeping_both)
        links = self.links()
        if links:
            lines.insert(-1, "Their pictures and videos are linked to these originals; importing allows CentraLight to "
                             "copy changes in from them: " + ", ".join(links) + ".")
        self.summary.setText("What changes here:\n" + "\n".join(f"• {line}" for line in lines))
        self.import_button.setEnabled(not self._show.read_only)

    def links(self) -> list[str]:
        """Links to originals in the inputs being imported that aren't followed yet on this PC (importing allows them)."""
        if self.plan is None or self.other is None:
            return []
        found = [link for input_id in self.plan.inputs for link in links_of(self.other.inputs[input_id])]
        unknown = [link for link in found if self._allowed is None or not self._allowed.allowed(link)]
        return list(dict.fromkeys(unknown))
