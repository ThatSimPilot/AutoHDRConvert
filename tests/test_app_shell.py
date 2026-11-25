"""Tests for the app shell outside the Controller: Tray, single-instance guard, startup hooks/failures, end-to-end, main().

Run with QT_QPA_PLATFORM=offscreen. Controller tests are in test_app.py and test_app_notify.py.
"""
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

from tests.app_fakes import ROOT, qapp, settle, spin  # first: sets QT_QPA_PLATFORM and sys.path

from PySide6.QtCore import QObject, Signal, qInstallMessageHandler, qWarning  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from autohdr.instance import SingleInstance  # noqa: E402
from autohdr.ui.tray import Tray  # noqa: E402

CHILD = ("import os,sys;os.environ['QT_QPA_PLATFORM']='offscreen';sys.path.insert(0,sys.argv[1]);"
         "from PySide6.QtWidgets import QApplication;from autohdr.instance import SingleInstance;"
         "app=QApplication([]);print(SingleInstance(sys.argv[2]).acquire())")


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
        self.assertEqual(t._tray.toolTip(), "Auto HDR-SDR Converter — Watching")
        self.assertEqual(t._toggle.text(), "Pause")
        t.set_running(False)
        self.assertEqual(t._tray.toolTip(), "Auto HDR-SDR Converter — Paused")
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


class AppModuleCase(unittest.TestCase):
    """autohdr.app imports the whole UI: skipped until every module exists."""

    @classmethod
    def setUpClass(cls):
        try:
            from autohdr import app as app_mod
        except ImportError as e:
            raise unittest.SkipTest(f"autohdr.app not importable yet: {e}")
        cls.app = app_mod


class TestExceptionHooks(AppModuleCase):
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


class TestFatalStartup(AppModuleCase):
    """main() with the Qt pieces mocked (a second real QApplication can't exist in this process)."""

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {"APPDATA": tmp}))
        self.enterContext(mock.patch.object(sys, "stderr", io.StringIO()))
        self.log_dir = os.path.join(tmp, "AutoHDRConvert")
        self.log_file = os.path.join(self.log_dir, "AutoHDRConvert.log")    # "Output to log" is off: nothing logs here
        self.qapplication = self.enterContext(mock.patch.object(self.app, "QApplication"))
        self.box = self.enterContext(mock.patch.object(self.app, "QMessageBox"))
        self.ctypes = self.enterContext(mock.patch.object(self.app, "ctypes"))
        self.guard = self.enterContext(mock.patch.object(self.app, "SingleInstance"))
        self.enterContext(mock.patch.object(self.app, "install_hooks"))
        self.enterContext(mock.patch.object(self.app, "icons"))

    def run_main(self, **failing):
        """main() where theme.apply / build raises the given exception (keyword: theme, build)."""
        theme = mock.Mock(**({"apply.side_effect": failing["theme"]} if "theme" in failing else {}))
        build = mock.Mock(side_effect=failing.get("build"))
        with mock.patch.object(self.app, "theme", theme), mock.patch.object(self.app, "build", build):
            return self.app.main()

    def log_text(self):
        with open(self.log_file, encoding="utf-8") as f:
            return f.read()

    def test_build_failure_is_written_to_the_log_even_with_logging_off_and_shown(self):
        os.makedirs(self.log_dir)
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("earlier run\n")
        self.assertEqual(self.run_main(build=RuntimeError("build boom")), 1)
        text = self.log_text()
        self.assertTrue(text.startswith("earlier run\n"), "appended, not overwritten")
        self.assertIn("Traceback", text)
        self.assertIn("RuntimeError: build boom", text)
        self.box.critical.assert_called_once()
        self.assertIn("build boom", self.box.critical.call_args[0][2])
        self.ctypes.windll.user32.MessageBoxW.assert_not_called()

    def test_theme_failure_is_reported_too_and_creates_the_log_folder(self):
        self.assertFalse(os.path.exists(self.log_dir))
        self.assertEqual(self.run_main(theme=ValueError("theme boom")), 1)
        self.assertIn("ValueError: theme boom", self.log_text())
        self.box.critical.assert_called_once()

    def test_without_a_qapplication_the_native_message_box_is_used(self):
        self.qapplication.instance.return_value = None
        self.qapplication.side_effect = RuntimeError("no platform plugin")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("RuntimeError: no platform plugin", self.log_text())
        self.box.critical.assert_not_called()
        (hwnd, text, title, flags), _ = self.ctypes.windll.user32.MessageBoxW.call_args
        self.assertIn("no platform plugin", text)
        self.assertTrue(flags & 0x10, "error icon")

    def test_unwritable_log_location_does_not_hide_the_message(self):
        blocker = os.path.join(os.environ["APPDATA"], "blocker")
        with open(blocker, "w"):
            pass
        os.environ["APPDATA"] = blocker                  # makedirs(blocker/AutoHDRConvert) fails
        self.assertEqual(self.run_main(build=RuntimeError("still reported")), 1)
        self.box.critical.assert_called_once()

    def test_message_box_failure_falls_back_to_the_native_one_and_never_raises(self):
        self.box.critical.side_effect = RuntimeError("no display")
        self.assertEqual(self.run_main(build=RuntimeError("boom")), 1)
        self.ctypes.windll.user32.MessageBoxW.assert_called_once()
        self.ctypes.windll.user32.MessageBoxW.side_effect = OSError("nope")
        self.assertEqual(self.run_main(build=RuntimeError("boom")), 1)

    def test_second_instance_exits_quietly_without_a_message(self):
        self.guard.return_value.acquire.return_value = False
        self.assertEqual(self.run_main(), 0)
        self.box.critical.assert_not_called()
        self.assertFalse(os.path.exists(self.log_file))


class TestEndToEnd(AppModuleCase):
    """Real core + real window + real controller, offscreen, with a fake hdrfix.bat that copies input to output."""

    def setUp(self):
        import json
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


class TestMain(AppModuleCase):
    """main() in a real child process: theme, icons, single-instance, hooks, the event loop and the quit path."""

    def test_main_starts_shows_the_window_and_exits_with_zero(self):
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
