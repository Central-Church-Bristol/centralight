"""The engine: house-light fades, Master, Cut / Fade to black, restore without flicker, supervision."""

from __future__ import annotations

import time
import unittest

import numpy as np

from centralight import paths
from centralight.commands import HOUSE_SWITCH_FADE, SendToBase, SetHouse, SetMaster, SwapShow, ToBlack
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


class BlackAndMasterTests(EngineTestCase):
    def test_fade_everything_to_black_keeps_the_levels_and_switching_on_fades_back(self) -> None:
        engine, clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        engine.tick()
        self.assertEqual(output.last[KEY], 255)
        engine.submit(ToBlack(1.0, everything=True))  # held: the house lights too, over 1 s
        self.run_for(engine, clock, 0.5)
        self.assertTrue(100 <= output.last[KEY] <= 155)
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[KEY], 0)
        frame = engine.frames.get()
        self.assertFalse(frame.house["dimmer-4"].on)  # its switch went off...
        self.assertEqual(frame.house["dimmer-4"].level, 1.0)  # ...and its fader kept its level
        self.assertTrue(frame.colour_black)
        engine.submit(SetHouse("dimmer-4", on=True))  # switched on again: a fade, as it went out (1 s, not 0.5 s)
        self.run_for(engine, clock, 0.5)
        self.assertTrue(100 <= output.last[KEY] <= 155)
        self.run_for(engine, clock, 0.6)
        self.assertEqual(output.last[KEY], 255)
        engine.submit(SetHouse("dimmer-4", on=False))  # back on, its switch works as normal again (0.5 s)
        self.run_for(engine, clock, HOUSE_SWITCH_FADE + 0.05)
        self.assertEqual(output.last[KEY], 0)

    def test_cut_to_black_and_the_switch_coming_back_with_a_cut(self) -> None:
        engine, _clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        engine.submit(ToBlack(0.0))  # a click: the colour lights only
        engine.tick()
        self.assertEqual(output.last[KEY], 255)  # the key lights stay as they are
        self.assertTrue(engine.frames.get().colour_black)
        engine.submit(ToBlack(0.0, everything=True))  # held: everything, at once
        engine.tick()
        self.assertEqual(output.last[KEY], 0)
        engine.submit(SetHouse("dimmer-4", toggle=True))  # a Stream Deck toggle without its own fade
        engine.tick()
        self.assertEqual(output.last[KEY], 255)  # back at once: it went out with a cut

    def test_inputs_and_automations_bring_house_lights_back_their_own_way(self) -> None:
        from centralight.model import Input, Transition
        from centralight.show_io import freeze

        engine, clock, output = self.make(LiveState(house={"dimmer-4": HouseSetting(1.0, True)}))
        lit = Input(id="lit", name="Lit", house=freeze({"dimmer-4": 0.6}), transition=Transition("cut", 0.0))
        engine.submit(SwapShow(engine.show.with_inputs({"lit": lit}, ("lit",))))
        engine.submit(ToBlack(2.0, everything=True))
        self.run_for(engine, clock, 2.1)
        self.assertEqual(output.last[KEY], 0)
        engine.submit(SendToBase("lit", cut=True))  # an input that sets the key lights: its own way (a cut), not 2 s
        engine.tick()
        self.assertEqual(output.last[KEY], round(0.6 * 255))
        engine.submit(ToBlack(2.0, everything=True))
        self.run_for(engine, clock, 2.1)
        engine._do_action_now("auto", {"do": "house", "light": "dimmer-4", "on": True}, clock())  # an automation: as set up
        self.run_for(engine, clock, HOUSE_SWITCH_FADE + 0.05)
        self.assertEqual(output.last[KEY], round(0.6 * 255))  # (the usual 0.5 s, not the blackout's 2 s)

    def test_the_next_input_lights_the_colour_lights_and_master_isnt_touched(self) -> None:
        from centralight.model import Input
        from centralight.show_io import freeze

        engine, _clock, output = self.make()
        red = Input(id="red", name="Red", tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF0000"}}], [], [], []]))
        engine.submit(SwapShow(engine.show.with_inputs({"red": red}, ("red",))))
        engine.submit(SendToBase("red", cut=True))
        engine.submit(SetMaster(0.7, fade=0))
        engine.tick()
        lit = output.last[SLIMPAR_101_RED]
        self.assertIn(lit, (178, 179))  # red at Master 70 %
        engine.submit(ToBlack(0.0))
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_101_RED], 0)
        self.assertTrue(engine.frames.get().colour_black)
        self.assertAlmostEqual(engine.frames.get().master, 0.7)  # Master isn't touched
        engine.submit(SendToBase("red", cut=True))  # the next input lights things again
        engine.tick()
        self.assertEqual(output.last[SLIMPAR_101_RED], lit)
        self.assertFalse(engine.frames.get().colour_black)

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

    def test_lights_off_quit_comes_back_black(self) -> None:
        engine, clock, _output = self.make()
        engine.submit(SetHouse("dimmer-4", level=0.8, on=True))
        self.run_for(engine, clock, 1.0)
        state = engine.stop(lights_off=True, fade=False)
        self.assertTrue(state.quit_dark)
        self.assertIsNone(state.base)
        self.assertEqual(state.house["dimmer-4"], HouseSetting(0.8, False))  # off, its level kept

        engine2, clock2, output2 = self.make(state)
        engine2.tick()
        self.assertEqual(output2.frames[0][KEY], 0)
        engine2.submit(SetHouse("dimmer-4", on=True))  # switched on by hand: it comes back at its level
        self.run_for(engine2, clock2, 0.6)
        self.assertEqual(output2.last[KEY], round(0.8 * 255))
        self.assertFalse(engine2._quit_dark)  # (a person took charge)

    def test_live_state_file_round_trip(self) -> None:
        state = LiveState(show="x", master=0.5, quit_dark=True, quit_at=1234.0, house={"dimmer-1": HouseSetting(0.25, True)})
        path = paths.app_data_dir() / "live-test.json"
        state.save(path)
        loaded = LiveState.load(path)
        self.assertEqual(loaded.house["dimmer-1"], HouseSetting(0.25, True))
        self.assertEqual((loaded.master, loaded.quit_dark, loaded.quit_at, loaded.show), (0.5, True, 1234.0, "x"))
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
