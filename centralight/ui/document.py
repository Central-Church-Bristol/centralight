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

Loading another show (load()) saves the old one's changes first. Each show keeps its own
histories while CentraLight runs, so going back to a show brings its undo back too.

The global automations have a document of their own (kind "global"): one history, saved to
_Global automations\\<id>.json and global.json, and sent to the engine as SetGlobalAutomations.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoGroup, QUndoStack

from ..commands import LoadShow, SetGlobalAutomations, SwapShow
from ..files import write_json_atomic
from ..global_automations import ORDER_FILE, order_data
from ..log import log_exception, log_info
from ..model import Show, frozen_map
from ..show_io import (
    AUTOMATIONS_DIR,
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
SETTLE_WAIT = 3.0  # seconds: before another show loads, its saves are waited for this long (then once more, briefly)
_STEP_NUMBERS = itertools.count(1)
OWN_SAVE_MEMORY_S = 10.0  # how long CentraLight remembers the files it wrote (so they aren't taken as outside changes)

SCOPE_SHOW = "show"  # inputs added / removed / reordered, global variables, the show's name
SCOPE_LAYOUT = "layout"  # lights, house lights, identify colours, saved layouts
SCOPE_AUTOMATIONS = "automations"  # the Automation tab
LAYOUT_FIELDS = ("fixtures", "house", "identify_seed", "layouts", "active_layout")
AUTOMATION_FIELDS = ("automations", "automation_order")
SHOW_FIELDS = ("name", "globals", "input_order", "notes")


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

    def empty(self) -> bool:
        """Nothing left to undo: a drag (or a colour pick, Cancelled) that ended where it began."""
        return (not self.added and not self.removed and all(old == new for old, new in self.edited.values())
                and all(part is None or part[0] == part[1] for part in (self.layout, self.show, self.automations)))


def merged_text(first: str, second: str) -> str:
    """'Opacity 80 % → 70 %' then 'Opacity 70 % → 60 %' makes 'Opacity 80 % → 60 %'."""
    if " → " in first and " → " in second:
        return first.rsplit(" → ", 1)[0] + " → " + second.rsplit(" → ", 1)[1]
    return second


class ChangeCommand(QUndoCommand):
    """One undo step."""

    def __init__(self, document: ShowDocument, change: Change, text: str, merge_key: str | None, applied: bool,
                 automatic: bool = False) -> None:
        super().__init__(text)
        self.document = document
        self.change = change
        self.merge_key = merge_key
        self._skip_redo = applied  # the change is already showing (e.g. after a drag)
        # (the Dashboard's Ctrl+Z undoes the newest step of any history: one CentraLight made by itself, such as a
        # linked video's update, never counts as the newest)
        self.seq = 0 if automatic else next(_STEP_NUMBERS)

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
        if self.change.empty():
            self.setObsolete(True)  # (back where it began: Qt drops the step, so none is left behind)
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
    automations_dir: str = AUTOMATIONS_DIR  # ("" for the global automations: straight in their folder)
    show_file: str = SHOW_FILE  # where show_data goes (global.json for the global automations)


class Autosaver:
    """Writes show files on its own thread ("autosave"), in the order they were asked for."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[SaveJob | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self.errors: deque[str] = deque(maxlen=20)  # the latest few failures (the window shows them)
        # (folder, kind, id, why), or (folder, "ok", "", "") when a save went through: the folder says whose show it was
        self.results: queue.SimpleQueue[tuple[str, str, str, str]] = queue.SimpleQueue()
        self._logged: dict[tuple[str, str], float] = {}  # (kind, id) -> when its failure was last written to the log
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
            where = job.automations_dir
            steps = [("input", inp.id, lambda inp=inp: save_input(folder, inp)) for inp in job.inputs]
            steps += [("layout", lay.id, lambda lay=lay: save_layout(folder, lay)) for lay in job.layouts]
            steps += [("delete-input", i, lambda i=i: delete_input_file(folder, i)) for i in job.deleted]
            steps += [("delete-layout", i, lambda i=i: delete_layout_file(folder, i)) for i in job.deleted_layouts]
            steps += [("automation", a.id, lambda a=a: save_automation(folder, a, where)) for a in job.automations]
            steps += [("delete-automation", i, lambda i=i: delete_automation_file(folder, i, where)) for i in job.deleted_automations]
            if job.show_data is not None:
                steps.append(("show", job.show_file, lambda data=job.show_data, name=job.show_file: write_json_atomic(folder / name, data)))
            failed = False
            for kind, item_id, step in steps:  # each file on its own: one that can't be written doesn't stop the rest
                try:
                    step()
                except Exception as exc:  # disk full, a file locked by another program, read-only...
                    failed = True
                    now = time.monotonic()
                    if now - self._logged.get((kind, str(item_id)), -1e9) > 600:  # (once in ten minutes, not every retry)
                        self._logged[(kind, str(item_id))] = now
                        log_exception(f"autosave ({kind} {item_id})", exc)
                    self.errors.append(str(exc))
                    self.results.put((job.folder, kind, str(item_id), str(exc) or type(exc).__name__))
            if not failed:
                self.results.put((job.folder, "ok", "", ""))
            if self._jobs.empty():
                self._idle.set()


# ---------------------------------------------------------------- the document


KIND_SHOW = "show"
KIND_GLOBAL = "global"  # the global automations' document


def folder_key(folder: str) -> str:
    """A folder written one way (case and slashes as Windows sees them), for comparing and looking up."""
    import os

    return os.path.normcase(os.path.abspath(folder)) if folder else ""


class ShowDocument(QObject):
    changed = Signal(object)  # the new Show
    loaded = Signal(object)  # another show was loaded (after changed): the new Show
    save_problem = Signal(str)  # what couldn't be saved, and why ("" = everything is saved)

    def __init__(self, show: Show, engine: Any, *, save_files: bool = True, save_delay_ms: int = SAVE_DELAY_MS,
                 kind: str = KIND_SHOW, undo_group: QUndoGroup | None = None) -> None:
        """kind: a show, or KIND_GLOBAL for the global automations (a Show holding just them). undo_group: share the
        window's, so Undo works on whichever history is on screen."""
        super().__init__()
        self.kind = kind
        self._show = show
        self._saved = show
        self._engine = engine
        self._save_files = save_files
        self._drag_start: Show | None = None
        self.undo_group = undo_group if undo_group is not None else QUndoGroup(self)
        self._stacks: dict[str, QUndoStack] = {}
        self._other_stacks: dict[str, dict[str, QUndoStack]] = {}  # other shows' histories (while CentraLight runs)
        self._scope = SCOPE_AUTOMATIONS if kind == KIND_GLOBAL else SCOPE_SHOW
        if undo_group is None:
            self.undo_group.setActiveStack(self.stack(self._scope))
        else:
            self.stack(self._scope)
        self._autosaver = Autosaver() if save_files and show.folder and not show.read_only else None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(save_delay_ms)
        self._save_timer.timeout.connect(self.flush)
        self._saving_paused = False
        self._held = 0  # colour pickers open with Preview (M14): saving waits for them to close
        self._pick_start: Show | None = None  # a colour pick with Preview under way (M14): the show as it began
        self._pick_open = False
        self._picking = False  # its picker's own change is being made (shown at once, with no undo step)
        self._asked_input: str | None = None  # the input "Load the changed file?" is about, while it's up
        self._newest_undone: list[QUndoStack] = []  # the histories the Dashboard's undo took steps from, in order
        self._retry_show = False  # show.json couldn't be written: write it again next time
        # inputs, layouts and automations whose file couldn't be written or removed: saved again next time, as they
        # are then (written if they're still in the show, removed if not)
        self._retry: dict[str, set[str]] = {"input": set(), "layout": set(), "automation": set()}
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(RETRY_SAVE_MS)
        self._retry_timer.timeout.connect(self.flush)
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

    def forget_wrote(self, kind: str, item_id: str) -> None:
        """Someone else's version of this item has been taken: CentraLight's own earlier saves of it no longer count
        as "ours" (so if the file is put back to one of them, that's a change too)."""
        self._recent_writes.pop((kind, item_id), None)

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

    def _push(self, before: Show, after: Show, text: str, merge_key: str | None, applied: bool,
              automatic: bool = False) -> None:
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
        self.stack(scope).push(ChangeCommand(self, change, text, merge_key, applied, automatic))

    # ------------------------------------------------------------ changes

    def _other_show(self, show: Show, text: str) -> bool:
        """A change worked out from another show than the loaded one (a question that was on screen while another show
        loaded, say): it's not made, so it can't land in the wrong show."""
        if folder_key(show.folder) == folder_key(self._show.folder):
            return False
        log_info(f"“{text}” wasn't made: it was for the show in {show.folder or '(none)'}, and another show is loaded now")
        return True

    def apply(self, show: Show, text: str, merge_key: str | None = None, automatic: bool = False) -> bool:
        """Make a change (one undo step). Returns False if nothing changed or it's read-only. automatic: CentraLight made
        it by itself (a linked file's update): the Dashboard's Ctrl+Z skips it."""
        if self.read_only or show == self._show or self._other_show(show, text):
            return False
        if self._pick_start is not None:
            if self._picking:  # (an open colour picker's Preview: shown now, with no undo step: see begin_pick)
                self._set(show)
                return True
            # anything else meanwhile (a file changed on disk, say) stays when the pick ends: it's done to its start too
            self._pick_start = Change(self._show, show).apply(self._pick_start, True)
        self._push(self._show, show, text, merge_key, applied=False, automatic=automatic)
        return True

    def begin_pick(self) -> None:
        """A colour picker with Preview has opened (M14). While it's open, its changes show at once, on the lights too,
        as a drag's do, with no undo step and nothing saved. end_pick() then puts the show back as it began, so Cancel
        leaves nothing behind (not even a keyframe) and Redo is kept; OK's colour is then made as one ordinary step."""
        if self._pick_start is None:
            self._pick_start = self._show
            self._pick_open = True
            self.hold_saving(True)

    @contextmanager
    def pick_change(self):
        """The open picker's own change: apply() shows it without an undo step (between begin_pick and end_pick)."""
        self._picking = True
        try:
            yield
        finally:
            self._picking = False

    def end_pick(self) -> None:
        start, self._pick_start = self._pick_start, None
        if start is not None and start is not self._show and folder_key(start.folder) == folder_key(self._show.folder):
            self._set(start)  # (back as it began: still held, so nothing's saved)
        if self._pick_open:
            self._pick_open = False
            self.hold_saving(False)

    def begin_drag(self) -> None:
        self._drag_start = self._show

    @property
    def dragging(self) -> bool:
        """A drag (or a slider) is changing the show right now, with its undo step still to come."""
        return self._drag_start is not None

    def files_in_history(self) -> set[str]:
        """The picture and video files the loaded show's undo histories can bring back (tidying leaves these)."""
        found: set[str] = set()
        for stack in self._stacks.values():
            for index in range(stack.count()):
                change = getattr(stack.command(index), "change", None)
                if not isinstance(change, Change):
                    continue
                inputs = [*change.added.values(), *change.removed.values()]
                inputs += [inp for pair in change.edited.values() for inp in pair]
                for inp in inputs:
                    found.update(element["file"] for track in inp.tracks for element in track
                                 if isinstance(element.get("file"), str) and element.get("file"))
                for side in change.layout or ():
                    found.update(layout.backdrop for layout in side.get("layouts", ()) if layout.backdrop)
        return found

    def preview(self, show: Show) -> None:
        """Show a change live (while dragging) without an undo step yet."""
        if self.read_only or self._other_show(show, "a drag"):
            return
        if self._drag_start is None:
            self._drag_start = self._show
        self._set(show)

    def end_drag(self, text: str) -> None:
        start, self._drag_start = self._drag_start, None
        if start is not None and start != self._show and folder_key(start.folder) == folder_key(self._show.folder):
            self._push(start, self._show, text or "Change", None, applied=True)

    def _set(self, show: Show) -> None:
        self._show = show
        self._engine.submit(SetGlobalAutomations(show) if self.kind == KIND_GLOBAL else SwapShow(show))
        self.changed.emit(show)
        if self._autosaver is not None and not self._saving_paused and not self._held:
            self._save_timer.start()

    def hold_saving(self, held: bool) -> None:
        """A colour being picked with Preview (M14): its changes are saved once the picker closes, not while it's open."""
        self._held = max(0, self._held + (1 if held else -1))
        if self._held:
            self._save_timer.stop()
        elif self._autosaver is not None and not self._saving_paused and self._show is not self._saved:
            self._save_timer.start()

    def saving_held(self) -> bool:
        return bool(self._held)

    def load(self, show: Show, automation: bool = False) -> None:
        """Another show: the old one's changes are saved first, and its histories kept for when it's loaded again
        (while CentraLight runs). The engine loads it (the lights freeze on what they show), then the window follows.
        automation: a global automation asked for it (not a person)."""
        if self._saving_paused:  # ("Load the changed file?" was up: that file stays as it is on disk, as when closing)
            self._saving_paused = False
            if self._asked_input is not None:
                self._leave_on_disk(self._asked_input)
        self._settle_saves()
        self._drag_start = None
        self._pick_start = None  # (another show: a pick under way has nothing to put back)
        self._save_timer.stop()
        self._retry_timer.stop()
        old_key, new_key = folder_key(self._show.folder), folder_key(show.folder)
        if old_key:
            self._other_stacks[old_key] = self._stacks
        self._stacks = self._other_stacks.pop(new_key, {}) if new_key else {}
        self._newest_undone.clear()
        self._show = self._saved = show
        self._retry_show = False
        self._retry = {"input": set(), "layout": set(), "automation": set()}
        self._recent_writes.clear()
        self._saving_paused = False
        self._asked_input = None
        if self._autosaver is None and self._save_files and show.folder and not show.read_only:
            self._autosaver = Autosaver()
            self._results.start()
        self.set_scope(self._scope)
        self._set_problem("")
        self._engine.submit(LoadShow(show, automation))
        self.changed.emit(show)
        self.loaded.emit(show)

    def pause_saving(self, paused: bool, asking_about: str | None = None) -> None:
        """While a question about a changed file is on screen, nothing is saved over it (retries included)."""
        self._saving_paused = paused
        self._asked_input = asking_about if paused else None
        if paused:
            self._save_timer.stop()
            self._retry_timer.stop()
        elif self._autosaver is not None and (self._show is not self._saved or self._pending_retries()):
            self._save_timer.start()

    def has_unsaved(self, kind: str, item_id: str) -> bool:
        """Is this input (or automation, layout) changed in CentraLight and not on disk yet, or did its save fail?"""
        if item_id in self._retry.get(kind, ()):
            return True
        if kind == "input":
            return self._show.inputs.get(item_id) != self._saved.inputs.get(item_id)
        if kind == "automation":
            return self._show.automations.get(item_id) != self._saved.automations.get(item_id)
        return False

    def _pending_retries(self) -> bool:
        return self._retry_show or any(self._retry.values())

    def _check_saves(self) -> None:
        """Every second: did the autosave thread manage? A file it couldn't write is put back as unsaved and tried
        again in a few seconds (until then, the header says so)."""
        if self._autosaver is None:
            return
        failures, ok = [], False
        here = folder_key(self._show.folder)
        while True:
            try:
                folder, kind, item_id, why = self._autosaver.results.get_nowait()
            except queue.Empty:
                break
            if folder_key(folder) != here:  # (a save of the show that was loaded before: it's not this show's to retry)
                if kind != "ok":
                    log_info(f"Couldn't {self._verb(kind)} {self._file_name(kind, item_id)} in {folder} (another show "
                             f"was loaded by then: the show's backups have earlier copies): {why}")
            elif kind == "ok":
                ok = True
            else:
                failures.append((kind, item_id, why))
        if failures:
            self._mark_unsaved(failures)
            kind, item_id, why = failures[0]
            more = f" (and {len(failures) - 1} more)" if len(failures) > 1 else ""
            self._set_problem(f"Couldn't {self._verb(kind)} {self._file_name(kind, item_id)}{more}: {why}\nCentraLight tries "
                              f"again every {RETRY_SAVE_MS // 1000} s. Is the file open in another program, or the disk full?")
            if not self._saving_paused:
                self._retry_timer.start()  # (its own timer: the 1.5 s autosave keeps its pace)
        elif ok and self.problem and self._show is self._saved and not self._pending_retries():
            log_info("Saving works again")
            self._set_problem("")

    def _file_name(self, kind: str, item_id: str) -> str:
        names = {"show": "show.json", "input": "inputs\\{}.json", "layout": "layouts\\{}.json", "automation": "automations\\{}.json"}
        if self.kind == KIND_GLOBAL:
            names = {"show": f"_Global automations\\{ORDER_FILE}", "automation": "_Global automations\\{}.json"}
        return names.get(kind.replace("delete-", ""), "{}").format(item_id)

    @staticmethod
    def _verb(kind: str) -> str:
        return "remove" if kind.startswith("delete-") else "save"

    def _set_problem(self, text: str) -> None:
        if text != self.problem:
            self.problem = text
            self.save_problem.emit(text)

    def _mark_unsaved(self, failures: list[tuple[str, str, str]]) -> None:
        for kind, item_id, _why in failures:
            if kind == "show":
                self._retry_show = True
            else:
                self._retry.setdefault(kind.replace("delete-", ""), set()).add(item_id)

    # ------------------------------------------------------------ saving

    def flush(self) -> None:
        """Save whatever changed since the last save (normally 1.5 s after the last change)."""
        self._save_timer.stop()
        if self._saving_paused:
            return  # (a question about a changed file is on screen: after it)
        if self._autosaver is None or (self._show is self._saved and not self._pending_retries()):
            return
        self._retry_timer.stop()
        old, new = self._saved, self._show
        to_dict = order_data if self.kind == KIND_GLOBAL else show_to_dict
        show_data = to_dict(new)
        changed_show = to_dict(old) != show_data or self._retry_show
        retry = {kind: set(ids) for kind, ids in self._retry.items()}
        self._retry_show = False
        self._retry = {"input": set(), "layout": set(), "automation": set()}
        # what changed, plus whatever couldn't be saved last time (written if it's in the show now, removed if not)
        inputs = tuple(inp for input_id, inp in new.inputs.items()
                       if old.inputs.get(input_id) != inp or input_id in retry["input"])
        deleted = tuple(dict.fromkeys([i for i in old.inputs if i not in new.inputs]
                                      + [i for i in retry["input"] if i not in new.inputs]))
        old_layouts = {lay.id: lay for lay in old.layouts}
        new_ids = {lay.id for lay in new.layouts}
        layouts = tuple(lay for lay in new.layouts if old_layouts.get(lay.id) != lay or lay.id in retry["layout"])
        deleted_layouts = tuple(dict.fromkeys([i for i in old_layouts if i not in new_ids]
                                              + [i for i in retry["layout"] if i not in new_ids]))
        automations = tuple(a for aid, a in new.automations.items()
                            if old.automations.get(aid) != a or aid in retry["automation"])
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
            where = {"automations_dir": "", "show_file": ORDER_FILE} if self.kind == KIND_GLOBAL else {}
            self._autosaver.save(SaveJob(new.folder, show_data if changed_show else None, inputs, deleted, layouts,
                                         deleted_layouts, automations, deleted_automations, **where))

    def close(self, timeout: float = 5.0) -> None:
        """Save anything outstanding and stop the autosave thread. If Windows closes CentraLight while "Load the
        changed file?" is on screen, everything else is saved, and that input's file stays as it is on disk (nobody
        said to write over it)."""
        if self._saving_paused:
            self._saving_paused = False
            if self._asked_input is not None:
                self._leave_on_disk(self._asked_input)
        self.flush()
        if self._autosaver is not None:
            self._autosaver.wait(timeout)
            self._autosaver.close()
            self._autosaver = None
        # Nothing keeps running: a closed document can be freed later by Python's garbage collector (its undo history
        # points back at it), on whichever thread that happens, and Qt can't stop a timer from another thread. One
        # left running then fires at freed memory (a crash found in the M14 tests).
        for timer in (self._save_timer, self._retry_timer, self._results):
            timer.stop()

    def _leave_on_disk(self, input_id: str) -> None:
        """Count an input as saved as it was, so the last save leaves its file alone (CentraLight is closing)."""
        saved = self._saved.inputs.get(input_id)
        inputs = dict(self._show.inputs)
        if saved is None:
            inputs.pop(input_id, None)
            order = tuple(i for i in self._show.input_order if i != input_id)
        else:
            inputs[input_id] = saved
            order = self._show.input_order
        self._show = self._show.with_inputs(inputs, order)
        self._retry["input"].discard(input_id)

    def _settle_saves(self, timeout: float = SETTLE_WAIT) -> None:
        """Save everything that's waiting, and wait for it (a few seconds at most: the window can't be used meanwhile),
        before another show loads: a file that couldn't be written is tried once more, then reported. Saves still
        going after that finish in the background; if one fails it's logged, not retried or blamed in the new show."""
        if self._autosaver is None:
            return
        for wait in (timeout, 1.0):
            self.flush()
            self._autosaver.wait(wait)
            self._check_saves()
            if not self._pending_retries():
                return
        log_info(f"These couldn't be saved in {self._show.folder} before another show loaded: {self.problem} "
                 "(the show's backups have earlier copies)")

    def forget_show(self, folder: str) -> None:
        """A show was deleted: its undo histories go with it (a show made later in the same folder starts afresh)."""
        for stack in (self._other_stacks.pop(folder_key(folder), None) or {}).values():
            self.undo_group.removeStack(stack)
            stack.deleteLater()

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
        self._newest_undone.append(best[1])
        best[1].undo()
        return True

    def redo_newest(self) -> bool:
        """Redo what undo_newest() undid, newest first (and nothing else: no older step from another history)."""
        while self._newest_undone:
            stack = self._newest_undone.pop()
            if stack.canRedo():
                stack.redo()
                return True
        return False

    def wait_saved(self, timeout: float = 5.0) -> bool:
        return self._autosaver.wait(timeout) if self._autosaver is not None else True
