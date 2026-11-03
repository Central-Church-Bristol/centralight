"""Saved layouts: the plain functions, the layouts\\<id>.json files, and the engine following a switch."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from centralight.commands import SwapShow
from centralight.layouts import (
    delete_layout,
    ensure_layouts,
    rename_layout,
    save_layout_as,
    set_backdrop,
    set_backdrop_opacity,
    sorted_layouts,
    switch_layout,
)
from centralight.model import Layout, MapSpot, frozen_map
from centralight.new_show import create_show, show_from_patch
from centralight.show_edit import add_colour_light, move_lights, remove_lights
from centralight.show_io import (
    InputFileError,
    backup_show,
    layout_from_dict,
    layout_to_dict,
    list_backups,
    load_show,
    restore_backup,
    save_whole_show,
    show_to_dict,
)
from tests.test_follow_screen import BaseTestCase

MAIN_101 = MapSpot(0.2, 0.2, 0.08)  # where SlimPAR 101 starts in a new show


class LayoutFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()

    def test_a_new_show_has_main(self) -> None:
        self.assertEqual(self.show.active_layout, "main")
        self.assertEqual([lay.name for lay in self.show.layouts], ["Main"])
        self.assertEqual(self.show.layout().positions["slimpar-101"], MAIN_101)
        self.assertEqual(len(self.show.layout().positions), 8)  # the colour lights only

    def test_save_as_then_switch(self) -> None:
        show, christmas = save_layout_as(self.show, "Christmas")
        self.assertEqual(show.active_layout, christmas)
        show = move_lights(show, {"slimpar-101": MapSpot(0.9, 0.8, 0.15)})
        self.assertEqual(show.layout().positions["slimpar-101"], MapSpot(0.9, 0.8, 0.15))  # edits go into the active layout
        self.assertEqual(show.layout("main").positions["slimpar-101"], MAIN_101)  # and nowhere else
        back = switch_layout(show, "main")
        self.assertEqual(back.fixture("slimpar-101").map, MAIN_101)
        self.assertEqual(switch_layout(back, christmas).fixture("slimpar-101").map, MapSpot(0.9, 0.8, 0.15))

    def test_lights_added_later_stay_put_and_join_the_layout(self) -> None:
        show, christmas = save_layout_as(self.show, "Christmas")
        show, new_id = add_colour_light(show, 0.4, 0.6)
        self.assertIn(new_id, show.layout().positions)
        self.assertNotIn(new_id, show.layout("main").positions)
        show = switch_layout(show, "main")
        self.assertEqual(show.fixture(new_id).map, MapSpot(0.4, 0.6, 0.07))  # Main didn't know it: it stays
        self.assertIn(new_id, show.layout("main").positions)  # and now Main has it

    def test_removed_lights_leave_every_layout(self) -> None:
        show, christmas = save_layout_as(self.show, "Christmas")
        show = remove_lights(show, {"slimpar-103"})
        for layout in show.layouts:
            self.assertNotIn("slimpar-103", layout.positions)

    def test_names_stay_unique_and_tidy(self) -> None:
        show, first = save_layout_as(self.show, "  Christmas   eve ")
        show, second = save_layout_as(show, "christmas eve")
        self.assertEqual(show.layout(first).name, "Christmas eve")
        self.assertEqual(show.layout(second).name, "christmas eve 2")
        self.assertNotEqual(first, second)
        renamed = rename_layout(show, second, "Main")
        self.assertEqual(renamed.layout(second).name, "Main 2")
        self.assertIs(rename_layout(show, second, "   "), show)
        self.assertEqual([lay.name for lay in sorted_layouts(show)], ["Christmas eve", "christmas eve 2", "Main"])

    def test_delete(self) -> None:
        self.assertIs(delete_layout(self.show, "main"), self.show)  # the last one can't go
        show, christmas = save_layout_as(self.show, "Christmas")
        show = move_lights(show, {"slimpar-101": MapSpot(0.9, 0.8, 0.15)})
        gone = delete_layout(show, christmas)  # the active one: the lights move to the neighbour
        self.assertEqual(gone.active_layout, "main")
        self.assertEqual(gone.fixture("slimpar-101").map, MAIN_101)
        self.assertEqual([lay.id for lay in gone.layouts], ["main"])
        other = delete_layout(show, "main")  # not the active one: nothing moves
        self.assertEqual(other.fixture("slimpar-101").map, MapSpot(0.9, 0.8, 0.15))

    def test_photo_and_strength_belong_to_a_layout(self) -> None:
        show = set_backdrop(self.show, "media/room-a.jpg")
        show = set_backdrop_opacity(show, 0.3)
        show, christmas = save_layout_as(show, "Christmas")  # a new layout starts with the same photo
        show = set_backdrop(show, "media/room-b.jpg")
        self.assertEqual(show.layout("main").backdrop, "media/room-a.jpg")
        self.assertEqual(show.layout(christmas).backdrop, "media/room-b.jpg")
        self.assertEqual(show.layout(christmas).backdrop_opacity, 0.3)
        self.assertEqual(set_backdrop_opacity(show, 7).layout().backdrop_opacity, 1.0)

    def test_ensure_recovers_a_missing_active_layout(self) -> None:
        lost = replace(self.show, layouts=(Layout("church", "Church", frozen_map({})),), active_layout="christmas")
        show, warnings = ensure_layouts(lost)
        self.assertEqual(show.active_layout, "christmas")
        self.assertEqual(show.layout().positions["slimpar-101"], MAIN_101)  # made from where the lights are
        self.assertTrue(warnings)
        none_yet, warnings = ensure_layouts(replace(self.show, layouts=(), active_layout=""), backdrop="room.jpg")
        self.assertEqual((none_yet.active_layout, none_yet.layout().name, none_yet.layout().backdrop), ("main", "Main", "room.jpg"))
        self.assertEqual(warnings, [])


class LayoutFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Show"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip(self) -> None:
        layout = Layout("christmas", "Christmas", frozen_map({"slimpar-101": MapSpot(0.12345, 0.5, 0.1)}), "media/room-1.jpg", 0.4)
        data = layout_to_dict(layout)
        self.assertEqual(data["format"], "centralight-layout")
        self.assertEqual(data["positions"]["slimpar-101"], {"x": 0.1235, "y": 0.5, "r": 0.1})
        again, warnings = layout_from_dict(json.loads(json.dumps(data)), expected_id="christmas")
        self.assertEqual(warnings, [])
        self.assertEqual((again.name, again.backdrop, again.backdrop_opacity), ("Christmas", "media/room-1.jpg", 0.4))
        self.assertNotIn("backdrop", layout_to_dict(replace(layout, backdrop="", backdrop_opacity=0.55)))

    def test_bad_files_are_explained(self) -> None:
        with self.assertRaises(InputFileError):
            layout_from_dict({"id": "other", "name": "X"}, expected_id="christmas")
        with self.assertRaises(InputFileError):
            layout_from_dict([1, 2])
        layout, warnings = layout_from_dict({"id": "x", "name": "X", "backdrop": "..\\..\\Windows\\win.ini", "positions": {"slimpar-101": "left"}})
        self.assertEqual(layout.backdrop, "")
        self.assertEqual(len(warnings), 2)
        for outside in ("C:/photo.jpg", "/etc/photo.jpg", "media/../../photo.jpg"):
            self.assertEqual(layout_from_dict({"id": "x", "backdrop": outside})[0].backdrop, "", outside)

    def test_a_new_show_saves_main_and_restarts_with_it(self) -> None:
        show = create_show(self.folder)
        self.assertTrue((self.folder / "layouts" / "main.json").is_file())
        self.assertEqual(json.loads((self.folder / "show.json").read_text(encoding="utf-8"))["active_layout"], "main")
        show, christmas = save_layout_as(show, "Christmas")
        show = move_lights(show, {"slimpar-101": MapSpot(0.9, 0.8, 0.15)})
        save_whole_show(show)
        again = load_show(self.folder)
        self.assertEqual(again.active_layout, christmas)
        self.assertEqual(again.fixture("slimpar-101").map, MapSpot(0.9, 0.8, 0.15))
        self.assertEqual(again.layout("main").positions["slimpar-101"], MAIN_101)

    def test_an_old_show_gets_main_with_its_room_photo(self) -> None:
        show = create_show(self.folder)
        data = show_to_dict(show)
        data.pop("active_layout")
        (self.folder / "show.json").write_text(json.dumps(data), encoding="utf-8")
        (self.folder / "layouts" / "main.json").unlink()
        (self.folder / "room.jpg").write_bytes(b"not really a photo")
        old = load_show(self.folder)
        self.assertEqual(old.active_layout, "main")
        self.assertEqual(old.layout().backdrop, "room.jpg")
        self.assertEqual(old.layout().positions["slimpar-101"], MAIN_101)

    def test_a_broken_layout_file_is_left_out(self) -> None:
        create_show(self.folder)
        (self.folder / "layouts" / "broken.json").write_text("{nope", encoding="utf-8")
        show = load_show(self.folder)
        self.assertEqual([lay.id for lay in show.layouts], ["main"])
        self.assertTrue(any("broken.json" in w for w in show.warnings))

    def test_backups_include_layouts(self) -> None:
        show = create_show(self.folder)
        show, christmas = save_layout_as(show, "Christmas")
        save_whole_show(show)
        backup = backup_show(self.folder)
        (self.folder / "layouts" / "christmas.json").unlink()
        restore_backup(self.folder, backup)
        self.assertTrue((self.folder / "layouts" / "christmas.json").is_file())
        self.assertEqual(len(list_backups(self.folder)), 2)  # restoring backed up the current files first


class SwitchingMovesTheSamplingTests(BaseTestCase):
    def test_the_lights_sample_their_new_spots_at_once(self) -> None:
        engine = self.make()
        engine.tick()
        self.assertEqual(self.rgb(5), (255, 0, 0))  # SlimPAR 101 sits on the left (red) half
        show, christmas = save_layout_as(self.show, "Christmas")
        show = move_lights(show, {"slimpar-101": MapSpot(0.85, 0.2, 0.08)})  # over to the blue half
        engine.submit(SwapShow(show))
        engine.tick()
        self.assertEqual(self.rgb(5), (0, 0, 255))
        engine.submit(SwapShow(switch_layout(show, "main")))
        engine.tick()  # one tick later it's back on the left
        self.assertEqual(self.rgb(5), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
