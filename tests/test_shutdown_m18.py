"""M18 batch 4: the PC shutting down or going to sleep. Two global automation events, "The PC shuts down or restarts"
and "The PC goes to sleep": their actions run at once (no countdown, fades cut short), Windows waits for the fade (at
most a few seconds) and the last frame, and a quit because Windows ends the session never holds the lights off at the
next start (so the next PC start is like any other: tomorrow's whole-day event loads its show, and its Start Up runs)."""

from __future__ import annotations

import ctypes
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from centralight.automations import GLOBAL, SHOW, check_condition, conditions_for, describe_condition, summary
from centralight.commands import SHUTDOWN_FADE, SLEEP_FADE, LoadShow, PowerEvent, SendToBase, SetHouse
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import NO_DEFAULT_SHOW, NO_SHOW
from centralight.output import encode_dmx
from centralight.settings import QUIT_ASK, QUIT_OFF, Settings
from centralight.show_io import automation_from_dict, automation_to_dict
from centralight.ui.power import (
    BLOCK_REASON,
    ENDSESSION_CLOSEAPP,
    SHUTDOWN_WAIT,
    SLEEP_WAIT,
    WM_ENDSESSION,
    WM_QUERYENDSESSION,
    PowerDown,
    PowerEventFilter,
    _MSG,
    power_event,
)
from tests.fakes import FakeOutput
from tests.test_automations import AutomationEngineTestCase, automation, uk_epoch, uk_local
from tests.test_gui import WindowTestCase

ENDSESSION_LOGOFF = 0x80000000
LIGHTS_OUT = automation("lights-out", [{"type": "pc_shutdown"}], [{"do": "fade_black", "fade": 3, "all": True}])
# tomorrow's event, as the user has it: a whole day, loading Central Church (Settings' Default show is None)
DIOCESAN = automation("dioscesioan-event", [{"type": "dates", "from": "11-07", "to": "11-07", "year": 2026}],
                      [{"do": "load_show", "show": "central-church"}])
START_UP = automation("start-up", [{"type": "show_loads"}], [{"do": "base", "input": "warm-welcome", "transition": "cut"}])


class FakeWindows:
    """Windows' side of the shutdown message, for PowerDown: a clock that sleeping moves on, the block reason's calls,
    and (engine) a real engine ticked as the clock moves."""

    def __init__(self, engine: Engine | None = None, clocks=None) -> None:
        self.calls: list[tuple] = []
        self.time = 0.0
        self.engine = engine
        self.clocks = clocks
        self.done_after: float | None = None  # (no engine: done is set this many seconds after the submit)
        self.waiting: list[tuple[float, threading.Event]] = []

    def clock(self) -> float:
        return self.clocks.mono if self.clocks is not None else self.time

    def sleep(self, seconds: float) -> None:
        if self.clocks is not None:
            self.clocks.advance(seconds)
            self.engine.tick()
            return
        self.time += seconds
        for due, done in self.waiting:
            if self.time >= due:
                done.set()

    def submit(self, command) -> None:
        self.calls.append(("submit", command.kind))
        if self.engine is not None:
            self.engine.submit(command)
            self.engine.tick()  # (the engine thread picks it up at once)
        elif command.done is not None and self.done_after is not None:
            self.waiting.append((self.time + self.done_after, command.done))

    def block(self, hwnd: int, reason: str) -> bool:
        self.calls.append(("block", hwnd, reason))
        return True

    def unblock(self, hwnd: int) -> bool:
        self.calls.append(("unblock", hwnd))
        return True

    def handler(self) -> PowerDown:
        return PowerDown(self.submit, block=self.block, unblock=self.unblock, clock=self.clock, sleep=self.sleep)


