"""M8 audio: the analysis of test tones (bass, middle, treble, pitch, loudness, beats, smoothing), the engine
giving audio.* to expressions, and the "Sound is loud enough" automation condition waiting N seconds."""

from __future__ import annotations

import math
import time
import unittest
from datetime import datetime

import numpy as np

from centralight.audio import BLOCK, RATE, AudioCapture, Analyzer, list_sources
from centralight.automations import AutomationRunner, Situation, check_condition, default_condition, describe_condition
from centralight.commands import SendToBase
from centralight.engine import Engine
from centralight.model import Automation, Input
from centralight.new_show import show_from_patch
from centralight.show_io import freeze
from tests.fakes import FakeClock, FakeOutput

SLIMPAR_105 = 41


def tone(freq: float, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def feed(analyzer: Analyzer, samples: np.ndarray):
    values = None
    for start in range(0, len(samples) - BLOCK + 1, BLOCK):
        values = analyzer.feed(samples[start:start + BLOCK], stamp=0.0)
    return values


class AnalyzerTests(unittest.TestCase):
    def test_bass_and_treble(self) -> None:
        low = feed(Analyzer(attack=0.0, release=0.0), tone(80, 1.0))
        high = feed(Analyzer(attack=0.0, release=0.0), tone(4000, 1.0))
        self.assertGreater(low.bass, 0.8)
        self.assertLess(low.treble, 0.2)
        self.assertGreater(high.treble, 0.8)
        self.assertLess(high.bass, 0.2)
        self.assertLess(low.pitch, high.pitch)

    def test_pitch_rises_with_the_note(self) -> None:
        pitches = [feed(Analyzer(attack=0.0, release=0.0), tone(f, 0.5)).pitch for f in (100, 400, 1600, 6400)]
        self.assertEqual(pitches, sorted(pitches))
        self.assertLess(pitches[0], 0.25)
        self.assertGreater(pitches[-1], 0.75)

    def test_loudness_and_silence(self) -> None:
        analyzer = Analyzer(attack=0.0, release=0.0)
        loud = feed(analyzer, tone(440, 1.0, 0.8))
        self.assertGreater(loud.level, 0.9)
        quiet = feed(analyzer, tone(440, 0.2, 0.05))
        self.assertLess(quiet.level, loud.level)
        silent = feed(analyzer, np.zeros(RATE // 2, dtype=np.float32))
        self.assertLess(silent.level, 0.05)
        self.assertLess(silent.bass, 0.05)

    def test_beats_on_a_kick_drum(self) -> None:
        analyzer = Analyzer(attack=0.0, release=0.05)
        kick = tone(60, 0.08, 0.9) * np.exp(-np.arange(int(0.08 * RATE)) / (0.02 * RATE)).astype(np.float32)
        beat_pattern = np.zeros(int(0.5 * RATE), dtype=np.float32)
        beat_pattern[:len(kick)] = kick
        beats = []
        for _ in range(6):  # a kick every half second
            for start in range(0, len(beat_pattern) - BLOCK + 1, BLOCK):
                beats.append(analyzer.feed(beat_pattern[start:start + BLOCK], stamp=0.0).beat)
        per_bar = len(beats) // 6
        peaks = [max(beats[i * per_bar:(i + 1) * per_bar]) for i in range(1, 6)]
        lows = [min(beats[i * per_bar:(i + 1) * per_bar]) for i in range(1, 6)]
        self.assertTrue(all(p > 0.9 for p in peaks), peaks)  # each kick is a beat...
        self.assertTrue(all(low < 0.2 for low in lows), lows)  # ...and it fades in between

    def test_smoothing(self) -> None:
        slow = Analyzer(attack=0.5, release=0.5)
        first = slow.feed(tone(440, BLOCK / RATE, 0.8), stamp=0.0)
        self.assertLess(first.level, 0.2)  # rising over half a second
        later = feed(slow, tone(440, 1.5, 0.8))
        self.assertGreater(later.level, 0.85)


class CaptureTests(unittest.TestCase):
    def test_off_is_silent(self) -> None:
        capture = AudioCapture("")
        capture.start()
        self.assertEqual(capture.state, "off")
        self.assertEqual(set(capture.latest().values()), {0.0})
        capture.stop()

    def test_rate(self) -> None:
        capture = AudioCapture("")
        tone = (0.1 * np.sin(np.arange(BLOCK) * 2 * math.pi * 440 / RATE)).astype(np.float32).tobytes()
        for _ in range(47):
            capture._callback(tone, BLOCK, None, 0)
        capture._rate_mark = (time.monotonic() - 1.0, 0)  # as if those 47 came over the last second
        self.assertAlmostEqual(capture.rate(), 47, delta=1.5)
        self.assertGreater(capture.latest()["level"], 0.0)
        capture._rate_mark = (time.monotonic() - 1.0, capture._blocks)  # then nothing for a second
        self.assertEqual(capture.rate(), 0.0)

    def test_sources(self) -> None:
        sources = list_sources()
        self.assertEqual(sources[0][0], "")
        self.assertEqual(sources[1][0], "output")


class FakeAudio:
    def __init__(self, **values) -> None:
        self.values = {"level": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "pitch": 0.0, **values}
        self.state, self.detail = "listening", "Test tone"

    def latest(self) -> dict:
        return dict(self.values)

    def rate(self) -> float:
        return 47.0


class EngineAudioTests(unittest.TestCase):
    def test_expressions_follow_the_sound(self) -> None:
        element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}, "opacity": {"expr": "audio.bass"}}
        show = show_from_patch().with_inputs({"pulse": Input(id="pulse", name="Pulse", tracks=freeze([[element], [], [], []]))}, ("pulse",))
        audio = FakeAudio(bass=0.8)
        output = FakeOutput()
        clock = FakeClock()
        engine = Engine(show, output, None, audio=audio, clock=clock)
        engine.submit(SendToBase("pulse", cut=True))
        clock.advance(0.025)
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_105 - 1], 204)  # 80 % of 255
        audio.values["bass"] = 0.2
        clock.advance(0.025)
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_105 - 1], 51)
        frame = engine.frames.get()
        self.assertEqual(frame.audio_state, "listening")
        self.assertEqual(frame.audio_rate, 47.0)
        engine.audio = None  # Settings turned it off
        clock.advance(0.025)
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_105 - 1], 0)


class AudioConditionTests(unittest.TestCase):
    def test_loud_for_n_seconds(self) -> None:
        cond = default_condition("audio", show_from_patch())
        self.assertEqual(check_condition(cond), [])
        self.assertIn("above 60 %", describe_condition(cond))
        self.assertTrue(check_condition({"type": "audio", "band": "noise"}))
        automation = Automation("loud", "Loud", conditions=freeze([{"type": "audio", "band": "level", "above": 0.5, "for": 3}]))
        runner = AutomationRunner()
        local = datetime(2026, 11, 8, 10, 0)  # (the time of day doesn't matter here)

        def check(now: float, level: float) -> bool:
            situation = Situation(now=now, local=local, audio={"level": level})
            return runner.condition_true(automation, 0, automation.conditions[0], situation)

        self.assertFalse(check(100.0, 0.7))
        self.assertFalse(check(102.0, 0.7))  # loud, but not for long enough yet
        self.assertTrue(check(103.5, 0.7))
        self.assertFalse(check(104.0, 0.3))  # quiet again: it starts counting afresh
        self.assertFalse(check(105.0, 0.9))
        self.assertTrue(check(108.0, 0.9))


if __name__ == "__main__":
    unittest.main()
