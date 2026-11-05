"""DMX output: light colours -> DMX bytes, and the thread that sends them.

encode_dmx() builds the 512-channel universe with fixtures.PROFILES, exactly like
fixtures.set_fixture (RGB, with a SlimPAR's own dimmer channel at full).

DmxOutputThread sends it on its own thread ("dmx-out"):
- only when something changed, plus once a second as a keepalive
- trimmed to the last channel a light uses (76), so a USB packet takes ~3.6 ms
- if the dongle is unplugged or another program has it, it says so and retries
  every 2 seconds, then sends the current frame as soon as it's back

Where it sends is a DeviceSpec: the Eurolite (USB), Art-Net, or Simulated, which
opens nothing and just remembers what it would have sent.
"""

from __future__ import annotations

import contextlib
import io
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from fixtures import PROFILES

from .log import log_exception, log_info
from .model import ROLE_COLOUR, Fixture
from .settings import OUTPUT_ARTNET, OUTPUT_SIMULATED, OUTPUT_USB, Settings

DMX_CHANNELS = 512
MIN_CHANNELS = 24  # the Enttec Pro protocol needs at least 24 channels per packet
KEEPALIVE_SECONDS = 1.0
RETRY_SECONDS = 2.0

# Output states, as shown in the header chip
CONNECTED = "connected"
CONNECTING = "connecting"
BUSY = "busy"
MISSING = "missing"
ERROR = "error"
CLOSED = "closed"

RGB = tuple[float, float, float]


class Device(Protocol):
    def send(self, dmx: bytes) -> None: ...

    def close(self, blackout: bool = True) -> None: ...


def to_byte(value: float) -> int:
    """0-1 -> 0-255, rounded (so a 0-255 value survives the round trip exactly). NaN -> 0."""
    if value >= 1.0:
        return 255
    if not value > 0.0:
        return 0
    return int(value * 255.0 + 0.5)


def universe_size(fixtures: Iterable[Fixture]) -> int:
    """How many channels to send: up to the last channel any light uses (at least 24)."""
    last = max((fx.last_channel for fx in fixtures), default=0)
    return max(MIN_CHANNELS, min(DMX_CHANNELS, last))


def show_channels(show) -> int:
    """How many channels a show needs sent: up to its last light's (universe_size), or all 512 when no show is loaded
    (M14: every channel at 0, so no light keeps a colour it was sent before, by this show or an earlier one)."""
    return DMX_CHANNELS if getattr(show, "no_show", False) else universe_size(show.fixtures)


def encode_dmx(
    fixtures: Iterable[Fixture],
    colours: Mapping[str, RGB],
    levels: Mapping[str, float],
    size: int = DMX_CHANNELS,
) -> bytes:
    """Build the DMX universe.

    colours: fixture id -> (r, g, b) from 0 to 1, for colour lights
    levels: fixture id -> 0 to 1, for house lights (dimmers)
    Lights not mentioned are sent as 0 (black / off).
    """
    dmx = bytearray(size)
    for fx in fixtures:
        prof = PROFILES[fx.profile]
        base = fx.address - 1
        if base + prof["channels"] > size:
            continue
        if fx.role == ROLE_COLOUR:
            r, g, b = colours.get(fx.id, (0.0, 0.0, 0.0))
            if "red" in prof:
                dmx[base + prof["red"]] = to_byte(r)
                dmx[base + prof["green"]] = to_byte(g)
                dmx[base + prof["blue"]] = to_byte(b)
                dmx[base + prof["dimmer"]] = 255
            else:  # a plain dimmer used as a colour light: brightest of the three (like set_fixture)
                dmx[base + prof["dimmer"]] = to_byte(max(r, g, b))
        else:
            level = levels.get(fx.id, 0.0)
            if "red" in prof:  # an RGB light used as a house light: white at that level
                for part in ("red", "green", "blue"):
                    dmx[base + prof[part]] = to_byte(level)
                dmx[base + prof["dimmer"]] = 255
            else:
                dmx[base + prof["dimmer"]] = to_byte(level)
    return bytes(dmx)


class SimulatedDevice:
    """Opens nothing. Remembers the last frame so the app can show what it would send."""

    port = "Simulated"

    def __init__(self) -> None:
        self.last: bytes | None = None
        self.count = 0

    def send(self, dmx: bytes) -> None:
        self.last = bytes(dmx)
        self.count += 1

    def close(self, blackout: bool = True) -> None:
        if blackout and self.last is not None:
            self.last = bytes(len(self.last))


@dataclass(frozen=True)
class DeviceSpec:
    """Which output to use and how to open it."""

    kind: str  # simulated | usb | artnet
    label: str  # what the header chip says before it connects
    factory: Callable[[], Device]
    trim: int | None = None  # channels per packet (None = all 512)


