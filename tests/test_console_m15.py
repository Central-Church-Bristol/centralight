"""M15: the Console page's engine side. What you touch wins, per light and per attribute, until Release (with a fade);
untouched attributes follow the inputs; between Live changes and a chase the later one wins. Master still scales Live
colours, Cut / Fade to black let go of everything with them, Identify shows on top, another show releases, and a reopen
carries on (a PC start doesn't). Chases: timing at 20, 40 and 60 ticks a second, crossfade, the directions, play once,
Tap, Pause, Next and stepping on each beat."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import MappingProxyType

from centralight.commands import (ConsoleChase, ConsoleRelease, ConsoleSet, LoadShow, SendToBase, SetIdentify, SetMaster,
                                  ToBlack)
from centralight.console import Chase, ChaseRun, ChaseStep, ConsoleState, chase_from_dict, chase_to_dict
from centralight.live_state import LiveState
from centralight.new_show import show_from_patch
from centralight.output import encode_dmx
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs

RED = {"red": 255, "green": 0, "blue": 0}
A, B = "slimpar-101", "slimpar-102"
PILLARS = ("slimpar-106", "slimpar-107")


def show():
    return with_inputs(show_from_patch())


def chans(engine, light: str = A) -> tuple[int, ...]:
    """The light's channels in the last frame sent: red, green, blue, amber, macro, strobe, program, dimmer, speed."""
    fx = next(f for f in engine.show.fixtures if f.id == light)
    frame = engine.fake_output.frames[-1]
    return tuple(frame[fx.address - 1:fx.address - 1 + fx.channels])


def steps(*reds: int, light: str = A) -> tuple[ChaseStep, ...]:
    return tuple(ChaseStep(MappingProxyType({light: (red / 255, 0.0, 0.0, 1.0)})) for red in reds)


class TakeoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine(show())
        self.engine.submit(SendToBase("worship-blue", cut=True))
        self.engine.settle(0.1)
        self.before_a, self.before_b = chans(self.engine, A), chans(self.engine, B)

    def test_what_you_touch_wins_per_light_and_attribute(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: RED}))
        self.assertEqual(chans(engine)[:3], (255, 0, 0))
        self.assertEqual(chans(engine)[7], 255)  # (brightness untouched: still the input's, full)
        self.assertEqual(chans(engine, B), self.before_b)  # (other lights follow the input)
        engine.submit(ConsoleSet({A: {"dimmer": 100}}))
        self.assertEqual(chans(engine)[:3] + chans(engine)[7:8], (255, 0, 0, 100))
        engine.submit(SendToBase("warm-white", cut=True))
        engine.settle(0.2)
        self.assertEqual(chans(engine)[:3], (255, 0, 0))  # (the input changed under it: the Console still wins)
        self.assertNotEqual(chans(engine, B)[:3], self.before_b[:3])
        engine.submit(ConsoleSet({A: {"amber": 90, "strobe": 30, "program": 12, "macro": 7, "dimmer_speed": 3}}))
        self.assertEqual(chans(engine), (255, 0, 0, 90, 7, 30, 12, 100, 3))

    def test_a_change_fades_in(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: RED}, fade=2.0))
        engine.settle(1.0)
        red, green, blue = chans(engine)[:3]
        self.assertAlmostEqual(red, round((255 + self.before_a[0]) / 2), delta=4)
        self.assertAlmostEqual(blue, round(self.before_a[2] / 2), delta=4)
        engine.settle(1.1)
        self.assertEqual(chans(engine)[:3], (255, 0, 0))

    def test_release_fades_back_and_strobe_snaps(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: {**RED, "strobe": 200, "dimmer": 55}}))
        engine.submit(ConsoleRelease((A,), 2.0))
        self.assertEqual(chans(engine)[5], 0)  # (strobe goes at once)
        engine.settle(1.0)
        middle = chans(engine)
        # (what the light gives, colour times dimmer, goes straight: the dimmer fades in its own straight line and
        # the colour makes up the rest)
        self.assertAlmostEqual(middle[7], (55 + 255) / 2, delta=3)
        self.assertAlmostEqual(middle[0], (255 * 55 + (self.before_a[0] * 255 - 255 * 55) * 0.5) / middle[7], delta=3)
        engine.settle(1.1)
        self.assertEqual(chans(engine), self.before_a)
        self.assertFalse(engine._console.active)

    def test_release_all_uses_the_settings_fade(self) -> None:
        engine = self.engine
        engine.release_fade = 1.0
        engine.submit(ConsoleSet({A: RED, B: RED}))
        engine.submit(ConsoleRelease())
        engine.settle(1.05)
        self.assertEqual((chans(engine, A), chans(engine, B)), (self.before_a, self.before_b))

    def test_master_still_scales_live_colours(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: RED}))
        engine.submit(SetMaster(0.5))
        engine.settle(1.0)
        self.assertAlmostEqual(chans(engine)[0], 128, delta=1)

    def test_cut_to_black_lets_go_at_once(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: {**RED, "strobe": 100}}))
        engine.submit(ToBlack(0.0))
        self.assertEqual(chans(engine)[:3], (0, 0, 0))
        self.assertEqual(chans(engine)[5], 0)
        self.assertFalse(engine._console.active)

    def test_fade_to_black_fades_it_with_the_rest(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: RED}))
        engine.submit(ToBlack(2.0))
        engine.settle(1.0)
        self.assertTrue(0 < chans(engine)[0] < 255)
        engine.settle(1.1)
        self.assertEqual(chans(engine)[:3], (0, 0, 0))
        self.assertFalse(engine._console.active)

    def test_identify_shows_on_top(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: {**RED, "dimmer": 40, "strobe": 200, "amber": 90}}))
        engine.submit(SetIdentify(True, engine.show.identify_seed))
        on_top = chans(engine)
        self.assertEqual(on_top[3:], (0, 0, 0, 0, 255, 0))  # (full brightness, no strobe or amber)
        engine.submit(SetIdentify(False, engine.show.identify_seed))
        self.assertEqual(chans(engine)[:3], (255, 0, 0))  # (and the Console's look comes back after)

    def test_another_show_releases_with_the_fade(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: RED}))
        engine.submit(LoadShow(replace(engine.show, folder="C:/Shows/Evening", name="Evening")))
        self.assertFalse(engine._console.held)
        engine.settle(2.1)
        self.assertFalse(engine._console.active)
        self.assertNotEqual(chans(engine)[:3], (255, 0, 0))

    def test_a_reopen_carries_on_and_a_pc_start_lets_go(self) -> None:
        engine = self.engine
        engine.submit(ConsoleSet({A: {**RED, "dimmer": 120}}))
        engine.submit(ConsoleChase("play", Chase(PILLARS, (
            ChaseStep(MappingProxyType({PILLARS[0]: (1.0, 0.0, 0.0, 1.0), PILLARS[1]: (0.0, 0.0, 1.0, 1.0)})),
            ChaseStep(MappingProxyType({PILLARS[0]: (0.0, 1.0, 0.0, 1.0), PILLARS[1]: (1.0, 1.0, 1.0, 1.0)}))), bpm=60)))
        engine.settle(1.5)  # (into its second step)
        saved = LiveState.from_dict(json.loads(json.dumps(engine.live_state().to_dict())))
        self.assertIn("held", saved.console)
        again = FakeEngine(engine.show, live=saved)
        self.assertEqual(chans(again)[:3] + chans(again)[7:8], (255, 0, 0, 120))
        self.assertEqual(chans(again, PILLARS[0])[:3], (0, 255, 0))  # (the chase, where it was)
        fresh = FakeEngine(engine.show, live=saved, pc_start=True)
        self.assertFalse(fresh._console.active)
        self.assertNotEqual(chans(fresh)[:3], (255, 0, 0))


