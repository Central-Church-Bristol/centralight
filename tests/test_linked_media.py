"""M13: linked pictures and videos, with a local copy per show.

Device names (COM3 is the Eurolite's port) are only ever checked here as plain text: nothing in these tests makes a
file or folder with such a name, or asks the disk about one.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from centralight.link_worker import FileOps, LinkJob, LinkWorker, MAX_STUCK
from centralight.links import (
    AllowedLinks,
    is_network_path,
    link_key,
    original_problem,
    read_stamps,
    safe_media_file,
    stamps_path,
    update_stamps,
)
from centralight.model import Layout, Show
from centralight.show_io import freeze, input_from_dict, layout_from_dict, layout_to_dict


def write_picture(path: Path, colour=(0, 128, 255), size=(32, 18)) -> Path:
    image = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    image[:] = colour
    ok, data = cv2.imencode(path.suffix or ".png", image)
    assert ok
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.tobytes())
    return path


def touch_later(path: Path, seconds: float = 5.0) -> None:
    """Move a file's modified time on (as saving it again would), so a check notices."""
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + int(seconds * 1e9)))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SpyOps(FileOps):
    """Records every original asked about, and the thread that asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def stat(self, path: str):
        self.asked.append((path, threading.current_thread().name))
        return super().stat(path)


class HangingOps(FileOps):
    """An original on a drive that's stopped answering: stat waits until released."""

    def __init__(self, hang_on: str) -> None:
        self.hang_on = hang_on
        self.release = threading.Event()
        self.entered = threading.Event()
        self.threads: list[str] = []

    def stat(self, path: str):
        if link_key(path) == link_key(self.hang_on):
            self.threads.append(threading.current_thread().name)
            self.entered.set()
            self.release.wait(30)
        return super().stat(path)


