"""Tests for autohdr.service: the event flow and the queue. hdrfix (subprocess.Popen) and send2trash are always mocked.

These drive the handler through a fake watchdog Observer so they are deterministic. See also test_service_lifecycle.py
(start/stop/configure/shutdown), test_service_watch.py (real watchdog, health check, lost events), test_validate.py.
"""
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import service  # noqa: E402
from autohdr.models import TONE_PRESETS, ConversionFinished, ConversionStarted, StateChanged  # noqa: E402
from autohdr.paths import HDRFIX_MISSING  # noqa: E402
from tests.service_support import (HDRFIX, LOGGER, ServiceCase, created, moved, touch,  # noqa: E402
                                   wait_for)


class EventFlowTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.use_fake_observer()

    def test_start_then_started_then_finished_in_order(self):
        svc = self.make()
        self.assertEqual(svc.start(), "")
        self.assertTrue(svc.running)
        src = self.src()
        self.observers[0].handler.on_created(created(src))
        (r,) = self.wait_finished()
        self.assertEqual(self.events, [StateChanged(True, ""), ConversionStarted(src), ConversionFinished(r)])
        self.assertEqual((r.ok, r.src, r.message, r.original_removed), (True, src, "", False))
        self.assertEqual(r.dst, os.path.join(self.out, "shot-sdr.jpg"))
        self.assertTrue(os.path.exists(src))
        self.assertEqual(self.runs, [[HDRFIX, src, r.dst, *TONE_PRESETS["natural"][1]]])

    def test_events_come_from_the_worker_not_the_caller(self):
        threads = []
        svc = self.make(on_event=lambda e: threads.append((type(e), threading.current_thread())))
        svc.start()
        self.observers[0].handler.on_created(created(self.src()))
        self.assertTrue(wait_for(lambda: len(threads) == 3))
        main = threading.current_thread()
        self.assertIs(threads[0][1], main)  # StateChanged comes from start()'s caller
        self.assertIsNot(threads[1][1], main)
        self.assertIs(threads[1][1], threads[2][1])  # Started and Finished: the one worker thread

    def test_failure_reports_a_message_and_keeps_the_original(self):
        self.script = lambda argv: (0, "", "Error: could not decode\n")  # hdrfix exits 0 on bad input
        svc = self.make(self.settings(delete_original=True))
        svc.start()
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.observers[0].handler.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertEqual((r.ok, r.dst, r.message), (False, "", "Error: could not decode"))
        self.assertTrue(os.path.exists(src))
        self.trash.assert_not_called()
        self.assertEqual(os.listdir(self.out), [])

    def test_only_jxr_files_and_not_directories_are_queued(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        for path, is_dir in (("a.jpg", False), ("a.jxr.tmp", False), ("noext", False), ("folder.jxr", True)):
            h.on_created(created(os.path.join(self.inp, path), is_dir))
        self.settle()
        self.assertEqual(self.runs, [])
        self.assertEqual(self.events, [StateChanged(True, "")])
        for name in ("one.jxr", "TWO.JXR", "Three.Jxr"):
            h.on_created(created(self.src(name)))
        self.wait_finished(3)
        self.assertEqual(sorted(os.listdir(self.out)), ["TWO-sdr.jpg", "Three-sdr.jpg", "one-sdr.jpg"])

    def test_uppercase_extensions_reach_hdrfix_under_a_lowercase_name(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        for name in ("TWO.JXR", "Three.Jxr"):
            h.on_created(created(self.src(name)))
        results = self.wait_finished(2)
        self.assertTrue(all(r.ok for r in results))
        for argv in self.runs:  # hdrfix rejects anything but a lowercase .jxr
            self.assertEqual(os.path.splitext(argv[1])[1], ".jxr")
        self.assertEqual(sorted(os.path.basename(r.dst) for r in results), ["TWO-sdr.jpg", "Three-sdr.jpg"])

    def test_on_moved_uses_the_destination(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        dest = self.src("shot.jxr")
        h.on_moved(moved(dest + ".part", dest))
        self.wait_finished()
        self.assertEqual(len(self.runs), 1)
        h.on_moved(moved(dest, dest + ".bak"))
        self.settle()
        self.assertEqual(len(self.runs), 1)

    def test_handler_never_raises(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        with mock.patch.object(svc, "_enqueue", side_effect=RuntimeError("boom")), self.assertLogs(LOGGER, "ERROR"):
            h.on_created(created(self.src()))
            h.on_moved(moved("a.jxr", "b.jxr"))
        with self.assertLogs(LOGGER, "ERROR"):
            h.on_created(types.SimpleNamespace())  # malformed event: no attributes at all
        h.on_created(created(self.src("ok.jxr")))  # and it still works afterwards
        self.wait_finished()

    def test_a_raising_on_event_does_not_kill_the_worker(self):
        seen = []

        def on_event(e):
            seen.append(e)
            if isinstance(e, ConversionFinished) and len(seen) < 6:
                raise RuntimeError("UI bug")

        svc = self.make(on_event=on_event)
        svc.start()
        h = self.observers[0].handler
        with self.assertLogs(LOGGER, "ERROR"):
            h.on_created(created(self.src("a.jxr")))
            self.assertTrue(wait_for(lambda: sum(isinstance(e, ConversionFinished) for e in seen) == 1))
            h.on_created(created(self.src("b.jxr")))
            self.assertTrue(wait_for(lambda: sum(isinstance(e, ConversionFinished) for e in seen) == 2))
        self.assertEqual(len(self.runs), 2)

    def test_an_exception_inside_convert_is_reported_and_the_worker_survives(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        src = self.src("a.jxr")
        with mock.patch.object(service, "convert_file", side_effect=RuntimeError("boom")), \
                self.assertLogs(LOGGER, "ERROR"):
            h.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertEqual([type(e) for e in self.events], [StateChanged, ConversionFinished])
        self.assertFalse(r.ok)
        self.assertTrue(r.message)
        h.on_created(created(self.src("b.jxr")))  # the worker survived
        self.wait_finished(2)
        self.assertTrue(self.finished()[1].ok)

    def test_hdrfix_disappearing_after_start_is_a_failure_not_a_crash(self):
        svc = self.make()
        svc.start()
        self.find.return_value = None
        self.observers[0].handler.on_created(created(self.src()))
        (r,) = self.wait_finished()
        self.assertEqual((r.ok, r.message), (False, HDRFIX_MISSING))
        self.assertEqual(self.runs, [])

    def test_a_file_that_vanished_before_work_started_is_skipped_silently(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        with self.assertLogs(LOGGER, "INFO") as logged:
            h.on_created(created(os.path.join(self.inp, "ghost.jxr")))
            self.assertTrue(wait_for(lambda: any("Skipped" in line for line in logged.output)))
        h.on_created(created(self.src("real.jxr")))
        self.wait_finished()
        self.assertEqual([type(e) for e in self.events], [StateChanged, ConversionStarted, ConversionFinished])

    def test_a_file_that_vanishes_while_we_wait_for_it_is_skipped_silently_too(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        src = self.src("slow.jxr")
        open(src, "wb").close()  # empty, so the service keeps waiting for it to be written
        timer = threading.Timer(0.1, os.remove, args=(src,))
        timer.start()
        self.addCleanup(timer.join)
        with self.assertLogs(LOGGER, "INFO") as logged:
            h.on_created(created(src))
            self.assertTrue(wait_for(lambda: any("Skipped" in line for line in logged.output)))
        self.assertTrue(wait_for(lambda: not svc._inflight))
        self.assertTrue(all(rec.levelno < 40 for rec in logged.records))  # nothing at ERROR
        self.assertEqual(self.events, [StateChanged(True, "")])  # no failure row, and no "Converting" left dangling
        self.assertEqual(self.runs, [])
        h.on_created(created(self.src("real.jxr")))  # the worker carries on
        self.wait_finished()
        self.assertEqual([type(e) for e in self.events], [StateChanged, ConversionStarted, ConversionFinished])

    def test_a_file_that_vanishes_mid_conversion_is_a_normal_failure(self):
        svc = self.make()
        svc.start()
        src = self.src()

        def script(argv):
            os.remove(src)
            return 0, "", "cannot open input"

        self.script = script
        with self.assertLogs(LOGGER, "ERROR"):
            self.observers[0].handler.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertEqual((r.ok, r.message), (False, "The file was removed before it could be converted"))
        self.assertEqual([type(e) for e in self.events], [StateChanged, ConversionStarted, ConversionFinished])

    def test_an_output_that_looks_incomplete_keeps_the_original_even_when_deleting(self):
        self.script = lambda argv: (touch(argv[2], b"no jpeg markers"), (0, "", ""))[1]
        svc = self.make(self.settings(delete_original=True))
        svc.start()
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.observers[0].handler.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertEqual((r.ok, r.original_removed), (True, False))
        self.assertEqual(r.message, "Converted, but the original was kept (output looked incomplete)")
        self.assertTrue(os.path.exists(src))
        self.trash.assert_not_called()


class DedupeAndQueueTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.use_fake_observer()

    def test_duplicate_events_while_running_or_queued_convert_once(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        src = self.src()
        self.gate.clear()
        h.on_created(created(src))
        self.assertTrue(self.hdrfix_started.wait(5))
        h.on_created(created(src))  # duplicates while it is converting
        h.on_moved(moved(src + ".x", src))
        h.on_created(created(src.upper()))  # same file, different spelling (Windows paths are case-insensitive)
        self.gate.set()
        self.wait_finished()
        self.settle()
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(len(self.finished()), 1)

    def test_a_path_can_be_converted_again_once_finished(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        src = self.src()
        h.on_created(created(src))
        self.wait_finished()
        h.on_created(created(src))
        self.wait_finished(2)
        self.assertEqual([os.path.basename(r.dst) for r in self.finished()], ["shot-sdr.jpg", "shot-sdr-1.jpg"])

    def test_files_convert_one_at_a_time_in_arrival_order(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        self.gate.clear()
        names = ["a.jxr", "b.jxr", "c.jxr"]
        for n in names:
            h.on_created(created(self.src(n)))
        self.assertTrue(self.hdrfix_started.wait(5))
        self.settle(0.2)
        self.gate.set()
        self.wait_finished(3)
        self.assertEqual([os.path.basename(r.src) for r in self.finished()], names)
        self.assertEqual(self.max_active, 1)

    def test_events_do_not_block_the_dispatching_thread(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        self.gate.clear()
        h.on_created(created(self.src("a.jxr")))
        self.assertTrue(self.hdrfix_started.wait(5))
        t0 = time.perf_counter()
        for n in ("b.jxr", "c.jxr", "d.jxr"):
            h.on_created(created(self.src(n)))  # a conversion is running, yet these return immediately
        self.assertLess(time.perf_counter() - t0, 0.2)
        self.gate.set()
        self.wait_finished(4)

    def test_retry_works_straight_after_a_failure(self):
        results = iter([(1, "", "bad"), (0, "", "")])

        def script(argv):
            rc, out, err = next(results)
            if rc == 0:
                touch(argv[2], b"jpeg")
            return rc, out, err

        self.script = script
        svc = self.make()
        svc.start()
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.observers[0].handler.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertFalse(r.ok)
        svc.retry(src)  # immediately after Finished: the path must already be eligible again
        self.wait_finished(2)
        self.assertTrue(self.finished()[1].ok)
        self.assertEqual(len(self.runs), 2)

    def test_retry_of_a_missing_file_reports_failure_without_started(self):
        svc = self.make()
        gone = os.path.join(self.inp, "gone.jxr")
        svc.retry(gone)
        (r,) = self.wait_finished()
        self.assertEqual((r.ok, r.src, r.dst, r.message), (False, gone, "", "The original file no longer exists"))
        self.assertEqual([type(e) for e in self.events], [ConversionFinished])
        self.assertEqual(self.runs, [])

    def test_retry_works_while_paused(self):
        svc = self.make()
        src = self.src()
        svc.retry(src)  # never started
        self.wait_finished()
        self.assertTrue(self.finished()[0].ok)
        self.assertFalse(svc.running)

    def test_stop_drops_files_still_waiting_but_lets_the_running_one_finish(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        self.gate.clear()
        a, b = self.src("a.jxr"), self.src("b.jxr")
        h.on_created(created(a))
        self.assertTrue(self.hdrfix_started.wait(5))
        h.on_created(created(b))  # queued behind a
        svc.stop()
        self.gate.set()
        (r,) = self.wait_finished()
        self.settle()
        self.assertEqual((r.src, r.ok), (a, True))
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(svc._inflight, set())  # b is not stuck as "in flight"
        svc.retry(b)  # and can be converted later
        self.wait_finished(2)


if __name__ == "__main__":
    unittest.main()
