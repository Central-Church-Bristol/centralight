"""M3 in the window: expressions (=), local and global variables, the Dashboard's Variables
panel, input hotkeys, Stream Deck URLs, and the remote working while a dialog is open."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTabWidget

from centralight.commands import SendToBase, SetVariable
from centralight.input_edit import find_element
from centralight.new_show import show_from_patch
from centralight.remote import RemoteServer
from centralight.settings import Settings
from centralight.ui.document import SCOPE_SHOW, input_scope
from centralight.ui.expression_edit import ExpressionDialog
from centralight.ui.main_window import MainWindow
from centralight.ui.settings_dialog import SettingsDialog
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class M3WindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window._shut_down = True
            if window.remote is not None:
                window.remote.stop()
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, remote=None) -> tuple[MainWindow, FakeEngine]:
        engine = FakeEngine(with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv", remote=remote)
        window.resize(1366, 768)
        window.show()
        process(self.app, 5)
        window.bridge.poll()
        process(self.app)
        self.windows.append(window)
        return window, engine

    def element(self, window, input_id: str, element_id: str):
        return find_element(window.document.show.inputs[input_id], element_id)[2]

    def tabs(self, window) -> QTabWidget:
        return window.editor_page.details.widget().findChild(QTabWidget)


class ExpressionTests(M3WindowTestCase):
    def test_give_a_setting_an_expression(self) -> None:
        window, engine = self.make()
        window.open_editor("warm-white")
        page = window.editor_page
        page.select_element("E1")
        process(self.app)
        details = page.details
        dialog = details.edit_expression("element:opacity")
        self.assertIsInstance(dialog, ExpressionDialog)
        dialog.edit.setText("wave(time, 0.5)")
        dialog._refresh()
        self.assertTrue(dialog.value_label.text().startswith("Right now: "))
        self.assertEqual(dialog.problem_label.text(), "")
        dialog._apply()
        process(self.app)
        self.assertEqual(self.element(window, "warm-white", "E1")["opacity"], {"value": 1.0, "expr": "wave(time, 0.5)"})
        button = details._exprs["element:opacity"]
        self.assertTrue(button.property("active"))
        self.assertFalse(button.property("error"))
        self.assertEqual(window.document.undo_group.undoText(), "Paint E1: opacity = wave(time, 0.5)")
        # changing the plain value keeps the expression (it's the expression's `value`)
        details._fields["element:opacity"].spin.setValue(50.0)
        self.assertEqual(self.element(window, "warm-white", "E1")["opacity"], {"value": 0.5, "expr": "wave(time, 0.5)"})
        window.undo()
        window.undo()
        self.assertNotIn("opacity", self.element(window, "warm-white", "E1"))

    def test_a_broken_expression_is_marked_and_explained(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        page = window.editor_page
        page.select_element("E1")
        dialog = page.details.edit_expression("element:fill.colour")
        dialog.edit.setText("mix(#FF0000, nothing_here, 0.5)")
        dialog._refresh()
        self.assertIn("nothing_here", dialog.problem_label.text())
        self.assertTrue(dialog.use_button.isEnabled())  # it can be used: it falls back until it's fixed
        dialog.edit.setText("mix(#FF0000,")
        dialog._refresh()
        self.assertFalse(dialog.use_button.isEnabled())  # it can't even be read
        dialog.edit.setText("mix(#FF0000, nothing_here, 0.5)")
        dialog._apply()
        process(self.app)
        button = page.details._exprs["element:fill.colour"]
        self.assertTrue(button.property("error"))
        self.assertIn("nothing_here", button.toolTip())

    def test_completion_inserts_a_name(self) -> None:
        window, _engine = self.make()
        window.open_editor("stage-shapes")
        page = window.editor_page
        page.select_element("E2")
        dialog = page.details.edit_expression("element:opacity")
        dialog.edit.setText("0.5 * spe")
        dialog.edit.setCursorPosition(len("0.5 * spe"))
        dialog._complete()
        self.assertEqual(dialog.completer.currentCompletion(), "speed")
        dialog._insert("speed")
        self.assertEqual(dialog.edit.text(), "0.5 * speed")
        dialog.reject()

    def test_house_level_expression(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        details = window.editor_page.details
        check, _level = details._fields["house:dimmer-4"]
        check.setChecked(True)
        dialog = details.edit_expression("house:dimmer-4")
        dialog.edit.setText("0.5 + 0.5 * wave(time)")
        dialog._apply()
        self.assertEqual(window.document.show.inputs["warm-white"].house["dimmer-4"], {"value": 1.0, "expr": "0.5 + 0.5 * wave(time)"})


class VariableTests(M3WindowTestCase):
    def test_locals(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        locals_panel = window.editor_page.details.locals_panel
        name = locals_panel.add("number")
        self.assertEqual(name, "local1")
        row = locals_panel.rows["local1"]
        row.name_edit.setText("width")
        row.name_edit.editingFinished.emit()
        self.assertEqual(window.document.show.inputs["warm-white"].locals[0]["name"], "width")
        locals_panel = window.editor_page.details.locals_panel
        locals_panel.rows["width"].value.setValue(0.25)
        self.assertEqual(window.document.show.inputs["warm-white"].locals[0]["value"], 0.25)
        bad = locals_panel.rows["width"]
        bad.name_edit.setText("sin")  # a function's name
        bad.name_edit.editingFinished.emit()
        self.assertTrue(bad.problem.isVisibleTo(bad))
        self.assertEqual(window.document.show.inputs["warm-white"].locals[0]["name"], "width")
        # two locals that use each other: both get marked
        other = locals_panel.add("number")
        dialog = locals_panel.edit_expression(other)
        dialog.edit.setText("width * 2")
        dialog._apply()
        dialog = window.editor_page.details.locals_panel.edit_expression("width")
        dialog.edit.setText(f"{other} + 1")
        dialog._apply()
        process(self.app)
        rows = window.editor_page.details.locals_panel.rows
        self.assertTrue(rows["width"].expr.property("error"))
        self.assertIn("circle", rows["width"].expr.toolTip())
        self.assertEqual(window.document.stack(input_scope("warm-white")).count(), 6)  # all in the input's own history

    def test_globals_tab_and_the_dashboard_panel(self) -> None:
        window, engine = self.make()
        window.open_editor("warm-white")
        tabs = self.tabs(window)
        tabs.setCurrentIndex(1)
        process(self.app)
        self.assertEqual(window.document.scope, SCOPE_SHOW)  # the Globals tab uses the show's history
        panel = window.editor_page.details.globals_panel
        name = panel.add("number")
        self.assertIn(name, [g.name for g in window.document.show.globals])
        panel.cards[name].dashboard.setChecked(True)
        self.assertTrue(next(g for g in window.document.show.globals if g.name == name).dashboard)
        window.show_page("dashboard")
        controls = window.dashboard.variables.controls
        self.assertIn(name, controls)
        controls[name].spin.setValue(0.8)  # moving it sets the variable on the engine
        engine.tick()
        self.assertAlmostEqual(engine.frames.get().variables[name], 0.8)
        engine.submit(SetVariable("speed", 3.0))  # e.g. from the Stream Deck: the panel follows
        window.bridge.poll()
        process(self.app)
        self.assertAlmostEqual(window.dashboard.variables.controls["speed"].value(), 3.0)
        window.undo()  # the dashboard's history has the global changes
        window.undo()
        self.assertNotIn(name, [g.name for g in window.document.show.globals])
        self.assertNotIn(name, window.dashboard.variables.controls)

    def test_locked_dashboard_variables(self) -> None:
        window, _engine = self.make()
        window.set_locked(True)
        self.assertFalse(window.dashboard.variables.isEnabled())


class HotkeyAndUrlTests(M3WindowTestCase):
    def set_hotkey(self, window, text: str) -> None:
        details = window.editor_page.details
        details.hotkey.setKeySequence(QKeySequence(text))
        details._hotkey_finished()

    def test_a_hotkey_sends_its_input(self) -> None:
        window, engine = self.make()
        window.open_editor("warm-white")
        self.set_hotkey(window, "F7")
        self.assertEqual(window.document.show.inputs["warm-white"].hotkey, "F7")
        window.show_page("dashboard")
        window.activateWindow()
        QTest.keyClick(window, Qt.Key.Key_F7)
        engine.settle(1.2)
        self.assertEqual(engine.frames.get().base_id, "warm-white")
        window.set_locked(True)
        window.fire_hotkey("worship-blue")
        engine.settle(1.2)
        self.assertEqual(engine.frames.get().base_id, "warm-white")  # Lock stops hotkeys too

    def test_hotkeys_that_clash_are_refused(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        self.set_hotkey(window, "Ctrl+Z")
        self.assertEqual(window.document.show.inputs["warm-white"].hotkey, "")
        self.assertIn("already", window.editor_page.details.hotkey_problem.text())
        self.set_hotkey(window, "F5")  # Stage shapes has F5
        self.assertEqual(window.document.show.inputs["warm-white"].hotkey, "")
        self.assertIn("Stage shapes", window.editor_page.details.hotkey_problem.text())

    def test_copy_stream_deck_urls(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        text = window.editor_page.details.copy_urls()
        self.assertEqual(QGuiApplication.clipboard().text(), text)
        self.assertIn("http://127.0.0.1:8746/api/input/warm-white", text)
        self.assertIn("/api/input/warm-white/overlay/4", text)


class RemoteInTheWindowTests(M3WindowTestCase):
    def test_the_chip_and_a_request_while_a_dialog_is_open(self) -> None:
        received: list = []
        engine_view = SimpleNamespace(show=None, frames=None, submit=received.append)
        remote = RemoteServer(engine_view, port=0)
        window, engine = self.make(remote=remote)
        engine_view.show, engine_view.frames = engine.show, engine.frames
        self.assertTrue(remote.start())
        window._update_remote_chip()
        self.assertEqual(window.header.chips["remote"].text(), f"Remote :{remote.port}")
        results: list = []

        def fire() -> None:
            for path in ("/api/input/warm-white", "/api/var/speed?value=2"):
                with urllib.request.urlopen(f"http://127.0.0.1:{remote.port}{path}", timeout=5) as reply:
                    results.append(reply.status)

        def while_open() -> None:
            worker = threading.Thread(target=fire)
            worker.start()
            worker.join(5)
            box = QApplication.activeModalWidget()
            if box is not None:
                box.done(0)

        QTimer.singleShot(50, while_open)
        box = QMessageBox(QMessageBox.Icon.Information, "A dialog", "The Stream Deck still works while this is open.", parent=window)
        box.exec()
        self.assertEqual(results, [200, 200])
        self.assertEqual(received, [SendToBase("warm-white"), SetVariable("speed", "2")])

    def test_settings_move_the_remote_to_another_port(self) -> None:
        engine_view = SimpleNamespace(show=None, frames=None, submit=lambda c: None)
        remote = RemoteServer(engine_view, port=0)
        window, engine = self.make(remote=remote)
        engine_view.show, engine_view.frames = engine.show, engine.frames
        remote.start()
        first = remote.port
        dialog = SettingsDialog(window.settings, "", window, ports=[])
        with socket.socket() as probe:  # a port nothing else is using
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        self.assertNotEqual(free, first)
        dialog.remote_port.setValue(free)
        with mock.patch.object(SettingsDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch("centralight.ui.main_window.SettingsDialog", return_value=dialog):
            window.open_settings()
        self.assertEqual(remote.port, dialog.remote_port.value())
        self.assertTrue(remote.running)
        self.assertEqual(window.header.chips["remote"].text(), f"Remote :{remote.port}")
        dialog.deleteLater()

    def test_a_busy_port_shows_on_the_chip(self) -> None:
        engine_view = SimpleNamespace(show=None, frames=None, submit=lambda c: None)
        first = RemoteServer(engine_view, port=0)
        self.assertTrue(first.start())
        self.addCleanup(first.stop)
        second = RemoteServer(engine_view, port=first.port)
        window, _engine = self.make(remote=second)
        second.start()
        window._update_remote_chip()
        self.assertEqual(window.header.chips["remote"].text(), "Remote off")
        self.assertIn(str(first.port), window.header.chips["remote"].toolTip())


if __name__ == "__main__":
    unittest.main()
