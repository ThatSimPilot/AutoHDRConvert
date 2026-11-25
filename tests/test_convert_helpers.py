"""Tests for the small helpers in autohdr.convert: output naming, the recycle safety rules and wait_for_file.

send2trash is always mocked; nothing real gets recycled.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import convert  # noqa: E402

LOGGER = "AutoHDRConvert"
REAL_DRIVE_TYPE = convert._drive_type  # captured before any test patches it
JPEG = b"\xff\xd8jpeg\xff\xd9"  # starts with FF D8, ends with FF D9: all the recycle check looks at


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


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
        touch(self.out, JPEG)
        self.assertTrue(convert.trash_original(self.src, self.out))
        self.trash.assert_called_once_with(os.path.abspath(self.src))

    def test_output_that_is_not_a_whole_jpeg_keeps_the_original(self):
        bad = {"no start marker": b"\x00\x00jpeg\xff\xd9", "no end marker": b"\xff\xd8jpeg\x00\x00",
               "cut off mid-stream": b"\xff\xd8" + b"x" * 100, "one byte": b"\xff", "three bytes": b"\xff\xd8\xff"}
        for label, data in bad.items():
            with self.subTest(output=label):
                touch(self.out, data)
                with self.assertLogs(LOGGER, "ERROR"):
                    self.assertEqual(convert._recycle(self.src, self.out), convert.INCOMPLETE_MSG)
                    self.assertFalse(convert.trash_original(self.src, self.out))
                self.trash.assert_not_called()
                self.assertTrue(os.path.exists(self.src))
        touch(self.out, b"\xff\xd8\xff\xd9")  # the smallest thing that has both markers
        self.assertTrue(convert.trash_original(self.src, self.out))

    def test_trash_failure_returns_false(self):
        touch(self.out, JPEG)
        self.trash.side_effect = OSError("denied")
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertFalse(convert.trash_original(self.src, self.out))
        self.assertTrue(os.path.exists(self.src))

    def test_only_fixed_drives_are_recycled(self):
        touch(self.out, JPEG)
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
        touch(self.out, JPEG)
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


if __name__ == "__main__":
    unittest.main()
