"""The thread that checks and copies linked originals (M13), so nothing else ever waits for them.

Reading Google Drive is what hung Media Background Pane, so an original is only ever touched here: never by the
engine, the lights' tick, the window or playback. If a drive or network hangs, only this thread waits.

A job asks about one original (and the show's copy of it now):
- unchanged (its size and modified time are what they were when the copy was made): "ok"
- changed, or never copied: the original is copied to a temporary file in the show's media folder, checked to open
  as a picture or video, and kept as a new copy with its own name ("candles 2.mp4"), so the copy in use is never
  overwritten and undo can go back to it: "update" (or "copied" when there was no copy). If the new version is the
  same as the copy in use, it's just noted: "ok".
- gone: "missing"; can't be read: "unreachable"; not a picture or video: "bad". The copy in use carries on.

A job that makes no progress for STUCK_AFTER seconds is given up on ("unreachable: not answering") and a fresh thread
carries on with the other jobs; the stuck one is left to finish or not (its answer is then ignored). After MAX_STUCK of
those, checking stops until CentraLight restarts.

Tidy jobs remove copies CentraLight made that nothing uses any more (only when a show loads, never while editing).
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .links import file_name, link_key, original_problem, read_stamps, safe_media_file, update_stamps
from .log import log_exception, log_info
from .model import is_device_name

STUCK_AFTER = 45.0  # seconds with no progress before a job is given up on
MAX_STUCK = 3  # threads left stuck before checking stops
MAX_COPY_BYTES = 4_000_000_000  # an original bigger than this isn't copied
SPARE_BYTES = 500_000_000  # room left on the disk after a copy
CHUNK = 1 << 20
INCOMING = ".incoming"  # in the media folder: copies being made
STOPPED = "checking has stopped until CentraLight restarts (drives weren't answering); the show's own copies keep playing"


@dataclass(frozen=True)
class LinkJob:
    kind: str  # "check" (or copy, when there's no copy yet) | "tidy"
    folder: str  # the show folder
    original: str = ""
    file: str = ""  # the show's copy now ("media/candles.mp4"; "" = none yet)
    media: str = "image"  # "image" or "video": what the original must open as
    keep: frozenset[str] = frozenset()  # tidy: copies still in use (by the show or its undo history)
    since: float = 0.0  # tidy: when that list was taken (wall time): copies made since are left
    token: Any = None  # given back with the result


@dataclass(frozen=True)
class LinkResult:
    job: LinkJob
    state: str  # ok | update | copied | missing | unreachable | bad | refused | tidied
    file: str = ""  # the new copy (update / copied)
    detail: str = ""


class FileOps:
    """What the worker does to files (tests swap in ones that hang or fail)."""

    def stat(self, path: str) -> os.stat_result:
        return os.stat(path)

    def open_original(self, path: str):
        return open(path, "rb")

    def verify(self, path: Path, media: str) -> bool:
        return verify_media(path, media)


def verify_media(path: Path, media: str) -> bool:
    """Does the file open as a picture (or a video with at least one picture in it)?"""
    import cv2
    import numpy as np

    try:
        if media == "image":
            data = np.fromfile(str(path), dtype=np.uint8)
            return cv2.imdecode(data, cv2.IMREAD_UNCHANGED) is not None
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return False
            ok, frame = cap.read()
            return bool(ok) and frame is not None
        finally:
            cap.release()
    except Exception as exc:  # a broken file must never stop the worker
        log_exception("checking a copied file", exc)
        return False


def same_contents(a: Path, b: Path) -> bool:
    """Byte for byte the same (read a piece at a time: videos can be big)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as first, open(b, "rb") as second:
            while True:
                x, y = first.read(CHUNK), second.read(CHUNK)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def copy_name(folder: Path, original: str, taken: set[str]) -> str:
    """A new name in the media folder for a copy of this original: "candles.mp4", then "candles 2.mp4", ..."""
    from .input_edit import media_stem

    base = Path(file_name(original))
    stem, suffix = media_stem(base.stem), base.suffix.lower()
    n = 1
    while True:
        name = f"media/{stem}{suffix}" if n == 1 else f"media/{stem} {n}{suffix}"
        if name.casefold() not in taken and not (folder / name).exists():
            return name
        n += 1


class _Worker(threading.Thread):
    def __init__(self, owner: LinkWorker, generation: int) -> None:
        super().__init__(name=f"CentraLight links {generation}", daemon=True)
        self.owner = owner
        self.generation = generation
        self.job: LinkJob | None = None
        self.progress_at = owner.clock()
        self.given_up = False

    def run(self) -> None:
        while True:
            if self.given_up:
                return
            job = self.owner._next(self)
            if job is None:
                return
            self.job = job
            self.progress_at = self.owner.clock()
            try:
                result = self.owner._run(job, self)
            except Exception as exc:  # one bad job never stops the others
                log_exception(f"checking {file_name(job.original) or job.kind}", exc)
                result = LinkResult(job, "unreachable", detail=str(exc))
            self.job = None
            self.owner._finish(self, result)


