r"""Run the whole CentraLight app for a while and watch its CPU and memory.

    .\.venv\Scripts\python.exe tools\soak.py --minutes 1
    .\.venv\Scripts\python.exe tools\soak.py --minutes 180 --csv soak.csv

The app runs with no visible window (Qt's offscreen platform), Simulated output and its
own temporary settings and show, so the lights and your real files are never touched.
A test NDI source ("CentraLight Soak Source") plays a slowly moving rainbow for it to
follow. Every --every seconds it records the app's CPU use and memory, and at the end
prints a summary (memory should level off, not keep climbing).
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "CentraLight Soak Source"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def child_pids(parent: int) -> list[int]:
    """Processes started by `parent` (the venv's python.exe starts the real interpreter)."""
    from centralight.priority import PROCESSENTRY32W

    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent:
                found.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return found


def sample(pid: int) -> tuple[float, int, int]:
    """(CPU seconds used so far, private bytes, working set bytes)."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise OSError(f"can't open process {pid}")
    try:
        creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
        kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(kernel), ctypes.byref(user))
        cpu = ((kernel.dwHighDateTime << 32 | kernel.dwLowDateTime) + (user.dwHighDateTime << 32 | user.dwLowDateTime)) / 1e7
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return cpu, int(counters.PrivateUsage), int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


class RainbowSender:
    def __init__(self, size: tuple[int, int], fps: float) -> None:
        import NDIlib as ndi

        self.ndi = ndi
        ndi.initialize()
        settings = ndi.SendCreate()
        settings.ndi_name = SOURCE
        settings.clock_video = False
        self.send = ndi.send_create(settings)
        self.size = size
        self.fps = fps
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        w, h = self.size
        x = np.linspace(0, 1, w, dtype=np.float32)
        frame = self.ndi.VideoFrameV2()
        frame.FourCC = self.ndi.FOURCC_VIDEO_TYPE_BGRX
        frame.xres, frame.yres = w, h
        frame.frame_rate_N, frame.frame_rate_D = int(self.fps), 1
        frame.line_stride_in_bytes = w * 4
        buffer = np.zeros((h, w, 4), dtype=np.uint8)
        start = time.perf_counter()
        next_t = start
        while self.running:
            t = time.perf_counter() - start
            hues = (x + t * 0.05) % 1.0
            row = np.array([colorsys.hsv_to_rgb(float(hh), 0.9, 1.0) for hh in hues[:: max(1, w // 64)]], dtype=np.float32)
            row = np.repeat(row, max(1, w // 64), axis=0)[:w]
            buffer[:, :, 0] = (row[:, 2] * 255)[None, :]
            buffer[:, :, 1] = (row[:, 1] * 255)[None, :]
            buffer[:, :, 2] = (row[:, 0] * 255)[None, :]
            frame.data = buffer
            self.ndi.send_send_video_v2(self.send, frame)
            next_t += 1.0 / self.fps
            time.sleep(max(0.0, next_t - time.perf_counter()))

    def stop(self) -> None:
        self.running = False
        self.thread.join(2)
        self.ndi.send_destroy(self.send)


def make_show(shows: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from centralight.new_show import create_show
    from centralight.show_io import load_show

    folder = shows / "Central Church"
    create_show(folder)
    path = folder / "inputs" / "follow-screen.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tracks"][0][0]["source"] = SOURCE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    load_show(folder)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=1.0)
    p.add_argument("--every", type=float, default=10.0, help="seconds between samples")
    p.add_argument("--size", default="960x540")
    p.add_argument("--csv", type=Path, help="also write the samples here")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    width, height = (int(v) for v in args.size.split("x"))
    home = Path(tempfile.mkdtemp(prefix="centralight-soak-"))
    make_show(home / "shows")
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        CENTRALIGHT_APPDATA=str(home / "appdata"),
        CENTRALIGHT_SHOWS=str(home / "shows"),
        CENTRALIGHT_INSTANCE=f"soak-{os.getpid()}",
    )
    sender = RainbowSender((width, height), 24.0)
    app = subprocess.Popen([sys.executable, str(ROOT / "main.py")], cwd=ROOT, env=env)
    rows = []
    try:
        time.sleep(3.0)
        kids = child_pids(app.pid)
        pid = kids[0] if kids else app.pid
        cores = os.cpu_count() or 1
        last_cpu, _, _ = sample(pid)
        last_t = time.monotonic()
        started = last_t
        print(f"Watching CentraLight (pid {pid}) for {args.minutes:g} min; source {width}x{height} at 24 fps; {cores} cores")
        while time.monotonic() - started < args.minutes * 60:
            time.sleep(args.every)
            if app.poll() is not None:
                print(f"The app stopped by itself (exit code {app.returncode})!")
                return 1
            cpu, private, working = sample(pid)
            now = time.monotonic()
            share = (cpu - last_cpu) / (now - last_t) * 100
            rows.append((round(now - started), round(share, 2), round(share / cores, 3), private // 1024, working // 1024))
            last_cpu, last_t = cpu, now
            print(f"  {rows[-1][0]:>6} s  CPU {share:5.1f} % of one core ({share / cores:.2f} % of the PC)  "
                  f"private {private / 1e6:6.1f} MB  working set {working / 1e6:6.1f} MB", flush=True)
        log = (home / "appdata" / "session.log")
        if args.csv:
            with args.csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["seconds", "cpu_one_core_pct", "cpu_pc_pct", "private_kb", "working_set_kb"])
                writer.writerows(rows)
        if rows:
            shares = [r[1] for r in rows]
            private = [r[3] for r in rows]
            half = len(private) // 2 or 1
            first, second = sum(private[:half]) / half, sum(private[half:]) / max(1, len(private) - half)
            print(f"Summary: CPU average {sum(shares) / len(shares):.1f} % of one core "
                  f"({sum(shares) / len(shares) / cores:.2f} % of the PC), worst {max(shares):.1f} %")
            print(f"Memory: private {private[0] / 1024:.1f} MB at the start, {private[-1] / 1024:.1f} MB at the end, "
                  f"peak {max(private) / 1024:.1f} MB; second half averages {(second - first) / 1024:+.1f} MB vs the first")
        if log.exists():
            text = log.read_text(encoding="utf-8")
            problems = [line for line in text.splitlines() if "Traceback" in line or "Error" in line]
            print(f"Session log: {len(text.splitlines())} lines, {len(problems)} lines mentioning errors")
        return 0
    finally:
        app.kill()
        app.wait(10)
        sender.stop()


if __name__ == "__main__":
    sys.exit(main())
