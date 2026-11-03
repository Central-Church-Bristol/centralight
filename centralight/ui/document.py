"""The show document: the window's copy of the show, with undo and autosave.

Every change goes through apply(), which:
- records an undo step (Ctrl+Z / Ctrl+Y)
- sends the new show to the engine, so the lights follow at once
- saves the changed files 1.5 s after the last change, on a background thread

While something is being dragged, preview() updates the engine without an undo step;
end_drag() then records the whole drag as one step.

Undo has separate histories, so undo only affects what you're looking at:
- one per input (the Editor shows the history of the input it's editing)
- one for the layout (lights, house lights, saved layouts)
- one for the show itself (inputs added, duplicated or deleted, global variables)
Each step stores only the part of the show it changed, so undoing it never touches
another input or the layout.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoGroup, QUndoStack

from ..commands import SwapShow
from ..files import write_json_atomic
from ..log import log_exception, log_info
from ..model import Show, frozen_map
from ..show_io import (
    SHOW_FILE,
    delete_automation_file,
    delete_input_file,
    delete_layout_file,
    save_automation,
    save_input,
    save_layout,
    show_to_dict,
)

SAVE_DELAY_MS = 1500
UNDO_LIMIT = 200
RETRY_SAVE_MS = 5000  # a file that couldn't be saved is tried again this often
_STEP_NUMBERS = itertools.count(1)
OWN_SAVE_MEMORY_S = 10.0  # how long CentraLight remembers the files it wrote (so they aren't taken as outside changes)

SCOPE_SHOW = "show"  # inputs added / removed / reordered, global variables, the show's name
SCOPE_LAYOUT = "layout"  # lights, house lights, identify colours, saved layouts
SCOPE_AUTOMATIONS = "automations"  # the Automation tab
LAYOUT_FIELDS = ("fixtures", "house", "identify_seed", "layouts", "active_layout")
AUTOMATION_FIELDS = ("automations", "automation_order")
SHOW_FIELDS = ("name", "globals", "input_order")


def input_scope(input_id: str) -> str:
    """The undo history of one input."""
    return f"input:{input_id}"


def changed_scopes(before: Show, after: Show) -> list[str]:
    """Which histories a change belongs to (normally exactly one)."""
    scopes: list[str] = []
    if any(getattr(before, f) != getattr(after, f) for f in LAYOUT_FIELDS):
        scopes.append(SCOPE_LAYOUT)
    if any(getattr(before, f) != getattr(after, f) for f in AUTOMATION_FIELDS):
        scopes.append(SCOPE_AUTOMATIONS)
    if any(getattr(before, f) != getattr(after, f) for f in SHOW_FIELDS) or before.inputs.keys() != after.inputs.keys():
        scopes.append(SCOPE_SHOW)
    for input_id, inp in after.inputs.items():
        old = before.inputs.get(input_id)
        if old is not None and old is not inp and old != inp:
            scopes.append(input_scope(input_id))
    return scopes


class Change:
    """What one undo step changes, and nothing else."""

    def __init__(self, before: Show, after: Show) -> None:
        self.layout = self._fields(before, after, LAYOUT_FIELDS)
        self.show = self._fields(before, after, SHOW_FIELDS)
        self.automations = self._fields(before, after, AUTOMATION_FIELDS)
        self.added = {i: after.inputs[i] for i in after.inputs.keys() - before.inputs.keys()}
        self.removed = {i: before.inputs[i] for i in before.inputs.keys() - after.inputs.keys()}
        self.edited = {
            i: (before.inputs[i], inp) for i, inp in after.inputs.items()
            if i in before.inputs and before.inputs[i] is not inp and before.inputs[i] != inp
        }

    @staticmethod
    def _fields(before: Show, after: Show, names: tuple[str, ...]) -> tuple[dict, dict] | None:
        old = {f: getattr(before, f) for f in names if getattr(before, f) != getattr(after, f)}
        return (old, {f: getattr(after, f) for f in old}) if old else None

    def apply(self, show: Show, forward: bool) -> Show:
        """The show with this step done (forward) or undone."""
        side = 1 if forward else 0
        changes: dict[str, Any] = {}
        for part in (self.layout, self.show, self.automations):
            if part is not None:
                changes.update(part[side])
        inputs = dict(show.inputs)
        # Inputs that come and go keep whatever was done to them in their own history meanwhile
        going, coming = (self.removed, self.added) if forward else (self.added, self.removed)
        for input_id in going:
            going[input_id] = inputs.pop(input_id, going[input_id])
        for input_id, inp in coming.items():
            inputs[input_id] = inp
        for input_id, pair in self.edited.items():
            if input_id in inputs:
                inputs[input_id] = pair[side]
        return replace(show, inputs=frozen_map(inputs), **changes)

    def merge(self, other: Change) -> bool:
        """Fold a later step of the same kind into this one (a slider drag is one step)."""
        if self.added or self.removed or other.added or other.removed:
            return False
        if self.edited.keys() != other.edited.keys():
            return False
        for mine, theirs in ((self.layout, other.layout), (self.show, other.show), (self.automations, other.automations)):
            if (mine is None) != (theirs is None) or (mine is not None and mine[0].keys() != theirs[0].keys()):
                return False
        if self.layout is not None:
            self.layout = (self.layout[0], other.layout[1])
        if self.show is not None:
            self.show = (self.show[0], other.show[1])
        if self.automations is not None:
            self.automations = (self.automations[0], other.automations[1])
        for input_id, (old, _new) in self.edited.items():
            self.edited[input_id] = (old, other.edited[input_id][1])
        return True


def merged_text(first: str, second: str) -> str:
    """'Opacity 80 % → 70 %' then 'Opacity 70 % → 60 %' makes 'Opacity 80 % → 60 %'."""
    if " → " in first and " → " in second:
        return first.rsplit(" → ", 1)[0] + " → " + second.rsplit(" → ", 1)[1]
    return second


class ChangeCommand(QUndoCommand):
    """One undo step."""

    def __init__(self, document: ShowDocument, change: Change, text: str, merge_key: str | None, applied: bool) -> None:
        super().__init__(text)
        self.document = document
        self.change = change
        self.merge_key = merge_key
        self._skip_redo = applied  # the change is already showing (e.g. after a drag)
        self.seq = next(_STEP_NUMBERS)  # (the Dashboard's Ctrl+Z undoes the newest step of any history)

    def redo(self) -> None:
        if self._skip_redo:
            self._skip_redo = False
            return
        self.document._set(self.change.apply(self.document.show, True))

    def undo(self) -> None:
        self.document._set(self.change.apply(self.document.show, False))

    def id(self) -> int:  # noqa: A003 (Qt name)
        return (hash(self.merge_key) & 0x7FFFFFFF) if self.merge_key else -1

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802
        if not isinstance(other, ChangeCommand) or not self.merge_key or other.merge_key != self.merge_key:
            return False
        if not self.change.merge(other.change):
            return False
        self.setText(merged_text(self.text(), other.text()))
        self.seq = other.seq
        return True


# ---------------------------------------------------------------- saving


@dataclass(frozen=True)
class SaveJob:
    folder: str
    show_data: dict | None  # show.json contents, or None if it didn't change
    inputs: tuple  # Input objects to write
    deleted: tuple[str, ...]  # input ids whose files go
    layouts: tuple = ()  # Layout objects to write
    deleted_layouts: tuple[str, ...] = ()
    automations: tuple = ()  # Automation objects to write
    deleted_automations: tuple[str, ...] = ()


class Autosaver:
    """Writes show files on its own thread ("autosave"), in the order they were asked for."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[SaveJob | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self.errors: list[str] = []
        self.results: queue.SimpleQueue[tuple[str, str, str]] = queue.SimpleQueue()  # (kind, id, why); ("ok", "", "")
        self._thread = threading.Thread(target=self._run, name="autosave", daemon=True)
        self._thread.start()

    def save(self, job: SaveJob) -> None:
        self._idle.clear()
        self._jobs.put(job)

    def wait(self, timeout: float = 5.0) -> bool:
        return self._idle.wait(timeout)

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join(5.0)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                self._idle.set()
                return
            folder = Path(job.folder)
            steps = [("input", inp.id, lambda inp=inp: save_input(folder, inp)) for inp in job.inputs]
            steps += [("layout", lay.id, lambda lay=lay: save_layout(folder, lay)) for lay in job.layouts]
            steps += [("delete-input", i, lambda i=i: delete_input_file(folder, i)) for i in job.deleted]
            steps += [("delete-layout", i, lambda i=i: delete_layout_file(folder, i)) for i in job.deleted_layouts]
            steps += [("automation", a.id, lambda a=a: save_automation(folder, a)) for a in job.automations]
            steps += [("delete-automation", i, lambda i=i: delete_automation_file(folder, i)) for i in job.deleted_automations]
            if job.show_data is not None:
                steps.append(("show", SHOW_FILE, lambda data=job.show_data: write_json_atomic(folder / SHOW_FILE, data)))
            failed = False
            for kind, item_id, step in steps:  # each file on its own: one that can't be written doesn't stop the rest
                try:
                    step()
                except Exception as exc:  # disk full, a file locked by another program, read-only...
                    failed = True
                    log_exception(f"autosave ({kind} {item_id})", exc)
                    self.errors.append(str(exc))
                    self.results.put((kind, str(item_id), str(exc) or type(exc).__name__))
            if not failed:
                self.results.put(("ok", "", ""))
            if self._jobs.empty():
                self._idle.set()


