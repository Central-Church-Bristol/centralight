"""Painted pictures that belong to a show: brush strokes on Paint elements (paint/<code>.png, colour and see-
through, 640x360) and painted masks on any element (masks/<code>.png, grey, 640x360: white shows the element,
black hides it).

Each file is named after its contents (the first 12 characters of a SHA-1), and never overwritten: a new stroke
makes a new file. So undo is exact (it just goes back to the previous file), backups stay small, and an AI or a
person editing the JSON can't break a picture by accident.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .files import write_atomic

PAINT_DIR = "paint"
MASKS_DIR = "masks"
ASSET_W, ASSET_H = 640, 360


def _png(data: np.ndarray) -> bytes:
    import cv2

    ok, encoded = cv2.imencode(".png", data, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise ValueError("the picture couldn't be made into a PNG")
    return encoded.tobytes()


def save_asset(folder: str | Path, kind: str, data: np.ndarray) -> str:
    """Save a painted picture into the show folder; returns its path in the show ("paint/1a2b3c4d5e6f.png").
    data: (360, 640, 4) RGBA uint8 for strokes (kind PAINT_DIR), (360, 640) uint8 for a mask (kind MASKS_DIR)."""
    import cv2

    if kind == PAINT_DIR:
        if data.shape != (ASSET_H, ASSET_W, 4):
            raise ValueError(f"strokes must be {ASSET_W}x{ASSET_H} RGBA")
        encoded = _png(cv2.cvtColor(np.ascontiguousarray(data), cv2.COLOR_RGBA2BGRA))
    elif kind == MASKS_DIR:
        if data.shape != (ASSET_H, ASSET_W):
            raise ValueError(f"a mask must be {ASSET_W}x{ASSET_H} grey")
        encoded = _png(np.ascontiguousarray(data))
    else:
        raise ValueError(f"unknown kind of painted picture {kind!r}")
    code = hashlib.sha1(encoded).hexdigest()[:12]
    relative = f"{kind}/{code}.png"
    target = Path(folder) / kind / f"{code}.png"
    if not target.is_file():  # the same picture again: the file is already there
        target.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(target, encoded)
    return relative


def load_strokes(path: Path) -> np.ndarray | None:
    """A strokes picture as (360, 640, 4) RGBA uint8 (None if it can't be read)."""
    import cv2

    try:
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    if image.shape[:2] != (ASSET_H, ASSET_W):
        image = cv2.resize(image, (ASSET_W, ASSET_H), interpolation=cv2.INTER_AREA)
    return image


def load_mask(path: Path) -> np.ndarray | None:
    """A painted mask as (360, 640) uint8, 255 = show (None if it can't be read)."""
    import cv2

    try:
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    if image.shape[:2] != (ASSET_H, ASSET_W):
        image = cv2.resize(image, (ASSET_W, ASSET_H), interpolation=cv2.INTER_AREA)
    return image
