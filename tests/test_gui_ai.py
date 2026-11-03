"""M5 in the window: Create / Edit with AI (copy the prompt, paste the reply, problems and the
fix message, the live preview, Try it on the lights, Add / Replace and undo), the ways in (the New
tile, a tile's sparkle, the Editor, Ctrl+V on the Dashboard), files changed outside CentraLight
(the problem badge), the AI guide files, and the Settings AI section."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton

from centralight.new_show import create_show, show_from_patch
from centralight.settings import Settings
from centralight.show_edit import add_global
from centralight.show_io import input_to_dict
from centralight.ui.ai_dialog import AIDialog
from centralight.ui.document import SCOPE_SHOW, input_scope
from centralight.ui.input_grid import TILE_MIN, InputTile
from centralight.ui.main_window import MainWindow
from centralight.ui.settings_dialog import SettingsDialog
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app
from tests.test_ai_guide import RealisticReplyTests

REPLY = RealisticReplyTests.REPLY  # a chat AI's answer: words, a ```json block, a // comment, a trailing comma


class AIWindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            if window.ai_dialog is not None:
                window.ai_dialog.reject()
            window._shut_down = True
            window.editor_page.shutdown()
            if window.watcher is not None:
                window.watcher.stop()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, show=None, save_files: bool = False) -> tuple[MainWindow, FakeEngine]:
        engine = FakeEngine(show or with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=save_files,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.resize(1366, 768)
        window.show()
        process(self.app, 5)
        window.bridge.poll()
        process(self.app)
        self.windows.append(window)
        return window, engine

    def show_folder(self) -> Path:
        return Path(self._tmp.name) / "Show"


class CreateWithAITests(AIWindowTestCase):
    def test_the_prompt_has_the_request_and_the_lights(self) -> None:
        window, _engine = self.make()
        dialog = window.open_ai_dialog()
        self.assertIsInstance(dialog, AIDialog)
        self.assertEqual(dialog.windowTitle(), "Create an input with AI")
        dialog.description.setPlainText("the pillars breathing purple, slowly")
        dialog.name.setText("Pillars breathe")
        dialog.copy_prompt()
        prompt = QGuiApplication.clipboard().text()
        self.assertIn("the pillars breathing purple, slowly", prompt)
        self.assertIn("Pillars breathe", prompt)
        for fx in window.document.show.colour_fixtures():
            self.assertIn(fx.id, prompt)  # the AI knows every light
        self.assertEqual(dialog.copy_button.text(), "Copied ✓")
        self.assertFalse(dialog.use_button.isEnabled())  # nothing to add yet

    def test_a_good_reply_previews_and_adds_an_input(self) -> None:
        window, _engine = self.make()
        dialog = window.open_ai_dialog()
        dialog.reply.setPlainText(REPLY)
        dialog.check()
        self.assertIsNotNone(dialog.candidate)
        self.assertEqual(dialog.problems, [])
        self.assertIn("Pillars breathe", dialog.status.text())
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertTrue(dialog.light_view.isVisibleTo(dialog))  # the live preview
        self.assertFalse(dialog.fix_button.isVisibleTo(dialog))
        self.assertGreater(dialog.light_view._colours["slimpar-106"][2], 0.3)  # a pillar, purple
        self.assertLess(sum(dialog.light_view._colours["slimpar-101"]), 0.01)  # not a pillar: left alone
        self.assertTrue(dialog.use())
        show = window.document.show
        self.assertIn("pillars-breathe", show.inputs)
        self.assertTrue(show.inputs["pillars-breathe"].ai)
        self.assertEqual(show.input_order[-1], "pillars-breathe")
        self.assertEqual(window.document.stack(SCOPE_SHOW).undoText(), "Add Pillars breathe (made with AI)")
        process(self.app)
        tile = window.dashboard.grid.tiles["pillars-breathe"]
        self.assertTrue(tile._badge_labels["ai"].isVisibleTo(tile))  # the sparkle badge
        window.document.set_scope(SCOPE_SHOW)
        window.undo()
        self.assertNotIn("pillars-breathe", window.document.show.inputs)

    def test_a_bad_reply_explains_and_copies_a_fix_message(self) -> None:
        window, _engine = self.make()
        dialog = window.open_ai_dialog()
        broken = REPLY.replace('"slimpar-101"', '"pillar-left"')
        dialog.reply.setPlainText(broken)
        dialog.check()
        self.assertIsNone(dialog.candidate)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertFalse(dialog.solo_button.isEnabled())
        self.assertTrue(dialog.problems_label.isVisibleTo(dialog))
        self.assertIn("pillar-left", dialog.problems_label.text())
        self.assertFalse(dialog.light_view.isVisibleTo(dialog))
        dialog.copy_fix()
        message = QGuiApplication.clipboard().text()
        self.assertIn("pillar-left", message)
        dialog.reply.setPlainText("Sorry, I can't help with lighting.")
        dialog.check()
        self.assertTrue(dialog.problems)  # not even JSON: still explained
        dialog.reply.setPlainText("")
        dialog.check()
        self.assertEqual(dialog.problems, [])
        self.assertEqual(dialog.status.text(), "Paste a reply to check it.")

    def test_try_it_on_the_lights_without_adding_it(self) -> None:
        window, engine = self.make()
        dialog = window.open_ai_dialog()
        dialog.reply.setPlainText(REPLY)
        dialog.check()
        dialog.solo_button.setChecked(True)
        self.assertEqual(engine.frames.get().solo, "pillars-breathe")
        self.assertNotIn("pillars-breathe", window.document.show.inputs)  # only tried, not added
        window.document.apply(window.document.show, "nothing")  # a show change doesn't end the trial
        engine.settle(0.1)
        self.assertEqual(engine.frames.get().solo, "pillars-breathe")
        dialog.reject()
        self.assertEqual(engine.frames.get().solo, "")  # closing the window ends it

    def test_a_reply_that_breaks_turns_the_trial_off(self) -> None:
        window, engine = self.make()
        dialog = window.open_ai_dialog()
        dialog.reply.setPlainText(REPLY)
        dialog.check()
        dialog.solo_button.setChecked(True)
        dialog.reply.setPlainText("{ not json")
        dialog.check()
        self.assertFalse(dialog.solo_button.isChecked())
        self.assertEqual(engine.frames.get().solo, "")


class EditWithAITests(AIWindowTestCase):
    def edited_reply(self, window) -> str:
        data = input_to_dict(window.document.show.inputs["warm-white"])
        data["name"] = "Warm pink"
        data["id"] = "warm-pink"  # AIs sometimes change the id too: it's kept
        data["tracks"][0][0]["fill"] = {"type": "colour", "colour": "#FF8FB1"}
        return "Here's the warmer pink version:\n\n```json\n" + json.dumps(data, indent=2) + "\n```\n"

    def test_edit_replaces_the_input_in_one_undo_step(self) -> None:
        window, _engine = self.make()
        before = window.document.show.inputs["warm-white"]
        dialog = window.open_ai_dialog("warm-white")
        self.assertEqual(dialog.windowTitle(), "Edit “Warm white” with AI")
        self.assertEqual(dialog.use_button.text(), "Replace")
        self.assertFalse(dialog.name.isVisibleTo(dialog))
        dialog.description.setPlainText("make it pink")
        prompt = dialog.copy_prompt()
        self.assertIn("make it pink", prompt)
        self.assertIn('"warm-white"', prompt)  # the input itself is in the prompt
        dialog.reply.setPlainText(self.edited_reply(window))
        dialog.check()
        self.assertIsNotNone(dialog.candidate)
        notes = dialog.notes_label.text()
        self.assertIn("Name: Warm white → Warm pink", notes)
        self.assertIn('Its id stays "warm-white"', notes)
        self.assertTrue(dialog.use())
        inp = window.document.show.inputs["warm-white"]
        self.assertEqual(inp.name, "Warm pink")
        self.assertTrue(inp.ai)
        self.assertNotIn("warm-pink", window.document.show.inputs)
        stack = window.document.stack(input_scope("warm-white"))
        self.assertEqual(stack.undoText(), "Warm pink: changed with AI")
        window.document.set_scope(input_scope("warm-white"))
        window.undo()
        self.assertEqual(window.document.show.inputs["warm-white"], before)


class WaysInTests(AIWindowTestCase):
    def test_the_new_tile_and_a_tiles_sparkle(self) -> None:
        window, _engine = self.make()
        QTest.mouseClick(window.dashboard.grid.new_tile.ai_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(window.ai_dialog)
        self.assertEqual(window.ai_dialog.input_id, "")
        window.ai_dialog.reject()
        QTest.mouseClick(window.dashboard.grid.tiles["worship-blue"].ai_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.ai_dialog.input_id, "worship-blue")
        window.ai_dialog.reject()

    def test_the_smallest_tile_fits_its_buttons(self) -> None:
        show = with_inputs(show_from_patch())
        tile = InputTile(show.inputs["pillars-purple"], show)
        margins = tile.layout().contentsMargins()
        self.assertLessEqual(tile.bottom.minimumSize().width(), TILE_MIN - margins.left() - margins.right())
        tile.deleteLater()

    def test_house_lights_under_a_narrow_preview_keep_their_levels(self) -> None:
        from centralight.ui.light_view import LightView
        from tests.gui_fixtures import long_names_show

        view = LightView(long_names_show())
        view.set_levels({}, {"dimmer-1": 0.35, "dimmer-4": 0.8})
        texts, _rooms = view.house_texts(2000)
        self.assertEqual(texts, ["Festoon lights along the back wall 35%", "Key lights for the preacher 80%"])
        texts, _rooms = view.house_texts(300)  # too narrow for both names: they share it, shortened with …
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].endswith(" 35%") and "…" in texts[0], texts)
        self.assertTrue(texts[1].endswith("80%"), texts)
        texts, _rooms = view.house_texts(60)  # hardly any room: the levels still show
        self.assertEqual([t.split()[-1] for t in texts], ["35%", "80%"])
        view.deleteLater()

    def test_the_editor_button(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        page = window.editor_page
        self.assertTrue(page.ai_button.isEnabled())
        QTest.mouseClick(page.ai_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.ai_dialog.input_id, "warm-white")
        window.ai_dialog.reject()

    def test_lock_stops_it(self) -> None:
        window, _engine = self.make()
        window.set_locked(True)
        self.assertIsNone(window.open_ai_dialog())
        QTest.mouseClick(window.dashboard.grid.tiles["worship-blue"].ai_button, Qt.MouseButton.LeftButton)
        self.assertIsNone(window.ai_dialog)

    def test_ctrl_v_on_the_dashboard(self) -> None:
        window, _engine = self.make()
        QGuiApplication.clipboard().setText("just some words, not an input")
        self.assertIsNone(window.paste_input())
        QGuiApplication.clipboard().setText('{"name": "some other JSON"}')
        self.assertIsNone(window.paste_input())
        self.assertIsNone(window.ai_dialog)
        QGuiApplication.clipboard().setText(REPLY)
        window.activateWindow()
        grid = window.dashboard.grid
        grid.setFocus()
        process(self.app)
        QTest.keyClick(grid, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        process(self.app)
        dialog = window.ai_dialog
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(dialog.candidate)  # checked straight away
        self.assertTrue(dialog.use_button.isEnabled())


class ShowFolderTests(AIWindowTestCase):
    def bump(self, path: Path, seconds: float = 3.0) -> None:
        stamp = time.time() + seconds
        os.utime(path, (stamp, stamp))

    def test_a_broken_file_shows_a_problem_badge(self) -> None:
        window, _engine = self.make(create_show(self.show_folder()), save_files=True)
        self.assertIsNotNone(window.watcher)
        path = self.show_folder() / "inputs" / "follow-screen.json"
        good = path.read_text(encoding="utf-8")
        path.write_text("{ this isn't finished", encoding="utf-8")
        self.bump(path)
        window.watcher.check()
        process(self.app)
        tile = window.dashboard.grid.tiles["follow-screen"]
        badge = tile._badge_labels["problem"]
        self.assertTrue(badge.isVisibleTo(tile))
        self.assertIn("last good version", badge.toolTip())
        path.write_text(good.replace('"Follow screen"', '"Follow the screen"'), encoding="utf-8")
        self.bump(path, 6.0)
        window.watcher.check()
        process(self.app)
        self.assertFalse(badge.isVisibleTo(tile))
        self.assertEqual(window.document.show.inputs["follow-screen"].name, "Follow the screen")

    def test_a_picture_from_elsewhere_can_be_copied_in(self) -> None:
        window, _engine = self.make(with_inputs(create_show(self.show_folder())))
        picture = Path(self._tmp.name) / "Downloads" / "Sunrise.png"
        picture.parent.mkdir()
        image = QImage(64, 36, QImage.Format.Format_RGB32)
        image.fill(QColor("#FF8800"))
        image.save(str(picture))
        data = {"format": "centralight-input", "version": 1, "id": "sunrise", "name": "Sunrise",
                "tracks": [[{"id": "E1", "kind": "image", "file": str(picture)}], [], [], []]}
        dialog = window.open_ai_dialog("", "```json\n" + json.dumps(data) + "\n```")
        self.assertIsNone(dialog.candidate)
        self.assertIn("outside the show folder", dialog.problems_label.text())
        self.assertTrue(dialog.media_button.isVisibleTo(dialog))
        self.assertIn(str(picture), dialog.media_label.text())
        self.assertEqual(dialog.copy_media_in(), ["media/Sunrise.png"])  # clicking the button is the OK
        self.assertTrue((self.show_folder() / "media" / "Sunrise.png").is_file())
        self.assertIsNotNone(dialog.candidate)
        self.assertEqual(dialog.candidate.tracks[0][0]["file"], "media/Sunrise.png")
        self.assertFalse(dialog.media_button.isVisibleTo(dialog))
        self.assertTrue(dialog.use())
        self.assertEqual(window.document.show.inputs["sunrise"].tracks[0][0]["file"], "media/Sunrise.png")

    def test_the_ai_guide_is_written_and_kept_up_to_date(self) -> None:
        window, _engine = self.make(create_show(self.show_folder()), save_files=True)
        self.assertTrue(window._guide_timer.isActive())
        window._guide_timer.stop()
        window._write_guide()
        for name in ("AI_GUIDE.md", "AGENTS.md", "input.schema.json"):
            self.assertTrue((self.show_folder() / name).is_file(), name)
        show, name = add_global(window.document.show, "colour")
        window.document.apply(show, "Add a global")
        self.assertTrue(window._guide_timer.isActive())  # rewritten soon
        window._guide_timer.stop()
        window._write_guide()
        self.assertIn(f"`{name}`", (self.show_folder() / "AI_GUIDE.md").read_text(encoding="utf-8"))
        window.document.apply(window.document.show, "nothing")  # nothing the guide uses: no rewrite
        self.assertFalse(window._guide_timer.isActive())


class SettingsAITests(AIWindowTestCase):
    def test_export_the_claude_skill(self) -> None:
        show = create_show(self.show_folder())
        dialog = SettingsDialog(Settings(), str(self.show_folder()), None, show=show)
        export = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Export Claude skill…")
        folders = [b for b in dialog.findChildren(QPushButton) if b.text() == "Open show folder"]
        self.assertEqual(len(folders), 2)  # in Files, and in AI (where someone using Claude Code or Cursor looks)
        target = Path(self._tmp.name) / "Skills"
        target.mkdir()
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=str(target)):
            QTest.mouseClick(export, Qt.MouseButton.LeftButton)
        skill = target / "centralight-inputs" / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertTrue(skill.read_text(encoding="utf-8").startswith("---\nname: centralight-inputs"))
        self.assertTrue(dialog.ai_status.text().startswith("Saved: "))
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
