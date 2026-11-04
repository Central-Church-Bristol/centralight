"""M13: .clshow files in the window: Export / Export everything / Import (Replace or Keep both), and a double-clicked
file reaching the running copy through the second-launch ping. Device names are checked as text only."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from centralight.new_show import create_show
from centralight.show_io import load_show, save_show_file
from centralight.shows import list_shows


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Shows"
        self.root.mkdir()
        morning = replace(create_show(self.root / "Morning", "Morning"), notes="The morning service")
        save_show_file(morning)
        (Path(morning.folder) / "media").mkdir(exist_ok=True)
        (Path(morning.folder) / "media" / "Candles.png").write_bytes(_png())
        self.morning = load_show(Path(morning.folder))
        self.windows = []

    def tearDown(self) -> None:
        from tests.qt_support import process

        for window in self.windows:
            box = getattr(window, "last_message", None)
            if box is not None:
                box.close()
            window.linked_media.close()
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self):
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.qt_support import process

        engine = FakeEngine(self.morning)
        window = MainWindow(Settings(), self.morning, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv", shows_root=self.root)
        window.resize(1366, 768)
        window.show()
        process(self.app, 3)
        self.windows.append(window)
        return window, engine

    def test_export_then_import_keeps_both(self) -> None:
        from centralight.clshow import read_package

        window, _engine = self.make()
        target = Path(self._tmp.name) / "Morning.clshow"
        report = window.export_package(False, str(target), media=True)
        self.assertIsNotNone(report)
        self.assertTrue(target.is_file())
        package = read_package(target)
        self.assertEqual([item.name for item in package.shows], ["Morning"])
        self.assertTrue(window._import_ticked(package, [("show", 0)], replacing=False))
        names = {info.name: info for info in list_shows(self.root)}
        self.assertIn("Morning (2)", names)
        copy = load_show(Path(names["Morning (2)"].folder))
        self.assertEqual(copy.notes, "The morning service")
        self.assertTrue((Path(copy.folder) / "media" / "Candles.png").is_file())
        self.assertEqual(window.document.show.folder, self.morning.folder, "an imported show isn't loaded")

    def test_export_without_media(self) -> None:
        from centralight.clshow import read_package

        window, _engine = self.make()
        target = Path(self._tmp.name) / "Small.clshow"
        window.export_package(False, str(target), media=False)
        package = read_package(target)
        self.assertFalse(package.media)
        self.assertEqual(package.shows[0].media, ())

    def test_the_import_question(self) -> None:
        from centralight.clshow import read_package
        from centralight.ui.package_dialogs import ImportPackageDialog

        window, _engine = self.make()
        target = Path(self._tmp.name) / "Morning.clshow"
        window.export_package(False, str(target), media=True)
        dialog = ImportPackageDialog(window, read_package(target), self.root, window.document.show.folder)
        try:
            self.assertIn("Import “Morning” as a new show?", [label.text() for label in dialog.findChildren(type(dialog.details))])
            self.assertTrue(dialog.keep_both.isVisibleTo(dialog))  # (the name is taken here)
            self.assertFalse(dialog.replace_choice.isEnabled(), "Morning is the loaded show: it can't be replaced")
            self.assertIn("isn't loaded", dialog.details.text())
        finally:
            dialog.deleteLater()

    def test_export_everything_and_its_ticks(self) -> None:
        from centralight.clshow import read_package
        from centralight.ui.package_dialogs import ImportPackageDialog

        evening = create_show(self.root / "Evening", "Evening")
        window, _engine = self.make()
        target = Path(self._tmp.name) / "Everything.clshow"
        window.export_package(True, str(target), media=True)
        package = read_package(target)
        self.assertEqual(sorted(item.name for item in package.shows), ["Evening", "Morning"])
        dialog = ImportPackageDialog(window, package, self.root, window.document.show.folder)
        try:
            ticked = dialog.ticked()
            self.assertEqual(sorted(kind for kind, _index in ticked if kind == "show"), ["show", "show"])
            self.assertNotIn(("settings", 0), ticked, "the settings start unticked")
        finally:
            dialog.deleteLater()
        del evening

    def test_a_device_name_is_refused_as_text(self) -> None:
        window, _engine = self.make()
        with mock.patch("centralight.ui.main_window.read_package") as reading, \
                mock.patch.object(window, "_message") as told:
            self.assertFalse(window.ask_import_package(r"C:\Users\Me\COM3.clshow"))
            self.assertFalse(window.ask_import_package(r"C:\COM3\Morning.clshow"))
            self.assertFalse(window.ask_import_package(r"\\.\COM3\Morning.clshow"))
            self.assertFalse(window.ask_import_package(r"C:\Users\Me\notes.txt"))
            self.assertFalse(window.ask_import_package("Morning.clshow" if False else r"relative\Morning.clshow"))
        reading.assert_not_called()
        self.assertEqual(told.call_count, 5)
        self.assertTrue(all(call.kwargs.get("warn") for call in told.call_args_list))

    def test_replace_chosen_in_the_dialog_is_kept_after_it_closes(self) -> None:
        from PySide6.QtWidgets import QDialog

        from centralight.clshow import read_package
        from centralight.ui.package_dialogs import ImportPackageDialog

        evening = create_show(self.root / "Evening", "Evening")
        window, _engine = self.make()
        from centralight.clshow import export_show

        target = Path(self._tmp.name) / "Evening.clshow"
        export_show(evening.folder, target)
        dialog = ImportPackageDialog(window, read_package(target), self.root, window.document.show.folder)
        try:
            dialog.show()
            self.assertTrue(dialog.replace_choice.isEnabled(), "Evening isn't the loaded show: it can be replaced")
            dialog.replace_choice.setChecked(True)
            dialog.done(QDialog.DialogCode.Accepted)
            self.assertFalse(dialog.isVisible())
            self.assertTrue(dialog.replacing, "the choice still answers once the dialog has closed")
        finally:
            dialog.deleteLater()

    def test_names_from_a_file_are_never_read_as_html(self) -> None:
        """A show named like an HTML picture: shown as plain text (Qt would read the picture from the disk)."""
        import json
        import zipfile

        from PySide6.QtCore import Qt

        from centralight.clshow import read_package
        from centralight.ui.package_dialogs import ImportPackageDialog

        window, _engine = self.make()
        target = Path(self._tmp.name) / "Html.clshow"
        window.export_package(False, str(target), media=False)
        with zipfile.ZipFile(target) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        data = json.loads(files["shows/1/show.json"])
        data["name"] = '<img src="probe.png">'
        files["shows/1/show.json"] = json.dumps(data).encode()
        hostile = Path(self._tmp.name) / "Hostile.clshow"
        with zipfile.ZipFile(hostile, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw, compress_type=zipfile.ZIP_DEFLATED if name.endswith(".json") else zipfile.ZIP_STORED)
        package = read_package(hostile)
        self.assertNotIn("<", package.shows[0].name)
        dialog = ImportPackageDialog(window, package, self.root, window.document.show.folder)
        try:
            for label in dialog.findChildren(type(dialog.details)):
                if label.text():
                    self.assertNotEqual(label.textFormat(), Qt.TextFormat.AutoText, label.text())
        finally:
            dialog.deleteLater()


class DoubleClickTests(unittest.TestCase):
    """A second launch with a .clshow file asks the running copy to offer it for import."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def test_the_file_reaches_the_running_copy(self) -> None:
        from centralight.ui.instance import InstanceServer, ask_running_copy_to_show, package_argument

        name = f"CentraLight.Tests.Open.{os.getpid()}"
        shown: list[bool] = []
        opened: list[str] = []
        server = InstanceServer(lambda: shown.append(True), name=name, on_open=opened.append)
        try:
            self.assertTrue(server.listening)
            path = r"C:\Users\Me\Documents\Sunday Morning.clshow"  # (only ever text here: nothing opens it)
            self.assertEqual(package_argument(["main.py", path]), path)
            self.assertEqual(package_argument(["main.py", "--minimized"]), "")
            self.assertEqual(self._second_launch(name, path), 0)  # "a second launch": its own program, like Windows'
            self._wait(lambda: opened)
            self.assertEqual(opened, [path])
            self.assertTrue(shown)
            self.assertEqual(self._second_launch(name, ""), 0)  # (a plain second launch still just shows the window)
            self._wait(lambda: len(shown) >= 2)
            self.assertEqual(len(shown), 2)
            self.assertEqual(opened, [path])
            self.assertFalse(ask_running_copy_to_show(name + ".nobody", timeout_ms=200))
        finally:
            server.close()

    def _second_launch(self, name: str, path: str) -> int:
        import subprocess
        import sys

        from tests.qt_support import process

        root = Path(__file__).resolve().parents[1]
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
            "from centralight.ui.instance import ask_running_copy_to_show;"
            "sys.exit(0 if ask_running_copy_to_show(sys.argv[2], open_file=sys.argv[3]) else 1)"
        )
        launch = subprocess.Popen([sys.executable, "-c", script, str(root), name, path])
        deadline = time.monotonic() + 10
        while launch.poll() is None and time.monotonic() < deadline:
            process(self.app)
            time.sleep(0.01)
        return launch.wait(5)

    def _wait(self, done, seconds: float = 5.0) -> None:
        from tests.qt_support import process

        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:
            process(self.app)
            time.sleep(0.01)


def _png() -> bytes:
    import cv2
    import numpy as np

    return cv2.imencode(".png", np.zeros((18, 32, 3), dtype=np.uint8))[1].tobytes()


if __name__ == "__main__":
    unittest.main()
