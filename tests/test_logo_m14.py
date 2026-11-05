"""M14, 5 Nov: the new logo ("soft light"): three circles, coral on top, lime bottom-left and blue bottom-right, with the
overlaps mixed like light and a white centre. The app draws the same logo itself when the picture is missing."""

from __future__ import annotations

import unittest

# places in a 1024 square, and the colour each has (ARGB): each circle alone, each overlap, the centre, outside
EXPECTED = {
    (512, 100): 0xFFFF6F59,  # coral
    (150, 800): 0xFFD6FF79,  # lime
    (874, 800): 0xFF00B7FF,  # blue
    (360, 470): 0xFFFFFFA8,  # coral + lime
    (665, 470): 0xFFFFD6FF,  # coral + blue
    (512, 800): 0xFFD6FFFF,  # lime + blue
    (512, 560): 0xFFFFFFFF,  # all three
}
OUTSIDE = (5, 5)


class LogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def drawn(self, background: int):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter

        from centralight.ui.icons import draw_logo

        image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
        image.fill(background)
        painter = QPainter(image)
        draw_logo(painter, QRectF(0, 0, 1024, 1024))
        painter.end()
        return image

    def test_the_drawn_logo(self) -> None:
        for background in (0x00000000, 0xFFFFFFFF):  # (see-through, and on a white page: the colours are the same)
            image = self.drawn(background)
            for (x, y), colour in EXPECTED.items():
                self.assertEqual(f"{image.pixel(x, y):08X}", f"{colour:08X}", (x, y, background))
            self.assertEqual(image.pixel(*OUTSIDE), background)

    def test_the_picture_is_the_same_logo(self) -> None:
        from PySide6.QtGui import QImage

        from centralight.paths import logo_path

        picture = QImage(str(logo_path()))
        if picture.isNull():
            self.skipTest("the logo picture isn't there")
        picture = picture.convertToFormat(QImage.Format.Format_ARGB32)
        self.assertEqual((picture.width(), picture.height()), (1024, 1024))
        for (x, y), colour in EXPECTED.items():
            self.assertEqual(f"{picture.pixel(x, y):08X}", f"{colour:08X}", (x, y))
        self.assertEqual(picture.pixel(*OUTSIDE) >> 24, 0)  # (see-through)


if __name__ == "__main__":
    unittest.main()
