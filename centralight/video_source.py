"""Video files for Video elements: decoded to small pictures as playback needs them.

Each file in use has a VideoClip. Its reader thread decodes a little ahead of where playback is (LEAD
seconds), at most 320x180 and 25 pictures a second, and then waits, so decoding is paced by the playback and
the engine never waits for it: it takes the newest picture that's due. A jump (a loop back to the start, or
the Editor's playhead moving) makes the reader seek. Decoding is in software, with two threads: for pictures this
small it's as quick as the graphics card's, and a reader costs a tenth of the memory and opens in a few ms (M14).

The engine's running copies of an input each have a reader of their own (so two copies at different moments don't
make one reader jump back and forth); a new copy takes over a reader another copy of that video has finished with.

clip.frame_at(t) gives the picture for t seconds into the clip (wrapping round at its end). With
threaded=False it decodes on the spot instead, which is what tests and the Editor's exact frames use.

Clips nobody has asked for in IDLE_CLOSE seconds are closed by tidy() (the engine calls it every second).
"""

from __future__ import annotations

import threading
import time
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np

from .log import log_exception, log_info

MAX_W, MAX_H = 320, 180
MAX_FPS = 25.0
LEAD = 0.5  # seconds decoded ahead of playback
JUMP = 2.0  # seconds: further ahead than this, the reader seeks instead of decoding its way there
IDLE_CLOSE = 10.0
DECODE_THREADS = 2  # FFmpeg's threads per reader (its own default is one per core: about 31 here, idle mostly)
REUSE_IDLE = 0.5  # seconds: a copy's reader nobody has asked for this long can be another copy's (M14)