def _open_usb(port: str | None) -> Device:
    from dmx_output import UsbDmxOutput

    # UsbDmxOutput prints the COM port list; keep that out of the app's console while it retries
    with contextlib.redirect_stdout(io.StringIO()):
        return UsbDmxOutput(port)


def _open_artnet(ip: str, universe: int) -> Device:
    from dmx_output import ArtNetOutput

    return ArtNetOutput(ip, universe)


def device_spec(settings: Settings, size: int) -> DeviceSpec:
    """The DeviceSpec for the output chosen in Settings."""
    if settings.output == OUTPUT_USB:
        port = settings.usb_port or None
        return DeviceSpec(
            OUTPUT_USB,
            settings.usb_port or "USB",
            lambda: _open_usb(port),
            None if settings.full_universe else size,
        )
    if settings.output == OUTPUT_ARTNET:
        ip, universe = settings.artnet_ip, settings.artnet_universe
        # stupidArtnet only accepts the full 512 channels it was set up for
        return DeviceSpec(OUTPUT_ARTNET, ip, lambda: _open_artnet(ip, universe), None)
    return DeviceSpec(OUTPUT_SIMULATED, "Simulated", SimulatedDevice, size)


@dataclass(frozen=True)
class OutputStatus:
    kind: str = OUTPUT_SIMULATED
    state: str = CONNECTING
    label: str = ""
    rate: float = 0.0  # packets sent in the last second
    sent: int = 0  # packets sent since the app started
    message: str = ""  # what's wrong, in plain words

    @property
    def ok(self) -> bool:
        return self.state == CONNECTED


def classify_open_error(exc: BaseException, label: str) -> tuple[str, str]:
    """(state, message for people) for a failure to open the output."""
    text = str(exc)
    if "No FTDI" in text:
        return MISSING, "Eurolite not found. Check its USB cable; CentraLight keeps looking."
    if "PermissionError" in text or "Access is denied" in text:
        return BUSY, (
            f"{label} is being used by another program (MagicQ, QLC+ or a console script). "
            "Close it and CentraLight will connect."
        )
    if "FileNotFoundError" in text or "could not open port" in text or "cannot find" in text.lower():
        return MISSING, f"{label} isn't there. Check the Eurolite's USB cable, or the COM port in Settings."
    return ERROR, text or type(exc).__name__


