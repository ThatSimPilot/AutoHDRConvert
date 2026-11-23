"""Single-instance guard. The first process listens on a local socket; a later launch pings it with "show" and exits."""
import ctypes
import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_ID = "AutoHDRConvert-3f6c1d2e-8a4b-4e57-9c0a-b1d5e7f20a64"
TIMEOUT_MS = 1000
log = logging.getLogger("AutoHDRConvert")


def _default_name() -> str:
    # Windows pipe names are machine-wide, so add the user: two signed-in users must not collide.
    return f"{_ID}-{os.environ.get('USERNAME', '')}"


class SingleInstance(QObject):
    show_requested = Signal()   # emitted in the first instance when another launch pings it

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self._name = name or _default_name()
        self._server = QLocalServer(self)   # must outlive acquire(): keep this object alive for the whole run
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def acquire(self) -> bool:
        """True: we are the first instance. False: another one is running and has been told to show itself."""
        # Ping first: on Windows listen() succeeds even when another server already owns the pipe name.
        if self._ping():
            return False
        QLocalServer.removeServer(self._name)  # nobody answered: a crashed run may have left the name behind (Unix)
        if not self._server.listen(self._name):
            log.warning("Single-instance guard unavailable (%s); running unguarded", self._server.errorString())
        return True   # shortcut: an unusable name must not stop the app; two launches in the same millisecond can both win

    def close(self) -> None:
        self._server.close()

    def _ping(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if not sock.waitForConnected(TIMEOUT_MS):
            return False
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: let the first instance take focus
        except Exception:
            pass
        sock.write(b"show")
        sock.waitForBytesWritten(TIMEOUT_MS)
        sock.waitForReadyRead(TIMEOUT_MS)   # the ack: hanging up before the server has read can lose the message
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            sock.waitForDisconnected(TIMEOUT_MS)
        return True

    def _on_connection(self) -> None:
        while (sock := self._server.nextPendingConnection()) is not None:
            sock.disconnected.connect(sock.deleteLater)
            sock.readyRead.connect(lambda s=sock: self._read(s))
            self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        if bytes(sock.readAll()) == b"show":
            sock.write(b"ok")
            self.show_requested.emit()
