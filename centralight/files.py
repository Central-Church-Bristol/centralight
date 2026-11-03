"""Safe saving: write a temp file, flush it to disk, then swap it in.

A crash or power cut in the middle of a save leaves either the old file or the new
one, never half of each.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


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


def read_json(path: Path) -> Any:
    """Load a JSON file (a UTF-8 byte-order mark from Notepad is fine)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
