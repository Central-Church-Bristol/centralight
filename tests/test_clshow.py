"""M13: .clshow files, and opening them with CentraLight.

- Export and import: a show comes back as it was (inputs, layouts, automations, notes, show variables, painted
  pictures, pictures and videos and their links; the same ids), with or without its pictures and videos, and
  exporting changes nothing in the show. Backups and the AI guide files stay behind.
- Export everything: every show, the global automations and the settings, then importing only some of them.
- A name that's taken: Keep both ("Morning (2)") or Replace (the old folder to a stand-in Recycle Bin, the id kept).
- Files from anyone: names outside the show folder, "C:" and "\\", device names (as text only: no file or folder with
  such a name is ever made, and nothing asks the disk about one), links, sizes that lie, unpacking bombs, unsafe
  expressions, bad automations and show.json files are refused or left out, and a failed import leaves nothing behind.
- The file type: registering, syncing and unregistering against a stand-in registry key (never the real
  Software\\Classes), and the document icon.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import shutil
import stat
import struct
import tempfile
import unittest
import uuid
import winreg
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

from centralight import clshow, filetype
from centralight.clshow import (
    KIND_EVERYTHING,
    KIND_SHOW,
    PackageError,
    export_everything,
    export_show,
    import_globals,
    import_settings,
    import_show,
    keep_both_name,
    name_taken,
    read_package,
)
from centralight.elements import has_device_name
from centralight.global_automations import FORMAT_GLOBAL, GLOBAL_DIR, ORDER_FILE, load_global_automations
from centralight.layouts import save_layout_as
from centralight.links import FORMAT_LINKS, read_stamps
from centralight.model import Automation, GlobalVar, Input
from centralight.new_show import show_from_patch
from centralight.settings import Settings
from centralight.show_io import (
    MAX_INPUT_BYTES,
    automation_to_dict,
    freeze,
    input_to_dict,
    load_show,
    save_show_file,
    save_whole_show,
    show_to_dict,
)
from centralight.shows import list_shows, show_id
from tests.starter import create_show  # (their shows keep Follow screen, 6 Nov)

ROOT = Path(__file__).resolve().parents[1]
PAINT = "paint/0123456789ab.png"
MASK = "masks/ba9876543210.png"
CANDLES = r"G:\My Drive\Church\candles.jpg"
LOOP = r"G:\My Drive\Church\loop.mp4"
ROOM = r"C:\Users\Church\Pictures\room.jpg"
NOTES = "Morning service.\nCandles from 10:30."


def rich_show(root: Path, name: str = "Morning") -> Path:
    """A show with a bit of everything: inputs with expressions, painted strokes and a mask, a picture and a video
    linked to their originals, a second layout with a linked room photo, an automation, a show variable and notes;
    plus things that aren't exported (backups, the AI guide files) and a file that isn't part of a show."""
    folder = root / name
    show = create_show(folder, name)
    pulse = Input(id="pulse", name="Pulse", locals=freeze([{"name": "width", "type": "number", "value": 0.2}]),
                  tracks=freeze([[{"id": "E1", "kind": "paint", "strokes": PAINT,
                                   "fill": {"type": "colour", "colour": {"expr": "hsv(time * speed, 1, 1)"}},
                                   "mask": {"paint": MASK, "exclude": ["slimpar-101"]}, "opacity": {"expr": "width * 2"}}],
                                 [{"id": "E2", "kind": "image", "file": "media/candles.jpg", "original": CANDLES}], [], []]))
    clip = Input(id="clip", name="Clip", infinite=False, length=8.0,
                 tracks=freeze([[{"id": "E1", "kind": "video", "file": "media/loop.mp4", "original": LOOP}], [], [], []]))
    show = show.with_inputs({**show.inputs, "pulse": pulse, "clip": clip}, show.input_order + ("pulse", "clip"))
    sunday = Automation(id="sunday", name="Sunday", conditions=freeze([{"type": "days", "days": ["sun"]}]),
                        actions=freeze([{"do": "base", "input": "pulse"}]))
    show = replace(show.with_automations({"sunday": sunday}, ("sunday",)), notes=NOTES,
                   globals=(GlobalVar("speed", "number", 0.5),))
    show, stage = save_layout_as(show, "Stage")
    show = replace(show, layouts=tuple(replace(lay, backdrop="media/room-abc.jpg", backdrop_original=ROOM)
                                       if lay.id == stage else lay for lay in show.layouts))
    save_whole_show(show)
    files = {
        PAINT: b"\x89PNG strokes", MASK: b"\x89PNG mask", "media/candles.jpg": b"\xff\xd8 candles",
        "media/loop.mp4": b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40, "media/room-abc.jpg": b"\xff\xd8 room",
        "backups/2026-11-01_090000.zip": b"PK old", "AI_GUIDE.md": b"guide", "AGENTS.md": b"guide",
        "input.schema.json": b"{}", "notes.txt": b"not part of a show",
        "media/links.json": json.dumps({"format": FORMAT_LINKS, "version": 1, "copies": {
            "media/candles.jpg": {"original": CANDLES, "size": 10, "modified": 1},
            "media/loop.mp4": {"original": LOOP, "size": 10252, "modified": 2}}}).encode("utf-8"),
    }
    for rel, data in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def snapshot(folder: Path) -> dict[str, tuple[bytes, int]]:
    return {p.relative_to(folder).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*")) if p.is_file()}


def input_data(input_id: str, element: dict | None = None, **extra: Any) -> dict:
    element = element or {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF0000"}}
    return {**input_to_dict(Input(id=input_id, name=input_id.title())), "tracks": [[element], [], [], []], **extra}


SHOW_JSON = show_to_dict(show_from_patch(name="Morning"))
ONE_SHOW = {"format": "centralight-package", "version": 1, "kind": "show", "made": "2026-11-03T21:00:00", "media": True,
            "shows": [{"path": "shows/1", "name": "Morning", "id": "morning", "folder": "Morning"}]}


def write_package(path: Path, files: dict[str, Any], manifest: Any = ONE_SHOW,
                  raw: list[tuple[zipfile.ZipInfo, bytes]] = ()) -> Path:
    """A .clshow made by hand (as anyone could), with zipfile: files {name: bytes, text or JSON data}, and raw entries
    whose zip information is set up by the test (names zipfile would otherwise tidy, links...)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if manifest is not None:
            archive.writestr("manifest.json", manifest if isinstance(manifest, (str, bytes)) else json.dumps(manifest))
        for name, data in files.items():  # (JSON packed, everything else stored as it is, as CentraLight does)
            packing = zipfile.ZIP_DEFLATED if name.lower().endswith(".json") else zipfile.ZIP_STORED
            archive.writestr(name, data if isinstance(data, (str, bytes)) else json.dumps(data), compress_type=packing)
        for info, data in raw:
            archive.writestr(info, data)
    return path


