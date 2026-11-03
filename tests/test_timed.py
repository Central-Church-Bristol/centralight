"""M6 timed inputs: keyframe editing, an input's own time (hold / loop / stop), elements placed in time, element
in / out transitions and cross transitions, editing a timeline (add, move, trim, split, infinite <-> timed),
and the engine playing all of it, deterministically under a hand-moved clock."""

from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from centralight.bus import Instance, render_input, track_elements
from centralight.commands import SendToBase, SendToOverlay
from centralight.elements import RenderContext
from centralight.engine import Engine
from centralight.input_edit import (
    add_element,
    crowded_tracks,
    element_keys,
    find_element,
    move_element_to,
    move_key,
    remove_key,
    set_element_transition,
    set_key,
    set_key_interpolation,
    set_length,
    set_timed,
    split_element,
    trim_element,
)
from centralight.model import END_HOLD, END_LOOP, END_STOP, Input, element_span, input_progress, input_time
from centralight.new_show import show_from_patch
from centralight.params import (
    base_value,
    key_at,
    keys_of,
    neighbour_key,
    with_key,
    with_key_interpolation,
    with_key_moved,
    without_key,
)
from centralight.lights import lights_for
from centralight.show_io import freeze, input_from_dict, input_to_dict
from tests.fakes import FakeClock, FakeOutput

SLIMPAR_105 = 41  # channel of its red


def paint(element_id: str, colour: str, start: float | None = None, end: float | None = None, **extra) -> dict:
    element = {"id": element_id, "kind": "paint", "fill": {"type": "colour", "colour": colour}, **extra}
    if start is not None:
        element["start"], element["end"] = start, end
    return element


def timed(input_id: str, tracks, length: float = 10.0, end: str = END_HOLD) -> Input:
    tracks = [list(t) for t in tracks] + [[]] * (4 - len(tracks))
    return Input(id=input_id, name=input_id.title(), infinite=False, length=length, end=end, tracks=freeze(tracks))


class KeyframeTests(unittest.TestCase):
    def test_add_change_and_remove(self) -> None:
        param = with_key(0.5, 0.0, 0.2)  # a fixed value becomes keyframes
        param = with_key(param, 2.0, 0.8)
        self.assertEqual(keys_of(param), [(0.0, 0.2, "smooth"), (2.0, 0.8, "smooth")])
        self.assertAlmostEqual(base_value(param, 1.0, 0.0), 0.5)  # smooth: half way at half time
        param = with_key(param, 2.0005, 0.9)  # close enough to 2 s: changes that key
        self.assertEqual(len(keys_of(param)), 2)
        self.assertEqual(key_at(param, 2.0)[1], 0.9)
        param = without_key(param, 0.0)
        param = without_key(param, 2.0)
        self.assertEqual(param, 0.9)  # no keys left: back to a fixed value

    def test_expressions_survive(self) -> None:
        param = with_key({"value": 0.5, "expr": "value * 2"}, 1.0, 0.3)
        self.assertEqual(param["expr"], "value * 2")
        self.assertNotIn("value", param)
        self.assertEqual(without_key(param, 1.0), {"expr": "value * 2", "value": 0.3})

    def test_move_interpolation_and_neighbours(self) -> None:
        param = with_key(with_key(with_key(0.0, 0.0, 0.0), 1.0, 1.0), 3.0, 0.0)
        moved = with_key_moved(param, 1.0, 2.0)
        self.assertEqual([k[0] for k in keys_of(moved)], [0.0, 2.0, 3.0])
        held = with_key_interpolation(param, 0.0, "hold")
        self.assertEqual(base_value(held, 0.9, 0.0), 0.0)  # holds until the next key
        linear = with_key_interpolation(param, 0.0, "linear")
        self.assertAlmostEqual(base_value(linear, 0.25, 0.0), 0.25)
        self.assertEqual(neighbour_key(param, 1.0, 1), 3.0)
        self.assertEqual(neighbour_key(param, 1.0, -1), 0.0)
        self.assertIsNone(neighbour_key(param, 3.0, 1))

    def test_colour_keys(self) -> None:
        param = with_key(with_key("#000000", 0.0, "#000000"), 2.0, "#FF0000")
        r, g, b = base_value(param, 2.0, (0, 0, 0), "colour")
        self.assertEqual((r, g, b), (1.0, 0.0, 0.0))


