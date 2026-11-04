""".clshow files in the window (M13): Show menu → Export…, Export everything… and Import… (and a double-clicked file).

The work itself is clshow.py's. Here: the questions (leave out pictures and videos? import as a new show? Replace or
Keep both?), and running the slow parts (packing, checking, copying) on a thread of their own while a progress window
shows, so the window keeps answering and the engine (and the lights) never notice.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..clshow import KIND_EVERYTHING, Package, keep_both_name, name_taken
from ..shows import same_folder

SETTINGS_ROW = "settings"
GLOBALS_ROW = "globals"


class Stopped(Exception):
    """Cancel was pressed: the work stops (clshow.py leaves nothing half-made behind)."""


def run_with_progress(parent: QWidget | None, title: str, work: Callable[[Callable[[int, int], None]], Any],
                      can_cancel: bool = True, state: dict | None = None) -> Any:
    """Run work(progress) on a thread of its own while a progress window shows; returns its result (or raises what it
    raised). progress(done, total) moves the bar (from the thread); Cancel, or state["stop"] set by the caller (the
    window, when a show has waited long enough), makes the next progress() raise Stopped."""
    state = state if state is not None else {}
    state.update({"done": 0, "total": 0, "stop": False})
    finished = threading.Event()

    def progress(done: int, total: int) -> None:
        state["done"], state["total"] = done, total
        if state["stop"]:
            raise Stopped()

    def run() -> None:
        try:
            state["result"] = work(progress)
        except BaseException as exc:  # (handed to the window's thread, which raises it)
            state["error"] = exc
        finally:
            finished.set()

    dialog = QProgressDialog("", "Cancel" if can_cancel else "", 0, 0, parent)
    label = QLabel(title)
    label.setTextFormat(Qt.TextFormat.PlainText)  # (show names come from files: never read as HTML)
    dialog.setLabel(label)
    dialog.setWindowTitle("CentraLight")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(400)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    if not can_cancel:
        dialog.setCancelButton(None)
    dialog.canceled.connect(lambda: state.__setitem__("stop", True))
    dialog.setValue(0)  # (so it shows after its 400 ms, not only once the work reports progress)
    thread = threading.Thread(target=run, name="CentraLight .clshow", daemon=True)
    thread.start()
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(100)

    def tick() -> None:
        total = int(state["total"])
        if total > 0:
            dialog.setMaximum(1000)
            dialog.setValue(min(999, int(1000 * int(state["done"]) / total)))
        if finished.is_set():
            loop.quit()

    timer.timeout.connect(tick)
    timer.start()
    loop.exec()
    timer.stop()
    dialog.close()
    dialog.deleteLater()
    if not finished.is_set():  # (CentraLight is closing: the work is stopped, and waited for, so nothing is half done)
        state["stop"] = True
        thread.join(10.0)
        raise Stopped()
    if "error" in state:
        raise state["error"]
    return state.get("result")


class ExportDialog(QDialog):
    """Before the file is chosen: leave out the pictures and videos?"""

    def __init__(self, parent: QWidget | None, name: str, everything: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export everything" if everything else f"Export “{name}”")
        self.setObjectName("ExportDialog")
        what = ("every show, the global automations and the settings, for moving CentraLight to another PC"
                if everything else f"the show “{name}”: its inputs, automations, layouts, room photo, notes, and the "
                                   "copies of its pictures and videos")
        intro = QLabel(f"One .clshow file with {what}. Double-clicking it on a PC with CentraLight offers to import it.")
        intro.setWordWrap(True)
        self.leave_out = QCheckBox("Leave out pictures and videos (a much smaller file)")
        buttons = QDialogButtonBox()
        buttons.addButton("Export…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.leave_out)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)


class ImportPackageDialog(QDialog):
    """What's in a .clshow file, and what to do with it: "Import “Morning” as a new show?", Replace or Keep both when the
    name is taken; for an Export everything file, ticks for each show, the global automations and the settings
    (the settings start unticked: the USB port and the window differ between PCs)."""

    def __init__(self, parent: QWidget | None, package: Package, shows_root: Path, loaded: str) -> None:
        super().__init__(parent)
        self.package = package
        self._root = shows_root
        self._loaded = loaded
        self.setWindowTitle("Import a show file")
        self.setObjectName("ImportPackageDialog")
        everything = package.kind == KIND_EVERYTHING
        importable = [item for item in package.shows if item.ok]
        if not everything and len(package.shows) == 1:
            item = package.shows[0]
            title = f"Import “{item.name}” as a new show?" if item.ok else f"“{item.name}” can't be imported"
        else:
            title = "Import from this file?"
        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        heading.setWordWrap(True)
        heading.setTextFormat(Qt.TextFormat.PlainText)  # (names and reasons come from the file: never read as HTML)
        self.items = QListWidget()
        self.items.setObjectName("ImportList")
        for item in package.shows:
            row = QListWidgetItem(f"{item.name}: {item.describe()}")
            row.setData(Qt.ItemDataRole.UserRole, ("show", item.index))
            if item.ok:
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(Qt.CheckState.Checked)
            else:
                row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.items.addItem(row)
        if package.globals is not None:
            row = QListWidgetItem(f"Global automations: {len(package.globals.automations)} (added; ones already here stay)")
            row.setData(Qt.ItemDataRole.UserRole, (GLOBALS_ROW, 0))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked)
            self.items.addItem(row)
        if package.settings is not None:
            row = QListWidgetItem("Settings (this PC keeps its own output, USB port, start with Windows and window)")
            row.setData(Qt.ItemDataRole.UserRole, (SETTINGS_ROW, 0))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            self.items.addItem(row)
        self.items.setVisible(everything or len(package.shows) != 1)
        self.replace_choice = QRadioButton()
        self.keep_both = QRadioButton()
        self.keep_both.setChecked(True)
        self.details = QLabel()
        self.details.setObjectName("Hint")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.items)
        layout.addWidget(self.replace_choice)
        layout.addWidget(self.keep_both)
        layout.addWidget(self.details)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)
        self.items.itemChanged.connect(lambda _item: self._update())
        self._update()
        self.import_button.setEnabled(bool(importable) or package.globals is not None or package.settings is not None)

    def ticked(self) -> list[tuple[str, int]]:
        if not self.items.isVisible() and not self.items.isVisibleTo(self) and self.items.count() == 1:
            row = self.items.item(0)
            return [row.data(Qt.ItemDataRole.UserRole)] if row.flags() & Qt.ItemFlag.ItemIsEnabled else []
        return [self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())
                if self.items.item(i).checkState() == Qt.CheckState.Checked]

    def shows_ticked(self) -> list[int]:
        return [index for kind, index in self.ticked() if kind == "show"]

    def clashes(self) -> list[tuple[int, Any]]:
        """(show index, the show here with its name) for the ticked shows whose names are taken."""
        found = []
        for index in self.shows_ticked():
            taken = name_taken(self._root, self.package.shows[index].name)
            if taken is not None:
                found.append((index, taken))
        return found

    def _update(self) -> None:
        clashes = self.clashes()
        if clashes:
            names = ", ".join(f"“{taken.name}”" for _index, taken in clashes)
            loaded = any(same_folder(taken.folder, self._loaded) for _index, taken in clashes)
            self.replace_choice.setText(f"Replace {names} (it goes to the Recycle Bin; its Stream Deck keys keep working)"
                                        .replace("&", "&&"))
            self.replace_choice.setEnabled(not loaded)
            if loaded:
                self.replace_choice.setToolTip("That show is loaded: load another show first to replace it")
                self.keep_both.setChecked(True)
            first = self.package.shows[clashes[0][0]].name
            self.keep_both.setText(f"Keep both (the import is called “{keep_both_name(self._root, first)}”"
                                   + (" and so on" if len(clashes) > 1 else "") + ")")
        for box in (self.replace_choice, self.keep_both):
            box.setVisible(bool(clashes))
        lines: list[str] = []
        if len(self.package.shows) == 1 and self.package.kind != KIND_EVERYTHING:
            lines.append(self.package.shows[0].describe() + ".")
        if not self.package.media:
            lines.append("Its pictures and videos were left out when it was exported.")
        if self.package.has_links:
            count = sum(len(item.originals) for item in self.package.shows)
            lines.append(f"Its pictures and videos are linked to {count} original file{'s' if count != 1 else ''} on the PC "
                         "it came from. CentraLight plays its own copies; it looks at an original only once you allow it "
                         "(the Editor's File row: Allow).")
        lines.append("An imported show isn't loaded: Show ▾ loads it when you're ready.")
        problems = self.package.all_problems()
        if problems:
            shown = problems[:8]
            lines.append("Left out or changed:\n" + "\n".join(f"• {p}" for p in shown)
                         + (f"\n• …and {len(problems) - len(shown)} more (the session log has them all)" if len(problems) > len(shown) else ""))
        self.details.setText("\n".join(lines))
        self.import_button.setEnabled(bool(self.ticked()))

    @property
    def replacing(self) -> bool:
        """Replace was chosen (asked of the dialog itself, so it still answers once the dialog has closed)."""
        return (self.replace_choice.isVisibleTo(self) and self.replace_choice.isEnabled()
                and self.replace_choice.isChecked())


def busy(on: bool) -> None:
    if on:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    else:
        QApplication.restoreOverrideCursor()
