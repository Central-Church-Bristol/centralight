"""Safe saving: write a temp file, flush it to disk, then swap it in.

A crash or power cut in the middle of a save leaves either the old file or the new
one, never half of each.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_written: dict[str, str] = {}  # file -> SHA-1 of the bytes CentraLight itself last wrote there
_written_lock = threading.Lock()


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def note_written(path: Path, data: bytes) -> None:
    with _written_lock:
        _written[_key(path)] = hashlib.sha1(data).hexdigest()


def written_by_us(path: Path, data: bytes) -> bool:
    """True if ``data`` is exactly what CentraLight itself last wrote to ``path``: its own save, not an outside
    change (comparing the bytes, not the loaded result, which can differ from what's in memory)."""
    with _written_lock:
        digest = _written.get(_key(path))
    return digest is not None and digest == hashlib.sha1(data).hexdigest()


def write_atomic(path: Path, data: bytes, retries: int = 10, retry_delay: float = 0.05) -> None:
    """Replace ``path`` with ``data`` in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        note_written(path, data)  # before it appears, so the file watcher can never see it as someone else's
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Antivirus, Explorer's preview pane or an editor can hold the file for a moment
                if attempt == retries - 1:
                    raise
                time.sleep(retry_delay * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_text_atomic(path: Path, text: str) -> None:
    write_atomic(path, text.encode("utf-8"))


def json_text(data: Any) -> str:
    """The JSON style every CentraLight file uses: 2-space indent, UTF-8, newline at the end."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json_text(data))


def _no_constant(_name: str) -> None:
    return None  # NaN and Infinity aren't JSON: they read as "missing", so the usual defaults and checks apply


def loads_json_bytes(raw: bytes) -> Any:
    """JSON from a file's bytes (a UTF-8 byte-order mark from Notepad is fine; NaN / Infinity read as null)."""
    return json.loads(raw.decode("utf-8-sig"), parse_constant=_no_constant)


def read_json(path: Path) -> Any:
    """Load a JSON file (a UTF-8 byte-order mark from Notepad is fine; NaN / Infinity read as null)."""
    return loads_json_bytes(Path(path).read_bytes())