class TextRuleTests(unittest.TestCase):
    """Whether a link can be used at all, from its text alone."""

    def test_device_names_are_refused(self) -> None:
        for text in (r"C:\Pictures\COM3.jpg", r"C:\COM3\candles.jpg", r"C:\Pictures\aux.png", r"C:\Pictures\Con .jpg",
                     r"C:\Pictures\nul.mp4", r"\\server\COM3\a.jpg", r"C:\x\..\COM3", "C:/Pictures/lpt1.png",
                     r"C:\Pictures\COM¹.jpg"):
            self.assertIn("device", original_problem(text), text)

    def test_other_bad_paths(self) -> None:
        self.assertTrue(original_problem("media/candles.jpg"))  # not a full path
        self.assertTrue(original_problem(r"\\?\C:\a.jpg"))
        self.assertTrue(original_problem(r"\\.\COM3"))
        self.assertTrue(original_problem("C:\\a\nb.jpg"))
        self.assertTrue(original_problem(r"C:\a\b.jpg:stream"))
        self.assertTrue(original_problem(r"C:\a\*.jpg"))
        self.assertTrue(original_problem("C:\\" + "a" * 1200 + ".jpg"))
        self.assertTrue(original_problem(42))
        self.assertTrue(original_problem(r"C:\Pictures\notes.txt", "image"))
        self.assertTrue(original_problem(r"C:\Videos\clip.mp4", "image"))
        self.assertEqual(original_problem(r"C:\Videos\clip.mp4", "video"), "")
        self.assertEqual(original_problem(r"G:\My Drive\Worship\candles.jpg", "image"), "")
        self.assertEqual(original_problem("G:/My Drive/candles.JPG", "image"), "")

    def test_network_paths_are_usable_text_but_marked(self) -> None:
        self.assertEqual(original_problem(r"\\server\share\candles.jpg", "image"), "")
        self.assertTrue(is_network_path(r"\\server\share\candles.jpg"))
        self.assertTrue(is_network_path("//server/share/candles.jpg"))
        self.assertFalse(is_network_path(r"C:\candles.jpg"))

    def test_same_original_however_written(self) -> None:
        self.assertEqual(link_key("G:/Drive/A.JPG"), link_key(r"g:\drive\a.jpg"))

    def test_a_media_file_that_is_a_full_or_network_path_is_never_looked_at(self) -> None:
        """An input file's "file" naming \\\\server\\share\\... (or C:\\...) is refused as text: nothing asks the disk."""
        from pathlib import Path as RealPath
        from unittest import mock

        from centralight.elements import media_path

        real_resolve = RealPath.resolve
        asked: list[str] = []

        def resolve(self, *args, **kwargs):
            if "server" in str(self) or "Pictures" in str(self):
                asked.append(str(self))
            return real_resolve(self, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(RealPath, "resolve", resolve):
            for file in (r"\\server\share\a.jpg", "//server/share/a.jpg", r"C:\Pictures\a.jpg", "C:/Pictures/a.jpg"):
                self.assertIsNone(media_path(folder, file), file)
        self.assertEqual(asked, [])

    def test_copy_names_are_checked_as_text(self) -> None:
        self.assertEqual(safe_media_file("media/candles.mp4"), "media/candles.mp4")
        for bad in ("media/COM3.jpg", "media/aux.png", "../media/a.jpg", "C:/media/a.jpg", "media/sub/a.jpg", "a.jpg", 7):
            self.assertEqual(safe_media_file(bad), "", bad)


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_allowed_links_are_kept_per_pc(self) -> None:
        path = self.root / "allowed.json"
        allowed = AllowedLinks(path)
        self.assertFalse(allowed.allowed(r"G:\Drive\a.jpg"))
        self.assertEqual(allowed.allow([r"G:\Drive\a.jpg", r"C:\COM3\b.jpg"]), [r"G:\Drive\a.jpg"])
        again = AllowedLinks(path)
        self.assertTrue(again.allowed("g:/drive/A.jpg"))
        self.assertFalse(again.allowed(r"C:\COM3\b.jpg"))  # never: a device name

    def test_stamps_leave_out_names_that_could_be_devices(self) -> None:
        folder = self.root / "Show"
        stamps_path(folder).parent.mkdir(parents=True)
        stamp = {"original": r"C:\a.jpg", "size": 1, "modified": 2}
        stamps_path(folder).write_text(json.dumps({"format": "centralight-media-links", "version": 1, "copies": {
            "media/a.jpg": stamp, "media/COM3.jpg": stamp, "../x.jpg": stamp, "C:/x.jpg": stamp}}), encoding="utf-8")
        self.assertEqual(list(read_stamps(folder)), ["media/a.jpg"])

    def test_links_in_show_files(self) -> None:
        inp, warnings = input_from_dict({"id": "x", "name": "X", "tracks": [
            [{"id": "E1", "kind": "image", "file": "media/a.jpg", "original": r"C:\Pictures\COM3.jpg"}],
            [{"id": "E2", "kind": "video", "file": "media/b.mp4", "original": r"G:\Drive\b.mp4"}],
            [{"id": "E3", "kind": "image", "file": "media/c.jpg", "original": "pictures/c.jpg"}]]})
        self.assertNotIn("original", inp.tracks[0][0])
        self.assertEqual(inp.tracks[1][0]["original"], r"G:\Drive\b.mp4")
        self.assertNotIn("original", inp.tracks[2][0])
        self.assertEqual(len([w for w in warnings if "link to an original" in w]), 2)
        layout = Layout("main", "Main", backdrop="media/r.jpg", backdrop_original=r"\\server\share\room.jpg")
        back, warnings = layout_from_dict(layout_to_dict(layout))
        self.assertEqual(back.backdrop_original, r"\\server\share\room.jpg")
        bad, warnings = layout_from_dict({**layout_to_dict(layout), "backdrop_original": r"C:\aux.jpg"})
        self.assertEqual(bad.backdrop_original, "")
        self.assertTrue(warnings)


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.show = self.root / "Show"
        (self.show / "media").mkdir(parents=True)
        self.original = write_picture(self.root / "Drive" / "Candles.png")
        self.workers: list[LinkWorker] = []

    def tearDown(self) -> None:
        for worker in self.workers:
            worker.close()
        self._tmp.cleanup()

    def worker(self, ops=None, clock=None) -> LinkWorker:
        worker = LinkWorker(ops=ops, clock=clock or time.monotonic)
        self.workers.append(worker)
        return worker

    def run_job(self, worker: LinkWorker, job: LinkJob):
        worker.submit(job)
        self.assertTrue(worker.wait_idle())
        results = worker.take_results()
        self.assertEqual(len(results), 1)
        return results[0]

    def job(self, file: str = "", media: str = "image", original: str | None = None) -> LinkJob:
        return LinkJob("check", str(self.show), str(original or self.original), file, media)

    def test_copy_then_unchanged_then_changed(self) -> None:
        opened: list[str] = []

        class Counting(FileOps):
            def open_original(self, path: str):
                opened.append(path)
                return super().open_original(path)

        worker = self.worker(Counting())
        first = self.run_job(worker, self.job())
        self.assertEqual((first.state, first.file), ("copied", "media/Candles.png"))
        self.assertTrue((self.show / "media" / "Candles.png").is_file())
        self.assertEqual(len(opened), 1)
        self.assertEqual(self.run_job(worker, self.job("media/Candles.png")).state, "ok")
        self.assertEqual(len(opened), 1, "an unchanged original isn't read again (only its size and time are looked at)")
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        changed = self.run_job(worker, self.job("media/Candles.png"))
        self.assertEqual((changed.state, changed.file), ("update", "media/Candles 2.png"))
        self.assertTrue((self.show / "media" / "Candles.png").is_file(), "the copy in use is never overwritten")
        self.assertEqual(set(read_stamps(self.show)), {"media/Candles.png", "media/Candles 2.png"})
        self.assertFalse(any((self.show / "media" / ".incoming").iterdir()))

    def test_same_contents_with_a_new_time_is_just_noted(self) -> None:
        worker = self.worker()
        self.run_job(worker, self.job())
        touch_later(self.original)
        result = self.run_job(worker, self.job("media/Candles.png"))
        self.assertEqual(result.state, "ok")
        self.assertFalse((self.show / "media" / "Candles 2.png").exists())
        self.assertEqual(read_stamps(self.show)["media/Candles.png"]["modified"], self.original.stat().st_mtime_ns)

    def test_an_earlier_version_is_used_again(self) -> None:
        worker = self.worker()
        self.run_job(worker, self.job())
        update_stamps(self.show, lambda s: s.__setitem__("media/Candles.png", {**s["media/Candles.png"], "modified": 1}))
        # the copy in use is an older version: a copy of this version exists already, so no new copy is made
        (self.show / "media" / "Old.png").write_bytes(b"old")
        update_stamps(self.show, lambda s: s.__setitem__("media/Candles 9.png", {
            "original": str(self.original), "size": self.original.stat().st_size,
            "modified": self.original.stat().st_mtime_ns}))
        write_picture(self.show / "media" / "Candles 9.png")
        result = self.run_job(worker, self.job("media/Candles.png"))
        self.assertEqual((result.state, result.file), ("update", "media/Candles 9.png"))

    def test_missing_bad_and_refused(self) -> None:
        spy = SpyOps()
        worker = self.worker(spy)
        self.assertEqual(self.run_job(worker, self.job(original=str(self.root / "Drive" / "gone.png"))).state, "missing")
        garbage = self.root / "Drive" / "broken.png"
        garbage.write_bytes(b"not a picture")
        bad = self.run_job(worker, self.job(original=str(garbage)))
        self.assertEqual(bad.state, "bad")
        self.assertFalse(any(p.suffix == ".png" for p in (self.show / "media").iterdir()))
        asked = len(spy.asked)
        for text in (r"C:\Drive\COM3.png", r"C:\COM3\a.png", r"C:\Drive\aux.png", "COM3:"):
            self.assertEqual(self.run_job(worker, self.job(original=text)).state, "refused")
        self.assertEqual(len(spy.asked), asked, "a device's name never reaches the disk")
        self.assertEqual(self.run_job(worker, self.job(original=str(self.original), media="video")).state, "refused")

    def test_a_copy_name_from_the_files_is_checked_first(self) -> None:
        worker = self.worker()
        result = self.run_job(worker, self.job(file="media/COM3.png"))
        self.assertEqual((result.state, result.file), ("copied", "media/Candles.png"))  # (treated as no copy at all)

    def test_a_hung_original_never_holds_up_the_others(self) -> None:
        clock = Clock()
        hanging = HangingOps(str(self.root / "Drive" / "Stuck.png"))
        worker = self.worker(hanging, clock)
        started = time.perf_counter()
        worker.submit(self.job(original=hanging.hang_on))
        worker.submit(self.job())
        self.assertLess(time.perf_counter() - started, 0.05, "asking never waits")
        self.assertTrue(hanging.entered.wait(5))
        self.assertNotEqual(hanging.threads[0], threading.main_thread().name)
        worker.watch()
        self.assertEqual(worker.take_results(), [], "not given up on yet")
        clock.now += 60
        worker.watch()
        results = worker.take_results()
        self.assertEqual([r.state for r in results], ["unreachable"])
        self.assertTrue(worker.wait_idle())
        self.assertEqual([(r.state, r.file) for r in worker.take_results()], [("copied", "media/Candles.png")])
        hanging.release.set()
        time.sleep(0.2)
        self.assertEqual(worker.take_results(), [], "the stuck thread's late answer is ignored")

    def test_too_many_stuck_stops_checking(self) -> None:
        clock = Clock()
        hanging = HangingOps(str(self.root / "Drive" / "Stuck.png"))
        worker = self.worker(hanging, clock)
        try:
            for _ in range(MAX_STUCK):
                hanging.entered.clear()
                worker.submit(self.job(original=hanging.hang_on))
                self.assertTrue(hanging.entered.wait(5))
                clock.now += 60
                worker.watch()
            self.assertTrue(worker.stopped)
            worker.submit(self.job())
            self.assertEqual(worker.pending(), [])
        finally:
            hanging.release.set()

    def test_tidy_removes_only_unused_copies_it_made(self) -> None:
        worker = self.worker()
        self.run_job(worker, self.job())
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        self.run_job(worker, self.job("media/Candles.png"))
        mine = write_picture(self.show / "media" / "My own.png")  # put there by a person: never CentraLight's to tidy
        result = self.run_job(worker, LinkJob("tidy", str(self.show), keep=frozenset({"media/Candles 2.png"})))
        self.assertEqual(result.state, "tidied")
        self.assertFalse((self.show / "media" / "Candles.png").exists())
        self.assertTrue((self.show / "media" / "Candles 2.png").is_file())
        self.assertTrue(mine.is_file())
        self.assertEqual(set(read_stamps(self.show)), {"media/Candles 2.png"})


    def test_tidy_leaves_a_copy_made_after_its_list_was_taken(self) -> None:
        """A check that finishes while a show loads: its new copy isn't on the tidy's list of what's in use yet, but it
        was made after that list was taken, so it stays."""
        worker = self.worker()
        self.run_job(worker, self.job())
        taken = time.time() - 100.0
        update_stamps(self.show, lambda s: s.__setitem__("media/Old.png", {"original": "C:\\x\\Old.png", "size": 1,
                                                                          "modified": 1, "made": taken - 1000.0}))
        write_picture(self.show / "media" / "Old.png")
        self.run_job(worker, LinkJob("tidy", str(self.show), keep=frozenset(), since=taken))
        self.assertTrue((self.show / "media" / "Candles.png").is_file(), "made after the list: left")
        self.assertFalse((self.show / "media" / "Old.png").exists(), "made long before: tidied")

    def test_a_moment_of_lock_never_wipes_the_records(self) -> None:
        worker = self.worker()
        self.run_job(worker, self.job())
        stamps_path(self.show).write_text("{ not json", encoding="utf-8")  # (damaged, or locked for a moment)
        with self.assertRaises(OSError):
            update_stamps(self.show, lambda s: s.__setitem__("media/x.png", {}))
        self.assertEqual(stamps_path(self.show).read_text(encoding="utf-8"), "{ not json", "nothing written over it")

    def test_an_original_that_cant_be_used_isnt_copied_again(self) -> None:
        opened: list[str] = []

        class Counting(FileOps):
            def open_original(self, path: str):
                opened.append(path)
                return super().open_original(path)

        garbage = self.root / "Drive" / "broken.png"
        garbage.write_bytes(b"not a picture")
        worker = self.worker(Counting())
        self.assertEqual(self.run_job(worker, self.job(original=str(garbage))).state, "bad")
        self.assertEqual(self.run_job(worker, self.job(original=str(garbage))).state, "bad")
        self.assertEqual(len(opened), 1, "the same version isn't read again")
        garbage.write_bytes(b"still not a picture, but changed")
        touch_later(garbage)
        self.run_job(worker, self.job(original=str(garbage)))
        self.assertEqual(len(opened), 2, "a new version is tried")

    def test_once_checking_has_stopped_every_ask_still_gets_an_answer(self) -> None:
        worker = self.worker()
        worker.stopped = True
        worker.submit(self.job())
        results = worker.take_results()
        self.assertEqual([r.state for r in results], ["unreachable"])
        self.assertIn("stopped", results[0].detail)


class WindowTests(unittest.TestCase):
    """LinkedMedia with the window: Choose…, Allow, updates that wait for the lights, warnings, tidying."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        from centralight.new_show import create_show
        from tests.gui_fixtures import with_inputs

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.folder = self.root / "Show"
        show = with_inputs(create_show(self.folder))
        self.original = write_picture(self.root / "Drive" / "Sunrise.png")
        inp = show.inputs["warm-white"]
        tracks = list(inp.tracks)
        tracks[1] = freeze([{"id": "E9", "kind": "image", "file": ""}])
        self.show = show.with_inputs({**show.inputs, "warm-white": replace(inp, tracks=tuple(tracks))})
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            window.linked_media.close()
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, show: Show | None = None, ops=None, clock=None):
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        engine = FakeEngine(show or self.show)
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=self.root / "fixture_map.csv")
        linked = window.linked_media
        linked.worker.close()
        linked.worker = LinkWorker(ops=ops, clock=clock or time.monotonic)
        linked.allowed = AllowedLinks(self.root / "allowed.json")
        self.in_use: frozenset[str] = frozenset()
        linked._in_use = lambda: self.in_use
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window, engine

    def settle(self, window) -> None:
        from tests.qt_support import process

        for _ in range(3):
            self.assertTrue(window.linked_media.worker.wait_idle())
            window.linked_media.poll()
            process(self.app, 2)

    def element(self, window, element_id: str = "E9"):
        from centralight.input_edit import find_element

        return find_element(window.document.show.inputs["warm-white"], element_id)[2]

    def open_element(self, window, element_id: str = "E9") -> None:
        from tests.qt_support import process

        window.open_editor("warm-white")
        window.editor_page.select_element(element_id) if hasattr(window.editor_page, "select_element") else \
            window.editor_page.details.set_target("warm-white", element_id)
        process(self.app, 2)

    def file_row(self, window):
        return window.editor_page.details._fields["element:file"]

    def test_choose_links_and_copies_in_one_undo_step(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        steps = window.document.stack("input:warm-white").count()
        window.editor_page.details.use_file(self.original)
        self.assertIn("Copying", self.file_row(window).status.text())
        self.settle(window)
        element = self.element(window)
        self.assertEqual(element["original"], str(self.original))
        self.assertEqual(element["file"], "media/Sunrise.png")
        self.assertTrue((self.folder / "media" / "Sunrise.png").is_file())
        self.assertEqual(window.document.stack("input:warm-white").count(), steps + 1)
        self.assertEqual(self.file_row(window).path.full_text() if hasattr(self.file_row(window).path, "full_text")
                         else self.file_row(window).path.toolTip().splitlines()[0], str(self.original))
        window.document.stack("input:warm-white").undo()
        self.assertNotIn("original", self.element(window))
        self.assertEqual(self.element(window)["file"], "")

    def test_a_written_link_waits_for_an_ok(self) -> None:
        spy = SpyOps()
        show = self._with_link(str(self.original), "")
        window, _engine = self.make(show, ops=spy)
        self.open_element(window)
        self.settle(window)
        self.assertEqual(spy.asked, [], "not looked at before someone here allows it")
        row = self.file_row(window)
        self.assertIn("Not checked yet", row.status.text())
        self.assertEqual(row.action.text(), "Allow")
        row.action.click()
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png")
        self.assertTrue(spy.asked)
        self.assertNotEqual(spy.asked[0][1], threading.main_thread().name)

    def test_network_links_need_the_ok_too(self) -> None:
        spy = SpyOps()
        window, _engine = self.make(self._with_link(r"\\server\share\Sunrise.png", ""), ops=spy)
        self.open_element(window)
        self.settle(window)
        self.assertEqual(spy.asked, [])
        self.assertIn("Not checked yet", self.file_row(window).status.text())

    def test_an_update_waits_until_it_isnt_on_the_lights(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        self.in_use = frozenset({"warm-white"})
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png", "on the lights: it waits")
        row = self.file_row(window)
        self.assertIn("Update ready", row.status.text())
        self.assertEqual(row.action.text(), "Update now")
        self.in_use = frozenset()
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")
        self.assertNotIn("Update ready", row.status.text())

    def test_update_now_uses_it_at_once(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        self.in_use = frozenset({"warm-white"})
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.file_row(window).action.click()
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")

    def test_a_missing_original_warns_and_the_copy_keeps_playing(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        self.original.unlink()
        window.linked_media.check_all()
        self.settle(window)
        row = self.file_row(window)
        self.assertFalse(row.warning.isHidden())
        self.assertIn("Original not found", row.status.text())
        self.assertIn("using its own copy", row.status.text())
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png")
        self.assertTrue((self.folder / "media" / "Sunrise.png").is_file())

    def test_a_hung_original_never_delays_the_window_or_the_engine(self) -> None:
        from tests.qt_support import process

        hanging = HangingOps(str(self.original))
        show = self._with_link(str(self.original), "")
        try:
            window, engine = self.make(show, ops=hanging)
            window.linked_media.allowed.allow([str(self.original)])
            started = time.perf_counter()
            window.linked_media.check_all()
            self.assertTrue(hanging.entered.wait(5))
            for _ in range(5):
                window.linked_media.poll()
                process(self.app)
            self.open_element(window)
            engine.settle(0.5)
            self.assertLess(time.perf_counter() - started, 3.0)
            self.assertIn("Checking", self.file_row(window).status.text())
            self.assertNotIn(threading.main_thread().name, hanging.threads)
        finally:
            hanging.release.set()

    def test_tidying_happens_when_a_show_loads_and_spares_the_history(self) -> None:
        from centralight.input_edit import set_element_value
        from centralight.show_io import save_whole_show

        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")  # (Sunrise.png is in the history now)
        stray = write_picture(self.folder / "media" / "Stray.png")
        update_stamps(self.folder, lambda s: s.__setitem__("media/Stray.png", {"original": r"C:\x\Stray.png", "size": 1, "modified": 1}))
        window.linked_media.check_all()
        self.settle(window)
        self.assertTrue(stray.is_file(), "never while editing")
        update_stamps(self.folder, lambda s: [stamp.__setitem__("made", 0.0) for stamp in s.values()])  # (made long ago)
        save_whole_show(window.document.show)
        window.document.load(window.document.show)
        self.settle(window)
        self.assertFalse(stray.exists())
        self.assertTrue((self.folder / "media" / "Sunrise.png").is_file(), "undo can still bring it back")
        self.assertTrue((self.folder / "media" / "Sunrise 2.png").is_file())
        del set_element_value

    def test_the_room_photo_is_linked_too(self) -> None:
        window, _engine = self.make()
        self.in_use = frozenset({"warm-white"})
        self.assertTrue(window.layout_page.set_room_photo(self.original))
        self.settle(window)
        layout = window.document.show.layout()
        self.assertEqual(layout.backdrop, "media/Sunrise.png")
        self.assertEqual(layout.backdrop_original, str(self.original))
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertEqual(window.document.show.layout().backdrop, "media/Sunrise 2.png", "never on the lights: at once")
        self.original.unlink()
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertFalse(window.layout_page.photo_warning.isHidden())
        self.assertIn("Original not found", window.layout_page.photo_status.text())

    def test_undoing_an_update_sticks(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")
        window.document.stack("input:warm-white").undo()
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png")
        window.linked_media.check_all()
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png", "undone: it isn't made again")
        row = self.file_row(window)
        self.assertEqual(row.action.text(), "Update now", "it's offered instead")
        row.action.click()
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")

    def test_an_automatic_update_isnt_the_dashboards_ctrl_z(self) -> None:
        from centralight.input_edit import update_input

        window, _engine = self.make()
        self.open_element(window)
        window.document.apply(update_input(window.document.show, "worship-blue", name="Worship blue!"), "Rename")
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        window.linked_media.check(str(self.original))
        self.settle(window)
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")
        window.document.undo_newest()  # (the Dashboard's Ctrl+Z: the person's own newest step, not CentraLight's)
        self.assertEqual(self.element(window)["file"], "media/Sunrise 2.png")

    def test_a_ready_update_whose_copy_has_gone_isnt_used(self) -> None:
        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.settle(window)
        write_picture(self.original, colour=(255, 0, 0))
        touch_later(self.original)
        self.in_use = frozenset({"warm-white"})
        window.linked_media.check(str(self.original))
        self.settle(window)
        (self.folder / "media" / "Sunrise 2.png").unlink()  # (as a tidy could have done)
        self.in_use = frozenset()
        window.linked_media.poll()
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png", "never pointed at a file that isn't there")
        self.settle(window)
        self.assertTrue((self.folder / "media" / self.element(window)["file"].split("/", 1)[1]).is_file())

    def test_the_ai_dialog_allows_only_what_it_showed(self) -> None:
        import json

        write_picture(self.folder / "media" / "Sunrise.png")
        window, _engine = self.make()
        network = r"\\203.0.113.5\share\Sunrise.png"
        data = {"format": "centralight-input", "version": 1, "id": "sunrise", "name": "Sunrise",
                "tracks": [[{"id": "E1", "kind": "image", "file": "media/Sunrise.png", "original": network}], [], [], []]}
        dialog = window.open_ai_dialog("", "```json\n" + json.dumps(data) + "\n```")
        self.assertIsNotNone(dialog.candidate)
        self.assertTrue(dialog.use())
        self.assertFalse(window.linked_media.allowed.allowed(network), "never shown, so never allowed")

    def test_a_choose_that_lands_during_a_drag_waits_for_it(self) -> None:
        from centralight.input_edit import update_input

        window, _engine = self.make()
        self.open_element(window)
        window.editor_page.details.use_file(self.original)
        self.assertTrue(window.linked_media.worker.wait_idle())
        window.document.begin_drag()
        window.document.preview(update_input(window.document.show, "warm-white", name="Warm white (dragging)"))
        window.linked_media.poll()
        self.assertEqual(self.element(window)["file"], "", "not during the drag")
        window.document.end_drag("Rename")
        window.linked_media.poll()
        self.assertEqual(self.element(window)["file"], "media/Sunrise.png")
        window.document.stack("input:warm-white").undo()
        self.assertEqual(self.element(window)["file"], "", "its own undo step")

    def test_names_are_compared_as_the_engine_reads_them(self) -> None:
        from centralight.ui.linked_media import files_used

        show = self._with_link(str(self.original), "media\\Sunrise.png")
        self.assertIn("media/Sunrise.png", files_used(show))


    def _with_link(self, original: str, file: str) -> Show:
        inp = self.show.inputs["warm-white"]
        tracks = list(inp.tracks)
        tracks[1] = freeze([{"id": "E9", "kind": "image", "file": file, "original": original}])
        return self.show.with_inputs({**self.show.inputs, "warm-white": replace(inp, tracks=tuple(tracks))})


if __name__ == "__main__":
    unittest.main()
