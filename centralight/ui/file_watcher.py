"""Watching the show folder for inputs and automations changed outside CentraLight (by Claude
Code or Cursor following AI_GUIDE.md, or by hand in a text editor).

- A new or changed file is checked and used straight away. It becomes one undo step ("Changed
  outside CentraLight"), so Ctrl+Z puts the old version back (and saves it over the file).
- A file that can't be used keeps the last good version; its tile shows what's wrong.
- A deleted file removes the input or automation (also undoable).
- CentraLight's own saves are recognised and ignored.
- If you're editing that same input in the Editor and your latest change hasn't been saved yet,
  you're asked whether to load the file or keep yours.

It uses Windows' change notifications plus a check every second, in case one is missed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from ..files import forget_written, loads_json_bytes, written_by_us
from ..log import log_exception, log_info
from ..model import Show
from ..show_io import AUTOMATIONS_DIR, INPUTS_DIR, MAX_INPUT_BYTES, InputFileError, automation_from_dict, parse_input_bytes
from .document import ShowDocument

POLL_MS = 1000
SETTLE_MS = 300  # wait for an editor to finish writing


class FolderWatcher(QObject):
    problems_changed = Signal(object)  # {input id: what's wrong with its file}

    def __init__(self, document: ShowDocument, ask_reload: Callable[[str], bool] | None = None,
                 editing: Callable[[], str] | None = None, parent: QObject | None = None) -> None:
        """ask_reload(name) -> True to load the file (asked only on a clash with unsaved edits);
        editing() -> the id of the input open in the Editor ("" = none)."""
        super().__init__(parent)
        self.document = document
        self._ask_reload = ask_reload or (lambda _name: True)
        self._editing = editing or (lambda: "")
        self.problems: dict[str, str] = {}
        self._stamps: dict[Path, tuple[int, int]] = {}
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self._settle.start())
        self._watcher.fileChanged.connect(lambda _p: self._settle.start())
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self.check)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.check)
        self.folder: Path | None = None
        self._asking = False  # "Load the changed file?" is on screen
        self.start(document.show)

    def start(self, show: Show) -> None:
        folder = Path(show.folder) if show.folder and not show.read_only else None
        if folder == self.folder:
            return
        self.stop()
        self.folder = folder
        if folder is None:
            return
        for sub in (INPUTS_DIR, AUTOMATIONS_DIR):
            (folder / sub).mkdir(exist_ok=True)
            self._watcher.addPath(str(folder / sub))
        self._stamps = self._scan()  # what's there now is what was loaded
        for input_id, error in show.input_errors.items():  # files that couldn't be read when the show opened
            self.problems[input_id] = f"{input_id}.json: {error}"
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._settle.stop()
        paths = self._watcher.directories() + self._watcher.files()
        if paths:
            self._watcher.removePaths(paths)
        self.folder = None

    def _scan(self) -> dict[Path, tuple[int, int]]:
        found: dict[Path, tuple[int, int]] = {}
        if self.folder is None:
            return found
        for sub in (INPUTS_DIR, AUTOMATIONS_DIR):
            directory = self.folder / sub
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                try:
                    info = path.stat()
                except OSError:
                    continue
                found[path] = (info.st_mtime_ns, info.st_size)
        return found

    # ------------------------------------------------------------ noticing changes

    def check(self) -> None:
        """Look for added, changed and deleted files, and act on them."""
        if self.folder is None or self._asking:  # (not while "Load the changed file?" is on screen)
            return
        now = self._scan()
        changed = [p for p, stamp in now.items() if self._stamps.get(p) != stamp]
        deleted = [p for p in self._stamps if p not in now]
        self._stamps = now
        for path in sorted(changed):  # each file on its own: one odd file mustn't stop the others being picked up
            try:
                if path.parent.name == INPUTS_DIR:
                    self._input_file(path)
                else:
                    self._automation_file(path)
            except Exception as exc:
                log_exception(f"reading {path.parent.name}\\{path.name}", exc)
        for path in deleted:
            try:
                if path.parent.name == INPUTS_DIR:
                    self._input_gone(path.stem)
                else:
                    self._automation_gone(path.stem)
            except Exception as exc:
                log_exception(f"{path.parent.name}\\{path.name} deleted", exc)

    def _read(self, path: Path) -> bytes | None:
        """The file's bytes, or None if it can't be read just now (it's checked again next time)."""
        try:
            with path.open("rb") as handle:
                return handle.read(MAX_INPUT_BYTES + 1)  # (no more: a file that big is refused anyway)
        except OSError:
            self._stamps.pop(path, None)  # e.g. another program is still writing it: try again at the next check
            return None

    def _set_problem(self, input_id: str, problem: str) -> None:
        if problem:
            self.problems[input_id] = problem
        else:
            self.problems.pop(input_id, None)
        self.problems_changed.emit(dict(self.problems))

    def _input_file(self, path: Path) -> None:
        input_id = path.stem
        raw = self._read(path)
        if raw is None:
            return
        if written_by_us(path, raw):  # CentraLight's own save, byte for byte
            if input_id in self.problems:
                self._set_problem(input_id, "")
            return
        forget_written(path)
        try:
            inp, warnings = parse_input_bytes(raw, path)
        except InputFileError as exc:
            had = input_id in self.document.show.inputs
            self._set_problem(input_id, f"{path.name}: {exc}" + (" (the last good version is still used)" if had else ""))
            log_info(f"inputs\\{path.name} couldn't be used: {exc}", key=f"bad-input-{input_id}")
            return
        if input_id in self.problems:
            self._set_problem(input_id, "")
        document = self.document
        saved = document.saved.inputs.get(input_id)
        current = document.show.inputs.get(input_id)
        if inp == saved or inp == current or document.wrote_recently("input", input_id, inp):
            return  # CentraLight's own save, or nothing new
        if current is not None and document.has_unsaved("input", input_id) and self._editing() == input_id:
            self._asking = True
            document.pause_saving(True, asking_about=input_id)  # nothing is saved over the file while the question is up
            try:
                load_theirs = self._ask_reload(current.name)
            finally:
                self._asking = False
                document.pause_saving(False)
            if not load_theirs:
                document.flush()  # keep mine: written over the file straight away
                return
            raw = self._read(path)  # the file may have changed again while the question was up: use it as it is now
            if raw is None:
                return
            try:
                inp, warnings = parse_input_bytes(raw, path)
            except InputFileError:
                return
        show = document.show
        order = show.input_order if input_id in show.inputs else show.input_order + (input_id,)
        new_show = show.with_inputs({**show.inputs, input_id: inp}, order)
        verb = "Changed" if current is not None else "Added"
        document.forget_wrote("input", input_id)
        document.note_on_disk(inputs={input_id: inp})
        if document.apply(new_show, f"{verb} outside CentraLight: {inp.name}"):
            log_info(f"inputs\\{path.name} {verb.lower()} outside CentraLight" + (f" ({'; '.join(warnings)})" if warnings else ""))

    def _input_gone(self, input_id: str) -> None:
        self._set_problem(input_id, "")
        document = self.document
        if (input_id not in document.saved.inputs or input_id not in document.show.inputs
                or document.wrote_recently("input", input_id, None)):
            return  # CentraLight deleted it itself
        show = document.show
        name = show.inputs[input_id].name
        inputs = {k: v for k, v in show.inputs.items() if k != input_id}
        if self.folder is not None:
            forget_written(self.folder / INPUTS_DIR / f"{input_id}.json")  # (put back later = a change)
        document.forget_wrote("input", input_id)
        document.note_on_disk(inputs={input_id: None})
        document.apply(show.with_inputs(inputs, tuple(i for i in show.input_order if i != input_id)), f"Deleted outside CentraLight: {name}")

    def _automation_file(self, path: Path) -> None:
        raw = self._read(path)
        if raw is None or written_by_us(path, raw):  # (CentraLight's own save, byte for byte)
            return
        forget_written(path)
        try:
            if len(raw) > MAX_INPUT_BYTES:
                raise InputFileError("the file is too big")
            automation, warnings = automation_from_dict(loads_json_bytes(raw), expected_id=path.stem)
        except (InputFileError, ValueError) as exc:
            log_info(f"automations\\{path.name} couldn't be used: {exc}", key=f"bad-automation-{path.stem}")
            return
        document = self.document
        saved = document.saved.automations.get(automation.id)
        current = document.show.automations.get(automation.id)
        if automation == saved or automation == current or document.wrote_recently("automation", automation.id, automation):
            return
        show = document.show
        order = show.automation_order if automation.id in show.automation_order else show.automation_order + (automation.id,)
        verb = "Changed" if current is not None else "Added"
        document.forget_wrote("automation", automation.id)
        document.note_on_disk(automations={automation.id: automation})
        if document.apply(show.with_automations({**show.automations, automation.id: automation}, order),
                          f"{verb} outside CentraLight: {automation.name}"):
            log_info(f"automations\\{path.name} {verb.lower()} outside CentraLight" + (f" ({'; '.join(warnings)})" if warnings else ""))

    def _automation_gone(self, automation_id: str) -> None:
        document = self.document
        if (automation_id not in document.saved.automations or automation_id not in document.show.automations
                or document.wrote_recently("automation", automation_id, None)):
            return
        show = document.show
        name = show.automations[automation_id].name
        remaining = {k: v for k, v in show.automations.items() if k != automation_id}
        if self.folder is not None:
            forget_written(self.folder / AUTOMATIONS_DIR / f"{automation_id}.json")
        document.forget_wrote("automation", automation_id)
        document.note_on_disk(automations={automation_id: None})
        document.apply(show.with_automations(remaining, tuple(i for i in show.automation_order if i != automation_id)),
                       f"Deleted outside CentraLight: {name}")
