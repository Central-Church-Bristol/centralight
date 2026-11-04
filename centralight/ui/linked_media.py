"""Linked pictures and videos in the window (M13): what each link's state is, and when an update is used.

CentraLight plays its own copy of each picture and video (in the show's media folder). The originals are only read
by link_worker.py's thread. This keeps the window's side:
- **Checks:** when a show loads, every 5 minutes, when an element is selected in the Editor, and when someone allows a
  link. Only links someone at this PC has chosen are checked (links.py: AllowedLinks); the rest show "Not checked yet".
- **Updates:** a changed original becomes a new copy; it's used once its input isn't on the lights (not on Base, an
  overlay, a transition or solo), as one undo step in that input's history. "Update now" uses it at once. The room
  photo is never on the lights, so it's updated straight away.
- **Choose…:** the chosen file is allowed and copied in on the thread; when the copy is ready, the element (or the room
  photo) points at it and at its original, as one undo step.
- **Tidying:** when a show loads, copies CentraLight made that nothing uses (in the show or its undo history) go.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ..input_edit import find_element, set_element_value
from ..layouts import _replace_layout
from ..link_worker import LinkJob, LinkResult, LinkWorker
from ..links import AllowedLinks, file_name, link_key, original_problem
from ..log import log_info
from ..model import Show
from ..show_io import safe_relative_file
from .document import ShowDocument, folder_key

CHECK_EVERY_MS = 5 * 60 * 1000
POLL_MS = 500
RETRY_AFTER = 15 * 60.0  # seconds before an original that couldn't be reached is tried again by the 5-minute check


@dataclass(frozen=True)
class Link:
    """One linked picture or video in a show: an element's, or a saved layout's room photo (input_id "")."""

    input_id: str
    element_id: str  # the element's id, or the layout's id for a room photo
    original: str
    file: str  # the show's copy now
    media: str  # "image" or "video"

    @property
    def is_photo(self) -> bool:
        return self.input_id == ""


@dataclass(frozen=True)
class LinkState:
    """How a link is: "unchecked" (waiting for an OK at this PC), "refused" (can't be used: detail says why),
    "checking", "ok", "update" (a new copy is ready: new_file), "missing", "unreachable" or "bad"."""

    status: str
    detail: str = ""
    new_file: str = ""


def links_in(show: Show) -> list[Link]:
    found: list[Link] = []
    for inp in show.ordered_inputs():
        for track in inp.tracks:
            for element in track:
                original = element.get("original")
                if element.get("kind") in ("image", "video") and isinstance(original, str) and original:
                    file = safe_relative_file(element.get("file"))  # (as the engine reads it: "media\\a.png" is "media/a.png")
                    found.append(Link(inp.id, str(element.get("id", "")), original, file, str(element.get("kind"))))
    for layout in show.layouts:
        if layout.backdrop_original:
            found.append(Link("", layout.id, layout.backdrop_original, safe_relative_file(layout.backdrop), "image"))
    return found


def files_used(show: Show) -> set[str]:
    """Every picture and video file the show points at (its elements' copies and its room photos)."""
    used: set[str] = set()
    for inp in show.inputs.values():
        for track in inp.tracks:
            for element in track:
                used.add(safe_relative_file(element.get("file")))  # (as the engine reads them)
    used.update(safe_relative_file(layout.backdrop) for layout in show.layouts)
    used.discard("")
    return used


class LinkedMedia(QObject):
    changed = Signal()  # some link's state changed (the File rows and the room photo redraw)

    def __init__(self, document: ShowDocument, engine: Any = None, allowed: AllowedLinks | None = None,
                 worker: LinkWorker | None = None, in_use: Callable[[], frozenset[str]] | None = None,
                 clock: Callable[[], float] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        import time

        self.document = document
        self.allowed = allowed if allowed is not None else AllowedLinks()
        self.worker = worker if worker is not None else LinkWorker()
        self._engine = engine
        self._in_use = in_use or self._engine_in_use
        self._clock = clock or time.monotonic
        self._states: dict[tuple[str, str], LinkState] = {}  # (show folder, link key) -> state
        self._failed_at: dict[tuple[str, str], float] = {}  # when an original couldn't be reached
        self._known: set[tuple[str, str, str]] = set()  # (show folder, link key, copy) already asked about
        # (input id, element id or layout id) -> the file being copied in / why Choose… didn't work: the loaded show's
        self.copying: dict[tuple[str, str], str] = {}
        self.problems: dict[tuple[str, str], str] = {}
        self._applied: dict[tuple[str, str, str], str] = {}  # (show, input id, element id) -> the update this applied
        self._declined: dict[tuple[str, str, str], str] = {}  # ...an update undone by someone: offered, not applied again
        self._later: list[LinkResult] = []  # Choose… results that arrived during a drag (made when it ends)
        self.copies: dict[str, str] = {}  # the AI paste dialog's "Copy into the show": original -> its copy ("" = copying)
        self.copy_problems: dict[str, str] = {}  # ... or why it couldn't be copied
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.poll)
        self._poll.start()
        self._every = QTimer(self)
        self._every.setInterval(CHECK_EVERY_MS)
        self._every.timeout.connect(lambda: self.check_all(regular=True))
        self._every.start()
        document.changed.connect(self._show_changed)
        document.loaded.connect(self._show_loaded)
        self._show_loaded(document.show)

    # ------------------------------------------------------------ asking

    def _key(self, original: str) -> tuple[str, str]:
        return folder_key(self.document.show.folder), link_key(original)

    def state(self, original: str) -> LinkState:
        problem = original_problem(original)
        if problem:
            return LinkState("refused", problem)
        if self.document.show.read_only or not self.document.show.folder:
            return LinkState("ok")  # (a read-only show isn't checked or changed: it plays its copies)
        if not self.allowed.allowed(original):
            return LinkState("unchecked")
        return self._states.get(self._key(original), LinkState("checking"))

    def _submit(self, link: Link, token: Any = None) -> None:
        show = self.document.show
        if not show.folder or show.read_only or original_problem(link.original, link.media):
            return
        if not self.allowed.allowed(link.original):
            return
        key = self._key(link.original)
        if self._states.get(key, LinkState("checking")).status != "update":
            self._states[key] = LinkState("checking")
        self.worker.submit(LinkJob("check", show.folder, link.original, link.file, link.media, token=token))

    def check(self, original: str) -> None:
        """Check one original now (its element was selected in the Editor, say)."""
        for link in links_in(self.document.show):
            if link_key(link.original) == link_key(original):
                self._submit(link)
        self.changed.emit()

    def check_all(self, regular: bool = False) -> None:
        """Check every link in the loaded show (regular: the 5-minute check, which leaves recently unreachable ones)."""
        now = self._clock()
        for link in links_in(self.document.show):
            key = self._key(link.original)
            if regular and now - self._failed_at.get(key, -RETRY_AFTER) < RETRY_AFTER:
                continue
            self._known.add((key[0], key[1], link.file))
            self._submit(link)
        self.changed.emit()

    def allow(self, original: str) -> None:
        """Someone at this PC said yes to this link: it's followed from now on."""
        if original_problem(original):
            return
        self.allowed.allow([original])
        log_info(f"Linked file {file_name(original)}: allowed ({original})")
        self.check(original)

    def choose(self, input_id: str, element_id: str, path: str, media: str) -> None:
        """Choose…: link an element to this file and copy it in (the element changes when the copy is ready)."""
        problem = original_problem(path, media)
        show = self.document.show
        if not problem and (not show.folder or show.read_only):
            problem = "this show isn't saved in a folder it can be copied into"
        if problem:
            self.problems[(input_id, element_id)] = f"{file_name(path) or path} can't be used: {problem}."
            self.changed.emit()
            return
        self.allowed.allow([path])
        self.problems.pop((input_id, element_id), None)
        self.copying[(input_id, element_id)] = path
        self.worker.submit(LinkJob("check", self.document.show.folder, path, "", media,
                                   token=("choose", self.document.show.folder, input_id, element_id)))
        self.changed.emit()

    def copy_in(self, originals: dict[str, str]) -> None:
        """The AI paste dialog's "Copy into the show" (clicking it is the OK): allow these originals ({the text as
        written: the path}) and copy each into the show on the links thread (see copies / copy_problems)."""
        from ..links import VIDEO_TYPES

        folder = self.document.show.folder
        for key, path in originals.items():
            media = "video" if path.lower().endswith(tuple(VIDEO_TYPES)) else "image"
            problem = original_problem(path, media) or ("" if folder and not self.document.show.read_only
                                                        else "this show isn't saved in a folder it can be copied into")
            if problem:
                self.copy_problems[key] = f"{file_name(path) or path} can't be used: {problem}."
                continue
            self.allowed.allow([path])
            self.copies[key] = ""
            self.copy_problems.pop(key, None)
            self.worker.submit(LinkJob("check", folder, path, "", media, token=("copy", folder, key)))
        self.changed.emit()

    def choose_photo(self, layout_id: str, path: str) -> None:
        """The Layout tab's Choose photo…: the same, for a layout's room photo."""
        self.choose("", layout_id, path, "image")

    def update_now(self, original: str) -> None:
        """Use a ready update at once, even where it's on the lights (one undone before is used too)."""
        here = folder_key(self.document.show.folder)
        for link in links_in(self.document.show):
            if link_key(link.original) == link_key(original):
                self._declined.pop((here, link.input_id, link.element_id), None)
        self._use_updates(force=link_key(original))

    # ------------------------------------------------------------ answers

    def poll(self) -> None:
        self.worker.watch()
        results = self.worker.take_results()
        if self._later and not self.document.dragging:
            results, self._later = self._later + results, []
        for result in results:
            self._take(result)
        if self._use_updates() or results:
            self.changed.emit()

    def _take(self, result: LinkResult) -> None:
        job = result.job
        if job.kind == "tidy":
            return
        here = folder_key(self.document.show.folder)
        token = job.token
        if isinstance(token, tuple) and token[:1] == ("copy",):
            _copy, folder, key = token
            if folder_key(folder) != here:
                self.copies.pop(key, None)
                return
            if result.state in ("copied", "update"):
                self.copies[key] = result.file
            else:
                self.copies.pop(key, None)
                self.copy_problems[key] = _problem_text(result)
            return
        if isinstance(token, tuple) and token[:1] == ("choose",):
            _choose, folder, input_id, element_id = token
            if folder_key(folder) != here:
                return  # (another show was loaded meanwhile: the copy stays in that show's folder, unused)
            if self.document.dragging:
                self._later.append(result)  # (made when the drag ends: never folded into its undo step)
                return
            self.copying.pop((input_id, element_id), None)
            if result.state in ("copied", "update"):
                self._linked(input_id, element_id, job.original, result.file)
                self._states[(here, link_key(job.original))] = LinkState("ok")
            else:
                self.problems[(input_id, element_id)] = _problem_text(result)
            return
        if folder_key(job.folder) != here:
            return
        key = (here, link_key(job.original))
        if result.state in ("update", "copied"):
            self._states[key] = LinkState("update", new_file=result.file)
            self._failed_at.pop(key, None)
        elif result.state == "unreachable" and result.detail.startswith("checking has stopped"):
            self._states[key] = LinkState("unreachable", result.detail)
        elif result.state == "ok":
            if self._states.get(key, LinkState("ok")).status != "update":
                self._states[key] = LinkState("ok")
            self._failed_at.pop(key, None)
        else:
            if self._states.get(key, LinkState("ok")).status != "update":
                self._states[key] = LinkState(result.state, result.detail)
            if result.state in ("unreachable", "missing", "bad"):
                self._failed_at[key] = self._clock()
            log_info(f"Linked file {file_name(job.original)}: {_problem_text(result)} (the show's own copy is used)",
                     key=f"link-{result.state}-{key[1]}")

    def _linked(self, input_id: str, element_id: str, original: str, file: str) -> None:
        """A chosen file is copied in: point the element (or the room photo) at it, as one undo step."""
        show = self.document.show
        if input_id == "":
            layout = show.layout(element_id)
            if layout is None:
                return
            new = _replace_layout(show, layout.id, backdrop=file, backdrop_original=original)
            self.document.apply(new, f"Room photo: {file_name(original)}")
            self._applied.pop((folder_key(show.folder), "", layout.id), None)
            return
        inp = show.inputs.get(input_id)
        found = find_element(inp, element_id) if inp is not None else None
        if found is None:
            return
        new = set_element_value(show, input_id, element_id, "original", original)
        new = set_element_value(new, input_id, element_id, "file", file)
        what = "Video" if found[2].get("kind") == "video" else "Picture"
        self.document.apply(new, f"{what}: {file_name(original)}")
        self._applied.pop((folder_key(show.folder), input_id, element_id), None)
        self._declined.pop((folder_key(show.folder), input_id, element_id), None)

    def _use_updates(self, force: str = "") -> bool:
        """Use ready updates where their input isn't on the lights (force: this original's everywhere)."""
        if self.document.dragging or self.document.read_only:
            return False
        here = folder_key(self.document.show.folder)
        in_use = self._in_use() if not force else frozenset()
        done = False
        for link in links_in(self.document.show):
            state = self._states.get((here, link_key(link.original)))
            if state is None or state.status != "update" or not state.new_file or link.file == state.new_file:
                continue
            if force and link_key(link.original) != force:
                continue
            where = (here, link.input_id, link.element_id)
            if not force and self._declined.get(where) == state.new_file:
                continue  # (someone undid this update: it's offered, with Update now, not made again)
            if not link.is_photo and link.input_id in in_use and link.file:
                continue  # (on the lights: it waits, unless it had no copy at all)
            if not (Path(self.document.show.folder) / state.new_file).is_file():  # (the show's own folder: never an original)
                self._states[(here, link_key(link.original))] = LinkState("checking")
                self._submit(link)
                continue
            show = self.document.show
            if link.is_photo:
                layout = show.layout(link.element_id)
                if layout is None:
                    continue
                new = _replace_layout(show, layout.id, backdrop=state.new_file)
                text = f"Room photo updated from {file_name(link.original)}"
            else:
                new = set_element_value(show, link.input_id, link.element_id, "file", state.new_file)
                text = f"{file_name(link.original)} updated from its original"
            if self.document.apply(new, text, automatic=True):
                done = True
                self._applied[where] = state.new_file
                log_info(f"Linked file {file_name(link.original)}: the new version is used now ({state.new_file})")
        if done or force:
            for key, state in list(self._states.items()):  # updates every link has taken are just "ok" now
                if state.status == "update" and key[0] == here and not any(
                        link_key(link.original) == key[1] and link.file != state.new_file
                        for link in links_in(self.document.show)):
                    self._states[key] = LinkState("ok")
        return done

    def _engine_in_use(self) -> frozenset[str]:
        frames = getattr(self._engine, "frames", None)
        frame = frames.get() if frames is not None else None
        return getattr(frame, "in_use", frozenset()) if frame is not None else frozenset()

    # ------------------------------------------------------------ the show

    def _show_changed(self, show: Show) -> None:
        """New links (an AI tool wrote one, an undo brought one back): ask about them. An update this made that's been
        undone is remembered, so it's offered (Update now) rather than made again."""
        here = folder_key(show.folder)
        for where, applied in list(self._applied.items()):
            if where[0] != here:
                continue
            link = next((l for l in links_in(show) if (l.input_id, l.element_id) == where[1:]), None)
            if link is None or link.file != applied:
                del self._applied[where]
                if link is not None:
                    self._declined[where] = applied
        for link in links_in(show):
            mark = (here, link_key(link.original), link.file)
            if mark not in self._known:
                self._known.add(mark)
                self._submit(link)

    def _show_loaded(self, show: Show) -> None:
        import time

        here = folder_key(show.folder)  # (what was known about this show's links is checked afresh)
        for table in (self._states, self._failed_at):
            for key in [k for k in table if k[0] == here]:
                del table[key]
        self.copying.clear()
        self.problems.clear()
        self._later.clear()
        if not show.folder or show.read_only:
            return
        keep = files_used(show) | {safe_relative_file(f) for f in self.document.files_in_history()}
        self.worker.submit(LinkJob("tidy", show.folder, keep=frozenset(keep), since=time.time()))
        self.check_all()

    def close(self) -> None:
        self._poll.stop()
        self._every.stop()
        self.worker.close()


def _problem_text(result: LinkResult) -> str:
    name = file_name(result.job.original)
    if result.state == "missing":
        return f"the original {name} wasn't found"
    if result.state == "refused":
        return f"{name} can't be used: {result.detail}"
    if result.state == "bad":
        return f"{name} couldn't be used: {result.detail}"
    return f"{name} couldn't be reached{': ' + result.detail if result.detail else ''}"
