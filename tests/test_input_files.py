"""Inputs and automations changed outside CentraLight (e.g. by Claude Code following AI_GUIDE.md):
new, changed, broken and deleted files, CentraLight's own saves, undo, and a clash with an edit."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from centralight.input_edit import update_input
from centralight.show_io import automation_to_dict, input_to_dict
from centralight.ui.document import SCOPE_AUTOMATIONS, SCOPE_SHOW, ShowDocument, input_scope
from centralight.ui.file_watcher import FolderWatcher
from tests.gui_fixtures import sample_automations
from tests.qt_support import process, qt_app
from tests.starter import create_show  # (their shows keep Follow screen, 6 Nov)


class RecordingEngine:
    def __init__(self) -> None:
        self.commands = []

    def submit(self, command) -> None:
        self.commands.append(command)


class FolderWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Show"
        show = create_show(self.folder)
        self.doc = ShowDocument(show, RecordingEngine(), save_delay_ms=10)
        self.asked: list[str] = []
        self.answer = True
        self.editing = ""
        self.watcher = FolderWatcher(self.doc, ask_reload=self._ask, editing=lambda: self.editing)
        self.problems: dict = {}
        self.watcher.problems_changed.connect(lambda p: self.problems.update({"now": p}))

    def tearDown(self) -> None:
        self.watcher.stop()
        self.doc.close()
        self._tmp.cleanup()

    def _ask(self, name: str) -> bool:
        self.asked.append(name)
        return self.answer

    def write(self, data: dict, name: str | None = None, sub: str = "inputs") -> Path:
        path = self.folder / sub / f"{name or data['id']}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        stamp = time.time() + 2 + len(self.asked)  # a different timestamp each time (the watcher looks at these)
        import os

        os.utime(path, (stamp, stamp))
        return path

    def paint(self, input_id: str, colour: str, name: str = "From outside") -> dict:
        return {"format": "centralight-input", "version": 1, "id": input_id, "name": name,
                "tracks": [[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": colour}}], [], [], []]}

    def test_a_new_file_appears_and_can_be_undone(self) -> None:
        self.write(self.paint("pillars-pulse", "#7A3CFF", "Pillars pulse"))
        self.watcher.check()
        show = self.doc.show
        self.assertIn("pillars-pulse", show.inputs)
        self.assertEqual(show.input_order[-1], "pillars-pulse")  # at the end of the grid
        self.assertEqual(self.doc.stack(SCOPE_SHOW).undoText(), "Added outside CentraLight: Pillars pulse")

    def test_a_changed_file_updates_and_ctrl_z_puts_it_back(self) -> None:
        before = self.doc.show.inputs["follow-screen"]
        changed = input_to_dict(before)
        changed["name"] = "Follow screen (warmer)"
        self.write(changed)
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Follow screen (warmer)")
        stack = self.doc.stack(input_scope("follow-screen"))
        self.assertEqual(stack.undoText(), "Changed outside CentraLight: Follow screen (warmer)")
        self.doc.set_scope(input_scope("follow-screen"))
        self.doc.undo()
        self.assertEqual(self.doc.show.inputs["follow-screen"], before)
        self.doc.flush()
        self.assertTrue(self.doc.wait_saved())
        on_disk = json.loads((self.folder / "inputs" / "follow-screen.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["name"], "Follow screen")  # the old version is written back over the file
        self.watcher.check()  # and that save of CentraLight's own isn't taken as another outside change
        self.assertEqual(stack.count(), 1)

    def test_a_broken_file_keeps_the_last_good_version(self) -> None:
        path = self.folder / "inputs" / "follow-screen.json"
        path.write_text("{ this isn't JSON", encoding="utf-8")
        import os

        os.utime(path, (time.time() + 5, time.time() + 5))
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Follow screen")  # still the good one
        self.assertIn("follow-screen", self.watcher.problems)
        self.assertIn("last good version", self.watcher.problems["follow-screen"])
        self.write(self.paint("follow-screen", "#FF0000", "Fixed"))
        self.watcher.check()
        self.assertNotIn("follow-screen", self.watcher.problems)  # fixed: the problem goes
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Fixed")

    def test_a_deleted_file_removes_the_input(self) -> None:
        (self.folder / "inputs" / "follow-screen.json").unlink()
        self.watcher.check()
        self.assertNotIn("follow-screen", self.doc.show.inputs)
        self.assertEqual(self.doc.stack(SCOPE_SHOW).undoText(), "Deleted outside CentraLight: Follow screen")

    def test_centralights_own_saves_are_ignored(self) -> None:
        self.doc.set_scope(input_scope("follow-screen"))
        self.doc.apply(update_input(self.doc.show, "follow-screen", name="Renamed here"), "Rename")
        self.doc.flush()
        self.assertTrue(self.doc.wait_saved())
        self.watcher.check()
        self.assertEqual(self.doc.stack(input_scope("follow-screen")).count(), 1)  # just the rename
        self.assertEqual(self.doc.stack(SCOPE_SHOW).count(), 0)

    def test_an_older_save_landing_late_is_still_ours(self) -> None:
        """Two saves close together: the first file write is noticed after the second save has started."""
        self.doc.set_scope(input_scope("follow-screen"))
        self.doc.apply(update_input(self.doc.show, "follow-screen", name="First"), "Rename")
        self.doc.flush()
        first = input_to_dict(self.doc.show.inputs["follow-screen"])
        self.doc.apply(update_input(self.doc.show, "follow-screen", name="Second"), "Rename")
        self.doc.flush()
        self.assertTrue(self.doc.wait_saved())
        self.write(first)  # what the watcher would see if it looked between the two writes
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Second")  # not taken as an outside change
        self.assertEqual(self.doc.stack(input_scope("follow-screen")).count(), 2)
        theirs = dict(first, name="From outside")
        self.write(theirs)  # a version CentraLight never wrote is still an outside change
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "From outside")

    def test_a_clash_with_an_unsaved_edit_asks(self) -> None:
        self.doc.set_scope(input_scope("follow-screen"))
        self.doc.apply(update_input(self.doc.show, "follow-screen", name="Mine"), "Rename")  # not saved yet
        self.editing = "follow-screen"
        self.answer = False  # keep mine
        self.write(self.paint("follow-screen", "#00FF00", "Theirs"))
        self.watcher.check()
        self.assertEqual(self.asked, ["Mine"])
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Mine")
        self.answer = True  # load the file
        self.write(self.paint("follow-screen", "#0000FF", "Theirs again"))
        self.watcher.check()
        self.assertEqual(self.doc.show.inputs["follow-screen"].name, "Theirs again")

    def test_automation_files_too(self) -> None:
        automation = sample_automations()[0]
        self.write(automation_to_dict(automation), sub="automations")
        self.watcher.check()
        self.assertEqual(self.doc.show.automations[automation.id], automation)
        self.assertEqual(self.doc.stack(SCOPE_AUTOMATIONS).undoText(), f"Added outside CentraLight: {automation.name}")
        (self.folder / "automations" / f"{automation.id}.json").unlink()
        self.watcher.check()
        self.assertNotIn(automation.id, self.doc.show.automations)

    def test_notices_by_itself(self) -> None:
        self.watcher._poll.setInterval(50)
        self.watcher._poll.start()
        self.write(self.paint("by-itself", "#FFFFFF", "By itself"))
        deadline = time.monotonic() + 5
        while "by-itself" not in self.doc.show.inputs and time.monotonic() < deadline:
            process(self.app)
            time.sleep(0.02)
        self.assertIn("by-itself", self.doc.show.inputs)


if __name__ == "__main__":
    unittest.main()
