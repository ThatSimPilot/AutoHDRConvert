"""Tests for autohdr.service's watcher: the real watchdog Observer, the health check and the lost-events rescan.

hdrfix and send2trash are mocked. The real-Observer tests each pay ~1 s for watchdog to wind down on Windows; the rest
use a fake Observer (and a real temp folder) so they stay deterministic.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watchdog.events import EventsLostEvent  # noqa: E402

from autohdr.models import ConversionStarted, StateChanged  # noqa: E402
from tests.service_support import LOGGER, ServiceCase, created, touch, wait_for  # noqa: E402

REMOVED = "The watch folder was removed"
DIED = "Watching stopped unexpectedly"


def health_threads():
    return [t for t in threading.enumerate() if t.name == "autohdr-health" and t.is_alive()]


class RealWatchdogTests(ServiceCase):
    """The real Observer on a temp folder."""

    def test_real_events_convert_jxr_files_any_case_and_renames_once_each_even_across_a_restart(self):
        svc = self.make()
        self.assertEqual(svc.start(), "")
        self.assertEqual(svc.start(), "")  # restart: the old observer is still winding down, the new one is live
        shot = self.src("Shot.JXR")
        touch(os.path.join(self.inp, "ignore.png"))
        touch(os.path.join(self.inp, "part.tmp"), b"jxr-data")
        os.rename(os.path.join(self.inp, "part.tmp"), os.path.join(self.inp, "renamed.jxr"))  # renamed into place
        self.wait_finished(2, timeout=10)
        self.settle(0.6)  # time for any duplicate or stale-observer event to (wrongly) trigger another conversion
        results = self.finished()
        self.assertEqual(sorted(os.path.basename(r.src) for r in results), ["Shot.JXR", "renamed.jxr"])
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(sorted(os.listdir(self.out)), ["Shot-sdr.jpg", "renamed-sdr.jpg"])
        order = [type(e) for e in self.events if not isinstance(e, StateChanged)]
        self.assertEqual(order.count(ConversionStarted), 2)
        for i, e in enumerate(self.events):  # every Finished is preceded by its own Started
            if type(e).__name__ == "ConversionFinished":
                self.assertIn(ConversionStarted(e.result.src), self.events[:i])
        self.assertTrue(os.path.exists(shot))
        self.assertEqual(self.events[0], StateChanged(True, ""))
        self.assertFalse([e for e in self.events if isinstance(e, StateChanged) and not e.running])

    def test_real_stop_does_not_block_the_caller_and_shutdown_joins_everything(self):
        svc = self.make()
        self.assertEqual(svc.start(), "")
        t0 = time.perf_counter()
        svc.stop()
        self.assertLess(time.perf_counter() - t0, 0.3)
        self.assertFalse(svc.running)
        svc.start()
        svc.shutdown(5)
        self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("autohdr-")], [])
        self.assertFalse(any(t.is_alive() for t in svc._retiring))

    def test_a_healthy_real_watcher_is_not_reported_as_dead(self):
        self.fast_health_checks(0.05)
        svc = self.make()
        self.assertEqual(svc.start(), "")
        self.settle(0.5)  # ten health checks against the real Observer and its emitter
        self.assertTrue(svc.running)
        self.assertEqual(self.events, [StateChanged(True, "")])
        self.assertEqual(len(health_threads()), 1)

    def test_a_removed_watch_folder_is_reported_instead_of_saying_watching(self):
        self.fast_health_checks(0.1)
        svc = self.make()
        self.assertEqual(svc.start(), "")
        os.rmdir(self.inp)  # watchdog's emitter stops itself; its observer thread keeps running
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertTrue(wait_for(lambda: StateChanged(False, REMOVED) in self.events, 10), f"{self.events}")
        self.assertFalse(svc.running)
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(False, REMOVED)])
        self.assertTrue(wait_for(lambda: not health_threads()))
        os.makedirs(self.inp)  # the user fixes the folder and presses Resume
        self.assertEqual(svc.start(), "")
        self.assertTrue(svc.running)
        self.settle(0.4)
        self.assertEqual(self.events[-1], StateChanged(True, ""))


class HealthCheckTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.use_fake_observer()
        self.fast_health_checks(0.05)

    def reported(self, reason):
        return StateChanged(False, reason) in self.events

    def test_a_healthy_watcher_stays_quiet(self):
        svc = self.make()
        svc.start()
        self.settle(0.4)
        self.assertTrue(svc.running)
        self.assertEqual(self.events, [StateChanged(True, "")])
        self.assertFalse(self.observers[0].stopped.is_set())

    def test_a_dead_observer_is_reported_and_stopped_cleanly(self):
        svc = self.make()
        svc.start()
        self.observers[0].alive = False
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertTrue(wait_for(lambda: self.reported(DIED)), f"{self.events}")
        self.assertFalse(svc.running)
        self.assertTrue(self.observers[0].stopped.wait(5))
        self.assertTrue(self.observers[0].joined.wait(5))
        self.settle(0.3)
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(False, DIED)])  # reported exactly once

    def test_a_dead_emitter_under_a_live_observer_is_reported(self):
        svc = self.make()
        svc.start()
        self.observers[0].emitters = [mock.Mock(is_alive=lambda: True), mock.Mock(is_alive=lambda: False)]
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertTrue(wait_for(lambda: self.reported(DIED)), f"{self.events}")
        self.assertFalse(svc.running)

    def test_a_removed_watch_folder_is_reported_with_a_clear_reason(self):
        svc = self.make()
        svc.start()
        os.rmdir(self.inp)
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertTrue(wait_for(lambda: self.reported(REMOVED)), f"{self.events}")
        self.assertFalse(svc.running)
        self.assertTrue(self.observers[0].stopped.wait(5))

    def test_it_can_be_resumed_after_a_failure(self):
        svc = self.make()
        svc.start()
        self.observers[0].alive = False
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertTrue(wait_for(lambda: self.reported(DIED)))
        self.assertEqual(svc.start(), "")
        self.assertTrue(svc.running)
        self.assertEqual(len(self.observers), 2)
        self.settle(0.3)  # the dead one is not reported again, and the new one is healthy
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(False, DIED), StateChanged(True, "")])
        self.observers[1].handler.on_created(created(self.src()))
        self.wait_finished()

    def test_a_user_stop_means_no_failure_report(self):
        svc = self.make()
        svc.start()
        svc.stop()
        self.observers[0].alive = False
        os.rmdir(self.inp)
        self.settle(0.4)
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(False, "")])

    def test_a_restart_replaces_the_health_check_instead_of_stacking_them(self):
        svc = self.make()
        svc.start()
        svc.start()
        self.assertTrue(wait_for(lambda: len(health_threads()) == 1))
        self.observers[0].alive = False  # the retired observer dying means nothing
        self.settle(0.3)
        self.assertTrue(svc.running)
        self.assertEqual(self.events, [StateChanged(True, ""), StateChanged(True, "")])

    def test_shutdown_ends_the_health_check(self):
        svc = self.make()
        svc.start()
        self.assertEqual(len(health_threads()), 1)
        svc.shutdown(5)
        self.assertEqual(health_threads(), [])
        self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("autohdr-")], [])

    def test_a_raising_on_event_does_not_break_the_health_check_thread(self):
        def on_event(e):
            if not e.running:
                raise RuntimeError("UI bug")

        svc = self.make(on_event=on_event)
        svc.start()
        self.observers[0].alive = False
        with self.assertLogs(LOGGER, "ERROR") as logged:
            self.assertTrue(wait_for(lambda: any("on_event handler raised" in m for m in logged.output)))
        self.assertFalse(svc.running)


class LostEventsTests(ServiceCase):
    """watchdog says events were lost (a big burst overflowed the OS buffer): list the folder and catch up."""

    def setUp(self):
        super().setUp()
        self.use_fake_observer()

    def lost(self, handler):
        handler.dispatch(EventsLostEvent(self.inp, is_directory=True))  # the way watchdog delivers it

    def test_unconverted_files_are_queued_and_already_converted_ones_are_not(self):
        svc = self.make()
        svc.start()
        for name in ("one.jxr", "TWO.JXR", "done.jxr"):
            self.src(name)
        touch(os.path.join(self.out, "done-sdr.jpg"), b"earlier")  # converted before the burst
        self.src("a.png")
        os.makedirs(os.path.join(self.inp, "sub"))
        self.src("deeper.jxr", folder=os.path.join(self.inp, "sub"))  # only the top level is watched
        os.makedirs(os.path.join(self.inp, "folder.jxr"))
        self.lost(self.observers[0].handler)
        results = self.wait_finished(2)
        self.settle(0.3)
        self.assertEqual(sorted(os.path.basename(r.src) for r in self.finished()), ["TWO.JXR", "one.jxr"])
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(sorted(os.listdir(self.out)), ["TWO-sdr.jpg", "done-sdr.jpg", "one-sdr.jpg"])

    def test_with_no_output_folder_set_the_output_is_looked_for_next_to_the_original(self):
        svc = self.make(self.settings(output_dir=""))
        svc.start()
        self.src("new.jxr")
        self.src("old.jxr")
        touch(os.path.join(self.inp, "old-sdr.jpg"), b"earlier")
        self.lost(self.observers[0].handler)
        (r,) = self.wait_finished()
        self.settle(0.3)
        self.assertEqual(os.path.basename(r.src), "new.jxr")
        self.assertEqual(len(self.runs), 1)

    def test_files_already_queued_or_converting_are_not_converted_twice(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        a, b = self.src("a.jxr"), self.src("b.jxr")
        self.gate.clear()
        h.on_created(created(a))
        self.assertTrue(self.hdrfix_started.wait(5))
        h.on_created(created(b))  # queued behind a
        self.src("c.jxr")  # its event was lost
        self.lost(h)
        self.lost(h)  # a second overflow changes nothing
        self.gate.set()
        self.wait_finished(3)
        self.settle(0.3)
        self.assertEqual(sorted(os.path.basename(r.src) for r in self.finished()), ["a.jxr", "b.jxr", "c.jxr"])
        self.assertEqual(len(self.runs), 3)

    def test_a_second_rescan_after_everything_converted_does_nothing(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        self.src("a.jxr")
        self.lost(h)
        self.wait_finished()
        self.lost(h)
        self.settle(0.3)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(os.listdir(self.out), ["a-sdr.jpg"])

    def test_a_rescan_from_an_old_session_is_ignored(self):
        svc = self.make()
        svc.start()
        svc.start()
        self.src("a.jxr")
        self.lost(self.observers[0].handler)  # the observer that is shutting down
        self.settle(0.3)
        self.assertEqual(self.runs, [])
        self.lost(self.observers[1].handler)
        self.wait_finished()

    def test_a_vanished_folder_is_logged_not_raised(self):
        svc = self.make()
        svc.start()
        os.rmdir(self.inp)
        with self.assertLogs(LOGGER, "ERROR"):
            self.lost(self.observers[0].handler)
        self.assertEqual(self.runs, [])

    def test_the_handler_never_raises_from_a_lost_events_signal(self):
        svc = self.make()
        svc.start()
        with mock.patch.object(svc, "_rescan", side_effect=RuntimeError("boom")), self.assertLogs(LOGGER, "ERROR"):
            self.lost(self.observers[0].handler)

    def test_a_stopped_service_does_not_rescan(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        svc.stop()
        self.src("a.jxr")
        self.lost(h)
        self.settle(0.3)
        self.assertEqual(self.runs, [])


if __name__ == "__main__":
    unittest.main()
