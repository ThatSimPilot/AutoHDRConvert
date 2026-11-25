"""Tests for autohdr.service: start/stop/restart, configure() and shutdown(). hdrfix and send2trash are always mocked.

Driven through a fake watchdog Observer. See test_service.py for the event flow and queue.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import convert  # noqa: E402
from autohdr.models import ConversionFinished, StateChanged  # noqa: E402
from autohdr.paths import HDRFIX_MISSING  # noqa: E402
from tests.service_support import (LOGGER, FakeObserver, FakeProc, ServiceCase, created, moved,  # noqa: E402
                                   touch)


class RestartAndStopTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.use_fake_observer()

    def test_stop_emits_state_and_is_idempotent(self):
        svc = self.make()
        svc.start()
        svc.stop()
        svc.stop()
        self.assertFalse(svc.running)
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(False, ""), StateChanged(False, "")])
        self.assertTrue(self.observers[0].stopped.wait(5))
        self.assertTrue(self.observers[0].joined.wait(5))

    def test_stop_without_start_is_harmless(self):
        svc = self.make()
        svc.stop()
        self.assertEqual(self.events, [StateChanged(False, "")])
        self.assertEqual(self.observers, [])

    def test_start_twice_restarts_cleanly_with_one_live_observer(self):
        svc = self.make()
        self.assertEqual(svc.start(), "")
        self.assertEqual(svc.start(), "")
        self.assertTrue(svc.running)
        self.assertEqual(len(self.observers), 2)
        self.assertTrue(self.observers[0].stopped.wait(5))
        self.assertFalse(self.observers[1].stopped.is_set())
        self.assertEqual((self.observers[1].path, self.observers[1].recursive), (self.inp, False))

    def test_restart_ignores_events_from_the_old_observer(self):
        svc = self.make()
        svc.start()
        svc.start()
        old, new = self.observers[0].handler, self.observers[1].handler
        src = self.src()
        old.on_created(created(src))  # a last event from the observer that is shutting down
        old.on_moved(moved(src + ".x", src))
        self.settle()
        self.assertEqual(self.runs, [])
        new.on_created(created(src))
        self.wait_finished()
        old.on_created(created(src))  # and again after the new session converted it
        self.settle()
        self.assertEqual(len(self.runs), 1)

    def test_events_from_a_stopped_session_are_ignored(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        svc.stop()
        h.on_created(created(self.src()))
        self.settle()
        self.assertEqual(self.runs, [])

    def test_start_creates_the_output_folder(self):
        svc = self.make()
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(svc.start(), "")
        self.assertTrue(os.path.isdir(self.out))

    def test_start_failures_return_the_reason_and_emit_it(self):
        blocker = touch(os.path.join(os.path.dirname(self.inp), "afile"))
        cases = (
            ("blank input", dict(input_dir=""), "Choose a folder to watch."),
            ("missing input", dict(input_dir=self.inp + "-nope"), "That folder doesn't exist."),
            ("input is a file", dict(input_dir=blocker), "That folder doesn't exist."),
            ("output under a file", dict(output_dir=os.path.join(blocker, "sub")), "That folder can't be created."),
        )
        for label, change, want in cases:
            with self.subTest(label), self.assertLogs(LOGGER, "ERROR"):
                svc = self.make(self.settings(**change))
                self.events.clear()
                self.assertEqual(svc.start(), want)
                self.assertFalse(svc.running)
                self.assertEqual(self.events, [StateChanged(False, want)])
        self.assertEqual(self.observers, [])

    def test_start_fails_when_hdrfix_is_missing(self):
        self.find.return_value = None
        svc = self.make()
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertEqual(svc.start(), HDRFIX_MISSING)
        self.assertEqual(self.events, [StateChanged(False, HDRFIX_MISSING)])
        self.assertEqual(self.observers, [])

    def test_failed_restart_leaves_the_service_stopped(self):
        svc = self.make()
        svc.start()
        with self.assertLogs(LOGGER, "ERROR"):
            svc.configure(self.settings(input_dir=self.inp + "-nope"))  # running + folder changed -> restart -> fails
        self.assertFalse(svc.running)
        self.assertEqual(self.events[-1], StateChanged(False, "That folder doesn't exist."))
        self.assertTrue(self.observers[0].stopped.wait(5))

    def test_observer_that_cannot_start_returns_an_error(self):
        svc = self.make()
        with mock.patch.object(FakeObserver, "start", side_effect=OSError("access denied")), \
                self.assertLogs(LOGGER, "ERROR"):
            err = svc.start()
        self.assertIn("access denied", err)
        self.assertFalse(svc.running)
        self.assertEqual(self.events, [StateChanged(False, err)])

    def test_pasted_quoted_paths_are_accepted(self):
        svc = self.make(self.settings(input_dir=f'  "{self.inp}"  ', output_dir=f'"{self.out}"'))
        self.assertEqual(svc.start(), "")
        self.assertEqual(self.observers[0].path, self.inp)
        self.observers[0].handler.on_created(created(self.src()))
        (r,) = self.wait_finished()
        self.assertEqual(r.dst, os.path.join(self.out, "shot-sdr.jpg"))


class ConfigureTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.use_fake_observer()

    def test_output_dir_and_delete_original_apply_to_the_next_file(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        out2 = os.path.join(os.path.dirname(self.out), "out2")
        h.on_created(created(self.src("a.jxr")))
        self.wait_finished()
        svc.configure(self.settings(output_dir=out2, delete_original=True))
        b = self.src("b.jxr")
        h.on_created(created(b))
        self.wait_finished(2)
        a_res, b_res = self.finished()
        self.assertEqual(os.path.dirname(a_res.dst), self.out)
        self.assertEqual(os.path.dirname(b_res.dst), out2)
        self.assertTrue(os.path.isfile(b_res.dst))
        self.assertEqual((a_res.original_removed, b_res.original_removed), (False, True))
        self.trash.assert_called_once_with(os.path.abspath(b))
        self.assertEqual(len(self.observers), 1)  # no restart for these two

    def test_tone_preset_applies_to_the_next_file_without_a_restart(self):
        svc = self.make()  # starts as natural
        svc.start()
        h = self.observers[0].handler
        expected = [["--tone-map", "aces"], ["--exposure", "0.5"], []]
        for n, (preset, extra) in enumerate(zip(("punchy", "bright", "natural"), expected)):
            svc.configure(self.settings(tone_preset=preset))
            h.on_created(created(self.src(f"{n}.jxr")))
            self.wait_finished(n + 1)
            self.assertEqual(self.runs[n][3:], extra)
        self.assertEqual(len(self.observers), 1)

    def test_blank_output_means_next_to_the_original(self):
        svc = self.make(self.settings(output_dir=""))
        self.assertEqual(svc.start(), "")
        src = self.src()
        self.observers[0].handler.on_created(created(src))
        (r,) = self.wait_finished()
        self.assertEqual(r.dst, os.path.join(self.inp, "shot-sdr.jpg"))
        self.assertFalse(os.path.exists(self.out))

    def test_changed_input_dir_restarts_a_running_watcher(self):
        svc = self.make()
        svc.start()
        new_in = os.path.join(os.path.dirname(self.inp), "in2")
        os.makedirs(new_in)
        svc.configure(self.settings(input_dir=new_in))
        self.assertTrue(svc.running)
        self.assertEqual([o.path for o in self.observers], [self.inp, new_in])
        self.assertTrue(self.observers[0].stopped.wait(5))
        self.assertEqual(self.events[-1], StateChanged(True, ""))
        self.observers[1].handler.on_created(created(self.src(folder=new_in)))
        self.wait_finished()

    def test_same_input_dir_spelled_differently_does_not_restart(self):
        svc = self.make()
        svc.start()
        svc.configure(self.settings(input_dir=self.inp.upper() + "\\"))
        svc.configure(self.settings(input_dir=f'"{self.inp}"'))
        self.assertEqual(len(self.observers), 1)
        self.assertFalse(self.observers[0].stopped.is_set())

    def test_configure_does_not_start_a_stopped_service(self):
        svc = self.make()
        svc.configure(self.settings(input_dir=self.inp))
        svc.start()
        svc.stop()
        n = len(self.observers)
        new_in = os.path.join(os.path.dirname(self.inp), "in2")
        os.makedirs(new_in)
        svc.configure(self.settings(input_dir=new_in))
        self.assertFalse(svc.running)
        self.assertEqual(len(self.observers), n)
        self.assertEqual(svc.start(), "")  # and the new folder is used when it is started
        self.assertEqual(self.observers[-1].path, new_in)

    def test_settings_passed_in_are_copied(self):
        s = self.settings()
        svc = self.make(s)
        s.output_dir = os.path.join(os.path.dirname(self.out), "elsewhere")  # caller mutates its own object later
        svc.start()
        self.observers[0].handler.on_created(created(self.src()))
        (r,) = self.wait_finished()
        self.assertEqual(os.path.dirname(r.dst), self.out)


class ShutdownTests(ServiceCase):
    def test_shutdown_stops_the_watcher_and_joins_the_threads(self):
        self.use_fake_observer()
        svc = self.make()
        svc.start()
        self.observers[0].handler.on_created(created(self.src()))
        self.wait_finished()
        worker = svc._worker
        self.assertTrue(worker.is_alive())
        svc.shutdown(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.observers[0].joined.is_set())
        self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("autohdr-")], [])
        self.assertFalse(svc.running)
        self.assertEqual(self.events[-1].__class__, ConversionFinished)  # shutdown itself emits nothing

    def test_after_shutdown_nothing_starts(self):
        self.use_fake_observer()
        svc = self.make()
        svc.shutdown(1)
        self.assertTrue(svc.start())  # an error message
        svc.retry(self.src())
        self.settle()
        self.assertEqual((self.runs, self.observers, self.events), ([], [], []))

    def test_shutdown_without_ever_starting_is_instant(self):
        svc = self.make()
        t0 = time.perf_counter()
        svc.shutdown(5)
        svc.shutdown(5)
        self.assertLess(time.perf_counter() - t0, 0.5)

    def test_shutdown_lets_a_quick_conversion_finish(self):
        self.use_fake_observer()
        svc = self.make()
        svc.start()
        self.gate.clear()
        src = self.src()
        self.observers[0].handler.on_created(created(src))
        self.assertTrue(self.hdrfix_started.wait(5))
        threading.Timer(0.2, self.gate.set).start()
        svc.shutdown(5)
        (r,) = self.finished()
        self.assertTrue(r.ok)  # not terminated: it finished inside the grace period
        self.assertTrue(os.path.isfile(r.dst))

    def test_shutdown_terminates_a_hanging_hdrfix_after_the_grace_period(self):
        self.use_fake_observer()
        svc = self.make()
        svc.start()
        self.gate.clear()
        src = self.src()
        self.observers[0].handler.on_created(created(src))
        self.assertTrue(self.hdrfix_started.wait(5))
        worker = svc._worker
        t0 = time.perf_counter()
        with self.assertLogs(LOGGER, "ERROR"):
            svc.shutdown(0.3)
        elapsed = time.perf_counter() - t0
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 2.5)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(svc._child)
        (r,) = self.finished()  # the interrupted conversion is still reported, as a failure
        self.assertFalse(r.ok)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(os.listdir(self.out), [])  # no partial jpg
        self.trash.assert_not_called()

    def test_hdrfix_is_not_started_for_a_file_still_settling_when_the_app_closes(self):
        self.use_fake_observer()
        svc = self.make()
        svc.start()
        in_wait, release = threading.Event(), threading.Event()

        def slow_wait(path):  # the worker is stuck here when shutdown() runs its terminate step
            in_wait.set()
            release.wait(10)

        with mock.patch.object(convert, "wait_for_file", slow_wait):
            self.observers[0].handler.on_created(created(self.src()))
            self.assertTrue(in_wait.wait(5))
            worker = svc._worker
            svc.shutdown(0.2)  # no child exists yet, so there is nothing to terminate
            self.assertTrue(worker.is_alive())
            release.set()  # the wait ends after shutdown gave up on the worker
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.runs, [])  # hdrfix never started, so nothing is orphaned
        self.assertEqual(self.events, [StateChanged(True, "")])  # and no dangling "Converting"
        self.assertEqual(os.listdir(self.out), [])

    def test_a_child_that_appears_after_shutdown_began_is_terminated_at_once(self):
        svc = self.make()
        svc.shutdown(1)
        late = FakeProc([], self)
        svc._set_child(late)
        self.assertTrue(late.terminated)
        svc._set_child(None)  # the end-of-conversion call is harmless
        other = self.make()
        fine = FakeProc([], self)
        other._set_child(fine)
        self.assertFalse(fine.terminated)  # a live service leaves its child alone


if __name__ == "__main__":
    unittest.main()
