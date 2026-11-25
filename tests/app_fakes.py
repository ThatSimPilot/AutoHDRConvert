"""Shared helpers for the app-shell tests (test_app*.py): Qt event-loop helpers and fake service/window/tray."""
import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autohdr.controller import Controller  # noqa: E402
from autohdr.models import ConversionFinished, ConversionResult, Problems, Settings  # noqa: E402

qapp = QApplication.instance() or QApplication([])


def spin(cond, timeout=3.0):
    """Run the Qt event loop until cond() is true (or time runs out); returns cond()."""
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


def settle(seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def result(ok=True, src=r"C:\shots\a.jxr", dst=r"C:\out\a-sdr.jpg", message=""):
    if not ok:
        dst = ""
    return ConversionResult(ok, src, dst, message, False, time.time())


class Recorder:
    """Shared by the fakes: records (name, *args) calls in one ordered list, plus the calling thread."""
    def _rec(self, name, *args):
        self.calls.append((name, *args))
        self.threads.append(threading.current_thread())

    def names(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


class FakeWindow(QObject, Recorder):
    settings_changed = Signal(object)
    toggle_requested = Signal()
    retry_requested = Signal(str)
    quit_requested = Signal()
    hidden_to_tray = Signal()

    def __init__(self, calls):
        super().__init__()
        self.calls, self.threads = calls, []
        self.pending_edit = None    # what a half-debounced edit would emit when the controller flushes it

    def set_running(self, running, error=""): self._rec("set_running", running, error)
    def set_converting(self, src): self._rec("set_converting", src)
    def add_activity(self, r): self._rec("add_activity", r)
    def set_problems(self, p): self._rec("set_problems", p)
    def show_and_raise(self): self._rec("show_and_raise")
    def hide(self): self._rec("window.hide")

    def _emit_if_changed(self):
        self._rec("flush")
        edit, self.pending_edit = self.pending_edit, None
        if edit is not None:
            self.settings_changed.emit(edit)


class FakeTray(QObject, Recorder):
    show_requested = Signal()
    toggle_requested = Signal()
    quit_requested = Signal()
    message_clicked = Signal()

    def __init__(self, calls):
        super().__init__()
        self.calls, self.threads = calls, []

    def set_running(self, running): self._rec("tray.set_running", running)
    def notify(self, title, message, error=False): self._rec("notify", title, message, error)
    def hide(self): self._rec("tray.hide")


class FakeService(Recorder):
    def __init__(self, calls):
        self.calls, self.threads = calls, []
        self.running = False
        self.start_error = ""
        self.shutdown_error = None

    def configure(self, s): self._rec("configure", s)
    def retry(self, src): self._rec("retry", src)

    def start(self):
        self._rec("start")
        self.running = not self.start_error
        return self.start_error

    def stop(self):
        self._rec("stop")
        self.running = False

    def shutdown(self):
        self._rec("shutdown")
        if self.shutdown_error:
            raise self.shutdown_error


class ControllerCase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.service = FakeService(self.calls)
        self.window = FakeWindow(self.calls)
        self.tray = FakeTray(self.calls)
        self.problems = Problems()
        self.opened = []
        self.saved = []
        self.logging_calls = []
        self.now = 1000.0

    def make(self, settings=None, open_folder=True):
        self.settings = settings or Settings(input_dir=r"C:\shots")
        c = Controller(
            self.service, self.window, self.tray, self.settings,
            save_settings=lambda s: (self.saved.append(s), self.calls.append(("save", s)))[0],
            setup_logging=lambda flag, log_dir="": self.logging_calls.append(flag),
            validate_settings=lambda s: (self.calls.append(("validate", s)), self.problems)[1],
            open_folder=self.opened.append if open_folder else None,
        )
        c._clock = lambda: self.now
        c.SAVE_DELAY_MS = 10
        self.addCleanup(c.deleteLater)
        return c

    def emit_result(self, c, r):
        c.on_event(ConversionFinished(r))
        settle(0.02)

    def toasts(self):
        return self.tray.names("notify")
