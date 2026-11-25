"""Tests for autohdr.logs. APPDATA and LOCALAPPDATA are temp dirs; handlers are reset in tearDown so no file handle leaks."""
import io
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import logs  # noqa: E402
from autohdr.paths import log_path  # noqa: E402

LOGGER = "AutoHDRConvert"


class SetupLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.local = os.path.join(self.tmp, "local")   # stands in for %LOCALAPPDATA%
        self.enterContext(mock.patch.dict(os.environ, {"APPDATA": self.tmp, "LOCALAPPDATA": self.local}))
        self.enterContext(mock.patch.object(sys, "stderr", io.StringIO()))  # keep the console handler quiet
        self.root = logging.getLogger()
        self.level = self.root.level
        self.addCleanup(self.reset)

    def reset(self):
        logs.setup_logging(False)  # closes any file handle so the temp dir can be deleted
        for h in logs._log_handlers:  # then drop our console handler too, restoring the root logger
            self.root.removeHandler(h)
        logs._log_handlers.clear()
        self.root.setLevel(self.level)

    def file_handlers(self):
        return [h for h in self.root.handlers if isinstance(h, logging.FileHandler)]

    def test_on_adds_one_file_handler_writing_to_localappdata_logs(self):
        logs.setup_logging(True)
        (fh,) = self.file_handlers()
        self.assertEqual(os.path.normcase(fh.baseFilename), os.path.normcase(log_path()))
        self.assertEqual(os.path.normcase(fh.baseFilename),
                         os.path.normcase(os.path.join(self.local, "AutoHDRConvert", "logs", "AutoHDRConvert.log")))
        logs.log.info("hello-log")
        logs.setup_logging(False)  # close before reading
        with open(log_path(), encoding="utf-8") as f:
            self.assertIn("INFO - hello-log", f.read())

    def test_off_removes_and_closes_the_handler(self):
        logs.setup_logging(True)
        (fh,) = self.file_handlers()
        logs.setup_logging(False)
        self.assertEqual(self.file_handlers(), [])
        self.assertIsNone(fh.stream)  # file closed

    def test_repeated_on_does_not_duplicate_handlers(self):
        logs.setup_logging(True)
        count = len(self.root.handlers)
        logs.setup_logging(True)
        self.assertEqual(len(self.root.handlers), count)
        self.assertEqual(len(self.file_handlers()), 1)

    def test_rotating_file_is_capped_at_one_megabyte(self):
        logs.setup_logging(True)
        (fh,) = self.file_handlers()
        self.assertEqual((fh.maxBytes, fh.backupCount), (1_000_000, 1))

    def test_a_configured_folder_is_used(self):
        folder = os.path.join(self.tmp, "mine")
        logs.setup_logging(True, folder)
        (fh,) = self.file_handlers()
        self.assertEqual(os.path.normcase(fh.baseFilename), os.path.normcase(os.path.join(folder, "AutoHDRConvert.log")))
        self.assertEqual(os.path.normcase(logs.active_log_folder()), os.path.normcase(folder))

    def test_unwritable_folder_falls_back_to_appdata(self):
        blocker = os.path.join(self.tmp, "afile")
        with open(blocker, "wb") as f:
            f.write(b"x")
        with self.assertLogs(LOGGER, "ERROR"):
            logs.setup_logging(True, os.path.join(blocker, "logs"))   # a folder can't be made under a file
        (fh,) = self.file_handlers()
        self.assertEqual(os.path.normcase(fh.baseFilename),
                         os.path.normcase(os.path.join(self.tmp, "AutoHDRConvert", "AutoHDRConvert.log")))

    def test_nothing_writable_does_not_raise(self):
        blocker = os.path.join(self.tmp, "afile")
        with open(blocker, "wb") as f:
            f.write(b"x")
        with mock.patch.dict(os.environ, {"APPDATA": blocker}), self.assertLogs(LOGGER, "ERROR"):
            logs.setup_logging(True, os.path.join(blocker, "logs"))
        self.assertEqual(self.file_handlers(), [])

    def test_no_console_handler_when_there_is_no_stderr(self):
        with mock.patch.object(sys, "stderr", None):
            logs.setup_logging(False)
            self.assertEqual(logs._log_handlers, [])
        logs.setup_logging(False)
        self.assertEqual(len(logs._log_handlers), 1)  # with a stderr there is exactly one console handler


if __name__ == "__main__":
    unittest.main()
