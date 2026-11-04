"""One copy at a time: a second launch (e.g. double-clicking run.bat while CentraLight is in the
tray) asks the running copy to show its window, then exits. A second launch by double-clicking a .clshow file
(M13) passes the file on: the running copy asks whether to import it."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    name = "CentraLight.Show"
    if os.environ.get("CENTRALIGHT_INSTANCE"):  # tests use their own name
        name += "." + os.environ["CENTRALIGHT_INSTANCE"]
    return name


MAX_MESSAGE = 4096  # bytes: "show", or "open <a .clshow file's path>"


def package_argument(argv: list[str]) -> str:
    """The .clshow file CentraLight was started with (a double-click), or ""."""
    for arg in argv[1:]:
        if isinstance(arg, str) and arg.lower().endswith(".clshow") and not arg.startswith("-"):
            return os.path.abspath(arg) if not arg.startswith(("\\\\", "//")) else arg  # (relative: from where it started)
    return ""


class InstanceServer(QObject):
    """Listens for a second launch; calls on_show when one asks, and on_open(path) when it brings a .clshow file."""

    def __init__(self, on_show: Callable[[], None], name: str | None = None,
                 on_open: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._on_show = on_show
        self._on_open = on_open
        self._buffers: dict[int, bytes] = {}
        self.name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._connected)
        QLocalServer.removeServer(self.name)  # a stale one left by a crash
        self.listening = self._server.listen(self.name)

    def _connected(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._closed(s))
            if socket.bytesAvailable():  # the message can arrive before we start listening for it
                self._read(socket)

    def _closed(self, socket: QLocalSocket) -> None:
        """The other copy has gone: what it sent (even without a line end) still counts."""
        self._read(socket, finished=True)
        self._buffers.pop(id(socket), None)
        socket.deleteLater()

    def _read(self, socket: QLocalSocket, finished: bool = False) -> None:
        key = id(socket)
        data = self._buffers.pop(key, b"") + bytes(socket.readAll())
        if not data:
            return
        if b"\n" not in data and len(data) < MAX_MESSAGE and not finished:
            self._buffers[key] = data  # (the rest is on its way)
            return
        line = data.split(b"\n", 1)[0][:MAX_MESSAGE].strip()
        if line.startswith(b"open ") and self._on_open is not None:
            path = line[5:].decode("utf-8", "replace").strip()
            self._on_show()
            if path.lower().endswith(".clshow"):
                self._on_open(path)
        elif line.startswith(b"show") or line.startswith(b"open "):
            self._on_show()

    def close(self) -> None:
        self._server.close()


def ask_running_copy_to_show(name: str | None = None, timeout_ms: int = 1500, open_file: str = "") -> bool:
    """From a second launch: ask the running copy to show itself (and to offer to import open_file, a .clshow file).
    True if it answered."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    message = b"show\n"
    if open_file:
        message = b"open " + open_file.replace("\n", " ").encode("utf-8")[:MAX_MESSAGE - 8] + b"\n"
    socket.write(message)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True
