"""Tests for the app shell's Controller (with fakes). Run with QT_QPA_PLATFORM=offscreen.

Toasts are in test_app_notify.py; tray, single-instance, startup and end-to-end are in test_app_shell.py.
"""
import threading
import unittest
from unittest import mock

from tests.app_fakes import ControllerCase, result, settle, spin  # first: sets QT_QPA_PLATFORM and sys.path

from PySide6.QtCore import QCoreApplication  # noqa: E402
from autohdr.models import (ConversionFinished, ConversionStarted, Problems, Settings,  # noqa: E402
                            StateChanged)


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
        self.assertEqual(self.window.names("set_problems"), [(self.problems,)] * 2)
        self.assertEqual(self.saved, [], "saving is debounced")
        self.assertTrue(spin(lambda: self.saved))
        settle(0.05)
        self.assertEqual(self.saved, [b])

    def test_logging_is_reconfigured_only_when_output_to_log_changes(self):
        c = self.make(Settings(output_to_log=False))
        self.window.settings_changed.emit(Settings(input_dir=r"C:\a", output_to_log=False))
        self.assertEqual(self.logging_calls, [], "an unrelated edit must not close and reopen the log file")
        self.window.settings_changed.emit(Settings(input_dir=r"C:\a", output_to_log=True))
        self.assertEqual(self.logging_calls, [True])
        self.window.settings_changed.emit(Settings(input_dir=r"C:\b", output_to_log=True))
        self.assertEqual(self.logging_calls, [True])
        self.window.settings_changed.emit(Settings(input_dir=r"C:\b", output_to_log=False))
        self.assertEqual(self.logging_calls, [True, False])

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
        self.assertEqual(self.toasts(), [], "a window click needs no toast: the window is in front of the user")

    def test_tray_resume_error_is_also_a_toast_because_the_window_may_be_hidden(self):
        c = self.make()
        self.service.start_error = "That folder doesn't exist."
        self.tray.toggle_requested.emit()
        self.assertEqual(self.window.names("set_running"), [(False, "That folder doesn't exist.")])
        (title, body, error), = self.toasts()
        self.assertTrue(error)
        self.assertIn("That folder doesn't exist.", body)
        self.service.start_error = ""
        self.tray.toggle_requested.emit()          # a successful resume stays quiet
        self.assertEqual(len(self.toasts()), 1)

    def test_tray_resume_error_is_shown_even_with_notifications_off(self):
        c = self.make(Settings(notifications="off"))
        self.service.start_error = "denied"
        self.tray.toggle_requested.emit()
        self.assertEqual(len(self.toasts()), 1, "it answers a click; the notification mode is for conversions")

    def test_retry_is_forwarded(self):
        c = self.make()
        self.window.retry_requested.emit(r"C:\shots\bad.jxr")
        self.assertEqual(self.service.names("retry"), [(r"C:\shots\bad.jxr",)])

    def test_tray_open_shows_the_window(self):
        c = self.make()
        self.tray.show_requested.emit()
        self.assertEqual(self.window.names("show_and_raise"), [()])