class LinkWorker:
    """Runs LinkJobs on its own thread. submit() never waits; take_results() gives the answers so far."""

    def __init__(self, ops: FileOps | None = None, clock: Callable[[], float] = time.monotonic,
                 stuck_after: float = STUCK_AFTER) -> None:
        self.ops = ops or FileOps()
        self.clock = clock
        self.stuck_after = stuck_after
        self._lock = threading.Lock()
        self._jobs: list[LinkJob] = []
        self._results: list[LinkResult] = []
        self._worker: _Worker | None = None
        self._generation = 0
        self.stuck = 0  # threads given up on
        self.stopped = False  # too many stuck threads: no more checking until a restart
        self._closed = False
        self._bad: dict[str, tuple[tuple[int, int], str]] = {}  # an original's version that couldn't be used, and why

    # ------------------------------------------------------------ the window's side

    def submit(self, job: LinkJob) -> None:
        """Queue a job (one for the same original and show already waiting is replaced by it)."""
        with self._lock:
            if self._closed:
                return
            if self.stopped:  # (it still answers, so nothing waits for ever for a copy that won't come)
                self._results.append(LinkResult(job, "unreachable", detail=STOPPED))
                return
            self._jobs = [j for j in self._jobs if not _same_job(j, job)] + [job]
            if self._worker is None:
                self._start()

    def take_results(self) -> list[LinkResult]:
        with self._lock:
            results, self._results = self._results, []
        return results

    def pending(self) -> list[LinkJob]:
        """Jobs waiting, and the one being worked on."""
        with self._lock:
            current = [self._worker.job] if self._worker is not None and self._worker.job is not None else []
            return current + list(self._jobs)

    def watch(self) -> None:
        """Give up on a job that's stopped moving (the window calls this about once a second)."""
        with self._lock:
            worker = self._worker
            if worker is None or worker.job is None or self.clock() - worker.progress_at < self.stuck_after:
                return
            job = worker.job
            worker.given_up = True
            self._worker = None
            self.stuck += 1
            self._results.append(LinkResult(job, "unreachable", detail="it isn't answering (a drive or the network may be stuck)"))
            log_info(f"Linked file {file_name(job.original) or job.kind}: no answer for {self.stuck_after:.0f} s, so it was "
                     f"left to finish on its own; the other checks carry on", key=f"link-stuck-{link_key(job.original)}")
            if self.stuck >= MAX_STUCK:
                self.stopped = True
                self._results += [LinkResult(waiting, "unreachable", detail=STOPPED) for waiting in self._jobs]
                self._jobs.clear()
                log_info(f"{self.stuck} linked-file checks are stuck, so checking stops until CentraLight restarts "
                         "(the shows' own copies keep playing)")
            elif self._jobs:
                self._start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._jobs.clear()
            if self._worker is not None:
                self._worker.given_up = True
                self._worker = None

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests: wait until nothing is queued or running."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._worker is None and not self._jobs:
                    return True
            time.sleep(0.01)
        return False

    # ------------------------------------------------------------ the thread's side

    def _start(self) -> None:
        self._generation += 1
        self._worker = _Worker(self, self._generation)
        self._worker.start()

    def _next(self, worker: _Worker) -> LinkJob | None:
        with self._lock:
            if worker is not self._worker or not self._jobs:
                if worker is self._worker:
                    self._worker = None
                return None
            return self._jobs.pop(0)

    def _finish(self, worker: _Worker, result: LinkResult) -> None:
        with self._lock:
            if worker is self._worker and not worker.given_up:
                self._results.append(result)

    def _moved(self, worker: _Worker) -> None:
        worker.progress_at = self.clock()

    def _run(self, job: LinkJob, worker: _Worker) -> LinkResult:
        if job.kind == "tidy":
            return self._tidy(job)
        return self._check(job, worker)

    def _check(self, job: LinkJob, worker: _Worker) -> LinkResult:
        problem = original_problem(job.original, job.media) or ("" if job.folder else "there's no show folder to copy it into")
        if problem:
            return LinkResult(job, "refused", detail=problem)
        folder = Path(job.folder)
        current = safe_media_file(job.file)  # (checked as text first: never outside the show, never a device's name)
        try:
            info = self.ops.stat(job.original)
        except FileNotFoundError:
            return LinkResult(job, "missing")
        except OSError as exc:
            return LinkResult(job, "unreachable", detail=exc.strerror or str(exc))
        self._moved(worker)
        if not _is_file(info):
            return LinkResult(job, "missing", detail="it isn't a file")
        if info.st_size > MAX_COPY_BYTES:
            return LinkResult(job, "bad", detail=f"it's too big to copy (over {MAX_COPY_BYTES // 1_000_000_000} GB)")
        stamp = {"original": job.original, "size": int(info.st_size), "modified": int(info.st_mtime_ns), "made": time.time()}
        key = link_key(job.original)
        with self._lock:
            bad = self._bad.get(key)
        if bad is not None and bad[0] == (stamp["size"], stamp["modified"]):
            return LinkResult(job, "bad", detail=bad[1])  # (the same version that couldn't be used: not copied again)
        stamps = read_stamps(folder)

        def matches(file: str) -> bool:
            have = stamps.get(file)
            return (have is not None and link_key(have["original"]) == key and have["size"] == stamp["size"]
                    and have["modified"] == stamp["modified"] and (folder / file).is_file())

        if current and matches(current):
            return LinkResult(job, "ok")
        stamp["made"] = time.time()
        for file in stamps:  # this version was copied before (an undone update, say): use that copy
            if file != current and matches(file):
                return LinkResult(job, "update" if current else "copied", file=file)
        incoming = folder / "media" / INCOMING
        temp = incoming / f"{uuid.uuid4().hex}{Path(file_name(job.original)).suffix.lower()}"
        try:
            incoming.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(incoming).free < info.st_size + SPARE_BYTES:
                return LinkResult(job, "bad", detail="there isn't enough room on the disk for a copy")
            self._copy(job.original, temp, info.st_size, worker)
            after = self.ops.stat(job.original)
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                return LinkResult(job, "unreachable", detail="it changed while it was being copied (it's checked again later)")
            if not self.ops.verify(temp, job.media):
                wanted = "a picture" if job.media == "image" else "a video"
                with self._lock:
                    self._bad[key] = ((stamp["size"], stamp["modified"]), f"it doesn't open as {wanted}")
                return LinkResult(job, "bad", detail=f"it doesn't open as {wanted}")
            if worker.given_up:
                raise OSError("the check was given up on")  # (a fresh thread does it: this one mustn't finish it too)
            if current and same_contents(temp, folder / current):
                made = stamps.get(current, {}).get("made", 0.0)
                update_stamps(folder, lambda s: s.__setitem__(current, {**stamp, "made": made}))
                return LinkResult(job, "ok")
            taken = {name.casefold() for name in stamps}
            name = copy_name(folder, job.original, taken)
            if worker.given_up:
                raise OSError("the check was given up on")
            os.replace(temp, folder / name)
            update_stamps(folder, lambda s: s.__setitem__(name, stamp))
            log_info(f"Linked file {file_name(job.original)}: copied in as {name}")
            return LinkResult(job, "update" if current else "copied", file=name)
        except FileNotFoundError:
            return LinkResult(job, "missing")
        except OSError as exc:
            return LinkResult(job, "unreachable", detail=exc.strerror or str(exc))
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _copy(self, original: str, target: Path, size: int, worker: _Worker) -> None:
        copied = 0
        with self.ops.open_original(original) as source, open(target, "wb") as out:
            while True:
                if worker.given_up:
                    raise OSError("the check was given up on")
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max(size, 0) + CHUNK * 4 or copied > MAX_COPY_BYTES:
                    raise OSError("the file kept growing while it was copied")
                out.write(chunk)
                self._moved(worker)

    def _tidy(self, job: LinkJob) -> LinkResult:
        """Remove copies CentraLight made that the show (and its undo history) no longer use, and leftover pieces."""
        if not job.folder:
            return LinkResult(job, "tidied")
        folder = Path(job.folder)
        keep = {name.casefold() for name in job.keep}
        removed: list[str] = []

        def tidy(stamps: dict) -> None:
            for file in list(stamps):
                if file.casefold() in keep or (job.since > 0 and stamps[file].get("made", 0.0) >= job.since - 1.0):
                    continue  # (in use, or made after the list of what's in use was taken: it may be about to be)
                path = folder / file
                try:
                    if path.is_file():
                        path.unlink()
                    removed.append(file)
                    del stamps[file]
                except OSError:  # (open somewhere: tried again next time the show loads)
                    pass

        try:
            update_stamps(folder, tidy)
        except OSError as exc:  # (media\links.json can't be read just now: nothing is tidied this time)
            return LinkResult(job, "tidied", detail=str(exc))
        incoming = folder / "media" / INCOMING
        if incoming.is_dir():
            for leftover in incoming.iterdir():
                if is_device_name(leftover.name):
                    continue  # (never looked at: a device's name)
                try:
                    if leftover.is_file() and time.time() - leftover.stat().st_mtime > 3600:
                        leftover.unlink()
                except OSError:
                    pass
        if removed:
            log_info(f"Tidied {len(removed)} unused cop{'y' if len(removed) == 1 else 'ies'} of linked files in "
                     f"{folder.name}: {', '.join(removed[:5])}{'…' if len(removed) > 5 else ''}")
        return LinkResult(job, "tidied", detail=", ".join(removed))


def _same_job(a: LinkJob, b: LinkJob) -> bool:
    if a.kind != b.kind or a.folder.casefold() != b.folder.casefold():
        return False
    if a.kind == "tidy":
        return True
    return link_key(a.original) == link_key(b.original) and a.file == b.file and a.token == b.token


def _is_file(info: os.stat_result) -> bool:
    import stat

    return stat.S_ISREG(info.st_mode)
