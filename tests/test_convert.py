"""Tests for autohdr.convert. send2trash and subprocess.Popen are always mocked; nothing real runs or gets recycled."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import convert  # noqa: E402

LOGGER = "AutoHDRConvert"
HDRFIX = r"C:\fake\hdrfix.exe"
REAL_DRIVE_TYPE = convert._drive_type  # captured before any test patches it


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class FakeProc:
    """Stands in for subprocess.Popen. `script(argv)` -> (returncode, stdout, stderr); it may write the output file."""

    def __init__(self, argv, kwargs, script):
        self.argv, self.kwargs, self.script = argv, kwargs, script
        self.returncode = None
        self.killed = self.terminated = False
        self.timeouts = []

    def communicate(self, timeout=None):
        self.timeouts.append(timeout)
        rc, out, err = self.script(self.argv)
        self.returncode = rc
        return out, err

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


class ConvertCase(unittest.TestCase):
    """Common sandbox: temp in/out folders, fake hdrfix process, mocked recycle bin, fast wait_for_file."""

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.inp, self.out = os.path.join(tmp, "in"), os.path.join(tmp, "out")
        os.makedirs(self.inp)
        os.makedirs(self.out)
        self.procs = []
        self.script = self.ok_script
        real_wait = convert.wait_for_file  # the real logic, with a tiny settle/timeout
        self.enterContext(mock.patch.object(convert, "wait_for_file", lambda p: real_wait(p, timeout=0.3, settle=0.02)))
        self.popen = self.enterContext(mock.patch.object(convert.subprocess, "Popen", side_effect=self.make_proc))
        self.trash = self.enterContext(mock.patch.object(convert, "send2trash"))
        self.drive = self.enterContext(mock.patch.object(convert, "_drive_type", return_value=convert.DRIVE_FIXED))

    def make_proc(self, argv, **kwargs):
        proc = FakeProc(argv, kwargs, lambda a: self.script(a))
        self.procs.append(proc)
        return proc

    @staticmethod
    def ok_script(argv):  # what hdrfix does on success: writes the output file
        touch(argv[2], b"jpeg")
        return 0, "", ""

    def src(self, name="shot.jxr"):
        return touch(os.path.join(self.inp, name), b"jxr-data")

    def convert(self, src, delete_original=False, **kw):
        return convert.convert_file(src, self.out, HDRFIX, delete_original, **kw)


class MakeOutputPathTests(unittest.TestCase):
    def setUp(self):
        self.out = self.enterContext(tempfile.TemporaryDirectory())

    def test_suffix_is_sdr_jpg_for_any_extension_case(self):
        for name, expected in (("shot.jxr", "shot-sdr.jpg"), ("SHOT.JXR", "SHOT-sdr.jpg")):
            with self.subTest(name=name):
                self.assertEqual(convert.make_output_path(os.path.join("in", name), self.out),
                                 os.path.join(self.out, expected))

    def test_only_the_final_extension_is_stripped(self):
        self.assertEqual(convert.make_output_path("a.jxr.b.jxr", self.out), os.path.join(self.out, "a.jxr.b-sdr.jpg"))

    def test_collisions_count_up(self):
        touch(os.path.join(self.out, "shot-sdr.jpg"))
        self.assertEqual(convert.make_output_path("shot.jxr", self.out), os.path.join(self.out, "shot-sdr-1.jpg"))
        touch(os.path.join(self.out, "shot-sdr-1.jpg"))
        self.assertEqual(convert.make_output_path("shot.jxr", self.out), os.path.join(self.out, "shot-sdr-2.jpg"))


class TrashOriginalTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.src = touch(os.path.join(tmp, "a.jxr"))
        self.out = os.path.join(tmp, "a-sdr.jpg")
        self.trash = self.enterContext(mock.patch.object(convert, "send2trash"))
        self.drive = self.enterContext(mock.patch.object(convert, "_drive_type", return_value=convert.DRIVE_FIXED))

    def test_missing_or_empty_output_keeps_original(self):
        for label, data in (("missing", None), ("empty", b"")):
            with self.subTest(output=label):
                if os.path.exists(self.out):
                    os.remove(self.out)
                if data is not None:
                    touch(self.out, data)
                with self.assertLogs(LOGGER, "ERROR"):
                    self.assertFalse(convert.trash_original(self.src, self.out))
                self.trash.assert_not_called()
                self.assertTrue(os.path.exists(self.src))

    def test_success_recycles_the_original(self):
        touch(self.out)
        self.assertTrue(convert.trash_original(self.src, self.out))
        self.trash.assert_called_once_with(os.path.abspath(self.src))

    def test_trash_failure_returns_false(self):
        touch(self.out)
        self.trash.side_effect = OSError("denied")
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFalse(convert.trash_original(self.src, self.out))
        self.assertTrue(os.path.exists(self.src))

    def test_only_fixed_drives_are_recycled(self):
        touch(self.out)
        for dtype, label in ((2, "removable"), (4, "network"), (5, "cd-rom"), (6, "ram disk"), (1, "no root"), (0, "unknown")):
            with self.subTest(drive=label):
                self.drive.return_value = dtype
                with self.assertLogs(LOGGER, "ERROR"):
                    self.assertFalse(convert.trash_original(self.src, self.out))
                self.trash.assert_not_called()
        self.drive.assert_called_with(self.src)
        self.drive.return_value = convert.DRIVE_FIXED
        self.assertTrue(convert.trash_original(self.src, self.out))

    def test_drive_check_failure_keeps_the_original(self):
        touch(self.out)
        self.drive.side_effect = OSError("no kernel32")
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFalse(convert.trash_original(self.src, self.out))
        self.trash.assert_not_called()

    def test_real_drive_type_is_an_int(self):  # exercises the ctypes call for real; nothing is recycled
        self.assertIsInstance(REAL_DRIVE_TYPE(self.src), int)


class WaitForFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def test_stable_file_returns(self):
        p = touch(os.path.join(self.tmp, "a.jxr"))
        self.assertIsNone(convert.wait_for_file(p, timeout=2, settle=0.05))

    def test_missing_file_raises_immediately(self):
        t0 = time.monotonic()
        with self.assertRaises(FileNotFoundError):
            convert.wait_for_file(os.path.join(self.tmp, "nope.jxr"), timeout=5, settle=0.05)
        self.assertLess(time.monotonic() - t0, 1)

    def test_file_vanishing_while_waiting_raises_immediately(self):
        p = touch(os.path.join(self.tmp, "gone.jxr"), b"")  # empty, so it keeps waiting
        threading.Timer(0.2, os.remove, args=(p,)).start()
        t0 = time.monotonic()
        with self.assertRaises(FileNotFoundError):
            convert.wait_for_file(p, timeout=5, settle=0.05)
        self.assertLess(time.monotonic() - t0, 2)

    def test_zero_byte_file_times_out(self):
        with self.assertRaises(TimeoutError):
            convert.wait_for_file(touch(os.path.join(self.tmp, "empty.jxr"), b""), timeout=0.3, settle=0.05)

    def test_timeout_is_idle_based_so_a_growing_file_is_not_failed(self):
        p = touch(os.path.join(self.tmp, "slow.jxr"))
        stop = threading.Event()

        def grow():
            while not stop.is_set():
                with open(p, "ab") as f:
                    f.write(b"x" * 10)
                time.sleep(0.05)

        t = threading.Thread(target=grow)
        t.start()
        self.addCleanup(t.join)
        self.addCleanup(stop.set)
        t0 = time.monotonic()
        threading.Timer(0.7, stop.set).start()  # stops growing after ~0.7 s, well past the 0.3 s idle timeout
        convert.wait_for_file(p, timeout=0.3, settle=0.15)  # returns only after growth stops + settle
        self.assertGreater(time.monotonic() - t0, 0.6)

    def test_unreadable_file_times_out_after_idle(self):
        p = touch(os.path.join(self.tmp, "locked.jxr"))
        with mock.patch.object(convert.os.path, "getsize", side_effect=PermissionError("sharing violation")):
            with self.assertRaises(TimeoutError):
                convert.wait_for_file(p, timeout=0.3, settle=0.05)


class ConvertFileSuccessTests(ConvertCase):
    def test_success_runs_hdrfix_with_a_safe_argv_and_keeps_the_original(self):
        src = self.src()
        r = self.convert(src)
        out = os.path.join(self.out, "shot-sdr.jpg")
        self.assertEqual((r.ok, r.src, r.dst, r.message, r.original_removed), (True, src, out, "", False))
        self.assertAlmostEqual(r.finished_at, time.time(), delta=5)
        (call,) = self.popen.call_args_list
        self.assertEqual(call.args[0], [HDRFIX, src, out])
        kw = call.kwargs
        self.assertFalse(kw["shell"])
        self.assertEqual(kw["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]), (subprocess.DEVNULL, subprocess.PIPE, subprocess.PIPE))
        self.assertEqual((kw["text"], kw["encoding"], kw["errors"]), (True, "utf-8", "replace"))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertTrue(os.path.exists(src))
        self.trash.assert_not_called()

    def test_hdrfix_gets_the_documented_timeout(self):
        self.convert(self.src())
        self.assertEqual(self.procs[0].timeouts, [120])

    def test_output_folder_is_created_for_every_conversion(self):
        os.rmdir(self.out)  # deleted since the service started
        r = self.convert(self.src())
        self.assertTrue(r.ok)
        self.assertTrue(os.path.isfile(r.dst))

    def test_existing_output_is_never_overwritten(self):
        touch(os.path.join(self.out, "shot-sdr.jpg"), b"earlier")
        r = self.convert(self.src())
        self.assertEqual(r.dst, os.path.join(self.out, "shot-sdr-1.jpg"))
        with open(os.path.join(self.out, "shot-sdr.jpg"), "rb") as f:
            self.assertEqual(f.read(), b"earlier")

    def test_delete_original_trashes_only_after_nonempty_output_exists(self):
        src, out = self.src(), os.path.join(self.out, "shot-sdr.jpg")
        seen = []
        self.trash.side_effect = lambda p: seen.append((p, os.path.getsize(out)))
        r = self.convert(src, delete_original=True)
        self.trash.assert_called_once_with(os.path.abspath(src))
        self.assertEqual(seen, [(os.path.abspath(src), 4)])  # output was already complete when trash ran
        self.assertEqual((r.ok, r.message, r.original_removed), (True, "", True))

    def test_original_on_a_non_fixed_drive_is_kept_but_the_conversion_succeeds(self):
        self.drive.return_value = 4  # DRIVE_REMOTE
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.trash.assert_not_called()
        self.assertEqual((r.ok, r.original_removed, r.message), (True, False, convert.NO_BIN_MSG))
        self.assertEqual(r.message, "Converted, but the original was kept (Recycle Bin isn't available on that drive)")
        self.assertTrue(os.path.isfile(r.dst))
        self.assertTrue(os.path.exists(src))

    def test_recycle_failure_keeps_the_original_but_the_conversion_succeeds(self):
        self.trash.side_effect = OSError("denied")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.assertEqual((r.ok, r.original_removed), (True, False))
        self.assertEqual(r.message, "Converted, but couldn't move the original to the Recycle Bin")
        self.assertTrue(os.path.isfile(r.dst))
        self.assertTrue(os.path.exists(src))

    def test_on_process_sees_the_child_then_none(self):
        calls = []
        self.convert(self.src(), on_process=calls.append)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0], self.procs[0])
        self.assertIsNone(calls[1])


class ConvertFileFailureTests(ConvertCase):
    def assertFailed(self, r, src, message=None):
        self.assertFalse(r.ok)
        self.assertEqual((r.src, r.dst, r.original_removed), (src, "", False))
        if message is not None:
            self.assertEqual(r.message, message)
        self.assertTrue(r.message)
        self.assertNotIn("Traceback", r.message)
        self.trash.assert_not_called()
        self.assertTrue(os.path.exists(src) or "removed" in r.message)
        self.assertEqual(os.listdir(self.out), [])  # no partial or empty jpg left behind

    def test_nonzero_exit_reports_the_last_stderr_line_and_keeps_everything(self):
        def script(argv):
            touch(argv[2], b"partial")  # hdrfix died half way through writing
            return 3, "", "starting\n\n  bad pixel format \n"

        self.script = script
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.assertFailed(r, src, "bad pixel format")

    def test_nonzero_exit_without_stderr_still_has_a_message(self):
        self.script = lambda argv: (7, "", "")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFailed(self.convert(src), src, "hdrfix failed (exit 7)")

    def test_exit_zero_without_output_is_a_failure_using_stderr(self):  # hdrfix exits 0 on bad input
        self.script = lambda argv: (0, "", "Error: could not decode image\n")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR") as logged:
            r = self.convert(src, delete_original=True)
        self.assertFailed(r, src, "Error: could not decode image")
        self.assertFalse(any("Converted" in line for line in logged.output))

    def test_exit_zero_without_output_or_stderr(self):
        self.script = lambda argv: (0, "", "")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFailed(self.convert(src, delete_original=True), src, "hdrfix didn't produce an image")

    def test_exit_zero_with_empty_output_deletes_the_empty_file(self):
        def script(argv):
            touch(argv[2], b"")
            return 0, "", ""

        self.script = script
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFailed(self.convert(src, delete_original=True), src, "hdrfix didn't produce an image")

    def test_long_stderr_lines_are_truncated(self):
        self.script = lambda argv: (1, "", "x" * 1000)
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(self.src())
        self.assertLessEqual(len(r.message), 200)

    def test_timeout_kills_the_child_and_cleans_up(self):
        src = self.src()
        calls = []

        def communicate(timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                touch(os.path.join(self.out, "shot-sdr.jpg"), b"partial")
                raise subprocess.TimeoutExpired("hdrfix", timeout)
            return "", ""  # the reaping call after kill()

        def make(argv, **kw):
            proc = FakeProc(argv, kw, None)
            proc.communicate = communicate
            self.procs.append(proc)
            return proc

        self.popen.side_effect = make
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.assertFailed(r, src, "hdrfix timed out after 120 s")
        self.assertTrue(self.procs[0].killed)
        self.assertEqual(calls, [120, None])

    def test_missing_hdrfix_exe(self):
        self.popen.side_effect = FileNotFoundError(2, "The system cannot find the file specified")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFailed(self.convert(src, delete_original=True), src,
                              "hdrfix.exe wasn't found. Put it next to the app or add it to PATH.")

    def test_other_launch_errors_are_readable(self):
        self.popen.side_effect = OSError(193, "%1 is not a valid Win32 application")
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src)
        self.assertFailed(r, src)
        self.assertIn("not a valid Win32 application", r.message)

    def test_source_that_never_finishes_being_written(self):
        src = self.src("empty.jxr")
        open(src, "wb").close()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src)
        self.assertFailed(r, src, "The file never finished being written")
        self.popen.assert_not_called()

    def test_missing_source_fails_at_once_without_running_hdrfix(self):
        src = os.path.join(self.inp, "ghost.jxr")
        t0 = time.monotonic()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src)
        self.assertLess(time.monotonic() - t0, 1)
        self.assertFailed(r, src, "The file was removed before it could be converted")
        self.popen.assert_not_called()

    def test_source_vanishing_mid_conversion_reports_removal(self):
        src = self.src()

        def script(argv):
            os.remove(src)
            return 0, "", "cannot open input\n"

        self.script = script
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.assertFailed(r, src, "The file was removed before it could be converted")

    def test_never_raises_whatever_goes_wrong(self):
        src = self.src()
        for label, patch in (
            ("popen", mock.patch.object(convert.subprocess, "Popen", side_effect=RuntimeError("boom"))),
            ("makedirs", mock.patch.object(convert.os, "makedirs", side_effect=PermissionError("denied"))),
            ("wait", mock.patch.object(convert, "wait_for_file", side_effect=ValueError("odd"))),
            ("make_output_path", mock.patch.object(convert, "make_output_path", side_effect=KeyError("k"))),
        ):
            with self.subTest(stage=label), patch, self.assertLogs(LOGGER, "ERROR"):
                r = self.convert(src, delete_original=True)
                self.assertFailed(r, src)
                self.assertTrue(r.message.startswith("Unexpected error"))

    def test_unexpected_error_while_waiting_for_hdrfix_stops_the_child(self):
        src = self.src()
        proc = FakeProc([], {}, None)
        proc.communicate = mock.Mock(side_effect=[RuntimeError("pipe broke"), ("", "")])
        self.popen.side_effect = lambda argv, **kw: proc
        calls = []
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, on_process=calls.append)
        self.assertFalse(r.ok)
        self.assertTrue(proc.killed)
        self.assertEqual(calls, [proc, None])


if __name__ == "__main__":
    unittest.main()