def raw_entry(name: str, **attributes: Any) -> zipfile.ZipInfo:
    """Zip information with exactly this name (zipfile turns "\\" into "/" and stops at a NUL unless told otherwise)."""
    info = zipfile.ZipInfo("placeholder")
    info.filename = name
    for key, value in attributes.items():
        setattr(info, key, value)
    return info


def patch_listed(path: Path, name: str, offset: int, form: str, value: int) -> None:
    """Change what the zip's list says about a file (its entry in the central directory, at offset: 8 its flags,
    24 its unpacked size), as someone editing the bytes could."""
    data = bytearray(path.read_bytes())
    wanted, at = name.encode("utf-8"), 0
    while True:
        at = data.index(b"PK\x01\x02", at)
        length = struct.unpack_from("<H", data, at + 28)[0]
        if bytes(data[at + 46:at + 46 + length]) == wanted:
            struct.pack_into(form, data, at + offset, value)
            break
        at += 4
    path.write_bytes(bytes(data))


def patch_listed_size(path: Path, name: str, size: int) -> None:
    """Make the zip's list say a file unpacks to a different size than it does."""
    patch_listed(path, name, 24, "<I", size)


class DeviceTouched(BaseException):
    """Something asked the disk about a device's name. (Not an Exception, so no "except Exception" can hide it.)"""


@contextlib.contextmanager
def no_device_paths():
    """Stop, and note, anything that asks the disk about a path with a device's name in it (COM3 is the lights' USB
    port: even asking whether "COM3.jpg" exists opens it). The call never reaches the disk; the test then checks the
    list is empty."""
    asked: list[str] = []

    def spy(real, places=(0,)):
        def wrapper(*args, **kwargs):
            for place in places:
                if place < len(args) and isinstance(args[place], (str, bytes, os.PathLike)):
                    text = os.fspath(args[place])
                    text = os.fsdecode(text) if isinstance(text, bytes) else text
                    if has_device_name(text):
                        asked.append(text)
                        raise DeviceTouched(text)
            return real(*args, **kwargs)
        return wrapper

    one = [(builtins, "open"), (io, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "mkdir"), (os, "listdir"),
           (os, "scandir"), (os, "remove"), (os, "unlink"), (os, "rmdir"), (os.path, "exists"), (os.path, "lexists"),
           (os.path, "isfile"), (os.path, "isdir"), (os.path, "realpath")]
    with contextlib.ExitStack() as stack:
        for module, name in one:
            stack.enter_context(mock.patch.object(module, name, spy(getattr(module, name))))
        for name in ("rename", "replace"):
            stack.enter_context(mock.patch.object(os, name, spy(getattr(os, name), (0, 1))))
        yield asked


class FilesCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "CentraLight Shows"
        self.root.mkdir()
        self.out = self.base / "Exports"
        self.out.mkdir()
        self.other = self.base / "Other PC" / "CentraLight Shows"  # (made by the import)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def folders(self, root: Path) -> list[str]:
        return sorted(p.name for p in root.iterdir())


# ---------------------------------------------------------------- export and import


