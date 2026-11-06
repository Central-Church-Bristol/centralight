r"""Run the whole CentraLight app for a while and watch its CPU and memory.

    .\.venv\Scripts\python.exe tools\soak.py --minutes 1
    .\.venv\Scripts\python.exe tools\soak.py --minutes 180 --csv soak.csv
    .\.venv\Scripts\python.exe tools\soak.py --minutes 180 --switch-shows 120

The app runs with no visible window (Qt's offscreen platform), Simulated output and its
own temporary settings and show, so the lights and your real files are never touched.
A test NDI source ("CentraLight Soak 1234 Source", with the soak's process number) plays a
slowly moving rainbow for it to follow, so two soaks at once never follow each other's (they
get their own remote ports too). Every --every seconds it records the app's CPU use and
memory, and at the end prints a summary (memory should level off, not keep climbing).

--switch-shows N: there are two shows, and every N seconds the other one is loaded (through
the remote, as a Stream Deck key would) and its "Follow screen" sent, so memory can be
watched across many show loads.

--linked-video N (M13): each show also has "Linked video", a Video element linked to an original
file outside the shows (allowed, as if someone at the PC had chosen it). The first show sends it
to Base each time it's loaded, so it plays on the lights; every N minutes the original is
replaced by a different video, so CentraLight copies it in and swaps it in when it isn't on the
lights. The summary counts the copies and swaps in the session log.

--countdown N (M13): each show has a show variable "mood" and an automation "mood above 0.5 → Fade to
black; when it stops → Follow screen". Every N minutes the soak sets mood to 0.9 (the automation's run
waits for its 15 s countdown, then fades), and 40 s later back to 0.1 (Follow screen comes back).

--overlay N (M14): every N seconds two copies of an input go on the Overlay a second apart (the linked video
with --linked-video, so each copy has a video reader of its own; else Follow screen), and Clear all clears them
20 s later. Each sample also counts the app's threads, which should level off too (a reader that's never
closed would show as a climbing count).

--no-show M (M14): the default show is None (lights off), and global schedules load Central Church for M minutes,
then none for M minutes, over and over: each schedule's end counts down, fades the lights off and closes the show,
and the next one loads it again from no show ("When this show loads" sends Follow screen). Each sample asks the
remote whether a show is loaded; the summary counts the closes and loads.

--live N (M16): a client of the live connection (/api/ws), as Companion's CentraLight module keeps one, stays connected
all along: it reads every state CentraLight sends, answers its pings, and every N seconds sends a command over it
(Release the Console, Master at full, a Tap: things that change nothing else). Every minute a second client opens,
reads the state and closes, as a Companion restart would. The summary counts them all, and any reconnects.
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
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = f"CentraLight Soak {os.getpid()} Source"  # (the app follows any source whose name contains its one)

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


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def thread_count(pid: int) -> int:
    """How many threads the process has now (0 if it can't be told)."""
    snap = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # (TH32CS_SNAPTHREAD: every thread on the PC)
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return 0
    count = 0
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(THREADENTRY32)
        more = kernel32.Thread32First(snap, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                count += 1
            more = kernel32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return count


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
    from centralight.new_show import add_starter_inputs, create_show
    from centralight.show_io import load_show

    folder = shows / "Central Church"
    add_starter_inputs(create_show(folder))  # (a new show has no inputs: the soak's Follow screen is made here)
    path = folder / "inputs" / "follow-screen.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tracks"][0][0]["source"] = SOURCE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    load_show(folder)


def make_clip(path: Path, hue: float) -> None:
    """A 4-second test video (320x180, 10 fps) in one colour, slowly brightening."""
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}.part.mp4")
    writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (320, 180))
    for frame in range(40):
        r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 0.4 + 0.6 * frame / 39)
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        image[:] = (int(b * 255), int(g * 255), int(r * 255))
        writer.write(image)
    writer.release()
    os.replace(temp, path)  # (in one go, as a sync tool would put a new version)