class TestResumeWhenFolderIsValid(ControllerCase):
    """Typing a folder is a series of settings edits: the first ones are invalid and must not end watching for good."""

    def edit(self, input_dir, problems=None):
        self.problems = problems or Problems()
        self.window.settings_changed.emit(Settings(input_dir=input_dir))

    def test_folder_typed_while_running_stops_then_starts_again_when_valid(self):
        c = self.make()
        self.window.toggle_requested.emit()
        self.assertEqual(self.service.names("start"), [()])
        self.edit(r"C:\sho", Problems(input="That folder doesn't exist."))
        self.assertEqual(self.service.names("stop"), [()])
        self.assertFalse(self.service.running)
        self.edit(r"C:\shot", Problems(input="That folder doesn't exist."))
        self.assertEqual(self.service.names("start"), [()], "still invalid: nothing to start yet")
        self.edit(r"C:\shots")
        self.assertEqual(self.service.names("start"), [(), ()])
        self.assertTrue(self.service.running)

    def test_settings_edits_do_not_clear_the_want_running_flag(self):
        c = self.make()
        self.window.toggle_requested.emit()
        self.edit(r"C:\sho", Problems(input="gone"))
        self.assertTrue(c._want_running)
        self.edit(r"C:\sh", Problems(tool="no hdrfix"))
        self.assertTrue(c._want_running)
        self.edit(r"C:\shots")
        self.assertTrue(c._want_running)
        self.assertTrue(self.service.running)

    def test_service_that_already_stopped_itself_in_configure_is_also_resumed(self):
        # The real configure() restarts a running watcher on a new folder; the restart fails and leaves it stopped.
        c = self.make()
        self.window.toggle_requested.emit()
        self.service.configure = lambda s: setattr(self.service, "running", False)
        self.edit(r"C:\sho", Problems(input="gone"))
        self.assertEqual(self.service.names("stop"), [])
        self.edit(r"C:\shots")
        self.assertEqual(self.service.names("start"), [(), ()])

    def test_user_pause_is_not_overridden_by_a_later_valid_edit(self):
        c = self.make()
        self.window.toggle_requested.emit()
        self.window.toggle_requested.emit()          # pause
        self.assertFalse(c._want_running)
        self.edit(r"C:\other")
        self.assertEqual(self.service.names("start"), [()])
        self.assertFalse(self.service.running)

    def test_paused_app_is_never_started_by_an_edit(self):
        c = self.make()
        self.edit(r"C:\shots")
        self.assertEqual(self.service.names("start"), [])

    def test_launch_with_auto_start_counts_as_wanting_to_run(self):
        c = self.make(Settings(input_dir=r"C:\shots", auto_start=True))
        c.start_initial()
        self.edit(r"C:\sho", Problems(input="gone"))
        self.edit(r"C:\shots")
        self.assertEqual(self.service.names("start"), [(), ()])

    def test_quit_clears_the_want_running_flag(self):
        c = self.make()
        self.window.toggle_requested.emit()
        with mock.patch.object(QCoreApplication, "quit"):
            c.quit()
        self.assertFalse(c._want_running)

    def test_failed_resume_is_reported_and_does_not_loop(self):
        c = self.make()
        self.window.toggle_requested.emit()
        self.edit(r"C:\sho", Problems(input="gone"))
        self.service.start_error = "Couldn't start watching that folder: denied"
        self.edit(r"C:\shots")
        settle(0.05)
        self.assertEqual(self.service.names("start"), [(), ()], "one attempt per edit, nothing retries by itself")
        self.assertEqual(self.window.names("set_running")[-1], (False, "Couldn't start watching that folder: denied"))
        self.assertEqual(self.toasts(), [], "only tray-initiated starts toast")
        self.service.start_error = ""
        self.edit(r"C:\shots2")                      # the next edit gets another try, and it works
        self.assertEqual(self.service.names("start"), [(), (), ()])
        self.assertTrue(self.service.running)


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


class TestQuit(ControllerCase):
    def quit_order(self, c, trigger):
        with mock.patch.object(QCoreApplication, "quit", side_effect=lambda: self.calls.append(("app.quit",))):
            trigger()

    def test_quit_sequence(self):
        c = self.make()
        self.quit_order(c, c.quit)
        names = [call[0] for call in self.calls]
        self.assertEqual(names, ["flush", "window.hide", "tray.hide", "shutdown", "save", "app.quit"])
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

    def test_tray_quit_flushes_an_edit_the_window_has_not_emitted_yet(self):
        c = self.make()
        late = Settings(input_dir=r"C:\typed-just-before-quit")
        self.window.pending_edit = late                 # typed < 400 ms ago: still in the window's debounce
        self.quit_order(c, self.tray.quit_requested.emit)
        self.assertEqual(self.saved, [late])
        self.assertEqual(self.service.names("configure"), [], "a flush at quit must not reconfigure or restart anything")
        self.assertEqual(self.service.names("start"), [])
        settle(0.05)
        self.assertEqual(self.saved, [late], "and the debounced save must not fire a second time")

    def test_about_to_quit_path_flushes_too(self):
        c = self.make()
        late = Settings(input_dir=r"C:\late")
        self.window.pending_edit = late
        self.quit_order(c, c.quit)                       # what app.aboutToQuit is connected to
        self.assertEqual(self.saved, [late])

    def test_quit_still_saves_and_exits_if_shutdown_raises(self):
        c = self.make()
        self.service.shutdown_error = RuntimeError("stuck")
        with self.assertRaises(RuntimeError):
            self.quit_order(c, c.quit)
        names = [call[0] for call in self.calls]
        self.assertEqual(names[-2:], ["save", "app.quit"])


if __name__ == "__main__":
    unittest.main()
