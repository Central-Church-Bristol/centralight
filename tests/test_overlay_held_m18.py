"""M18 batch 2: Send to Overlay as each input says. The setting ("stays", the default, or "held") saved and loaded (an
older input without it stays), the Dashboard button (a copy that stays however long it's held, or one that goes when
it's let go: the mouse or Space), the Editor's choice, the remote's send and release (URLs, JSON and the live
connection), the phone page, the AI guide, and an automation's copy, which always stays."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest

from centralight.ai_guide import describe_changes, guide_text, input_schema
from centralight.automations import action_command
from centralight.commands import ClearFromOverlay, SendToOverlay
from centralight.input_edit import update_input
from centralight.model import OVERLAY_HELD, OVERLAY_STAYS, Input
from centralight.remote import command_for, held_tag
from centralight.show_io import InputFileError, input_file, input_from_dict, input_to_dict, read_input_file, save_input
from tests import test_remote
from tests.fakes import FakeEngine
from tests.qt_support import process
from tests.test_gui_editor import EditorTestCase
from tests.test_live_m16 import Client
from tests.test_overlay_m14 import held_show


class SettingTests(unittest.TestCase):
    def test_saved_and_loaded(self) -> None:
        plain = Input(id="warm", name="Warm")
        self.assertNotIn("overlay", input_to_dict(plain))  # (the default is left out of the file)
        held = replace(plain, overlay=OVERLAY_HELD)
        self.assertEqual(input_to_dict(held)["overlay"], "held")
        with tempfile.TemporaryDirectory() as folder:
            save_input(Path(folder), held)
            loaded, warnings = read_input_file(input_file(Path(folder), "warm"))
            self.assertEqual((loaded.overlay, warnings), (OVERLAY_HELD, []))
            save_input(Path(folder), plain)
            self.assertEqual(read_input_file(input_file(Path(folder), "warm"))[0].overlay, OVERLAY_STAYS)

    def test_an_older_input_stays(self) -> None:
        inp, warnings = input_from_dict({"id": "old", "name": "Old", "tracks": []})  # (from before M18: no "overlay")
        self.assertEqual((inp.overlay, warnings), (OVERLAY_STAYS, []))

    def test_a_wrong_value_is_refused_with_a_reason(self) -> None:
        with self.assertRaises(InputFileError) as caught:
            input_from_dict({"id": "x", "name": "X", "overlay": "flash", "tracks": []})
        self.assertIn('"overlay" must be one of stays, held', str(caught.exception))

    def test_the_ai_guide_and_schema(self) -> None:
        show = held_show()
        self.assertEqual(input_schema(show)["properties"]["overlay"]["enum"], ["stays", "held"])
        self.assertIn("`overlay`", guide_text(show))
        old = show.inputs["warm-white"]
        self.assertIn("Send to Overlay: stays until cleared → only while held", describe_changes(old, replace(old, overlay=OVERLAY_HELD)))

    def test_an_automations_copy_stays_whatever_the_input_says(self) -> None:
        show = held_show("worship-blue")
        command = action_command({"do": "overlay", "input": "worship-blue"}, show)
        self.assertEqual(command, SendToOverlay("worship-blue"))  # (no tag: nothing lets it go)
        engine = FakeEngine(show)
        engine.submit(command)
        engine.settle(2.0)
        self.assertEqual([c.input_id for c in engine.frames.get().overlay.copies], ["worship-blue"])


class DashboardTests(EditorTestCase):
    def frame(self, window, engine, seconds: float = 0.2):
        engine.settle(seconds)
        window.bridge.poll()
        process(self.app)
        return engine.frames.get()

    def test_stays_the_copy_is_still_there_after_the_release(self) -> None:
        window, engine = self.make(held_show("worship-blue"))
        button = window.dashboard.grid.tiles["warm-white"].overlay_button
        self.assertIn("stays until it's cleared", button.toolTip())
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual([(c.input_id, c.tag) for c in self.frame(window, engine, 1.5).overlay.copies], [("warm-white", "")])

    def test_held_the_release_clears_it(self) -> None:
        window, engine = self.make(held_show("worship-blue"))
        button = window.dashboard.grid.tiles["worship-blue"].overlay_button
        self.assertIn("only while this button is held down", button.toolTip())
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        self.assertEqual([c.input_id for c in self.frame(window, engine).overlay.copies], ["worship-blue"])
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.frame(window, engine).overlay.copies, ())

    def test_space_works_the_same_way(self) -> None:
        window, engine = self.make(held_show("worship-blue"))
        held = window.dashboard.grid.tiles["worship-blue"].overlay_button
        QTest.keyPress(held, Qt.Key.Key_Space)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)
        QTest.keyRelease(held, Qt.Key.Key_Space)
        self.assertEqual(self.frame(window, engine).overlay.copies, ())
        QTest.keyClick(window.dashboard.grid.tiles["warm-white"].overlay_button, Qt.Key.Key_Space)
        self.assertEqual([c.input_id for c in self.frame(window, engine, 1.5).overlay.copies], ["warm-white"])

    def test_losing_the_keyboard_lets_go(self) -> None:
        """(A dialog or another window in front while it's held: the copy mustn't stick.)"""
        window, engine = self.make(held_show("worship-blue"))
        held = window.dashboard.grid.tiles["worship-blue"].overlay_button
        QTest.mousePress(held, Qt.MouseButton.LeftButton)
        self.assertTrue(held.is_held())
        held.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertFalse(held.is_held())
        self.assertEqual(self.frame(window, engine).overlay.copies, ())

    def test_changing_the_setting_while_its_held_still_lets_it_go(self) -> None:
        window, engine = self.make(held_show("worship-blue"))
        held = window.dashboard.grid.tiles["worship-blue"].overlay_button
        QTest.mousePress(held, Qt.MouseButton.LeftButton)
        window.document.apply(update_input(window.document.show, "worship-blue", overlay=OVERLAY_STAYS), "Stays")
        process(self.app)
        self.assertEqual(window.dashboard.grid.tiles["worship-blue"].input.overlay, OVERLAY_STAYS)
        QTest.mouseRelease(held, Qt.MouseButton.LeftButton)
        self.assertEqual(self.frame(window, engine).overlay.copies, ())  # (what it said when it was pressed counts)


class EditorTests(EditorTestCase):
    def test_the_choice_on_the_input_tab(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        details = window.editor_page.details
        choice = details._fields["input:overlay"]
        self.assertEqual((choice.currentData(), choice.currentText()), (OVERLAY_STAYS, "Stays until cleared"))
        self.assertEqual([choice.itemText(i) for i in range(choice.count())], ["Stays until cleared", "Only while held"])
        self.assertNotIn("/overlay/release", details.copy_urls())
        choice.setCurrentIndex(choice.findData(OVERLAY_HELD))
        process(self.app)
        self.assertEqual(window.document.show.inputs["warm-white"].overlay, OVERLAY_HELD)
        self.assertEqual(window.document.undo_stack.undoText(), "Warm white: Send to Overlay, only while held")
        self.assertIn("held down", window.dashboard.grid.tiles["warm-white"].overlay_button.toolTip())
        text = details.copy_urls()
        self.assertIn("/api/input/warm-white/overlay/release", text)
        self.assertIn("Companion", text)  # (the Stream Deck app can't let go)
        window.document.undo_stack.undo()
        process(self.app)
        self.assertEqual(window.document.show.inputs["warm-white"].overlay, OVERLAY_STAYS)
        self.assertEqual(details._fields["input:overlay"].currentData(), OVERLAY_STAYS)


class RemoteTests(test_remote.RemoteTestCase):
    def setUp(self) -> None:
        super().setUp()
        held = replace(self.show.inputs["worship-blue"], overlay=OVERLAY_HELD)
        self.show = self.show.with_inputs({**self.show.inputs, "worship-blue": held}, self.show.input_order)
        self.engine.show = self.show

    def test_send_and_release(self) -> None:
        tag = held_tag("worship-blue")
        self.assertEqual(self.sent("/api/input/warm-white/overlay"), SendToOverlay("warm-white"))  # (stays)
        self.assertEqual(self.sent("/api/input/worship-blue/overlay"), SendToOverlay("worship-blue", tag=tag))
        self.assertEqual(self.sent("/api/input/worship-blue/overlay/release"), ClearFromOverlay(tag=tag))
        self.assertEqual(self.sent("/api/input/Worship%20blue/overlay/release"), ClearFromOverlay(tag=tag))  # (its name)
        # (an input whose copies stay: its release clears nothing, as only held sends carry the tag)
        self.assertEqual(self.sent("/api/input/warm-white/overlay/release"), ClearFromOverlay(tag=held_tag("warm-white")))
        self.assertEqual(self.request("/api/input/nobody/overlay/release")[0], 404)

    def test_post_command(self) -> None:
        tag = held_tag("worship-blue")
        self.assertEqual(self.request("/api/command", "POST", {"op": "overlay", "id": "worship-blue"}), (200, {"ok": True}))
        self.assertEqual(self.engine.commands[-1], SendToOverlay("worship-blue", tag=tag))
        self.assertEqual(self.request("/api/command", "POST", {"op": "overlay_release", "id": "worship-blue"}), (200, {"ok": True}))
        self.assertEqual(self.engine.commands[-1], ClearFromOverlay(tag=tag))

    def test_the_live_connection(self) -> None:
        client = Client(self.server.port)
        self.addCleanup(client.close)
        state = client.message("state")["state"]
        self.assertEqual({i["id"]: i["overlay"] for i in state["inputs"]}["worship-blue"], "held")
        self.assertTrue(client.command(1, {"path": "input/worship-blue/overlay"})["ok"])
        self.assertTrue(client.command(2, {"path": "input/worship-blue/overlay/release"})["ok"])
        tag = held_tag("worship-blue")
        self.assertEqual(self.engine.commands[-2:], [SendToOverlay("worship-blue", tag=tag), ClearFromOverlay(tag=tag)])

    def test_the_phone_page(self) -> None:
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn('data-overlay="/api/input/worship-blue/overlay" data-mode="held">Overlay (hold)</button>', page)
        self.assertIn('data-overlay="/api/input/warm-white/overlay" data-mode="stays">Overlay</button>', page)
        self.assertNotIn('data-path="/api/input/worship-blue/overlay"', page)  # (not the plain tap's handler)
        for part in ('"pointerdown"', '"pointerup", "pointercancel", "lostpointercapture"', 'call(send + "/release")'):
            self.assertIn(part, page)
        self.assertIn("Only while held", page)  # (the Website action only presses: say what to use instead)


class EngineTests(unittest.TestCase):
    def test_a_quick_tap_from_the_remote_never_leaves_a_held_copy(self) -> None:
        engine = FakeEngine(held_show("worship-blue"))

        def run(path: str) -> None:
            engine.submit(command_for(path.split("/"), {}, engine.show))

        run("input/warm-white/overlay")
        run("input/worship-blue/overlay")
        run("input/worship-blue/overlay/release")  # (straight after the press)
        run("input/warm-white/overlay/release")  # (stays: nothing)
        engine.settle(2.0)
        self.assertEqual([c.input_id for c in engine.frames.get().overlay.copies], ["warm-white"])


if __name__ == "__main__":
    unittest.main()
