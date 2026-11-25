"""Start menu shortcut tests. Everything is mocked: no real Start Menu writes and no real PowerShell."""
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autohdr import shortcut  # noqa: E402


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.appdata = tmp.name
        env = mock.patch.dict(os.environ, {"APPDATA": self.appdata})
        env.start()
        self.addCleanup(env.stop)
        self.lnk = shortcut.shortcut_path()
        os.makedirs(os.path.dirname(self.lnk))

    def fake_run(self, *, write=True, stdout="", stderr="", returncode=0):
        """A subprocess.run stand-in that behaves like PowerShell creating the .lnk (or failing)."""
        def run(cmd, **kw):
            if write:
                with open(self.lnk, "w") as f:
                    f.write("lnk")
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        return mock.patch.object(shortcut.subprocess, "run", side_effect=run)

    def test_shortcut_path_is_under_appdata_start_menu(self):
        expected = os.path.join(self.appdata, "Microsoft", "Windows", "Start Menu", "Programs",
                                "Auto HDR-SDR Converter.lnk")
        self.assertEqual(shortcut.shortcut_path(), expected)

    def test_available_only_when_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(shortcut.available())
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertFalse(shortcut.available())
        with mock.patch.object(sys, "frozen", None, create=True):
            self.assertFalse(shortcut.available())

    def test_exists_follows_the_file(self):
        self.assertFalse(shortcut.exists())
        with open(self.lnk, "w") as f:
            f.write("lnk")
        self.assertTrue(shortcut.exists())

    def test_create_when_not_frozen_refuses_without_running_powershell(self):
        with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(shortcut.subprocess, "run") as run:
            self.assertEqual(shortcut.create(), "Only the built app can add a shortcut.")
        run.assert_not_called()
        self.assertFalse(os.path.exists(self.lnk))

    def test_create_success_runs_powershell_and_returns_empty(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Apps\AutoHDR\AutoHDR.exe"), \
                self.fake_run() as run:
            self.assertEqual(shortcut.create(), "")
        self.assertTrue(os.path.isfile(self.lnk))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:6], ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"])
        kw = run.call_args.kwargs
        self.assertEqual(kw["timeout"], 30)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertTrue(kw["capture_output"])
        self.assertTrue(kw["text"])
        self.assertFalse(kw.get("shell", False))
        self.assertEqual(kw["creationflags"], subprocess.CREATE_NO_WINDOW)
        script = cmd[-1]
        self.assertIn("TargetPath = 'C:\\Apps\\AutoHDR\\AutoHDR.exe'", script)
        self.assertIn("WorkingDirectory = 'C:\\Apps\\AutoHDR'", script)
        self.assertIn("IconLocation = 'C:\\Apps\\AutoHDR\\AutoHDR.exe,0'", script)
        self.assertIn("Description = 'Auto HDR-SDR Converter'", script)
        self.assertIn("Save()", script)

    def test_create_failure_reports_last_stderr_line(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                self.fake_run(write=False, stderr="noise\nAccess is denied.\n", returncode=1):
            self.assertEqual(shortcut.create(), "Access is denied.")
        self.assertFalse(os.path.exists(self.lnk))

    def test_create_failure_falls_back_to_stdout_then_generic(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                self.fake_run(write=False, stdout="Something went wrong\n"):
            self.assertEqual(shortcut.create(), "Something went wrong")
        with mock.patch.object(sys, "frozen", True, create=True), self.fake_run(write=False):
            self.assertEqual(shortcut.create(), "Couldn't create the shortcut.")

    def test_apostrophe_in_path_is_doubled_in_script(self):
        target = r"C:\Users\O'Brien\AutoHDR.exe"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", target), self.fake_run() as run:
            self.assertEqual(shortcut.create(), "")
        script = run.call_args.args[0][-1]
        self.assertIn("TargetPath = 'C:\\Users\\O''Brien\\AutoHDR.exe'", script)
        self.assertIn("WorkingDirectory = 'C:\\Users\\O''Brien'", script)
        self.assertNotIn("O'Brien", script)                     # no unescaped single quote left inside the literal

    def test_remove_present_then_absent(self):
        with open(self.lnk, "w") as f:
            f.write("lnk")
        self.assertEqual(shortcut.remove(), "")
        self.assertFalse(os.path.exists(self.lnk))
        self.assertEqual(shortcut.remove(), "")                 # already gone is fine

    def test_remove_reports_os_error(self):
        with open(self.lnk, "w") as f:
            f.write("lnk")
        with mock.patch.object(shortcut.os, "remove", side_effect=PermissionError("locked")):
            err = shortcut.remove()
        self.assertTrue(err)

    def test_nothing_raises_when_subprocess_fails(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(shortcut.subprocess, "run", side_effect=OSError("no powershell")):
            self.assertEqual(shortcut.create(), "no powershell")
        with mock.patch.object(shortcut.os.path, "isfile", side_effect=OSError("boom")):
            self.assertFalse(shortcut.exists())
            self.assertTrue(shortcut.remove())


if __name__ == "__main__":
    unittest.main()