class ConditionTests(unittest.TestCase):
    def test_global_only_and_they_check_save_load_and_read_well(self) -> None:
        for kind, name in (("pc_shutdown", "The PC shuts down or restarts"), ("pc_sleep", "The PC goes to sleep")):
            self.assertEqual(conditions_for(GLOBAL)[kind], name)
            self.assertNotIn(kind, conditions_for(SHOW))
            self.assertEqual(check_condition({"type": kind}, GLOBAL), [])
            self.assertTrue(check_condition({"type": kind}, SHOW))  # (a show's automation can't use them)
            self.assertEqual(describe_condition({"type": kind, "invert": True}), f"not ({name})")
        data = json.loads(json.dumps(automation_to_dict(LIGHTS_OUT)))
        loaded, warnings = automation_from_dict(data, "lights-out", scope="global")
        self.assertEqual((loaded.conditions, warnings, loaded.enabled), (LIGHTS_OUT.conditions, [], True))
        in_a_show, warnings = automation_from_dict(data, "lights-out", scope="show")
        self.assertFalse(in_a_show.enabled)  # (switched off until its condition is fixed, as any wrong condition)
        self.assertIn("unknown condition 'pc_shutdown'", " ".join(warnings))
        self.assertEqual(summary(LIGHTS_OUT, scope=GLOBAL),
                         "The PC shuts down or restarts → fade to black (3 s), Festoon and Key lights too")

    def test_the_ai_guides_name_them(self) -> None:
        from centralight.ai_guide import global_guide_text

        text = global_guide_text([("central-church", "Central Church")], ["Festoon", "Key lights"])
        self.assertIn("`pc_shutdown` (the pc shuts down or restarts)", text)
        self.assertIn("`pc_sleep` (the pc goes to sleep)", text)
        self.assertIn("power cut", text)


class MessageTests(unittest.TestCase):
    def test_windows_messages(self) -> None:
        self.assertEqual(power_event(WM_QUERYENDSESSION, 0, 0), "shutdown")  # (shutting down or restarting)
        self.assertEqual(power_event(WM_QUERYENDSESSION, 0, ENDSESSION_LOGOFF), "shutdown")  # (signing out)
        self.assertEqual(power_event(WM_QUERYENDSESSION, 0, ENDSESSION_CLOSEAPP), "shutdown")  # (closed for an update)
        self.assertEqual(power_event(WM_ENDSESSION, 0, 0), "shutdown cancelled")
        self.assertEqual(power_event(WM_ENDSESSION, 1, 0), "")  # (Qt quits then, as before)

    def test_the_filter_passes_the_window_on_and_lets_qt_answer(self) -> None:
        seen = []
        power_filter = PowerEventFilter(lambda kind, hwnd: seen.append((kind, hwnd)))
        msg = _MSG(hwnd=0x1234, message=WM_QUERYENDSESSION, wParam=0, lParam=0)
        self.assertEqual(power_filter.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)), (False, 0))
        self.assertEqual(seen, [("shutdown", 0x1234)])


class PowerDownTests(unittest.TestCase):
    def test_a_shutdown_waits_for_the_fade_then_lets_windows_carry_on(self) -> None:
        windows = FakeWindows()
        windows.done_after = 3.0  # (the engine says the lights got there after 3 s)
        handler = windows.handler()
        handler("shutdown", 0x42)
        self.assertEqual(windows.calls, [("block", 0x42, BLOCK_REASON), ("submit", "shutdown"), ("unblock", 0x42)])
        self.assertAlmostEqual(windows.time, 3.0, delta=0.05)
        handler("shutdown", 0x43)  # (Windows asks the next window: the lights have been seen to)
        self.assertEqual(len(windows.calls), 3)
        handler("shutdown cancelled", 0x42)  # (another app stopped it: a later shutdown fades again)
        handler("shutdown", 0x42)
        self.assertEqual(windows.calls[3:], [("block", 0x42, BLOCK_REASON), ("submit", "shutdown"), ("unblock", 0x42)])

    def test_never_longer_than_windows_waits(self) -> None:
        windows = FakeWindows()  # (the lights never get there: the engine is stuck, say)
        windows.handler()("shutdown", 0x42)
        self.assertAlmostEqual(windows.time, SHUTDOWN_WAIT, delta=0.05)
        self.assertLess(SHUTDOWN_WAIT, 5.0)  # (Windows waits about 5 s)
        self.assertEqual(windows.calls[-1], ("unblock", 0x42))

    def test_sleep_isnt_held_and_waits_less(self) -> None:
        windows = FakeWindows()
        handler = windows.handler()
        handler("sleeping", 0x42)
        self.assertEqual(windows.calls, [("submit", "sleeping")])
        self.assertAlmostEqual(windows.time, SLEEP_WAIT, delta=0.05)
        self.assertLess(SLEEP_WAIT, 2.0)  # (Windows allows about 2 s)
        handler("sleeping", 0x43)  # (told to another of its windows: the same sleep)
        self.assertEqual(len(windows.calls), 1)
        handler("woke", 0x42)
        handler("sleeping", 0x42)  # (the next sleep)
        self.assertEqual(windows.calls[1:], [("submit", "woke"), ("submit", "sleeping")])

    def test_a_failed_block_still_lets_windows_carry_on(self) -> None:
        windows = FakeWindows()
        windows.done_after = 0.5

        def broken(_hwnd, _reason):
            raise OSError("no user32 here")

        PowerDown(windows.submit, block=broken, unblock=windows.unblock, clock=windows.clock, sleep=windows.sleep)("shutdown", 7)
        self.assertEqual(windows.calls, [("submit", "shutdown")])  # (nothing to unblock)
        self.assertAlmostEqual(windows.time, 0.5, delta=0.05)

    def test_waking_goes_to_the_engine(self) -> None:
        windows = FakeWindows()
        windows.handler()("woke", 0x42)
        self.assertEqual(windows.calls, [("submit", "woke")])


