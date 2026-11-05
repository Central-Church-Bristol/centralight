"""M14 item 4: one Overlay instead of Overlays 1-4, driven like a person would: Send to Base and Send to Overlay on each
tile (a click on the tile itself does nothing), each send a new copy on top (up to 4 of one input), hold Send to
Overlay for a flash, a red box per copy that clears it, the tile's menu, the padlock, dragging a tile to move it, and
older automations brought up to date."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest

from centralight.automations import action_command, check_action, describe_action
from centralight.commands import ClearFromOverlay, ClearOverlay, SendToOverlay
from centralight.input_edit import move_input
from centralight.new_show import show_from_patch
from tests.gui_fixtures import with_inputs
from tests.qt_support import process
from tests.test_gui_editor import EditorTestCase


class TileTests(EditorTestCase):
    def frame(self, window, engine, seconds: float = 0.2):
        engine.settle(seconds)
        window.bridge.poll()
        process(self.app)
        return engine.frames.get()

    def test_send_to_base_and_a_click_on_the_tile_does_nothing(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["warm-white"]
        before = self.frame(window, engine).base_id
        QTest.mouseClick(tile, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, tile.map.geometry().center())
        self.assertEqual(self.frame(window, engine).base_id, before)  # (clicking the tile itself sends nothing now)
        QTest.mouseClick(tile.base_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.frame(window, engine).base_id, "warm-white")
        self.assertEqual(self.frame(window, engine).overlay.copies, ())

    def test_each_click_is_a_copy_up_to_four_and_a_box_clears_one(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["pillars-purple"]
        for _ in range(4):
            QTest.mouseClick(tile.overlay_button, Qt.MouseButton.LeftButton)
        frame = self.frame(window, engine)
        self.assertEqual([c.input_id for c in frame.overlay.copies], ["pillars-purple"] * 4)
        self.assertEqual(len(tile.boxes.copies()), 4)
        self.assertFalse(tile.overlay_button.isEnabled())  # (4: it waits until one goes)
        QTest.mouseClick(tile.overlay_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 4)
        second = tile.boxes.copies()[1][0]
        box = tile.boxes.box_rects()[1]
        QTest.mouseClick(tile.boxes, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, box.center().toPoint())
        frame = self.frame(window, engine)
        self.assertEqual(len(frame.overlay.copies), 3)
        self.assertNotIn(second, [c.number for c in frame.overlay.copies])  # (that copy, and only that one)
        self.assertTrue(tile.overlay_button.isEnabled())
        self.assertEqual(len(tile.boxes.copies()), 3)  # (the boxes to its right moved left)

    def test_hold_send_to_overlay_for_a_flash(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["pillars-purple"]
        button = tile.overlay_button
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)  # (on at once: no waiting to see)
        time.sleep(0.55)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.frame(window, engine).overlay.copies, ())  # held: it clears when it's let go
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)  # a quick click: it stays
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)

    def test_a_held_fourth_copy_still_clears_when_let_go(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["pillars-purple"]
        for _ in range(3):
            QTest.mouseClick(tile.overlay_button, Qt.MouseButton.LeftButton)
        QTest.mousePress(tile.overlay_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 4)
        self.assertTrue(tile.overlay_button.isEnabled())  # (held: it waits to grey out, so letting go arrives)
        time.sleep(0.55)
        QTest.mouseRelease(tile.overlay_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 3)
        self.assertTrue(tile.overlay_button.isEnabled())

    def test_the_tile_menu_sends_and_clears(self) -> None:
        window, engine = self.make()
        menu = window.dashboard.tile_menu("pillars-purple")
        actions = {a.text(): a for a in menu.actions()}
        self.assertFalse(actions["Clear from Overlay"].isEnabled())  # (none there yet)
        actions["Send to Overlay"].trigger()
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)
        menu = window.dashboard.tile_menu("pillars-purple")
        actions = {a.text(): a for a in menu.actions()}
        self.assertTrue(actions["Clear from Overlay"].isEnabled())
        actions["Clear from Overlay"].trigger()
        self.assertEqual(self.frame(window, engine, 1.2).overlay.copies, ())

    def test_the_padlock_stops_the_buttons_and_boxes(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["pillars-purple"]
        QTest.mouseClick(tile.overlay_button, Qt.MouseButton.LeftButton)
        self.frame(window, engine)
        window.set_locked(True)
        process(self.app)
        self.assertFalse(tile.base_button.isEnabled())
        self.assertFalse(tile.overlay_button.isEnabled())
        tile.boxes.clear_requested.emit(tile.boxes.copies()[0][0])  # (even if a click got through)
        window.dashboard.grid.tiles["warm-white"].base_requested.emit("warm-white")
        frame = self.frame(window, engine)
        self.assertEqual(len(frame.overlay.copies), 1)
        self.assertNotEqual(frame.base_id, "warm-white")


class DragTests(EditorTestCase):
    def test_move_input_counts_the_tiles_as_they_are(self) -> None:
        show = with_inputs(show_from_patch())
        ids = [i.id for i in show.ordered_inputs()]

        def order(moved):
            return [i.id for i in moved.ordered_inputs()]

        self.assertEqual(order(move_input(show, ids[0], 3)), ids[1:3] + ids[:1] + ids[3:])  # before the tile at 3
        self.assertEqual(order(move_input(show, ids[4], 1)), ids[:1] + [ids[4]] + ids[1:4] + ids[5:])
        self.assertEqual(order(move_input(show, ids[0], 99)), ids[1:] + ids[:1])  # (past the end: last)
        self.assertEqual(order(move_input(show, ids[2], 2)), ids)  # (where it is: no change)
        self.assertIs(move_input(show, "nothing", 0), show)

    def test_dropping_a_tile_moves_it_and_undo_puts_it_back(self) -> None:
        window, _engine = self.make()
        grid = window.dashboard.grid
        before = [i.id for i in window.document.show.ordered_inputs()]
        first = grid.tiles[before[0]].geometry()
        mime = QMimeData()
        mime.setData("application/x-centralight-input", b"christmas")
        at = QPointF(first.left() + 4, first.center().y())
        self.assertEqual(grid.drop_index(at.toPoint()), 0)
        grid.dropEvent(QDropEvent(at, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        after = [i.id for i in window.document.show.ordered_inputs()]
        self.assertEqual(after, ["christmas"] + [i for i in before if i != "christmas"])
        self.assertEqual(window.document.undo_stack.undoText(), "Move Candlelight")
        window.undo()
        self.assertEqual([i.id for i in window.document.show.ordered_inputs()], before)

    def test_a_drag_starts_only_when_the_mouse_moves(self) -> None:
        window, _engine = self.make()
        tile = window.dashboard.grid.tiles["warm-white"]
        with mock.patch.object(window.dashboard.grid, "_drag") as drag:
            tile.drag_requested.disconnect()
            tile.drag_requested.connect(drag)
            centre = tile.map.geometry().center()
            QTest.mousePress(tile, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
            QTest.mouseRelease(tile, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
            drag.assert_not_called()  # (a click: nothing)
            QTest.mousePress(tile, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
            QTest.mouseMove(tile, centre + type(centre)(30, 0))
            drag.assert_called_once_with("warm-white")


class AutomationTests(unittest.TestCase):
    def test_the_actions_and_what_they_say(self) -> None:
        show = with_inputs(show_from_patch())
        self.assertEqual(check_action({"do": "overlay", "input": "pillars-purple"}), [])
        self.assertEqual(action_command({"do": "overlay", "input": "pillars-purple"}, show), SendToOverlay("pillars-purple"))
        self.assertEqual(action_command({"do": "overlay_clear", "input": "pillars-purple"}, show),
                         ClearFromOverlay(input_id="pillars-purple"))
        self.assertEqual(action_command({"do": "overlay_clear_all"}, show), ClearOverlay())
        self.assertIsNone(action_command({"do": "overlay", "input": "nothing"}, show))
        self.assertEqual(describe_action({"do": "overlay", "input": "pillars-purple"}, show), "Pillars purple to the Overlay")
        self.assertEqual(describe_action({"do": "overlay_clear", "input": "pillars-purple"}, show),
                         "clear Pillars purple from the Overlay")
        self.assertEqual(describe_action({"do": "overlay_clear_all"}, show), "clear the Overlay")


if __name__ == "__main__":
    unittest.main()
