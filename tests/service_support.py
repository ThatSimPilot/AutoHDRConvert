"""Shared fakes for the autohdr.service tests (test_service*.py, test_validate.py). Not a test module itself.

hdrfix (subprocess.Popen) and send2trash are always mocked; no real Recycle Bin. Most tests drive the handler through
FakeObserver so they are deterministic; the real watchdog Observer is only used in test_service_watch.py.
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
from autohdr.models import ConversionFinished, Settings  # noqa: E402

LOGGER = "AutoHDRConvert"
HDRFIX = r"C:\fake\hdrfix.exe"
JPEG = b"\xff\xd8jpeg\xff\xd9"  # starts with FF D8, ends with FF D9: what the recycle check wants to see


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
    """Records what the service asks of watchdog's Observer. Set `alive = False` to play a watcher that died."""

    def __init__(self):
        self.handler = self.path = None
        self.started = False
        self.alive = True
        self.emitters = []  # objects with is_alive(), like watchdog's
        self.stopped = threading.Event()
        self.joined = threading.Event()

    def schedule(self, handler, path, recursive=False):
        self.handler, self.path, self.recursive = handler, path, recursive

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

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
        touch(argv[2], JPEG)
        return 0, "", ""

    def use_fake_observer(self):
        def factory():
            self.observers.append(FakeObserver())
            return self.observers[-1]

        self.enterContext(mock.patch.object(service, "Observer", side_effect=factory))

    def fast_health_checks(self, interval=0.05):
        self.enterContext(mock.patch.object(service.ConversionService, "HEALTH_INTERVAL", interval))

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
