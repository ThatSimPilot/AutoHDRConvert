"""Tests for the app shell: Controller (with fakes), single-instance guard, Tray. Run with QT_QPA_PLATFORM=offscreen."""
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QCoreApplication, QObject, Signal, qInstallMessageHandler, qWarning  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from autohdr.controller import Controller  # noqa: E402
from autohdr.instance import SingleInstance  # noqa: E402
from autohdr.models import (ConversionFinished, ConversionResult, ConversionStarted, Problems, Settings,  # noqa: E402
                            StateChanged)
from autohdr.ui.tray import Tray  # noqa: E402

CHILD = ("import os,sys;os.environ['QT_QPA_PLATFORM']='offscreen';sys.path.insert(0,sys.argv[1]);"
         "from PySide6.QtWidgets import QApplication;from autohdr.instance import SingleInstance;"
         "app=QApplication([]);print(SingleInstance(sys.argv[2]).acquire())")
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

    def set_running(self, running, error=""): self._rec("set_running", running, error)
    def set_converting(self, src): self._rec("set_converting", src)
    def add_activity(self, r): self._rec("add_activity", r)
    def set_problems(self, p): self._rec("set_problems", p)
    def show_and_raise(self): self._rec("show_and_raise")
    def hide(self): self._rec("window.hide")


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
            setup_logging=lambda flag: self.logging_calls.append(flag),
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


