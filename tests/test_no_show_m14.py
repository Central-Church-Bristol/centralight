"""M14 item 5, added 5 Nov: the Default show is "Last loaded" (as before), "None (lights off)" or a show. With None, a PC
start opens no show, and when a schedule ends the show closes (after the countdown, the lights fade off), with the
default show's hand-back rules. With no show loaded every DMX channel is 0, the global automations carry on, the header's
controls that need a show are greyed out, every tab but Automation shows "No show loaded" (a Load button per show, New
show…, Import a show…), and the phone page says so too. A reopen after a quit or a crash carries on with no show."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from centralight.automations import AutomationRunner
from centralight.commands import CloseShow, LoadShow, SendToBase, SetCountdownLength, SetDefaultShow, SetHouse
from centralight.countdown import counts_down, describe
from centralight.live_state import LiveState
from centralight.model import NO_DEFAULT_SHOW, NO_SHOW
from centralight.new_show import create_show
from centralight.output import DMX_CHANNELS, show_channels
from centralight.schedule import describe_block, week_schedule
from centralight.settings import Settings
from tests.fakes import FakeEngine, FakeOutput
from tests.test_automations import AutomationEngineTestCase, automation
from tests.test_default_show_m14 import SUNDAY, container, morning
from tests.test_shows import FilesCase, WindowCase


def drain(engine) -> list[dict]:
    """What the engine has asked the window for since last time."""
    asked = []
    while not engine.gui_requests.empty():
        asked.append(engine.gui_requests.get()[1])
    return asked


class RunnerTests(unittest.TestCase):
    """The hand-back with the default None: the same rules as a default show, closing the show instead of loading one."""

    def setUp(self) -> None:
        self.runner = AutomationRunner()
        self.runner.default_show = NO_DEFAULT_SHOW
        self.mono = 1000.0

    def check(self, global_automations, hhmm: str, day: date = SUNDAY) -> list[dict]:
        hours, minutes = (int(x) for x in hhmm.split(":"))
        local = datetime.combine(day, datetime.min.time()) + timedelta(hours=hours, minutes=minutes)
        self.mono += 60.0
        from centralight.automations import Situation

        actions = self.runner.check(global_automations, Situation(now=local.timestamp(), local=local), self.mono)
        actions += self.runner.due(self.mono)
        return [action for _who, action in actions]

    def test_the_show_closes_when_the_schedule_ends(self) -> None:
        shows = container(morning())
        self.assertEqual([a["do"] for a in self.check(shows, "10:00")], ["load_show"])
        ended = self.check(shows, "12:31")
        self.assertEqual([a["do"] for a in ended], ["close_show"])
        self.assertTrue(ended[0].get("_default"))
        self.assertEqual(self.check(shows, "13:00"), [])  # (once)

    def test_a_show_loaded_by_hand_since_stays(self) -> None:
        shows = container(morning())
        self.check(shows, "10:00")
        self.runner.manual_load()
        self.assertEqual(self.check(shows, "12:31"), [])

    def test_the_schedules_own_ending_wins(self) -> None:
        for ending, expected in (({"do": "load_show", "show": "evening"}, ["load_show"]), ({"do": "cut_black"}, ["cut_black"])):
            self.setUp()
            shows = container(morning(on_end=[ending]))
            self.check(shows, "10:00")
            self.assertEqual([a["do"] for a in self.check(shows, "12:31")], expected, ending)

    def test_a_longer_schedule_still_matching_gets_its_show_back_and_closes_after(self) -> None:
        sunday = automation("sunday", [{"type": "days", "days": ["sun"]}], [{"do": "load_show", "show": "sunday"}])
        evening = automation("evening", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "18:00", "to": "20:00"}],
                             [{"do": "load_show", "show": "evening"}])
        shows = container(sunday, evening)
        self.check(shows, "10:00")
        self.check(shows, "18:00")
        back = self.check(shows, "20:00")
        self.assertEqual([(a["do"], a.get("show")) for a in back], [("load_show", "sunday")])
        closed = self.check(shows, "00:01", SUNDAY + timedelta(days=1))
        self.assertEqual([a["do"] for a in closed], ["close_show"])

    def test_the_close_waits_for_the_countdown_and_a_new_schedule_drops_it(self) -> None:
        youth = automation("youth", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "12:35", "to": "14:00"}],
                           [{"do": "load_show", "show": "youth"}])
        shows = container(morning(), youth)
        self.check(shows, "10:00")
        self.runner.countdown = 600.0
        self.runner.counted = lambda action: counts_down(action, "global")
        self.runner.needless = lambda action: False
        self.assertEqual(self.check(shows, "12:31"), [])  # (counting down)
        self.assertTrue(any(p[3].get("do") == "close_show" for p in self.runner.pending))
        self.check(shows, "12:35")  # (Youth starts: its show wins over the close)
        self.assertFalse(any(p[3].get("do") == "close_show" for p in self.runner.pending))
        self.assertTrue(any(p[3].get("show") == "youth" for p in self.runner.pending))  # (after its own countdown)
        self.assertEqual(self.runner.loader, "youth")

    def test_the_countdown_says_so(self) -> None:
        self.assertTrue(counts_down({"do": "close_show"}, "global"))
        self.assertEqual(describe({"do": "close_show"}), "Closing the show (lights off)")


class EngineTests(AutomationEngineTestCase):
    MORNING = automation("sunday-morning", [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                         [{"do": "load_show", "show": "morning"}])

    def test_a_schedule_ending_counts_down_fades_the_lights_then_closes_the_show(self) -> None:
        self.make(datetime(2026, 11, 8, 8, 59), [], global_automations=[self.MORNING])
        self.engine.submit(SetDefaultShow(NO_DEFAULT_SHOW, 2.0))
        self.engine.submit(SetCountdownLength(15.0))
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.engine.submit(SetHouse("dimmer-4", level=0.6, on=True))
        self.run_until(datetime(2026, 11, 8, 12, 29, 50))
        drain(self.engine)  # (Morning's own load at 09:00: no window here to load it)
        self.assertEqual(self.base(), "warm-welcome")
        seen: dict = {}
        for second in range(90):
            self.clocks.advance(1.0)
            self.engine.tick()
            countdown = self.engine.frames.get().countdown
            if countdown is not None and "countdown" not in seen:
                seen["countdown"] = (second, countdown.text)
            if self.engine._closing is not None and "fade" not in seen:
                seen["fade"] = second
                self.assertEqual(self.base(), "")  # (fading to black)
            asked = [a for a in drain(self.engine) if a.get("do") == "close_show"]
            if asked:
                seen["close"] = (second, asked[0])
                break
        self.assertEqual(seen["countdown"][1], "Closing the show (lights off)")
        self.assertAlmostEqual(seen["fade"] - seen["countdown"][0], 15, delta=1.5)  # (after the usual countdown)
        self.assertAlmostEqual(seen["close"][0] - seen["fade"], 2, delta=1.5)  # (once the lights have faded off)
        self.assertEqual(seen["close"][1].get("_scope"), "global")  # (an automation's: not a person's load)
        self.assertFalse(self.engine._house["dimmer-4"].on)  # (the Festoon and Key lights too)
        # the window closes the show: every channel at 0, the house light's level kept for the next show
        self.engine.submit(LoadShow(NO_SHOW, automation=True))
        self.engine.tick()
        self.assertTrue(self.engine.show.no_show)
        self.assertEqual(self.output.last, bytes(DMX_CHANNELS))
        self.assertAlmostEqual(self.engine._house["dimmer-4"].level.target, 0.6)
        self.assertTrue(self.engine.live_state().no_show)

    def test_a_show_loaded_during_the_fade_stops_the_close(self) -> None:
        self.make(datetime(2026, 11, 8, 10, 0), [])
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.engine.tick()
        self.engine.submit(CloseShow(2.0, "Show menu"))
        self.engine.tick()
        self.assertIsNotNone(self.engine._closing)
        self.clocks.advance(1.0)
        self.engine.submit(LoadShow(self.show))  # (the same folder: just its latest version) ...
        other = __import__("dataclasses").replace(self.show, folder=str(Path("C:/Shows/Evening")), name="Evening")
        self.engine.submit(LoadShow(other))  # (... and another show, by hand)
        self.engine.tick()
        self.assertIsNone(self.engine._closing)
        self.clocks.advance(3.0)
        self.engine.tick()
        self.assertEqual([a for a in drain(self.engine) if a.get("do") == "close_show"], [])
        self.assertFalse(self.engine.show.no_show)

    def test_closing_by_hand_from_the_show_menu(self) -> None:
        self.make(datetime(2026, 11, 8, 10, 0), [], global_automations=[self.MORNING])
        self.run_until(datetime(2026, 11, 8, 10, 1))
        drain(self.engine)
        self.assertEqual(self.engine._global_runner.loader, "sunday-morning")
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.engine.submit(CloseShow(2.0, "Show menu"))
        self.engine.tick()
        self.assertEqual(drain(self.engine), [])  # (the lights fade off first)
        self.clocks.advance(2.5)
        self.engine.tick()
        asked = [a for a in drain(self.engine) if a.get("do") == "close_show"]
        self.assertEqual(asked, [{"do": "close_show"}])  # (a person's: no "_scope")
        self.engine.submit(LoadShow(NO_SHOW))
        self.engine.tick()
        self.assertIsNone(self.engine._global_runner.loader)  # (a person's choice: nothing comes back over it)
        self.assertEqual(self.output.last, bytes(DMX_CHANNELS))

    def test_lights_already_off_close_at_once(self) -> None:
        self.make(datetime(2026, 11, 8, 10, 0), [])
        self.engine.submit(CloseShow(2.0, "Show menu"))
        self.engine.tick()
        self.assertEqual([a.get("do") for a in drain(self.engine)], ["close_show"])

    def test_with_no_show_the_global_automations_run_and_load_a_show(self) -> None:
        self.make(datetime(2026, 11, 8, 8, 58), [], global_automations=[self.MORNING])
        self.engine.submit(LoadShow(NO_SHOW))
        self.engine.tick()
        drain(self.engine)
        self.assertEqual(self.engine._show_names(), frozenset())  # (no show: a show called "None" isn't loaded)
        self.run_until(datetime(2026, 11, 8, 9, 0, 30))
        asked = [a for a in drain(self.engine) if a.get("do") == "load_show"]
        self.assertEqual([a.get("show") for a in asked], ["morning"])
        self.assertEqual(self.output.last, bytes(DMX_CHANNELS))  # (and until it's loaded, the lights stay off)

    def test_the_next_show_comes_up_from_black_with_the_house_levels_kept(self) -> None:
        self.make(datetime(2026, 11, 8, 10, 0), [])
        self.engine.submit(SetHouse("dimmer-4", level=0.35, on=True))
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.engine.tick()
        self.engine.submit(LoadShow(NO_SHOW))  # (straight away: every light off, nothing held)
        self.engine.tick()
        self.assertEqual(self.output.last, bytes(DMX_CHANNELS))
        self.assertFalse(self.engine._house["dimmer-4"].on)
        self.engine.submit(LoadShow(__import__("dataclasses").replace(self.show, folder=str(Path("C:/Shows/Evening")))))
        self.engine.tick()
        self.assertIsNone(self.engine._base.held)  # (lights from black: nothing of before is held)
        self.assertAlmostEqual(self.engine._house["dimmer-4"].level.target, 0.35)
        self.assertFalse(self.engine._house["dimmer-4"].on)

    def test_a_pc_restart_mid_service_brings_the_schedules_show_back(self) -> None:
        """With None as the default, a PC restart opens no show; a schedule under way loads its show again at once."""
        from centralight.engine import Engine
        from tests.test_automations import uk_local

        self.make(datetime(2026, 11, 8, 8, 59), [], global_automations=[self.MORNING])
        self.run_until(datetime(2026, 11, 8, 10, 0))
        live = LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))
        live.saved_at = self.clocks.wall
        self.clocks.advance(15 * 60.0)
        self.clocks.uptime_value = 60.0  # (the PC restarted at 10:15)
        engine = Engine(NO_SHOW, FakeOutput(), live.for_show(""), clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                        local_time=uk_local, uptime=lambda: self.clocks.uptime_value, global_automations=self.global_automations,
                        pc_start=True, default_show=NO_DEFAULT_SHOW, default_opened=True)
        for _ in range(3):
            self.clocks.advance(1.0)
            engine.tick()
        asked = [a.get("show") for a in drain(engine) if a.get("do") == "load_show"]
        self.assertEqual(asked, ["morning"])
        self.assertEqual(engine.output.frames[-1], bytes(DMX_CHANNELS))  # (until it's loaded: every light off)

    def test_a_pc_start_with_no_show_sends_every_channel_at_zero(self) -> None:
        engine = FakeEngine(NO_SHOW, pc_start=True)
        self.assertEqual(engine.fake_output.frames[-1], bytes(DMX_CHANNELS))
        self.assertEqual(show_channels(NO_SHOW), DMX_CHANNELS)  # (all 512, so no light keeps an old colour)

    def test_live_state_keeps_no_show(self) -> None:
        engine = FakeEngine(NO_SHOW)
        saved = json.loads(json.dumps(engine.live_state().to_dict()))
        self.assertTrue(saved["no_show"])
        self.assertTrue(LiveState.from_dict(saved).no_show)
        self.assertNotIn("no_show", LiveState(show="x").to_dict())  # (an ordinary save is as before)
        self.assertFalse(LiveState.from_dict({"show": "x"}).no_show)


class StartTests(FilesCase):
    def test_which_show_opens(self) -> None:
        from centralight.app import show_to_open

        create_show(self.root / "Central Church", "Central Church")
        evening = create_show(self.root / "Evening", "Evening")
        last = LiveState(show=evening.folder)
        none = Settings(default_show=NO_DEFAULT_SHOW)
        self.assertIsNone(show_to_open(none, last, root=self.root, pc_start=True))  # (a PC start: no show)
        self.assertEqual(show_to_open(none, last, root=self.root, pc_start=False).name, "Evening")  # (a reopen carries on)
        closed = LiveState(no_show=True)
        self.assertIsNone(show_to_open(none, closed, root=self.root, pc_start=False))  # (a reopen with no show: still none)
        self.assertIsNone(show_to_open(Settings(), closed, root=self.root, pc_start=True))  # (Last loaded: none was)
        self.assertEqual(show_to_open(Settings(default_show="central-church"), closed, root=self.root, pc_start=True).name,
                         "Central Church")  # (a default show: it opens at a PC start)
        self.assertEqual(show_to_open(Settings(), last, root=self.root, pc_start=True).name, "Evening")  # (Last loaded)

    def test_old_settings_read_as_last_loaded(self) -> None:
        settings = Settings()
        self.assertEqual(settings.default_show, "")  # (Last loaded, as before)
        settings.update({"default_show": "_none"})
        self.assertEqual(settings.default_show, NO_DEFAULT_SHOW)


class ScheduleTests(unittest.TestCase):
    def test_the_gaps_say_no_show(self) -> None:
        shows = {"morning": "Morning", "central-church": "Central Church"}
        blocks, _unplaced = week_schedule([morning()], shows, date(2026, 11, 2), default_show=NO_DEFAULT_SHOW)
        defaults = [b for b in blocks if b.default]
        self.assertEqual(len(defaults), 2)
        self.assertTrue(all(b.show_label == "No show (lights off)" and not b.missing and not b.show_id for b in defaults))
        self.assertIn("the lights are off", describe_block(defaults[0]))


class RemoteTests(unittest.TestCase):
    def test_the_api_with_no_show(self) -> None:
        from centralight.commands import CountdownAction, RequestShow
        from centralight.remote import BadRequest, command_for, state_of
        from centralight.shows import ShowInfo

        for path in (["input", "warm-white"], ["master"], ["black", "cut"], ["house", "festoon", "on"], ["var", "mood"],
                     ["overlay", "clear"], ["layout", "main"], ["automation", "x", "run"], ["identify", "on"]):
            with self.assertRaises(BadRequest) as caught:
                command_for(path, {"level": "0.5", "value": "1"}, NO_SHOW)
            self.assertEqual((caught.exception.status, str(caught.exception)), (404, "No show loaded"), path)
        shows = [ShowInfo("evening", "Evening", str(Path("C:/Shows/Evening")))]
        self.assertIsInstance(command_for(["show", "evening"], {}, NO_SHOW, shows=shows), RequestShow)
        self.assertIsInstance(command_for(["countdown", "skip"], {"id": "g:1"}, NO_SHOW), CountdownAction)
        state = state_of(FakeEngine(NO_SHOW), shows)
        self.assertTrue(state["no_show"])
        self.assertEqual((state["show_id"], state["inputs"]), ("", []))

    def test_the_phone_page(self) -> None:
        from centralight.remote import RemoteServer, help_page

        engine = FakeEngine(NO_SHOW)
        server = RemoteServer(engine, port=0)
        page = help_page(server)
        self.assertIn("<h1>No show loaded</h1>", page)
        self.assertIn("None (lights off)", page)  # (the switcher: pick a show to load it)
        self.assertIn("The lights are off", page)
        self.assertNotIn("Cut to black", page)
        self.assertNotIn("Inputs: tap to send to Base", page)
        shown = help_page(RemoteServer(FakeEngine(), port=0))
        self.assertIn("Cut to black", shown)  # (with a show: as before)
        self.assertIn("Inputs: tap to send to Base", shown)


class TrimOutput(FakeOutput):
    def __init__(self) -> None:
        super().__init__()
        self.trims: list[int] = []

    def set_trim(self, size: int) -> None:
        self.trims.append(size)


class WindowTests(WindowCase):
    def make_window(self, show, global_automations=None, output=None, settings=None):
        from centralight.ui.main_window import MainWindow
        from tests.qt_support import process

        engine = FakeEngine(show, global_automations=global_automations, output=output)
        window = MainWindow(settings or Settings(), show, engine, engine.fake_output, save_files=True,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv", global_automations=global_automations,
                            shows_root=self.root)
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window, engine

    def test_no_show_loaded_every_tab_shows_the_card_but_automation(self) -> None:
        from tests.qt_support import process

        create_show(self.root / "Central Church", "Central Church")
        create_show(self.root / "Evening", "Evening")
        window, engine = self.make_window(NO_SHOW, global_automations=container(morning()))
        for key in ("dashboard", "editor", "layout"):
            window.show_page(key)
            process(self.app)
            self.assertIs(window.stack.currentWidget(), window.no_show_page, key)
            self.assertEqual(window.current_page(), key)
        window.show_page("automation")
        self.assertIs(window.stack.currentWidget(), window.automation_page)
        page = window.automation_page
        self.assertEqual(page.show_title.text(), "THIS SHOW (NO SHOW LOADED)")
        self.assertIn("No show is loaded", page.list.item(0).text())
        self.assertFalse(page.new_button.isEnabled())
        self.assertEqual(page.global_list.count(), 1)  # (the global automations, still there to edit)
        card = window.no_show_page
        self.assertEqual(sorted(Path(f).name for f in card.load_buttons), ["Central Church", "Evening"])
        self.assertIn("Global automations are still on", card.automations_text())
        self.assertIn("Sun 09:00", card.automations_text())
        # the header: "Show: None", and what needs a show greyed out, saying why
        header = window.header
        self.assertIn("Show: None", header.show_picker.text())
        for control in (header.master, header.cut_black, header.fade_black):
            self.assertFalse(control.isEnabled())
            self.assertEqual(control.toolTip(), "Load a show first")
        self.assertTrue(header.lock_button.isEnabled())
        self.assertTrue(header.settings_button.isEnabled())
        self.assertEqual(window.windowTitle(), "CentraLight — No show loaded")
        self.assertEqual(engine.fake_output.frames[-1], bytes(DMX_CHANNELS))
        # the show menu: the shows, None (lights off) ticked, nothing about "None" itself
        picker = header.show_picker
        picker.fill_menu()
        texts = [a.text() for a in picker.picker_menu.actions()]
        self.assertIn("None (lights off)", texts)
        self.assertTrue(next(a for a in picker.picker_menu.actions() if a.text() == "None (lights off)").isChecked())
        self.assertFalse(any(t.startswith(("Duplicate", "Rename", "Notes about", "Export “")) for t in texts))
        # loading from the card: the show comes in as any load does
        button = next(b for folder, b in window.no_show_page.load_buttons.items() if Path(folder).name == "Evening")
        button.click()
        process(self.app)
        self.assertEqual((window.document.show.name, engine.show.name), ("Evening", "Evening"))
        window.show_page("dashboard")
        self.assertIs(window.stack.currentWidget(), window.dashboard)
        self.assertTrue(header.master.isEnabled())
        self.assertNotEqual(header.master.toolTip(), "Load a show first")

    def test_closing_the_show_by_hand_and_by_a_schedule(self) -> None:
        from tests.qt_support import process

        central = create_show(self.root / "Central Church", "Central Church")
        output = TrimOutput()
        window, engine = self.make_window(central, output=output)
        window.show_page("layout")
        window.close_show()  # (Show ▾ → None (lights off): the lights fade off first)
        self.assertFalse(window.document.show.no_show)
        engine.settle(2.5)
        window._do_engine_requests()
        process(self.app)
        self.assertTrue(window.document.show.no_show)
        self.assertTrue(engine.show.no_show)
        self.assertIs(window.stack.currentWidget(), window.no_show_page)
        self.assertEqual(output.trims[-1], DMX_CHANNELS)
        self.assertEqual(engine.fake_output.frames[-1], bytes(DMX_CHANNELS))
        # a schedule loads a show, then ends with the default None: the engine asks for the close
        window.load_show(central.folder, who="Sunday Morning", automation=True)
        process(self.app)
        self.assertFalse(window.document.show.no_show)
        self.assertIs(window.stack.currentWidget(), window.layout_page)
        engine.gui_requests.put(("Sunday Morning", {"do": "close_show", "_scope": "global"}))
        window._do_engine_requests()
        process(self.app)
        self.assertTrue(window.document.show.no_show)
        self.assertEqual(window.header.show_picker.text().split("  ")[0], "Show: None")

    def test_opened_with_no_show_the_header_has_the_last_shows_house_lights_greyed_out(self) -> None:
        central = create_show(self.root / "Central Church", "Central Church")
        self.assertTrue(central.house)
        window, _engine = self.make_window(NO_SHOW, settings=Settings(show_folder=central.folder))
        header = window.header
        self.assertEqual(list(header.house_controls), [h.fixture for h in central.house])
        for control in header.house_controls.values():
            self.assertFalse(control.isEnabled())
            self.assertEqual(control.toolTip(), "Load a show first")
        bare, _engine = self.make_window(NO_SHOW)  # (no show loaded before: none)
        self.assertEqual(bare.header.house_controls, {})

    def test_closing_and_loading_again_leaves_no_histories_behind(self) -> None:
        central = create_show(self.root / "Central Church", "Central Church")
        window, _engine = self.make_window(central)
        group = window.document.undo_group

        def cycle() -> None:
            window._close_now("Test", automation=True)
            window.load_show(central.folder, who="Test", automation=True)

        cycle()
        before = len(group.stacks())
        for _ in range(5):
            cycle()
        self.assertEqual(len(group.stacks()), before)  # (each close made a history for "no show" that stayed, before)

    def test_no_backup_with_no_show(self) -> None:
        from unittest import mock

        window, _engine = self.make_window(NO_SHOW)
        with mock.patch("centralight.ui.main_window.backup_show") as backup:
            window._backup()
        backup.assert_not_called()

    def test_a_new_show_from_no_show_has_the_last_shows_lights(self) -> None:
        from dataclasses import replace

        central = create_show(self.root / "Central Church", "Central Church")
        window, _engine = self.make_window(NO_SHOW)
        window.settings.show_folder = central.folder
        template = window._new_show_template()
        self.assertEqual(template.name, "Central Church")
        self.assertEqual(template.fixtures, central.fixtures)
        window.settings.show_folder = ""
        self.assertTrue(window._new_show_template().fixtures)  # (none known: the church's standard lights)
        self.assertTrue(replace(NO_SHOW).no_show)

    def test_the_settings_dialog(self) -> None:
        from centralight.ui.settings_dialog import SettingsDialog

        central = create_show(self.root / "Central Church", "Central Church")
        create_show(self.root / "Evening", "Evening")
        window, _engine = self.make_window(central)
        dialog = SettingsDialog(window.settings, central.folder, window, shows=window.list_shows())
        box = dialog.default_show
        self.assertEqual([box.itemText(i) for i in range(box.count())],
                         ["Last loaded", "None (lights off)", "Central Church", "Evening"])
        self.assertEqual(box.currentIndex(), 0)  # (an old or empty setting reads as Last loaded)
        box.setCurrentIndex(box.findData(NO_DEFAULT_SHOW))
        dialog.apply_to(window.settings)
        self.assertEqual(window.settings.default_show, "_none")
        again = SettingsDialog(window.settings, central.folder, window, shows=window.list_shows())
        self.assertEqual(again.default_show.currentText(), "None (lights off)")
        # with no show loaded, what needs its folder is greyed out (it would open CentraLight's own folder)
        from PySide6.QtWidgets import QPushButton

        bare = SettingsDialog(window.settings, "", window, show=NO_SHOW, shows=window.list_shows())
        buttons = {b.text(): b for b in bare.findChildren(QPushButton)}
        for text in ("Open show folder", "Open the AI guide", "Export Claude skill…"):
            self.assertFalse(buttons[text].isEnabled(), text)
        self.assertTrue(buttons["Open log folder"].isEnabled())

    def test_the_cards_automations_line(self) -> None:
        from centralight.shows import ShowInfo
        from centralight.ui.no_show import NONE, ON, PAUSED, automations_line

        shows = [ShowInfo("morning", "Morning Worship", "C:/Shows/Morning")]
        saturday = datetime(2026, 11, 7, 20, 0)
        state, text = automations_line(container(morning()), shows, False, saturday)
        self.assertEqual(state, ON)
        self.assertEqual(text, "Global automations are still on.  Next: Sunday Morning, Sun 09:00 → Morning Worship")
        self.assertEqual(automations_line(container(morning()), shows, True, saturday)[0], PAUSED)
        self.assertEqual(automations_line(container(), shows, False, saturday)[0], NONE)


if __name__ == "__main__":
    unittest.main()
