"""The session log: %LOCALAPPDATA%\\CentraLight\\session.log.

pythonw has no console, so crashes in background threads would vanish. Everything that
goes wrong is written here instead. The same error repeating every tick is only written
once every few seconds, and the file is rotated before it gets big.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path

from .paths import session_log_path

_lock = threading.Lock()
_last_written: dict[str, float] = {}
_LOG_INTERVAL = 5.0
_MAX_BYTES = 512_000


def log_exception(context: str, exc: BaseException | None = None, *, path: Path | None = None) -> None:
    """Append one traceback to the session log. Repeats of the same error are throttled."""
    if exc is None:
        exc = sys.exc_info()[1]
        if exc is None:
            return
    key = f"{context}:{type(exc).__name__}"
    if not _should_write(key):
        return
    try:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:  # e.g. an exception whose own text can't be made
        text = f"{type(exc).__name__} (its details couldn't be written)\n"
    _write(path, f"{context}\n{text}")


def log_info(message: str, *, key: str | None = None, path: Path | None = None) -> None:
    """Append one line to the session log. With a key, repeats are throttled like errors."""
    if key is not None and not _should_write(key):
        return
    _write(path, message + "\n")


def install_crash_logging() -> None:
    """Record uncaught exceptions from any thread (and from Qt slots) in the session log."""
    if getattr(install_crash_logging, "_installed", False):
        return
    install_crash_logging._installed = True  # type: ignore[attr-defined]
    previous_thread_hook = threading.excepthook
    previous_hook = sys.excepthook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        if args.exc_value is not None:
            log_exception(f"thread {name}", args.exc_value)
        try:
            previous_thread_hook(args)
        except Exception:
            return

    def _hook(exc_type, exc_value, exc_tb) -> None:
        if exc_value is not None:
            log_exception("uncaught", exc_value)
        try:
            previous_hook(exc_type, exc_value, exc_tb)
        except Exception:
            return

    threading.excepthook = _thread_hook
    sys.excepthook = _hook


def _should_write(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        if now - _last_written.get(key, -_LOG_INTERVAL) < _LOG_INTERVAL:
            return False
        _last_written[key] = now
        return True


def _write(path: Path | None, text: str) -> None:
    """Never raises: it runs inside error handlers (the engine's, every thread's), where a second error would do harm."""
    try:
        target = path if path is not None else session_log_path()
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size > _MAX_BYTES:
                previous = target.with_name(target.name + ".1")
                if previous.is_file():
                    previous.unlink()
                target.replace(previous)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with target.open("a", encoding="utf-8", errors="backslashreplace") as handle:
                handle.write(f"{stamp} {text}")
    except Exception:  # a full disk, a locked file, a stray character: logging must never break the caller
        return
