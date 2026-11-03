"""The engine: house-light fades, Master, Blackout, restore without flicker, supervision."""

from __future__ import annotations

import time
import unittest

import numpy as np

from centralight import paths
from centralight.commands import SetBlackout, SetHouse, SetMaster, SwapShow
from centralight.engine import Engine
from centralight.live_state import HouseSetting, LiveState
from centralight.new_show import show_from_patch
from tests.fakes import FakeClock, FakeOutput

FESTOON = 0  # DMX channel 1, as a 0-based index
KEY = 3  # DMX channel 4
SLIMPAR_101_RED = 4  # channel 5


class EngineTestCase(unittest.TestCase):
    def make(self, live: LiveState | None = None) -> tuple[Engine, FakeClock, FakeOutput]:
        clock = FakeClock()
        output = FakeOutput()
        engine = Engine(show_from_patch(), output, live, clock=clock)
        return engine, clock, output

    def run_for(self, engine: Engine, clock: FakeClock, seconds: float, step: float = 0.025) -> None:
        for _ in range(int(round(seconds / step))):
            clock.advance(step)
            engine.tick()


class HouseLightTests(EngineTestCase):
    def test_house_lights_start_off(self) -> None:
        engine, _clock, output = self.make()
        engine.tick()
        self.assertEqual(output.last[FESTOON], 0)
        self.assertEqual(output.last[KEY], 0)

    def test_switch_fades_over_half_a_second(self) -> None:
        engine, clock, output = self.make()
        engine.submit(SetHouse("dimmer-4", on=True))
        engine.tick()
        self.assertEqual(output.last[KEY], 0)
        self.run_for(engine, clock, 0.25)
        self.assertTrue(110 <= output.last[KEY] <= 145, output.last[KEY])  # about half way
        self.run_for(engine, clock, 0.3)
        self.assertEqual(output.last[KEY], 255)
        self.assertEqual(output.last[FESTOON], 0)  # the festoon is separate

    def test_fader_moves_are_smoothed_and_kept_while_off(self) -> None:
        engine, clock, output = self.make()
        engine.submit(SetHouse("dimmer-1", level=0.4, on=True))
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[FESTOON], 102)
        engine.submit(SetHouse("dimmer-1", on=False))
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[FESTOON], 0)
        engine.submit(SetHouse("dimmer-1", toggle=True))
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[FESTOON], 102)  # back to its level

    def test_explicit_fade_time(self) -> None:
        engine, clock, output = self.make()
        engine.submit(SetHouse("dimmer-4", level=1.0, on=True, fade=3.0))
        self.run_for(engine, clock, 1.5)
        self.assertTrue(120 <= output.last[KEY] <= 135)
        self.run_for(engine, clock, 1.6)
        self.assertEqual(output.last[KEY], 255)

    def test_unknown_house_light_is_ignored(self) -> None:
        engine, clock, output = self.make()
        engine.submit(SetHouse("nope", on=True))
        self.run_for(engine, clock, 0.1)
        self.assertEqual(engine.frames.get().faults, 0)


class BlackoutAndMasterTests(EngineTestCase):
    def test_blackout_fades_and_wins_over_house_lights(self) -> None:
        engine, clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        engine.tick()
        self.assertEqual(output.last[KEY], 255)
        engine.submit(SetBlackout(on=True))
        self.run_for(engine, clock, 0.25)
        self.assertTrue(100 <= output.last[KEY] <= 155)
        self.run_for(engine, clock, 0.3)
        self.assertEqual(output.last[KEY], 0)
        frame = engine.frames.get()
        self.assertTrue(frame.blackout)
        self.assertEqual(frame.house["dimmer-4"].level, 1.0)  # the fader itself didn't move
        engine.submit(SetBlackout(on=None))  # toggle back
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[KEY], 255)

    def test_instant_blackout(self) -> None:
        engine, _clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        engine.submit(SetBlackout(on=True, instant=True))
        engine.tick()
        self.assertEqual(output.last[KEY], 0)

    def test_master_scales_colour_lights_only(self) -> None:
        engine, clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        engine._render = lambda now: np.ones((4, engine._lights.count), dtype=np.float32)  # white at every light
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_101_RED], 255)
        engine.submit(SetMaster(0.5))
        self.run_for(engine, clock, 0.2)
        self.assertEqual(output.last[SLIMPAR_101_RED], 128)
        self.assertEqual(output.last[KEY], 255)


