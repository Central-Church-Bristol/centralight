"""Fixes from the full review of 3 Nov (five reviewers, one per area). Each test reproduces what went wrong."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path

from centralight.automations import AutomationRunner, Situation, describe_action, describe_condition, summary
from centralight.commands import SendToBase
from centralight.engine import Engine, clamp01
from centralight.model import Automation
from centralight.new_show import create_show, show_from_patch
from centralight.output import to_byte
from centralight.settings import Settings
from centralight.show_io import automation_to_dict, input_from_dict, input_to_dict, load_automations, load_show
from centralight.ui.document import SCOPE_AUTOMATIONS, ShowDocument
from centralight.ui.file_watcher import FolderWatcher
from tests.fakes import FakeClock, FakeOutput
from tests.qt_support import qt_app


class RecordingEngine:
    def __init__(self) -> None:
        self.commands = []

    def submit(self, command) -> None:
        self.commands.append(command)


def touch(path: Path, ahead: float = 5.0) -> None:
    stamp = time.time() + ahead
    os.utime(path, (stamp, stamp))


class BadFilesNeverStopTheShowTests(unittest.TestCase):
    """One wrongly typed value (an AI's mistake, a hand edit) used to stop CentraLight starting at all."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Show"
        create_show(self.folder)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, data) -> None:
        (self.folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.folder / rel).write_text(json.dumps(data), encoding="utf-8")

    def test_wrong_types_in_automations(self) -> None:
        base = {"format": "centralight-automation", "version": 1, "name": "Odd"}
        self.write("automations/odd-action.json", dict(base, id="odd-action", conditions=[], actions=[{"do": ["base"]}]))
        self.write("automations/odd-condition.json", dict(base, id="odd-condition", conditions=[{"type": ["days"]}]))
        self.write("automations/odd-values.json", dict(base, id="odd-values", conditions=[{"type": "days", "days": 5}],
                                                      actions=[{"do": "master", "level": "half"}]))
        automations, warnings = load_automations(self.folder)
        self.assertEqual(len(automations["odd-action"].actions), 0)  # the broken action is left out
        self.assertFalse(automations["odd-condition"].enabled)  # a broken condition switches it off
        self.assertTrue(warnings)
        for automation in automations.values():  # and nothing about them can stop the screen or the engine
            summary(automation, None)
            AutomationRunner().matches(automation, Situation(now=0.0, local=__import__("datetime").datetime(2026, 11, 8, 9)))

    def test_wrong_types_in_show_json(self) -> None:
        data = json.loads((self.folder / "show.json").read_text(encoding="utf-8"))
        data["globals"] = None
        data["house"] = 5
        self.write("show.json", data)
        show = load_show(self.folder)  # opens, with warnings, instead of raising TypeError
        self.assertEqual(show.globals, ())
        self.assertTrue(any("globals" in w for w in show.warnings))
        data["fixtures"][0]["profile"] = ["slimpar"]
        self.write("show.json", data)
        from centralight.app import open_show

        show, message = open_show(Settings(show_folder=str(self.folder)))
        self.assertTrue(show.read_only)  # the standard lights, read-only, with a message saying why...
        self.assertIn("couldn't open the show", message)
        self.assertTrue(show.fixtures)  # ...but it starts

    def test_nan_in_files_reads_as_missing(self) -> None:
        path = self.folder / "inputs" / "follow-screen.json"
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        data["house"] = {"dimmer-1": "NAN_HERE"}
        path.write_text(json.dumps(data).replace('"NAN_HERE"', "NaN"), encoding="utf-8")
        show = load_show(self.folder)
        self.assertIsNone(show.inputs["follow-screen"].house["dimmer-1"])  # NaN isn't JSON: it reads as null

    def test_nan_never_stops_the_dmx(self) -> None:
        self.assertEqual(clamp01(math.nan), 0.0)
        self.assertEqual(to_byte(math.nan), 0)
        self.assertEqual(clamp01(0.5), 0.5)
        self.assertEqual(to_byte(1.0), 255)
        show = load_show(self.folder)
        follow = show.inputs["follow-screen"]
        from dataclasses import replace

        odd = replace(follow, id="odd", name="Odd", house={"dimmer-1": math.nan})
        show = show.with_inputs({**show.inputs, "odd": odd}, show.input_order + ("odd",))
        output, clock = FakeOutput(), FakeClock()
        engine = Engine(show, output, None, clock=clock)
        engine.submit(SendToBase("odd", cut=True))
        for _ in range(140):  # (past the startup wait for the screen feed's first picture)
            clock.advance(0.025)
            engine.tick()
        self.assertIsNotNone(output.last)  # DMX still goes out
        self.assertEqual(engine.frames.get().faults, 0)

    def test_an_automation_with_a_bad_value_cant_stop_the_engine(self) -> None:
        show = show_from_patch()
        odd = Automation("odd", "Odd", True, "all", ({"type": "audio", "band": "level", "above": "60%"},),
                         ({"do": "base", "input": "follow-screen", "transition": {"kind": "fade", "duration": "slow"}},))
        show = show.with_automations({"odd": odd}, ("odd",))
        output, clock = FakeOutput(), FakeClock()
        engine = Engine(show, output, None, clock=clock)
        for _ in range(60):
            clock.advance(0.025)
            engine.tick()
        self.assertEqual(engine.frames.get().faults, 0)
        self.assertIn("sound (level) above", describe_condition(odd.conditions[0]))  # described (with the default), not raised
        describe_action({"do": "master", "level": "loud"})