class TestEventBridge(ControllerCase):
    def test_events_from_a_worker_thread_land_on_the_main_thread(self):
        c = self.make()
        r = result()
        events = [StateChanged(True), ConversionStarted(r.src), ConversionFinished(r), StateChanged(False, "boom")]

        def worker():
            for e in events:
                c.on_event(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(self.calls, [], "nothing may run on the worker thread / before the loop runs")
        self.assertTrue(spin(lambda: len(self.window.names("set_running")) == 2))
        self.assertEqual(self.window.names("set_running"), [(True, ""), (False, "boom")])
        self.assertEqual(self.tray.names("tray.set_running"), [(True,), (False,)])
        self.assertEqual(self.window.names("set_converting"), [(r.src,), (None,)])
        self.assertEqual(self.window.names("add_activity"), [(r,)])
        for who in (self.window, self.tray):
            self.assertEqual({th for th in who.threads}, {threading.main_thread()})

    def test_events_after_quit_are_ignored(self):
        c = self.make()
        with mock.patch.object(QCoreApplication, "quit"):
            c.quit()
        c.on_event(StateChanged(True))
        settle(0.05)
        self.assertEqual(self.window.names("set_running"), [])


class TestSettingsAndActions(ControllerCase):
    def test_settings_change_configures_validates_and_saves_once_debounced(self):
        c = self.make()
        a = Settings(input_dir=r"C:\a", output_to_log=True)
        b = Settings(input_dir=r"C:\b", output_to_log=True)
        self.problems = Problems(output="nope")
        self.window.settings_changed.emit(a)
        self.window.settings_changed.emit(b)
        self.assertEqual(self.service.names("configure"), [(a,), (b,)])
        self.assertEqual(self.logging_calls, [True, True])
        self.assertEqual(self.window.names("set_problems"), [(self.problems,)] * 2)
        self.assertEqual(self.saved, [], "saving is debounced")
        self.assertTrue(spin(lambda: self.saved))
        settle(0.05)
        self.assertEqual(self.saved, [b])

    def test_problem_appearing_while_running_stops_the_service(self):
        c = self.make()
        for problems, should_stop in [(Problems(input="gone"), True), (Problems(tool="no hdrfix"), True),
                                      (Problems(output="read-only"), False), (Problems(), False)]:
            with self.subTest(problems=problems):
                self.calls.clear()
                self.service.running = True
                self.problems = problems
                self.window.settings_changed.emit(Settings())
                self.assertEqual(bool(self.service.names("stop")), should_stop)
                # configure() before validation, stop (if any) after
                if should_stop:
                    self.assertLess(self.calls.index(("configure", Settings())), self.calls.index(("stop",)))

    def test_problem_while_paused_does_not_touch_the_service(self):
        c = self.make()
        self.problems = Problems(input="gone")
        self.window.settings_changed.emit(Settings())
        self.assertEqual(self.service.names("stop"), [])
        self.assertEqual(self.service.names("start"), [])

    def test_failed_restart_inside_configure_keeps_its_error(self):
        # configure() already stopped the service (running False): the controller must not stop it again,
        # because stop() would emit StateChanged(False, "") and wipe the error text.
        c = self.make()
        self.service.running = True
        self.service.configure = lambda s: setattr(self.service, "running", False)
        self.problems = Problems(input="gone")
        self.window.settings_changed.emit(Settings())
        self.assertEqual(self.service.names("stop"), [])

    def test_toggle_from_window_and_tray(self):
        c = self.make()
        self.window.toggle_requested.emit()
        self.assertEqual(self.service.names("start"), [()])
        self.assertTrue(self.service.running)
        self.tray.toggle_requested.emit()
        self.assertEqual(self.service.names("stop"), [()])
        self.assertEqual(self.window.names("set_running"), [], "state changes arrive as service events, not from toggle")

    def test_start_error_is_shown_in_the_window(self):
        c = self.make()
        self.service.start_error = "Can't watch this folder"
        self.window.toggle_requested.emit()
        self.assertEqual(self.window.names("set_running"), [(False, "Can't watch this folder")])
        self.assertFalse(self.service.running)

    def test_retry_is_forwarded(self):
        c = self.make()
        self.window.retry_requested.emit(r"C:\shots\bad.jxr")
        self.assertEqual(self.service.names("retry"), [(r"C:\shots\bad.jxr",)])

    def test_tray_open_shows_the_window(self):
        c = self.make()
        self.tray.show_requested.emit()
        self.assertEqual(self.window.names("show_and_raise"), [()])


class TestStartInitial(ControllerCase):
    def test_auto_start_with_no_problems_starts(self):
        c = self.make(Settings(input_dir=r"C:\shots", auto_start=True))
        c.start_initial()
        self.assertEqual(self.window.names("set_problems"), [(Problems(),)])
        self.assertEqual(self.service.names("start"), [()])

    def test_auto_start_off_does_not_start(self):
        c = self.make(Settings(input_dir=r"C:\shots", auto_start=False))
        c.start_initial()
        self.assertEqual(self.window.names("set_problems"), [(Problems(),)])
        self.assertEqual(self.service.names("start"), [])

    def test_problems_prevent_start_and_are_shown(self):
        self.problems = Problems(input="missing")
        c = self.make(Settings(auto_start=True))
        c.start_initial()
        self.assertEqual(self.window.names("set_problems"), [(self.problems,)])
        self.assertEqual(self.service.names("start"), [])

    def test_start_error_at_launch_is_shown(self):
        self.service.start_error = "denied"
        c = self.make(Settings(input_dir=r"C:\shots", auto_start=True))
        c.start_initial()
        self.assertEqual(self.window.names("set_running"), [(False, "denied")])


class TestNotifications(ControllerCase):
    def test_policy_matrix(self):
        # (mode, kind) -> toast expected?
        cases = {
            ("failures", "ok"): False, ("failures", "failed"): True, ("failures", "ok+note"): False,
            ("all", "ok"): True, ("all", "failed"): True, ("all", "ok+note"): True,
            ("off", "ok"): False, ("off", "failed"): False, ("off", "ok+note"): False,
        }
        for (mode, kind), expected in cases.items():
            with self.subTest(mode=mode, kind=kind):
                self.calls.clear()
                c = self.make(Settings(notifications=mode))
                r = {"ok": result(), "failed": result(ok=False, message="hdrfix failed"),
                     "ok+note": result(message="Converted, but the original was kept")}[kind]
                self.emit_result(c, r)
                self.assertEqual(len(self.toasts()), 1 if expected else 0)

    def test_toast_content(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result(ok=False, src=r"C:\shots\bad.jxr", message="hdrfix exited 1"))
        (title, body, error), = self.toasts()
        self.assertTrue(error)
        self.assertIn("bad.jxr", body)
        self.assertIn("hdrfix exited 1", body)

        self.now += 10
        self.calls.clear()
        self.emit_result(c, result(message="Converted, but the original was kept"))
        (title, body, error), = self.toasts()
        self.assertFalse(error)
        self.assertIn("a.jxr", body)
        self.assertIn("a-sdr.jpg", body)
        self.assertIn("original was kept", body)

    def test_unknown_mode_behaves_like_failures_only(self):
        c = self.make(Settings(notifications="bogus"))
        self.emit_result(c, result())
        self.assertEqual(self.toasts(), [])
        self.emit_result(c, result(ok=False, message="x"))
        self.assertEqual(len(self.toasts()), 1)

    def test_hidden_to_tray_toast_is_shown_once(self):
        c = self.make(Settings(notifications="off"))
        self.window.hidden_to_tray.emit()
        self.window.hidden_to_tray.emit()
        (title, body, error), = self.toasts()
        self.assertEqual(body, "Still running in the system tray")
        self.assertFalse(error)

    def test_burst_of_failures_collapses_into_one_follow_up(self):
        c = self.make()
        for _ in range(4):
            self.emit_result(c, result(ok=False, message="bad"))
            self.now += 0.5
        self.assertEqual(len(self.toasts()), 1, "only the first toast is immediate")
        self.assertTrue(c._toast_timer.isActive())
        self.now += 5
        c._flush_toasts()
        self.assertEqual(len(self.toasts()), 2)
        title, body, error = self.toasts()[1]
        self.assertEqual(body, "3 more conversions failed")
        self.assertTrue(error)

    def test_follow_up_wording_singular_and_mixed(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result())
        self.emit_result(c, result(ok=False, message="bad"))
        c._flush_toasts()
        self.assertEqual(self.toasts()[1][1], "1 more conversion failed")

        self.now += 10
        self.calls.clear()
        self.emit_result(c, result())
        self.emit_result(c, result())
        self.emit_result(c, result(ok=False, message="bad"))
        self.emit_result(c, result())
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual(body, "1 more conversion failed, 2 more converted")
        self.assertTrue(error)

    def test_successes_only_follow_up_is_not_an_error(self):
        c = self.make(Settings(notifications="all"))
        for _ in range(3):
            self.emit_result(c, result())
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual((body, error), ("2 more converted", False))

    def test_follow_up_restarts_the_gap(self):
        c = self.make()
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.now += 6
        c._flush_toasts()
        self.emit_result(c, result(ok=False, message="c"))   # right after the follow-up: pending again
        self.assertEqual(len(self.toasts()), 2)
        self.now += 6
        self.emit_result(c, result(ok=False, message="d"))   # gap has passed but "c" is still pending: stay ordered
        self.assertEqual(len(self.toasts()), 2)
        c._flush_toasts()
        self.assertEqual(self.toasts()[2][1], "2 more conversions failed")

    def test_pending_toasts_are_dropped_if_notifications_get_turned_off(self):
        c = self.make()
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.window.settings_changed.emit(Settings(notifications="off"))
        c._flush_toasts()
        self.assertEqual(len(self.toasts()), 1)

    def test_follow_up_fires_from_the_real_timer(self):
        c = self.make()
        c.TOAST_GAP_S = 0.05
        c._clock = time.monotonic
        c._last_toast = float("-inf")
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.assertTrue(spin(lambda: len(self.toasts()) == 2))
        self.assertEqual(self.toasts()[1][1], "1 more conversion failed")

    def test_clicking_a_toast_opens_its_folder(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result(ok=False, src=r"C:\shots\bad.jxr", message="x"))
        self.tray.message_clicked.emit()
        self.now += 10
        self.emit_result(c, result(src=r"C:\shots\a.jxr", dst=r"C:\out\a-sdr.jpg"))
        self.tray.message_clicked.emit()
        self.assertEqual(self.opened, [r"C:\shots", r"C:\out"])

    def test_clicking_the_tray_hint_opens_nothing_and_missing_opener_is_fine(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result())
        self.window.hidden_to_tray.emit()
        self.tray.message_clicked.emit()
        self.assertEqual(self.opened, [])
        c2 = self.make(open_folder=False)
        c2._toast_folder = r"C:\x"
        self.tray.message_clicked.emit()   # must not raise


class TestQuit(ControllerCase):
    def quit_order(self, c, trigger):
        with mock.patch.object(QCoreApplication, "quit", side_effect=lambda: self.calls.append(("app.quit",))):
            trigger()

    def test_quit_sequence(self):
        c = self.make()
        self.quit_order(c, c.quit)
        names = [call[0] for call in self.calls]
        self.assertEqual(names, ["window.hide", "tray.hide", "shutdown", "save", "app.quit"])
        self.assertLess(names.index("tray.hide"), names.index("app.quit"))

    def test_quit_from_window_and_tray_runs_once(self):
        c = self.make()
        self.quit_order(c, self.window.quit_requested.emit)
        self.quit_order(c, self.tray.quit_requested.emit)
        self.quit_order(c, c.quit)
        self.assertEqual(self.service.names("shutdown"), [()])
        self.assertEqual(len(self.saved), 1)

    def test_quit_saves_latest_settings_and_cancels_the_debounced_save(self):
        c = self.make()
        c.SAVE_DELAY_MS = 50
        s = Settings(input_dir=r"C:\new")
        self.window.settings_changed.emit(s)
        self.quit_order(c, c.quit)
        settle(0.15)
        self.assertEqual(self.saved, [s])

    def test_quit_still_saves_and_exits_if_shutdown_raises(self):
        c = self.make()
        self.service.shutdown_error = RuntimeError("stuck")
        with self.assertRaises(RuntimeError):
            self.quit_order(c, c.quit)
        names = [call[0] for call in self.calls]
        self.assertEqual(names[-2:], ["save", "app.quit"])


class TestTray(unittest.TestCase):
    def tray(self, available, running=True):
        p = mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=available)
        p.start()
        self.addCleanup(p.stop)
        t = Tray(running)
        self.addCleanup(t.deleteLater)
        return t

    def test_unavailable_tray_is_safe(self):
        t = self.tray(False)
        self.assertFalse(t.available())
        t.notify("t", "m")
        t.notify("t", "m", error=True)
        t.set_running(False)
        t.hide()

    def test_tooltip_and_menu_follow_state(self):
        t = self.tray(False, running=True)
        self.assertEqual(t._tray.toolTip(), "Auto HDR-SDR Converter \u2014 Watching")
        self.assertEqual(t._toggle.text(), "Pause")
        t.set_running(False)
        self.assertEqual(t._tray.toolTip(), "Auto HDR-SDR Converter \u2014 Paused")
        self.assertEqual(t._toggle.text(), "Resume")

    def test_menu_actions_emit_signals(self):
        t = self.tray(False)
        got = []
        t.show_requested.connect(lambda: got.append("show"))
        t.toggle_requested.connect(lambda: got.append("toggle"))
        t.quit_requested.connect(lambda: got.append("quit"))
        actions = {a.text(): a for a in t._menu.actions() if a.text()}
        self.assertEqual(set(actions), {"Open", "Pause", "Quit"})
        for text in ("Open", "Pause", "Quit"):
            actions[text].trigger()
        self.assertEqual(got, ["show", "toggle", "quit"])

    def test_single_left_click_opens_and_other_clicks_do_not(self):
        t = self.tray(False)
        got = []
        t.show_requested.connect(lambda: got.append(1))
        reason = QSystemTrayIcon.ActivationReason
        for r in (reason.DoubleClick, reason.Context, reason.MiddleClick):
            t._on_activated(r)
        self.assertEqual(got, [])
        t._on_activated(reason.Trigger)
        self.assertEqual(got, [1])

    def test_notify_uses_critical_icon_for_errors_and_truncates(self):
        t = self.tray(True)
        with mock.patch.object(QSystemTrayIcon, "showMessage") as show:
            t.notify("T" * 100, "m" * 400)
            t.notify("x", "y", error=True)
        (title, msg, kind, _), _ = show.call_args_list[0]
        self.assertEqual((len(title), len(msg)), (60, 250))
        self.assertEqual(kind, QSystemTrayIcon.MessageIcon.Information)
        self.assertEqual(show.call_args_list[1][0][2], QSystemTrayIcon.MessageIcon.Critical)

    def test_clicking_a_toast_is_forwarded(self):
        t = self.tray(False)
        got = []
        t.message_clicked.connect(lambda: got.append(1))
        t._tray.messageClicked.emit()
        self.assertEqual(got, [1])


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self.name = f"AutoHDRTest-{uuid.uuid4().hex}"
        self.guards = []

    def tearDown(self):
        for g in self.guards:
            g.close()
            g.deleteLater()

    def guard(self):
        g = SingleInstance(self.name)
        self.guards.append(g)
        return g

    def second_launch_acquires(self):
        """A real second process (as a user's second double-click). The main thread keeps spinning so the first
        instance can answer; the parent must not block, or the child's ack never comes."""
        with subprocess.Popen([sys.executable, "-c", CHILD, ROOT, self.name], stdout=subprocess.PIPE, text=True) as p:
            self.assertTrue(spin(lambda: p.poll() is not None, timeout=30), "second launch hung")
            out = p.stdout.read()
        return out.strip() == "True"

    def test_second_launch_is_refused_and_first_is_told_to_show(self):
        first = self.guard()
        shown = []
        first.show_requested.connect(lambda: shown.append(1))
        self.assertTrue(first.acquire())
        self.assertFalse(self.second_launch_acquires())
        self.assertTrue(spin(lambda: shown))
        settle(0.05)
        self.assertEqual(shown, [1])

    def test_each_further_launch_raises_the_window_again(self):
        first = self.guard()
        shown = []
        first.show_requested.connect(lambda: shown.append(1))
        self.assertTrue(first.acquire())
        for n in (1, 2):
            self.assertFalse(self.second_launch_acquires())
            self.assertTrue(spin(lambda: len(shown) == n))

    def test_unknown_message_does_not_raise_the_window(self):
        first = self.guard()
        shown = []
        first.show_requested.connect(lambda: shown.append(1))
        self.assertTrue(first.acquire())
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        self.assertTrue(sock.waitForConnected(1000))
        sock.write(b"nope")
        settle(0.2)
        sock.disconnectFromServer()
        self.assertEqual(shown, [])

    def test_name_is_free_again_after_the_first_instance_closes(self):
        first = self.guard()
        self.assertTrue(first.acquire())
        first.close()
        self.assertTrue(self.guard().acquire())

    def test_stale_name_is_removed_and_listen_retried(self):
        real_listen = QLocalServer.listen
        removed = []

        def listen(server, name):    # fails like a leftover socket from a crashed run, until the name is removed
            return real_listen(server, name) if removed else False

        g = self.guard()
        with mock.patch.object(QLocalServer, "listen", listen), \
                mock.patch.object(QLocalServer, "removeServer", removed.append):
            self.assertTrue(g.acquire())
        self.assertEqual(removed, [self.name])
        self.assertFalse(self.second_launch_acquires(), "the recovered server is really serving")

    def test_unusable_name_runs_unguarded_instead_of_refusing_to_start(self):
        g = self.guard()
        with mock.patch.object(QLocalServer, "listen", return_value=False), \
                mock.patch.object(QLocalServer, "removeServer"), \
                self.assertLogs("AutoHDRConvert", "WARNING"):
            self.assertTrue(g.acquire())