class DmxOutputThread:
    def __init__(
        self,
        spec: DeviceSpec,
        *,
        keepalive: float = KEEPALIVE_SECONDS,
        retry: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        name: str = "dmx-out",
    ) -> None:
        self._spec = spec
        self._next_spec: DeviceSpec | None = None
        self._keepalive = keepalive
        self._retry = retry
        self._clock = clock
        self._cond = threading.Condition()
        self._frame: bytes | None = None
        self._frame_version = 0
        self._seen_version = 0
        self._closing = False
        self._close_blackout = True
        self._reconnect = False
        self._device: Device | None = None
        self._last_sent: bytes | None = None
        self._next_keepalive = 0.0
        self._next_retry = 0.0
        self._sent_times: deque[float] = deque(maxlen=400)
        self._sent_total = 0
        self._status = OutputStatus(kind=spec.kind, state=CONNECTING, label=spec.label)
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    # ------------------------------------------------------------ used by other threads

    def start(self) -> None:
        self._thread.start()

    def submit(self, frame: bytes) -> None:
        """Hand over the newest universe (512 bytes). Only the newest one is ever sent."""
        with self._cond:
            if frame == self._frame:
                return
            self._frame = bytes(frame)
            self._frame_version += 1
            self._cond.notify_all()

    def set_spec(self, spec: DeviceSpec) -> None:
        """Switch to another output. The old one is blacked out and closed first."""
        with self._cond:
            self._next_spec = spec
            self._cond.notify_all()

    def set_trim(self, size: int) -> None:
        """The show's lights now need `size` channels (a light was added, or moved up): send that many from now on,
        straight away and without reopening the port. It only grows: a light removed from the show keeps being sent
        zeros (rather than holding its last colour because nothing reaches it any more). Outputs that always send
        all 512 aren't affected."""
        with self._cond:
            changed = False
            for name in ("_spec", "_next_spec"):
                spec = getattr(self, name)
                if spec is not None and spec.trim is not None and size > spec.trim:
                    setattr(self, name, replace(spec, trim=max(1, int(size))))
                    changed = True
            if changed:
                self._last_sent = None  # send the current frame again, at the new length
                self._frame_version += 1
                self._cond.notify_all()

    def reconnect(self) -> None:
        """After the PC wakes: close the port (leaving the lights as they are) and open it again at once, in case
        Windows gave the dongle a new handle while it slept."""
        with self._cond:
            self._reconnect = True
            self._cond.notify_all()

    def status(self) -> OutputStatus:
        now = self._clock()
        with self._cond:
            while self._sent_times and now - self._sent_times[0] > 1.0:
                self._sent_times.popleft()
            return replace(self._status, rate=float(len(self._sent_times)), sent=self._sent_total)

    def last_sent(self) -> bytes | None:
        with self._cond:
            return self._last_sent

    def close(self, blackout: bool = True, timeout: float = 3.0) -> None:
        """Stop the thread. blackout=False leaves the lights as they are."""
        with self._cond:
            self._closing = True
            self._close_blackout = blackout
            self._cond.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout)
        elif self._device is not None:  # never started: close here
            self._finish(self._frame, blackout)

    # ------------------------------------------------------------ the dmx-out thread

    def _run(self) -> None:
        while True:
            with self._cond:
                if not (self._closing or self._next_spec is not None or self._reconnect
                        or self._frame_version != self._seen_version):
                    self._cond.wait(self._wait_time())
                closing = self._closing
                blackout = self._close_blackout
                spec = self._next_spec
                self._next_spec = None
                reconnect, self._reconnect = self._reconnect, False
                frame = self._frame
                self._seen_version = self._frame_version
            try:
                if closing:
                    self._finish(frame, blackout)
                    return
                if spec is not None:
                    self._switch(spec)
                elif reconnect and self._device is not None:
                    self._reopen()
                if self._device is None and self._clock() >= self._next_retry:
                    self._open()
                if self._device is not None and frame is not None:
                    now = self._clock()
                    if frame != self._last_sent or now >= self._next_keepalive:
                        self._send(frame)
            except Exception as exc:  # never let the output thread die
                log_exception("dmx-out", exc)
                time.sleep(0.1)

    def _wait_time(self) -> float:
        """How long to sleep when there's nothing new (called with the lock held)."""
        now = self._clock()
        if self._device is None:
            due = self._next_retry
        elif self._frame is None:
            return 1.0  # nothing to send yet; submit() wakes us
        else:
            due = self._next_keepalive
        return min(1.0, max(0.005, due - now))

    def _set_status(self, state: str, message: str = "", label: str | None = None) -> None:
        with self._cond:
            self._status = replace(
                self._status,
                kind=self._spec.kind,
                state=state,
                message=message,
                label=label if label is not None else self._status.label,
            )

    def _open(self) -> None:
        spec = self._spec
        try:
            device = spec.factory()
        except Exception as exc:
            state, message = classify_open_error(exc, spec.label)
            self._next_retry = self._clock() + self._retry
            if self._status.state != state:
                log_info(f"Output {spec.kind} {spec.label}: {message}")
            self._set_status(state, message)
            return
        self._device = device
        self._last_sent = None  # send the current frame straight away
        label = getattr(device, "port", None) or spec.label
        log_info(f"Output connected: {spec.kind} {label}")
        self._set_status(CONNECTED, "", label=str(label))

    def _send(self, frame: bytes) -> None:
        device = self._device
        if device is None:
            return
        trim = self._spec.trim
        try:
            device.send(frame[:trim] if trim else frame)
        except Exception as exc:
            self._lost(exc)
            return
        now = self._clock()
        with self._cond:
            self._last_sent = frame
            self._sent_times.append(now)
            self._sent_total += 1
        self._next_keepalive = now + self._keepalive

    def _lost(self, exc: BaseException) -> None:
        device, self._device = self._device, None
        try:
            if device is not None:
                device.close(blackout=False)
        except Exception:
            pass
        self._next_retry = self._clock() + self._retry
        message = f"Lost the connection ({exc}). Retrying every {self._retry:g} s."
        log_info(f"Output {self._spec.kind} {self._status.label}: {message}")
        self._set_status(ERROR, message)

    def _reopen(self) -> None:
        device, self._device = self._device, None
        try:
            device.close(blackout=False)  # the dongle keeps showing the last look meanwhile
        except Exception as exc:
            log_exception("dmx-out close (reconnecting)", exc)
        self._next_retry = 0.0
        log_info(f"Output {self._spec.kind} {self._status.label}: reconnecting after the PC woke")

    def _switch(self, spec: DeviceSpec) -> None:
        device, self._device = self._device, None
        if device is not None:
            try:
                device.close(blackout=True)  # never leave the old output's lights stuck on
            except Exception as exc:
                log_exception("dmx-out close", exc)
        self._spec = spec
        self._last_sent = None
        self._next_retry = 0.0
        with self._cond:
            self._status = OutputStatus(kind=spec.kind, state=CONNECTING, label=spec.label)

    def _finish(self, frame: bytes | None, blackout: bool) -> None:
        device, self._device = self._device, None
        if device is None:
            self._set_status(CLOSED)
            return
        try:
            if not blackout and frame is not None and frame != self._last_sent:
                trim = self._spec.trim
                device.send(frame[:trim] if trim else frame)  # leave the newest look on
                self._last_sent = frame
            device.close(blackout=blackout)
        except Exception as exc:
            log_exception("dmx-out close", exc)
        self._set_status(CLOSED)