# ---------------------------------------------------------------- the document


class ShowDocument(QObject):
    changed = Signal(object)  # the new Show
    save_problem = Signal(str)  # what couldn't be saved, and why ("" = everything is saved)

    def __init__(self, show: Show, engine: Any, *, save_files: bool = True, save_delay_ms: int = SAVE_DELAY_MS) -> None:
        super().__init__()
        self._show = show
        self._saved = show
        self._engine = engine
        self._drag_start: Show | None = None
        self.undo_group = QUndoGroup(self)
        self._stacks: dict[str, QUndoStack] = {}
        self._scope = SCOPE_SHOW
        self.undo_group.setActiveStack(self.stack(SCOPE_SHOW))
        self._autosaver = Autosaver() if save_files and show.folder and not show.read_only else None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(save_delay_ms)
        self._save_timer.timeout.connect(self.flush)
        self._saving_paused = False
        self._retry_show = False  # show.json couldn't be written: write it again next time
        self._retry_deletes: dict[str, set[str]] = {"input": set(), "layout": set(), "automation": set()}
        self.problem = ""
        self._results = QTimer(self)
        self._results.setInterval(1000)
        self._results.timeout.connect(self._check_saves)
        if self._autosaver is not None:
            self._results.start()
        self._recent_writes: dict[tuple[str, str], deque] = {}  # (kind, id) -> (when, version written or None)

    @property
    def show(self) -> Show:
        return self._show

    @property
    def read_only(self) -> bool:
        return self._show.read_only

    @property
    def saved(self) -> Show:
        """The show as CentraLight last saved it (so its own saves aren't mistaken for outside changes)."""
        return self._saved

    def note_on_disk(self, inputs: dict | None = None, automations: dict | None = None) -> None:
        """Files changed outside CentraLight already hold these versions ({id: Input / Automation, or None for a
        deleted file}), so saving compares with them: undoing an outside change then writes the old version back."""
        saved = self._saved
        if inputs:
            merged = dict(saved.inputs)
            for key, value in inputs.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            saved = replace(saved, inputs=frozen_map(merged))
        if automations:
            merged = dict(saved.automations)
            for key, value in automations.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            saved = replace(saved, automations=frozen_map(merged))
        self._saved = saved

    def wrote_recently(self, kind: str, item_id: str, version: Any) -> bool:
        """True if CentraLight itself saved this version of an "input" or "automation" (None: deleted its file) in
        the last few seconds. A save can land after a newer change, so the latest one isn't always enough."""
        now = time.monotonic()
        return any(now - when <= OWN_SAVE_MEMORY_S and kept == version
                   for when, kept in self._recent_writes.get((kind, item_id), ()))

    def _wrote(self, kind: str, item_id: str, version: Any) -> None:
        self._recent_writes.setdefault((kind, item_id), deque(maxlen=8)).append((time.monotonic(), version))

    # ------------------------------------------------------------ histories

    def stack(self, scope: str) -> QUndoStack:
        """The undo history for a scope (SCOPE_SHOW, SCOPE_LAYOUT or input_scope(id))."""
        stack = self._stacks.get(scope)
        if stack is None:
            stack = QUndoStack(self.undo_group)
            stack.setUndoLimit(UNDO_LIMIT)
            self.undo_group.addStack(stack)
            self._stacks[scope] = stack
        return stack

    @property
    def scope(self) -> str:
        return self._scope

    def set_scope(self, scope: str) -> None:
        """Which history Undo / Redo and the History panel use (what's on screen)."""
        self._scope = scope
        self.undo_group.setActiveStack(self.stack(scope))

    @property
    def undo_stack(self) -> QUndoStack:
        """The history in use right now."""
        return self.stack(self._scope)

    def undo(self) -> None:
        self.undo_stack.undo()

    def redo(self) -> None:
        self.undo_stack.redo()

    def _push(self, before: Show, after: Show, text: str, merge_key: str | None, applied: bool) -> None:
        scopes = changed_scopes(before, after)
        if not scopes:
            return
        scope = self._scope if self._scope in scopes else scopes[0]
        if len(scopes) > 1:
            log_info(f'Undo step "{text}" changed {", ".join(scopes)}; it went in the {scope} history', key="undo-scopes")
        change = Change(before, after)
        for input_id in change.added:  # a brand-new input starts with an empty history
            old = self._stacks.get(input_scope(input_id))
            if old is not None:
                old.clear()
        self.stack(scope).push(ChangeCommand(self, change, text, merge_key, applied))

    # ------------------------------------------------------------ changes

    def apply(self, show: Show, text: str, merge_key: str | None = None) -> bool:
        """Make a change (one undo step). Returns False if nothing changed or it's read-only."""
        if self.read_only or show == self._show:
            return False
        self._push(self._show, show, text, merge_key, applied=False)
        return True

    def begin_drag(self) -> None:
        self._drag_start = self._show

    def preview(self, show: Show) -> None:
        """Show a change live (while dragging) without an undo step yet."""
        if self.read_only:
            return
        if self._drag_start is None:
            self._drag_start = self._show
        self._set(show)

    def end_drag(self, text: str) -> None:
        start, self._drag_start = self._drag_start, None
        if start is not None and start != self._show:
            self._push(start, self._show, text or "Change", None, applied=True)

    def _set(self, show: Show) -> None:
        self._show = show
        self._engine.submit(SwapShow(show))
        self.changed.emit(show)
        if self._autosaver is not None and not self._saving_paused:
            self._save_timer.start()

    def pause_saving(self, paused: bool) -> None:
        """While a question about a changed file is on screen, nothing is saved over it."""
        self._saving_paused = paused
        if paused:
            self._save_timer.stop()
        elif self._autosaver is not None and self._show is not self._saved:
            self._save_timer.start()

    def _pending_retries(self) -> bool:
        return self._retry_show or any(self._retry_deletes.values())

    def _check_saves(self) -> None:
        """Every second: did the autosave thread manage? A file it couldn't write is put back as unsaved and tried
        again in a few seconds (until then, the header says so)."""
        if self._autosaver is None:
            return
        failures, ok = [], False
        while True:
            try:
                kind, item_id, why = self._autosaver.results.get_nowait()
            except queue.Empty:
                break
            if kind == "ok":
                ok = True
            else:
                failures.append((kind, item_id, why))
        if failures:
            self._mark_unsaved(failures)
            names = {"show": "show.json", "input": "inputs\\{}.json", "layout": "layouts\\{}.json", "automation": "automations\\{}.json"}
            kind, item_id, why = failures[0]
            what = names.get(kind.replace("delete-", ""), "{}").format(item_id)
            verb = "remove" if kind.startswith("delete-") else "save"
            more = f" (and {len(failures) - 1} more)" if len(failures) > 1 else ""
            self._set_problem(f"Couldn't {verb} {what}{more}: {why}\nCentraLight tries again every {RETRY_SAVE_MS // 1000} s. "
                              "Is the file open in another program, or the disk full?")
            if not self._saving_paused:
                self._save_timer.start(RETRY_SAVE_MS)
        elif ok and self.problem and self._show is self._saved and not self._pending_retries():
            log_info("Saving works again")
            self._set_problem("")

    def _set_problem(self, text: str) -> None:
        if text != self.problem:
            self.problem = text
            self.save_problem.emit(text)

    def _mark_unsaved(self, failures: list[tuple[str, str, str]]) -> None:
        saved = self._saved
        inputs, automations = dict(saved.inputs), dict(saved.automations)
        layouts = list(saved.layouts)
        for kind, item_id, _why in failures:
            if kind == "input":
                inputs.pop(item_id, None)  # (so the next save writes it again)
            elif kind == "layout":
                layouts = [lay for lay in layouts if lay.id != item_id]
            elif kind == "automation":
                automations.pop(item_id, None)
            elif kind == "show":
                self._retry_show = True
            elif kind.startswith("delete-"):
                self._retry_deletes.setdefault(kind[len("delete-"):], set()).add(item_id)
        self._saved = replace(saved, inputs=frozen_map(inputs), automations=frozen_map(automations), layouts=tuple(layouts))

    # ------------------------------------------------------------ saving

    def flush(self) -> None:
        """Save whatever changed since the last save (normally 1.5 s after the last change)."""
        self._save_timer.stop()
        if self._autosaver is None or (self._show is self._saved and not self._pending_retries()):
            return
        old, new = self._saved, self._show
        show_data = show_to_dict(new)
        changed_show = show_to_dict(old) != show_data or self._retry_show
        retry = {kind: set(ids) for kind, ids in self._retry_deletes.items()}
        self._retry_show = False
        self._retry_deletes = {"input": set(), "layout": set(), "automation": set()}
        inputs = tuple(inp for input_id, inp in new.inputs.items() if old.inputs.get(input_id) != inp)
        deleted = tuple(dict.fromkeys([i for i in old.inputs if i not in new.inputs] + [i for i in retry["input"] if i not in new.inputs]))
        old_layouts = {lay.id: lay for lay in old.layouts}
        new_ids = {lay.id for lay in new.layouts}
        layouts = tuple(lay for lay in new.layouts if old_layouts.get(lay.id) != lay)
        deleted_layouts = tuple(dict.fromkeys([i for i in old_layouts if i not in new_ids] + [i for i in retry["layout"] if i not in new_ids]))
        automations = tuple(a for aid, a in new.automations.items() if old.automations.get(aid) != a)
        deleted_automations = tuple(dict.fromkeys([i for i in old.automations if i not in new.automations]
                                                  + [i for i in retry["automation"] if i not in new.automations]))
        self._saved = new
        for inp in inputs:
            self._wrote("input", inp.id, inp)
        for input_id in deleted:
            self._wrote("input", input_id, None)
        for automation in automations:
            self._wrote("automation", automation.id, automation)
        for automation_id in deleted_automations:
            self._wrote("automation", automation_id, None)
        if changed_show or inputs or deleted or layouts or deleted_layouts or automations or deleted_automations:
            self._autosaver.save(SaveJob(new.folder, show_data if changed_show else None, inputs, deleted, layouts,
                                         deleted_layouts, automations, deleted_automations))

    def close(self, timeout: float = 5.0) -> None:
        """Save anything outstanding and stop the autosave thread."""
        self.flush()
        if self._autosaver is not None:
            self._autosaver.wait(timeout)
            self._autosaver.close()
            self._autosaver = None

    def undo_newest(self) -> bool:
        """The Dashboard's Ctrl+Z: the newest change to the show or to any input (an AI's change to an input goes in
        that input's own history). Layout and automation changes are undone on their own tabs (an automation
        switching a layout or another automation isn't something the person at the Dashboard just did)."""
        best = None
        for scope, stack in self._stacks.items():
            if scope != SCOPE_SHOW and not scope.startswith(input_scope("")):
                continue
            if stack.canUndo():
                command = stack.command(stack.index() - 1)
                seq = getattr(command, "seq", 0)
                if best is None or seq > best[0]:
                    best = (seq, stack)
        if best is None:
            return False
        self._newest_undone = best[1]
        best[1].undo()
        return True

    def redo_newest(self) -> bool:
        """Redo what undo_newest() last undid (or the active history's next step)."""
        stack = getattr(self, "_newest_undone", None)
        if stack is not None and stack.canRedo():
            stack.redo()
            return True
        self.redo()
        return False

    def wait_saved(self, timeout: float = 5.0) -> bool:
        return self._autosaver.wait(timeout) if self._autosaver is not None else True