class ExportImportTests(FilesCase):
    def test_a_show_comes_back_as_it_was(self) -> None:
        folder = rich_show(self.root)
        before = snapshot(folder)
        steps: list[tuple[int, int]] = []
        report = export_show(folder, self.out / "Morning.clshow", progress=lambda done, total: steps.append((done, total)))
        self.assertEqual(snapshot(folder), before)  # exporting changed nothing in the show
        self.assertEqual(report.shows, ["Morning"])
        self.assertEqual(steps[-1][0], steps[-1][1])
        self.assertEqual([line for line in report.skipped], ["Morning: \"notes.txt\" isn't part of a show, so it was left out"])
        with zipfile.ZipFile(report.target) as archive:
            names = set(archive.namelist())
            self.assertEqual(archive.getinfo("shows/1/media/loop.mp4").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.getinfo("shows/1/inputs/pulse.json").compress_type, zipfile.ZIP_DEFLATED)
        self.assertEqual(names, {"manifest.json"} | {f"shows/1/{rel}" for rel in (
            "show.json", "inputs/follow-screen.json", "inputs/pulse.json", "inputs/clip.json", "layouts/main.json",
            "layouts/stage.json", "automations/sunday.json", PAINT, MASK, "media/candles.jpg", "media/loop.mp4",
            "media/room-abc.jpg", "media/links.json")})  # (no backups, AI guide files or notes.txt)
        self.assertEqual(report.files, len(names) - 1)

        package = read_package(report.target)
        self.assertEqual((package.kind, package.media, package.globals, package.settings), (KIND_SHOW, True, None, None))
        item = package.shows[0]
        self.assertTrue(item.ok)
        self.assertEqual((item.name, item.id, item.folder, item.problems), ("Morning", "morning", "Morning", ()))
        self.assertEqual(set(item.originals), {CANDLES, LOOP, ROOM})  # text only: nothing has looked at them
        self.assertTrue(package.has_links)
        self.assertEqual(item.describe(), "3 inputs, 1 automation, 2 layouts, 3 pictures and videos (10 KB)")

        made = import_show(package, 0, self.other, name=item.name)
        self.assertEqual(made, self.other / "Morning")
        self.assertEqual(show_id(made), show_id(folder))  # the same id: Stream Deck keys still work
        mine, theirs = load_show(folder), load_show(made)
        self.assertEqual(theirs.warnings, ())
        self.assertEqual(dict(theirs.inputs), dict(mine.inputs))  # (the links to originals too)
        self.assertEqual(dict(theirs.automations), dict(mine.automations))
        self.assertEqual(theirs.layouts, mine.layouts)  # (the room photo and its link too)
        for name in ("name", "notes", "globals", "fixtures", "house", "input_order", "active_layout", "identify_seed"):
            self.assertEqual(getattr(theirs, name), getattr(mine, name), name)
        for rel in (PAINT, MASK, "media/candles.jpg", "media/loop.mp4", "media/room-abc.jpg"):
            self.assertEqual((made / rel).read_bytes(), (folder / rel).read_bytes(), rel)
        self.assertEqual(read_stamps(made), read_stamps(folder))  # media\links.json: where each copy came from
        for gone in ("backups", "AI_GUIDE.md", "AGENTS.md", "input.schema.json", "notes.txt"):
            self.assertFalse((made / gone).exists(), gone)
        self.assertEqual(self.folders(self.other), ["Morning"])  # (nothing else left behind)

    def test_leaving_out_pictures_and_videos_keeps_the_room_photo_and_the_links(self) -> None:
        folder = rich_show(self.root)
        report = export_show(folder, self.out / "Small.clshow", media=False)
        with zipfile.ZipFile(report.target) as archive:
            names = set(archive.namelist())
        self.assertNotIn("shows/1/media/candles.jpg", names)
        self.assertNotIn("shows/1/media/loop.mp4", names)
        for kept in ("media/room-abc.jpg", "media/links.json", PAINT, MASK):
            self.assertIn(f"shows/1/{kept}", names)
        package = read_package(report.target)
        self.assertFalse(package.media)
        made = import_show(package, 0, self.other, name="Morning")
        theirs = load_show(made)
        image = dict(theirs.inputs["pulse"].tracks[1][0])
        self.assertEqual((image["file"], image["original"]), ("media/candles.jpg", CANDLES))  # the link waits for an OK
        self.assertFalse((made / "media" / "candles.jpg").exists())
        self.assertTrue((made / "media" / "room-abc.jpg").is_file())
        self.assertEqual(read_stamps(made), read_stamps(folder))

    def test_everything_and_importing_only_some_of_it(self) -> None:
        rich_show(self.root, "Morning")
        create_show(self.root / "Evening Service", "Evening")
        folder = self.root / GLOBAL_DIR
        folder.mkdir()
        load = Automation(id="sunday-morning", name="Sunday: load Morning", conditions=freeze([{"type": "days", "days": ["sun"]}]),
                          actions=freeze([{"do": "load_show", "show": "morning"}]))
        dim = Automation(id="dim", name="Dim at night", conditions=freeze([{"type": "time", "from": "22:00", "to": "23:00"}]),
                         actions=freeze([{"do": "master", "level": 0.3}]))
        for automation in (load, dim):
            (folder / f"{automation.id}.json").write_text(json.dumps(automation_to_dict(automation)), encoding="utf-8")
        (folder / ORDER_FILE).write_text(json.dumps({"format": FORMAT_GLOBAL, "version": 1, "order": ["dim", "sunday-morning"]}),
                                         encoding="utf-8")
        (folder / "AI_GUIDE.md").write_text("guide", encoding="utf-8")
        settings_file = self.base / "settings.json"
        Settings(output="usb", usb_port="COM3", black_fade=3.5, geometry=[1, 2, 3, 4],
                 show_folder=str(self.root / "Morning")).save(settings_file)  # (only ever text: no port is opened)

        report = export_everything(self.root, self.out / "Everything.clshow", settings_file=settings_file)
        self.assertEqual((report.shows, report.globals, report.settings), (["Evening", "Morning"], 2, True))
        self.assertFalse(any("AI_GUIDE" in line for line in report.skipped), report.skipped)
        package = read_package(report.target)
        self.assertEqual(package.kind, KIND_EVERYTHING)
        self.assertEqual([(s.name, s.id) for s in package.shows], [("Evening", "evening-service"), ("Morning", "morning")])
        self.assertEqual([a.id for a in package.globals.ordered()], ["dim", "sunday-morning"])
        self.assertEqual(package.settings.settings.black_fade, 3.5)
        self.assertEqual(package.all_problems(), [])

        # A new PC: just Evening, the global automations and the settings
        evening = package.shows[0]
        made = import_show(package, evening, self.other, name=evening.name)
        self.assertEqual(made, self.other / "Evening Service")  # the folder (and id) it had
        self.assertEqual([s.name for s in list_shows(self.other)], ["Evening"])
        result = import_globals(package, self.other / GLOBAL_DIR)
        self.assertEqual((result.added, result.replaced, result.kept), (("Dim at night", "Sunday: load Morning"), (), ()))
        here = load_global_automations(self.other / GLOBAL_DIR)
        self.assertEqual([a.id for a in here.ordered_automations()], ["dim", "sunday-morning"])
        self.assertEqual(here.automations["sunday-morning"], load)
        again = import_globals(package, self.other / GLOBAL_DIR)
        self.assertEqual(again.kept, ("Dim at night", "Sunday: load Morning"))  # already here: left as they are
        self.assertEqual(import_globals(package, self.other / GLOBAL_DIR, replace=True).replaced, again.kept)

        mine = self.base / "Other PC" / "settings.json"
        Settings(usb_port="COM7", geometry=[5, 6, 7, 8], show_folder=str(made)).save(mine)
        import_settings(package, mine)
        settings = Settings.load(mine)
        # this PC keeps its own output (Simulated here), port, window and folder; the rest comes across
        self.assertEqual((settings.output, settings.black_fade), ("simulated", 3.5))
        self.assertEqual((settings.usb_port, settings.geometry, settings.show_folder), ("COM7", [5, 6, 7, 8], str(made)))
        self.assertEqual(package.settings.settings.remote_key, "", "the phone page's key never leaves this PC")

    def test_a_show_file_has_no_globals_or_settings_to_import(self) -> None:
        package = read_package(export_show(rich_show(self.root), self.out / "Morning.clshow").target)
        with self.assertRaises(PackageError):
            import_globals(package, self.other / GLOBAL_DIR)
        with self.assertRaises(PackageError):
            import_settings(package, self.base / "settings.json")
        self.assertFalse((self.base / "settings.json").exists())

    def test_a_name_thats_taken_keep_both_or_replace(self) -> None:
        folder = rich_show(self.root)
        package = read_package(export_show(folder, self.out / "Morning.clshow").target)
        self.assertEqual(name_taken(self.root, "MORNING").folder, str(folder))
        self.assertIsNone(name_taken(self.root, "Evening"))
        both = keep_both_name(self.root, "Morning")
        self.assertEqual(both, "Morning (2)")
        made = import_show(package, 0, self.root, name=both)
        self.assertEqual(load_show(made).name, "Morning (2)")
        self.assertNotEqual(show_id(made), show_id(folder))
        self.assertEqual(keep_both_name(self.root, "Morning (2)"), "Morning (3)")

        save_show_file(replace(load_show(folder), notes="The old one"), folder)
        binned: list[tuple[str, str]] = []

        def trash(path: Path) -> None:  # (a stand-in for the Recycle Bin)
            binned.append((path.name, json.loads((path / "show.json").read_text(encoding="utf-8")).get("notes")))
            shutil.rmtree(path)

        notes: list[str] = []
        replaced = import_show(package, 0, self.root, name="Morning", replace_folder=folder, trash=trash, notes=notes)
        self.assertEqual(replaced, folder)  # its folder, so its id (and Stream Deck keys) stay
        self.assertEqual(len(binned), 1)
        self.assertTrue(binned[0][0].startswith("Morning (replaced "), binned)  # (put back, it can't mix with the new one)
        self.assertEqual(binned[0][1], "The old one")
        self.assertEqual(load_show(folder).notes, NOTES)
        self.assertEqual(notes, [])
        self.assertEqual(self.folders(self.root), ["Morning", "Morning (2)"])

    def test_an_old_show_that_cant_go_to_the_recycle_bin_stays_where_it_can_be_seen(self) -> None:
        folder = rich_show(self.root)
        package = read_package(export_show(folder, self.out / "Morning.clshow").target)

        def full(_path: Path) -> None:
            raise OSError("the Recycle Bin is full")

        notes: list[str] = []
        import_show(package, 0, self.root, name="Morning", replace_folder=folder, trash=full, notes=notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("Recycle Bin", notes[0])
        names = self.folders(self.root)
        self.assertEqual(names[0], "Morning")
        self.assertTrue(names[1].startswith("Morning (replaced "), names)

    def test_replace_refuses_the_loaded_show_and_folders_that_arent_shows_here(self) -> None:
        folder = rich_show(self.root)
        (self.root / GLOBAL_DIR).mkdir()
        package = read_package(export_show(folder, self.out / "Morning.clshow").target)
        binned: list[Path] = []
        for options in ({"replace_folder": folder, "loaded": str(folder)}, {"replace_folder": self.base / "Elsewhere" / "Morning"},
                        {"replace_folder": self.root / GLOBAL_DIR}, {"replace_folder": self.root},
                        {"replace_folder": self.root / "Not a show"}):
            with self.assertRaises(PackageError, msg=str(options)):
                import_show(package, 0, self.root, name="Morning", trash=binned.append, **options)
        self.assertEqual(binned, [])
        self.assertEqual(self.folders(self.root), sorted([GLOBAL_DIR, "Morning"]))  # nothing left behind

    def test_export_never_looks_at_a_file_with_a_devices_name(self) -> None:
        folder = rich_show(self.root)
        real_iterdir = Path.iterdir

        def iterdir(directory: Path):  # (such a file can't be made, so the folder listing is given one)
            yield from real_iterdir(directory)
            if directory.name == "media":
                yield directory / "COM3.jpg"

        with mock.patch.object(Path, "iterdir", iterdir), no_device_paths() as asked:
            report = export_show(folder, self.out / "Morning.clshow")
        self.assertEqual(asked, [])
        self.assertTrue(any("COM3.jpg" in line and "device" in line for line in report.skipped), report.skipped)

    def test_a_failed_export_leaves_no_file(self) -> None:
        folder = rich_show(self.root)

        def stop(done: int, total: int) -> None:
            if done > 100:
                raise KeyboardInterrupt  # (as the window's Cancel would stop it)

        with self.assertRaises(KeyboardInterrupt):
            export_show(folder, self.out / "Morning.clshow", progress=stop)
        self.assertEqual(self.folders(self.out), [])
        with self.assertRaises(PackageError):
            export_show(self.root / "Nothing here", self.out / "Nothing.clshow")
        self.assertEqual(self.folders(self.out), [])


# ---------------------------------------------------------------- files from anyone


class HostileFileTests(FilesCase):
    def make(self, files: dict[str, Any] | None = None, *, manifest: Any = ONE_SHOW, show_json: Any = SHOW_JSON,
             raw: list[tuple[zipfile.ZipInfo, bytes]] = ()) -> Path:
        contents = {"shows/1/show.json": show_json, "shows/1/inputs/good.json": input_data("good")}
        contents.update(files or {})
        return write_package(self.out / f"{uuid.uuid4().hex[:8]}.clshow", contents, manifest, list(raw))

    def refused(self, path: Path, wanted: str = "") -> str:
        with no_device_paths() as asked, self.assertRaises(PackageError) as caught:
            read_package(path)
        self.assertEqual(asked, [])
        self.assertIn(wanted, str(caught.exception))
        return str(caught.exception)

    def test_the_disk_spy_stops_calls_before_they_reach_the_disk(self) -> None:
        # (checked with a made-up device, probe.jpg: a real device's name never goes near the disk, even here)
        probe = self.base / "probe.jpg"
        calls = (probe.exists, probe.is_file, probe.is_dir, probe.stat, probe.resolve, probe.mkdir, probe.unlink,
                 lambda: probe.open("rb"), lambda: probe.write_bytes(b"x"), lambda: os.listdir(probe),
                 lambda: os.rename(self.base / "x", probe), lambda: shutil.rmtree(probe))
        with mock.patch(f"{__name__}.has_device_name", lambda text: "probe" in str(text)):
            for call in calls:
                with no_device_paths() as asked, self.assertRaises(DeviceTouched):
                    call()
                self.assertEqual(len(asked), 1)
        self.assertFalse(probe.exists())

    def test_names_that_point_outside_the_show_are_refused(self) -> None:
        for name in ("../evil.json", "shows/1/../../evil.json", "/evil.json", "C:/Windows/evil.json", "shows/1/inputs/x.json:hidden",
                     "shows//1/show.json", "shows/1/./show.json"):
            self.refused(self.make({name: b"{}"}), "to keep this PC safe")
        self.refused(self.make(raw=[(raw_entry("shows\\1\\inputs\\evil.json"), b"{}")]), "\\")
        self.refused(self.make(raw=[(raw_entry("shows/1/inputs/evil.json\x00.exe"), b"{}")]), "control character")

    def test_device_names_are_refused_as_text(self) -> None:
        for name in ("shows/1/media/COM3.jpg", "aux.png", "COM3:", "shows/1/inputs/com3.json", "shows/1/COM3/x.jpg",
                     "shows/1/media/Con .jpg", "shows/1/media/lpt1.mp4"):
            self.refused(self.make({name: b"x"}), "to keep this PC safe")
        self.assertEqual(self.folders(self.root), [])

    def test_a_packed_picture_is_refused(self) -> None:
        """Pictures and videos are stored as they are in a .clshow: one packed (deflated) could unpack to far more than
        the file holds, so a file with one isn't opened."""
        path = self.out / "packed.clshow"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(ONE_SHOW))
            archive.writestr("shows/1/show.json", json.dumps(SHOW_JSON))
            archive.writestr("shows/1/media/clip.mp4", bytes(2_000_000), compress_type=zipfile.ZIP_DEFLATED)
        self.refused(path, "packed in a way")

    def test_odd_names_links_and_twins_are_refused(self) -> None:
        for name in ("shows/1/media/CANDLE~1.JPG", "shows/1/media/x.jpg.", "shows/1/media/x.jpg ", "shows/1/media/x?.jpg",
                     "shows/1/media/x\x01.jpg"):
            self.refused(self.make({name: b"x"}), "to keep this PC safe")
        link = raw_entry("shows/1/media/link.jpg", create_system=3, external_attr=(stat.S_IFLNK | 0o777) << 16)
        self.refused(self.make(raw=[(link, b"C:/Windows/win.ini")]), "link")
        self.refused(self.make({"shows/1/media/Candles.jpg": b"a", "shows/1/media/candles.JPG": b"b"}), "two files")
        locked = self.make({"shows/1/media/x.jpg": b"x"})
        patch_listed(locked, "shows/1/media/x.jpg", 8, "<H", 0x1)  # (zipfile can't write a locked file itself)
        self.refused(locked, "password")
        fine = read_package(self.make({"shows/1/media/Candles ~ warm.jpg": b"x", "shows/1/media/Café (1).mp4": b"y"}))
        self.assertEqual(sorted(f.name for f in fine.shows[0].files), ["media/Café (1).mp4", "media/Candles ~ warm.jpg"])

    def test_the_manifest_must_be_right(self) -> None:
        self.refused(self.make(manifest=None), "manifest.json")
        self.refused(self.make(manifest="not JSON {"), "manifest.json")
        self.refused(self.make(manifest={**ONE_SHOW, "format": "something-else"}), "isn't a CentraLight show file")
        self.refused(self.make(manifest={**ONE_SHOW, "version": 2}), "newer CentraLight")
        self.refused(self.make(manifest={**ONE_SHOW, "kind": "program"}), "doesn't say what it holds")
        for shows in ([{"path": "../shows/1"}], [{"path": "shows/1"}, {"path": "shows/2"}], "shows/1", []):
            self.refused(self.make(manifest={**ONE_SHOW, "shows": shows}), "list of shows")
        not_a_zip = self.out / "Not a zip.clshow"
        not_a_zip.write_bytes(b"MZ" + bytes(5000))
        self.refused(not_a_zip, "isn't a CentraLight show file")
        self.refused(self.out / "Missing.clshow", "isn't there")

    def test_sizes_are_limited(self) -> None:
        big = input_data("big", name="Big " + os.urandom(MAX_INPUT_BYTES // 2).hex())  # (random: it doesn't pack small)
        package = read_package(self.make({"shows/1/inputs/big.json": big}))
        item = package.shows[0]
        self.assertEqual(sorted(item.inputs), ["good"])
        self.assertTrue(any(p.startswith("inputs\\big.json was left out: the file is too big") for p in item.problems), item.problems)
        self.refused(self.make({"shows/1/inputs/bomb.json": " " * 8_000_000}), "unpacks to far more")  # (a JSON bomb)
        with mock.patch.object(clshow, "MAX_ENTRIES", 3):
            self.refused(self.make({"shows/1/inputs/a.json": input_data("a"), "shows/1/inputs/b.json": input_data("b")}), "too many files")
        with mock.patch.object(zipfile, "ZIP_FILECOUNT_LIMIT", 2):  # (a big zip's numbers: zipfile writes them as zip64)
            big_zip = self.make({"shows/1/inputs/a.json": input_data("a")})
        self.assertEqual(sorted(read_package(big_zip).shows[0].inputs), ["a", "good"])
        with mock.patch.object(clshow, "MAX_ENTRIES", 3):
            self.refused(big_zip, "too many files")
        data = bytearray(big_zip.read_bytes())  # zipfile believes the zip64 record over the end record, so must this
        record = data.rindex(b"PK\x06\x06")
        struct.pack_into("<QQ", data, record + 32, 10 ** 9, 10 ** 12)  # (its count of files, and their list's size)
        big_zip.write_bytes(bytes(data))
        self.refused(big_zip, "too many files")
        with mock.patch.object(clshow, "MAX_TOTAL_BYTES", 5000):
            self.refused(self.make({"shows/1/media/clip.mp4": bytes(6000)}), "unpacks to more than")
        busy = {"id": "E1", "kind": "paint", "shapes": [{"id": f"S{n}", "opacity": {"expr": f"t * {n}"}} for n in range(4)]}
        with mock.patch.object(clshow, "MAX_EXPRESSIONS", 3):  # (each one is compiled to be checked)
            item = read_package(self.make({"shows/1/inputs/busy.json": input_data("busy", busy)})).shows[0]
        self.assertEqual(sorted(item.inputs), ["good"])
        self.assertTrue(any(p.startswith("inputs\\busy.json was left out: it has 4 expressions") for p in item.problems),
                        item.problems)
        with mock.patch.object(clshow, "MAX_MEDIA_BYTES", 100):
            item = read_package(self.make({"shows/1/media/clip.mp4": bytes(200)})).shows[0]
            self.assertEqual(item.files, ())
            self.assertTrue(any("media\\clip.mp4 was left out: it's too big" in p for p in item.problems), item.problems)

    def test_a_list_that_lies_about_a_size_gets_nothing_past(self) -> None:
        for size in (20, 5000):  # (smaller than it is, and bigger)
            path = self.make({"shows/1/inputs/fib.json": input_data("fib")})
            patch_listed_size(path, "shows/1/inputs/fib.json", size)
            item = read_package(path).shows[0]
            self.assertEqual(sorted(item.inputs), ["good"])
            self.assertTrue(any(p.startswith("inputs\\fib.json was left out: it's damaged") for p in item.problems), item.problems)

    def test_a_failed_import_leaves_nothing_behind(self) -> None:
        path = self.make({"shows/1/media/clip.mp4": bytes(range(256)) * 20})
        patch_listed_size(path, "shows/1/media/clip.mp4", 100)  # (it says 100 bytes: it's 5,120)
        package = read_package(path)
        self.assertTrue(package.shows[0].ok)
        with self.assertRaises(PackageError) as caught:
            import_show(package, 0, self.root, name="Morning")
        self.assertIn("damaged", str(caught.exception))
        self.assertEqual(self.folders(self.root), [])

    def test_a_file_that_changes_after_it_was_checked_isnt_imported(self) -> None:
        path = self.make({"shows/1/media/clip.mp4": b"the video that was checked"})
        package = read_package(path)
        write_package(path, {"shows/1/show.json": SHOW_JSON, "shows/1/media/clip.mp4": b"something else entirely!!"})
        with self.assertRaises(PackageError) as caught:
            import_show(package, 0, self.root, name="Morning")
        self.assertIn("changed", str(caught.exception))
        self.assertEqual(self.folders(self.root), [])

    def test_unsafe_expressions_and_files_outside_the_show_leave_an_input_out(self) -> None:
        nasty = {
            "evil": {"id": "E1", "kind": "paint", "opacity": {"expr": "__import__('os').system('calc')"}},
            "sneaky": {"id": "E1", "kind": "paint", "opacity": {"keys": [[0, {"expr": "open('x').read()"}]]}},
            "outside": {"id": "E1", "kind": "image", "file": r"C:\Windows\win.ini"},
            "network": {"id": "E1", "kind": "image", "file": r"\\server\share\candles.jpg"},
            "strokes": {"id": "E1", "kind": "paint", "strokes": "../../paint/0123456789ab.png"},
        }
        files = {f"shows/1/inputs/{input_id}.json": input_data(input_id, element) for input_id, element in nasty.items()}
        files["shows/1/inputs/unknown.json"] = input_data("unknown", {"id": "E1", "kind": "paint", "opacity": {"expr": "brightness_of_moon"}})
        with no_device_paths() as asked:
            item = read_package(self.make(files)).shows[0]
        self.assertEqual(asked, [])
        self.assertEqual(sorted(item.inputs), ["good", "unknown"])  # (a variable it doesn't have is only a note)
        self.assertTrue(any("brightness_of_moon" in n for n in item.notes), item.notes)
        for input_id in nasty:
            self.assertTrue(any(p.startswith(f"inputs\\{input_id}.json was left out: ") for p in item.problems), (input_id, item.problems))
        self.assertTrue(any("expression" in p for p in item.problems if "evil" in p))

    def test_what_the_checks_drop_stays_dropped_and_links_are_only_text(self) -> None:
        element = {"id": "E1", "kind": "image", "file": "media/candles.jpg", "original": r"C:\Pictures\COM3.jpg"}
        links = {"format": FORMAT_LINKS, "version": 1, "copies": {
            "media/candles.jpg": {"original": r"\\.\COM3", "size": 1, "modified": 1},
            "media/room.jpg": {"original": r"\\server\photos\room.jpg", "size": 1, "modified": 1},
            "../outside.jpg": {"original": r"C:\Pictures\x.jpg", "size": 1, "modified": 1}}}
        package_path = self.make({"shows/1/inputs/linked.json": input_data("linked", element, evil="ignored", blur=0.3),
                                  "shows/1/media/links.json": links, "shows/1/media/candles.jpg": b"\xff\xd8"})
        with no_device_paths() as asked:
            package = read_package(package_path)
            item = package.shows[0]
            made = import_show(package, 0, self.root, name="Morning")
        self.assertEqual(asked, [])
        self.assertEqual(item.originals, (r"\\server\photos\room.jpg",))  # (a network path: it waits for an OK too)
        written = json.loads((made / "inputs" / "linked.json").read_text(encoding="utf-8"))
        self.assertNotIn("evil", written)
        self.assertNotIn("original", written["tracks"][0][0])  # (a device's name: dropped as text)
        self.assertEqual(read_stamps(made), {"media/room.jpg": {"original": r"\\server\photos\room.jpg", "size": 1, "modified": 1, "made": 0.0}})

    def test_a_bad_automation_is_switched_off_and_a_bad_show_is_refused(self) -> None:
        odd = {"format": "centralight-automation", "version": 1, "id": "odd", "name": "Odd", "logic": "all",
               "conditions": [{"type": "format_c"}], "actions": [{"do": "master", "level": 0.5}, {"do": "run", "program": "calc"}]}
        everything = {**ONE_SHOW, "kind": "everything", "shows": [
            {"path": "shows/1", "name": "Morning", "id": "morning", "folder": "Morning"},
            {"path": "shows/2", "name": "Broken", "id": "broken", "folder": "Broken"},
            {"path": "shows/3", "name": "Newer", "id": "newer", "folder": "Newer"}]}
        package = read_package(self.make({"shows/1/automations/odd.json": odd, "shows/2/show.json": "not JSON {",
                                          "shows/3/show.json": {**SHOW_JSON, "version": 99},
                                          "globals/bad.json": {**odd, "id": "bad"}}, manifest=everything))
        morning, broken, newer = package.shows
        self.assertFalse(morning.automations["odd"].enabled)
        self.assertEqual([dict(a) for a in morning.automations["odd"].actions], [{"do": "master", "level": 0.5}])
        self.assertTrue(any(p.startswith("automations\\odd.json: ") for p in morning.problems), morning.problems)
        self.assertFalse(package.globals.automations["bad"].enabled)
        self.assertFalse(broken.ok)
        self.assertIn("show.json", broken.refused)
        self.assertIn("newer CentraLight", newer.refused)
        self.assertTrue(any(line.startswith("Broken: ") for line in package.all_problems()))
        for item in (broken, newer):
            with self.assertRaises(PackageError):
                import_show(package, item, self.root, name=item.name)
        self.assertEqual(self.folders(self.root), [])

    def test_files_that_arent_part_of_a_show_are_left_out(self) -> None:
        package = read_package(self.make({"shows/1/readme.txt": b"hello", "shows/1/media/notes.txt": b"x",
                                          "shows/1/media/sub/x.jpg": b"x", "shows/1/inputs/Upper.json": input_data("upper"),
                                          "shows/2/show.json": SHOW_JSON, "extra.bin": b"x", "settings.json": {}}))
        item = package.shows[0]
        self.assertEqual(len(item.notes), 4, item.notes)
        self.assertEqual(len(package.problems), 3, package.problems)  # (shows/2 isn't listed; a show file has no settings)
        made = import_show(package, 0, self.root, name="Morning")
        self.assertEqual(sorted(p.relative_to(made).as_posix() for p in made.rglob("*") if p.is_file()),
                         ["inputs/good.json", "show.json"])


# ---------------------------------------------------------------- the file type (a stand-in registry key)


TEST_PARENT = r"Software\CentraLightTests"


def _key_exists(path: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _key_text(path: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def _delete_test_tree(path: str) -> None:
    if not path.startswith(TEST_PARENT + "\\"):
        raise AssertionError(f"not a test key: {path}")  # (never anywhere else)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(key, len(children)))
                except OSError:
                    break
    except FileNotFoundError:
        return
    for child in children:
        _delete_test_tree(rf"{path}\{child}")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def _delete_parent_if_empty() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_PARENT, 0, winreg.KEY_READ) as key:
            subkeys, values, _changed = winreg.QueryInfoKey(key)
        if subkeys == 0 and values == 0:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_PARENT)
    except OSError:
        pass


class FileTypeTests(unittest.TestCase):
    COMMAND = r'"C:\CentraLight\.venv\Scripts\pythonw.exe" "C:\CentraLight\main.py" "%1"'
    ICON = r"C:\CentraLight\launcher\clshow.ico"

    def setUp(self) -> None:
        self.base = rf"{TEST_PARENT}\Classes-{uuid.uuid4().hex[:12]}"
        self.addCleanup(_delete_parent_if_empty)  # (cleanups run last first: the test's key goes, then its parent)
        self.addCleanup(_delete_test_tree, self.base)
        self.told: list[int] = []
        for patcher in (mock.patch.object(filetype, "BASE", self.base),  # (even a call without base= stays in the test key)
                        mock.patch.object(filetype, "_notify", lambda: self.told.append(1))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_register_then_sync_writes_only_what_differs(self) -> None:
        self.assertEqual(filetype.registered_command(base=self.base), "")
        self.assertTrue(filetype.register(self.COMMAND, self.ICON, base=self.base))
        self.assertEqual(_key_text(rf"{self.base}\.clshow"), "CentraLight.Show")
        self.assertEqual(_key_text(rf"{self.base}\CentraLight.Show"), "CentraLight show")
        self.assertEqual(_key_text(rf"{self.base}\CentraLight.Show\DefaultIcon"), self.ICON)
        self.assertEqual(filetype.registered_command(base=self.base), self.COMMAND)
        self.assertEqual(len(self.told), 1)  # Explorer was told once
        with mock.patch.object(winreg, "SetValueEx", side_effect=AssertionError("nothing should be written")):
            self.assertEqual(filetype.sync(True, self.COMMAND, self.ICON, base=self.base), "")
        self.assertEqual(len(self.told), 1)  # (nothing changed: Explorer isn't bothered)
        moved = self.COMMAND.replace(r"C:\CentraLight", r"D:\Church\CentraLight")  # CentraLight's folder moved
        self.assertEqual(filetype.sync(True, moved, self.ICON, base=self.base), "")
        self.assertEqual(filetype.registered_command(base=self.base), moved)
        self.assertEqual(len(self.told), 2)

    def test_switching_it_off_removes_only_whats_centralights(self) -> None:
        filetype.register(self.COMMAND, self.ICON, base=self.base)
        self.assertEqual(filetype.sync(False, base=self.base), "")
        self.assertFalse(_key_exists(rf"{self.base}\.clshow"))
        self.assertFalse(_key_exists(rf"{self.base}\CentraLight.Show"))
        self.assertEqual(len(self.told), 2)
        self.assertEqual(filetype.sync(False, base=self.base), "")  # (nothing to do)
        self.assertEqual(len(self.told), 2)

        filetype.register(self.COMMAND, self.ICON, base=self.base, notify=False)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{self.base}\.clshow", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Other.Program")  # .clshow given to another program
        self.assertEqual(filetype.registered_command(base=self.base), "")
        self.assertTrue(filetype.unregister(base=self.base, notify=False))
        self.assertEqual(_key_text(rf"{self.base}\.clshow"), "Other.Program")  # left alone
        self.assertFalse(_key_exists(rf"{self.base}\CentraLight.Show"))
        self.assertEqual(len(self.told), 2)  # (notify=False)

    def test_the_command_is_centralights_with_the_file(self) -> None:
        command = filetype.open_command()
        self.assertTrue(command.endswith(' "%1"'), command)
        self.assertEqual(command, f'"{ROOT / "CentraLight.exe"}" "%1"')  # (Python inside it, 8 Nov)
        self.assertNotIn("--minimized", command)
        self.assertEqual(filetype.ICON, ROOT / "launcher" / "clshow.ico")

    def test_the_document_icon_has_the_sizes_windows_uses(self) -> None:
        data = filetype.ICON.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = {struct.unpack_from("<B", data, 6 + 16 * i)[0] or 256 for i in range(count)}
        self.assertLessEqual({16, 24, 32, 48, 256}, sizes)


if __name__ == "__main__":
    unittest.main()