def add_countdown_automation(show_folder: Path) -> None:
    """"mood" (a show variable) and "Mood fade" (mood above 0.5 → Fade to black; when it stops → Follow screen)."""
    path = show_folder / "show.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["globals"] = [g for g in data.get("globals", []) if g.get("name") != "mood"] + [
        {"name": "mood", "type": "number", "default": 0.0, "min": 0.0, "max": 1.0}]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    automation = {"format": "centralight-automation", "version": 1, "id": "mood-fade", "name": "Mood fade", "logic": "all",
                  "conditions": [{"type": "variable", "name": "mood", "compare": ">", "value": 0.5}],
                  "actions": [{"do": "fade_black", "fade": 1.0}],
                  "on_end": [{"do": "base", "input": "follow-screen", "transition": "cut"}]}
    (show_folder / "automations").mkdir(exist_ok=True)
    (show_folder / "automations" / "mood-fade.json").write_text(json.dumps(automation, indent=2), encoding="utf-8")


def add_no_show_schedules(shows: Path, minutes: float, total_minutes: float) -> int:
    """--no-show: global schedules ("Soak 000", ...) that load Central Church for `minutes`, then leave `minutes` with
    none, from two minutes from now until the soak ends; and the show's "Start up" (When this show loads → Follow
    screen), so each load lights things again. Returns how many schedules there are."""
    step = max(1, int(round(minutes)))
    folder = shows / "_Global automations"  # (each global automation is <id>.json, right in it)
    folder.mkdir(parents=True, exist_ok=True)
    at = (datetime.now() + timedelta(minutes=2)).replace(second=0, microsecond=0)
    until = datetime.now() + timedelta(minutes=total_minutes + 1)
    count = 0
    while at < until:
        end = at + timedelta(minutes=step)
        data = {"format": "centralight-automation", "version": 1, "id": f"soak-{count:03d}", "name": f"Soak {count:03d}",
                "logic": "all", "conditions": [{"type": "time", "from": f"{at:%H:%M}", "to": f"{end:%H:%M}"}],
                "actions": [{"do": "load_show", "show": "central-church"}]}
        (folder / f"soak-{count:03d}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        at = end + timedelta(minutes=step)
        count += 1
    show_folder = shows / "Central Church"
    start_up = {"format": "centralight-automation", "version": 1, "id": "start-up", "name": "Start up", "logic": "all",
                "conditions": [{"type": "show_loads"}],
                "actions": [{"do": "base", "input": "follow-screen", "transition": "cut"}]}
    (show_folder / "automations").mkdir(exist_ok=True)
    (show_folder / "automations" / "start-up.json").write_text(json.dumps(start_up, indent=2), encoding="utf-8")
    return count


def remote_state(port: int) -> dict:
    """The app's /api/state ({} if it didn't answer)."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as reply:
            data = json.loads(reply.read().decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def add_linked_video(shows: Path, original: Path, appdata: Path) -> None:
    """Each show gets "Linked video" (a Video element linked to the original); the original is allowed, as if someone
    at the PC had chosen it."""
    from centralight.links import AllowedLinks

    for folder in shows.iterdir():
        if not (folder / "show.json").is_file():
            continue
        data = {"format": "centralight-input", "version": 1, "id": "linked-video", "name": "Linked video",
                "tracks": [[{"id": "E1", "kind": "video", "file": "", "original": str(original)}], [], [], []]}
        (folder / "inputs" / "linked-video.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    AllowedLinks(appdata / "allowed_links.json").allow([str(original)])


def make_second_show(shows: Path) -> None:
    """"Evening": a copy of the soak's show, to switch to and fro."""
    from centralight.shows import duplicate_show

    duplicate_show(shows / "Central Church", shows, "Evening")


def free_port() -> int:
    """A port nothing is using: Windows hands out a different one each time, so soaks started together never share
    one (and it's never the real remote's)."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def remote_get(port: int, path: str) -> bool:
    """Ask the app's remote for something (as a Stream Deck key does). True if it said ok."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as reply:
            return bool(json.loads(reply.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


class LiveClient(threading.Thread):
    """--live (M16): a Companion-like client of the live connection for the whole soak (see the top)."""

    COMMANDS = ({"path": "live/release"}, {"path": "master", "params": {"level": "1"}}, {"path": "live/tap"})

    def __init__(self, port: int, every: float) -> None:
        super().__init__(daemon=True, name="soak-live")
        self.port, self.every = port, every
        self.states = self.sent = self.answered = self.refused = self.connects = self.churns = 0
        self.problems: list[str] = []
        self._stopping = threading.Event()

    def stop(self) -> None:
        self._stopping.set()
        self.join(5.0)

    def _open(self):
        import base64
        import socket

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET /api/ws HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            data = sock.recv(4096)
            if not data:
                raise ConnectionError("closed during the handshake")
            head += data
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode("latin-1"))
        return sock, head.partition(b"\r\n\r\n")[2]

    @staticmethod
    def _send(sock, opcode: int, payload: bytes) -> None:
        import struct

        mask = os.urandom(4)
        size = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | size]) if size < 126 else bytes([0x80 | 126]) + struct.pack("!H", size))
        sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    @staticmethod
    def _frames(buffer: bytes):
        """The whole frames in buffer: ([(opcode, payload)], what's left)."""
        import struct

        frames = []
        while len(buffer) >= 2:
            size, at = buffer[1] & 0x7F, 2
            if size == 126:
                if len(buffer) < 4:
                    break
                size, at = struct.unpack("!H", buffer[2:4])[0], 4
            elif size == 127:
                if len(buffer) < 10:
                    break
                size, at = struct.unpack("!Q", buffer[2:10])[0], 10
            if len(buffer) < at + size:
                break
            frames.append((buffer[0] & 0x0F, buffer[at:at + size]))
            buffer = buffer[at + size:]
        return frames, buffer

    def _churn(self) -> None:
        """A second client: opens, reads the first state, closes."""
        sock, buffer = self._open()
        try:
            sock.settimeout(5)
            while True:
                frames, buffer = self._frames(buffer)
                if any(op == 0x1 and json.loads(data).get("type") == "state" for op, data in frames):
                    break
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError("closed before the state came")
                buffer += data
            self._send(sock, 0x8, (1000).to_bytes(2, "big"))
            self.churns += 1
        finally:
            sock.close()

    def run(self) -> None:
        import select

        last_command = last_churn = time.monotonic()
        number = 0
        while not self._stopping.is_set():
            try:
                sock, buffer = self._open()
            except (OSError, ConnectionError) as exc:
                self.problems.append(f"connecting: {exc}")
                self._stopping.wait(2.0)
                continue
            self.connects += 1
            try:
                while not self._stopping.is_set():
                    ready, _, _ = select.select([sock], [], [], 0.5)
                    if ready:
                        data = sock.recv(65536)
                        if not data:
                            raise ConnectionError("CentraLight closed the live connection")
                        buffer += data
                    frames, buffer = self._frames(buffer)
                    for opcode, payload in frames:
                        if opcode == 0x9:
                            self._send(sock, 0xA, payload)  # (its ping: pong)
                        elif opcode == 0x8:
                            raise ConnectionError(f"CentraLight closed it ({payload[:2].hex()})")
                        elif opcode == 0x1:
                            message = json.loads(payload)
                            if message.get("type") == "state":
                                self.states += 1
                            elif message.get("type") == "result":
                                self.answered += bool(message.get("ok"))
                                self.refused += not message.get("ok")
                    now = time.monotonic()
                    if now - last_command >= self.every:
                        last_command = now
                        command = self.COMMANDS[number % len(self.COMMANDS)]
                        number += 1
                        self._send(sock, 0x1, json.dumps({"type": "command", "id": number, "command": command}).encode())
                        self.sent += 1
                    if now - last_churn >= 60.0:
                        last_churn = now
                        try:
                            self._churn()
                        except (OSError, ConnectionError, ValueError) as exc:
                            self.problems.append(f"second client: {exc}")
            except (OSError, ConnectionError, ValueError) as exc:
                if not self._stopping.is_set():
                    self.problems.append(str(exc))
            finally:
                sock.close()


