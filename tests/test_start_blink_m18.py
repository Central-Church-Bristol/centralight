"""The start-up blink (8 Nov): after a PC restart, with the lights left on and a schedule bringing the same show back,
the lights went black for a moment, then faded in again. Now a PC start sends nothing until the start-up automations
are done, then fades from the frame the lights were last sent (live.json) to what CentraLight shows."""

from __future__ import annotations

import json
import threading
import unittest

from centralight.commands import QUIT_FADE, SHUTDOWN_FADE, PowerEvent, SendToBase, ToBlack
from centralight.live_state import LiveState
from centralight.model import Show
from centralight.new_show import show_from_patch
from centralight.show_io import freeze
from tests.fakes import FakeEngine
from tests.gui_fixtures import paint_input
from tests.test_automations import automation

WARM = paint_input("warm-white", "Warm white", "#FFB46B")
START_UP = automation("start-up", [{"type": "show_loads"}],
                      [{"do": "base", "input": "warm-white", "transition": {"kind": "fade", "duration": 2}}])
LIGHTS_OUT = automation("lights-out", [{"type": "pc_shutdown"}], [{"do": "fade_black", "fade": 3, "all": True}])


def a_show(start_up: bool = True):
    show = show_from_patch().with_inputs({WARM.id: WARM}, (WARM.id,))
    if start_up:
        show = show.with_automations({START_UP.id: START_UP}, (START_UP.id,))
    return show


def the_look() -> bytes:
    """The frame "Warm white" on Base sends (what the lights showed before the restart)."""
    engine = FakeEngine(a_show(start_up=False))
    from centralight.commands import SendToBase

    engine.submit(SendToBase("warm-white", cut=True))
    engine.settle(0.5)
    return engine.fake_output.frames[-1]


def pc_start(last: bytes, start_up: bool = True, global_automations: Show | None = None) -> FakeEngine:
    return FakeEngine(a_show(start_up), LiveState(last_frame=list(last)), global_automations=global_automations,
                      pc_start=True)


def black() -> bytes:
    """The frame with nothing on Base (the colours at 0)."""
    return FakeEngine(a_show(start_up=False)).fake_output.frames[-1]


def down_only(frames: list[bytes]) -> bool:
    return all(all(b <= a for a, b in zip(earlier, later)) for earlier, later in zip(frames, frames[1:]))


