"""Tests for autohdr.convert. send2trash and subprocess.Popen are always mocked; nothing real runs or gets recycled."""
import logging
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
from autohdr.models import TONE_PRESETS  # noqa: E402

LOGGER = "AutoHDRConvert"
HDRFIX = r"C:\fake\hdrfix.exe"
JPEG = b"\xff\xd8jpeg\xff\xd9"  # starts with FF D8, ends with FF D9: all the recycle check looks at


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
        touch(argv[2], JPEG)
        return 0, "", ""

    def src(self, name="shot.jxr"):
        return touch(os.path.join(self.inp, name), b"jxr-data")

    def convert(self, src, delete_original=False, **kw):
        return convert.convert_file(src, self.out, HDRFIX, delete_original, **kw)


class ConvertFileSuccessTests(ConvertCase):
    def test_success_runs_hdrfix_with_a_safe_argv_and_keeps_the_original(self):
        src = self.src()
        r = self.convert(src)
        out = os.path.join(self.out, "shot-sdr.jpg")
        self.assertEqual((r.ok, r.src, r.dst, r.message, r.original_removed), (True, src, out, "", False))
        self.assertAlmostEqual(r.finished_at, time.time(), delta=5)
        (call,) = self.popen.call_args_list
        self.assertEqual(call.args[0], [HDRFIX, src, out, *TONE_PRESETS["natural"][1]])
        kw = call.kwargs
        self.assertFalse(kw["shell"])
        self.assertEqual(kw["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]), (subprocess.DEVNULL, subprocess.PIPE, subprocess.PIPE))
        self.assertEqual((kw["text"], kw["encoding"], kw["errors"]), (True, "utf-8", "replace"))
        self.assertGreater(os.path.getsize(out), 0)
        self.assertTrue(os.path.exists(src))
        self.trash.assert_not_called()

    def test_tone_preset_options_follow_the_two_paths_and_natural_adds_nothing(self):
        cases = tuple((key, TONE_PRESETS[key][1]) for key in ("natural", "bright"))
        for key, extra in cases:
            with self.subTest(preset=key):
                self.popen.reset_mock()
                src = self.src()
                r = self.convert(src, tone_preset=key)
                self.assertTrue(r.ok)
                self.assertEqual(self.popen.call_args.args[0], [HDRFIX, src, r.dst, *extra])

    def test_unknown_tone_preset_falls_back_to_natural(self):
        src = self.src()
        r = self.convert(src, tone_preset="no-such-preset")
        self.assertTrue(r.ok)
        self.assertEqual(self.popen.call_args.args[0], [HDRFIX, src, r.dst, *TONE_PRESETS["natural"][1]])

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
        self.assertEqual(seen, [(os.path.abspath(src), len(JPEG))])  # output was already complete when trash ran
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

    def test_output_that_looks_incomplete_keeps_the_original_but_the_conversion_succeeds(self):
        def script(argv):
            touch(argv[2], b"jpeg without end marker")
            return 0, "", ""

        self.script = script
        src = self.src()
        with self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(src, delete_original=True)
        self.trash.assert_not_called()
        self.assertEqual((r.ok, r.original_removed, r.message), (True, False, convert.INCOMPLETE_MSG))
        self.assertEqual(r.message, "Converted, but the original was kept (output looked incomplete)")
        self.assertTrue(os.path.isfile(r.dst))
        self.assertTrue(os.path.exists(src))

    def test_on_process_sees_the_child_then_none(self):
        calls = []
        self.convert(self.src(), on_process=calls.append)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0], self.procs[0])
        self.assertIsNone(calls[1])


