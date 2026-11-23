"""Application entry point: builds the real objects, wires them (see controller.py) and runs the Qt event loop."""
import logging
import os
import sys
import threading
from types import SimpleNamespace

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from autohdr import __version__
from autohdr.config import load_settings, save_settings
from autohdr.controller import Controller
from autohdr.instance import SingleInstance
from autohdr.logs import setup_logging
from autohdr.paths import APP_NAME, app_data_dir, find_hdrfix
from autohdr.service import ConversionService, validate_settings
from autohdr.ui import icons, theme
from autohdr.ui.main_window import MainWindow
from autohdr.ui.tray import Tray

log = logging.getLogger(APP_NAME)
_keep = []          # objects that must live as long as the process (single-instance server, window, ...)
_toast = None       # set to tray.notify once the tray exists
_toasted = False    # at most one "unexpected error" toast per run

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO, QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR, QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def open_folder(path: str) -> None:
    try:
        os.startfile(path)
    except Exception as e:
        log.warning(f"Could not open {path}: {e}")


def open_log_folder() -> None:
    os.makedirs(app_data_dir(), exist_ok=True)   # the folder only exists once something was saved or logged
    open_folder(app_data_dir())


def _excepthook(exc_type, exc, tb) -> None:
    """Qt slots, timers and the main thread end up here. The windowed build has no stderr, so log them."""
    global _toasted
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
    if _toast and not _toasted and threading.current_thread() is threading.main_thread():
        _toasted = True   # widgets and the tray only from the UI thread; one toast so a loop can't spam
        try:
            _toast("Unexpected error", f"{exc_type.__name__}: {exc}", True)
        except Exception:
            pass


def _thread_excepthook(args) -> None:
    if args.exc_type is not SystemExit:
        name = args.thread.name if args.thread else "?"
        log.error(f"Unhandled exception in thread {name}", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _qt_message(mode, context, message) -> None:
    log.log(_QT_LEVELS.get(mode, logging.WARNING), f"Qt: {message}")


def install_hooks() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    qInstallMessageHandler(_qt_message)


def build() -> SimpleNamespace:
    """Create and wire the real objects. Needs a QApplication; does not show anything or start watching."""
    settings = load_settings()
    setup_logging(settings.output_to_log)   # first thing after load, so everything below can log
    tray = Tray(running=False)
    window = MainWindow(settings, tray.available())
    window.set_hdrfix_path(find_hdrfix() or "")
    window.set_open_log_folder(open_log_folder)
    # The service needs the controller's callback and the controller needs the service: bind the name late.
    service = ConversionService(lambda event: controller.on_event(event), settings)
    controller = Controller(
        service, window, tray, settings,
        save_settings=save_settings, setup_logging=setup_logging, validate_settings=validate_settings,
        open_folder=open_folder,
    )
    return SimpleNamespace(settings=settings, tray=tray, window=window, service=service, controller=controller)


def main() -> int:
    global _toast
    install_hooks()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)   # the tray keeps the app alive while the window is hidden
    guard = SingleInstance()
    if not guard.acquire():
        return 0                            # the running instance was asked to show itself
    theme.apply(app)
    app.setWindowIcon(icons.app_icon())
    parts = build()
    _keep.extend([guard, parts])
    _toast = parts.tray.notify
    guard.show_requested.connect(parts.window.show_and_raise)
    app.aboutToQuit.connect(parts.controller.quit)   # also covers logoff/shutdown; quit() is idempotent
    parts.window.show_and_raise()
    parts.controller.start_initial()
    return app.exec()