class OwnSavesTests(unittest.TestCase):
    """CentraLight's own saves used to be read back through the loader; an in-between state (no days ticked, an
    infinite input's length) then looked like an outside change and could switch an automation off for good."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Show"
        self.doc = ShowDocument(create_show(self.folder), RecordingEngine(), save_delay_ms=10)
        self.watcher = FolderWatcher(self.doc, ask_reload=lambda _name: True, editing=lambda: "")

    def tearDown(self) -> None:
        self.watcher.stop()
        self.doc.close()
        self._tmp.cleanup()

    def test_an_automation_with_no_days_ticked_for_a_moment(self) -> None:
        from centralight.automations import add_automation, update_automation

        self.doc.set_scope(SCOPE_AUTOMATIONS)
        show, automation_id = add_automation(self.doc.show, "Sunday welcome")
        self.doc.apply(show, "New automation")
        automation = self.doc.show.automations[automation_id]
        conditions = [dict(c) for c in automation.conditions]
        conditions[0] = {"type": "days", "days": []}  # Sun unticked first...
        self.doc.apply(update_automation(self.doc.show, automation_id, conditions=conditions), "days []")
        self.doc.flush()
        self.assertTrue(self.doc.wait_saved())
        touch(self.folder / "automations" / f"{automation_id}.json")
        self.watcher.check()  # ...and the watcher sees CentraLight's own file
        self.assertTrue(self.doc.show.automations[automation_id].enabled)  # not switched off
        names = [self.doc.stack(SCOPE_AUTOMATIONS).text(i) for i in range(self.doc.stack(SCOPE_AUTOMATIONS).count())]
        self.assertFalse(any("outside" in n for n in names), names)

    def test_an_infinite_input_keeps_its_length(self) -> None:
        from dataclasses import replace

        inp = replace(self.doc.show.inputs["follow-screen"], infinite=True, length=30.0, end="loop")
        again, _warnings = input_from_dict(json.loads(json.dumps(input_to_dict(inp))))
        self.assertEqual(again, inp)  # an exact round trip: no phantom "changed outside"

    def test_another_programs_bytes_are_still_an_outside_change(self) -> None:
        data = input_to_dict(self.doc.show.inputs["follow-screen"])
        data["name"] = "Theirs"
        path = self.folder / "inputs" / "follow-screen.json"
        path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        touch(path)
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Theirs")

    def test_one_odd_file_doesnt_stop_the_others(self) -> None:
        (self.folder / "automations").mkdir(exist_ok=True)
        odd = self.folder / "automations" / "a-odd.json"
        odd.write_text(json.dumps({"format": "centralight-automation", "id": "a-odd", "conditions": [{"type": ["x"]}]}), encoding="utf-8")
        good = input_to_dict(self.doc.show.inputs["follow-screen"])
        good.update(id="z-new", name="New one")
        new = self.folder / "inputs" / "z-new.json"
        new.write_text(json.dumps(good), encoding="utf-8")
        touch(odd)
        touch(new)
        self.watcher.check()
        self.assertIn("z-new", self.doc.show.inputs)


# ---------------------------------------------------------------- automations (batch B)

from datetime import datetime as _dt, timedelta as _td  # noqa: E402

from centralight.automations import system_uptime, timed_continuous, upcoming  # noqa: E402
from centralight.commands import PowerEvent, SetBlackout, SwapShow  # noqa: E402
from centralight.live_state import LiveState  # noqa: E402
from centralight.show_io import freeze  # noqa: E402
from tests.test_automations import SUNDAY_MORNING, WELCOME, AutomationEngineTestCase, automation, uk_local  # noqa: E402


class AutomationReviewTests(AutomationEngineTestCase):
    def restart(self, live: LiveState, **kwargs) -> Engine:
        engine = Engine(self.show, FakeOutput(), live, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                        local_time=uk_local, uptime=lambda: 9999.0, **kwargs)
        self.engine = engine
        engine.tick()
        return engine

    def saved(self) -> LiveState:
        return LiveState.from_dict(json.loads(json.dumps(self.engine.live_state().to_dict())))

    def test_a_restart_mid_service_doesnt_undo_what_someone_chose(self) -> None:
        self.make(_dt(2026, 11, 8, 8, 59), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        self.run_until(_dt(2026, 11, 8, 9, 0, 30))
        self.assertEqual(self.base(), "warm-welcome")
        self.engine.submit(SendToBase("evening", cut=True))  # chosen by hand at 10:30
        self.run_until(_dt(2026, 11, 8, 10, 30))
        live = self.saved()
        self.clocks.advance(20)
        engine = self.restart(live)
        self.assertEqual(self.base(), "evening")
        self.assertEqual([h.what for h in engine._runner.history].count("ran"), 1)

    def test_delete_and_undo_mid_range_doesnt_run_it_again(self) -> None:
        self.make(_dt(2026, 11, 8, 8, 59), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        self.run_until(_dt(2026, 11, 8, 9, 0, 30))
        self.engine.submit(SendToBase("evening", cut=True))
        self.run_until(_dt(2026, 11, 8, 9, 30))
        everything = self.show
        self.engine.submit(SwapShow(everything.with_automations({}, ())))  # deleted by mistake...
        self.run_until(_dt(2026, 11, 8, 9, 31))
        self.engine.submit(SwapShow(everything))  # ...and Ctrl+Z
        self.run_until(_dt(2026, 11, 8, 9, 32))
        self.assertEqual(self.base(), "evening")
        self.assertEqual(len(self.runs("welcome")), 1)

    def test_a_midnight_service(self) -> None:
        midnight = automation("midnight", [{"type": "dates", "from": "12-24", "to": "12-24"},
                                           {"type": "time", "from": "23:00", "to": "01:00"}],
                              [{"do": "base", "input": "evening", "transition": "cut"}],
                              on_end=[{"do": "base", "input": "ping", "transition": "cut"}])
        self.make(_dt(2026, 12, 23, 23, 50), [midnight])
        self.run_until(_dt(2026, 12, 24, 0, 30))
        self.assertEqual(self.runs("midnight"), [])  # not the night before
        self.run_until(_dt(2026, 12, 24, 23, 0, 30))
        self.assertEqual(self.base(), "evening")
        self.run_until(_dt(2026, 12, 25, 0, 30))
        self.assertEqual(self.base(), "evening")  # it doesn't stop at midnight, mid-service
        self.run_until(_dt(2026, 12, 25, 1, 0, 30))
        self.assertEqual(self.base(), "ping")  # it stops at 01:00
        found = upcoming(self.show, _dt(2026, 12, 20, 12, 0), days=7)
        self.assertEqual([when for when, _a, _o in found], [_dt(2026, 12, 24, 23, 0)])

    def test_delayed_actions_dont_outlive_the_match(self) -> None:
        later = automation("later", [{"type": "input_live", "input": "warm-welcome", "where": "base"}],
                           [{"do": "overlay", "input": "ping", "slot": 1, "state": "on", "delay": 5}],
                           on_end=[{"do": "overlay_off", "slot": 1}])
        self.make(_dt(2026, 11, 9, 12, 0), [later])
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.run_until(_dt(2026, 11, 9, 12, 0, 2), step=0.5)
        self.engine.submit(SendToBase("evening", cut=True))  # moved on after 2 s
        self.run_until(_dt(2026, 11, 9, 12, 0, 10), step=0.5)
        self.assertFalse(self.engine.frames.get().overlays[0].on)  # the overlay never came on

    def test_music_with_gaps_doesnt_trip_the_loop_guard(self) -> None:
        loud = Automation("loud", "Loud", True, "all", freeze([{"type": "audio", "band": "level", "above": 0.5, "for": 5}]),
                          freeze([{"do": "overlay", "input": "x", "slot": 1, "state": "on"}]),
                          freeze([{"do": "overlay_off", "slot": 1}]))
        show = show_from_patch().with_automations({"loud": loud}, ("loud",))
        runner = AutomationRunner()
        local = _dt(2026, 11, 8, 10, 0)
        for second in range(90):  # 7 s loud, 1 s quiet, over and over
            level = 0.2 if second % 8 == 7 else 0.8
            runner.check(show, Situation(now=1000.0 + second, local=local + _td(seconds=second), audio={"level": level}),
                         1000.0 + second)
        self.assertFalse(runner.states["loud"].tripped)
        self.assertEqual([h.what for h in runner.history].count("ran"), 1)  # one run: the gaps don't stop it

    def test_asleep_through_a_range_doesnt_run_it_at_wake(self) -> None:
        self.make(_dt(2026, 11, 8, 7, 0), [automation("welcome", SUNDAY_MORNING, WELCOME)])
        self.engine.tick()
        self.clocks.jump_wall(6 * 3600)  # asleep 07:00 -> 13:00
        self.clocks.advance(0.025)
        self.engine.tick()
        self.clocks.advance(2)
        self.engine.tick()
        self.assertEqual(self.runs("welcome"), [])

    def test_a_new_days_range_after_a_sleep_inside_yesterdays(self) -> None:
        daily = automation("daily", [{"type": "time", "from": "08:00", "to": "22:00"}],
                           [{"do": "base", "input": "ping", "transition": "cut"}])
        self.make(_dt(2026, 11, 9, 20, 59), [daily])
        self.run_until(_dt(2026, 11, 9, 21, 0))
        self.assertEqual(len(self.runs("daily")), 1)
        self.clocks.jump_wall(12 * 3600)  # asleep 21:00 -> 09:00 tomorrow
        self.clocks.advance(0.025)
        self.engine.tick()
        self.clocks.advance(2)
        self.engine.tick()
        self.assertEqual(len(self.runs("daily")), 2)  # today's occurrence runs

    def test_continuity_check(self) -> None:
        welcome = automation("welcome", SUNDAY_MORNING, WELCOME)
        self.assertTrue(timed_continuous(welcome, _dt(2026, 11, 8, 9, 0), _dt(2026, 11, 8, 12, 0)))
        self.assertFalse(timed_continuous(welcome, _dt(2026, 11, 8, 9, 0), _dt(2026, 11, 15, 10, 0)))

    def test_the_pc_starts_when_windows_starts_it(self) -> None:
        self.assertGreaterEqual(system_uptime(), 0.0)  # a 64-bit number (it used to go negative after 25 days)
        startup = automation("startup", [{"type": "pc_startup"}], [{"do": "base", "input": "ping", "transition": "cut"}])
        self.make(_dt(2026, 11, 8, 8, 0), [startup])
        self.engine.tick()
        self.assertEqual(self.runs("startup"), [])  # Windows has been up an hour: not within 10 minutes
        self.restart(LiveState(), started_by_windows=True)  # started by Windows at sign-in (Fast Startup)
        self.assertEqual(self.base(), "ping")

    def test_ndi_condition_with_the_full_name(self) -> None:
        from centralight.automations import _ndi_true

        self.assertTrue(_ndi_true("DESKTOP-AR8BC8U (Media Background Pane)", {"media background pane": True}))
        self.assertTrue(_ndi_true("Media Background Pane", {"desktop-ar8bc8u (media background pane)": True}))
        self.assertFalse(_ndi_true("ProPresenter", {"media background pane": True}))
        feed = automation("feed", [{"type": "ndi", "source": "ProPresenter"}], [])
        self.make(_dt(2026, 11, 8, 8, 0), [feed])
        self.assertIn("ProPresenter", Engine._ndi_names(self.show))  # kept connected, so it can be seen

    def test_a_lights_off_restart_lets_automations_light_the_room(self) -> None:
        self.make(_dt(2026, 11, 8, 8, 59), [automation("welcome", SUNDAY_MORNING, WELCOME)],
                  live=LiveState(blackout=True))
        self.assertTrue(self.engine._blackout)
        self.run_until(_dt(2026, 11, 8, 9, 0, 30))
        self.assertEqual(self.base(), "warm-welcome")
        self.assertFalse(self.engine._blackout)  # lifted by the automation
        self.engine.submit(SetBlackout(on=True))  # a Blackout someone chooses isn't lifted
        self.engine.submit(PowerEvent("woke"))
        self.run_until(_dt(2026, 11, 8, 9, 2))
        self.assertTrue(self.engine._blackout)


# ---------------------------------------------------------------- output and engine (batch C)

from centralight.output import OUTPUT_USB, DeviceSpec, DmxOutputThread  # noqa: E402
from tests.fakes import FakePort, StubSources  # noqa: E402


def _wait_for(check, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return bool(check())


class OutputReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.thread = DmxOutputThread(DeviceSpec(OUTPUT_USB, "COM9", self.port.factory, trim=76), keepalive=5.0, retry=0.1)
        self.thread.start()

    def tearDown(self) -> None:
        self.thread.close(blackout=True, timeout=2.0)

    def test_a_light_added_past_channel_76_gets_dmx_at_once(self) -> None:
        frame = bytearray(512)
        frame[76:85] = bytes([255, 255, 255, 0, 0, 0, 0, 255, 0])  # SlimPAR 109 on channels 77-85
        self.thread.submit(bytes(frame))
        self.assertTrue(_wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.assertEqual(len(self.port.all_sent()[-1]), 76)
        self.thread.set_trim(85)  # the window does this when the show changes
        self.assertTrue(_wait_for(lambda: len(self.port.all_sent()[-1]) == 85))
        self.assertEqual(self.port.all_sent()[-1][76:85], bytes(frame[76:85]))
        self.assertEqual(len(self.port.devices), 1)  # without reopening the port (which would black out)

    def test_reconnect_after_waking_reopens_and_resends(self) -> None:
        frame = bytes([9] * 512)
        self.thread.submit(frame)
        self.assertTrue(_wait_for(lambda: len(self.port.all_sent()) >= 1))
        self.thread.reconnect()
        self.assertTrue(_wait_for(lambda: len(self.port.devices) == 2 and bool(self.port.devices[-1].sent)))
        self.assertEqual(self.port.devices[-1].sent[-1], frame[:76])  # the same look, straight away
        self.assertFalse(self.port.devices[0].closed_with_blackout)  # the lights were left as they were meanwhile


class EngineReviewTests(unittest.TestCase):
    def test_a_restart_doesnt_flash_the_lights_black_while_the_feed_connects(self) -> None:
        from centralight.new_show import FOLLOW_SCREEN_ID, follow_screen_input
        from tests.test_follow_screen import split_picture

        show = show_from_patch().with_inputs({FOLLOW_SCREEN_ID: follow_screen_input()}, (FOLLOW_SCREEN_ID,))
        sources, output, clock = StubSources(), FakeOutput(), FakeClock()
        live = LiveState(show="x", base={"input": FOLLOW_SCREEN_ID, "time": 30.0})
        engine = Engine(show, output, live, sources=sources, clock=clock)
        for _ in range(40):  # one second with no picture yet: nothing is sent (the dongle repeats the old look)
            clock.advance(0.025)
            engine.tick()
        self.assertEqual(output.frames, [])
        sources.put("Media Background Pane", split_picture())
        clock.advance(0.025)
        engine.tick()
        self.assertEqual(len(output.frames), 1)
        self.assertEqual(tuple(output.frames[0][40:43]), (255, 0, 0))  # the first frame sent already follows the screen

    def test_shuffle_then_undo_keeps_the_lights_and_legend_in_step(self) -> None:
        from dataclasses import replace

        from centralight.commands import SetIdentify

        show = show_from_patch()
        engine = Engine(show, FakeOutput(), None, clock=FakeClock())
        engine.submit(SetIdentify(True, seed=6945))  # Shuffle: the engine's seed...
        engine.submit(SwapShow(replace(show, identify_seed=6945)))  # ...and the show's (in the Layout's undo history)
        engine.tick()
        engine.submit(SwapShow(replace(show, identify_seed=7)))  # Ctrl+Z puts the show's seed back
        engine.tick()
        self.assertEqual(engine._identify_seed, 7)
        self.assertEqual(engine.frames.get().identify_seed, 7)


class VideoEndTests(unittest.TestCase):
    def test_a_video_shorter_than_it_says_doesnt_spin(self) -> None:
        from centralight.video_source import VideoClip
        from tests.test_video import make_video

        with tempfile.TemporaryDirectory() as folder:
            clip = VideoClip(make_video(Path(folder) / "short.avi"), threaded=False)
            try:
                real = clip.duration
                clip.duration = real + 0.6  # as some MKV / WebM files claim
                clip.frame_at(real - 0.5)  # playing along...
                clip.frame_at(real + 0.3)  # ...past the real end, inside the claimed length
                self.assertLessEqual(clip.duration, real + 0.11)  # the clip now ends where the pictures do
                self.assertEqual(clip._decode_towards(clip.duration - 0.05, ahead=0.5), 0)  # nothing more: no spinning
            finally:
                clip.close()


# ---------------------------------------------------------------- the window (batch D)

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from centralight.commands import SendToOverlay  # noqa: E402
from centralight.ui.main_window import MainWindow  # noqa: E402
from tests.fakes import FakeEngine  # noqa: E402
from tests.gui_fixtures import with_inputs  # noqa: E402
from tests.qt_support import process  # noqa: E402


def wheel(widget, notches: int = 1) -> None:
    centre = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(centre, QPointF(widget.mapToGlobal(centre.toPoint())), QPoint(0, 0), QPoint(0, 120 * notches),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


class WindowReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine(with_inputs(show_from_patch()))
        self.window = MainWindow(Settings(), self.engine.show, self.engine, self.engine.fake_output, save_files=False,
                                 notes_path=Path(self._tmp.name) / "fixture_map.csv")
        self.window.resize(1366, 768)
        self.window.show()
        process(self.app, 3)

    def tearDown(self) -> None:
        self.window._shut_down = True
        self.window.editor_page.shutdown()
        self.window.document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def refresh(self) -> None:
        self.engine.tick()
        self.window.bridge.poll()
        process(self.app)

    def test_the_wheel_doesnt_change_a_fader_that_hasnt_been_clicked(self) -> None:
        fader = self.window.header.house_controls["dimmer-1"].fader
        before = fader.value()
        wheel(fader, -3)
        process(self.app)
        self.engine.settle(0.2)
        self.assertEqual(fader.value(), before)
        self.assertFalse(self.engine.frames.get().house["dimmer-1"].on)  # and the festoon stayed off
        fader.setFocus()
        wheel(fader, -1)  # once it's been clicked into, the wheel works
        self.assertNotEqual(fader.value(), before)

    def test_the_padlock_locks_the_layers_panel(self) -> None:
        self.engine.submit(SendToOverlay(2, "pillars-purple", state="on"))
        self.engine.settle(0.5)
        self.refresh()
        row = self.window.dashboard.layer_rows["overlay2"]
        self.assertTrue(row.switch.isEnabled())
        self.window.set_locked(True)
        self.refresh()
        self.assertFalse(row.switch.isEnabled())
        self.assertFalse(row.opacity.isEnabled())
        self.window._overlay_switched(2, False)  # even if a click got through
        self.engine.settle(0.5)
        self.assertTrue(self.engine.frames.get().overlays[1].on)
        self.window.set_locked(False)
        self.refresh()
        self.assertTrue(row.switch.isEnabled())

    def test_a_double_click_on_an_overlay_button_is_one_click(self) -> None:
        tile = self.window.dashboard.grid.tiles["pillars-purple"]
        tile.overlay_buttons[0].click()
        tile.overlay_buttons[0].click()  # straight after: a double-click
        self.engine.settle(0.5)
        self.assertTrue(self.engine.frames.get().overlays[0].on)

    def test_typing_in_a_number_box_while_the_editor_plays(self) -> None:
        from centralight.ui.editor_widgets import NumberField

        field = NumberField(0.0, 1.0, unit="%")
        field.show()
        field.spin.setFocus()
        process(self.app)
        field.spin.lineEdit().setText("50")  # being typed...
        field.set_value(1.0)  # ...while the playing Editor pushes the current value
        self.assertTrue(field.spin.lineEdit().text().startswith("50"), field.spin.lineEdit().text())  # not "100.0 %"
        field.close()

    def test_leaving_the_editor_stops_its_drawing(self) -> None:
        self.window.open_editor("follow-screen")
        process(self.app, 3)
        self.assertIsNotNone(self.window.editor_page._renderer._requests.get())
        self.window.show_page("dashboard")
        self.assertIsNone(self.window.editor_page._renderer._requests.get())  # the render thread waits


class WindowReviewTests2(unittest.TestCase):
    setUpClass = WindowReviewTests.__dict__["setUpClass"]
    setUp = WindowReviewTests.setUp
    tearDown = WindowReviewTests.tearDown
    refresh = WindowReviewTests.refresh

    def test_undo_after_visiting_the_globals_tab_works_on_the_element(self) -> None:
        from centralight.ui.document import input_scope

        self.window.open_editor("warm-white")
        process(self.app, 2)
        page = self.window.editor_page
        from PySide6.QtWidgets import QTabWidget

        page.details.findChild(QTabWidget, "DetailsTabs").setCurrentIndex(1)  # the Globals tab (nothing selected)
        process(self.app)
        self.assertEqual(self.window.document.scope, "show")
        element_id = self.window.document.show.inputs["warm-white"].tracks[0][0]["id"]
        page.select_element(element_id)  # then an element is clicked on the timeline
        process(self.app)
        self.assertEqual(self.window.document.scope, input_scope("warm-white"))

    def test_automation_rows_show_the_undone_value(self) -> None:
        from centralight.ui.document import SCOPE_AUTOMATIONS

        self.window.show_page("automation")
        page = self.window.automation_page
        page.selected = "sunday-welcome"
        page._build_editor(self.window.document.show, force=True)
        process(self.app)
        row = page.rows["conditions"][1]  # 09:00-12:30
        row._set("to", "13:00")
        process(self.app)
        self.assertEqual(self.window.document.show.automations["sunday-welcome"].conditions[1]["to"], "13:00")
        self.window.document.set_scope(SCOPE_AUTOMATIONS)
        self.window.document.undo()
        process(self.app)
        row = page.rows["conditions"][1]
        self.assertEqual(row.data["to"], "12:30")  # the row shows the undone value...
        row._set("from", "09:30")
        self.assertEqual(dict(self.window.document.show.automations["sunday-welcome"].conditions[1]),
                         {"type": "time", "from": "09:30", "to": "12:30"})  # ...so the next edit doesn't put 13:00 back

    def test_a_dates_day_stays_inside_its_month_and_no_days_says_so(self) -> None:
        from centralight.automations import add_automation, update_automation

        self.window.show_page("automation")
        page = self.window.automation_page
        show, new_id = add_automation(self.window.document.show, "Easter")
        self.window.document.apply(show, "New automation")
        show = update_automation(self.window.document.show, new_id, conditions=[{"type": "dates", "from": "03-31", "to": "04-02"}])
        self.window.document.apply(show, "dates")
        page.selected = new_id
        page._build_editor(self.window.document.show, force=True)
        process(self.app)
        from PySide6.QtWidgets import QComboBox, QSpinBox

        row = page.rows["conditions"][0]
        day = row.findChildren(QSpinBox)[0]
        month = [c for c in row.findChildren(QComboBox) if c is not row.kind][0]
        month.setCurrentIndex(month.findData(4))  # March -> April
        process(self.app)
        self.assertEqual(self.window.document.show.automations[new_id].conditions[0]["from"], "04-30")  # never 04-31
        show = update_automation(self.window.document.show, new_id, conditions=[{"type": "days", "days": ["sun"]}])
        self.window.document.apply(show, "days")
        page._build_editor(self.window.document.show, force=True)
        process(self.app)
        row = page.rows["conditions"][0]
        self.assertFalse(row._no_days_note.isVisibleTo(row))
        row._toggle_day("sun", False)
        self.assertTrue(row._no_days_note.isVisibleTo(row))

    def test_a_stuck_engine_shows_and_diagnostics_says_so(self) -> None:
        from centralight.ui.diagnostics import report

        self.engine.is_running = lambda: True  # (a real engine thread)
        self.window._last_new_frame -= 10.0  # no new frame for 10 s
        self.window._check_engine()
        self.assertTrue(self.window.header.chips["engine"].isVisibleTo(self.window))
        self.assertIn("NOT RESPONDING", dict(report(self.engine.frames.get(), age=10.0))["Engine"])
        self.engine.settle(0.2)  # new frames: it clears
        self.window.bridge.poll()
        process(self.app)
        self.assertFalse(self.window.header.chips["engine"].isVisibleTo(self.window))

    def test_ctrl_v_on_the_dashboard_with_focus_on_the_tab_button(self) -> None:
        from unittest import mock

        from PySide6.QtTest import QTest

        self.window.header.tabs["dashboard"].setFocus()
        process(self.app)
        with mock.patch.object(MainWindow, "paste_input") as paste:
            QTest.keyClick(self.window.header.tabs["dashboard"], Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            process(self.app)
        paste.assert_called_once()

    def test_settings_dialogs_dont_pile_up(self) -> None:
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from centralight.ui.settings_dialog import SettingsDialog

        with mock.patch.object(SettingsDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            for _ in range(3):
                self.window.open_settings()
                process(self.app)
        process(self.app)
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.window.findChildren(SettingsDialog), [])

    def test_an_overlays_clock_doesnt_rewrite_live_json(self) -> None:
        from centralight.live_state import LiveState

        self.window._save_files = True
        a = LiveState(show="x", overlays=[{"input": "pillars-purple", "time": 1.0}, None, None, None])
        b = LiveState(show="x", overlays=[{"input": "pillars-purple", "time": 2.5}, None, None, None])
        from dataclasses import replace

        frame = self.engine.frames.get()
        self.window._saved_live = None
        self.window._save_live_soon(replace(frame, live=a))
        first = self.window._pending_live
        self.window._saved_live = {**a.to_dict(), "overlays": [{"input": "pillars-purple"}, None, None, None]}
        self.window._saved_live.pop("saved_at", None)
        if self.window._saved_live.get("base") is not None:
            self.window._saved_live["base"] = {"input": self.window._saved_live["base"].get("input")}
        self.window._pending_live = None
        self.window._save_live_soon(replace(frame, live=b))
        self.assertIsNone(self.window._pending_live)  # only the overlay's clock moved: nothing to save
        self.assertIsNotNone(first)
        self.window._save_files = False


if __name__ == "__main__":
    unittest.main()
