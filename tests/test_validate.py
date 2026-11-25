"""Tests for autohdr.service.validate_settings: checks only, creates nothing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autohdr import service  # noqa: E402
from autohdr.models import Problems, Settings  # noqa: E402
from tests.service_support import ServiceCase, touch  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