class VideoClip:
    def __init__(self, path: Path, threaded: bool = True) -> None:
        self.path = Path(path)
        self.threaded = threaded
        self.error = ""
        self.duration = 0.0
        self.fps = 0.0
        self._cap = None
        self._lock = threading.Condition()
        self._frames: list[tuple[float, np.ndarray]] = []  # (time, RGB picture), in time order
        self._times: list[float] = []
        self._next_keep = 0.0  # pictures closer together than 1 / MAX_FPS are skipped
        self._want = 0.0
        self._last: np.ndarray | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self.last_used = time.monotonic()
        self.stamp = 0  # the file's modification time when it was opened (VideoLibrary reopens a replaced file)
        self._open()

    # ------------------------------------------------------------ opening

    def _open(self) -> None:
        import cv2

        cap = None
        threads = [cv2.CAP_PROP_N_THREADS, DECODE_THREADS] if hasattr(cv2, "CAP_PROP_N_THREADS") else []
        try:
            cap = cv2.VideoCapture(str(self.path), cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE, *threads])
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(str(self.path), cv2.CAP_FFMPEG)
        except Exception as exc:  # an unusual codec or a broken file
            log_exception(f"opening the video {self.path.name}", exc)
            cap = None
        if cap is None or not cap.isOpened():
            self.error = "this video can't be read"
            return
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        self.fps = fps if 1.0 <= fps <= 240.0 else 25.0
        self.duration = count / self.fps if count > 0 else 0.0
        self._cap = cap
        if self.duration <= 0:
            self.error = "this video is empty"

    # ------------------------------------------------------------ pictures

    def frame_at(self, t: float) -> np.ndarray | None:
        """The picture t seconds into the clip (wrapping round), RGB uint8 at most 320x180; None if there's none
        yet (the very first moment of a threaded clip) or the file can't be read."""
        self.last_used = time.monotonic()
        if self._cap is None or self.duration <= 0:
            return None
        t = float(t) % self.duration
        if not self.threaded:
            with self._lock:
                self._decode_towards(t, ahead=0.0)
                return self._best(t)
        with self._lock:
            self._want = t
            self._lock.notify_all()
            if self._thread is None:
                self._running = True
                self._thread = threading.Thread(target=self._run, name=f"video-{self.path.name}", daemon=True)
                self._thread.start()
            if not self._times:  # (a new reader, or one just taken over: its first picture now, not a blank moment)
                self._decode_towards(t, ahead=0.0, limit=4)
            return self._best(t)

    def take_over(self) -> None:
        """Another copy of the video plays from this reader now (M14): what it kept is for the other one's moment."""
        with self._lock:
            self._frames.clear()
            self._times.clear()
            self._last = None
            self._next_keep = 0.0

    def _best(self, t: float) -> np.ndarray | None:
        """The newest picture that's due at t (or the last one shown, while the reader catches up)."""
        index = bisect_right(self._times, t + 1e-6) - 1
        if index >= 0:
            self._last = self._frames[index][1]
        return self._last

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
                want = self._want
                if self._times and self._times[0] - 1e-6 <= want and self._times[-1] >= min(want + LEAD, self.duration - 1.5 / self.fps):
                    self._lock.wait(0.05)  # far enough ahead: wait for playback
                    continue
            try:
                with self._lock:
                    if self._decode_towards(want, ahead=LEAD, limit=3) == 0:
                        self._lock.wait(0.05)  # nothing more to read just now: never spin
            except Exception as exc:
                log_exception(f"decoding the video {self.path.name}", exc)
                time.sleep(0.5)

    def _decode_towards(self, t: float, ahead: float, limit: int = 10_000) -> int:
        """Decode pictures until there's one at t + ahead (seeking first if t is before what's kept or far
        ahead of it). limit: at most this many pictures per call (the reader thread lets the engine in).
        Returns how many were decoded."""
        import cv2

        cap = self._cap
        last = self._times[-1] if self._times else None
        if last is None or t < self._times[0] - 1e-6 or t > last + JUMP:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t - 1.0 / self.fps) * 1000.0)
            self._frames.clear()
            self._times.clear()
            self._next_keep = 0.0
        target = min(t + ahead, self.duration)
        decoded = 0
        while (not self._times or self._times[-1] < target - 1e-6) and decoded < limit:
            if not cap.grab():
                # the end of the file. If that's well before the length the file claims (some MKV / WebM / TS
                # files, or a cut-short copy), the clip really ends here: loop from here instead of waiting
                if self._times and self._times[-1] < self.duration - 2.0 / self.fps:
                    real = self._times[-1] + 1.0 / self.fps
                    log_info(f"Video {self.path.name}: it ends at {real:.1f} s, not {self.duration:.1f} s as it says")
                    self.duration = real
                break
            moment = max(0.0, cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            decoded += 1
            if self._times and moment < self._next_keep:
                continue  # more than 25 a second: skip this one
            ok, image = cap.retrieve()
            if not ok or image is None:
                continue
            self._keep(moment, image)
        while len(self._times) > 2 and self._times[1] <= t - 1.0 / self.fps:  # drop what's been shown
            self._times.pop(0)
            self._frames.pop(0)
        return decoded

    def _keep(self, moment: float, image: np.ndarray) -> None:
        import cv2

        h, w = image.shape[:2]
        scale = min(1.0, MAX_W / w, MAX_H / h)
        if scale < 1.0:
            image = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        picture = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        if self._times and moment <= self._times[-1]:
            return
        self._times.append(moment)
        self._frames.append((moment, picture))
        self._next_keep = moment + 1.0 / MAX_FPS - 1e-4

    def close(self) -> None:
        with self._lock:
            self._running = False
            self._lock.notify_all()
        if self._thread is not None:
            self._thread.join(1.0)
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None


class VideoLibrary:
    """The clips in use, by file. Shared by the engine, the Editor and the previews."""

    def __init__(self) -> None:
        self._clips: dict[tuple[str, bool, Any], VideoClip] = {}
        self._lock = threading.Lock()

    def clip(self, path: Path, threaded: bool = True, player: Any = None) -> VideoClip:
        """The clip for a file (opened the first time; opened again if the file has been replaced). player: who plays
        it, for the engine's running copies of an input (M14): each has a clip of its own, so two copies of one video
        at different moments (on the Overlay, say) don't make one reader seek back and forth every tick. A new copy
        takes over a reader another copy of the video has finished with, so sending a video again and again (to the
        beat, say) doesn't open one each time. None: shared."""
        path = Path(path)
        name = str(path.resolve()).lower()
        key = (name, threaded, player)
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        old = None
        taken = False
        with self._lock:
            clip = self._clips.get(key)
            if clip is not None and clip.stamp != stamp:
                old, clip = self._clips.pop(key), None
            if clip is None and player is not None:
                now = time.monotonic()
                spare = next((k for k, c in self._clips.items() if k[:2] == (name, threaded) and k[2] is not None
                              and c.stamp == stamp and now - c.last_used > REUSE_IDLE), None)
                if spare is not None:
                    clip = self._clips.pop(spare)
                    clip.last_used = now  # (so another new copy at the same moment doesn't take it too)
                    self._clips[key] = clip
                    taken = True
            if clip is None:
                clip = VideoClip(path, threaded)
                clip.stamp = stamp
                self._clips[key] = clip
                if clip.error:
                    log_info(f"Video {path.name}: {clip.error}", key=f"video-{key[0]}")
        if taken:
            clip.take_over()
        if old is not None:
            _close_later(old)
        return clip

    def tidy(self, idle: float = IDLE_CLOSE) -> None:
        """Close clips nobody has asked for in a while."""
        now = time.monotonic()
        with self._lock:
            stale = [key for key, clip in self._clips.items() if now - clip.last_used > idle]
            clips = [self._clips.pop(key) for key in stale]
        for clip in clips:
            _close_later(clip)

    def close(self) -> None:
        with self._lock:
            clips = list(self._clips.values())
            self._clips.clear()
        for clip in clips:
            clip.close()


def _close_later(clip: VideoClip) -> None:
    """Close a reader on a thread of its own, so the engine's tick doesn't wait for it."""
    threading.Thread(target=clip.close, name=f"video-close-{clip.path.name}", daemon=True).start()


videos = VideoLibrary()
