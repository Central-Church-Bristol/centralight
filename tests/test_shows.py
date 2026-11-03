"""M12: shows and global automations.

- Shows are folders: listing, finding, New, Duplicate, Rename and Delete (to the Recycle Bin, never the loaded show).
- Loading a show never triggers an input: the lights freeze on what they show (Base and the overlays as one still look
  on Base) until one of the new show's inputs is sent, which transitions from it. House lights and Master keep their
  levels; the new show's variables come back as they were; its automations are checked straight away ("When this
  show loads").
- Start-up: the PC starting gives black with nothing playing; reopening resumes, a held look included.
- Global automations: app-wide, checked before the show's; Load a show (the show's automations wait for it), house
  lights by name, "This show is loaded"; they can't touch a show's inputs.
- The window: the show picker, a history per show, a change meant for another show refused, the two automation lists,
  importing a layout from another show.
- The API: /api/show/<id or name>, /api/shows, /api/global-automation/<id>/..., "not in this show".
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock

from centralight.automations import action_command, check_action, check_condition
from centralight.commands import (
    LoadShow,
    RequestShow,
    RunAutomation,
    SendToBase,
    SendToOverlay,
    SetAutomationEnabled,
    SetHouse,
    SetMaster,
    SetOverlayOpacity,
    SetVariable,
    ShowLoadFailed,
)
from centralight.engine import Engine
from centralight.global_automations import GLOBAL_DIR, ORDER_FILE, load_global_automations
from centralight.layouts import import_summary, save_layout_as
from centralight.live_state import HouseSetting, LiveState
from centralight.model import Automation, GlobalVar, Input, MapSpot, Show
from centralight.new_show import create_show, show_from_patch
from centralight.show_io import SHOW_FILE, ShowError, automation_from_dict, automation_to_dict, freeze, load_show, save_whole_show
from centralight.shows import delete_show, duplicate_show, find_show, list_shows, new_folder, new_show, unique_name
from tests.fakes import FakeOutput
from tests.test_automations import Clocks, automation, uk_local

RED, GREEN, BLUE = "#FF0000", "#00FF00", "#0000FF"


def solid(input_id: str, colour: str, seconds: float = 1.0) -> Input:
    from centralight.model import Transition

    return Input(id=input_id, name=input_id.title(), transition=Transition("fade", seconds),
                 tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": colour}}], [], [], []]))


def a_show(name: str, folder: str, inputs: list[Input], automations: list[Automation] = (), variables=()) -> Show:
    show = show_from_patch(name=name).with_inputs({i.id: i for i in inputs}, tuple(i.id for i in inputs))
    show = show.with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))
    return replace(show, folder=folder, globals=tuple(variables))


def globals_of(*automations: Automation) -> Show:
    return Show(name="Global automations").with_automations({a.id: a for a in automations}, tuple(a.id for a in automations))


MORNING = "C:/Shows/Morning"
EVENING = "C:/Shows/Evening"


class EngineCase(unittest.TestCase):
    def make(self, show: Show, live: LiveState | None = None, uptime: float = 86400.0, global_automations: Show | None = None,
             start: datetime = datetime(2026, 11, 8, 10, 0)) -> Engine:
        """uptime: how long Windows has been running (under 10 minutes: the PC has just started)."""
        self.clocks = Clocks(start)
        self.clocks.uptime_value = uptime
        self.engine = Engine(show, FakeOutput(), live, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                             local_time=uk_local, uptime=lambda: self.clocks.uptime_value, global_automations=global_automations)
        self.engine.tick()
        return self.engine

    def run_for(self, seconds: float, step: float = 0.025) -> None:
        for _ in range(max(1, int(round(seconds / step)))):
            self.clocks.advance(step)
            self.engine.tick()

    def send(self, command) -> None:
        self.engine.submit(command)
        self.run_for(0.025)

    def frame(self):
        self.engine._publish(self.clocks.mono, *self._out())
        return self.engine.frames.get()

    def _out(self):
        frame = self.engine.frames.get()
        return dict(frame.colours), {fid: v.out for fid, v in frame.house.items()}

    def colours(self) -> dict:
        return dict(self.engine.frames.get().colours)


class LoadingAShowTests(EngineCase):
    def morning_and_evening(self):
        morning = a_show("Morning", MORNING, [solid("red", RED), solid("blue", BLUE)])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)])
        return morning, evening

    def test_the_lights_freeze_until_one_of_its_inputs_is_sent(self) -> None:
        morning, evening = self.morning_and_evening()
        self.make(morning)
        self.send(SendToBase("red", cut=True))
        self.send(SendToOverlay(1, "blue", "on"))
        self.send(SetOverlayOpacity(1, 0.5, 0.0))
        self.send(SetHouse("dimmer-4", level=0.6, on=True))
        self.send(SetMaster(0.8, 0.0))
        self.run_for(1.5)  # (the overlay's reveal)
        before = self.colours()
        light = next(iter(before))
        self.assertGreater(before[light][0], 0.1)
        self.assertGreater(before[light][2], 0.1)  # red and blue, mixed by the overlay
        self.send(LoadShow(evening))
        self.run_for(0.5)
        frame = self.engine.frames.get()
        self.assertIs(self.engine.show, evening)
        for light_id, rgb in before.items():  # the same colours, held still
            for got, wanted in zip(frame.colours[light_id], rgb):
                self.assertAlmostEqual(got, wanted, places=4)
        self.assertEqual(frame.base, "frozen (from Morning)")
        self.assertEqual(frame.frozen_from, "Morning")
        self.assertFalse(any(o.on for o in frame.overlays))  # the overlays were folded into the still look
        self.assertFalse(frame.colour_black)
        self.assertTrue(frame.house["dimmer-4"].on)  # the house lights and Master keep their levels
        self.assertAlmostEqual(frame.house["dimmer-4"].level, 0.6, places=3)
        self.assertAlmostEqual(frame.master, 0.8, places=3)
        self.send(SendToBase("green"))  # its own transition (a 1 s fade) from the frozen colours
        self.run_for(0.5)
        middle = self.colours()[light]
        self.assertTrue(0.1 < middle[1] < 0.7 and middle[0] < before[light][0])
        self.run_for(1.0)
        self.assertEqual(self.engine.frames.get().base, "Green")
        r, g, b = self.colours()[light]
        self.assertAlmostEqual(g, 0.8, places=3)  # (Master 80 %)
        self.assertAlmostEqual(r + b, 0.0, places=3)

    def test_a_light_in_both_shows_keeps_its_colour_and_a_new_one_is_unlit(self) -> None:
        morning, evening = self.morning_and_evening()
        fixtures = [fx for fx in evening.fixtures if fx.id != "slimpar-108"]
        template = next(fx for fx in fixtures if fx.id == "slimpar-101")
        fixtures.append(replace(template, id="slimpar-201", name="SlimPAR 201", address=200, map=MapSpot(0.5, 0.5)))
        evening = replace(evening, fixtures=tuple(fixtures))
        self.make(morning)
        self.send(SendToBase("red", cut=True))
        self.send(LoadShow(evening))
        self.run_for(0.1)
        colours = self.colours()
        self.assertNotIn("slimpar-108", colours)
        self.assertAlmostEqual(colours["slimpar-101"][0], 1.0, places=3)  # still red
        self.assertEqual(colours["slimpar-201"], (0.0, 0.0, 0.0))  # new here: unlit

    def test_loading_while_black_stays_black(self) -> None:
        morning, evening = self.morning_and_evening()
        self.make(morning)
        self.send(LoadShow(evening))
        frame = self.engine.frames.get()
        self.assertEqual(frame.base, "Black")
        self.assertTrue(frame.colour_black)

    def test_the_show_variables_come_back_as_they_were(self) -> None:
        speed = GlobalVar("speed", "number", 0.3, 0.0, 2.0)
        morning = a_show("Morning", MORNING, [solid("red", RED)], variables=[speed])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)], variables=[speed])
        self.make(morning)
        self.send(SetVariable("speed", 1.5))
        self.send(LoadShow(evening))
        self.assertAlmostEqual(self.engine._globals["speed"], 0.3)  # Evening's own: its starting value
        self.send(SetVariable("speed", 0.9))
        self.send(LoadShow(morning))
        self.assertAlmostEqual(self.engine._globals["speed"], 1.5)  # Morning's, as it was
        live = LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))
        self.assertEqual(list(live.show_globals.values()), [{"speed": 0.9}])  # (Evening's, kept for next time)

    def test_its_automations_are_checked_when_it_loads(self) -> None:
        on_load = automation("on-load", [{"type": "show_loads"}], [{"do": "base", "input": "green", "transition": "cut"}])
        sunday = automation("sunday", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                            [{"do": "house", "light": "dimmer-1", "level": 0.5}])
        old = automation("old", [{"type": "time", "from": "09:00", "to": "12:30"}], [{"do": "master", "level": 0.2}])
        morning = a_show("Morning", MORNING, [solid("red", RED)], [old])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)], [on_load, sunday])
        self.make(morning)  # Sunday 10:00
        self.assertAlmostEqual(self.engine._master.target, 0.2)  # (Morning's own ran)
        self.send(SetMaster(1.0, 0.0))
        self.send(LoadShow(evening))
        self.run_for(0.1)
        self.assertEqual(self.engine.frames.get().base, "Green")  # "When this show loads"
        self.assertAlmostEqual(self.engine._house["dimmer-1"].level.target, 0.5)  # Sunday 09:00-12:30, caught up
        self.assertAlmostEqual(self.engine._master.target, 1.0)  # Morning's automations don't run any more
        self.assertEqual([h.automation for h in self.engine._runner.history], ["on-load", "sunday"])

    def test_the_same_show_again_is_just_its_latest_version(self) -> None:
        morning, _evening = self.morning_and_evening()
        self.make(morning)
        self.send(SendToBase("red", cut=True))
        self.send(LoadShow(replace(morning, name="Morning (renamed)")))
        self.assertEqual(self.engine.frames.get().base, "Red")  # nothing froze


class StartingTests(EngineCase):
    def live(self) -> LiveState:
        return LiveState(show=MORNING, base={"input": "red", "time": 12.0}, house={"dimmer-4": HouseSetting(0.6, True)},
                         master=0.9)

    def test_the_pc_starting_starts_black_and_runs_when_this_show_loads(self) -> None:
        loads = automation("loads", [{"type": "show_loads"}], [{"do": "house", "light": "dimmer-1", "level": 0.3}])
        morning = a_show("Morning", MORNING, [solid("red", RED)], [loads])
        self.make(morning, self.live(), uptime=120.0)
        frame = self.engine.frames.get()
        self.assertEqual(frame.base, "Black")  # nothing playing
        self.assertFalse(frame.house["dimmer-4"].on)  # every light black: the house lights' switches off...
        self.assertAlmostEqual(frame.house["dimmer-4"].level, 0.6, places=3)  # ...their levels kept
        self.assertAlmostEqual(frame.master, 0.9, places=3)
        self.assertEqual([h.automation for h in self.engine._runner.history], ["loads"])

    def test_reopening_resumes_and_doesnt_run_when_this_show_loads(self) -> None:
        loads = automation("loads", [{"type": "show_loads"}], [{"do": "house", "light": "dimmer-1", "level": 0.3}])
        morning = a_show("Morning", MORNING, [solid("red", RED)], [loads])
        self.make(morning, self.live(), uptime=86400.0)
        frame = self.engine.frames.get()
        self.assertEqual(frame.base, "Red")
        self.assertTrue(frame.house["dimmer-4"].on)
        self.assertEqual(list(self.engine._runner.history), [])  # a crash restart doesn't change the lights

    def test_a_held_look_comes_back_when_it_reopens(self) -> None:
        morning = a_show("Morning", MORNING, [solid("red", RED)])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)])
        self.make(morning)
        self.send(SendToBase("red", cut=True))
        self.send(LoadShow(evening))
        before = self.colours()
        live = LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))
        self.assertEqual(live.show, EVENING)
        self.assertEqual(live.frozen["from"], "Morning")
        self.make(evening, live)  # CentraLight reopens (no PC restart)
        frame = self.engine.frames.get()
        self.assertEqual(frame.base, "frozen (from Morning)")
        for light_id, rgb in before.items():
            for got, wanted in zip(frame.colours[light_id], rgb):
                self.assertAlmostEqual(got, wanted, places=3)

    def test_live_json_of_another_show_keeps_only_whats_app_wide(self) -> None:
        live = LiveState(show=MORNING, base={"input": "red", "time": 1.0}, globals={"speed": 1.2}, master=0.5,
                         house={"dimmer-4": HouseSetting(0.6, True)}, quit_dark=True, quit_at=123.0,
                         automations={"paused": True, "paused_since": 99.0, "states": {"x": {"matched": True}}},
                         global_automations={"states": {"g": {"matched": True}}},
                         frozen={"from": "Before", "colours": {"slimpar-101": [1, 0, 0, 1]}})
        other = live.for_show(EVENING)
        self.assertEqual((other.show, other.base, other.frozen, other.globals), (EVENING, None, None, {}))
        self.assertEqual((other.master, other.quit_dark, other.quit_at), (0.5, True, 123.0))
        self.assertEqual(other.house["dimmer-4"].on, True)
        self.assertEqual(other.show_globals, {MORNING: {"speed": 1.2}})
        self.assertEqual(other.automations, {"paused": True, "paused_since": 99.0})  # (pausing is app-wide)
        self.assertEqual(other.global_automations, live.global_automations)
        self.assertIs(live.for_show(MORNING), live)


class GlobalAutomationTests(EngineCase):
    def test_load_a_show_and_the_shows_automations_wait_for_it(self) -> None:
        load = automation("load", [{"type": "time", "from": "10:00", "to": "10:30"}], [{"do": "load_show", "show": "evening"}])
        mine = automation("mine", [{"type": "time", "from": "10:00", "to": "11:00"}], [{"do": "base", "input": "red", "transition": "cut"}])
        morning = a_show("Morning", MORNING, [solid("red", RED)], [mine])
        self.make(morning, global_automations=globals_of(load), start=datetime(2026, 11, 8, 9, 59, 50))
        self.run_for(11.0, step=0.5)  # 10:00
        who, request = self.engine.gui_requests.get_nowait()
        self.assertEqual(request, {"do": "load_show", "show": "evening", "_scope": "global"})
        self.assertEqual(who, "Load")
        self.assertEqual(self.engine.frames.get().base, "Black")  # Morning's own automation waits for the load...
        self.send(ShowLoadFailed())  # ...and if it can't load, the loaded show's automations carry on
        self.run_for(1.0)
        self.assertEqual(self.engine.frames.get().base, "Red")

    def test_the_loaded_shows_automations_run_once_it_has_loaded(self) -> None:
        load = automation("load", [{"type": "time", "from": "10:00", "to": "10:30"}], [{"do": "load_show", "show": "evening"}])
        on_load = automation("on-load", [{"type": "show_loads"}], [{"do": "base", "input": "green", "transition": "cut"}])
        morning = a_show("Morning", MORNING, [solid("red", RED)])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)], [on_load])
        self.make(morning, global_automations=globals_of(load), start=datetime(2026, 11, 8, 10, 0, 5))
        self.assertEqual(self.engine.gui_requests.get_nowait()[1]["do"], "load_show")
        self.send(LoadShow(evening))  # (the window loads it)
        self.assertEqual(self.engine.frames.get().base, "Green")
        self.run_for(5.0)
        self.assertTrue(self.engine.gui_requests.empty())  # Evening is loaded: "load Evening" doesn't ask again

    def test_they_run_before_the_shows_own(self) -> None:
        dim = automation("dim", [{"type": "time", "from": "10:00", "to": "11:00"}], [{"do": "master", "level": 0.5}])
        bright = automation("bright", [{"type": "time", "from": "10:00", "to": "11:00"}], [{"do": "master", "level": 0.9}])
        self.make(a_show("Morning", MORNING, [solid("red", RED)], [bright]), global_automations=globals_of(dim))
        self.assertAlmostEqual(self.engine._master.target, 0.9)  # the show's ran last, so it wins
        self.assertEqual([h.automation for h in self.engine._global_runner.history], ["dim"])
        self.assertEqual([h.automation for h in self.engine._runner.history], ["bright"])

    def test_house_lights_by_name_and_this_show_is_loaded(self) -> None:
        keys = automation("keys", [{"type": "loaded_show", "show": "evening"}],
                          [{"do": "house", "label": "key lights", "level": 0.4}, {"do": "house", "label": "Organ lights", "level": 1.0}])
        morning = a_show("Morning", MORNING, [solid("red", RED)])
        evening = a_show("Evening", EVENING, [solid("green", GREEN)])
        self.make(morning, global_automations=globals_of(keys))
        self.run_for(1.5)
        self.assertFalse(self.engine._house["dimmer-4"].on)  # Morning is loaded, not Evening
        self.send(LoadShow(evening))
        self.run_for(1.5)
        self.assertTrue(self.engine._house["dimmer-4"].on)  # found by its name ("Organ lights" isn't there: skipped)
        self.assertAlmostEqual(self.engine._house["dimmer-4"].level.target, 0.4)

    def test_they_cant_touch_a_shows_inputs(self) -> None:
        self.assertTrue(check_action({"do": "base", "input": "red"}, "global"))
        self.assertTrue(check_action({"do": "variable", "name": "speed", "value": 1}, "global"))
        self.assertTrue(check_condition({"type": "input_live", "input": "red"}, "global"))
        self.assertTrue(check_condition({"type": "pc_startup"}, "show"))  # (a show's: "When this show loads")
        self.assertFalse(check_action({"do": "load_show", "show": "evening"}, "global"))
        self.assertFalse(check_action({"do": "house", "label": "Festoon", "level": 0.5}, "global"))
        self.assertIsNone(action_command({"do": "base", "input": "red"}, a_show("M", MORNING, [solid("red", RED)]), "global"))

    def test_run_now_and_the_stream_deck(self) -> None:
        dim = automation("dim", [], [{"do": "master", "level": 0.3}])
        self.make(a_show("Morning", MORNING, [solid("red", RED)]), global_automations=globals_of(dim))
        self.send(RunAutomation("dim", "global"))
        self.assertAlmostEqual(self.engine._master.target, 0.3)
        self.send(SetAutomationEnabled("dim", False, "global"))
        self.assertEqual(self.engine.gui_requests.get_nowait(),
                         ("Stream Deck", {"do": "automation", "automation": "dim", "enabled": False, "_scope": "global"}))
        self.send(RequestShow(EVENING))
        self.assertEqual(self.engine.gui_requests.get_nowait(), ("Stream Deck", {"do": "load_show", "folder": EVENING}))


class QuitHoldTests(EngineCase):
    """After a "Turn lights off" quit, automations don't light things until one of their time ranges begins after it."""

    def setUp(self) -> None:
        from tests.test_automations import uk_epoch

        on_load = automation("on-load", [{"type": "show_loads"}], [{"do": "base", "input": "green", "transition": "cut"}])
        self.morning = a_show("Morning", MORNING, [solid("red", RED)])
        self.evening = a_show("Evening", EVENING, [solid("green", GREEN)], [on_load])
        self.live = LiveState(show=MORNING, quit_dark=True, quit_at=uk_epoch(datetime(2026, 11, 7, 22, 0)))  # Saturday night

    def load_by(self, globals_: Show) -> None:
        self.make(self.morning, self.live, global_automations=globals_, start=datetime(2026, 11, 8, 8, 29, 50))
        self.run_for(12.0, step=0.5)
        _who, request = self.engine.gui_requests.get_nowait()
        self.assertEqual(request["show"], "evening")
        self.send(LoadShow(self.evening, automation=True))  # (the window loads it for the global automation)

    def test_a_scheduled_global_automation_loading_a_show_lets_it_light_things(self) -> None:
        self.load_by(globals_of(automation("load", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "13:00"}],
                                           [{"do": "load_show", "show": "evening"}])))
        self.assertFalse(self.engine._quit_dark)  # Sunday 08:30 began after the quit
        self.assertEqual(self.engine.frames.get().base, "Green")  # so Evening's "When this show loads" lit things

    def test_a_global_automation_with_no_time_of_its_own_doesnt(self) -> None:
        self.load_by(globals_of(automation("load", [{"type": "app_started"}], [{"do": "load_show", "show": "evening"}])))
        self.assertTrue(self.engine._quit_dark)
        self.assertEqual(self.engine.frames.get().base, "Black")  # still held off

    def test_a_person_loading_a_show_takes_charge(self) -> None:
        self.make(self.morning, self.live, start=datetime(2026, 11, 8, 3, 0))
        self.send(LoadShow(self.evening))
        self.assertFalse(self.engine._quit_dark)
        self.assertEqual(self.engine.frames.get().base, "Green")


