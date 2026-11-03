r"""Check "Follow screen" end to end with a real NDI source, without touching the lights.

    .\.venv\Scripts\python.exe tools\ndi_follow_check.py

It starts a test NDI source called "CentraLight Test Source" on this PC that switches
between red and blue every second, and runs CentraLight's real engine and NDI receiver
following it, with a Simulated output (nothing is sent to the lights). It reports:
- lag: from a colour change in the source to the new colour in the DMX frame
- reconnect: the source is stopped for 3 seconds and started again (like Media
  Background Pane restarting); the lights should hold their colour meanwhile, and
  follow again a few seconds after it's back.

The test source disappears when the check finishes (about 40 seconds).
"""

from __future__ import annotations

import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from centralight.engine import Engine  # noqa: E402
from centralight.model import Input  # noqa: E402
from centralight.new_show import show_from_patch  # noqa: E402
from centralight.show_io import freeze  # noqa: E402
from centralight.sources import SourceManager  # noqa: E402

SOURCE = "CentraLight Test Source"
RED = (255, 0, 0)
BLUE = (0, 0, 255)
SLIMPAR_101 = 4  # 0-based index of its red channel


class TestSender:
    """An NDI source that shows one flat colour, switching on request."""

    def __init__(self, ndi, size=(640, 360), fps=24.0) -> None:
        self.ndi = ndi
        self.size = size
        self.fps = fps
        self.colour = RED
        self.changed_at = time.perf_counter()
        self._running = False
        self._thread: threading.Thread | None = None
        self._send = None

    def start(self) -> None:
        settings = self.ndi.SendCreate()
        settings.ndi_name = SOURCE
        settings.clock_video = False
        settings.clock_audio = False
        self._send = self.ndi.send_create(settings)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(2)
        if self._send is not None:
            self.ndi.send_destroy(self._send)
            self._send = None

    def set_colour(self, colour) -> None:
        self.colour = colour
        self.changed_at = time.perf_counter()

    def _run(self) -> None:
        w, h = self.size
        frame = self.ndi.VideoFrameV2()
        buffer = np.zeros((h, w, 4), dtype=np.uint8)
        frame.FourCC = self.ndi.FOURCC_VIDEO_TYPE_BGRX
        frame.xres, frame.yres = w, h
        frame.frame_rate_N, frame.frame_rate_D = int(self.fps), 1
        frame.line_stride_in_bytes = w * 4
        next_t = time.perf_counter()
        while self._running:
            r, g, b = self.colour
            buffer[:, :] = (b, g, r, 255)
            frame.data = buffer
            self.ndi.send_send_video_v2(self._send, frame)
            next_t += 1.0 / self.fps
            time.sleep(max(0.0, next_t - time.perf_counter()))


class RecordingOutput:
    """Stands in for the DMX output: remembers when SlimPAR 101's colour changed."""

    def __init__(self) -> None:
        self.changes: list[tuple[float, tuple[int, int, int]]] = []
        self._last = None

    def submit(self, frame: bytes) -> None:
        rgb = tuple(frame[SLIMPAR_101:SLIMPAR_101 + 3])
        if rgb != self._last:
            self._last = rgb
            self.changes.append((time.perf_counter(), rgb))

    def status(self):
        from centralight.output import CONNECTED, OutputStatus

        return OutputStatus(kind="simulated", state=CONNECTED, label="Simulated")


def close_to(rgb, colour) -> bool:
    """NDI compresses the picture, so (255, 0, 0) may arrive as (254, 0, 1)."""
    return all(abs(a - b) <= 24 for a, b in zip(rgb, colour))


def first_change_after(output: RecordingOutput, when: float, colour) -> float | None:
    for stamp, rgb in list(output.changes):
        if stamp >= when and close_to(rgb, colour):
            return stamp - when
    return None


def main() -> int:
    import NDIlib as ndi

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not ndi.initialize():
        print("NDI couldn't start on this PC.")
        return 1
    sender = TestSender(ndi)
    sender.start()
    inp = Input(id="follow-test", name="Follow test", tracks=freeze([[{"id": "E1", "kind": "ndi", "source": SOURCE}], [], [], []]))
    show = show_from_patch().with_inputs({inp.id: inp}, (inp.id,))
    output = RecordingOutput()
    sources = SourceManager()
    engine = Engine(show, output, None, sources=sources, initial_base=inp.id)
    engine.start()
    try:
        print("Waiting for the receiver to find the test source...")
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline and not (output.changes and close_to(output.changes[-1][1], RED)):
            time.sleep(0.05)
        if not output.changes or not close_to(output.changes[-1][1], RED):
            print("FAIL: the test source never reached the lights")
            return 1
        print(f"Following after {output.changes[-1][0] - sender.changed_at:.1f} s")

        lags = []
        colour = BLUE
        for _ in range(10):
            sender.set_colour(colour)
            changed = sender.changed_at
            time.sleep(1.0)
            lag = first_change_after(output, changed, colour)
            if lag is not None:
                lags.append(lag * 1000)
            colour = RED if colour == BLUE else BLUE
        if lags:
            print(f"Lag from source to DMX over {len(lags)} changes: median {statistics.median(lags):.0f} ms, "
                  f"worst {max(lags):.0f} ms (target: 100 ms or less)")
        else:
            print("FAIL: no colour changes reached the lights")

        print("Stopping the test source for 3 seconds (like Media Background Pane restarting)...")
        held = output.changes[-1][1]
        sender.stop()
        time.sleep(3.0)
        status = sources.statuses().get(SOURCE.lower())
        print(f"  while it's gone: lights were {held}, now {output.changes[-1][1]} (held: {close_to(output.changes[-1][1], held)}); "
              f"status: {status.state if status else '?'}")
        sender = TestSender(ndi)
        sender.set_colour(BLUE if close_to(held, RED) else RED)
        target = sender.colour
        sender.start()
        restarted = time.perf_counter()
        while time.perf_counter() - restarted < 15:
            if first_change_after(output, restarted, target) is not None:
                break
            time.sleep(0.05)
        back = first_change_after(output, restarted, target)
        if back is None:
            print("FAIL: the lights didn't follow the source again within 15 s")
        else:
            print(f"  following again {back:.1f} s after the source came back (target: about 5 s)")
        frame = engine.frames.get()
        print(f"Engine: {frame.tick_rate:.0f} ticks/s, {frame.tick_ms:.2f} ms per tick (worst {frame.tick_max_ms:.2f} ms), faults {frame.faults}")
        return 0
    finally:
        engine.stop()
        sources.close()
        sender.stop()


if __name__ == "__main__":
    sys.exit(main())