class InputTimeTests(unittest.TestCase):
    def test_hold_loop_stop(self) -> None:
        held = timed("a", [], 4.0, END_HOLD)
        self.assertAlmostEqual(input_time(held, 2.0), 2.0)
        self.assertLess(input_time(held, 9.0), 4.0)  # holds its last moment
        self.assertGreater(input_time(held, 9.0), 3.999)
        looped = timed("b", [], 4.0, END_LOOP)
        self.assertAlmostEqual(input_time(looped, 9.0), 1.0)
        self.assertAlmostEqual(input_progress(looped, 9.0), 0.25)
        stopped = timed("c", [], 4.0, END_STOP)
        self.assertEqual(input_time(stopped, 9.0), 9.0)  # past the end: nothing plays
        self.assertEqual(input_progress(stopped, 9.0), 1.0)
        self.assertIsNone(input_progress(Input(id="d", name="D"), 3.0))  # infinite: no progress

    def test_which_elements_are_showing(self) -> None:
        inp = timed("a", [[paint("E1", "#FF0000", 0, 4), paint("E2", "#0000FF", 3, 8)]])
        track = inp.tracks[0]
        self.assertEqual([e["id"] for e in track_elements(track, 1.0, inp)], ["E1"])
        self.assertEqual([e["id"] for e in track_elements(track, 3.5, inp)], ["E1", "E2"])  # crossing
        self.assertEqual([e["id"] for e in track_elements(track, 6.0, inp)], ["E2"])
        self.assertEqual(track_elements(track, 9.0, inp), [])
        self.assertEqual(element_span(track[1], inp), (3.0, 8.0))


class TimedRenderTests(unittest.TestCase):
    def colours(self, inp: Input, t: float) -> dict:
        show = show_from_patch()
        ctx = RenderContext(0, 0, lights=lights_for(show.fixtures), per_light=True)
        return ctx.lights.colours(render_input(Instance(inp, 0.0), t, ctx))

    def red(self, inp: Input, t: float) -> float:
        return float(self.colours(inp, t)["slimpar-105"][0])

    def test_elements_play_at_their_times(self) -> None:
        inp = timed("seq", [[paint("E1", "#FF0000", 0, 2), paint("E2", "#00FF00", 2, 4)]], 4.0)
        np.testing.assert_allclose(self.colours(inp, 1.0)["slimpar-105"], (1, 0, 0), atol=1e-3)
        np.testing.assert_allclose(self.colours(inp, 3.0)["slimpar-105"], (0, 1, 0), atol=1e-3)
        np.testing.assert_allclose(self.colours(inp, 9.0)["slimpar-105"], (0, 1, 0), atol=1e-3)  # held

    def test_keyframes_count_from_the_elements_start(self) -> None:
        element = paint("E2", "#FF0000", 5, 10, opacity={"keys": [[0, 0.0], [2, 1.0, "linear"]]})
        inp = timed("late", [[element]], 10.0)
        self.assertAlmostEqual(self.red(inp, 5.0), 0.0, delta=0.01)
        self.assertAlmostEqual(self.red(inp, 6.0), 0.5, delta=0.02)  # one second into the element
        self.assertAlmostEqual(self.red(inp, 7.5), 1.0, delta=0.01)

    def test_in_and_out_transitions(self) -> None:
        element = paint("E1", "#FF0000", 0, 4, **{"in": {"kind": "fade", "duration": 1.0}, "out": {"kind": "fade", "duration": 2.0}})
        inp = timed("fades", [[element]], 4.0)
        self.assertLess(self.red(inp, 0.1), 0.1)  # fading in
        self.assertAlmostEqual(self.red(inp, 1.5), 1.0, delta=0.01)
        self.assertGreater(self.red(inp, 2.5), self.red(inp, 3.5))  # fading out over the last 2 s
        self.assertLess(self.red(inp, 3.95), 0.05)

    def test_cross_transition_where_elements_overlap(self) -> None:
        inp = timed("cross", [[paint("E1", "#FF0000", 0, 4), paint("E2", "#0000FF", 3, 8)]], 8.0)
        before, middle, after = (self.colours(inp, t)["slimpar-105"] for t in (2.9, 3.5, 4.1))
        np.testing.assert_allclose(before, (1, 0, 0), atol=1e-3)
        self.assertGreater(middle[0], 0.2)  # both, half way
        self.assertGreater(middle[2], 0.2)
        np.testing.assert_allclose(after, (0, 0, 1), atol=1e-3)

    def test_loop_plays_the_same_again(self) -> None:
        element = paint("E1", "#FF0000", 0, 4, opacity={"keys": [[0, 0.0], [4, 1.0, "linear"]]})
        inp = timed("loop", [[element]], 4.0, END_LOOP)
        for t in (0.5, 1.7, 3.2):
            self.assertAlmostEqual(self.red(inp, t), self.red(inp, t + 4.0), places=4)
            self.assertAlmostEqual(self.red(inp, t), self.red(inp, t + 12.0), places=4)

    def test_stop_shows_nothing_after_the_end(self) -> None:
        inp = timed("stop", [[paint("E1", "#FF0000", 0, 4)]], 4.0, END_STOP)
        self.assertAlmostEqual(self.red(inp, 3.9), 1.0, delta=1e-3)
        self.assertAlmostEqual(self.red(inp, 4.5), 0.0, delta=1e-3)