class RestoreTests(EngineTestCase):
    def test_restart_sends_exactly_the_same_first_frame(self) -> None:
        engine, clock, output = self.make()
        engine.submit(SetHouse("dimmer-4", level=0.7, on=True))
        engine.submit(SetHouse("dimmer-1", level=0.3, on=True))
        engine.submit(SetMaster(0.8))
        self.run_for(engine, clock, 1.0)
        state = engine.stop(lights_off=False)
        last = output.last

        engine2, _clock2, output2 = self.make(LiveState.from_dict(state.to_dict()))
        engine2.tick()
        self.assertEqual(output2.frames[0], last)  # no flash: the very first frame matches

    def test_lights_off_quit_comes_back_blacked_out(self) -> None:
        engine, clock, _output = self.make()
        engine.submit(SetHouse("dimmer-4", on=True))
        self.run_for(engine, clock, 1.0)
        state = engine.stop(lights_off=True, fade=False)
        self.assertTrue(state.blackout)
        self.assertTrue(state.house["dimmer-4"].on)  # one click on Blackout brings it all back

        engine2, clock2, output2 = self.make(state)
        engine2.tick()
        self.assertEqual(output2.frames[0][KEY], 0)
        engine2.submit(SetBlackout(on=False))
        self.run_for(engine2, clock2, 0.6)
        self.assertEqual(output2.last[KEY], 255)

    def test_live_state_file_round_trip(self) -> None:
        state = LiveState(show="x", master=0.5, blackout=True, house={"dimmer-1": HouseSetting(0.25, True)})
        path = paths.app_data_dir() / "live-test.json"
        state.save(path)
        loaded = LiveState.load(path)
        self.assertEqual(loaded.house["dimmer-1"], HouseSetting(0.25, True))
        self.assertEqual((loaded.master, loaded.blackout, loaded.show), (0.5, True, "x"))
        path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(LiveState.load(path))


class ShowSwapTests(EngineTestCase):
    def test_new_house_lights_start_off_and_removed_ones_go(self) -> None:
        engine, clock, _output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        show = engine.show
        fewer = type(show)(name=show.name, fixtures=tuple(fx for fx in show.fixtures if fx.id != "dimmer-1"), house=show.house[1:])
        engine.submit(SwapShow(fewer))
        self.run_for(engine, clock, 0.1)
        self.assertEqual(set(engine.frames.get().house), {"dimmer-4"})
        engine.submit(SwapShow(show))
        self.run_for(engine, clock, 0.1)
        frame = engine.frames.get()
        self.assertFalse(frame.house["dimmer-1"].on)
        self.assertTrue(frame.house["dimmer-4"].on)


class _Boom(BaseException):
    """Escapes the per-tick handler, so it kills the thread (like a real crash would)."""


class SupervisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = FakeOutput()
        self.engine = Engine(show_from_patch(), self.output)

    def tearDown(self) -> None:
        self.engine.stop()

    def wait_for(self, condition, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    def test_failed_tick_is_logged_and_the_loop_carries_on(self) -> None:
        log = paths.session_log_path()
        before = log.read_text(encoding="utf-8") if log.exists() else ""
        state = {"armed": True}

        def fail_once() -> None:
            if state["armed"]:
                state["armed"] = False
                raise ValueError("injected tick failure")

        self.engine._fault_hook = fail_once
        self.engine.start()
        self.assertTrue(self.wait_for(lambda: self.engine.frames.get().faults == 1))
        seq = self.engine.frames.get().seq
        self.assertTrue(self.wait_for(lambda: self.engine.frames.get().seq > seq + 3))  # still ticking
        text = log.read_text(encoding="utf-8")[len(before):]
        self.assertIn("engine tick", text)
        self.assertIn("ValueError: injected tick failure", text)

    def test_watchdog_restarts_a_dead_engine_thread(self) -> None:
        state = {"armed": True}

        def die_once() -> None:
            if state["armed"]:
                state["armed"] = False
                raise _Boom()

        self.engine._fault_hook = die_once
        self.engine.start()
        self.assertTrue(self.wait_for(lambda: self.engine._restarts == 1))
        self.assertTrue(self.wait_for(lambda: self.engine.frames.get().restarts == 1))
        count = len(self.output.frames)
        self.assertTrue(self.wait_for(lambda: len(self.output.frames) > count + 5))

    def test_runs_at_about_40_ticks_a_second(self) -> None:
        self.engine.start()
        time.sleep(1.3)
        frame = self.engine.frames.get()
        self.assertTrue(30 <= frame.tick_rate <= 45, frame.tick_rate)
        self.assertLess(frame.tick_ms, 5.0)


if __name__ == "__main__":
    unittest.main()