# ---------------------------------------------------------------- the files


class FilesCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "CentraLight Shows"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ShowFolderTests(FilesCase):
    def test_the_shows_are_the_folders_with_a_show_json(self) -> None:
        create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening Service", "Evening")
        (self.root / GLOBAL_DIR).mkdir()
        (self.root / "Notes").mkdir()
        shows = list_shows(self.root)
        self.assertEqual([(s.id, s.name) for s in shows], [("evening-service", "Evening"), ("morning", "Morning")])
        for wanted in ("evening-service", "Evening Service", "evening", "EVENING"):
            self.assertEqual(find_show(self.root, wanted).id, "evening-service", wanted)
        self.assertIsNone(find_show(self.root, "Christmas"))

    def test_a_new_show_has_the_lights_and_layouts_and_follow_screen_only(self) -> None:
        current = create_show(self.root / "Morning", "Morning")
        photo = self.root / "Morning" / "media" / "room-abc.jpg"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"not really a jpeg")
        current, _layout = save_layout_as(current, "Stage")
        current = replace(current, layouts=tuple(replace(lay, backdrop="media/room-abc.jpg") if lay.name == "Stage" else lay
                                                 for lay in current.layouts),
                          globals=(GlobalVar("speed"),))
        current = current.with_automations({"a": automation("a", [], [])}, ("a",))
        save_whole_show(current)
        folder = new_show(load_show(self.root / "Morning"), self.root, "Evening")
        made = load_show(folder)
        self.assertEqual(made.name, "Evening")
        self.assertEqual(made.fixtures, current.fixtures)
        self.assertEqual({lay.name for lay in made.layouts}, {lay.name for lay in current.layouts})
        self.assertTrue((folder / "media" / "room-abc.jpg").is_file())  # the room photo came too
        self.assertEqual(list(made.inputs), ["follow-screen"])
        self.assertEqual((dict(made.automations), made.globals), ({}, ()))
        self.assertEqual(unique_name(self.root, "evening"), "evening 2")

    def test_duplicate_copies_everything_but_the_backups(self) -> None:
        current = create_show(self.root / "Morning", "Morning")
        current = current.with_automations({"a": automation("a", [], [])}, ("a",))
        save_whole_show(current)
        (self.root / "Morning" / "backups").mkdir(exist_ok=True)
        (self.root / "Morning" / "backups" / "old.zip").write_bytes(b"x")
        (self.root / "Morning" / "media").mkdir(exist_ok=True)
        (self.root / "Morning" / "media" / "clip.mp4").write_bytes(b"video")
        folder = duplicate_show(self.root / "Morning", self.root, "Morning")  # (a name that's taken: "Morning 2")
        copy = load_show(folder)
        self.assertEqual(copy.name, "Morning 2")
        self.assertEqual(set(copy.inputs), set(current.inputs))  # the same ids: Stream Deck keys work in both
        self.assertIn("a", copy.automations)
        self.assertTrue((folder / "media" / "clip.mp4").is_file())
        self.assertFalse((folder / "backups").exists())

    def test_delete_goes_to_the_recycle_bin_and_never_the_loaded_show(self) -> None:
        create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening", "Evening")
        binned = []
        with self.assertRaises(ShowError):
            delete_show(self.root / "Morning", str(self.root / "Morning"), trash=binned.append)
        delete_show(self.root / "Evening", str(self.root / "Morning"), trash=binned.append)
        self.assertEqual(binned, [self.root / "Evening"])
        with self.assertRaises(ShowError):
            delete_show(self.root, str(self.root / "Morning"), trash=binned.append)  # (not a show)

    def test_names_that_windows_cant_use_make_safe_folders(self) -> None:
        self.assertEqual(new_folder(self.root, 'Carols: "Nine Lessons"?').name, "Carols Nine Lessons")
        self.assertEqual(new_folder(self.root, "CON").name, "CON show")
        (self.root / "Morning").mkdir()
        self.assertEqual(new_folder(self.root, "Morning").name, "Morning 2")


