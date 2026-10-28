"""Keeps one organizer per user: a second launch asks the running one to come to the front."""

from __future__ import annotations

import getpass
import re
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "claude-code-organizer-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


def notify_running_instance() -> bool:
    """Returns True if another instance is running (and was asked to show itself)."""
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if not socket.waitForConnected(500):
        return False
    if sys.platform == "win32":
        # We were just started by the user, so we may let the running copy take the foreground.
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    socket.write(b"activate\n")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


class InstanceServer(QObject):
    activation_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        # A server left behind by a crashed instance (a stale socket file on Linux) would block listen().
        QLocalServer.removeServer(_server_name())
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.listen(_server_name())
        self._server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            self.activation_requested.emit()