class TestExceptionHooks(unittest.TestCase):
    """Lives in app.py, which imports the whole UI: skipped until every module exists."""

    @classmethod
    def setUpClass(cls):
        try:
            from autohdr import app as app_mod
        except ImportError as e:
            raise unittest.SkipTest(f"autohdr.app not importable yet: {e}")
        cls.app = app_mod

    def setUp(self):
        saved = (sys.excepthook, threading.excepthook, self.app._toast, self.app._toasted)
        self.addCleanup(lambda: (setattr(sys, "excepthook", saved[0]), setattr(threading, "excepthook", saved[1]),
                                 setattr(self.app, "_toast", saved[2]), setattr(self.app, "_toasted", saved[3]),
                                 qInstallMessageHandler(None)))
        self.app._toast, self.app._toasted = None, False
        self.app.install_hooks()

    def test_unhandled_exception_is_logged_with_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            info = sys.exc_info()
        with self.assertLogs(self.app.log, "ERROR") as cm:
            sys.excepthook(*info)
        self.assertIsNotNone(cm.records[0].exc_info)

    def test_exception_in_a_qt_slot_reaches_the_log(self):
        class Emitter(QObject):
            ping = Signal()
        e = Emitter()
        e.ping.connect(lambda: 1 / 0)
        with self.assertLogs(self.app.log, "ERROR") as cm:
            e.ping.emit()
        self.assertIn("ZeroDivisionError", cm.output[0] + str(cm.records[0].exc_info))

    def test_exception_in_a_thread_is_logged(self):
        def boom():
            raise RuntimeError("worker died")
        with self.assertLogs(self.app.log, "ERROR") as cm:
            t = threading.Thread(target=boom, name="svc")
            t.start()
            t.join()
        self.assertIn("svc", cm.output[0])
        self.assertIsNotNone(cm.records[0].exc_info)

    def test_qt_messages_are_logged(self):
        with self.assertLogs(self.app.log, "WARNING") as cm:
            qWarning("qt says hi")
        self.assertIn("qt says hi", cm.output[0])

    def test_one_toast_only_and_only_from_the_ui_thread(self):
        toast = mock.Mock()
        self.app._toast = toast
        with self.assertLogs(self.app.log, "ERROR"):
            t = threading.Thread(target=lambda: self.app._excepthook(KeyError, KeyError("k"), None))
            t.start()
            t.join()
            toast.assert_not_called()
            self.app._excepthook(KeyError, KeyError("k"), None)
            self.app._excepthook(KeyError, KeyError("k"), None)
        toast.assert_called_once()
        self.assertTrue(toast.call_args[0][2])