class StartBlinkTests(unittest.TestCase):
    def test_the_same_look_coming_back_never_dips(self) -> None:
        look = the_look()
        self.assertTrue(any(look))
        engine = pc_start(look)
        self.assertEqual(engine.fake_output.frames, [])  # (nothing sent yet: the lights keep their look)
        engine.settle(3.0)
        frames = engine.fake_output.frames
        self.assertTrue(frames)
        for frame in frames:  # (no frame darker than the look: it holds, then carries on)
            self.assertTrue(all(f >= h - 1 for f, h in zip(frame, look)), (list(frame[:16]), list(look[:16])))
        self.assertEqual(frames[-1], look)

    def test_nothing_to_load_fades_to_black(self) -> None:
        look = the_look()
        engine = pc_start(look, start_up=False)
        engine.settle(engine.black_fade + 1.5)
        frames = engine.fake_output.frames
        self.assertTrue(frames)
        black = FakeEngine(a_show(start_up=False)).fake_output.frames[-1]  # (nothing on Base: colours at 0)
        self.assertEqual(frames[-1], black)  # (black, as a PC start leaves them)
        first = frames[0]
        self.assertTrue(all(f >= h - 40 for f, h in zip(first, look)))  # (from the look, not a cut to black)
        totals = [sum(frame) for frame in frames]
        self.assertEqual(totals, sorted(totals, reverse=True))  # (down only)

    def test_after_a_shutdown_fade_it_comes_up_from_black(self) -> None:
        look = the_look()
        for last in (black(), bytes(len(look))):  # (a shutdown fade's black; a quit's blackout, all at 0)
            engine = pc_start(last)
            engine.settle(3.0)
            frames = engine.fake_output.frames
            self.assertLess(sum(frames[0]), sum(look) // 3)  # (from black...)
            self.assertEqual(frames[-1], look)  # (...up to the look)
            totals = [sum(frame) for frame in frames]
            self.assertEqual(totals, sorted(totals))  # (up only)

    def test_a_reopen_without_a_restart_is_as_before(self) -> None:
        look = the_look()
        engine = FakeEngine(a_show(), LiveState(last_frame=list(look), base={"input": "warm-white", "time": 1.0}))
        self.assertTrue(engine.fake_output.frames)  # (sent from the first tick: no hold)
        self.assertIsNone(engine._crossfade)
        engine.settle(0.5)
        self.assertEqual(engine.fake_output.frames[-1], look)

    def test_a_shutdown_just_after_a_pc_start_fades_from_the_look(self) -> None:
        look = the_look()
        globals_ = Show(name="Global automations").with_automations({LIGHTS_OUT.id: LIGHTS_OUT}, (LIGHTS_OUT.id,))
        engine = pc_start(look, global_automations=globals_)
        self.assertEqual(engine.fake_output.frames, [])
        done = threading.Event()
        engine.submit(PowerEvent("shutdown", done))  # (Windows shutting down within the start-up's wait)
        self.assertFalse(engine._start_hold)
        self.assertEqual(engine.fake_output.frames[0], look)  # (sent at once, from the look the lights have)
        engine.settle(SHUTDOWN_FADE - 1.5)
        self.assertFalse(done.is_set())  # (Windows waits while the lights fade...)
        engine.settle(1.5)
        self.assertTrue(done.is_set())  # (...within the time it waits)
        frames = engine.fake_output.frames
        self.assertEqual(frames[-1], black())
        self.assertTrue(down_only(frames))

    def test_someone_taking_charge_isnt_kept_waiting(self) -> None:
        look = the_look()
        engine = pc_start(look, start_up=False)
        engine.submit(SendToBase("warm-white", cut=True))  # (a tap on the phone, say, while it starts)
        self.assertFalse(engine._start_hold)
        engine.settle(engine.black_fade + 0.5)
        frames = engine.fake_output.frames
        self.assertTrue(all(frame == look for frame in frames))  # (the same look: no change you'd see)

    def test_a_quit_just_after_a_pc_start_fades_in_its_time(self) -> None:
        look = the_look()
        engine = pc_start(look)
        engine.submit(ToBlack(QUIT_FADE, everything=True))  # (what a quit with "Turn the lights off" sends)
        engine.settle(QUIT_FADE + 0.05)
        frames = engine.fake_output.frames
        self.assertEqual(frames[0], look)  # (from the look...)
        self.assertEqual(frames[-1], black())  # (...to black, in the quit's time)
        self.assertTrue(down_only(frames))

    def test_a_fade_to_black_meanwhile_gets_there_in_its_time(self) -> None:
        engine = pc_start(black())  # (what a shutdown fade leaves)
        engine.settle(1.0)  # (the look fading up from black)
        self.assertIsNotNone(engine._crossfade)
        engine.submit(ToBlack(0.5))
        sent = len(engine.fake_output.frames)
        engine.settle(0.55)
        frames = engine.fake_output.frames[sent - 1:]
        self.assertEqual(frames[-1], black())
        self.assertTrue(down_only(frames))  # (on from where it was: no jump)

    def test_what_a_quit_leaves_is_saved(self) -> None:
        look = the_look()
        engine = FakeEngine(a_show(start_up=False))
        from centralight.commands import SendToBase

        engine.submit(SendToBase("warm-white", cut=True))
        engine.settle(0.5)
        self.assertEqual(bytes(engine.live_state().last_frame), look)  # (leaving the lights as they are)
        state = engine.stop(lights_off=True, fade=False)
        self.assertEqual(state.last_frame, [0] * len(look))  # ("Turn the lights off": black)
        saved = LiveState.from_dict(json.loads(json.dumps(LiveState(last_frame=list(look)).to_dict())))
        self.assertEqual(saved.last_frame, list(look))
        self.assertEqual(saved.for_show("elsewhere").last_frame, list(look))  # (whatever show opens next)
        self.assertEqual(LiveState.from_dict({"last_frame": [1, 300]}).last_frame, [])  # (odd: ignored)


class LiveSaveTests(unittest.TestCase):
    def test_the_frame_alone_never_makes_a_save(self) -> None:
        from centralight.ui.main_window import _live_key

        self.assertEqual(_live_key(LiveState(last_frame=[1, 2, 3])), _live_key(LiveState(last_frame=[9, 9, 9])))


if __name__ == "__main__":
    unittest.main()