class LaggingOutput(FakeOutput):
    """An output whose thread hasn't sent the newest frame yet (until `send`)."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: bytes | None = None

    def last_sent(self) -> bytes | None:
        return self.sent


class EngineTests(AutomationEngineTestCase):
    def black(self) -> bytes:
        return encode_dmx(self.show.fixtures, {fx.id: (0.0, 0.0, 0.0) for fx in self.show.colour_fixtures()},
                          {h.fixture: 0.0 for h in self.show.house})

    def lit(self) -> None:
        self.engine.submit(SendToBase("warm-welcome", cut=True))
        self.engine.submit(SetHouse("dimmer-4", level=0.7, on=True, fade=0.0))
        for _ in range(3):
            self.clocks.advance(0.025)
            self.engine.tick()
        self.assertNotEqual(self.output.last, self.black())

    def test_a_long_fade_is_cut_to_four_seconds_with_no_countdown(self) -> None:
        slow = automation("lights-out", [{"type": "pc_shutdown"}], [{"do": "fade_black", "fade": 30, "all": True}])
        self.make(datetime(2026, 11, 6, 21, 0), [], global_automations=[slow])
        self.run_until(datetime(2026, 11, 6, 21, 2), step=1.0)  # (past the start-up minute: countdowns are on)
        self.assertGreater(self.engine.countdown_seconds, 0.0)
        self.lit()
        done = threading.Event()
        self.engine.submit(PowerEvent("shutdown", done))
        self.clocks.advance(0.025)
        self.engine.tick()
        self.assertEqual(len(self.global_runs("lights-out")), 1)
        self.assertIsNone(self.engine.frames.get().countdown)  # (at once: no banner)
        self.assertTrue(self.engine._fading(self.clocks.mono))
        self.clocks.advance(SHUTDOWN_FADE - 0.5)
        self.engine.tick()
        self.assertFalse(done.is_set())  # (still fading)
        self.clocks.advance(0.6)
        self.engine.tick()
        self.assertTrue(done.is_set())
        self.assertEqual(self.output.last, self.black())  # (the Key lights too)

    def test_nothing_about_it_lets_windows_carry_on_at_once(self) -> None:
        self.make(datetime(2026, 11, 6, 21, 0), [], global_automations=[DIOCESAN])
        self.engine.tick()
        self.engine.submit(SendToBase("evening", cut=True))
        self.clocks.advance(1.0)
        self.engine.tick()
        done = threading.Event()
        self.engine.submit(PowerEvent("shutdown", done))
        self.clocks.advance(0.025)
        self.engine.tick()
        self.assertTrue(done.is_set())
        self.assertEqual(self.base(), "evening")  # (left as it was)

    def test_it_waits_for_the_last_frame_to_go_out(self) -> None:
        self.make(datetime(2026, 11, 6, 21, 0), [], global_automations=[LIGHTS_OUT])
        self.output = LaggingOutput()
        self.engine.output = self.output
        self.engine.tick()
        done = threading.Event()
        self.engine.submit(PowerEvent("shutdown", done))
        for _ in range(4 * 40):
            self.clocks.advance(0.025)
            self.engine.tick()
        self.assertFalse(done.is_set())  # (the fade is over, but the frame hasn't gone out)
        self.output.sent = self.output.last  # (the output thread sends it)
        self.clocks.advance(0.025)
        self.engine.tick()
        self.assertTrue(done.is_set())

    def test_with_the_dongle_unplugged_there_is_no_frame_to_wait_for(self) -> None:
        from centralight.output import ERROR, OutputStatus

        self.make(datetime(2026, 11, 6, 21, 0), [], global_automations=[LIGHTS_OUT])
        self.output = LaggingOutput()
        self.output.status_value = OutputStatus(kind="usb", state=ERROR, label="COM3")
        self.engine.output = self.output
        self.engine.tick()
        done = threading.Event()
        self.engine.submit(PowerEvent("shutdown", done))
        self.clocks.advance(0.025)
        self.engine.tick()
        self.assertTrue(done.is_set())

    def test_sleep_runs_its_automations_with_a_short_fade(self) -> None:
        night = automation("night", [{"type": "pc_sleep"}], [{"do": "fade_black", "fade": 5, "all": True}])
        self.make(datetime(2026, 11, 6, 23, 0), [], global_automations=[night, LIGHTS_OUT])
        self.engine.tick()
        self.lit()
        windows = FakeWindows(self.engine, self.clocks)
        start = self.clocks.mono
        windows.handler()("sleeping", 0x42)
        self.assertEqual(len(self.global_runs("night")), 1)
        self.assertEqual(self.global_runs("lights-out"), [])  # (sleep isn't a shutdown)
        self.assertLessEqual(self.clocks.mono - start, SLEEP_FADE + 0.2)  # (done once the 1 s fade was over)
        self.assertEqual(self.output.last, self.black())
        self.assertNotIn(("block", 0x42, BLOCK_REASON), windows.calls)  # (Windows can't be held going to sleep)


class TomorrowTests(AutomationEngineTestCase):
    """The rule for tomorrow's Diocesan event: a Windows shutdown must never hold the lights off at the next start."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def shut_down_tonight(self, hold_off: bool = False) -> LiveState:
        """Friday 6 Nov, 21:00: the lights are on, and Windows shuts down (the fake end-session, with the shutdown's
        fade), then CentraLight quits with "Turn the lights off" (as Windows ends the session: hold_off False)."""
        self.make(datetime(2026, 11, 6, 21, 0), [], global_automations=[LIGHTS_OUT, DIOCESAN], uptime=12 * 3600.0)
        self.engine.tick()
        self.engine.submit(SendToBase("evening", cut=True))
        self.engine.submit(SetHouse("dimmer-4", level=0.7, on=True, fade=0.0))
        self.run_until(datetime(2026, 11, 6, 21, 5), step=1.0)
        windows = FakeWindows(self.engine, self.clocks)
        windows.handler()("shutdown", 0x42)  # (WM_QUERYENDSESSION)
        self.assertEqual([c[0] for c in windows.calls], ["block", "submit", "unblock"])
        self.assertEqual(len(self.global_runs("lights-out")), 1)
        self.assertEqual(self.base(), "")
        self.assertFalse(self.engine.frames.get().house["dimmer-4"].on)
        live = self.engine.stop(lights_off=True, fade=False, hold_off=hold_off)  # (WM_ENDSESSION: the quit)
        live.saved_at = self.clocks.wall  # (as LiveState.save() stamps it)
        return LiveState.from_dict(json.loads(json.dumps(live.to_dict())))

    def start_tomorrow(self, live: LiveState) -> Engine:
        """Saturday 7 Nov, 08:00: the PC starts (Windows has been up 90 s). The default show is None, so no show opens
        (app.show_to_open), and the whole-day event loads Central Church."""
        from centralight.app import show_to_open

        self.clocks.advance(uk_epoch(datetime(2026, 11, 7, 8, 0)) - self.clocks.wall)
        self.clocks.uptime_value = 90.0
        self.assertIsNone(show_to_open(Settings(default_show=NO_DEFAULT_SHOW), live, pc_start=True))
        self.output = FakeOutput()
        self.engine = Engine(NO_SHOW, self.output, live.for_show(NO_SHOW.folder), clock=lambda: self.clocks.mono,
                             wall=lambda: self.clocks.wall, local_time=uk_local, uptime=lambda: self.clocks.uptime_value,
                             global_automations=self.global_automations, default_show=NO_DEFAULT_SHOW)
        self.clocks.advance(1.0)
        self.engine.tick()
        requests = []
        while not self.engine.gui_requests.empty():
            requests.append(self.engine.gui_requests.get()[1])
        self.assertEqual([r.get("show") for r in requests if r.get("do") == "load_show"], ["central-church"])
        central = replace(self.show, name="Central Church", folder=str(Path(self._tmp.name) / "Central Church")).with_automations(
            {START_UP.id: START_UP}, (START_UP.id,))
        self.engine.submit(LoadShow(central, automation=True))  # (the window loads it, as it does for the request)
        for _ in range(3):
            self.clocks.advance(0.025)
            self.engine.tick()
        return self.engine

    def test_a_shutdown_tonight_doesnt_keep_tomorrows_event_dark(self) -> None:
        engine = self.start_tomorrow(self.shut_down_tonight())
        self.assertTrue(engine._pc_start)
        self.assertFalse(engine._quit_dark)
        self.assertEqual(len(self.global_runs("dioscesioan-event")), 1)
        self.assertEqual(engine.show.name, "Central Church")
        self.assertEqual(len(self.runs("start-up")), 1)  # ("When this show loads")
        self.assertEqual(self.base(), "warm-welcome")  # (the lights come on)

    def test_a_quit_by_hand_with_the_lights_off_still_holds_them(self) -> None:
        """(As before, for a quit from the tray with "Turn the lights off": a whole day doesn't lift it.)"""
        engine = self.start_tomorrow(self.shut_down_tonight(hold_off=True))
        self.assertTrue(engine._quit_dark)
        self.assertEqual(self.base(), "")


