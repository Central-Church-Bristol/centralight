"""Show folders: the new show from the patch, load/save, validation, migration, backups."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from centralight.fixtures import PATCH

from centralight import files, show_io
from centralight.model import Input, Transition
from centralight.new_show import open_or_create_show, show_from_patch
from centralight.show_io import (
    InputFileError,
    ShowError,
    backup_show,
    input_from_dict,
    input_to_dict,
    list_backups,
    load_show,
    prune_backups,
    restore_backup,
    save_input,
    save_show_file,
)
from tests.starter import create_show  # (their shows keep Follow screen, 6 Nov)


class TempFolder(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "Central Church"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class NewShowTests(TempFolder):
    def test_show_from_patch_matches_fixtures_py(self) -> None:
        show = show_from_patch()
        self.assertEqual(len(show.fixtures), len(PATCH))
        for item, fx in zip(PATCH, show.fixtures):
            self.assertEqual(fx.address, item["start_ch"])
            self.assertEqual(fx.profile, item["profile"])
        self.assertEqual([fx.id for fx in show.colour_fixtures()][:2], ["slimpar-101", "slimpar-102"])
        self.assertEqual([(h.fixture, h.label) for h in show.house], [("dimmer-1", "Festoon"), ("dimmer-4", "Key lights")])
        for fx in show.colour_fixtures():
            self.assertTrue(0 <= fx.map.x <= 1 and 0 <= fx.map.y <= 1)
        spots = {(fx.map.x, fx.map.y) for fx in show.colour_fixtures()}
        self.assertEqual(len(spots), 8)  # nobody starts on top of anybody else

    def test_create_then_open(self) -> None:
        created = create_show(self.folder)
        self.assertTrue((self.folder / "show.json").is_file())
        self.assertTrue((self.folder / "inputs").is_dir())
        self.assertEqual(open_or_create_show(self.folder).fixtures, created.fixtures)
        with self.assertRaises(ShowError):
            create_show(self.folder)


class RoundTripTests(TempFolder):
    def test_show_and_inputs_round_trip(self) -> None:
        show = create_show(self.folder)
        inp = Input(
            id="bar-sweep",
            name="Bar sweep",
            colour="#7A3CFF",
            infinite=False,
            length=10,
            end="loop",
            transition=Transition("wipe", 2.0),
            tracks=show_io.freeze([[{"id": "E4", "kind": "ndi", "source": "Media Background Pane"}], [], [], []]),
        )
        save_input(self.folder, inp)
        loaded = load_show(self.folder)
        self.assertEqual(loaded.inputs["bar-sweep"], inp)
        self.assertEqual(loaded.fixtures, show.fixtures)
        self.assertEqual(loaded.warnings, ())

    def test_defaults_are_left_out(self) -> None:
        data = input_to_dict(Input(id="calm", name="Calm"))
        self.assertEqual(set(data), {"format", "version", "id", "name", "tracks"})

    def test_hand_written_input_with_bom_and_missing_fields(self) -> None:
        create_show(self.folder)
        path = self.folder / "inputs" / "warm.json"
        path.write_text('﻿{"name": "Warm", "tracks": [[{"kind": "paint"}]]}', encoding="utf-8")
        show = load_show(self.folder)
        self.assertEqual(show.inputs["warm"].name, "Warm")  # the id comes from the file name


class ValidationTests(TempFolder):
    def test_readable_errors(self) -> None:
        cases = [
            ([], "JSON object"),
            ({"id": "Bad Id"}, "lower-case"),
            ({"id": "x", "transition": {"kind": "spin"}}, "transition"),
            ({"id": "x", "length": -1}, "at least"),
            ({"id": "x", "tracks": [[], [], [], [], []]}, "up to 4"),
            ({"id": "x", "tracks": [[{"no": "kind"}]]}, "kind"),
            ({"id": "x", "version": 99}, "newer"),
            ({"id": "x", "format": "something-else"}, "format"),
        ]
        for data, words in cases:
            with self.subTest(data=data):
                with self.assertRaises(InputFileError) as caught:
                    input_from_dict(data)
                self.assertIn(words, str(caught.exception))

    def test_bad_files_are_reported_not_fatal(self) -> None:
        create_show(self.folder)
        inputs = self.folder / "inputs"
        (inputs / "broken.json").write_text("{not json", encoding="utf-8")
        (inputs / "renamed.json").write_text(json.dumps({"id": "other", "name": "Other"}), encoding="utf-8")
        save_input(self.folder, Input(id="good", name="Good"))
        show = load_show(self.folder)
        self.assertEqual(set(show.inputs), {"follow-screen", "good"})
        self.assertIn("isn't valid JSON", show.input_errors["broken"])
        self.assertIn("must match", show.input_errors["renamed"])

    def test_unknown_fields_and_bad_colour_warn(self) -> None:
        _inp, warnings = input_from_dict({"id": "x", "colour": "red", "sparkle": 1})
        self.assertTrue(any("sparkle" in w for w in warnings))
        self.assertTrue(any("colour" in w for w in warnings))

    def test_bad_show_json(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / "show.json").write_text('{"fixtures": [{"id": "a", "profile": "nope", "address": 1}]}', encoding="utf-8")
        with self.assertRaises(ShowError) as caught:
            load_show(self.folder)
        self.assertIn("unknown profile", str(caught.exception))
        with self.assertRaises(ShowError):
            load_show(self.folder / "missing")


class VersionTests(TempFolder):
    def test_newer_show_opens_read_only_and_refuses_to_save(self) -> None:
        create_show(self.folder)
        path = self.folder / "show.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = show_io.SHOW_VERSION + 1
        path.write_text(json.dumps(data), encoding="utf-8")
        show = load_show(self.folder)
        self.assertTrue(show.read_only)
        self.assertIn("read-only", show.warnings[0])
        with self.assertRaises(ShowError):
            save_show_file(show)

    def test_migrations_run_in_order(self) -> None:
        def one_to_two(data: dict) -> dict:
            data["name"] = data["name"] + " (v2)"
            return data

        def two_to_three(data: dict) -> dict:
            data["name"] = data["name"] + " (v3)"
            return data

        with mock.patch.object(show_io, "SHOW_VERSION", 3), mock.patch.dict(show_io.MIGRATIONS, {1: one_to_two, 2: two_to_three}):
            data, read_only = show_io.migrate_show({"version": 1, "name": "Old"})
        self.assertFalse(read_only)
        self.assertEqual(data["name"], "Old (v2) (v3)")
        self.assertEqual(data["version"], 3)


class AtomicSaveTests(TempFolder):
    def test_no_temp_files_left_behind(self) -> None:
        create_show(self.folder)
        for n in range(5):
            save_input(self.folder, Input(id="x", name=f"Name {n}"))
        names = sorted(p.name for p in (self.folder / "inputs").iterdir())
        self.assertEqual(names, ["follow-screen.json", "x.json"])

    def test_retries_when_the_file_is_briefly_locked(self) -> None:
        target = Path(self._tmp.name) / "a.json"
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked by antivirus")
            return real_replace(src, dst)

        with mock.patch.object(files.os, "replace", flaky_replace):
            files.write_json_atomic(target, {"ok": True})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_gives_up_cleanly_if_locked_for_good(self) -> None:
        target = Path(self._tmp.name) / "b.json"
        with mock.patch.object(files.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                files.write_atomic(target, b"x", retries=3, retry_delay=0.001)
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])


class BackupTests(TempFolder):
    def test_backup_skips_when_nothing_changed(self) -> None:
        create_show(self.folder)
        first = backup_show(self.folder, datetime(2026, 11, 2, 10, 0, 0))
        self.assertIsNotNone(first)
        self.assertIsNone(backup_show(self.folder, datetime(2026, 11, 2, 10, 10, 0)))
        save_input(self.folder, Input(id="x", name="X"))
        self.assertIsNotNone(backup_show(self.folder, datetime(2026, 11, 2, 10, 20, 0)))
        self.assertEqual(len(list_backups(self.folder)), 2)

    def test_prune_keeps_last_20_plus_one_a_day_for_60_days(self) -> None:
        create_show(self.folder)
        backups = self.folder / "backups"
        backups.mkdir()
        now = datetime(2026, 11, 2, 12, 0, 0)
        stamps = []
        for day in range(90):  # three backups a day for 90 days
            for hour in (9, 12, 15):
                stamp = now - timedelta(days=day) + timedelta(hours=hour - 12)
                if stamp <= now:
                    stamps.append(stamp)
        for stamp in stamps:
            (backups / stamp.strftime("%Y-%m-%d_%H%M%S.zip")).write_bytes(b"PK")
        prune_backups(self.folder, now)
        kept = [t for t, _ in list_backups(self.folder)]
        newest_20 = sorted(stamps, reverse=True)[:20]
        for stamp in newest_20:
            self.assertIn(stamp, kept)
        days = {t.date() for t in kept}
        self.assertEqual(len(days), 61)  # today and the 60 days before
        self.assertTrue(all(now - t <= timedelta(days=60) for t in kept))
        self.assertEqual(len(kept), 20 + (61 - 7))  # 7 days are already covered by the newest 20

    def test_restore_puts_the_old_files_back(self) -> None:
        create_show(self.folder)
        save_input(self.folder, Input(id="old", name="Old"))
        backup = backup_show(self.folder, datetime(2026, 11, 2, 9, 0, 0))
        (self.folder / "inputs" / "old.json").unlink()
        save_input(self.folder, Input(id="new", name="New"))
        restore_backup(self.folder, backup)
        self.assertEqual(set(load_show(self.folder).inputs), {"follow-screen", "old"})


if __name__ == "__main__":
    unittest.main()