class TimelineEditTests(unittest.TestCase):
    def setUp(self) -> None:
        show = show_from_patch()
        inp = Input(id="seq", name="Seq", tracks=freeze([[paint("E1", "#FF0000")], [paint("E2", "#00FF00")], [], []]))
        self.show = show.with_inputs({"seq": inp}, ("seq",))

    def inp(self, show) -> Input:
        return show.inputs["seq"]

    def element(self, show, element_id: str) -> dict:
        return find_element(self.inp(show), element_id)[2]

    def test_timed_and_back(self) -> None:
        show = set_timed(self.show, "seq", True)
        inp = self.inp(show)
        self.assertFalse(inp.infinite)
        self.assertEqual(element_span(self.element(show, "E1"), inp), (0.0, inp.length))
        show, e3 = add_element(show, "seq", 0, "paint", start=6.0)
        self.assertEqual(element_span(self.element(show, e3), self.inp(show)), (6.0, 10.0))  # up to the end
        self.assertEqual(list(crowded_tracks(self.inp(show))), [0])
        back = set_timed(show, "seq", False, keep={0: e3})
        self.assertTrue(self.inp(back).infinite)
        self.assertEqual([e["id"] for e in self.inp(back).tracks[0]], [e3])
        self.assertNotIn("start", self.inp(back).tracks[0][0])

    def test_move_trim_and_split(self) -> None:
        show = set_timed(self.show, "seq", True)
        show = set_key(show, "seq", "E1", "opacity", 2.0, 0.0)
        show = set_key(show, "seq", "E1", "opacity", 6.0, 1.0)
        show = move_element_to(show, "seq", "E1", 1.0)
        self.assertEqual(element_span(self.element(show, "E1"), self.inp(show)), (1.0, 11.0))  # keeps its length (past the end: it just stops there)
        show = trim_element(show, "seq", "E1", start=3.0)  # trimming the start keeps the keys where they were
        keys = keys_of(self.element(show, "E1")["opacity"])
        self.assertEqual([k[0] for k in keys], [0.0, 4.0])  # was 2 s and 6 s after a start of 1 s
        show, second = split_element(show, "seq", "E1", 5.0)
        first_el, second_el = self.element(show, "E1"), self.element(show, second)
        self.assertEqual(element_span(first_el, self.inp(show))[1], 5.0)
        self.assertEqual(element_span(second_el, self.inp(show))[0], 5.0)
        # the look carries on across the cut: E1 ends where E2 begins
        end_value = base_value(first_el["opacity"], 2.0, 0.0)
        start_value = base_value(second_el["opacity"], 0.0, 0.0)
        self.assertAlmostEqual(end_value, start_value, places=3)

    def test_transitions_and_length(self) -> None:
        show = set_timed(self.show, "seq", True)
        show = set_element_transition(show, "seq", "E1", "in", "wipe", 1.5)
        self.assertEqual(self.element(show, "E1")["in"], {"kind": "wipe", "duration": 1.5})
        show = set_element_transition(show, "seq", "E1", "in", "cut")
        self.assertNotIn("in", self.element(show, "E1"))
        show = set_length(show, "seq", 20.0, END_LOOP)
        self.assertEqual((self.inp(show).length, self.inp(show).end), (20.0, END_LOOP))

    def test_keys_on_shapes_and_listing(self) -> None:
        show = set_timed(self.show, "seq", True)
        element = dict(self.element(show, "E1"))
        element["shapes"] = [{"id": "S1", "type": "rect", "x": 0.5}]
        inp = self.inp(show)
        show = show.with_inputs({**show.inputs, "seq": replace(inp, tracks=freeze([[element], list(inp.tracks[1]), [], []]))})
        show = set_key(show, "seq", "E1", "shapes:S1:x", 1.0, 0.2)
        show = set_key(show, "seq", "E1", "opacity", 2.0, 0.5)
        show = set_key_interpolation(show, "seq", "E1", "opacity", 2.0, "hold")
        keys = element_keys(self.element(show, "E1"))
        self.assertEqual(set(keys), {"shapes:S1:x", "opacity"})
        self.assertEqual(keys["opacity"][0][2], "hold")
        show = move_key(show, "seq", "E1", "opacity", 2.0, 3.0)
        show = remove_key(show, "seq", "E1", "shapes:S1:x", 1.0)
        self.assertEqual(set(element_keys(self.element(show, "E1"))), {"opacity"})

    def test_files_keep_it_all(self) -> None:
        show = set_timed(self.show, "seq", True)
        show = set_element_transition(show, "seq", "E1", "out", "fade", 2.0)
        show = set_key(show, "seq", "E1", "opacity", 1.0, 0.5)
        inp = self.inp(show)
        again, warnings = input_from_dict(input_to_dict(inp), expected_id="seq")
        self.assertEqual(warnings, [])
        self.assertEqual(again, inp)


