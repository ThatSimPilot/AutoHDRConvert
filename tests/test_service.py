"""Tests for autohdr.service. hdrfix (subprocess.Popen) and send2trash are always mocked; no real Recycle Bin.

Most tests drive the handler through a fake watchdog Observer so they are deterministic; a few use the real
watchdog Observer on a temp folder to prove the wiring (case-insensitive .jxr, renames, stop not blocking).
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import convert, service  # noqa: E402
from autohdr.models import (ConversionFinished, ConversionStarted, Problems, Settings,  # noqa: E402
                            StateChanged)
from autohdr.paths import HDRFIX_MISSING  # noqa: E402

LOGGER = "AutoHDRConvert"
HDRFIX = r"C:\fake\hdrfix.exe"


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def created(path, is_directory=False):
    return types.SimpleNamespace(is_directory=is_directory, src_path=path, dest_path=path)


def moved(src, dest, is_directory=False):
    return types.SimpleNamespace(is_directory=is_directory, src_path=src, dest_path=dest)


class FakeObserver:
    """Records what the service asks of watchdog's Observer."""

    def __init__(self):
        self.handler = self.path = None
        self.started = False
        self.stopped = threading.Event()
        self.joined = threading.Event()

    def schedule(self, handler, path, recursive=False):
        self.handler, self.path, self.recursive = handler, path, recursive

    def start(self):
        self.started = True

    def stop(self):
        self.stopped.set()

    def join(self, timeout=None):
        self.joined.set()


class FakeProc:
    """A hdrfix child. Blocks on the test's gate (or until terminated), then runs the test's script."""

    def __init__(self, argv, test):
        self.argv, self.test = argv, test
        self.returncode = None
        self.terminated = False

    def communicate(self, timeout=None):
        t = self.test
        with t.lock:
            t.active += 1
            t.max_active = max(t.max_active, t.active)
        t.hdrfix_started.set()
        deadline = time.monotonic() + 10
        while not (t.gate.is_set() or self.terminated) and time.monotonic() < deadline:
            time.sleep(0.005)
        with t.lock:
            t.active -= 1
        if self.terminated:
            self.returncode = 1
            return "", "terminated"
        self.returncode, out, err = t.script(self.argv)
        return out, err

    def kill(self):
        self.terminated = True

    def terminate(self):
        self.terminated = True


