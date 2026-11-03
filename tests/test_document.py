"""The show document: undo/redo with separate histories, merging, drags, read-only, and saving
in the background (including saved layouts)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.commands import SwapShow
from centralight.input_edit import add_element, delete_input, duplicate_input, new_input, set_element_value, update_input
from centralight.layouts import delete_layout, save_layout_as, switch_layout
from centralight.model import MapSpot
from centralight.new_show import create_show
from centralight.show_edit import move_lights, rename_light
from centralight.show_io import load_show
from centralight.ui.document import SCOPE_LAYOUT, SCOPE_SHOW, ShowDocument, input_scope, merged_text
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class RecordingEngine:
    def __init__(self) -> None:
        self.commands = []

    def submit(self, command) -> None:
        self.commands.append(command)


class DocumentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Show"
        self.show = create_show(self.folder)
        self.engine = RecordingEngine()
        self.doc = ShowDocument(self.show, self.engine, save_delay_ms=10)
        self.doc.set_scope(SCOPE_LAYOUT)  # as if the Layout tab were open

    def tearDown(self) -> None:
        self.doc.close()
        self._tmp.cleanup()

    def saved_show(self) -> dict:
        self.doc.flush()
        self.assertTrue(self.doc.wait_saved())
        return json.loads((self.folder / "show.json").read_text(encoding="utf-8"))


class DocumentTests(DocumentTestCase):
    def test_apply_undo_redo(self) -> None:
        renamed = rename_light(self.show, "slimpar-101", "Drum light")
        self.assertTrue(self.doc.apply(renamed, "Rename SlimPAR 101 → Drum light"))
        self.assertEqual(self.doc.show.fixture("slimpar-101").name, "Drum light")
        self.assertIsInstance(self.engine.commands[-1], SwapShow)
        self.assertEqual(self.engine.commands[-1].show, renamed)
        self.doc.undo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").name, "SlimPAR 101")
        self.doc.redo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").name, "Drum light")
        self.assertFalse(self.doc.apply(self.doc.show, "Nothing"))

    def test_merged_edits_are_one_undo_step(self) -> None:
        for old, x in ((20, 0.1), (10, 0.2), (20, 0.3)):
            self.doc.apply(move_lights(self.doc.show, {"slimpar-101": MapSpot(x, 0.2, 0.08)}), f"Across {old} % → {round(x * 100)} %", merge_key="spin")
        self.assertEqual(self.doc.undo_stack.count(), 1)
        self.assertEqual(self.doc.undo_stack.text(0), "Across 20 % → 30 %")  # first "from", last "to"
        self.doc.undo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.2)

    def test_merged_text(self) -> None:
        self.assertEqual(merged_text("Opacity 80 % → 70 %", "Opacity 70 % → 60 %"), "Opacity 80 % → 60 %")
        self.assertEqual(merged_text("Move SlimPAR 101", "Move SlimPAR 101"), "Move SlimPAR 101")

    def test_a_drag_is_one_undo_step(self) -> None:
        self.doc.begin_drag()
        for x in (0.3, 0.4, 0.5):
            self.doc.preview(move_lights(self.doc.show, {"slimpar-101": MapSpot(x, 0.5, 0.08)}))
        self.assertEqual(self.doc.undo_stack.count(), 0)
        self.doc.end_drag("Move SlimPAR 101")
        self.assertEqual(self.doc.undo_stack.count(), 1)
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.5)
        self.doc.undo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.2)

    def test_saves_in_the_background(self) -> None:
        self.doc.apply(rename_light(self.show, "slimpar-103", "Centre wash"), "Rename light")
        data = self.saved_show()
        names = [fx["name"] for fx in data["fixtures"]]
        self.assertIn("Centre wash", names)

    def test_saves_after_the_delay_on_its_own(self) -> None:
        self.doc.apply(rename_light(self.show, "slimpar-104", "Right wash"), "Rename light")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            process(self.app)
            if "Right wash" in (self.folder / "show.json").read_text(encoding="utf-8"):
                break
            time.sleep(0.02)
        self.assertIn("Right wash", (self.folder / "show.json").read_text(encoding="utf-8"))

    def test_deleted_inputs_lose_their_file(self) -> None:
        self.assertTrue((self.folder / "inputs" / "follow-screen.json").exists())
        self.doc.apply(self.show.with_inputs({}, ()), "Delete input")
        self.saved_show()
        self.assertFalse((self.folder / "inputs" / "follow-screen.json").exists())

    def test_read_only_shows_refuse_changes(self) -> None:
        locked = replace(self.show, read_only=True)
        doc = ShowDocument(locked, self.engine)
        self.assertFalse(doc.apply(rename_light(locked, "slimpar-101", "X"), "Rename"))
        doc.preview(rename_light(locked, "slimpar-101", "X"))
        self.assertEqual(doc.show.fixture("slimpar-101").name, "SlimPAR 101")
        doc.close()


class SeparateHistoryTests(DocumentTestCase):
    """Each input, the layout and the show have their own history."""

    def setUp(self) -> None:
        super().setUp()
        self.doc.close()
        self.show = with_inputs(self.show)
        self.doc = ShowDocument(self.show, self.engine, save_files=False)

    def edit_input(self, input_id: str, name: str) -> None:
        self.doc.set_scope(input_scope(input_id))
        self.doc.apply(update_input(self.doc.show, input_id, name=name), f"Rename input → {name}")

    def test_each_input_has_its_own_history(self) -> None:
        self.edit_input("warm-white", "Warm wash")
        self.edit_input("worship-blue", "Deep blue")
        self.doc.set_scope(SCOPE_LAYOUT)
        self.doc.apply(rename_light(self.doc.show, "slimpar-101", "Drum light"), "Rename SlimPAR 101 → Drum light")
        self.assertEqual(self.doc.stack(input_scope("warm-white")).count(), 1)
        self.assertEqual(self.doc.stack(input_scope("worship-blue")).count(), 1)
        self.assertEqual(self.doc.stack(SCOPE_LAYOUT).count(), 1)
        # Undo in Warm white's history leaves the newer changes elsewhere alone
        self.doc.set_scope(input_scope("warm-white"))
        self.doc.undo()
        show = self.doc.show
        self.assertEqual(show.inputs["warm-white"].name, "Warm white")
        self.assertEqual(show.inputs["worship-blue"].name, "Deep blue")
        self.assertEqual(show.fixture("slimpar-101").name, "Drum light")
        # and nothing is left to undo there, though the other histories still have theirs
        self.assertFalse(self.doc.undo_stack.canUndo())
        self.doc.undo()
        self.assertEqual(self.doc.show.inputs["worship-blue"].name, "Deep blue")

    def test_switching_inputs_switches_the_history(self) -> None:
        self.edit_input("warm-white", "Warm wash")
        self.doc.set_scope(input_scope("christmas"))
        self.assertFalse(self.doc.undo_group.canUndo())
        self.doc.set_scope(input_scope("warm-white"))
        self.assertTrue(self.doc.undo_group.canUndo())
        self.assertEqual(self.doc.undo_group.undoText(), "Rename input → Warm wash")

    def test_layout_undo_never_touches_inputs(self) -> None:
        self.doc.set_scope(SCOPE_LAYOUT)
        self.doc.apply(move_lights(self.doc.show, {"slimpar-101": MapSpot(0.9, 0.9, 0.1)}), "Move SlimPAR 101")
        self.edit_input("warm-white", "Warm wash")
        self.doc.set_scope(SCOPE_LAYOUT)
        self.doc.undo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").map, MapSpot(0.2, 0.2, 0.08))
        self.assertEqual(self.doc.show.inputs["warm-white"].name, "Warm wash")

    def test_new_and_deleted_inputs_keep_their_own_history(self) -> None:
        self.doc.set_scope(SCOPE_SHOW)
        show, new_id = new_input(self.doc.show)
        self.doc.apply(show, "New input")
        self.doc.set_scope(input_scope(new_id))
        show, element_id = add_element(self.doc.show, new_id, 0, "paint")
        self.doc.apply(show, "Add paint element")
        self.doc.apply(set_element_value(self.doc.show, new_id, element_id, "opacity", 0.5), "Opacity 100 % → 50 %")
        # Undo "New input" from the show's history (the dashboard), then redo it: the input
        # comes back as it was, and its own history still lines up with it
        self.doc.set_scope(SCOPE_SHOW)
        self.doc.undo()
        self.assertNotIn(new_id, self.doc.show.inputs)
        self.doc.redo()
        element = self.doc.show.inputs[new_id].tracks[0][0]
        self.assertEqual(element["opacity"], 0.5)
        self.doc.set_scope(input_scope(new_id))
        self.doc.undo()
        self.assertNotIn("opacity", self.doc.show.inputs[new_id].tracks[0][0])
        # Deleting another input and undoing that brings it back exactly
        before = self.doc.show.inputs["christmas"]
        self.doc.set_scope(SCOPE_SHOW)
        self.doc.apply(delete_input(self.doc.show, "christmas"), "Delete Christmas")
        self.doc.undo()
        self.assertEqual(self.doc.show.inputs["christmas"], before)
        self.assertIn("christmas", self.doc.show.input_order)

    def test_a_reused_input_id_starts_a_fresh_history(self) -> None:
        self.doc.set_scope(SCOPE_SHOW)
        show, first = new_input(self.doc.show)
        self.doc.apply(show, "New input")
        self.edit_input(first, "Gone soon")
        self.doc.set_scope(SCOPE_SHOW)
        self.doc.apply(delete_input(self.doc.show, first), "Delete Gone soon")
        show, second = new_input(self.doc.show)
        self.assertEqual(second, first)
        self.doc.apply(show, "New input")
        self.assertEqual(self.doc.stack(input_scope(second)).count(), 0)

    def test_duplicate_goes_in_the_show_history(self) -> None:
        self.doc.set_scope(SCOPE_SHOW)
        show, copy_id = duplicate_input(self.doc.show, "worship-blue")
        self.doc.apply(show, "Duplicate Worship blue")
        self.assertEqual(self.doc.stack(SCOPE_SHOW).count(), 1)
        self.assertEqual(self.doc.stack(input_scope("worship-blue")).count(), 0)
        self.doc.undo()
        self.assertNotIn(copy_id, self.doc.show.inputs)

    def test_slider_drags_merge(self) -> None:
        self.doc.set_scope(input_scope("warm-white"))
        for old, new in ((100, 90), (90, 70), (70, 60)):
            self.doc.apply(set_element_value(self.doc.show, "warm-white", "E1", "opacity", new / 100),
                           f"Paint E1: opacity {old} % → {new} %", merge_key="warm-white/E1/opacity")
        self.assertEqual(self.doc.undo_stack.count(), 1)
        self.assertEqual(self.doc.undo_group.undoText(), "Paint E1: opacity 100 % → 60 %")
        self.doc.undo()
        self.assertNotIn("opacity", self.doc.show.inputs["warm-white"].tracks[0][0])

    def test_history_jumps_and_redo_after_a_jump(self) -> None:
        self.doc.set_scope(SCOPE_LAYOUT)
        for x in (0.3, 0.4, 0.5):
            self.doc.apply(move_lights(self.doc.show, {"slimpar-101": MapSpot(x, 0.2, 0.08)}), f"Move SlimPAR 101 to {round(x * 100)} %")
        stack = self.doc.undo_stack
        self.assertEqual([stack.text(i) for i in range(stack.count())], ["Move SlimPAR 101 to 30 %", "Move SlimPAR 101 to 40 %", "Move SlimPAR 101 to 50 %"])
        stack.setIndex(1)  # what clicking the first step in the History panel does
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.3)
        stack.setIndex(0)  # "Start"
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.2)
        self.doc.redo()
        self.doc.redo()
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.4)
        stack.setIndex(3)
        self.assertEqual(self.doc.show.fixture("slimpar-101").map.x, 0.5)


class SavedLayoutDocumentTests(DocumentTestCase):
    def test_switching_is_undoable_and_saved(self) -> None:
        show, christmas = save_layout_as(self.doc.show, "Christmas")
        self.doc.apply(show, "Save layout as Christmas")
        self.doc.apply(move_lights(self.doc.show, {"slimpar-101": MapSpot(0.9, 0.9, 0.12)}), "Move SlimPAR 101")
        self.doc.apply(switch_layout(self.doc.show, "main"), "Switch to layout Main")
        self.assertEqual(self.doc.show.fixture("slimpar-101").map, MapSpot(0.2, 0.2, 0.08))
        self.doc.undo()
        self.assertEqual(self.doc.show.active_layout, christmas)
        self.assertEqual(self.doc.show.fixture("slimpar-101").map, MapSpot(0.9, 0.9, 0.12))
        self.saved_show()
        files = sorted(p.name for p in (self.folder / "layouts").glob("*.json"))
        self.assertEqual(files, ["christmas.json", "main.json"])
        again = load_show(self.folder)  # a restart
        self.assertEqual(again.active_layout, christmas)
        self.assertEqual(again.fixture("slimpar-101").map, MapSpot(0.9, 0.9, 0.12))
        self.assertEqual(again.layout("main").positions["slimpar-101"], MapSpot(0.2, 0.2, 0.08))

    def test_deleted_layouts_lose_their_file(self) -> None:
        show, christmas = save_layout_as(self.doc.show, "Christmas")
        self.doc.apply(show, "Save layout as Christmas")
        self.saved_show()
        self.assertTrue((self.folder / "layouts" / "christmas.json").is_file())
        self.doc.apply(delete_layout(self.doc.show, christmas), "Delete layout Christmas")
        self.saved_show()
        self.assertFalse((self.folder / "layouts" / "christmas.json").exists())
        self.assertEqual(load_show(self.folder).active_layout, "main")


if __name__ == "__main__":
    unittest.main()
