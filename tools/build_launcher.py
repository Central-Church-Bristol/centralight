"""Build CentraLight.exe: a small launcher with the CentraLight logo that starts the app without a console window.
Python runs inside it (8 Nov: launcher/Program.cs), so Windows sees CentraLight, not Python.

    .venv\\Scripts\\python.exe tools\\build_launcher.py

It uses the C# compiler that comes with Windows (.NET Framework 4), so nothing needs installing. It's built for 64-bit
Windows, like the Python it runs, with Python's own pythonw.exe manifest (launcher/CentraLight.manifest). The exe has
to stay in the CentraLight folder, next to main.py; put a shortcut to it on the desktop or in the Start menu.
(Adapted from Media Background Pane's launcher.)
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOGO = ROOT / "Assets" / "CentraLight Logo 1024.png"
ICO = ROOT / "launcher" / "icon.ico"
SOURCE = ROOT / "launcher" / "Program.cs"
MANIFEST = ROOT / "launcher" / "CentraLight.manifest"
EXE = ROOT / "CentraLight.exe"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def logo_image():
    """The logo (Assets/CentraLight Logo 1024.png), or the one the app draws itself if that file isn't there."""
    from PySide6.QtGui import QImage, QPainter

    image = QImage(str(LOGO))
    if not image.isNull():
        return image
    from centralight.ui import icons

    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    from PySide6.QtCore import QRectF

    icons.draw_logo(painter, QRectF(0, 0, 256, 256))
    painter.end()
    return image


def _scaled(image, size: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if scaled.width() != size or scaled.height() != size:  # centre a non-square logo
        from PySide6.QtGui import QPainter

        square = QImage(size, size, QImage.Format.Format_ARGB32)
        square.fill(0)
        painter = QPainter(square)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        scaled = square
    return scaled.convertToFormat(QImage.Format.Format_ARGB32)


def _dib_image(image) -> bytes:
    width, height = image.width(), image.height()
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
    xor = bytearray(width * height * 4)
    index = 0
    for y in range(height - 1, -1, -1):
        for x in range(width):
            colour = image.pixelColor(x, y)
            xor[index:index + 4] = bytes((colour.blue(), colour.green(), colour.red(), colour.alpha()))
            index += 4
    stride = ((width + 31) // 32) * 4
    mask = bytearray(stride * height)
    for row, y in enumerate(range(height - 1, -1, -1)):
        base = row * stride
        for x in range(width):
            if image.pixelColor(x, y).alpha() == 0:
                mask[base + (x // 8)] |= 1 << (7 - (x % 8))
    return header + bytes(xor) + bytes(mask)


def write_ico(image, path: Path) -> None:
    blobs = [_dib_image(_scaled(image, size)) for size in ICON_SIZES]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = bytearray(), bytearray()
    for size, blob in zip(ICON_SIZES, blobs):
        stored = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        payload += blob
    data = header + bytes(entries) + bytes(payload)
    if path.is_file() and path.read_bytes() == data:
        return  # unchanged: leave the file (and its date) alone
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def compile_exe(out: Path = EXE, ico: Path = ICO) -> None:
    if not CSC.is_file():
        raise SystemExit(f"The C# compiler that comes with Windows wasn't found: {CSC}")
    command = [str(CSC), "/nologo", "/target:winexe", "/optimize+", "/platform:x64", f"/win32icon:{ico}",
               f"/win32manifest:{MANIFEST}", "/reference:System.Windows.Forms.dll", f"/out:{out}", str(SOURCE)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtGui import QGuiApplication

    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[args.index("--out") + 1]) if "--out" in args else EXE
    ico = Path(args[args.index("--ico") + 1]) if "--ico" in args else ICO  # (the tests build theirs elsewhere)
    _app = QGuiApplication.instance() or QGuiApplication(["build_launcher", "-platform", "offscreen"])
    write_ico(logo_image(), ico)
    compile_exe(out, ico)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
