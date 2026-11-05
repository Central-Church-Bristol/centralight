"""M14: tick boxes keep a visible border when they're empty (the Infinite one disappeared into the background)."""

from __future__ import annotations

import unittest
from pathlib import Path


def _close(colour, hex_code: str, slack: int = 40) -> bool:
    from PySide6.QtGui import QColor

    wanted = QColor(hex_code)
    return all(abs(a - b) <= slack for a, b in ((colour.red(), wanted.red()), (colour.green(), wanted.green()),
                                                 (colour.blue(), wanted.blue())))


class TickBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def box_picture(self, checked: bool):
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton

        from tests.qt_support import process

        box = QCheckBox("Infinite")
        box.setChecked(checked)
        box.resize(140, 24)
        box.show()
        process(self.app)
        option = QStyleOptionButton()
        box.initStyleOption(option)
        rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, box)
        image = box.grab().toImage()
        box.close()
        box.deleteLater()
        return image, rect

    def test_an_empty_box_has_a_border_you_can_see(self) -> None:
        from centralight.ui import theme

        image, rect = self.box_picture(False)
        self.assertGreaterEqual(rect.width(), 12)
        edge = image.pixelColor(rect.left(), rect.center().y())
        inside = image.pixelColor(rect.center())
        self.assertTrue(_close(edge, theme.TEXT_3) or _close(edge, theme.TEXT_2), edge.name())  # (grey; lighter on hover)
        self.assertTrue(_close(inside, theme.PANEL, 12), inside.name())  # (empty inside)
        self.assertGreater(abs(edge.lightness() - inside.lightness()), 40)

    def test_a_ticked_box_is_filled(self) -> None:
        from centralight.ui import theme

        image, rect = self.box_picture(True)
        corner = image.pixelColor(rect.left() + 2, rect.top() + 2)
        self.assertTrue(_close(corner, theme.ACCENT), corner.name())

    def test_a_greyed_out_part_ticked_box_still_shows(self) -> None:
        """(Review: with the padlock on, a part-ticked group of lights looked empty: a dark dash on a dark box.)"""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton

        from centralight.ui import theme
        from tests.qt_support import process

        box = QCheckBox("Pillars")
        box.setTristate(True)
        box.setCheckState(Qt.CheckState.PartiallyChecked)
        box.setEnabled(False)
        box.resize(140, 24)
        box.show()
        process(self.app)
        option = QStyleOptionButton()
        box.initStyleOption(option)
        rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, box)
        image = box.grab().toImage()
        box.close()
        box.deleteLater()
        corner = image.pixelColor(rect.left() + 2, rect.top() + 2)
        self.assertTrue(_close(corner, theme.TEXT_3), corner.name())

    def test_the_tick_pictures_are_there(self) -> None:
        from PySide6.QtGui import QImageReader

        from centralight.ui import theme

        folder = Path(theme.__file__).parent
        for name in ("check.svg", "check_mixed.svg"):
            self.assertTrue(QImageReader(str(folder / name)).canRead(), name)
            self.assertIn(f'url("{folder.as_posix()}/{name}")', theme.stylesheet())

    def test_ticks_in_lists_have_borders_too(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        from centralight.ui import theme
        from tests.qt_support import process

        view = QListWidget()
        item = QListWidgetItem("Warm welcome")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        view.addItem(item)
        view.resize(200, 60)
        view.show()
        process(self.app)
        row = view.visualItemRect(item)
        image = view.viewport().grab().toImage()
        found = [image.pixelColor(x, row.center().y()) for x in range(row.left(), row.left() + 24)]
        view.close()
        view.deleteLater()
        self.assertTrue(any(_close(colour, theme.TEXT_3) or _close(colour, theme.TEXT_2) for colour in found),
                        [c.name() for c in found])


if __name__ == "__main__":
    unittest.main()
