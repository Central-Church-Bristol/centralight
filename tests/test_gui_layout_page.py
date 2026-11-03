"""The dashboard input grid and the Layout page, driven like a person would."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from centralight.new_show import create_show, show_from_patch
from centralight.notes import load_notes
from centralight.settings import Settings
from centralight.ui.main_window import MainWindow
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class M1WindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.notes_path = Path(self._tmp.name) / "fixture_map.csv"
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window._shut_down = True
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, show=None, size=(1366, 768)) -> tuple[MainWindow, FakeEngine]:
        engine = FakeEngine(show or with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False, notes_path=self.notes_path)
        window.resize(*size)
        window.show()
        process(self.app, 5)
        window.bridge.poll()
        process(self.app)
        self.windows.append(window)
        return window, engine


class InputGridTests(M1WindowTestCase):
    def test_tiles_send_to_base_and_mark_it(self) -> None:
        window, engine = self.make()
        grid = window.dashboard.grid
        self.assertEqual(list(grid.tiles), ["follow-screen", "stage-shapes", "warm-white", "pillars-purple", "worship-blue", "christmas", "off"])
        QTest.mouseClick(grid.tiles["warm-white"], Qt.MouseButton.LeftButton)
        engine.settle(1.2)
        window.bridge.poll()
        process(self.app)
        self.assertEqual(engine.frames.get().base_id, "warm-white")
        self.assertTrue(grid.tiles["warm-white"].property("onBase"))
        self.assertFalse(grid.tiles["follow-screen"].property("onBase"))
        self.assertIn("Warm white", window.dashboard.caption.full_text())

    def test_shift_click_cuts(self) -> None:
        window, engine = self.make()
        QTest.mouseClick(window.dashboard.grid.tiles["off"], Qt.MouseButton.LeftButton)  # fades over 1 s
        engine.tick()
        self.assertIsNotNone(engine.frames.get().base_progress)
        QTest.mouseClick(window.dashboard.grid.tiles["worship-blue"], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
        engine.tick()
        frame = engine.frames.get()
        self.assertEqual(frame.base_id, "worship-blue")
        self.assertIsNone(frame.base_progress)
        self.assertEqual(engine.fake_output.last[4:7], bytes((0x20, 0x50, 0xFF)))  # SlimPAR 101 is worship blue at once

    def test_locked_tiles_do_nothing(self) -> None:
        window, engine = self.make()
        window.set_locked(True)
        QTest.mouseClick(window.dashboard.grid.tiles["warm-white"], Qt.MouseButton.LeftButton)
        engine.settle(0.2)
        self.assertEqual(engine.frames.get().base_id, "")

    def test_timed_and_ai_badges(self) -> None:
        window, _engine = self.make()
        tiles = window.dashboard.grid.tiles
        self.assertTrue(tiles["christmas"]._badge_labels["timed"].isVisibleTo(tiles["christmas"]))
        self.assertTrue(tiles["pillars-purple"]._badge_labels["ai"].isVisibleTo(tiles["pillars-purple"]))
        self.assertFalse(tiles["warm-white"]._badge_labels["ai"].isVisibleTo(tiles["warm-white"]))

    def test_tiles_wrap_into_rows_of_equal_width(self) -> None:
        window, _engine = self.make(size=(1280, 720))
        tiles = list(window.dashboard.grid.tiles.values())
        widths = {tile.width() for tile in tiles}
        self.assertEqual(len(widths), 1)
        self.assertTrue(190 <= widths.pop() <= 270)
        rows = {tile.y() for tile in tiles}
        self.assertGreater(len(rows), 1)


class LayoutPageTests(M1WindowTestCase):
    def open_layout(self, show=None):
        window, engine = self.make(show)
        QTest.mouseClick(window.header.tabs["layout"], Qt.MouseButton.LeftButton)
        process(self.app, 3)
        return window, engine, window.layout_page

    def drag_item(self, page, fixture_id: str, dx: int, dy: int) -> None:
        item = page._items[fixture_id]
        viewport = page.view.viewport()
        start = page.view.mapFromScene(item.pos())
        end = start + QPoint(dx, dy)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for step in range(1, 6):
            QTest.mouseMove(viewport, start + QPoint(dx * step // 5, dy * step // 5))
            process(self.app)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        process(self.app, 2)

    def test_dragging_a_dot_moves_the_light_in_one_undo_step(self) -> None:
        window, engine, page = self.open_layout()
        before = window.document.show.fixture("slimpar-106").map
        self.drag_item(page, "slimpar-106", 60, -30)
        after = window.document.show.fixture("slimpar-106").map
        self.assertGreater(after.x, before.x + 0.03)
        self.assertLess(after.y, before.y - 0.03)
        self.assertEqual(window.document.undo_stack.count(), 1)
        self.assertEqual(engine.show.fixture("slimpar-106").map, after)  # the engine follows the drag
        window.undo()
        self.assertEqual(window.document.show.fixture("slimpar-106").map, before)
        self.assertAlmostEqual(page._items["slimpar-106"].pos().x(), before.x * 640, delta=0.01)

    def test_clicking_a_dot_flashes_its_light(self) -> None:
        window, engine, page = self.open_layout()
        item = page._items["slimpar-103"]
        point = page.view.mapFromScene(item.pos())
        QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        engine.tick()
        self.assertIn("slimpar-103", engine._flashes)
        self.assertEqual(window.document.undo_stack.count(), 0)

    def test_table_edits_and_where_notes(self) -> None:
        window, _engine, page = self.open_layout()
        table = page.table
        row = [table.row_id(r) for r in range(table.rowCount())].index("slimpar-101")
        table.item(row, table.column("name")).setText("Drum wash")
        self.assertEqual(window.document.show.fixture("slimpar-101").name, "Drum wash")
        row = [table.row_id(r) for r in range(table.rowCount())].index("slimpar-101")
        table.item(row, table.column("where")).setText("above the drum kit")
        self.assertEqual(load_notes(self.notes_path)["Drum wash"], "above the drum kit")
        row = [table.row_id(r) for r in range(table.rowCount())].index("slimpar-102")
        table.item(row, table.column("address")).setData(Qt.ItemDataRole.EditRole, 10)
        process(self.app)
        self.assertEqual(window.document.show.fixture("slimpar-102").address, 10)
        row = [table.row_id(r) for r in range(table.rowCount())].index("slimpar-102")
        address_item = table.item(row, table.column("address"))
        self.assertIn("Overlaps", address_item.toolTip())  # 10-18 overlaps SlimPAR 101 (5-13)
        self.assertEqual(address_item.foreground().color(), QColor("#FF3B5C"))

    def test_identify_goes_off_when_leaving_the_page(self) -> None:
        window, engine, page = self.open_layout()
        QTest.mouseClick(page.identify_switch, Qt.MouseButton.LeftButton)
        engine.tick()
        self.assertTrue(engine.frames.get().identify)
        QTest.mouseClick(window.header.tabs["dashboard"], Qt.MouseButton.LeftButton)
        engine.tick()
        self.assertFalse(engine.frames.get().identify)

    def test_test_colours_on_the_selection_then_all(self) -> None:
        window, engine, page = self.open_layout()
        page._items["slimpar-108"].setSelected(True)
        QTest.mouseClick(page.test_buttons["Green"], Qt.MouseButton.LeftButton)
        engine.tick()
        self.assertEqual(engine.fake_output.last[67:70], bytes((0, 255, 0)))
        self.assertEqual(engine.fake_output.last[4:7], bytes((0, 0, 0)))
        page.scene.clearSelection()
        QTest.mouseClick(page.test_buttons["White"], Qt.MouseButton.LeftButton)
        engine.tick()
        self.assertEqual(engine.fake_output.last[4:7], bytes((255, 255, 255)))
        QTest.mouseClick(page.stop_test_button, Qt.MouseButton.LeftButton)
        engine.tick()
        self.assertFalse(engine.frames.get().testing)

    def test_add_and_remove_lights(self) -> None:
        window, _engine, page = self.open_layout()
        QTest.mouseClick(page.add_button, Qt.MouseButton.LeftButton)
        added = [fx for fx in window.document.show.colour_fixtures() if fx.name == "SlimPAR 109"]
        self.assertEqual(len(added), 1)
        self.assertIn(added[0].id, page._items)
        self.assertEqual(page.selected_ids(), [added[0].id])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
        self.assertIsNone(window.document.show.fixture(added[0].id))
        self.assertNotIn(added[0].id, page._items)
        window.undo()
        self.assertIsNotNone(window.document.show.fixture(added[0].id))

    def test_align_and_exact_position(self) -> None:
        window, _engine, page = self.open_layout()
        for fid in ("slimpar-105", "slimpar-106", "slimpar-107"):
            page._items[fid].setSelected(True)
        page.align("top")
        ys = {window.document.show.fixture(f).map.y for f in ("slimpar-105", "slimpar-106", "slimpar-107")}
        self.assertEqual(len(ys), 1)
        page.scene.clearSelection()
        page._items["slimpar-101"].setSelected(True)
        process(self.app)
        page.x_spin.setValue(33.0)
        self.assertAlmostEqual(window.document.show.fixture("slimpar-101").map.x, 0.33)

    def test_room_photo_is_copied_into_the_show(self) -> None:
        folder = Path(self._tmp.name) / "Show"
        show = with_inputs(create_show(folder))
        window, _engine, page = self.open_layout(show)
        photo = Path(self._tmp.name) / "room.png"
        image = QImage(800, 450, QImage.Format.Format_RGB32)
        image.fill(QColor("#336699"))
        image.save(str(photo))
        self.assertTrue(page.set_room_photo(photo))
        backdrop = window.document.show.layout().backdrop
        self.assertRegex(backdrop, r"^media/room-[0-9a-f]{12}\.jpg$")  # its own file, named from its contents
        self.assertTrue((folder / backdrop).is_file())
        self.assertFalse(page.backdrop.pixmap().isNull())
        page._clear_photo()
        self.assertEqual(window.document.show.layout().backdrop, "")
        self.assertTrue(page.backdrop.pixmap().isNull())
        window.undo()  # the file was kept, so the photo comes back
        self.assertEqual(window.document.show.layout().backdrop, backdrop)
        self.assertFalse(page.backdrop.pixmap().isNull())

    def test_locked_page_is_disabled(self) -> None:
        window, _engine, page = self.open_layout()
        window.set_locked(True)
        self.assertFalse(page.isEnabled())
        window.set_locked(False)
        self.assertTrue(page.isEnabled())

    def test_read_only_show_cannot_be_edited(self) -> None:
        window, _engine, page = self.open_layout(replace(with_inputs(show_from_patch()), read_only=True))
        self.assertFalse(page.add_button.isEnabled())
        self.assertTrue(page.read_only_hint.isVisible())
        self.assertIn("read-only", window.windowTitle())


if __name__ == "__main__":
    unittest.main()


class SavedLayoutAndHistoryTests(M1WindowTestCase):
    def open_layout(self):
        window, engine = self.make()
        QTest.mouseClick(window.header.tabs["layout"], Qt.MouseButton.LeftButton)
        process(self.app, 3)
        return window, engine, window.layout_page

    def set_across(self, page, fixture_id: str, percent: float) -> None:
        page.scene.clearSelection()
        page._items[fixture_id].setSelected(True)
        process(self.app)
        page.x_spin.setValue(percent)

    def menu_actions(self, page) -> dict:
        page.fill_layout_menu()
        return {action.text(): action for action in page.layout_menu.actions() if action.text()}

    def test_save_as_switch_rename_and_delete(self) -> None:
        window, engine, page = self.open_layout()
        self.assertIn("Layout: Main", page.layout_button.text())
        with mock.patch.object(QInputDialog, "getText", return_value=("Christmas", True)):
            page._ask_save_layout_as()
        self.assertEqual(window.document.show.layout().name, "Christmas")
        self.assertIn("Layout: Christmas", page.layout_button.text())
        self.set_across(page, "slimpar-101", 80.0)
        self.assertAlmostEqual(engine.show.fixture("slimpar-101").map.x, 0.8)
        actions = self.menu_actions(page)
        self.assertTrue(actions["Christmas"].isChecked())
        actions["Main"].trigger()  # switching moves the lights at once, for the engine too
        self.assertAlmostEqual(engine.show.fixture("slimpar-101").map.x, 0.2)
        self.assertAlmostEqual(page._items["slimpar-101"].pos().x(), 0.2 * 640, delta=0.01)
        with mock.patch.object(QInputDialog, "getText", return_value=("Main hall", True)):
            page._ask_rename_layout()
        self.assertIn("Layout: Main hall", page.layout_button.text())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page._ask_delete_layout()
        self.assertEqual(window.document.show.layout().name, "Christmas")
        self.assertAlmostEqual(engine.show.fixture("slimpar-101").map.x, 0.8)
        self.assertFalse(self.menu_actions(page)["Delete this layout…"].isEnabled())  # the last one can't go
        window.undo()
        self.assertEqual(window.document.show.layout().name, "Main hall")
        self.assertAlmostEqual(engine.show.fixture("slimpar-101").map.x, 0.2)

    def test_long_layout_names_are_shortened_with_a_tooltip(self) -> None:
        window, _engine, page = self.open_layout()
        name = "Christmas Eve candlelit carol service with the big tree by the stage"
        page.save_layout_as(name)
        self.assertTrue(page.layout_button.text().endswith("…  ▾"))
        self.assertIn(name, page.layout_button.toolTip())

    def test_undo_buttons_name_the_step_and_history_jumps(self) -> None:
        window, _engine, page = self.open_layout()
        bar = page.undo_bar
        self.assertFalse(bar.undo_button.isEnabled())
        self.assertEqual(bar.undo_button.toolTip(), "Nothing to undo")
        for fixture_id, percent in (("slimpar-101", 30.0), ("slimpar-102", 50.0), ("slimpar-103", 70.0)):
            self.set_across(page, fixture_id, percent)
        self.assertEqual(bar.undo_button.toolTip(), "Undo: Move SlimPAR 103  (Ctrl+Z)")
        popup = bar.show_history()
        process(self.app, 2)
        model = popup.view.model()
        texts = [model.index(row, 0).data() for row in range(model.rowCount())]
        self.assertEqual(texts, ["Start (before these changes)", "Move SlimPAR 101", "Move SlimPAR 102", "Move SlimPAR 103"])
        rect = popup.view.visualRect(model.index(1, 0))
        QTest.mouseClick(popup.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
        show = window.document.show
        self.assertAlmostEqual(show.fixture("slimpar-101").map.x, 0.3)
        self.assertAlmostEqual(show.fixture("slimpar-102").map.x, 0.4)  # back where it started
        self.assertEqual(bar.redo_button.toolTip(), "Redo: Move SlimPAR 102  (Ctrl+Y)")
        QTest.mouseClick(bar.redo_button, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(window.document.show.fixture("slimpar-102").map.x, 0.5)
        popup.close()

    def test_each_tab_has_its_own_history(self) -> None:
        window, _engine, page = self.open_layout()
        self.set_across(page, "slimpar-101", 40.0)
        window.open_editor("warm-white")
        name = window.editor_page.details._fields["input:name"]
        name.setText("Warm wash")
        name.editingFinished.emit()
        bar = window.editor_page.undo_bar
        self.assertEqual(bar.undo_button.toolTip(), "Undo: Rename Warm white → Warm wash  (Ctrl+Z)")
        window.undo()  # only the rename
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")
        self.assertAlmostEqual(window.document.show.fixture("slimpar-101").map.x, 0.4)
        window.open_editor("worship-blue")
        self.assertFalse(bar.undo_button.isEnabled())  # Worship blue has no changes of its own
        window.show_page("layout")
        self.assertEqual(page.undo_bar.undo_button.toolTip(), "Undo: Move SlimPAR 101  (Ctrl+Z)")
        window.show_page("dashboard")
        window.undo()  # the dashboard's history (inputs added or deleted) is empty: nothing happens
        self.assertAlmostEqual(window.document.show.fixture("slimpar-101").map.x, 0.4)
