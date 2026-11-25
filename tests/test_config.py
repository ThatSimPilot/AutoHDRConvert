"""Tests for autohdr.config. APPDATA is always a temp dir; the real config is never touched."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import config, paths  # noqa: E402
from autohdr.models import Settings  # noqa: E402

LOGGER = "AutoHDRConvert"


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class ConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.appdata = os.path.join(tmp, "appdata")
        self.legacy_dir = os.path.join(tmp, "appfolder")
        os.makedirs(self.legacy_dir)
        self.new_file = os.path.join(self.appdata, "AutoHDRConvert", "config.json")
        self.legacy_file = os.path.join(self.legacy_dir, "config.json")
        self.enterContext(mock.patch.dict(os.environ, {"APPDATA": self.appdata}))
        self.enterContext(mock.patch.object(config, "app_dir", return_value=self.legacy_dir))

    def write_new(self, data):
        touch(self.new_file, json.dumps(data).encode())

    def test_missing_config_gives_defaults(self):
        self.assertEqual(config.load_settings(), Settings())

    def test_save_then_load_round_trips(self):
        s = Settings("C:\\in", "C:\\out", True, True, True, "all", False)
        self.assertTrue(config.save_settings(s))
        self.assertTrue(os.path.isfile(self.new_file))
        self.assertEqual(config.load_settings(), s)

    def test_tone_preset_round_trips_and_defaults_to_natural(self):
        for key in ("natural", "bright"):
            with self.subTest(key=key):
                self.assertTrue(config.save_settings(Settings(tone_preset=key)))
                with open(self.new_file, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["tone_preset"], key)
                self.assertEqual(config.load_settings().tone_preset, key)
        self.write_new({"input_dir": "C:\\in"})  # an older config without the key
        self.assertEqual(config.load_settings().tone_preset, "natural")

    def test_bad_tone_preset_falls_back_to_natural_for_that_field_only(self):
        for value in ("loud", "", "PUNCHY", 5, None, True, ["punchy"], {"punchy": 1}):
            with self.subTest(value=value):
                self.write_new({"tone_preset": value, "input_dir": "C:\\in", "auto_start": False})
                with self.assertLogs(LOGGER, "WARNING"):
                    got = config.load_settings()
                self.assertEqual(got, Settings(input_dir="C:\\in", auto_start=False, tone_preset="natural"))

    def test_legacy_config_used_only_while_new_one_is_absent(self):
        with open(self.legacy_file, "w", encoding="utf-8") as f:
            json.dump({"input_dir": "legacy_in", "unknown_key": 1}, f)
        self.assertEqual(config.load_settings(), Settings(input_dir="legacy_in"))  # defaults filled, junk dropped
        config.save_settings(Settings(input_dir="new_in"))
        self.assertEqual(config.load_settings().input_dir, "new_in")

    def test_corrupt_config_falls_back_to_defaults_without_resurrecting_legacy(self):
        with open(self.legacy_file, "w", encoding="utf-8") as f:
            json.dump({"input_dir": "legacy_in"}, f)
        touch(self.new_file, b"{not json")
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertEqual(config.load_settings(), Settings())

    def test_non_object_json_gives_defaults(self):
        touch(self.new_file, b"[1, 2]")
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertEqual(config.load_settings(), Settings())

    def test_unreadable_config_never_raises(self):
        os.makedirs(self.new_file)  # a folder where the file should be
        with self.assertLogs(LOGGER, "ERROR"):
            self.assertEqual(config.load_settings(), Settings())

    def test_unknown_keys_are_ignored(self):
        self.write_new({"input_dir": "C:\\in", "theme": "neon", "nested": {"a": 1}})
        self.assertEqual(config.load_settings(), Settings(input_dir="C:\\in"))

    def test_wrong_types_fall_back_per_field(self):
        self.write_new({"input_dir": 5, "output_dir": "C:\\out", "delete_original": "yes", "minimize_to_tray": 1,
                        "output_to_log": True, "notifications": ["all"], "auto_start": None})
        with self.assertLogs(LOGGER, "WARNING"):
            got = config.load_settings()
        self.assertEqual(got, Settings(output_dir="C:\\out", output_to_log=True))

    def test_notifications_accepts_only_the_three_modes(self):
        for value, want in (("failures", "failures"), ("all", "all"), ("off", "off"), ("loud", "failures"), ("", "failures")):
            with self.subTest(value=value):
                self.write_new({"notifications": value})
                with self.assertNoLogs(LOGGER, "ERROR"):
                    self.assertEqual(config.load_settings().notifications, want)

    def test_pasted_paths_lose_whitespace_and_surrounding_quotes(self):
        self.write_new({"input_dir": '  "C:\\Users\\me\\Shots"  ', "output_dir": ' C:\\out '})
        got = config.load_settings()
        self.assertEqual((got.input_dir, got.output_dir), ("C:\\Users\\me\\Shots", "C:\\out"))

    def test_save_failure_returns_false(self):
        blocker = touch(os.path.join(self.legacy_dir, "afile"))
        with mock.patch.dict(os.environ, {"APPDATA": blocker}), self.assertLogs(LOGGER, "ERROR"):
            self.assertFalse(config.save_settings(Settings()))

    def test_save_is_atomic_a_failed_write_keeps_the_old_file_and_leaves_no_temp(self):
        config.save_settings(Settings(input_dir="good"))
        with mock.patch.object(config.json, "dump", side_effect=OSError("disk full")), self.assertLogs(LOGGER, "ERROR"):
            self.assertFalse(config.save_settings(Settings(input_dir="bad")))
        self.assertEqual(config.load_settings().input_dir, "good")
        self.assertEqual(os.listdir(os.path.dirname(self.new_file)), ["config.json"])

    def test_save_writes_a_temp_file_beside_the_config_then_replaces(self):
        seen = []
        real_replace = os.replace
        self.enterContext(mock.patch.object(config.os, "replace", lambda a, b: (seen.append((a, b)), real_replace(a, b))[1]))
        self.assertTrue(config.save_settings(Settings()))
        (tmp, dst), = seen
        self.assertEqual(os.path.dirname(tmp), os.path.dirname(dst))
        self.assertNotEqual(tmp, dst)
        with open(self.new_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f), asdict(Settings()))


class SettingsFolderTests(unittest.TestCase):
    """set_config_dir: the user picks where config.json lives. Everything happens in temp dirs."""

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.appdata = os.path.join(tmp, "appdata")
        self.legacy_dir = os.path.join(tmp, "appfolder")
        self.chosen = os.path.join(tmp, "my settings")
        os.makedirs(self.legacy_dir)
        self.default = os.path.join(self.appdata, "AutoHDRConvert")
        self.default_file = os.path.join(self.default, "config.json")
        self.pointer = os.path.join(self.default, "config_location.txt")
        self.enterContext(mock.patch.dict(os.environ, {"APPDATA": self.appdata}))
        self.enterContext(mock.patch.object(config, "app_dir", return_value=self.legacy_dir))

    def test_config_dir_defaults_to_the_appdata_folder(self):
        self.assertEqual(paths.config_dir(), self.default)
        self.assertFalse(os.path.exists(self.pointer))

    def test_set_config_dir_copies_the_config_and_writes_the_pointer(self):
        config.save_settings(Settings(input_dir="old_in"))
        self.assertEqual(config.set_config_dir(self.chosen), "")
        self.assertTrue(os.path.isdir(self.chosen))
        self.assertTrue(os.path.isfile(os.path.join(self.chosen, "config.json")))
        with open(self.pointer, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), os.path.normpath(self.chosen))
        self.assertEqual(paths.config_dir(), os.path.normpath(self.chosen))
        self.assertEqual(config.load_settings().input_dir, "old_in")
        self.assertTrue(os.path.isfile(self.default_file), "the old file stays behind as a backup")

    def test_quoted_pasted_folder_is_cleaned(self):
        self.assertEqual(config.set_config_dir(f'  "{self.chosen}"  '), "")
        with open(self.pointer, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), os.path.normpath(self.chosen))

    def test_existing_config_in_the_new_folder_is_not_overwritten(self):
        config.save_settings(Settings(input_dir="old_in"))
        touch(os.path.join(self.chosen, "config.json"), json.dumps(asdict(Settings(input_dir="there"))).encode())
        self.assertEqual(config.set_config_dir(self.chosen), "")
        self.assertEqual(config.load_settings().input_dir, "there")

    def test_blank_folder_returns_to_the_default_and_removes_the_pointer(self):
        config.save_settings(Settings(input_dir="old_in"))
        self.assertEqual(config.set_config_dir(self.chosen), "")
        self.assertEqual(config.set_config_dir(""), "")
        self.assertFalse(os.path.exists(self.pointer))
        self.assertEqual(paths.config_dir(), self.default)
        self.assertEqual(config.load_settings().input_dir, "old_in")

    def test_choosing_the_default_folder_removes_the_pointer(self):
        self.assertEqual(config.set_config_dir(self.chosen), "")
        self.assertEqual(config.set_config_dir(self.default.upper()), "")
        self.assertFalse(os.path.exists(self.pointer))
        self.assertEqual(paths.config_dir(), self.default)

    def test_unusable_folder_returns_an_error_and_changes_nothing(self):
        config.save_settings(Settings(input_dir="old_in"))
        blocker = touch(os.path.join(self.legacy_dir, "afile"))   # a folder can't be made under a file
        for bad in (os.path.join(blocker, "sub"), blocker):
            with self.subTest(bad=bad):
                err = config.set_config_dir(bad)
                self.assertIsInstance(err, str)
                self.assertTrue(err)
                self.assertFalse(os.path.exists(self.pointer))
                self.assertEqual(paths.config_dir(), self.default)
                self.assertEqual(config.load_settings().input_dir, "old_in")

    def test_never_raises_even_when_the_pointer_cannot_be_written(self):
        os.makedirs(self.pointer)  # a folder where the pointer file should be
        with mock.patch.object(config, "config_location_file", return_value=self.pointer):
            err = config.set_config_dir(self.chosen)
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
