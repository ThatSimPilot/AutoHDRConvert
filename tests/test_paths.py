"""Tests for autohdr.paths. Run from the repo root:  python -m unittest discover -s tests -v

APPDATA and LOCALAPPDATA are always temp dirs here, so the real settings and logs are never touched."""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from autohdr import paths  # noqa: E402


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class AppLocationTests(unittest.TestCase):
    def test_app_dir_is_the_repo_root_when_not_frozen(self):
        self.assertEqual(os.path.normcase(paths.app_dir()), os.path.normcase(ROOT))
        self.assertEqual(paths.resource_path("icon.png"), os.path.join(paths.app_dir(), "icon.png"))

    def test_frozen_app_dir_is_the_exe_folder_and_resources_are_in_the_bundle(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", os.path.join("C:\\", "apps", "AutoHDR.exe")), \
                mock.patch.object(sys, "_MEIPASS", os.path.join("C:\\", "tmp", "_MEI123"), create=True):
            self.assertEqual(paths.app_dir(), os.path.join("C:\\", "apps"))
            self.assertEqual(paths.resource_path("icon.png"), os.path.join("C:\\", "tmp", "_MEI123", "icon.png"))


class AppDataTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.appdata = os.path.join(tmp, "Roaming")
        self.local = os.path.join(tmp, "Local")
        self.enterContext(mock.patch.dict(os.environ, {"APPDATA": self.appdata, "LOCALAPPDATA": self.local}))
        self.default = os.path.join(self.appdata, "AutoHDRConvert")

    def test_settings_default_to_the_appdata_folder(self):
        self.assertEqual(paths.app_data_dir(), self.default)
        self.assertEqual(paths.config_dir(), self.default)
        self.assertEqual(paths.config_path(), os.path.join(self.default, "config.json"))
        self.assertEqual(paths.config_location_file(), os.path.join(self.default, "config_location.txt"))
        self.assertEqual(paths.fallback_log_path(), os.path.join(self.default, "AutoHDRConvert.log"))

    def test_config_dir_reads_the_pointer_file(self):
        chosen = os.path.join(self.local, "chosen")
        touch(paths.config_location_file(), f'  "{chosen}"\r\n'.encode())
        self.assertEqual(paths.config_dir(), chosen)
        self.assertEqual(paths.config_path(), os.path.join(chosen, "config.json"))
        self.assertEqual(paths.app_data_dir(), self.default, "the pointer doesn't move the pointer file or the fallback log")

    def test_blank_or_garbage_pointer_falls_back_to_the_default(self):
        pointer = paths.config_location_file()
        for content in (b"", b"   \r\n", b'""', b"relative\\folder", b"\xff\xfe\x00garbage"):
            with self.subTest(content=content):
                touch(pointer, content)
                self.assertEqual(paths.config_dir(), self.default)
                self.assertEqual(paths.config_path(), os.path.join(self.default, "config.json"))
        os.remove(pointer)
        os.makedirs(pointer)  # unreadable: a folder where the file should be
        self.assertEqual(paths.config_dir(), self.default)

    def test_blank_appdata_falls_back_to_the_home_folder(self):
        with mock.patch.dict(os.environ, {"APPDATA": ""}):
            self.assertEqual(paths.app_data_dir(), os.path.join(os.path.expanduser("~"), "AutoHDRConvert"))

    def test_default_logs_live_in_localappdata_not_beside_the_exe(self):
        want = os.path.join(self.local, "AutoHDRConvert", "logs")
        self.assertEqual(paths.default_log_dir(), want)
        self.assertEqual(paths.log_dir(), want)
        self.assertEqual(paths.log_path(), os.path.join(want, "AutoHDRConvert.log"))
        with mock.patch.object(paths, "app_dir", return_value=os.path.join("C:\\", "apps")):
            self.assertEqual(paths.log_path(), os.path.join(want, "AutoHDRConvert.log"))

    def test_blank_localappdata_falls_back_to_the_home_folder(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.assertEqual(paths.default_log_dir(),
                             os.path.join(os.path.expanduser("~"), "AppData", "Local", "AutoHDRConvert", "logs"))

    def test_a_configured_log_folder_wins_and_is_cleaned(self):
        self.assertEqual(paths.log_dir(' "D:\\my logs" '), "D:\\my logs")
        self.assertEqual(paths.log_path("D:\\L"), os.path.join("D:\\L", "AutoHDRConvert.log"))


class CleanPathTests(unittest.TestCase):
    def test_trims_whitespace_and_one_pair_of_quotes(self):
        for raw, want in (("  C:\\a b  ", "C:\\a b"), ('"C:\\a b"', "C:\\a b"), (' "C:\\a b" ', "C:\\a b"),
                          ('""C:\\a""', '"C:\\a"'), ('"C:\\a', '"C:\\a'), ('"', '"'), ("", ""), (None, "")):
            with self.subTest(raw=raw):
                self.assertEqual(paths.clean_path(raw), want)


class FindHdrfixTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.appd, self.mei = os.path.join(tmp, "app"), os.path.join(tmp, "mei")
        os.makedirs(self.appd)
        os.makedirs(self.mei)
        self.which = self.enterContext(mock.patch.object(paths.shutil, "which", return_value=None))
        self.enterContext(mock.patch.object(paths, "app_dir", return_value=self.appd))

    def test_app_folder_copy_wins_over_bundle_and_path(self):
        want = touch(os.path.join(self.appd, "hdrfix.exe"))
        touch(os.path.join(self.mei, "hdrfix.exe"))
        self.which.return_value = r"C:\bin\hdrfix.exe"
        with mock.patch.object(sys, "_MEIPASS", self.mei, create=True):
            self.assertEqual(paths.find_hdrfix(), want)

    def test_bundle_then_path_fallbacks(self):
        bundled = touch(os.path.join(self.mei, "hdrfix.exe"))
        self.which.return_value = r"C:\bin\hdrfix.exe"
        with mock.patch.object(sys, "_MEIPASS", self.mei, create=True):
            self.assertEqual(paths.find_hdrfix(), bundled)
            os.remove(bundled)
            self.assertEqual(paths.find_hdrfix(), r"C:\bin\hdrfix.exe")

    def test_none_when_not_found_anywhere(self):
        self.assertIsNone(paths.find_hdrfix())
        self.which.assert_called_once_with("hdrfix")


if __name__ == "__main__":
    unittest.main()