class ChaseTests(unittest.TestCase):
    def test_live_and_chase_the_later_wins(self) -> None:
        engine = FakeEngine(show())
        chase = Chase((A,), steps(50, 100), bpm=60)
        engine.submit(ConsoleChase("play", chase))
        self.assertEqual(chans(engine)[0], 50)
        engine.submit(ConsoleSet({A: {"red": 200}}))
        self.assertEqual(chans(engine)[0], 200)  # (Live, later, wins)
        engine.submit(ConsoleChase("play", chase))
        self.assertEqual(chans(engine)[0], 50)  # (the chase, later again, wins)

    def test_timing_at_20_40_and_60_ticks_a_second(self) -> None:
        for rate in (20, 40, 60):
            engine = FakeEngine(show())
            engine.submit(ConsoleChase("play", Chase((A,), steps(40, 80, 120, 160), bpm=120)))  # (a step every 0.5 s)
            start = engine.clock.t
            for _ in range(rate * 3):
                engine.clock.advance(1.0 / rate)
                engine.tick()
                elapsed = engine.clock.t - start
                place = elapsed / 0.5
                if abs(place - round(place)) * 0.5 < 1.5 / rate:
                    continue  # (right on a step's start: either side of it is fair)
                self.assertEqual(chans(engine)[0], 40 * (int(place) % 4 + 1), (rate, round(elapsed, 3)))

    def test_crossfade(self) -> None:
        glide = ChaseRun(Chase((A,), steps(0, 200), bpm=60, crossfade=1.0), 0.0)
        self.assertAlmostEqual(glide.look(A, 1.5)[0] * 255, 100, delta=1)  # (halfway into step 2: halfway there)
        snap = ChaseRun(Chase((A,), steps(0, 200), bpm=60, crossfade=0.0), 0.0)
        self.assertAlmostEqual(snap.look(A, 1.01)[0] * 255, 200, delta=1)
        half = ChaseRun(Chase((A,), steps(0, 200), bpm=60, crossfade=0.5), 0.0)
        self.assertAlmostEqual(half.look(A, 1.25)[0] * 255, 100, delta=1)
        self.assertAlmostEqual(half.look(A, 1.6)[0] * 255, 200, delta=1)

    def test_directions(self) -> None:
        def order(direction: str, loop: bool = True) -> list[int]:
            run = ChaseRun(Chase((A,), steps(1, 2, 3, 4), bpm=60, direction=direction, loop=loop), 0.0)
            return [run.step_of(i) for i in range(10)]

        self.assertEqual(order("forward"), [0, 1, 2, 3, 0, 1, 2, 3, 0, 1])
        self.assertEqual(order("backward"), [3, 2, 1, 0, 3, 2, 1, 0, 3, 2])
        self.assertEqual(order("bounce"), [0, 1, 2, 3, 2, 1, 0, 1, 2, 3])
        shuffled = ChaseRun(Chase((A,), steps(1, 2, 3, 4), bpm=60, direction="random"), 0.0)
        seen = [shuffled.step_of(i) for i in range(40)]
        for number in range(10):
            self.assertEqual(sorted(seen[number * 4:number * 4 + 4]), [0, 1, 2, 3])  # (each pass plays each step once)
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))  # (never the same step twice in a row)
        self.assertEqual(order("forward", loop=False), [0, 1, 2, 3, 3, 3, 3, 3, 3, 3])  # (once: holds its last step)
        self.assertEqual(order("bounce", loop=False), [0, 1, 2, 3, 2, 1, 0, 0, 0, 0])  # (there and back)

    def test_tap_pause_next_and_speed_keep_its_place(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9"})
        state.play(Chase((A,), steps(1, 2, 3, 4), bpm=60), 0.0)
        run = state.chase
        self.assertAlmostEqual(run.position(2.5), 2.5)
        self.assertIsNone(state.tap(10.0))
        self.assertIsNone(None)
        self.assertAlmostEqual(state.tap(10.5), 120.0)  # (two taps half a second apart: 120 BPM)
        self.assertAlmostEqual(state.tap(11.0), 120.0)
        self.assertAlmostEqual(run.position(11.0) + 1.0, run.position(11.5), places=6)  # (now a step every 0.5 s)
        self.assertIsNone(state.tap(20.0))  # (a long gap starts a new count)
        self.assertIsNone(state.tap(20.0))  # (two in one tick, or a double press: the same tap)
        self.assertIsNone(state.tap(20.03))
        place = run.position(20.0)
        state.pause_chase(True, 20.0)
        self.assertAlmostEqual(run.position(30.0), place)
        state.pause_chase(False, 30.0)
        self.assertAlmostEqual(run.position(30.25), place + 0.5)
        state.next_step(30.25)
        self.assertAlmostEqual(run.position(30.25), int(place + 0.5) + 1)

    def test_step_on_each_beat(self) -> None:
        run = ChaseRun(Chase((A,), steps(1, 2, 3), bpm=60, on_beat=True), 0.0)
        self.assertEqual(run.current(5.0), 0)  # (no beats: it waits)
        for moment, level in ((5.0, 1.0), (5.05, 1.0), (5.2, 0.4), (5.5, 1.0), (5.6, 0.2)):
            run.hear(level, moment)
        self.assertEqual(run.beats, 2)  # (two rises, however long each beat stays up)
        self.assertEqual(run.current(5.6), 2)

    def test_release_stops_the_chase_on_those_lights(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9", B: "slimpar9"})
        state.play(Chase((A, B), (ChaseStep(MappingProxyType({A: (1.0, 0.0, 0.0, 1.0), B: (0.0, 1.0, 0.0, 1.0)})),)), 0.0)
        state.release([A], 1.0, 0.5)
        self.assertEqual(state.chase.lights, [B])
        state.release([B], 1.0, 0.6)
        self.assertIsNone(state.chase)

    def test_a_chase_saves_and_reads_back(self) -> None:
        chase = Chase((A, B), (ChaseStep(MappingProxyType({A: (1.0, 0.5, 0.0, 0.8)})),), bpm=96, seconds=None,
                      crossfade=0.4, direction="bounce", loop=False, on_beat=True)
        again = chase_from_dict(json.loads(json.dumps(chase_to_dict(chase))))
        self.assertEqual(chase_to_dict(again), chase_to_dict(chase))
        self.assertIsNone(chase_from_dict({"lights": [], "steps": []}))
        odd = chase_from_dict({"lights": [A], "steps": [{A: [2, -1, 0.5, 1]}], "bpm": 9999, "direction": "sideways"})
        self.assertEqual((odd.bpm, odd.direction, odd.steps[0].looks[A]), (300.0, "forward", (1.0, 0.0, 0.5, 1.0)))


class EverywhereTests(unittest.TestCase):
    """Release from elsewhere: an automation action (show and global), the API and the phone page."""

    def test_the_chase_from_the_stream_deck(self) -> None:
        engine = FakeEngine(show())
        chase = Chase(lights=(A,), steps=steps(255, 0, 128, 64), bpm=60.0)
        engine.submit(ConsoleChase("play", chase))
        engine.settle(1.5)  # (one step a second: step 2 now)
        self.assertEqual(engine._console.chase.current(engine.clock.t), 1)
        engine.submit(ConsoleChase("speed", bpm=120.0))  # (twice as fast, from the same place)
        self.assertEqual(engine._console.chase.chase.bpm, 120.0)
        self.assertEqual(engine._console.chase.current(engine.clock.t), 1)
        engine.settle(0.5)
        self.assertEqual(engine._console.chase.current(engine.clock.t), 2)
        engine.submit(ConsoleChase("speed", bpm=1.0))
        self.assertEqual(engine._console.chase.chase.bpm, 10.0)  # (the slowest there is)
        engine.submit(ConsoleChase("pause"))
        engine.submit(ConsoleChase("next"))
        self.assertEqual(engine._console.chase.current(engine.clock.t), 3)
        engine.submit(ConsoleRelease(lights=(A,), fade=0.0))
        self.assertIsNone(engine._console.chase)
        engine.submit(ConsoleChase("speed", bpm=90.0))  # (no chase: nothing happens)
        self.assertIsNone(engine._console.chase)

    def test_the_automation_action(self) -> None:
        from centralight.automations import GLOBAL, SHOW, action_command, actions_for, default_action, describe_action
        from centralight.commands import RunAutomation
        from tests.test_automations import automation

        for scope in (SHOW, GLOBAL):
            self.assertIn("release_console", actions_for(scope))
            self.assertEqual(action_command({"do": "release_console"}, show(), scope), ConsoleRelease())
        self.assertEqual(default_action("release_console", show()), {"do": "release_console"})
        self.assertIn("release the Console", describe_action({"do": "release_console"}, show()))
        end = automation("end-of-service", [{"type": "days", "days": ["sun"]}], [{"do": "release_console"}])
        engine = FakeEngine(show().with_automations({end.id: end}, (end.id,)))
        engine.submit(ConsoleSet({A: RED}))
        engine.submit(RunAutomation(end.id))
        engine.settle(2.1)  # (Settings' release fade)
        self.assertFalse(engine._console.active)

    def test_the_api_and_the_phone_page(self) -> None:
        from centralight.remote import RemoteServer, command_for, command_from_json, help_page, state_of

        self.assertEqual(command_for(["live", "release"], {}, show()), ConsoleRelease())
        self.assertEqual(command_for(["live", "release"], {"fade": "1"}, show()), ConsoleRelease(fade=1.0))
        self.assertEqual(command_from_json({"op": "live", "state": "release"}, show()), ConsoleRelease())
        self.assertEqual(command_for(["live", "release"], {"lights": f"{A}, {B},{A}"}, show()), ConsoleRelease(lights=(A, B)))
        self.assertEqual(command_from_json({"op": "live", "state": "release", "lights": [B]}, show()),
                         ConsoleRelease(lights=(B,)))
        for action in ("tap", "next", "pause", "resume"):
            self.assertEqual(command_for(["live", action], {}, show()), ConsoleChase(action))
        self.assertEqual(command_for(["live", "speed"], {"bpm": "90"}, show()), ConsoleChase("speed", bpm=90.0))
        self.assertEqual(command_for(["live", "speed"], {"bpm": "9000"}, show()).bpm, 300.0)  # (kept to 10-300)
        self.assertEqual(command_from_json({"op": "live", "state": "speed", "bpm": 75}, show()), ConsoleChase("speed", bpm=75.0))
        from centralight.remote import BadRequest
        for parts, params, status in ((["live", "speed"], {}, 400), (["live", "release"], {"lights": "nobody"}, 404),
                                      (["live", "dance"], {}, 404), (["live", "release"], {"lights": " , "}, 400)):
            with self.assertRaises(BadRequest) as caught:
                command_for(parts, params, show())
            self.assertEqual(caught.exception.status, status, parts)
        engine = FakeEngine(show())
        engine.submit(ConsoleSet({A: RED, B: {"dimmer": 10}}))
        engine.settle(0.3)
        self.assertEqual(state_of(engine, [])["console"], {"held": 2, "lights": [A, B]})
        page = help_page(RemoteServer(engine, port=0))
        self.assertIn('id="console" data-path="/api/live/release"', page)


class PageLogicTests(unittest.TestCase):
    """What the page works out for itself: choosing lights, fans, the quick chases and Add step."""

    LIGHTS = ["l1", "l2", "l3", "l4", "l5"]

    def test_choosing(self) -> None:
        from centralight.console import choose

        self.assertEqual(choose("all", self.LIGHTS, ["l3"]), self.LIGHTS)
        self.assertEqual(choose("none", self.LIGHTS, ["l3"]), [])
        self.assertEqual(choose("invert", self.LIGHTS, ["l2", "l4"]), ["l1", "l3", "l5"])
        self.assertEqual(choose("odd", self.LIGHTS, []), ["l1", "l3", "l5"])  # (none chosen: of all of them)
        self.assertEqual(choose("even", self.LIGHTS, ["l5", "l3", "l1", "l2"]), ["l3", "l2"])  # (of the chosen, in order)

    def test_fans(self) -> None:
        from centralight.console import fan

        rainbow = fan(["a", "b", "c"], "rainbow")
        self.assertEqual(rainbow["a"], {"red": 255.0, "green": 0.0, "blue": 0.0})
        self.assertEqual(rainbow["b"], {"red": 0.0, "green": 255.0, "blue": 0.0})
        across = fan(["a", "b", "c"], "colours", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertEqual(across["b"], {"red": 127.5, "green": 0.0, "blue": 127.5})
        levels = fan(["a", "b", "c", "d", "e"], "brightness", low=0.2, high=1.0)
        self.assertEqual([levels[k]["dimmer"] for k in "abcde"], [51.0, 102.0, 153.0, 204.0, 255.0])
        self.assertEqual(fan(["a"], "colours", (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)), {"a": {"red": 0.0, "green": 255.0, "blue": 0.0}})

    def test_quick_chases(self) -> None:
        from centralight.console import colours_across, odd_even, one_at_a_time

        red, blue = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
        across = colours_across(["a", "b", "c"], [red, blue])
        self.assertEqual(len(across), 2)
        self.assertEqual([across[0].looks[k][:3] for k in "abc"], [red, blue, red])
        self.assertEqual([across[1].looks[k][:3] for k in "abc"], [blue, red, blue])  # (moved on one light)
        running = one_at_a_time(["a", "b", "c"], red)
        self.assertEqual([[step.looks[k][:3] == red for k in "abc"] for step in running],
                         [[True, False, False], [False, True, False], [False, False, True]])
        flip = odd_even(["a", "b", "c", "d"], red)
        self.assertEqual([[step.looks[k][:3] == red for k in "abcd"] for step in flip],
                         [[True, False, True, False], [False, True, False, True]])

    def test_add_step_takes_master_back_out(self) -> None:
        from centralight.console import step_from

        values = {A: (128, 64, 0, 0, 0, 0, 0, 200, 0)}
        step = step_from([A, B], values, {A: "slimpar9", B: "slimpar9"}, master=0.5)
        self.assertEqual(set(step.looks), {A})
        self.assertAlmostEqual(step.looks[A][0], 1.0, places=2)
        self.assertAlmostEqual(step.looks[A][1], 0.502, places=2)
        self.assertAlmostEqual(step.looks[A][3], 200 / 255, places=3)


class GroupsAndColoursTests(unittest.TestCase):
    def test_saved_with_the_show(self) -> None:
        from centralight.show_edit import add_saved_colour, remove_lights, remove_saved_colour, save_group
        from centralight.show_io import show_from_dict, show_to_dict

        base = show()
        changed = save_group(base, "  Front  row ", [A, B, "dimmer-1", "nope"])
        self.assertEqual([(g.name, g.lights) for g in changed.groups], [("Front row", (A, B))])  # (colour lights only)
        changed = save_group(changed, "FRONT ROW", [B])
        self.assertEqual([(g.name, g.lights) for g in changed.groups], [("FRONT ROW", (B,))])  # (same name: replaced)
        self.assertIs(save_group(changed, "Empty", ["dimmer-1"]), changed)
        changed = add_saved_colour(add_saved_colour(add_saved_colour(changed, "#ff9a3c"), "#00D8FF"), "#FF9A3C")
        self.assertEqual(changed.saved_colours, ("#00D8FF", "#FF9A3C"))  # (again: moves to the end)
        self.assertIs(add_saved_colour(changed, "orange"), changed)
        again, warnings = show_from_dict(json.loads(json.dumps(show_to_dict(changed))))
        self.assertEqual((again.groups, again.saved_colours, warnings), (changed.groups, changed.saved_colours, []))
        self.assertNotIn("groups", show_to_dict(base))  # (none: left out, so the file is as before)
        self.assertEqual(remove_saved_colour(changed, "#00d8ff").saved_colours, ("#FF9A3C",))
        self.assertEqual(remove_lights(changed, {B}).groups, ())  # (its only light gone: the group goes)
        odd, warnings = show_from_dict({**show_to_dict(base), "groups": [{"name": "", "lights": [A]}, {"name": "x", "lights": "a"}],
                                        "saved_colours": ["#12345", 7, "#abcdef"]})
        self.assertEqual((odd.groups, odd.saved_colours), ((), ("#ABCDEF",)))
        self.assertTrue(warnings)


class EncodeTests(unittest.TestCase):
    def test_the_other_channels(self) -> None:
        fixtures = show_from_patch().fixtures
        plain = encode_dmx(fixtures, {A: (1.0, 0.5, 0.0)}, {})
        self.assertEqual(encode_dmx(fixtures, {A: (1.0, 0.5, 0.0)}, {}, extras={}), plain)
        more = encode_dmx(fixtures, {A: (1.0, 0.5, 0.0)}, {}, extras={A: {"amber": 77, "strobe": 200, "dimmer": 128, "nope": 9}})
        self.assertEqual(tuple(more[4:13]), (255, 128, 0, 77, 0, 200, 0, 128, 0))
        self.assertEqual(more[13:], plain[13:])


class GrowthTests(unittest.TestCase):
    """Hours of use in a moment: thousands of random holds, releases and chase changes leave nothing behind."""

    def test_many_changes_leave_nothing_behind(self) -> None:
        import random

        from centralight.console import CHANNELS, TAPS_KEPT

        lights = [f"slimpar-{n}" for n in range(101, 107)] + ["dimmer-1"]
        state = ConsoleState()
        state.set_lights({light: "slimpar9" for light in lights[:-1]} | {"dimmer-1": "dimmer"})
        chance = random.Random(15)
        now = 0.0
        for number in range(6000):
            now += chance.choice((0.0, 0.01, 0.025, 0.1, 0.5))
            pick = chance.random()
            some = chance.sample(lights, chance.randint(1, len(lights)))
            if pick < 0.3:
                values = {light: {attr: chance.uniform(-20, 300) for attr in chance.sample(CHANNELS["slimpar9"], 3)}
                          for light in some}
                state.set(values | {"nobody": {"red": 9}}, chance.choice((0.0, 0.5, 2.0)), now)
            elif pick < 0.45:
                state.release(some if chance.random() < 0.7 else None, chance.choice((0.0, 1.0, 2.0)), now,
                              only=("red", "strobe") if chance.random() < 0.2 else None)
            elif pick < 0.55:
                state.play(Chase(tuple(some), steps(*(chance.randint(0, 255) for _ in range(3)), light=some[0]),
                                 bpm=chance.uniform(10, 300), crossfade=chance.random(),
                                 direction=chance.choice(("forward", "backward", "bounce", "random")),
                                 loop=chance.random() < 0.8, on_beat=chance.random() < 0.2), now)
            elif pick < 0.62 and state.chase is not None:
                state.update_chase(replace(state.chase.chase, bpm=chance.uniform(10, 300)), now)
            elif pick < 0.7:
                state.pause_chase(chance.random() < 0.5, now)
            elif pick < 0.75:
                state.next_step(now)
            elif pick < 0.82:
                state.tap(now)
            elif pick < 0.86:
                state.set_speed(chance.uniform(0, 400), now)
            else:
                state.hear(chance.random(), now)
            for light in lights:
                rgb, raw = state.merge(light, (chance.random(), chance.random(), chance.random()), now)
                self.assertTrue(all(0.0 <= c <= 1.0 for c in rgb), (number, light, rgb))
                self.assertTrue(all(0 <= v <= 255 for v in raw.values()), (number, light, raw))
            self.assertLessEqual(len(state.held), len(lights))
            self.assertLessEqual(len(state.releasing), len(lights))
            self.assertLessEqual(len(state._taps), TAPS_KEPT)
            self.assertLessEqual(len(state._last), len(lights))
            self.assertTrue(all(len(attrs) <= 9 for attrs in [*state.held.values(), *state.releasing.values()]))
        state.release(None, 1.0, now)
        for _ in range(4):  # (the slowest fade back still going was 2 s)
            now += 0.6
            for light in lights:
                state.merge(light, (0.0, 0.0, 0.0), now)
        self.assertFalse(state.active, (state.held, state.releasing, state.chase and state.chase.lights))  # (all faded back)
        self.assertEqual((state.held, state.releasing), ({}, {}))
        json.dumps(state.to_dict(now))
        state.set({"dimmer-1": {"dimmer": 50}}, 0.0, now)
        state.set_lights({"dimmer-1": "slimpar9"})  # (another show: the same light now has colours too)
        self.assertEqual(state.attributes("dimmer-1")[:3], ("red", "green", "blue"))
        state.play(Chase(("dimmer-1",), steps(10, 20, light="dimmer-1")), now)
        state.set_lights({"dimmer-1": "dimmer"})  # (and back: it keeps only what a dimmer has)
        state.release(None, 0.5, now)
        self.assertEqual(set(state.releasing.get("dimmer-1", {})), {"dimmer"})


class ReviewTests(unittest.TestCase):
    """The first review of the engine side (5 Nov): each finding, fixed."""

    def given(self, engine, light: str = A) -> float:
        """What the light gives: its brightest colour channel times its dimmer (0-255)."""
        c = chans(engine, light)
        return max(c[:3]) * c[7] / 255

    def test_a_dimmed_light_never_flares(self) -> None:
        engine = FakeEngine(show())  # (nothing on Base: the inputs are black)
        engine.submit(ConsoleSet({A: {**RED, "dimmer": 20}}, 2.0))  # (from black, faded in)
        seen = []
        for _ in range(90):
            engine.settle(0.025)
            seen.append(self.given(engine))
        self.assertLessEqual(max(seen), 21)
        self.assertTrue(all(b >= a - 1 for a, b in zip(seen, seen[1:])))  # (only ever brighter)
        engine.submit(ConsoleRelease((A,), 2.0))
        seen = []
        for _ in range(90):
            engine.settle(0.025)
            seen.append(self.given(engine))
        self.assertLessEqual(max(seen), 21)
        self.assertTrue(all(b <= a + 1 for a, b in zip(seen, seen[1:])))  # (only ever darker)
        self.assertEqual(seen[-1], 0)
        engine.submit(ConsoleSet({A: {**RED, "dimmer": 51}}))
        engine.settle(0.1)
        engine.submit(ToBlack(2.0))
        peak = 0.0
        for _ in range(90):
            engine.settle(0.025)
            peak = max(peak, self.given(engine))
        self.assertLessEqual(peak, 52)

    def test_a_chase_crossfade_never_flares(self) -> None:
        from centralight.console import chase_look

        chase = Chase((A,), (ChaseStep(MappingProxyType({A: (0.2, 0.0, 0.0, 1.0)})),
                             ChaseStep(MappingProxyType({A: (1.0, 0.0, 0.0, 0.2)}))), bpm=60, crossfade=1.0)
        given = [chase_look(chase, A, 1.0 + i / 20, given=True)[0] for i in range(20)]
        self.assertAlmostEqual(max(given), 0.2, places=6)  # (both steps give 0.2: so does the way between)
        self.assertAlmostEqual(chase_look(chase, A, 1.5)[3], 0.6)  # (and the brightness itself goes straight)

    def test_cut_to_black_also_cuts_a_light_fading_back(self) -> None:
        engine = FakeEngine(show())
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.release_fade = 10.0
        engine.submit(ConsoleSet({A: {"red": 0, "green": 0, "blue": 255}}))
        engine.settle(0.1)
        engine.submit(ConsoleRelease((A,)))  # (10 s back to the input)
        engine.settle(0.5)
        engine.submit(ToBlack(0.0))
        engine.settle(0.05)
        self.assertEqual(chans(engine)[:3], (0, 0, 0))
        self.assertFalse(engine._console.active)
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.submit(ConsoleSet({A: {"red": 255}}))
        engine.settle(0.1)
        engine.submit(ConsoleRelease((A,)))
        engine.settle(0.5)
        engine.submit(ToBlack(1.0))  # (a fade to black: a 10 s fade back ends with it)
        engine.settle(1.1)
        self.assertEqual(chans(engine)[:3], (0, 0, 0))

    def test_next_while_paused_always_moves_on(self) -> None:
        run = ChaseRun(Chase((A,), steps(10, 20, 30, 40, 50), bpm=97.0), 118_608.13)
        run.set_paused(True, 118_610.77)
        seen = [run.current(118_620.0)]
        for _ in range(8):
            run.next_step(118_620.0)
            seen.append(run.current(118_620.0))
        self.assertEqual(seen, [(seen[0] + n) % 5 for n in range(9)])

    def test_a_fade_starts_from_what_the_light_shows_now(self) -> None:
        engine = FakeEngine(show())
        engine.submit(SendToBase("warm-white", cut=True))
        engine.submit(ConsoleSet({A: RED}))
        engine.settle(0.1)
        engine.submit(ConsoleRelease((A,), 0.0))
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.settle(0.5)
        before = chans(engine)[0]
        engine.submit(ConsoleSet({A: {"red": 0}}, 4.0))
        engine.settle(0.05)
        self.assertLessEqual(chans(engine)[0], before + 2)  # (from the blue input's red, not the old warm white)

    def test_a_reopen_keeps_live_changes_over_the_chase(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9", B: "slimpar9"})
        state.play(Chase((A, B), (ChaseStep(MappingProxyType({A: (0.0, 1.0, 0.0, 1.0), B: (0.0, 1.0, 0.0, 1.0)})),)), 0.0)
        state.set({A: RED}, 0.0, 1.0)  # (later than the chase: it wins on A)
        saved = json.loads(json.dumps(state.to_dict(2.0)))
        again = ConsoleState()
        again.set_lights({A: "slimpar9", B: "slimpar9"})
        again.restore(saved, 50.0)
        self.assertEqual(again.merge(A, (0.0, 0.0, 0.0), 50.0)[0], (1.0, 0.0, 0.0))
        self.assertEqual(again.merge(B, (0.0, 0.0, 0.0), 50.0)[0], (0.0, 1.0, 0.0))

    def test_a_beat_counts_however_the_ticks_fall(self) -> None:
        run = ChaseRun(Chase((A,), steps(1, 2, 3), bpm=120, on_beat=True), 0.0)
        # (audio.beat as a 40 Hz tick reads it: the 1.0 itself is often missed, only its way down seen)
        for moment, level in ((0.0, 0.1), (0.025, 0.84), (0.05, 0.69), (0.5, 0.15), (0.525, 1.0), (0.55, 0.81),
                              (1.0, 0.1), (1.025, 0.71), (1.05, 0.58)):
            run.hear(level, moment)
        self.assertEqual(run.beats, 3)

    def test_a_dimmer_as_a_colour_light(self) -> None:
        state = ConsoleState()
        state.set_lights({"dim": "dimmer"})
        self.assertEqual(state.merge("dim", (0.5, 0.2, 0.1), 0.0), ((0.5, 0.5, 0.5), {}))  # (as the inputs give it)
        state.set({"dim": {"dimmer": 255}}, 0.0, 1.0)
        self.assertEqual(state.merge("dim", (0.0, 0.0, 0.0), 1.0), ((1.0, 1.0, 1.0), {}))  # (full, as Master allows)
        state.set({"dim": {"dimmer": 51}}, 2.0, 2.0)
        self.assertAlmostEqual(state.merge("dim", (0.0, 0.0, 0.0), 2.0)[0][0], 1.0)  # (fades from where it was)
        state.merge("dim", (0.0, 0.0, 0.0), 4.5)  # (the engine works it out every tick)
        state.release(["dim"], 1.0, 5.0)
        self.assertAlmostEqual(state.merge("dim", (0.0, 0.0, 0.0), 5.5)[0][0], 0.1, places=2)  # (halfway down to 0)
        state.play(Chase(("dim",), (ChaseStep(MappingProxyType({"dim": (1.0, 0.5, 0.0, 0.5)})),)), 6.0)
        self.assertAlmostEqual(state.merge("dim", (0.0, 0.0, 0.0), 6.0)[0][0], 0.5)  # (as the input would light it)

    def test_the_consoles_lights_are_not_black(self) -> None:
        engine = FakeEngine(show())
        self.assertTrue(engine.colour_black())
        engine.submit(ConsoleSet({A: RED}))
        engine.settle(0.05)
        self.assertFalse(engine.colour_black())
        engine.submit(ConsoleRelease(fade=0.0))
        engine.settle(0.05)
        self.assertTrue(engine.colour_black())

    def test_lights_a_new_chase_leaves_fade_back(self) -> None:
        engine = FakeEngine(show())
        engine.submit(SendToBase("warm-white", cut=True))
        engine.settle(0.1)
        warm = chans(engine)[:3]
        engine.submit(ConsoleChase("play", Chase((A,), steps(255))))
        engine.settle(0.1)
        engine.submit(ConsoleChase("play", Chase((B,), steps(255, light=B))))
        engine.settle(1.0)  # (halfway through the 2 s release fade)
        self.assertNotEqual(chans(engine)[:3], (255, 0, 0))
        self.assertNotEqual(chans(engine)[:3], warm)
        engine.settle(1.1)
        self.assertEqual(chans(engine)[:3], warm)

    def test_stop_keeps_what_live_changes_hold(self) -> None:
        engine = FakeEngine(show())
        engine.submit(ConsoleChase("play", Chase((A,), steps(255))))
        engine.submit(ConsoleSet({A: {"amber": 200}}))
        engine.submit(ConsoleChase("stop"))
        engine.settle(2.1)
        self.assertIsNone(engine._console.chase)
        self.assertEqual(chans(engine)[3], 200)  # (amber, held by a Live change, stays)

    def test_switching_step_on_beat_on_keeps_its_place(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9"})
        chase = Chase((A,), steps(10, 20, 30, 40), bpm=60)
        state.play(chase, 0.0)
        state.update_chase(replace(chase, on_beat=True), 2.5)
        self.assertEqual(state.chase.current(3.0), 2)

    def test_a_broken_live_json_never_stops_a_start(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9"})
        chase = chase_to_dict(Chase((A,), steps(10, 20)))
        state.restore({"held": {A: {"red": 10 ** 400, "green": 40}},
                       "chase": {"chase": {**chase, "bpm": 10 ** 400, "steps": [{A: [10 ** 400, 0, 0, 1]}]},
                                 "position": 1e308}}, 5.0)
        rgb, _raw = state.merge(A, (0.0, 0.0, 0.0), 6.0)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in rgb))
        engine = FakeEngine(show(), live=LiveState(console={"held": {A: "nonsense"}, "chase": [1, 2]}))
        engine.settle(0.05)  # (it started)
        self.assertFalse(engine._console.active)


class FinalReviewTests(unittest.TestCase):
    """The last review (5 Nov): the fade fixes made right, and the rest."""

    def given(self, engine, light: str = A) -> float:
        c = chans(engine, light)
        return max(c[:3]) * c[7] / 255

    def test_a_fade_cut_short_never_jumps(self) -> None:
        engine = FakeEngine(show())
        engine.submit(SendToBase("warm-white", cut=True))
        engine.settle(0.1)
        engine.submit(ConsoleSet({A: {"dimmer": 102}}, 5.0))  # (a brightness fan with a Fade)
        engine.settle(3.0)
        engine.submit(ToBlack(3.0))  # (Fade to black, part-way through)
        seen = [self.given(engine)]
        for _ in range(130):
            engine.settle(0.025)
            seen.append(self.given(engine))
        self.assertLess(max(abs(b - a) for a, b in zip(seen, seen[1:])), 12)  # (smooth: no flash or dip)
        self.assertEqual(seen[-1], 0)
        engine.submit(SendToBase("warm-white", cut=True))
        engine.submit(ConsoleSet({A: {"red": 255, "green": 0, "blue": 0, "dimmer": 51}}, 0.0))
        engine.settle(0.1)
        engine.submit(ConsoleRelease((A,), 2.0))
        engine.settle(0.025)
        self.assertLess(chans(engine)[7], 60)  # (the dimmer itself fades: it doesn't jump to full and back)

    def test_a_live_colour_over_a_breathing_chase_keeps_it_breathing(self) -> None:
        engine = FakeEngine(show())
        white = (1.0, 1.0, 1.0)
        chase = Chase((A,), (look_of(A, white, 1.0), look_of(A, white, 0.3)), bpm=60, crossfade=1.0)
        engine.submit(ConsoleChase("play", chase))
        engine.submit(ConsoleSet({A: {"red": 0, "green": 0, "blue": 255}}))  # (a saved colour clicked: later)
        dimmers = []
        for _ in range(100):
            engine.settle(0.025)
            dimmers.append(chans(engine)[7])
        self.assertGreater(max(dimmers) - min(dimmers), 120)  # (still breathing: the chase's brightness)
        self.assertEqual(chans(engine)[:3], (0, 0, 255))

    def test_stop_fades_to_what_live_held_before(self) -> None:
        engine = FakeEngine(show())
        engine.submit(ConsoleSet({A: {"red": 0, "green": 0, "blue": 255}}))  # (Live blue)
        engine.submit(ConsoleChase("play", Chase((A,), steps(255))))  # (a red chase, later: it wins)
        engine.settle(0.1)
        self.assertEqual(chans(engine)[:3], (255, 0, 0))
        engine.submit(ConsoleChase("stop"))
        engine.settle(1.0)
        middle = chans(engine)[:3]
        self.assertTrue(60 < middle[0] < 200 and 60 < middle[2] < 200, middle)  # (fading, not snapped)
        engine.settle(1.2)
        self.assertEqual(chans(engine)[:3], (0, 0, 255))

    def test_cancel_after_a_show_change_holds_nothing_new(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9", B: "slimpar9"})
        state.keep([A])
        state.set_lights({B: "slimpar9"})  # (another show, without A)
        state.put_back(1.0)
        self.assertFalse(state.active)

    def test_a_chase_left_on_no_lights_stops(self) -> None:
        state = ConsoleState()
        state.set_lights({A: "slimpar9", B: "slimpar9"})
        state.play(Chase((A, B), steps(10, 20)), 0.0)
        state.release([B], 0.0, 0.5)
        state.update_chase(Chase((B,), steps(30, 40, light=B)), 1.0)  # (a quick chase on B only: B was let go)
        self.assertIsNone(state.chase)
        again = ConsoleState()
        again.set_lights({A: "slimpar9"})
        again.restore({"chase": {"chase": chase_to_dict(Chase((A,), steps(10, 20))), "lights": [], "position": 1}}, 5.0)
        self.assertIsNone(again.chase)  # (it was on none of its lights: nothing to carry on)

    def test_a_dimmer_as_a_colour_light_fades_from_what_it_shows(self) -> None:
        import numpy as np

        state = ConsoleState()
        state.set_lights({"dim": "dimmer"})
        state.note_inputs(("dim",), np.zeros((1, 3), dtype=np.float32))  # (black from the inputs)
        state.set({"dim": {"dimmer": 128}}, 2.0, 0.0)
        self.assertLess(state.merge("dim", (0.0, 0.0, 0.0), 0.05)[0][0], 0.05)  # (from black, not from full)

    def test_steps_that_arent_a_list(self) -> None:
        from centralight.show_import import _lights_used

        inp = with_inputs(show_from_patch()).inputs["warm-white"]
        odd = replace(inp, tracks=(({"id": "E1", "kind": "lights", "steps": 5},),) + inp.tracks[1:])
        self.assertIsInstance(_lights_used(odd), set)  # (no error)


def look_of(light: str, rgb: tuple, level: float) -> ChaseStep:
    return ChaseStep(MappingProxyType({light: (*rgb, level)}))


if __name__ == "__main__":
    unittest.main()
