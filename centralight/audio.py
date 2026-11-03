"""Sound for expressions: audio.level, audio.bass, audio.mid, audio.treble, audio.beat and audio.pitch (all 0-1).

Where it comes from (Settings → Audio): off, "This PC's output" (whatever the PC is playing, captured with WASAPI
loopback, so nothing needs plugging in), or an input such as a mixer feed. Capture uses PyAudioWPatch (PortAudio
with loopback) in its own callback thread: 48 kHz, blocks of 1024 samples.

Analysis (Analyzer, plain numpy, so tests can feed it tones):
- level: loudness (RMS in dB, -60 to 0) with a slow automatic gain, so quiet and loud services both use the range;
- bass (20-250 Hz), mid (250 Hz-2 kHz), treble (2-8 kHz): each band's energy over its own slowly falling peak;
- beat: jumps to 1 when the bass suddenly rises above its recent average, then falls away;
- pitch: the spectrum's centre of mass on a log scale (80 Hz = 0, 8 kHz = 1).
Level, bands and pitch are smoothed: they rise over `attack` seconds and fall over `release` (Settings).

Nothing is recorded or kept: each block is analysed and dropped. When no sound arrives (a loopback with the PC
silent sends nothing), the values fall back to 0.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

from .log import log_exception, log_info

SOURCE_OFF = ""
SOURCE_OUTPUT = "output"  # this PC's output (loopback)
RATE = 48000
BLOCK = 1024
STALE = 0.5  # seconds without a block: the values fall back to 0
BANDS = {"bass": (20.0, 250.0), "mid": (250.0, 2000.0), "treble": (2000.0, 8000.0)}
NAMES = ("level", "bass", "mid", "treble", "beat", "pitch")
SILENT = {name: 0.0 for name in NAMES}


@dataclass(frozen=True)
class AudioValues:
    level: float = 0.0
    bass: float = 0.0
    mid: float = 0.0
    treble: float = 0.0
    beat: float = 0.0
    pitch: float = 0.0
    stamp: float = 0.0  # time.monotonic() of the block they came from

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in NAMES}


def _approach(current: float, target: float, dt: float, attack: float, release: float) -> float:
    """Move towards target: over `attack` seconds when rising, `release` when falling."""
    seconds = attack if target > current else release
    if seconds <= 1e-4:
        return target
    return current + (target - current) * (1.0 - math.exp(-dt / seconds))


class Analyzer:
    """Blocks of mono samples (floats, -1 to 1) in, AudioValues out."""

    PEAK_FALL = 8.0  # seconds for a band's peak (and the level's automatic gain) to fall by about two thirds
    BEAT_FALL = 0.12  # seconds for a beat to fade
    BEAT_GAP = 0.18  # at most one beat in this long
    BEAT_RISE = 1.5  # the bass must jump to this many times its recent average to count

    def __init__(self, rate: int = RATE, attack: float = 0.05, release: float = 0.3) -> None:
        self.rate = rate
        self.attack = attack
        self.release = release
        self._peaks = {name: 1e-6 for name in BANDS}
        self._total_peak = 1e-6  # all the bands together: a band with next to nothing in it stays near 0
        self._level_peak = 0.25
        self._bass_average = 0.0
        self._beat = 0.0
        self._since_beat = 1.0
        self._smooth = dict(SILENT)
        self._window: np.ndarray | None = None

    def feed(self, samples: np.ndarray, dt: float | None = None, stamp: float | None = None) -> AudioValues:
        samples = np.asarray(samples, dtype=np.float32).ravel()
        n = len(samples)
        if n == 0:
            return AudioValues(**self._smooth, stamp=stamp or time.monotonic())
        dt = n / self.rate if dt is None else dt
        if self._window is None or len(self._window) != n:
            self._window = np.hanning(n).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(samples * self._window)) ** 2
        freqs = np.fft.rfftfreq(n, 1.0 / self.rate)
        fall = math.exp(-dt / self.PEAK_FALL)
        targets: dict[str, float] = {}
        # loudness, with a slow automatic gain
        rms = float(np.sqrt(np.mean(samples * samples))) if n else 0.0
        db = 20.0 * math.log10(max(rms, 1e-6))
        loud = min(1.0, max(0.0, (db + 60.0) / 60.0))
        self._level_peak = max(loud, self._level_peak * fall, 0.25)
        targets["level"] = min(1.0, loud / self._level_peak)
        # bands
        energy = {name: float(spectrum[(freqs >= low) & (freqs < high)].sum()) for name, (low, high) in BANDS.items()}
        self._total_peak = max(sum(energy.values()), self._total_peak * fall, 1e-6)
        for name in BANDS:
            self._peaks[name] = max(energy[name], self._peaks[name] * fall, 1e-6)
            floor = self._total_peak * 0.01  # at least 1 % of everything: leakage from other bands doesn't count
            targets[name] = min(1.0, energy[name] / max(self._peaks[name], floor)) if loud > 0.05 else 0.0
        # beat: the bass jumping above its recent average
        self._since_beat += dt
        bass = targets["bass"]
        if (bass > 0.3 and bass > self.BEAT_RISE * self._bass_average + 0.05 and self._since_beat >= self.BEAT_GAP):
            self._beat = 1.0
            self._since_beat = 0.0
        else:
            self._beat *= math.exp(-dt / self.BEAT_FALL)
        self._bass_average += (bass - self._bass_average) * (1.0 - math.exp(-dt / 0.4))
        # pitch: the spectrum's centre of mass, on a log scale
        audible = (freqs >= 40.0) & (freqs <= 12000.0)
        total = float(spectrum[audible].sum())
        if total > 1e-9 and loud > 0.05:
            centroid = float((spectrum[audible] * freqs[audible]).sum() / total)
            targets["pitch"] = min(1.0, max(0.0, (math.log2(max(centroid, 1.0)) - math.log2(80.0)) / (math.log2(8000.0) - math.log2(80.0))))
        else:
            targets["pitch"] = self._smooth["pitch"]
        for name in ("level", "bass", "mid", "treble", "pitch"):
            self._smooth[name] = _approach(self._smooth[name], targets[name], dt, self.attack, self.release)
        self._smooth["beat"] = self._beat
        return AudioValues(**{k: round(v, 4) for k, v in self._smooth.items()}, stamp=stamp if stamp is not None else time.monotonic())


# ---------------------------------------------------------------- capture


def list_sources() -> list[tuple[str, str]]:
    """(value for settings, what to show): off, this PC's output, then the PC's inputs."""
    sources = [(SOURCE_OFF, "Off"), (SOURCE_OUTPUT, "This PC's output (what it's playing)")]
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return sources
    audio = None
    try:
        audio = pyaudio.PyAudio()
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        for i in range(wasapi.get("deviceCount", 0)):
            info = audio.get_device_info_by_host_api_device_index(wasapi["index"], i)
            if info.get("maxInputChannels", 0) > 0 and not info.get("isLoopbackDevice", False):
                sources.append((str(info["name"]), str(info["name"])))
    except Exception as exc:
        log_exception("listing audio inputs", exc)
    finally:
        if audio is not None:
            audio.terminate()
    return sources


class AudioCapture:
    """Listens to one source and keeps the newest AudioValues. latest() is safe from any thread."""

    def __init__(self, source: str = SOURCE_OFF, attack: float = 0.05, release: float = 0.3) -> None:
        self.source = source
        self.analyzer = Analyzer(RATE, attack, release)
        self._values: AudioValues | None = None
        self._lock = threading.Lock()
        self._audio = None
        self._stream = None
        self.state = "off"  # off, listening, error
        self.detail = ""
        self._channels = 1

    def set_smoothing(self, attack: float, release: float) -> None:
        self.analyzer.attack, self.analyzer.release = attack, release

    def start(self) -> None:
        if not self.source:
            self.state, self.detail = "off", ""
            return
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.state, self.detail = "error", "the audio library isn't installed (run run.bat once to add it)"
            return
        try:
            self._audio = pyaudio.PyAudio()
            device = self._device(pyaudio)
            if device is None:
                self.state, self.detail = "error", f"can't find {self.source!r}"
                self._close_library()
                return
            self._channels = max(1, min(2, int(device.get("maxInputChannels", 1))))
            rate = int(device.get("defaultSampleRate", RATE))
            self.analyzer.rate = rate
            self._stream = self._audio.open(format=pyaudio.paFloat32, channels=self._channels, rate=rate, input=True,
                                            frames_per_buffer=BLOCK, input_device_index=int(device["index"]),
                                            stream_callback=self._callback)
            self._stream.start_stream()
            self.state, self.detail = "listening", str(device.get("name", ""))
            log_info(f"Audio: listening to {self.detail}")
        except Exception as exc:
            log_exception("starting audio", exc)
            self.state, self.detail = "error", str(exc) or type(exc).__name__
            self.stop()

    def _device(self, pyaudio):
        audio = self._audio
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        if self.source == SOURCE_OUTPUT:
            speakers = audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if speakers.get("isLoopbackDevice"):
                return speakers
            for loopback in audio.get_loopback_device_info_generator():
                if speakers["name"] in loopback["name"]:
                    return loopback
            return None
        for i in range(wasapi.get("deviceCount", 0)):
            info = audio.get_device_info_by_host_api_device_index(wasapi["index"], i)
            if info.get("name") == self.source and info.get("maxInputChannels", 0) > 0:
                return info
        return None

    def _callback(self, data, frames, _time_info, _status):
        import pyaudiowpatch as pyaudio

        try:
            samples = np.frombuffer(data, dtype=np.float32)
            if self._channels > 1:
                samples = samples.reshape(-1, self._channels).mean(axis=1)
            values = self.analyzer.feed(samples)
            with self._lock:
                self._values = values
        except Exception as exc:  # never let the audio thread die
            log_exception("analysing audio", exc)
        return (None, pyaudio.paContinue)

    def latest(self) -> dict[str, float]:
        """The newest values (0 when off, or when nothing has arrived for a moment)."""
        with self._lock:
            values = self._values
        if values is None or time.monotonic() - values.stamp > STALE:
            return dict(SILENT)
        return values.as_dict()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as exc:
                log_exception("stopping audio", exc)
        self._close_library()
        if self.state == "listening":
            self.state = "off"

    def _close_library(self) -> None:
        audio, self._audio = self._audio, None
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