class ServiceCase(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.inp, self.out = os.path.join(tmp, "in"), os.path.join(tmp, "out")
        os.makedirs(self.inp)
        self.lock = threading.Lock()
        self.active = self.max_active = 0
        self.gate = threading.Event()
        self.gate.set()  # hdrfix finishes at once unless a test clears this
        self.hdrfix_started = threading.Event()
        self.script = self.ok_script
        self.runs = []  # argv of every hdrfix launch
        self.events = []
        self.observers = []
        self.services = []
        real_wait = convert.wait_for_file
        self.enterContext(mock.patch.object(convert, "wait_for_file", lambda p: real_wait(p, timeout=0.5, settle=0.02)))
        self.enterContext(mock.patch.object(convert.subprocess, "Popen", side_effect=self.make_proc))
        self.trash = self.enterContext(mock.patch.object(convert, "send2trash"))
        self.enterContext(mock.patch.object(convert, "_drive_type", return_value=convert.DRIVE_FIXED))
        self.find = self.enterContext(mock.patch.object(service, "find_hdrfix", return_value=HDRFIX))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.gate.set()
        for svc in self.services:
            svc.shutdown(5)

    # -- fakes ----------------------------------------------------------------------------------------------------
    def make_proc(self, argv, **kwargs):
        self.runs.append(argv)
        return FakeProc(argv, self)

    @staticmethod
    def ok_script(argv):
        touch(argv[2], b"jpeg")
        return 0, "", ""

    def use_fake_observer(self):
        def factory():
            self.observers.append(FakeObserver())
            return self.observers[-1]

        self.enterContext(mock.patch.object(service, "Observer", side_effect=factory))

    # -- helpers --------------------------------------------------------------------------------------------------
    def settings(self, **kw):
        return Settings(**{"input_dir": self.inp, "output_dir": self.out, **kw})

    def make(self, settings=None, on_event=None):
        svc = service.ConversionService(on_event or self.events.append, settings or self.settings())
        self.services.append(svc)
        return svc

    def src(self, name="shot.jxr", folder=None):
        return touch(os.path.join(folder or self.inp, name), b"jxr-data")

    def finished(self):
        return [e.result for e in list(self.events) if isinstance(e, ConversionFinished)]

    def wait_finished(self, n=1, timeout=5):
        self.assertTrue(wait_for(lambda: len(self.finished()) >= n, timeout), f"events: {self.events}")
        return self.finished()

    def settle(self, seconds=0.2):  # to prove that nothing more happens
        time.sleep(seconds)


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
        self.assertEqual(self.runs, [[HDRFIX, src, r.dst]])

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

    def test_an_exception_inside_convert_still_pairs_started_with_finished(self):
        svc = self.make()
        svc.start()
        h = self.observers[0].handler
        src = self.src("a.jxr")
        with mock.patch.object(service, "convert_file", side_effect=RuntimeError("boom")), \
                self.assertLogs(LOGGER, "ERROR"):
            h.on_created(created(src))
            (r,) = self.wait_finished()
        self.assertEqual([type(e) for e in self.events], [StateChanged, ConversionStarted, ConversionFinished])
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


class ValidateSettingsTests(ServiceCase):
    def test_all_good(self):
        self.assertEqual(service.validate_settings(self.settings()), Problems())
        self.assertFalse(Problems().any)

    def test_input_problems(self):
        for value, want in (("", "Choose a folder to watch."), ("   ", "Choose a folder to watch."),
                            ('""', "Choose a folder to watch."), (self.inp + "-nope", "That folder doesn't exist."),
                            (touch(os.path.join(self.inp, "f.txt")), "That folder doesn't exist.")):
            with self.subTest(value=value):
                p = service.validate_settings(self.settings(input_dir=value))
                self.assertEqual((p.input, p.output, p.tool), (want, "", ""))
                self.assertTrue(p.any)

    def test_blank_output_is_fine_and_a_creatable_one_is_fine_without_being_created(self):
        self.assertEqual(service.validate_settings(self.settings(output_dir="")).output, "")
        deep = os.path.join(self.out, "a", "b", "c")
        self.assertEqual(service.validate_settings(self.settings(output_dir=deep)).output, "")
        self.assertFalse(os.path.exists(self.out))  # validate_settings creates nothing

    def test_output_problems(self):
        f = touch(os.path.join(self.inp, "afile"))
        for value, want in ((f, "That's a file, not a folder."),
                            (os.path.join(f, "sub"), "That folder can't be created."),
                            (os.path.join(self.out, 'bad|name'), 'Folder names can\'t contain < > : " | ? *'),
                            (os.path.join(self.out, "what?"), 'Folder names can\'t contain < > : " | ? *')):
            with self.subTest(value=value):
                p = service.validate_settings(self.settings(output_dir=value))
                self.assertEqual((p.input, p.output), ("", want))

    def test_output_on_a_drive_that_does_not_exist(self):
        free = [c for c in "ZYXWVUTSR" if not os.path.exists(f"{c}:\\")]
        if not free:
            self.skipTest("no free drive letter")
        p = service.validate_settings(self.settings(output_dir=f"{free[0]}:\\shots"))
        self.assertEqual(p.output, "That folder can't be created.")

    def test_missing_hdrfix_is_a_tool_problem(self):
        self.find.return_value = None
        p = service.validate_settings(self.settings())
        self.assertEqual((p.input, p.output, p.tool), ("", "", "hdrfix.exe wasn't found. Put it next to the app or add it to PATH."))
        self.assertTrue(p.any)

    def test_pasted_paths_are_normalised(self):
        p = service.validate_settings(self.settings(input_dir=f' "{self.inp}" ', output_dir=f'"{self.out}"'))
        self.assertEqual(p, Problems())

    def test_several_problems_at_once(self):
        self.find.return_value = None
        p = service.validate_settings(Settings(input_dir="", output_dir=os.path.join(self.inp, "x|y")))
        self.assertTrue(p.input and p.output and p.tool)


class RealWatchdogTests(ServiceCase):
    """The real Observer on a temp folder (each test pays ~1 s for watchdog to wind down on Windows)."""

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
            if isinstance(e, ConversionFinished):
                self.assertIn(ConversionStarted(e.result.src), self.events[:i])
        self.assertTrue(os.path.exists(shot))

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


if __name__ == "__main__":
    unittest.main()