class HdrfixInputTests(ConvertCase):
    """hdrfix only accepts a lowercase .jxr extension, so other spellings go through a temp link that is always removed."""

    def setUp(self):
        super().setUp()
        self.seen = []  # (path hdrfix was given, its content) per run
        self.made = []  # every temp folder created
        real_mkdtemp = convert.tempfile.mkdtemp

        def mkdtemp(*a, **kw):
            self.made.append(real_mkdtemp(*a, **kw))
            return self.made[-1]

        self.enterContext(mock.patch.object(convert.tempfile, "mkdtemp", mkdtemp))

        def strict_hdrfix(argv):  # the real one: "Invalid input file type" for anything but a lowercase .jxr
            if os.path.splitext(argv[1])[1] != ".jxr":
                return 0, "", "Error: Invalid input file type"
            with open(argv[1], "rb") as f:
                self.seen.append((argv[1], f.read()))
            touch(argv[2], JPEG)
            return 0, "", ""

        self.script = strict_hdrfix

    def test_any_spelling_of_the_extension_converts_under_the_original_stem(self):
        for name in ("lower.jxr", "UPPER.JXR", "Mixed.Jxr", "two.jXr"):
            with self.subTest(name=name):
                self.seen.clear()
                self.made.clear()
                src = self.src(name)
                r = self.convert(src)
                stem = os.path.splitext(name)[0]
                self.assertEqual((r.ok, r.message, r.dst), (True, "", os.path.join(self.out, f"{stem}-sdr.jpg")))
                ((path, data),) = self.seen
                self.assertEqual(data, b"jxr-data")  # hdrfix saw the real content
                self.assertTrue(os.path.exists(src))
                if name.endswith(".jxr"):
                    self.assertEqual((path, self.made), (src, []))  # already acceptable: no detour at all
                else:
                    self.assertEqual(len(self.made), 1)
                    self.assertEqual(os.path.dirname(path), self.made[0])
                    self.assertNotEqual(self.made[0], self.inp)  # never inside the watched folder
                    self.assertFalse(os.path.exists(self.made[0]))  # removed again

    def test_a_hard_link_is_tried_first_and_a_copy_is_the_fallback(self):
        src = self.src("UPPER.JXR")
        with mock.patch.object(convert.os, "link", wraps=os.link) as link:
            self.assertTrue(self.convert(src).ok)
        self.assertEqual(link.call_args.args[0], src)
        self.seen.clear()
        with mock.patch.object(convert.os, "link", side_effect=OSError("different volume")):
            r = self.convert(src)
        self.assertTrue(r.ok)
        ((path, data),) = self.seen
        self.assertEqual(data, b"jxr-data")
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_deleting_the_original_recycles_the_original_not_the_link(self):
        src = self.src("UPPER.JXR")
        r = self.convert(src, delete_original=True)
        self.assertEqual((r.ok, r.original_removed), (True, True))
        self.trash.assert_called_once_with(os.path.abspath(src))

    def test_temp_folder_is_removed_whatever_happens_to_hdrfix(self):
        def timing_out(argv, **kw):
            proc = FakeProc(argv, kw, None)
            calls = []

            def communicate(timeout=None):
                calls.append(timeout)
                if len(calls) == 1:
                    raise subprocess.TimeoutExpired("hdrfix", timeout)
                return "", ""

            proc.communicate = communicate
            return proc

        cases = (
            ("non-zero exit", lambda: setattr(self, "script", lambda argv: (3, "", "bad"))),
            ("exit 0, no image", lambda: setattr(self, "script", lambda argv: (0, "", "Error: nope"))),
            ("hdrfix missing", lambda: setattr(self.popen, "side_effect", FileNotFoundError(2, "gone"))),
            ("cannot launch", lambda: setattr(self.popen, "side_effect", OSError(193, "not a Win32 app"))),
            ("unexpected error", lambda: setattr(self.popen, "side_effect", RuntimeError("boom"))),
            ("timeout", lambda: setattr(self.popen, "side_effect", timing_out)),
        )
        for label, arrange in cases:
            with self.subTest(case=label):
                self.made.clear()
                self.popen.side_effect = self.make_proc
                arrange()
                with self.assertLogs(LOGGER, "ERROR"):
                    r = self.convert(self.src("SHOT.JXR"))
                self.assertFalse(r.ok)
                self.assertEqual(len(self.made), 1)
                self.assertFalse(os.path.exists(self.made[0]))
                self.assertEqual(os.listdir(self.out), [])

    def test_temp_folder_is_removed_when_hdrfix_is_not_started(self):
        self.convert(self.src("SHOT.JXR"), on_ready=lambda: False)
        self.assertEqual(len(self.made), 1)
        self.assertFalse(os.path.exists(self.made[0]))

    def test_a_failure_while_making_the_link_is_reported_not_raised(self):
        with mock.patch.object(convert.os, "link", side_effect=OSError("no")), \
                mock.patch.object(convert.shutil, "copyfile", side_effect=OSError(28, "No space left on device")), \
                self.assertLogs(LOGGER, "ERROR"):
            r = self.convert(self.src("SHOT.JXR"))
        self.assertFalse(r.ok)
        self.assertTrue(r.message.startswith("Unexpected error"))
        self.assertFalse(os.path.exists(self.made[0]))
        self.popen.assert_not_called()


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

    def test_missing_source_is_skipped_silently_without_running_hdrfix(self):
        src = os.path.join(self.inp, "ghost.jxr")
        t0 = time.monotonic()
        with self.assertLogs(LOGGER, "INFO") as logged:
            r = self.convert(src)
        self.assertLess(time.monotonic() - t0, 1)
        self.assertIsNone(r)  # nothing to report: no failure row, no toast
        self.assertTrue(any("Skipped" in line for line in logged.output))
        self.assertTrue(all(rec.levelno < logging.ERROR for rec in logged.records))
        self.popen.assert_not_called()
        self.assertEqual(os.listdir(self.out), [])

    def test_source_vanishing_while_waiting_for_it_to_settle_is_skipped_silently(self):
        src = self.src("slow.jxr")
        open(src, "wb").close()  # empty, so wait_for_file keeps waiting
        timer = threading.Timer(0.1, os.remove, args=(src,))
        timer.start()
        self.addCleanup(timer.join)
        ready = []
        with self.assertLogs(LOGGER, "INFO") as logged:
            r = self.convert(src, on_ready=lambda: ready.append(1) or True)
        self.assertIsNone(r)
        self.assertEqual(ready, [])
        self.assertTrue(all(rec.levelno < logging.ERROR for rec in logged.records))
        self.popen.assert_not_called()
        self.assertEqual(os.listdir(self.out), [])

    def test_on_ready_runs_once_the_file_has_settled_and_right_before_hdrfix(self):
        order = []
        self.popen.side_effect = lambda argv, **kw: order.append("popen") or self.make_proc(argv, **kw)
        r = self.convert(self.src(), on_ready=lambda: order.append("ready") or True)
        self.assertTrue(r.ok)
        self.assertEqual(order, ["ready", "popen"])

    def test_on_ready_returning_false_means_hdrfix_is_never_started(self):
        src = self.src()
        calls = []
        r = self.convert(src, delete_original=True, on_process=calls.append, on_ready=lambda: False)
        self.assertIsNone(r)
        self.popen.assert_not_called()
        self.assertEqual(calls, [])
        self.assertEqual(os.listdir(self.out), [])
        self.assertTrue(os.path.exists(src))
        self.trash.assert_not_called()

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