class TestEndToEnd(unittest.TestCase):
    """Real core + real window + real controller, offscreen, with a fake hdrfix.bat that copies input to output."""

    @classmethod
    def setUpClass(cls):
        try:
            from autohdr import app as app_mod
        except ImportError as e:
            raise unittest.SkipTest(f"autohdr.app not importable yet: {e}")
        cls.app = app_mod

    def setUp(self):
        import json
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name
        self.watch = os.path.join(root, "watch")
        self.appdata = os.path.join(root, "AppData")
        os.makedirs(self.watch)
        os.makedirs(os.path.join(self.appdata, "AutoHDRConvert"))
        self.config = os.path.join(self.appdata, "AutoHDRConvert", "config.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"input_dir": self.watch, "output_dir": "", "notifications": "all", "auto_start": True}, f)
        self.flag = os.path.join(root, "fail.flag")
        self.bat = os.path.join(root, "hdrfix.bat")
        with open(self.bat, "w") as f:   # exit 1 while the flag file exists, else copy input to output
            f.write(f'@if exist "{self.flag}" exit /b 1{chr(10)}@copy /Y %1 %2 >nul{chr(10)}')

        from autohdr.logs import setup_logging
        for target in ("autohdr.app.find_hdrfix", "autohdr.service.find_hdrfix"):
            p = mock.patch(target, return_value=self.bat)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"APPDATA": self.appdata})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(setup_logging, False)
        qapp.setQuitOnLastWindowClosed(False)

        self.parts = self.app.build()
        self.addCleanup(self._teardown)
        self.window, self.service, self.controller = self.parts.window, self.parts.service, self.parts.controller
        self.window.show()

    def _teardown(self):
        self.service.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.controller.deleteLater()
        self.parts.tray.deleteLater()

    def drop(self, name="shot.jxr"):
        src = os.path.join(self.watch, name)
        with open(src, "wb") as f:
            f.write(b"fake jxr data")
        return src

    def rows(self):
        return len(self.window._rows)

    def test_launch_converts_a_dropped_file_and_shows_it(self):
        self.controller.start_initial()                      # auto_start from the config, no problems
        self.assertTrue(self.service.running)
        self.assertTrue(spin(lambda: self.window._running))  # StateChanged reached the window via the bridge
        src = self.drop()
        self.assertTrue(spin(lambda: self.rows() == 1, timeout=20), "no activity row appeared")
        row = self.window._rows[0]
        self.assertTrue(row.result.ok, row.result.message)
        self.assertEqual(os.path.basename(row.result.dst), "shot-sdr.jpg")
        with open(os.path.join(self.watch, "shot-sdr.jpg"), "rb") as f:
            self.assertEqual(f.read(), b"fake jxr data")
        self.assertTrue(os.path.exists(src), "the original is kept by default")
        self.assertEqual(self.window.counter_label.text(), "1 converted this session")

    def test_failure_shows_a_row_and_retry_converts(self):
        open(self.flag, "w").close()
        self.controller.start_initial()
        src = self.drop()
        self.assertTrue(spin(lambda: self.rows() == 1, timeout=20))
        self.assertFalse(self.window._rows[0].result.ok)
        self.assertTrue(os.path.exists(src), "a failed conversion keeps the original")
        self.assertFalse(os.path.exists(os.path.join(self.watch, "shot-sdr.jpg")), "no partial output")
        os.remove(self.flag)
        self.window.retry_requested.emit(src)                # what the Retry button emits
        self.assertTrue(spin(lambda: self.window.counter_label.text() == "1 converted this session", timeout=20))
        self.assertTrue(os.path.exists(os.path.join(self.watch, "shot-sdr.jpg")))

    def test_pause_then_resume_from_the_window(self):
        self.controller.start_initial()
        self.assertTrue(spin(lambda: self.window._running))
        self.window.toggle_requested.emit()
        self.assertTrue(spin(lambda: not self.window._running))
        self.assertFalse(self.service.running)
        self.window.toggle_requested.emit()
        self.assertTrue(spin(lambda: self.window._running))

    def test_missing_watch_folder_is_reported_and_not_started(self):
        os.rmdir(self.watch)
        self.controller.start_initial()
        self.assertFalse(self.service.running)
        self.assertTrue(self.window.in_err.text())

    def test_settings_edit_is_saved_and_quit_ends_the_event_loop(self):
        import dataclasses
        import json
        self.controller.start_initial()
        self.window.settings_changed.emit(dataclasses.replace(self.parts.settings, notifications="off"))
        self.assertEqual(self.controller._settings.notifications, "off")
        spin(lambda: False, timeout=0.05)
        self.window.quit_requested.emit()                     # as the Quit button / tray menu does
        self.assertFalse(self.service.running)
        ended = []
        qapp.aboutToQuit.connect(lambda: ended.append(1))
        from PySide6.QtCore import QTimer
        QTimer.singleShot(5000, qapp.quit)                    # safety net: fail instead of hanging
        self.assertEqual(qapp.exec(), 0)
        self.assertEqual(ended, [1])
        with open(self.config, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["notifications"], "off")


class TestMain(unittest.TestCase):
    """main() in a real child process: theme, icons, single-instance, hooks, the event loop and the quit path."""

    def test_main_starts_shows_the_window_and_exits_with_zero(self):
        import tempfile
        try:
            import autohdr.app  # noqa: F401
        except ImportError as e:
            self.skipTest(f"autohdr.app not importable yet: {e}")
        child = chr(10).join([
            "import os, sys, uuid; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; sys.path.insert(0, sys.argv[1])",
            "from PySide6.QtCore import QCoreApplication, QTimer",
            "import autohdr.instance as inst; inst._ID = 'AutoHDRTest-' + uuid.uuid4().hex",
            "from autohdr import app",
            "orig = app.Controller.start_initial",
            "def start(self):",
            "    orig(self); QTimer.singleShot(800, QCoreApplication.quit)",
            "app.Controller.start_initial = start",
            "raise SystemExit(app.main())",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, APPDATA=tmp)
            r = subprocess.run([sys.executable, "-c", child, ROOT], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertTrue(os.path.exists(os.path.join(tmp, "AutoHDRConvert", "config.json")), "settings saved on quit")


if __name__ == "__main__":
    unittest.main()
