"""Show edits and the fixture_map.csv notes (in the format the old patch-test script wrote, so its file still reads)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from centralight.model import MapSpot
from centralight.new_show import show_from_patch
from centralight.notes import load_notes, save_notes
from centralight.show_edit import (
    add_colour_light,
    channel_conflicts,
    move_lights,
    next_free_address,
    remove_lights,
    rename_light,
    set_address,
    set_profile,
)


class ShowEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()

    def test_move_clamps_to_the_map(self) -> None:
        moved = move_lights(self.show, {"slimpar-101": MapSpot(1.4, -0.2), "dimmer-1": MapSpot(0.5, 0.5)})
        self.assertEqual(moved.fixture("slimpar-101").map, MapSpot(1.0, 0.0))
        self.assertIsNone(moved.fixture("dimmer-1").map)  # house lights aren't on the map

    def test_rename_house_light_renames_its_label(self) -> None:
        renamed = rename_light(self.show, "dimmer-4", "Pulpit lights")
        self.assertEqual(renamed.house_label("dimmer-4"), "Pulpit lights")
        self.assertIs(rename_light(self.show, "dimmer-4", "   "), self.show)

    def test_addresses_stay_in_the_universe(self) -> None:
        self.assertEqual(set_address(self.show, "slimpar-101", 600).fixture("slimpar-101").address, 504)
        self.assertEqual(set_address(self.show, "slimpar-101", 0).fixture("slimpar-101").address, 1)
        changed = set_profile(set_address(self.show, "dimmer-1", 512), "dimmer-1", "slimpar9")
        self.assertEqual(changed.fixture("dimmer-1").address, 504)

    def test_new_light_goes_after_the_last_channel(self) -> None:
        self.assertEqual(next_free_address(self.show, 9), 77)
        added, new_id = add_colour_light(self.show)
        fx = added.fixture(new_id)
        self.assertEqual((fx.name, fx.address, fx.profile), ("SlimPAR 109", 77, "slimpar9"))
        self.assertEqual(channel_conflicts(added), {})
        self.assertEqual(len(remove_lights(added, {new_id}).fixtures), len(self.show.fixtures))

    def test_conflicts_are_found_both_ways(self) -> None:
        clash = set_address(self.show, "slimpar-102", 10)
        conflicts = channel_conflicts(clash)
        self.assertIn("SlimPAR 101 (ch 5-13)", conflicts["slimpar-102"])
        self.assertIn("SlimPAR 102 (ch 10-18)", conflicts["slimpar-101"])
        self.assertEqual(channel_conflicts(self.show), {})

    def test_removing_a_house_light_removes_its_control(self) -> None:
        fewer = remove_lights(self.show, {"dimmer-1"})
        self.assertEqual([h.fixture for h in fewer.house], ["dimmer-4"])


class NotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "fixture_map.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip_keeps_notes_for_other_names(self) -> None:
        show = show_from_patch()
        save_notes(show, {"SlimPAR 101": "front left, above the drums", "Old light": "gone"}, self.path)
        notes = load_notes(self.path)
        self.assertEqual(notes["SlimPAR 101"], "front left, above the drums")
        self.assertEqual(notes["Old light"], "gone")
        self.assertEqual(notes["SlimPAR 102"], "")
        self.assertTrue(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))  # Excel-friendly, like the patch test

    def test_same_file_as_the_old_patch_test(self) -> None:
        """A fixture_map.csv the old patch-test script wrote (its exact format) reads, and ours is written the same way."""
        old = ("\ufeffname,group,start_ch,channels,profile,where\r\n"
               "SlimPAR 101,Top Wash,10,9,slimpar9,\r\n"
               "SlimPAR 105,Side Fill,46,9,slimpar9,left wall\r\n")
        self.path.write_bytes(old.encode("utf-8"))
        self.assertEqual(load_notes(self.path)["SlimPAR 105"], "left wall")
        save_notes(show_from_patch(), {"SlimPAR 106": "pillar, left"}, self.path)
        text = self.path.read_bytes().decode("utf-8-sig")
        self.assertTrue(text.startswith("name,group,start_ch,channels,profile,where\r\n"))
        self.assertIn("SlimPAR 106,Pillars,", text)
        self.assertEqual(load_notes(self.path)["SlimPAR 106"], "pillar, left")

    def test_missing_or_broken_file_means_no_notes(self) -> None:
        self.assertEqual(load_notes(self.path), {})
        self.path.write_bytes(b"\xff\xfe\x00garbage")
        self.assertEqual(load_notes(self.path), {})


if __name__ == "__main__":
    unittest.main()
