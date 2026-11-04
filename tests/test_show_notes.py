"""M13: show notes (show.json, the show menu, the Dashboard, the phone page, the AI guide, Duplicate)."""

from __future__ import annotations

import http.client
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.ai_guide import guide_text
from centralight.model import NOTES_LIMIT, clean_notes
from centralight.new_show import create_show, show_from_patch
from centralight.remote import RemoteServer
from centralight.shows import duplicate_show, list_shows
from centralight.show_io import load_show, save_show_file, show_from_dict, show_to_dict

NOTES = 'Sunday morning service.\nSend Warm welcome as people arrive. <script>alert("x")</script>'


class FileTests(unittest.TestCase):
    def test_cleaned_and_saved_in_show_json(self) -> None:
        self.assertEqual(clean_notes("\r\n  Morning\r\n\x07tea \t!\n\n"), "Morning\ntea \t!")
        self.assertEqual(len(clean_notes("x" * (NOTES_LIMIT + 50))), NOTES_LIMIT)
        self.assertEqual(clean_notes(42), "")
        show = replace(show_from_patch(), notes="Morning\nservice")
        self.assertEqual(show_to_dict(show)["notes"], "Morning\nservice")
        self.assertNotIn("notes", show_to_dict(show_from_patch()))
        self.assertEqual(show_from_dict(show_to_dict(show))[0].notes, "Morning\nservice")

    def test_duplicate_and_the_show_list_carry_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            show = replace(create_show(root / "Morning", "Morning"), notes="Morning service")
            save_show_file(show)
            copy = duplicate_show(Path(show.folder), root, "Morning copy")
            self.assertEqual(load_show(copy).notes, "Morning service")
            self.assertEqual({info.name: info.notes for info in list_shows(root)},
                             {"Morning": "Morning service", "Morning copy": "Morning service"})

    def test_names_in_files_are_never_html(self) -> None:
        """Every name read from a show's files loses "<" and ">" (Qt would show them as HTML, reading any picture a tag
        names from the disk); formulas and comparisons keep theirs."""
        from centralight.show_io import automation_from_dict, input_from_dict, layout_from_dict

        tag = '<img src="//./COM3">'
        show, _warnings = show_from_dict({**show_to_dict(show_from_patch()), "name": tag})
        self.assertNotIn("<", show.name)
        self.assertTrue(all("<" not in fx.name for fx in show.fixtures))
        inp, _warnings = input_from_dict({"id": "x", "name": tag, "tracks": [[{"id": tag, "kind": "paint",
                                                                               "opacity": {"value": 1, "expr": "a < b"}}]]})
        self.assertNotIn("<", inp.name)
        self.assertNotIn("<", inp.tracks[0][0]["id"])
        self.assertEqual(inp.tracks[0][0]["opacity"]["expr"], "a < b")
        layout, _warnings = layout_from_dict({"id": "main", "name": tag})
        self.assertNotIn("<", layout.name)
        automation, _warnings = automation_from_dict({"id": "a", "name": tag, "conditions": [
            {"type": "variable", "name": "mood", "compare": "<", "value": 1}], "actions": [{"do": "master", "level": 0.5}]})
        self.assertNotIn("<", automation.name)
        self.assertEqual(automation.conditions[0]["compare"], "<")

    def test_the_ai_guide_has_them(self) -> None:
        text = guide_text(replace(show_from_patch(), notes="For the evening service.\nKeep it calm."))
        self.assertIn("## About this show", text)
        self.assertIn("> Keep it calm.", text)
        self.assertNotIn("About this show", guide_text(show_from_patch()))


class PhoneTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_remote import RecordingEngine

        self.engine = RecordingEngine(replace(show_from_patch(), notes=NOTES))
        self.server = RemoteServer(self.engine, port=0, lan=False)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)

    def get(self, path: str) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("GET", path)
            return connection.getresponse().read().decode("utf-8")
        finally:
            connection.close()

    def test_on_the_phone_page_and_in_the_state(self) -> None:
        import json

        page = self.get("/")
        self.assertIn("Send Warm welcome as people arrive.", page)
        self.assertNotIn('<script>alert("x")</script>', page)  # (escaped)
        self.assertIn('id="notes"', page)
        state = json.loads(self.get("/api/state"))
        self.assertEqual(state["notes"], NOTES)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            window.linked_media.close()
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, notes: str = ""):
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        show = replace(create_show(self.root / "Morning", "Morning"), notes=notes)
        save_show_file(show)
        other = replace(create_show(self.root / "Evening", "Evening"), notes="Evening worship\nsecond line")
        save_show_file(other)
        engine = FakeEngine(show)
        window = MainWindow(Settings(), show, engine, engine.fake_output, save_files=False,
                            notes_path=self.root / "fixture_map.csv", shows_root=self.root)
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window

    def test_edit_them_from_the_show_menu(self) -> None:
        from PySide6.QtWidgets import QDialog

        from tests.qt_support import process

        window = self.make()
        panel = window.dashboard.notes_panel
        self.assertFalse(panel.isVisibleTo(window))  # (no notes: no panel)
        picker = window.header.show_picker
        picker.fill_menu()
        texts = [a.text() for a in picker.picker_menu.actions()]
        self.assertTrue(any("Notes about" in t for t in texts))
        self.assertTrue(any(t.strip() == "Evening worship …" for t in texts), texts)  # (under its name: the first line)
        dialog = window.edit_notes()
        dialog.editor.setPlainText("Sunday morning.\nWarm welcome first.")
        dialog.done(QDialog.DialogCode.Accepted)
        process(self.app, 2)
        self.assertEqual(window.document.show.notes, "Sunday morning.\nWarm welcome first.")
        self.assertTrue(panel.isVisibleTo(window))
        self.assertIn("Warm welcome first.", panel.text.text())
        panel.toggle.click()
        self.assertFalse(panel.text.isVisibleTo(window))
        window.document.undo_newest()
        self.assertEqual(window.document.show.notes, "")
        self.assertFalse(panel.isVisibleTo(window))

    def test_cancel_changes_nothing(self) -> None:
        from PySide6.QtWidgets import QDialog

        window = self.make("Keep these")
        dialog = window.edit_notes()
        dialog.editor.setPlainText("Something else")
        dialog.done(QDialog.DialogCode.Rejected)
        self.assertEqual(window.document.show.notes, "Keep these")


if __name__ == "__main__":
    unittest.main()