class EngineTimedTests(unittest.TestCase):
    def setUp(self) -> None:
        inputs = {
            "flash": timed("flash", [[paint("E1", "#FF0000", 0, 2)]], 2.0, END_STOP),
            "cycle": timed("cycle", [[paint("E1", "#FF0000", 0, 1), paint("E2", "#0000FF", 1, 2)]], 2.0, END_LOOP),
            "base": Input(id="base", name="Base", tracks=freeze([[paint("E1", "#00FF00")], [], [], []])),
        }
        self.show = show_from_patch().with_inputs(inputs, tuple(inputs))
        self.clock = FakeClock()
        self.output = FakeOutput()
        self.engine = Engine(self.show, self.output, None, clock=self.clock)

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.025))):
            self.clock.advance(0.025)
            self.engine.tick()

    def rgb(self) -> tuple:
        return tuple(self.output.last[SLIMPAR_105 - 1:SLIMPAR_105 + 2])

    def test_stop_takes_it_off_base(self) -> None:
        self.engine.submit(SendToBase("flash", cut=True))
        self.run_for(1.0)
        self.assertEqual(self.rgb(), (255, 0, 0))
        self.assertAlmostEqual(self.engine.frames.get().playing["flash"], 0.5, delta=0.08)  # frames go out every 0.1 s
        self.run_for(1.2)
        frame = self.engine.frames.get()
        self.assertEqual(frame.base_id, "")  # finished: Base is empty
        self.assertEqual(self.rgb(), (0, 0, 0))
        self.assertNotIn("flash", frame.playing)

    def test_resending_a_timed_input_restarts_it(self) -> None:
        self.engine.submit(SendToBase("cycle", cut=True))
        self.run_for(1.5)
        self.assertEqual(self.rgb(), (0, 0, 255))  # its second half
        self.engine.submit(SendToBase("cycle", cut=True))
        self.run_for(0.25)
        self.assertEqual(self.rgb(), (255, 0, 0))  # from the start again

    def test_loop_and_overlay_stop(self) -> None:
        self.engine.submit(SendToBase("base", cut=True))
        self.engine.submit(SendToBase("cycle", cut=True))
        seen = []
        self.run_for(0.5)
        for _ in range(4):  # at 0.5, 1.5, 2.5 and 3.5 s
            seen.append(self.rgb())
            self.run_for(1.0)
        self.assertEqual(seen, [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 255)])  # round and round
        self.engine.submit(SendToBase("base", cut=True))
        self.engine.submit(SendToOverlay(1, "flash"))
        self.run_for(1.0)
        self.assertTrue(self.engine.frames.get().overlays[0].on)
        self.run_for(1.6)
        self.assertFalse(self.engine.frames.get().overlays[0].on)  # finished: the overlay went off
        self.assertEqual(self.rgb(), (0, 255, 0))


if __name__ == "__main__":
    unittest.main()