def use_soak_folders(home: Path) -> None:
    """This process uses the soak's own app data and shows too, not only the app it starts: what it does with
    CentraLight's code here (making the shows, allowing the linked video) logs to the soak's session.log, never the
    real one in %LOCALAPPDATA%\\CentraLight (M14)."""
    os.environ["CENTRALIGHT_APPDATA"] = str(home / "appdata")
    os.environ["CENTRALIGHT_SHOWS"] = str(home / "shows")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=1.0)
    p.add_argument("--every", type=float, default=10.0, help="seconds between samples")
    p.add_argument("--size", default="960x540")
    p.add_argument("--csv", type=Path, help="also write the samples here")
    p.add_argument("--switch-shows", type=float, default=0.0, help="load the other show every this many seconds (0: never)")
    p.add_argument("--linked-video", type=float, default=0.0,
                   help="a video linked to an original, replaced every this many minutes (0: none)")
    p.add_argument("--countdown", type=float, default=0.0,
                   help="an automation counts down and fades every this many minutes (0: never)")
    p.add_argument("--overlay", type=float, default=0.0,
                   help="two copies of an input go on the Overlay every this many seconds, cleared 20 s later (0: never)")
    p.add_argument("--no-show", type=float, default=0.0,
                   help="the default show is None; schedules load the show for this many minutes, then none (0: never)")
    p.add_argument("--live", type=float, default=0.0,
                   help="a live connection (Companion's) all along, sending a command every this many seconds (0: none)")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    width, height = (int(v) for v in args.size.split("x"))
    home = Path(tempfile.mkdtemp(prefix="centralight-soak-"))
    use_soak_folders(home)  # (first: everything below writes to the soak's own session log, never the real one)
    make_show(home / "shows")
    if args.countdown > 0:
        add_countdown_automation(home / "shows" / "Central Church")  # (before the second show is copied from it)
    schedules = add_no_show_schedules(home / "shows", args.no_show, args.minutes) if args.no_show > 0 else 0
    port = free_port()  # (its own remote port)
    (home / "appdata").mkdir(parents=True, exist_ok=True)
    settings = {"output": "simulated", "remote_port": port, **({"default_show": "_none"} if args.no_show > 0 else {})}
    (home / "appdata" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    if args.switch_shows > 0:
        make_second_show(home / "shows")
    original = home / "originals" / "Linked clip.mp4"
    hue, replaced = 0.0, 0
    if args.linked_video > 0:
        make_clip(original, hue)
        add_linked_video(home / "shows", original, home / "appdata")
    switches, failed, next_show = 0, 0, "evening"
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
        print(f"Watching CentraLight (pid {pid}) for {args.minutes:g} min; source {width}x{height} at 24 fps; {cores} cores"
              + (f"; switching shows every {args.switch_shows:g} s" if args.switch_shows > 0 else ""))
        last_switch = started
        last_replace = started
        last_mood, mood_high, cycles = started, False, 0
        last_overlay, clear_at, overlay_sends, overlay_clears = started, 0.0, 0, 0
        threads_seen: list[int] = []
        no_show_samples, show_samples, last_no_show, changes = 0, 0, None, 0
        if args.linked_video > 0:
            time.sleep(3.0)
            remote_get(port, "/api/input/linked-video")  # (on the lights: its updates wait until it isn't)
        live = LiveClient(port, args.live) if args.live > 0 else None
        if live is not None:
            live.start()
        while time.monotonic() - started < args.minutes * 60:
            time.sleep(args.every)
            if app.poll() is not None:
                print(f"The app stopped by itself (exit code {app.returncode})!")
                return 1
            while args.switch_shows > 0 and time.monotonic() - last_switch >= args.switch_shows:
                last_switch += args.switch_shows
                if remote_get(port, f"/api/show/{next_show}"):
                    time.sleep(1.0)  # (the window loads it)
                    linked = args.linked_video > 0 and next_show == "central-church"
                    remote_get(port, "/api/input/linked-video" if linked else "/api/input/follow-screen")
                    switches += 1
                    next_show = "central-church" if next_show == "evening" else "evening"
                else:
                    failed += 1
            if args.countdown > 0:
                if not mood_high and time.monotonic() - last_mood >= args.countdown * 60:
                    mood_high, last_mood = remote_get(port, "/api/var/mood?value=0.9"), time.monotonic()
                    cycles += 1
                elif mood_high and time.monotonic() - last_mood >= 40.0:
                    remote_get(port, "/api/var/mood?value=0.1")
                    mood_high, last_mood = False, time.monotonic()
            if clear_at and time.monotonic() >= clear_at:  # (first: a send due now mustn't put it off)
                overlay_clears += remote_get(port, "/api/overlay/clear")
                clear_at = 0.0
            if args.overlay > 0 and time.monotonic() - last_overlay >= args.overlay:
                last_overlay = time.monotonic()
                target = "linked-video" if args.linked_video > 0 else "follow-screen"
                for copy in range(2):  # (two copies at different moments of it)
                    if copy:
                        time.sleep(1.0)
                    overlay_sends += remote_get(port, f"/api/input/{target}/overlay")
                clear_at = time.monotonic() + 20.0
            if args.linked_video > 0 and time.monotonic() - last_replace >= args.linked_video * 60:
                last_replace = time.monotonic()
                hue += 0.37
                make_clip(original, hue)  # a new version of the original, while CentraLight may be playing its copy
                replaced += 1
            if args.no_show > 0:
                state = remote_state(port)
                if state:
                    now_no_show = bool(state.get("no_show"))
                    no_show_samples += now_no_show
                    show_samples += not now_no_show
                    changes += last_no_show is not None and now_no_show != last_no_show
                    last_no_show = now_no_show
            cpu, private, working = sample(pid)
            threads = thread_count(pid)
            threads_seen.append(threads)
            now = time.monotonic()
            share = (cpu - last_cpu) / (now - last_t) * 100
            rows.append((round(now - started), round(share, 2), round(share / cores, 3), private // 1024, working // 1024,
                         threads))
            last_cpu, last_t = cpu, now
            print(f"  {rows[-1][0]:>6} s  CPU {share:5.1f} % of one core ({share / cores:.2f} % of the PC)  "
                  f"private {private / 1048576:6.1f} MB  working set {working / 1048576:6.1f} MB  "
                  f"threads {threads}", flush=True)  # (as Task Manager counts MB)
        log = (home / "appdata" / "session.log")
        if live is not None:
            live.stop()
            print(f"Live connection: connected {live.connects} times, {live.states} states received, {live.sent} commands "
                  f"sent ({live.answered} answered ok, {live.refused} refused), {live.churns} second clients"
                  + (f"; problems: {live.problems[:5]}" if live.problems else ""))
        if args.csv:
            with args.csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["seconds", "cpu_one_core_pct", "cpu_pc_pct", "private_kb", "working_set_kb", "threads"])
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
        if threads_seen:
            print(f"Threads: {threads_seen[0]} at the start, {threads_seen[-1]} at the end, most {max(threads_seen)}")
        if args.switch_shows > 0:
            print(f"Shows loaded: {switches} times ({failed} asks the remote didn't answer)")
        if args.overlay > 0:
            print(f"Overlay: {overlay_sends} copies sent, cleared {overlay_clears} times")
        if log.exists() and args.countdown > 0:
            text = log.read_text(encoding="utf-8")
            print(f"Countdown: mood set high {cycles} times; {text.count('for its countdown')} runs waited for their "
                  f"countdown, {text.count('Countdowns dropped')} drops")
        if log.exists() and args.no_show > 0:
            text = log.read_text(encoding="utf-8")
            print(f"No show: {schedules} schedules; the show closed {text.count('the show closed (no show loaded')} times, "
                  f"loaded again {text.count(': show “Central Church” loaded')} times; {text.count('No show loaded: every light is off')} "
                  f"no-show loads in the engine; samples {no_show_samples} with no show, {show_samples} with one, "
                  f"{changes} changes seen")
        if log.exists() and args.linked_video > 0:
            text = log.read_text(encoding="utf-8")
            print(f"Linked video: the original was replaced {replaced} times; {text.count('copied in as')} copies made, "
                  f"{text.count('the new version is used now')} swapped in, {text.count('Tidied')} tidies")
        if log.exists():
            text = log.read_text(encoding="utf-8")
            problems = [line for line in text.splitlines() if "Traceback" in line or "Error" in line]
            print(f"Session log: {len(text.splitlines())} lines, {len(problems)} lines mentioning errors")
            for line in problems[:5]:
                print(f"    {line[:200]}")
        return 0
    finally:
        app.kill()
        app.wait(10)
        sender.stop()


if __name__ == "__main__":
    sys.exit(main())
