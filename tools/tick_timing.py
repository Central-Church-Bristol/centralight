r"""How well the engine keeps time while the app's pictures are busy: "the lights come first".

    .\.venv\Scripts\python.exe tools\tick_timing.py
    .\.venv\Scripts\python.exe tools\tick_timing.py --seconds 60 --switch 0.001

Runs the real app in this process with no visible window (Qt's offscreen platform), Simulated
output and its own temporary settings and show, so the lights and your real files are never
touched. A test NDI source (the soak's moving rainbow, 960x540 at 24 fps) is on Base. For each
situation it measures every engine tick: how late it started after its time, and how long it
took (median, 99th percentile and worst, and how many started over 5 ms late):

- no window open (CentraLight in the tray)
- the Dashboard showing (its live picture drawn 5 times a second)
- the Editor playing a video at its full preview size (640x360), in a big window

--switch sets Python's thread switch interval (sys.setswitchinterval; normally 5 ms), to see
whether the engine waits for Python's lock while the pictures are being drawn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = "media/timing-test.mp4"


def make_video(path: Path, seconds: float = 8.0, fps: int = 30) -> None:
    """A colourful moving test clip, 1280x720 (CentraLight decodes it small, as it would any video)."""
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
    xs = np.linspace(0, 1, 1280, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, 720, dtype=np.float32)[:, None]
    for i in range(int(seconds * fps)):
        t = i / fps
        frame = np.empty((720, 1280, 3), dtype=np.uint8)
        frame[..., 0] = (127 + 127 * np.sin(6.28 * (xs + t * 0.3))).astype(np.uint8)
        frame[..., 1] = (127 + 127 * np.sin(6.28 * (ys + t * 0.2))).astype(np.uint8)
        frame[..., 2] = (127 + 127 * np.sin(6.28 * (xs + ys + t * 0.5))).astype(np.uint8)
        writer.write(frame)
    writer.release()


def make_show(shows: Path, source: str) -> None:
    """The soak's show (Follow screen on the test source), plus an input playing the test clip."""
    from centralight.new_show import add_starter_inputs, create_show
    from centralight.show_io import load_show

    folder = shows / "Central Church"
    add_starter_inputs(create_show(folder))  # (a new show has no inputs: Follow screen is made here)
    path = folder / "inputs" / "follow-screen.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tracks"][0][0]["source"] = source
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    make_video(folder / VIDEO)
    video_input = {"format": "centralight-input", "version": 1, "id": "timing-video", "name": "Timing video",
                   "tracks": [[{"id": "E1", "kind": "video", "file": VIDEO}], [], [], []]}
    (folder / "inputs" / "timing-video.json").write_text(json.dumps(video_input, indent=2), encoding="utf-8")
    load_show(folder)


def run_for(app, seconds: float) -> None:
    """Let the app run (its event loop) for a while. (Not app.quit(): that would ask the window to close.)"""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def describe(name: str, timing: dict) -> str:
    return (f"{name:<38} late: median {timing['late_median_ms']:.2f} ms, 99th {timing['late_p99_ms']:.2f} ms, "
            f"worst {timing['late_worst_ms']:.1f} ms, {int(timing['late_over_5ms'])} of {int(timing['ticks'])} over 5 ms | "
            f"tick: median {timing['work_median_ms']:.3f} ms, 99th {timing['work_p99_ms']:.2f} ms, worst {timing['work_worst_ms']:.1f} ms")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=30.0, help="how long to measure each situation")
    p.add_argument("--switch", type=float, default=None, help="sys.setswitchinterval to use (seconds)")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    home = Path(tempfile.mkdtemp(prefix="centralight-timing-"))
    os.environ.update(QT_QPA_PLATFORM="offscreen", CENTRALIGHT_APPDATA=str(home / "appdata"),
                      CENTRALIGHT_SHOWS=str(home / "shows"), CENTRALIGHT_INSTANCE=f"timing-{os.getpid()}")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    if args.switch is not None:
        sys.setswitchinterval(args.switch)
    from soak import SOURCE, RainbowSender

    make_show(home / "shows", SOURCE)
    sender = RainbowSender((960, 540), 24.0)

    from PySide6.QtWidgets import QApplication

    from centralight.app import open_show
    from centralight.engine import Engine
    from centralight.new_show import FOLLOW_SCREEN_ID
    from centralight.output import DmxOutputThread, device_spec, universe_size
    from centralight.settings import Settings
    from centralight.sources import SourceManager
    from centralight.timing import begin_precise_timer
    from centralight.ui.main_window import MainWindow
    from centralight.ui.theme import apply_theme

    begin_precise_timer()
    import cv2

    cv2.setNumThreads(1)  # as the app runs
    app = QApplication(["centralight-timing"])
    app.setStyle("Fusion")
    apply_theme(app)
    settings = Settings.load()  # (fresh: Simulated output)
    assert settings.output == "simulated", settings.output
    show, _problem = open_show(settings)
    output = DmxOutputThread(device_spec(settings, universe_size(show.fixtures)))
    output.start()
    sources = SourceManager()
    engine = Engine(show, output, None, sources=sources, initial_base=FOLLOW_SCREEN_ID)
    engine.start()
    window = MainWindow(settings, show, engine, output, save_files=False)
    results = {}
    try:
        print(f"Measuring each situation for {args.seconds:g} s (switch interval {sys.getswitchinterval() * 1000:g} ms)")
        run_for(app, 5.0)  # the feed connects
        window.hide()
        run_for(app, 2.0)
        engine.reset_timing()
        run_for(app, args.seconds)
        results["no window open"] = engine.tick_timing()
        print(describe("No window open:", results["no window open"]), flush=True)

        window.resize(1920, 1080)
        window.show()
        window.show_page("dashboard")
        run_for(app, 2.0)
        engine.reset_timing()
        run_for(app, args.seconds)
        results["dashboard"] = engine.tick_timing()
        print(describe("The Dashboard showing:", results["dashboard"]), flush=True)

        window.open_editor("timing-video")
        window.editor_page.select_element("E1")
        run_for(app, 3.0)
        engine.reset_timing()
        pictures = window.editor_page._renderer.pictures
        before = pictures.get_versioned()[0]
        run_for(app, args.seconds)
        results["editor video"] = engine.tick_timing()
        print(describe("The Editor playing a video (640x360):", results["editor video"]), flush=True)
        rate = (pictures.get_versioned()[0] - before) / args.seconds
        print(f"The Editor's viewports drew {rate:.1f} pictures a second (it aims for 8 while something moves)", flush=True)
    finally:
        window._shut_down = True
        window.editor_page.shutdown()
        engine.stop()
        output.close(blackout=False)
        sources.close()
        sender.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
