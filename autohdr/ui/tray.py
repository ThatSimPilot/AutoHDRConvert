"""System tray icon: state, quick actions and toasts. Safe to construct when Windows has no tray."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

TITLE = "Auto HDR-SDR Converter"


def _icon(running: bool) -> QIcon:
    try:
        from autohdr.ui.icons import tray_icon
        return tray_icon(running)
    except Exception:  # icons not ready / paint failure: the bundled PNG still beats an invisible tray icon
        from autohdr.paths import resource_path
        return QIcon(resource_path("icon.png"))


class Tray(QObject):
    show_requested = Signal()
    toggle_requested = Signal()
    quit_requested = Signal()
    message_clicked = Signal()      # the user clicked the last toast

    def __init__(self, running: bool):
        super().__init__()
        self._available = QSystemTrayIcon.isSystemTrayAvailable()
        self._tray = QSystemTrayIcon(self)
        menu = QMenu()              # no parent widget: we own it, so keep the reference
        menu.addAction("Open").triggered.connect(self.show_requested)
        self._toggle = menu.addAction("")
        self._toggle.triggered.connect(self.toggle_requested)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit_requested)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.messageClicked.connect(self.message_clicked)
        self.set_running(running)
        if self._available:
            self._tray.show()

    def available(self) -> bool:
        return self._available

    def set_running(self, running: bool) -> None:
        self._tray.setIcon(_icon(running))
        self._tray.setToolTip(f"{TITLE} — {'Watching' if running else 'Paused'}")
        self._toggle.setText("Pause" if running else "Resume")

    def notify(self, title: str, message: str, error: bool = False) -> None:
        if not self._available:
            return
        kind = QSystemTrayIcon.MessageIcon.Critical if error else QSystemTrayIcon.MessageIcon.Information
        self._tray.showMessage(title[:60], message[:250], kind, 8000)  # Windows truncates long balloons

    def hide(self) -> None:
        self._tray.hide()           # otherwise Windows can leave a ghost icon until it is hovered

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:   # single left-click, the Windows convention
            self.show_requested.emit()
