"""Tests for autohdr.config. APPDATA is always a temp dir; the real config is never touched."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import config  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