class GlobalAutomationFileTests(FilesCase):
    def test_they_load_in_their_order_and_a_shows_condition_switches_one_off(self) -> None:
        folder = self.root / GLOBAL_DIR
        folder.mkdir()
        good = automation("sunday", [{"type": "days", "days": ["sun"]}], [{"do": "load_show", "show": "morning"},
                                                                        {"do": "base", "input": "red"}])
        bad = automation("bad", [{"type": "input_live", "input": "red"}], [{"do": "cut_black"}])
        for item in (good, bad):
            (folder / f"{item.id}.json").write_text(json.dumps(automation_to_dict(item)), encoding="utf-8")
        (folder / ORDER_FILE).write_text(json.dumps({"order": ["sunday", "bad"]}), encoding="utf-8")
        container = load_global_automations(folder)
        self.assertEqual([a.id for a in container.ordered_automations()], ["sunday", "bad"])
        self.assertEqual([dict(a) for a in container.automations["sunday"].actions], [{"do": "load_show", "show": "morning"}])
        self.assertFalse(container.automations["bad"].enabled)  # "An input is live" isn't for global automations
        self.assertTrue(any("left out" in w for w in container.warnings))

    def test_a_shows_old_start_conditions_become_when_this_show_loads(self) -> None:
        data = automation_to_dict(automation("starts", [{"type": "pc_startup"}], [{"do": "cut_black"}]))
        converted, notes = automation_from_dict(data)
        self.assertEqual([dict(c) for c in converted.conditions], [{"type": "show_loads"}])
        self.assertTrue(converted.enabled)
        self.assertIn("When this show loads", notes[0])


