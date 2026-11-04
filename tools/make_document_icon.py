"""Make launcher\\clshow.ico: the icon Windows shows for .clshow files (CentraLight show files): a white page with a
folded corner, and the CentraLight logo's three circles on it.

    .venv\\Scripts\\python.exe tools\\make_document_icon.py

The logo is the same one CentraLight.exe's icon has (tools\\build_launcher.py): the logo picture in Assets, or, if
that isn't there, the circles the app draws itself (centralight.ui.icons.draw_logo). Each size is drawn on its own
(16 to 256 pixels), so the small ones stay crisp, and the icon file is written the way build_launcher.py writes the
launcher's. Run it again only if the design changes: the file is kept in the CentraLight folder, and Windows reads it
from there (centralight/filetype.py).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

ICO = ROOT / "launcher" / "clshow.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
PAGE = "#FFFFFF"
EDGE = "#8A94A3"
FOLD = "#DCE2EA"


def logo_picture():
    """The logo picture CentraLight.exe's icon is made from (None if it isn't there: the circles are drawn instead)."""
    from build_launcher import LOGO
    from PySide6.QtGui import QImage

    image = QImage(str(LOGO))
    return None if image.isNull() else image


def document_image(size: int, logo=None):
    """One size of the icon, as a QImage: the page, its folded corner and the logo (logo: the logo picture, or None to
    draw the circles)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    from centralight.ui.icons import draw_logo

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    line = float(max(1, round(size / 64)))
    half = line / 2  # (lines on whole pixels stay sharp)
    left, right = round(size * 0.14) + half, round(size * 0.86) - half
    top, bottom = round(size * 0.03) + half, round(size * 0.97) - half
    fold = round((right - left) * 0.3)

    page = QPainterPath()
    page.moveTo(left, top)
    page.lineTo(right - fold, top)
    page.lineTo(right, top + fold)
    page.lineTo(right, bottom)
    page.lineTo(left, bottom)
    page.closeSubpath()
    pen = QPen(QColor(EDGE), line)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(PAGE))
    painter.drawPath(page)

    corner = QPainterPath()
    corner.moveTo(right - fold, top)
    corner.lineTo(right - fold, top + fold)
    corner.lineTo(right, top + fold)
    corner.closeSubpath()
    painter.setBrush(QColor(FOLD))
    painter.drawPath(corner)

    side = round((right - left) * 0.9)
    x, y = round((left + right - side) / 2), round(top + (bottom - top) * 0.6 - side / 2)
    if logo is not None:  # (scaled smoothly first: drawn straight from 1024 pixels, small sizes would be blocky)
        painter.drawImage(x, y, logo.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    else:  # the circles, on a see-through layer of their own: they mix with each other, not with the white page
        layer = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(0)
        circles = QPainter(layer)
        draw_logo(circles, QRectF(x, y, side, side))
        circles.end()
        painter.drawImage(0, 0, layer)
    painter.end()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def ico_bytes(images: list) -> bytes:
    """An .ico file holding these pictures (32-bit, with a see-through mask), as build_launcher.py writes them."""
    from build_launcher import _dib_image

    blobs = [_dib_image(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = bytearray(), bytearray()
    for image, blob in zip(images, blobs):
        stored = 0 if image.width() >= 256 else image.width()  # (0 means 256)
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        payload += blob
    return header + bytes(entries) + bytes(payload)


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtGui import QGuiApplication

    args = list(sys.argv[1:] if argv is None else argv)
    target = Path(args[args.index("--ico") + 1]) if "--ico" in args else ICO
    _app = QGuiApplication.instance() or QGuiApplication(["make_document_icon", "-platform", "offscreen"])
    logo = logo_picture()
    data = ico_bytes([document_image(size, logo) for size in SIZES])
    if target.is_file() and target.read_bytes() == data:
        print(f"{target} (unchanged)")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
