"""One copy at a time: a second launch (e.g. double-clicking run.bat while CentraLight is in the
tray) asks the running copy to show its window, then exits."""

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


class InstanceServer(QObject):
    """Listens for a second launch; calls on_show when one asks."""

    def __init__(self, on_show: Callable[[], None], name: str | None = None) -> None:
        super().__init__()
        self._on_show = on_show
        self.name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._connected)
        QLocalServer.removeServer(self.name)  # a stale one left by a crash
        self.listening = self._server.listen(self.name)

    def _connected(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():  # the message can arrive before we start listening for it
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if bytes(socket.readAll()).strip().startswith(b"show"):
            self._on_show()

    def close(self) -> None:
        self._server.close()


def ask_running_copy_to_show(name: str | None = None, timeout_ms: int = 1500) -> bool:
    """From a second launch: ask the running copy to show itself. True if it answered."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"show\n")
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True