# ---------------------------------------------------------------- the window


class WindowCase(FilesCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        super().setUp()
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            window._shut_down = True
            window.editor_page.shutdown()
            for watcher in (window.watcher, window.global_watcher):
                if watcher is not None:
                    watcher.stop()
            window.document.close()
            window.global_document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        super().tearDown()

    def make(self, show: Show, global_automations: Show | None = None, save_files: bool = True):
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        engine = FakeEngine(show, global_automations=global_automations)
        window = MainWindow(Settings(), show, engine, engine.fake_output, save_files=save_files,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv", global_automations=global_automations,
                            shows_root=self.root)
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window, engine


class ShowPickerTests(WindowCase):
    def test_loading_a_show_switches_everything_and_keeps_each_shows_history(self) -> None:
        from centralight.ui.document import SCOPE_SHOW

        morning = create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening", "Evening")
        window, engine = self.make(morning)
        self.assertEqual([s.name for s in window.list_shows()], ["Evening", "Morning"])
        window.document.apply(replace(window.document.show, input_order=tuple(reversed(window.document.show.input_order)) + ("x",)),
                              "A change in Morning")
        stale = replace(morning, name="Meant for Morning")
        self.assertTrue(window.load_show(str(self.root / "Evening")))
        self.assertEqual(window.document.show.name, "Evening")
        self.assertEqual(engine.show.name, "Evening")
        self.assertIn("Evening", window.header.show_picker.text())
        self.assertEqual(Path(window.settings.show_folder), self.root / "Evening")
        self.assertEqual(window.watcher.folder, self.root / "Evening")
        self.assertFalse(window.document.apply(stale, "stale"))  # a change worked out for Morning isn't made here
        self.assertEqual(window.document.show.name, "Evening")
        self.assertFalse(window.document.stack(SCOPE_SHOW).canUndo())  # Evening's own history: empty
        self.assertTrue(window.load_show(str(self.root / "Morning")))
        self.assertTrue(window.document.stack(SCOPE_SHOW).canUndo())  # Morning's came back with it
        self.assertFalse(window.load_show(str(self.root / "Morning")))  # (loaded already)

    def test_new_duplicate_rename_and_delete(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        morning = create_show(self.root / "Morning", "Morning")
        window, _engine = self.make(morning)
        with mock.patch.object(QInputDialog, "getText", return_value=("Evening", True)):
            window.ask_new_show()
        self.assertEqual(window.document.show.name, "Evening")
        self.assertEqual(list(window.document.show.inputs), ["follow-screen"])
        with mock.patch.object(QInputDialog, "getText", return_value=("Evening copy", True)):
            window.ask_duplicate_show()
        self.assertEqual(window.document.show.name, "Evening copy")
        copy_folder = Path(window.document.show.folder)
        with mock.patch.object(QInputDialog, "getText", return_value=("Sunday evening", True)):
            window.ask_rename_show()
        window.document.flush()
        self.assertTrue(window.document.wait_saved())
        self.assertEqual(json.loads((copy_folder / SHOW_FILE).read_text(encoding="utf-8"))["name"], "Sunday evening")
        self.assertEqual(Path(window.document.show.folder), copy_folder)  # the folder (and its id) stays
        binned = []
        real_delete = delete_show
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch("centralight.ui.main_window.delete_show", lambda folder, loaded: real_delete(folder, loaded, trash=binned.append)):
            window.ask_delete_show(str(self.root / "Morning"))
            window.ask_delete_show(str(copy_folder))  # (the loaded show: nothing happens)
        self.assertEqual(binned, [self.root / "Morning"])

    def test_a_global_automation_or_the_stream_deck_loads_a_show_through_the_window(self) -> None:
        morning = create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening", "Evening")
        window, engine = self.make(morning)
        engine.gui_requests.put(("Sunday evening", {"do": "load_show", "show": "evening", "_scope": "global"}))
        window._do_engine_requests()
        self.assertEqual(window.document.show.name, "Evening")
        engine.gui_requests.put(("Stream Deck", {"do": "load_show", "folder": str(self.root / "Morning")}))
        window._do_engine_requests()
        self.assertEqual(window.document.show.name, "Morning")
        engine.gui_requests.put(("Stream Deck", {"do": "load_show", "show": "christmas"}))
        window._do_engine_requests()  # (no such show: logged, and nothing changes)
        self.assertEqual(window.document.show.name, "Morning")


class AutomationListsTests(WindowCase):
    def test_two_lists_with_their_own_history(self) -> None:
        from tests.gui_fixtures import sample_global_automations

        show = create_show(self.root / "Morning", "Morning")
        globals_ = replace(sample_global_automations(), folder=str(self.root / GLOBAL_DIR))
        window, engine = self.make(show, global_automations=globals_)
        window.show_page("automation")
        page = window.automation_page
        self.assertEqual(page.global_list.count(), 2)
        self.assertIn("MORNING", page.show_title.text())
        new_id = page.new_automation("global")
        self.assertTrue(page.global_selected())
        self.assertIs(window._active_document(), window.global_document)
        self.assertIn(new_id, window.global_document.show.automations)
        self.assertIn(new_id, engine.global_automations.automations)  # the engine has it at once
        self.assertEqual(dict(window.document.show.automations), {})  # (not in the show)
        page.add_rule("actions", "load_show")
        window.undo()  # the global list's history
        self.assertEqual(len(window.global_document.show.automations[new_id].actions), 0)
        window.global_document.flush()
        self.assertTrue(window.global_document.wait_saved())
        self.assertTrue((self.root / GLOBAL_DIR / f"{new_id}.json").is_file())
        order = json.loads((self.root / GLOBAL_DIR / ORDER_FILE).read_text(encoding="utf-8"))["order"]
        self.assertEqual(order[-1], new_id)
        page.new_automation("show")
        self.assertFalse(page.global_selected())
        self.assertIs(window._active_document(), window.document)
        self.assertEqual(len(window.document.show.automations), 1)


class ImportLayoutTests(WindowCase):
    def test_import_a_layout_from_another_show(self) -> None:
        morning = create_show(self.root / "Morning", "Morning")
        evening = create_show(self.root / "Evening", "Evening")
        fixtures = [replace(fx, address=fx.address + 100) if fx.id == "slimpar-101" else fx
                    for fx in evening.fixtures if fx.id != "slimpar-108"]
        evening = replace(evening, fixtures=tuple(fixtures))
        evening, layout_id = save_layout_as(evening, "Stage")
        photo = self.root / "Evening" / "media" / "room-x.png"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"png bytes")
        evening = replace(evening, layouts=tuple(replace(lay, backdrop="media/room-x.png") if lay.id == layout_id else lay
                                                 for lay in evening.layouts))
        save_whole_show(evening)
        evening = load_show(self.root / "Evening")
        lines = import_summary(morning, evening)
        self.assertTrue(any(line.startswith("Lights removed: SlimPAR 108") for line in lines), lines)
        self.assertTrue(any(line.startswith("Addresses changed: SlimPAR 101") for line in lines), lines)
        window, _engine = self.make(morning)
        self.assertTrue(window.layout_page.import_from(evening, layout_id))
        show = window.document.show
        self.assertEqual(show.fixtures, evening.fixtures)
        active = show.layout()
        self.assertEqual(active.name, "Stage (from Evening)")
        self.assertTrue((Path(show.folder) / active.backdrop).is_file())  # the room photo was copied here
        window.show_page("layout")
        window.undo()  # one step, in the layout's history
        self.assertEqual(window.document.show.fixtures, morning.fixtures)


# ---------------------------------------------------------------- the API


class ShowRouteTests(FilesCase):
    def test_the_routes(self) -> None:
        from centralight.remote import BadRequest, command_for, command_from_json

        create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening", "Evening")
        shows = list_shows(self.root)
        show = a_show("Morning", str(self.root / "Morning"), [solid("red", RED)])
        self.assertEqual(command_for(["show", "evening"], {}, show, shows=shows), RequestShow(str(self.root / "Evening")))
        self.assertEqual(command_for(["show", "Evening"], {}, show, shows=shows), RequestShow(str(self.root / "Evening")))
        self.assertEqual(command_from_json({"op": "show", "id": "evening"}, show, shows=shows), RequestShow(str(self.root / "Evening")))
        with self.assertRaises(BadRequest) as caught:
            command_for(["show", "christmas"], {}, show, shows=shows)
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("morning", str(caught.exception))
        globals_ = globals_of(automation("sunday", [], []))
        self.assertEqual(command_for(["global-automation", "sunday", "run"], {}, show, global_automations=globals_),
                         RunAutomation("sunday", "global"))
        self.assertEqual(command_for(["global-automation", "Sunday", "disable"], {}, show, global_automations=globals_),
                         SetAutomationEnabled("sunday", False, "global"))
        with self.assertRaises(BadRequest) as caught:
            command_for(["input", "green"], {}, show)
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("not in this show (Morning)", str(caught.exception))

    def test_over_http(self) -> None:
        import http.client

        from centralight.remote import RemoteServer
        from tests.fakes import FakeEngine

        create_show(self.root / "Morning", "Morning")
        create_show(self.root / "Evening", "Evening")
        engine = FakeEngine(load_show(self.root / "Morning"))
        server = RemoteServer(engine, port=0, shows_root=self.root)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)

        def get(path: str):
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            try:
                connection.request("GET", path)
                reply = connection.getresponse()
                raw = reply.read()
                return reply.status, json.loads(raw) if "json" in reply.getheader("Content-Type", "") else raw.decode("utf-8")
            finally:
                connection.close()

        status, reply = get("/api/shows")
        self.assertEqual(status, 200)
        self.assertEqual(reply["shows"], [{"id": "evening", "name": "Evening", "loaded": False},
                                          {"id": "morning", "name": "Morning", "loaded": True}])
        status, state = get("/api/state")
        self.assertEqual((state["show"], state["show_id"]), ("Morning", "morning"))
        status, page = get("/")
        self.assertIn('<select id="shows"', page)
        self.assertIn('<option value="evening">Evening</option>', page)
        self.assertEqual(get("/api/show/evening"), (200, {"ok": True}))
        self.assertEqual(engine.gui_requests.get_nowait(), ("Stream Deck", {"do": "load_show", "folder": str(self.root / "Evening")}))
        status, reply = get("/api/show/christmas")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
