"""M14 item 4: one Overlay instead of Overlays 1-4, driven like a person would: Send to Base and Send to Overlay on each
tile (a click on the tile itself does nothing), each send a new copy on top (up to 4 of one input), Send to Overlay as
the input says (M18: a copy that stays, or one only while it's held), a red box per copy that clears it, the tile's
menu, the padlock, dragging a tile to move it, and older automations brought up to date."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace
from unittest import mock

from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest

from centralight.automations import action_command, check_action, describe_action
from centralight.commands import ClearFromOverlay, ClearOverlay, SendToOverlay
from centralight.input_edit import move_input
from centralight.model import OVERLAY_HELD
from centralight.new_show import show_from_patch
from tests.gui_fixtures import with_inputs
from tests.qt_support import process
from tests.test_gui_editor import EditorTestCase


def held_show(*input_ids: str):
    """The test show, with these inputs' Send to Overlay set to "Only while held" (M18)."""
    show = with_inputs(show_from_patch())
    changed = {i: replace(show.inputs[i], overlay=OVERLAY_HELD) for i in input_ids}
    return show.with_inputs({**show.inputs, **changed}, show.input_order)


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

    def test_send_to_overlay_does_what_the_input_says(self) -> None:
        """M18: no more click-or-hold. A copy that stays (the default), however long it's held; for an input set to
        "Only while held", a copy that goes when it's let go, however quick the click."""
        window, engine = self.make(held_show("worship-blue"))
        button = window.dashboard.grid.tiles["pillars-purple"].overlay_button
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)  # (on at once: no waiting to see)
        time.sleep(0.55)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.frame(window, engine).overlay.copies), 1)  # held a while: it stays all the same
        held = window.dashboard.grid.tiles["worship-blue"].overlay_button
        QTest.mousePress(held, Qt.MouseButton.LeftButton)
        self.assertEqual([c.input_id for c in self.frame(window, engine).overlay.copies], ["pillars-purple", "worship-blue"])
        QTest.mouseRelease(held, Qt.MouseButton.LeftButton)  # (a quick click: it goes)
        self.assertEqual([c.input_id for c in self.frame(window, engine).overlay.copies], ["pillars-purple"])

    def test_a_held_fourth_copy_still_clears_when_let_go(self) -> None:
        window, engine = self.make(held_show("pillars-purple"))
        tile = window.dashboard.grid.tiles["pillars-purple"]
        for _ in range(3):
            engine.submit(SendToOverlay("pillars-purple"))  # (copies that stay: from an automation, say)
        self.frame(window, engine)
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
    def test_a_deleted_inputs_copies_leave(self) -> None:
        """(Review: they kept playing, and with its tile gone nothing but Clear all could clear them; a held flash whose
        input was deleted mid-hold stayed too.)"""
        from centralight.commands import SwapShow
        from centralight.input_edit import delete_input
        from tests.fakes import FakeEngine

        show = with_inputs(show_from_patch())
        engine = FakeEngine(show)
        engine.submit(SendToOverlay("pillars-purple", tag="held"))
        engine.settle(1.5)
        self.assertEqual(len(engine.frames.get().overlay.copies), 1)
        engine.submit(SwapShow(delete_input(show, "pillars-purple")))
        engine.settle(2.0)
        self.assertEqual(engine.frames.get().overlay.copies, ())

    def test_moving_a_tile_keeps_inputs_whose_files_didnt_load_in_their_place(self) -> None:
        show = with_inputs(show_from_patch())
        ids = [inp.id for inp in show.ordered_inputs()]
        broken = show.with_inputs(show.inputs, (ids[0], "broken", *ids[1:]))  # (inputs/broken.json couldn't be read)
        moved = move_input(broken, ids[-1], 0)
        self.assertEqual(list(moved.input_order), [ids[-1], "broken", ids[0], *ids[1:-1]])
        self.assertEqual([i.id for i in moved.ordered_inputs()], [ids[-1], *ids[:-1]])

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
