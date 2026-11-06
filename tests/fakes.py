"""Fakes for tests: a clock you move by hand, a dongle you can unplug, and an output
that just records frames. Nothing here touches real hardware."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import serial

from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import Show
from centralight.new_show import show_from_patch
from centralight.output import CONNECTED, OutputStatus


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


class FakeDevice:
    """One opened 'dongle'. Raises like pyserial once its FakePort is unplugged."""

    def __init__(self, port: FakePort) -> None:
        self.port_obj = port
        self.port = port.name
        self.sent: list[bytes] = []
        self.closed = False
        self.closed_with_blackout: bool | None = None
        self.dead = False

    def send(self, dmx: bytes) -> None:
        if self.dead or self.closed:
            raise serial.SerialException("WriteFile failed (PermissionError(13, 'The device does not recognize the command.', None, 22))")
        with self.port_obj.lock:
            self.sent.append(bytes(dmx))

    def close(self, blackout: bool = True) -> None:
        if blackout and not self.dead:
            with self.port_obj.lock:
                self.sent.append(bytes(512))
        self.closed = True
        self.closed_with_blackout = blackout


class FakePort:
    """A pretend Eurolite: plug, unplug, or let 'another program' hold it."""

    def __init__(self, name: str = "COM9") -> None:
        self.name = name
        self.plugged = True
        self.busy = False
        self.devices: list[FakeDevice] = []
        self.lock = threading.Lock()

    def factory(self) -> FakeDevice:
        if self.busy:
            raise ConnectionError(
                f"Could not open {self.name}: could not open port '{self.name}': "
                "PermissionError(13, 'Access is denied.', None, 5). Close MagicQ, QLC+ or any other "
                "script using the dongle, then try again."
            )
        if not self.plugged:
            raise ConnectionError("No FTDI (0403:6001) COM port found. Check the USB cable.")
        device = FakeDevice(self)
        self.devices.append(device)
        return device

    def unplug(self) -> None:
        self.plugged = False
        for device in self.devices:
            device.dead = True

    def replug(self) -> None:
        self.plugged = True

    def all_sent(self) -> list[bytes]:
        with self.lock:
            return [frame for device in self.devices for frame in device.sent]


@dataclass
class FakeOutput:
    """Stands in for DmxOutputThread in engine tests: records every submitted frame."""

    frames: list[bytes] = field(default_factory=list)
    status_value: OutputStatus = field(default_factory=lambda: OutputStatus(kind="simulated", state=CONNECTED, label="Simulated"))

    closed_with_blackout: bool | None = None
    specs: list = field(default_factory=list)

    def submit(self, frame: bytes) -> None:
        self.frames.append(bytes(frame))

    def status(self) -> OutputStatus:
        return self.status_value

    def close(self, blackout: bool = True, timeout: float = 3.0) -> None:
        self.closed_with_blackout = blackout

    def set_spec(self, spec) -> None:
        self.specs.append(spec)

    @property
    def last(self) -> bytes:
        return self.frames[-1]


class FakeSender:
    """A pretend NDI source (like Media Background Pane) for FakeNdi."""

    def __init__(self, name: str, size: tuple[int, int] = (960, 540), colour=(255, 0, 0), fps: float = 24.0, pattern=None) -> None:
        self.name = name
        self.size = size
        self.colour = colour
        self.fps = fps
        self.pattern = pattern  # optional: function(t) -> (h, w, 3) RGB uint8
        self.running = True  # on the network
        self.paused = False  # on the network but sending no pictures
        self.url = "127.0.0.1:5961"

    def image(self, t: float):
        import numpy as np

        if self.pattern is not None:
            return self.pattern(t)
        w, h = self.size
        image = np.empty((h, w, 3), dtype=np.uint8)
        image[:, :] = self.colour
        return image


class _FakeRecv:
    def __init__(self) -> None:
        self.name = ""
        self.destroyed = False
        self.next_due = 0.0
        self.settings = None


class FakeNdi:
    """Stands in for the NDIlib module: same functions, but pictures come from FakeSenders."""

    FRAME_TYPE_NONE = 0
    FRAME_TYPE_VIDEO = 1
    FRAME_TYPE_AUDIO = 2
    FRAME_TYPE_METADATA = 3
    FRAME_TYPE_ERROR = 4
    RECV_COLOR_FORMAT_BGRX_BGRA = 0
    RECV_BANDWIDTH_HIGHEST = 100

    class Source:
        def __init__(self, p_ndi_name=None, p_url_address=None) -> None:
            self.ndi_name = p_ndi_name
            self.url_address = p_url_address

    class RecvCreateV3:
        def __init__(self) -> None:
            self.color_format = None
            self.bandwidth = None
            self.ndi_recv_name = None
            self.source_to_connect_to = None
            self.allow_video_fields = True

    class VideoFrame:
        def __init__(self, data, xres: int, yres: int) -> None:
            self.data = data
            self.xres = xres
            self.yres = yres

    def __init__(self, *senders: FakeSender) -> None:
        self.senders = {s.name: s for s in senders}
        self.lock = threading.Lock()
        self.created = 0
        self.destroyed = 0
        self.captured = 0
        self.freed = 0
        self.settings: list = []

    def add(self, sender: FakeSender) -> None:
        self.senders[sender.name] = sender

    def initialize(self) -> bool:
        return True

    def find_create_v2(self, settings=None):
        return object()

    def find_wait_for_sources(self, finder, timeout_ms: int) -> bool:
        import time as _time

        _time.sleep(min(timeout_ms / 1000.0, 0.02))
        return True

    def find_get_current_sources(self, finder):
        return [self.Source(s.name, s.url) for s in list(self.senders.values()) if s.running]

    def find_destroy(self, finder) -> None:
        return None

    def recv_create_v3(self, settings):
        recv = _FakeRecv()
        recv.settings = settings
        self.settings.append(settings)
        with self.lock:
            self.created += 1
        return recv

    def recv_connect(self, recv, source) -> None:
        recv.name = source.ndi_name

    def recv_capture_v2(self, recv, timeout_ms, want_video=True, want_audio=True, want_metadata=True):
        import time as _time

        import numpy as np

        sender = self.senders.get(recv.name)
        if sender is None or not sender.running or sender.paused or recv.destroyed:
            _time.sleep(min(timeout_ms / 1000.0, 0.02))
            return (self.FRAME_TYPE_NONE, None, None, None)
        now = _time.monotonic()
        wait = recv.next_due - now
        if wait > 0:
            _time.sleep(min(wait, timeout_ms / 1000.0))
            if _time.monotonic() < recv.next_due:
                return (self.FRAME_TYPE_NONE, None, None, None)
        recv.next_due = max(now, recv.next_due) + 1.0 / sender.fps
        rgb = sender.image(now)
        h, w = rgb.shape[:2]
        bgrx = np.empty((h, w, 4), dtype=np.uint8)
        bgrx[:, :, :3] = rgb[:, :, ::-1]
        bgrx[:, :, 3] = 255
        with self.lock:
            self.captured += 1
        return (self.FRAME_TYPE_VIDEO, self.VideoFrame(bgrx, w, h), None, None)

    def recv_free_video_v2(self, recv, frame) -> None:
        with self.lock:
            self.freed += 1

    def recv_get_no_connections(self, recv) -> int:
        sender = self.senders.get(recv.name)
        return 1 if sender is not None and sender.running and not recv.destroyed else 0

    def recv_destroy(self, recv) -> None:
        recv.destroyed = True
        with self.lock:
            self.destroyed += 1


class StubSources:
    """Stands in for SourceManager in engine tests: pictures are put in by hand."""

    def __init__(self) -> None:
        self.frames: dict = {}
        self.keep: set[str] = set()
        self.seq = 0

    def put(self, name: str, image) -> None:
        from centralight.ndi_source import SourceFrame

        self.seq += 1
        h, w = image.shape[:2]
        self.frames[name.lower()] = SourceFrame(image, (w, h), 0.0, self.seq)

    def drop(self, name: str) -> None:
        self.frames.pop(name.lower(), None)

    def frame(self, name: str):
        return self.frames.get(name.strip().lower())

    def set_keep(self, names) -> None:
        self.keep = {n.lower() for n in names}

    def statuses(self) -> dict:
        return {}

    def tidy(self) -> None:
        return None


class FakeEngine(Engine):
    """A real Engine with a hand-moved clock: every submit() is applied at once.

    The window tests use it so clicks and drags change the frames straight away,
    without an engine thread.
    """

    def __init__(self, show: Show | None = None, live: LiveState | None = None, output: FakeOutput | None = None,
                 global_automations: Show | None = None, pc_start: bool = False) -> None:
        """pc_start: the PC has just started (the lights start black). Otherwise Windows has been up a day."""
        self.clock = FakeClock()
        self.fake_output = output or FakeOutput()
        super().__init__(show or show_from_patch(), self.fake_output, live, clock=self.clock,
                         uptime=(lambda: 60.0) if pc_start else (lambda: 86400.0), global_automations=global_automations)
        self.tick()

    def submit(self, command) -> None:  # type: ignore[override]
        super().submit(command)
        self.tick()

    def settle(self, seconds: float = 1.0, step: float = 0.025) -> None:
        """Move time on (finishing any fades) and tick along the way."""
        steps = max(1, int(round(seconds / step)))
        for _ in range(steps):
            self.clock.advance(step)
            self.tick()

    def start(self) -> None:  # no threads in window tests
        return

    def stop(self, lights_off: bool = False, fade: bool = True, hold_off: bool = True) -> LiveState:  # type: ignore[override]
        if lights_off:
            from centralight.commands import ToBlack

            Engine.submit(self, ToBlack(0.0, everything=True))
            self.tick()
        return Engine.stop(self, lights_off=lights_off, fade=False, hold_off=hold_off)