class WindowQuitTests(WindowTestCase):
    def test_windows_ending_the_session_never_holds_the_lights_off(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_OFF))
        window.quit_without_asking("as Windows logs off or shuts down")
        self.assertTrue(engine.fake_output.closed_with_blackout)  # ("Turn the lights off": they go off...)
        self.assertFalse(engine._quit_dark)  # (...but aren't held off at the next start)

    def test_a_quit_by_hand_still_holds_them(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_OFF))
        window.shutdown(lights_off=True)  # (as Quit does with "Turn the lights off")
        self.assertTrue(engine._quit_dark)

    def test_ask_leaves_them(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_ASK))
        window.quit_without_asking("as Windows logs off or shuts down")
        self.assertFalse(engine.fake_output.closed_with_blackout)
        self.assertFalse(engine._quit_dark)

    def test_the_settings_hint(self) -> None:
        from PySide6.QtWidgets import QLabel

        from centralight.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(Settings(), "")
        self.addCleanup(dialog.deleteLater)
        group = dialog.quit_action.parentWidget()
        texts = [label.toolTip() for label in group.findChildren(QLabel)] + [dialog.quit_action.toolTip()]
        self.assertTrue(any("The PC shuts down or restarts" in t and "The PC goes to sleep" in t for t in texts))  # (8 Nov:
        # in the quit choice's tooltip)

    def test_the_editors_note_says_what_they_cant_catch(self) -> None:
        from centralight.ui.automation_page import EVENT_HINTS

        for kind in ("pc_shutdown", "pc_sleep"):
            self.assertIn("power cut", EVENT_HINTS[kind])
            self.assertIn("crash", EVENT_HINTS[kind])


if __name__ == "__main__":
    unittest.main()
