"""M19 (18 Nov): the Light type window and the New light type window, from the Layout tab, driven as a person would
(the windows' exec is replaced by what the person does in them)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from centralight import light_types
from centralight.light_types import LPC018, Channel, make_type
from centralight.own_types import load_own, save_own
from centralight.show_io import show_to_dict
from centralight.ui.light_type_dialog import LightTypeDialog, LightTypeEditor
from tests.qt_support import process
from tests.test_gui_layout_page import M1WindowTestCase

ACCEPTED = QDialog.DialogCode.Accepted


def library_item(dialog: LightTypeDialog, name: str):
    section = dialog.tree.topLevelItem(dialog.tree.topLevelItemCount() - 1)
    for i in range(section.childCount()):
        maker = section.child(i)
        for j in range(maker.childCount()):
            if maker.child(j).text(0) == name:
                return maker.child(j)
    raise AssertionError(f"{name} isn't in the library")


def section_items(dialog: LightTypeDialog, index: int) -> list[str]:
    section = dialog.tree.topLevelItem(index)
    return [section.child(i).text(0) for i in range(section.childCount()) if not section.child(i).isHidden()]


class LightTypeWindowTests(M1WindowTestCase):
    def open_layout(self):
        window, engine = self.make()
        QTest.mouseClick(window.header.tabs["layout"], Qt.MouseButton.LeftButton)
        process(self.app, 3)
        page = window.layout_page
        self.own = Path(self._tmp.name) / "_Light types"
        page._light_types_dir = lambda: self.own
        return window, engine, page

    def frame(self, engine, window) -> bytes:
        engine.tick()
        window.bridge.poll()
        process(self.app)
        return bytes(engine.fake_output.last)

    def test_the_list_and_its_search(self) -> None:
        _window, _engine, page = self.open_layout()
        dialog = page._type_dialog(["slimpar-101"], current="slimpar9")
        names = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(names, ["In this show", "Yours", "Built in", "Library · Open Fixture Library"])
        self.assertEqual(section_items(dialog, 0), ["Dimmer (1 channel)", "SlimPAR Q12 (9 channels)"])
        self.assertEqual(section_items(dialog, 1), ["None yet"])
        self.assertEqual(dialog.picked().id, "slimpar9")  # (the light's own type, picked)
        self.assertEqual(dialog.channels.rowCount(), 9)
        self.assertEqual((dialog.use_button.text(), dialog.use_button.isEnabled()), ("Use for 1 light", True))
        self.assertEqual(dialog.edit_button.text(), "Edit…")  # (in this show: changed in place)
        dialog.search.setText("slimpar q12")
        q12 = library_item(dialog, "SlimPAR Q12 BT")
        self.assertFalse(q12.isHidden())
        self.assertTrue(q12.parent().isExpanded())
        self.assertTrue(library_item(dialog, "SlimPAR 64").isHidden())
        self.assertEqual(section_items(dialog, 1), [])  # ("None yet" goes while searching)
        dialog.tree.setCurrentItem(q12)
        self.assertFalse(dialog.mode.isHidden())
        self.assertEqual([dialog.mode.itemText(i) for i in range(dialog.mode.count())], ["4-channel", "8-channel"])
        dialog.mode.setCurrentIndex(1)
        self.assertEqual((dialog.picked().mode, dialog.channels.rowCount()), ("8-channel", 8))
        self.assertEqual(dialog.channels.item(7, 1).text(), "Dimmer")
        self.assertEqual(dialog.edit_button.text(), "Edit a copy…")
        dialog.search.setText("")
        self.assertFalse(library_item(dialog, "SlimPAR 64").isHidden())

    def test_with_no_lights_chosen_it_is_for_looking(self) -> None:
        _window, _engine, page = self.open_layout()
        dialog = page._type_dialog([])
        dialog.tree.setCurrentItem(library_item(dialog, "SlimPAR Q12 BT"))
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertTrue(dialog.new_button.isEnabled())

    def test_use_a_library_type_on_the_chosen_lights(self) -> None:
        window, engine, page = self.open_layout()
        for light in ("slimpar-101", "slimpar-102"):
            page._items[light].setSelected(True)

        def pick(dialog: LightTypeDialog) -> int:
            self.assertEqual(dialog.use_button.text(), "Use for 2 lights")
            dialog.tree.setCurrentItem(library_item(dialog, "SlimPAR Q12 BT"))
            dialog.mode.setCurrentIndex(1)
            dialog._use()
            return dialog.result()

        with mock.patch.object(LightTypeDialog, "exec", pick):
            QTest.mouseClick(page.type_button, Qt.MouseButton.LeftButton)
        show = window.document.show
        profiles = {show.fixture(light).profile for light in ("slimpar-101", "slimpar-102")}
        self.assertEqual(len(profiles), 1)
        kind = light_types.require(profiles.pop())
        self.assertEqual((kind.maker, kind.model, kind.mode), ("Chauvet DJ", "SlimPAR Q12 BT", "8-channel"))
        self.assertEqual([t["id"] for t in show_to_dict(show)["light_types"]], [kind.id])  # (the show carries it)
        self.assertEqual(page.table.item(0, 2).text(), kind.name)
        self.assertIn("5-12", [page.table.item(r, 4).text() for r in range(page.table.rowCount())])
        out = self.frame(engine, window)
        self.assertEqual(out[4 + 7], 255)  # (its dimmer, the 8th channel, full)
        window.undo()
        self.assertEqual(window.document.show.fixture("slimpar-101").profile, "slimpar9")

    def test_double_clicking_a_type_opens_the_window_for_that_light(self) -> None:
        _window, _engine, page = self.open_layout()
        asked = []
        with mock.patch.object(type(page), "choose_light_type", lambda self, ids=None: asked.append(ids)):
            column = page.table.column("profile")
            page.table.cellDoubleClicked.emit(3, column)
        self.assertEqual(asked, [[page.table.row_id(3)]])

    def test_a_new_type_is_tried_on_the_lights_then_saved(self) -> None:
        window, engine, page = self.open_layout()
        page._items["slimpar-101"].setSelected(True)
        before = window.document.undo_stack.count()
        seen = {}

        def type_it(editor: LightTypeEditor) -> int:
            self.assertEqual(editor.try_label.text(), "Try on 1 light")
            editor.maker.setText("Test")
            editor.model.setText("Par 6")
            editor.mode.setText("6-channel")
            editor._set_rows([Channel("dimmer"), Channel("red"), Channel("green"), Channel("blue"), Channel("white"),
                              Channel("strobe", "Shutter", 255)], 5)
            self.assertEqual(editor.table.cellWidget(5, 3).currentWidget(), editor.table.cellWidget(5, 3).spin)
            self.assertEqual(editor.table.cellWidget(4, 3).label.text(), "colour")
            editor._try_colour((1.0, 0.0, 0.0))  # (what's typed reaches the light first, then it goes red)
            seen["frame"] = self.frame(engine, window)[4:10]
            seen["steps"] = window.document.undo_stack.count()
            editor._try_colour((1.0, 1.0, 1.0))
            seen["white"] = self.frame(engine, window)[4:10]
            editor._save()
            return editor.result()

        with mock.patch.object(LightTypeEditor, "exec", type_it):
            kind, applied = page._edit_light_type(None, "new", ["slimpar-101"])
        self.assertTrue(applied)
        self.assertEqual(seen["frame"], bytes((255, 255, 0, 0, 0, 255)))
        self.assertEqual(seen["white"], bytes((255, 0, 0, 0, 255, 255)))  # (white: its white LEDs)
        self.assertEqual(seen["steps"], before)  # (no undo step while it's typed)
        self.assertEqual(window.document.undo_stack.count(), before + 1)  # (Save: one)
        self.assertEqual(kind.name, "Test Par 6 (6-channel)")
        self.assertEqual(window.document.show.fixture("slimpar-101").profile, kind.id)
        self.assertEqual([k.id for k in load_own(self.own)], [kind.id])  # (in your light types)
        self.frame(engine, window)
        self.assertFalse(engine.frames.get().testing)  # (the test colour went off with the window)
        window.undo()
        self.assertEqual(window.document.show.fixture("slimpar-101").profile, "slimpar9")

    def test_cancel_puts_the_lights_back(self) -> None:
        window, engine, page = self.open_layout()
        before = window.document.show

        def type_and_cancel(editor: LightTypeEditor) -> int:
            editor.model.setText("Nope")
            editor._send_preview()
            self.assertNotEqual(window.document.show.fixture("slimpar-103").profile, "slimpar9")
            editor.reject()
            return editor.result()

        with mock.patch.object(LightTypeEditor, "exec", type_and_cancel):
            kind, applied = page._edit_light_type(None, "new", ["slimpar-103"])
        self.assertEqual((kind, applied), (None, False))
        self.assertEqual(window.document.show, before)
        self.assertEqual(load_own(self.own), [])

    def test_an_edit_in_place_takes_every_light_of_that_type(self) -> None:
        window, _engine, page = self.open_layout()

        def change_strobe(editor: LightTypeEditor) -> int:
            self.assertEqual(editor.try_label.text(), "Try on 8 lights")
            editor.table.cellWidget(5, 3).spin.setValue(200)  # (the SlimPAR's strobe "normally")
            editor._save()
            return editor.result()

        with mock.patch.object(LightTypeEditor, "exec", change_strobe):
            kind, applied = page._edit_light_type(light_types.SLIMPAR9, "edit", [])
        self.assertTrue(applied)
        slimpars = [fx for fx in window.document.show.fixtures if fx.name.startswith("SlimPAR")]
        self.assertEqual({fx.profile for fx in slimpars}, {kind.id})
        self.assertEqual(kind.default_of("strobe"), 200.0)
        self.assertEqual(kind.model, "SlimPAR Q12 USB")

    def test_add_a_light_asks_its_type_when_none_is_chosen(self) -> None:
        window, _engine, page = self.open_layout()
        page.scene.clearSelection()

        def pick(dialog: LightTypeDialog) -> int:
            self.assertEqual(dialog.use_button.text(), "Add a light")
            dialog._select(LPC018.id, "builtin")
            dialog._use()
            return dialog.result()

        with mock.patch.object(LightTypeDialog, "exec", pick):
            QTest.mouseClick(page.add_button, Qt.MouseButton.LeftButton)
        added = [fx for fx in window.document.show.fixtures if fx.profile == LPC018.id]
        self.assertEqual([(fx.name, fx.address) for fx in added], [("LPC018 1", 77)])
        self.assertEqual(page.selected_ids(), [added[0].id])
        QTest.mouseClick(page.add_button, Qt.MouseButton.LeftButton)  # (now it's chosen: another like it)
        self.assertEqual([fx.name for fx in window.document.show.fixtures if fx.profile == LPC018.id],
                         ["LPC018 1", "LPC018 2"])

    def test_yours_are_listed_and_can_be_taken_off(self) -> None:
        _window, _engine, page = self.open_layout()
        mine = make_type("Mine (3 channels)", [Channel("red"), Channel("green"), Channel("blue")], short="Mine",
                         model="Mine")
        save_own(self.own, mine)
        (self.own / "broken.json").write_text("{nope", encoding="utf-8")  # (left out, not a crash)
        dialog = page._type_dialog([])
        self.assertEqual(section_items(dialog, 1), ["Mine (3 channels)"])
        dialog._select(mine.id, "yours")
        self.assertFalse(dialog.remove_button.isHidden())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._remove()
        self.assertEqual(section_items(dialog, 1), ["None yet"])
        self.assertFalse((self.own / f"{mine.id}.json").exists())

    def test_the_editor_checks_what_is_typed(self) -> None:
        editor = LightTypeEditor(None)
        self.addCleanup(editor.deleteLater)
        self.assertFalse(editor.save_button.isEnabled())
        self.assertIn("model", editor.problem.text())
        editor.model.setText("Bar")
        self.assertTrue(editor.save_button.isEnabled())
        editor._set_rows([Channel("strobe"), Channel("program")], 0)
        self.assertFalse(editor.save_button.isEnabled())
        self.assertIn("gives light", editor.problem.text())
        editor._set_rows([Channel("red"), Channel("green"), Channel("blue")], 2)
        editor._add()
        self.assertEqual([ch.role for ch in editor.channels()], ["red", "green", "blue", "other"])
        editor.table.selectRow(3)
        editor._move(-1)
        self.assertEqual([ch.role for ch in editor.channels()], ["red", "green", "other", "blue"])
        editor._remove()
        self.assertEqual([ch.role for ch in editor.channels()], ["red", "green", "blue"])
        self.assertFalse(editor.try_buttons["Red"].isEnabled())  # (no lights to try it on)
        self.assertEqual(editor.name_label.text(), "Shows as Bar (3 channels)")


if __name__ == "__main__":
    unittest.main()
