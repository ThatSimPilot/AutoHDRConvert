"""UI smoke tests (offscreen). Run: .venv\\Scripts\\python.exe -m unittest tests.test_ui -v"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))   # offscreen has no fonts by default
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autohdr.models import TONE_PRESETS, ConversionResult, Problems, Settings  # noqa: E402
from autohdr.ui import icons, main_window, settings_dialog, theme  # noqa: E402
from autohdr.ui.main_window import MainWindow  # noqa: E402
from autohdr.ui.settings_dialog import SettingsDialog  # noqa: E402

app = None


def setUpModule():
    global app
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "light")


def bad(name="a", msg="hdrfix failed"):
    return ConversionResult(False, f"C:/shots/{name}.jxr", "", msg, False, time.time())


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        QTest.qWait(10)
    return cond()


class Base(unittest.TestCase):
    def window(self, settings=None, tray=True):
        win = MainWindow(settings or Settings(input_dir="C:/shots"), tray)
        self.addCleanup(self._dispose, win)
        win.show()
        return win

    @staticmethod
    def _dispose(win):
        win.hide()
        win.deleteLater()
        app.processEvents()

    @staticmethod
    def record(signal):
        got = []
        signal.connect(lambda *a: got.append(a[0] if len(a) == 1 else a))
        return got


class Palette(unittest.TestCase):
    PAIRS = [("text", "window"), ("text", "surface"), ("text2", "window"), ("text2", "surface"), ("danger", "surface"),
             ("success", "surface"), ("warning", "surface"), ("text", "danger_bg"), ("text2", "danger_bg"),
             ("text", "control"), ("text", "control_hover")]

    def test_text_contrast_in_both_schemes(self):
        for scheme in ("light", "dark"):
            c = theme.Colors(scheme, QColor(theme.FALLBACK_ACCENT))
            for fg, bg in self.PAIRS:
                with self.subTest(scheme=scheme, fg=fg, bg=bg):
                    self.assertGreaterEqual(theme.contrast(getattr(c, fg), getattr(c, bg)), 4.5)

    def test_any_system_accent_stays_readable(self):
        for scheme in ("light", "dark"):
            for hexval in (theme.FALLBACK_ACCENT, "#4CC2FF", "#308CC6", "#E81123", "#FFB900", "#767676", "#107C10", "#FFFFFF", "#000000"):
                c = theme.Colors(scheme, QColor(hexval))
                with self.subTest(scheme=scheme, accent=hexval):
                    self.assertGreaterEqual(theme.contrast(c.accent_text, c.accent), 4.5)    # button label
                    self.assertGreaterEqual(theme.contrast(c.accent_fg, c.surface), 4.5)     # accent as text/stroke

    def test_both_schemes_apply(self):
        for scheme in ("dark", "light"):
            theme.apply(app, scheme)
            self.assertEqual(theme.scheme(), scheme)
            self.assertIn("QPushButton", app.styleSheet())
        win = MainWindow(Settings(input_dir="C:/shots"), True)     # builds fine under either scheme
        win.deleteLater()
        theme.apply(app, "light")


class Icons(unittest.TestCase):
    def test_icons_exist_and_pause_differs(self):
        self.assertFalse(icons.app_icon().isNull())
        on, off = icons.tray_icon(True), icons.tray_icon(False)
        self.assertFalse(on.isNull() or off.isNull())
        self.assertNotEqual(on.pixmap(32, 32).toImage(), off.pixmap(32, 32).toImage())

    def test_missing_icon_png_falls_back_to_painted_icon(self):
        icons._cache.clear()
        with mock.patch.object(icons, "_icon_png", return_value=""):
            self.assertFalse(icons.app_icon().isNull())
            self.assertFalse(icons.tray_icon(False).isNull())
        icons._cache.clear()

    def test_glyph_icon_paints(self):
        for name in icons._GLYPHS:
            self.assertFalse(icons.icon(name).pixmap(24, 24).isNull(), name)


class MainWindowSignals(Base):
    def test_toggle_button_emits(self):
        win = self.window()
        got = self.record(win.toggle_requested)
        win.toggle_button.click()
        self.assertEqual(len(got), 1)

    def test_text_edit_is_debounced_then_emitted_once(self):
        win = self.window(Settings(input_dir=""))
        got = self.record(win.settings_changed)
        for ch in "C:\\pics":
            QTest.keyClick(win.in_edit, ch)
        app.processEvents()
        self.assertEqual(got, [])                                # nothing yet: still inside the 400 ms window
        self.assertTrue(wait_for(lambda: got))
        QTest.qWait(600)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].input_dir, "C:\\pics")

    def test_pasted_paths_are_normalised(self):
        win = self.window(Settings(input_dir=""))
        got = self.record(win.settings_changed)
        win.in_edit.setText('  "C:\\My Pics"  ')
        win.in_edit.textEdited.emit(win.in_edit.text())
        win.in_edit.editingFinished.emit()                       # leaving the field emits immediately
        self.assertEqual(got[-1].input_dir, "C:\\My Pics")
        self.assertEqual(win.in_edit.text(), "C:\\My Pics")
        win.out_edit.setText(' "D:\\out" ')
        win.out_edit.textEdited.emit(win.out_edit.text())
        self.assertTrue(wait_for(lambda: got[-1].output_dir == "D:\\out"))

    def test_radio_emits_immediately(self):
        win = self.window()
        got = self.record(win.settings_changed)
        win.bin_choice.click()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].delete_original)
        win.keep_choice.click()
        self.assertFalse(got[1].delete_original)

    def test_unchanged_text_does_not_emit(self):
        win = self.window()
        got = self.record(win.settings_changed)
        win.in_edit.textEdited.emit(win.in_edit.text())
        QTest.qWait(550)
        self.assertEqual(got, [])

    def test_set_settings_does_not_emit(self):
        win = self.window()
        got = self.record(win.settings_changed)
        win.set_settings(Settings(input_dir="D:/other", output_dir="E:/out", delete_original=True))
        QTest.qWait(550)
        self.assertEqual(got, [])
        self.assertEqual((win.in_edit.text(), win.out_edit.text(), win.bin_choice.isChecked()), ("D:/other", "E:/out", True))
        self.assertTrue(win.out_reset.isVisibleTo(win))

    def test_reset_clears_output_and_emits(self):
        win = self.window(Settings(input_dir="C:/shots", output_dir="D:/out"))
        got = self.record(win.settings_changed)
        win.out_reset.click()
        self.assertEqual(got[-1].output_dir, "")
        self.assertFalse(win.out_reset.isVisibleTo(win))
        self.assertEqual(win.out_edit.placeholderText(), "Same folder as originals")

    def test_browse_uses_directory_dialog(self):
        win = self.window(Settings())
        got = self.record(win.settings_changed)
        with mock.patch.object(main_window.QFileDialog, "getExistingDirectory", return_value="C:/Users/me/Shots") as dlg:
            win.in_browse.click()
        dlg.assert_called_once()
        self.assertEqual(got[-1].input_dir, os.path.normpath("C:/Users/me/Shots"))
        with mock.patch.object(main_window.QFileDialog, "getExistingDirectory", return_value=""):
            win.out_browse.click()                               # cancelled: nothing changes
        self.assertEqual(len(got), 1)

    def test_dialog_edit_flows_through_window_signal(self):
        win = self.window()
        got = self.record(win.settings_changed)
        win.open_settings()
        dlg = win._dialog
        self.assertTrue(dlg.isVisible())
        dlg.log_switch.click()
        self.assertTrue(got[-1].output_to_log)
        dlg.notify_choices["all"].click()
        self.assertEqual(got[-1].notifications, "all")
        dlg.tone_choices["bright"].click()
        self.assertEqual(got[-1].tone_preset, "bright")
        self.assertEqual(got[-1].input_dir, "C:/shots")          # folder fields are untouched
        dlg.close()


class MainWindowClosing(Base):
    def test_ctrl_q_quits(self):
        win = self.window()
        got = self.record(win.quit_requested)
        win.activateWindow()
        QTest.qWaitForWindowActive(win, 1000)
        QTest.keyClick(win, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
        if not got:                                              # shortcut delivery needs an active window; fall back
            win._quit_shortcut.activated.emit()
        self.assertEqual(len(got), 1)

    def test_quit_button_emits_once(self):
        win = self.window()
        got = self.record(win.quit_requested)
        win.quit_button.click()
        win.quit_button.click()
        self.assertEqual(len(got), 1)

    def test_close_quits_when_not_minimising(self):
        win = self.window(Settings(input_dir="C:/shots", minimize_to_tray=False))
        quit_, hidden = self.record(win.quit_requested), self.record(win.hidden_to_tray)
        win.close()
        self.assertEqual((len(quit_), len(hidden)), (1, 0))

    def test_close_hides_to_tray_when_enabled_and_available(self):
        win = self.window(Settings(input_dir="C:/shots", minimize_to_tray=True), tray=True)
        quit_, hidden = self.record(win.quit_requested), self.record(win.hidden_to_tray)
        win.close()
        self.assertEqual((len(quit_), len(hidden)), (0, 1))
        self.assertFalse(win.isVisible())
        win.show_and_raise()
        self.assertTrue(win.isVisible())

    def test_close_quits_when_tray_missing_even_if_enabled(self):
        win = self.window(Settings(input_dir="C:/shots", minimize_to_tray=True), tray=False)
        quit_, hidden = self.record(win.quit_requested), self.record(win.hidden_to_tray)
        win.close()
        self.assertEqual((len(quit_), len(hidden)), (1, 0))

    def test_pending_edit_is_flushed_before_quit(self):
        win = self.window(Settings(input_dir=""))
        order = []
        win.settings_changed.connect(lambda s: order.append(("settings", s.input_dir)))
        win.quit_requested.connect(lambda: order.append(("quit", None)))
        QTest.keyClicks(win.in_edit, "C:\\late")
        win.close()
        self.assertEqual(order, [("settings", "C:\\late"), ("quit", None)])


class StatusAndProblems(Base):
    def test_state_copy(self):
        win = self.window()
        win.set_running(False)
        self.assertEqual(win.title_label.fullText(), "Paused")
        self.assertEqual(win.toggle_button.text(), "Resume")
        win.set_running(True)
        self.assertEqual(win.title_label.fullText(), "Watching for HDR screenshots")
        self.assertEqual(win.toggle_button.text(), "Pause")
        win.set_converting("C:/shots/big.jxr")
        self.assertEqual(win.title_label.fullText(), "Converting big.jxr…")
        self.assertEqual(win.badge.state(), "converting")
        win.set_converting(None)
        self.assertEqual(win.badge.state(), "running")
        win.set_running(False, "The folder vanished")
        self.assertEqual(win.title_label.fullText(), "Can't watch this folder")
        self.assertEqual(win.detail_label.text(), "The folder vanished")
        self.assertEqual(win.badge.state(), "error")

    def test_unconfigured_is_idle(self):
        win = self.window(Settings())
        self.assertEqual(win.title_label.fullText(), "Choose a folder to start")
        self.assertEqual(win.toggle_button.text(), "Start")
        self.assertFalse(win.toggle_button.isEnabled())

    def test_problems_disable_resume(self):
        win = self.window()
        win.set_running(False)
        self.assertTrue(win.toggle_button.isEnabled())
        win.set_problems(Problems(input="This folder doesn't exist."))
        self.assertFalse(win.toggle_button.isEnabled())
        self.assertEqual(win.in_err.text(), "This folder doesn't exist.")
        self.assertTrue(win.in_err.isVisibleTo(win))
        win.set_problems(Problems(tool="hdrfix.exe wasn't found."))
        self.assertFalse(win.toggle_button.isEnabled())
        self.assertFalse(win.in_err.isVisibleTo(win))
        self.assertEqual(win.title_label.fullText(), "Can't convert yet")
        win.set_problems(Problems(output="Can't write there."))
        self.assertTrue(win.toggle_button.isEnabled())           # an output problem alone doesn't block watching
        self.assertEqual(win.out_err.text(), "Can't write there.")
        win.set_problems(Problems())
        self.assertFalse(win.out_err.isVisibleTo(win))

    def test_error_card_clears_when_the_folder_problem_is_fixed(self):
        win = self.window()
        win.set_problems(Problems(input="This folder doesn't exist."))
        win.set_running(False, "That folder doesn't exist.")
        self.assertEqual((win.title_label.fullText(), win.detail_label.text()), ("Can't watch this folder", "That folder doesn't exist."))
        win.set_problems(Problems())                             # the user fixed the folder
        self.assertEqual((win.title_label.fullText(), win.toggle_button.text()), ("Paused", "Resume"))
        self.assertFalse(win.detail_label.isVisibleTo(win))
        self.assertEqual(win.badge.state(), "paused")

    def test_error_card_survives_unrelated_problem_updates(self):
        win = self.window()
        win.set_running(False, "Access is denied.")              # e.g. start() failed with no validation problem
        win.set_problems(Problems())                             # arrives right after the failed restart: keep the error
        self.assertEqual(win.title_label.fullText(), "Can't watch this folder")
        win.set_problems(Problems(output="Can't write there."))
        self.assertEqual(win.detail_label.text(), "Access is denied.")

    def test_folder_edit_clears_a_stale_error_but_other_edits_do_not(self):
        win = self.window()
        got = self.record(win.settings_changed)
        win.set_running(False, "The folder vanished")
        win.bin_choice.click()                                   # not a folder edit
        self.assertEqual(win.title_label.fullText(), "Can't watch this folder")
        win.in_edit.setText("D:/other")
        win.in_edit.editingFinished.emit()
        self.assertEqual((win.title_label.fullText(), win.toggle_button.text()), ("Paused", "Resume"))
        self.assertEqual(got[-1].input_dir, "D:/other")
        win.set_running(False, "Still broken")                   # a failed restart reports after the edit and shows
        self.assertEqual(win.detail_label.text(), "Still broken")

    def test_set_running_without_error_clears_the_error_text(self):
        win = self.window()
        win.set_running(False, "boom")
        win.set_running(False, "")
        self.assertEqual(win.title_label.fullText(), "Paused")
        self.assertFalse(win.detail_label.isVisibleTo(win))
        self.assertEqual(win.detail_label.text(), "")
        win.set_running(False, "boom")
        win.set_running(True)
        self.assertEqual(win.title_label.fullText(), "Watching for HDR screenshots")

    def test_pause_stays_available_while_running(self):
        win = self.window()
        win.set_running(True)
        win.set_problems(Problems(input="gone"))
        self.assertTrue(win.toggle_button.isEnabled())

    def test_accessible_names(self):
        win = self.window()
        self.assertEqual(win.in_edit.accessibleName(), "Watch folder")
        self.assertEqual(win.out_edit.accessibleName(), "Save JPGs to")
        self.assertTrue(win.settings_button.accessibleName())

    def test_tab_order_reaches_rows_before_footer(self):
        win = self.window()
        win.add_activity(bad("a"))
        chain, w = [], win.toggle_button
        for _ in range(40):
            chain.append(w)
            w = w.nextInFocusChain()
            if w is win.toggle_button:
                break
        self.assertLess(chain.index(win._rows[0].retry_btn), chain.index(win.settings_button))
        self.assertLess(chain.index(win.bin_choice), chain.index(win._rows[0].retry_btn))


class SettingsDialogTests(unittest.TestCase):
    DEFAULT_DIR = r"C:\Users\me\AppData\Roaming\Auto HDR-SDR Converter"

    def setUp(self):
        # Nothing here touches the real Start Menu, the real settings folder or PowerShell.
        self.sc = self._patch("shortcut")
        self.sc.available.return_value = True
        self.sc.exists.return_value = False
        self.sc.create.return_value = ""
        self.sc.remove.return_value = ""
        self.cfg = self._patch("config")
        self.cfg.set_config_dir.return_value = ""
        self.pth = self._patch("paths")
        self.pth.config_dir.return_value = self.DEFAULT_DIR
        self.pth.app_data_dir.return_value = self.DEFAULT_DIR

    def _patch(self, name):
        p = mock.patch.object(settings_dialog, name)
        self.addCleanup(p.stop)
        return p.start()

    def dialog(self, settings=None, tray=True, **kw):
        d = SettingsDialog(settings or Settings(), tray, kw.get("path", ""), kw.get("open_log"))
        self.addCleanup(d.deleteLater)
        return d

    def test_tray_unavailable_disables_tray_option(self):
        d = self.dialog(Settings(minimize_to_tray=True), tray=False)
        self.assertFalse(d.tray_choice.isEnabled())
        self.assertTrue(d.quit_choice.isChecked())
        self.assertIn("isn't available", d.tray_note.text())
        d = self.dialog(Settings(minimize_to_tray=True), tray=True)
        self.assertTrue(d.tray_choice.isEnabled())
        self.assertTrue(d.tray_choice.isChecked())
        self.assertIn("Click the tray icon", d.tray_note.text())

    def test_set_settings_is_silent_and_edits_emit(self):
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        d.set_settings(Settings(notifications="off", auto_start=False, output_to_log=True, minimize_to_tray=True))
        self.assertEqual(got, [])
        self.assertTrue(d.notify_choices["off"].isChecked())
        d.auto_start.click()
        d.tray_choice.click() if not d.tray_choice.isChecked() else d.quit_choice.click()
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].auto_start)
        self.assertFalse(got[1].minimize_to_tray)

    def test_tone_picker_lists_presets_and_emits_the_key(self):
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        self.assertEqual([c.text() for c in d.tone_choices.values()], [label for label, _ in TONE_PRESETS.values()])
        self.assertEqual(list(d.tone_choices), list(TONE_PRESETS))
        d.tone_choices["bright"].click()
        d.tone_choices["natural"].click()
        self.assertEqual([s.tone_preset for s in got], ["bright", "natural"])

    def test_set_settings_tone_is_silent_and_unknown_key_is_safe(self):
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        d.set_settings(Settings(tone_preset="bright"))
        self.assertTrue(d.tone_choices["bright"].isChecked())
        d.set_settings(Settings(tone_preset="no-such-preset"))   # falls back to the first preset
        self.assertTrue(d.tone_choices["natural"].isChecked())
        self.assertEqual(got, [])

    def test_open_log_folder_callable_is_used_and_guarded(self):
        calls = []
        d = self.dialog(open_log=lambda: calls.append(1))
        d.log_button.click()
        self.assertEqual(calls, [1])
        d.set_open_log_folder(mock.Mock(side_effect=OSError("nope")))
        d.log_button.click()                                      # must not raise

    def test_about_shows_hdrfix_path_or_error(self):
        d = self.dialog(path=r"C:\tools\hdrfix.exe")
        self.assertIn(r"C:\tools\hdrfix.exe", d.hdrfix_label.fullText())
        d.set_hdrfix_path("")
        self.assertIn("wasn't found", d.hdrfix_label.fullText())

    def test_start_menu_switch_starts_from_shortcut_state(self):
        self.assertFalse(self.dialog().start_menu_switch.isChecked())
        self.sc.exists.return_value = True
        self.assertTrue(self.dialog().start_menu_switch.isChecked())

    def test_toggling_shortcut_creates_and_removes(self):
        d = self.dialog()
        d.start_menu_switch.click()
        self.sc.create.assert_called_once()
        self.assertTrue(d.start_menu_switch.isChecked())
        self.assertTrue(d.files_note.isHidden())
        d.start_menu_switch.click()
        self.sc.remove.assert_called_once()
        self.assertFalse(d.start_menu_switch.isChecked())

    def test_failed_create_reverts_switch_and_shows_error(self):
        self.sc.create.return_value = "Access is denied."
        d = self.dialog()
        d.start_menu_switch.click()
        self.assertFalse(d.start_menu_switch.isChecked())
        self.assertEqual(d.files_note.text(), "Access is denied.")
        self.assertFalse(d.files_note.isHidden())
        self.sc.remove.assert_not_called()

    def test_failed_remove_reverts_switch_and_shows_error(self):
        self.sc.exists.return_value = True
        self.sc.remove.return_value = "Couldn't remove the shortcut."
        d = self.dialog()
        d.start_menu_switch.click()
        self.assertTrue(d.start_menu_switch.isChecked())
        self.assertEqual(d.files_note.text(), "Couldn't remove the shortcut.")

    def test_success_clears_an_earlier_error(self):
        self.sc.create.side_effect = ["Access is denied.", ""]
        d = self.dialog()
        d.start_menu_switch.click()
        self.assertFalse(d.files_note.isHidden())
        d.start_menu_switch.click()
        self.assertEqual(d.files_note.text(), "")
        self.assertTrue(d.files_note.isHidden())

    def test_shortcut_unavailable_disables_switch(self):
        self.sc.available.return_value = False
        d = self.dialog()
        self.assertFalse(d.start_menu_switch.isEnabled())
        self.assertEqual(d.files_note.text(), "Available in the built app only.")
        self.assertFalse(d.files_note.isHidden())
        self.sc.create.assert_not_called()

    def test_shortcut_switch_never_emits_settings_changed(self):
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        d.start_menu_switch.click()
        d.start_menu_switch.click()
        self.sc.create.return_value = "Access is denied."
        d.start_menu_switch.click()
        self.assertEqual(got, [])

    def test_settings_folder_label_marks_default(self):
        d = self.dialog()
        self.assertEqual(d.config_dir_label.fullText(), f"Settings folder: {self.DEFAULT_DIR}  (default)")
        self.assertFalse(d.config_reset_button.isEnabled())

    def test_settings_folder_label_for_custom_folder(self):
        self.pth.config_dir.return_value = "D:/Settings/AutoHDR"
        d = self.dialog()
        self.assertEqual(d.config_dir_label.fullText(), "Settings folder: D:/Settings/AutoHDR")
        self.assertTrue(d.config_reset_button.isEnabled())

    def test_change_settings_folder_calls_set_config_dir(self):
        d = self.dialog()
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory", return_value="D:/Settings/AutoHDR") as dlg:
            d.config_change_button.click()
        self.assertEqual(dlg.call_args.args[2], self.DEFAULT_DIR)     # starts at the current folder
        self.cfg.set_config_dir.assert_called_once_with(os.path.normpath("D:/Settings/AutoHDR"))
        self.assertTrue(d.files_note.isHidden())

    def test_cancelled_folder_dialog_changes_nothing(self):
        d = self.dialog()
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory", return_value=""):
            d.config_change_button.click()
        self.cfg.set_config_dir.assert_not_called()

    def test_change_settings_folder_failure_shows_error(self):
        self.cfg.set_config_dir.return_value = "That folder can't be used."
        d = self.dialog()
        with mock.patch.object(settings_dialog.QFileDialog, "getExistingDirectory", return_value="D:/Settings"):
            d.config_change_button.click()
        self.assertEqual(d.files_note.text(), "That folder can't be used.")
        self.assertFalse(d.files_note.isHidden())

    def test_use_default_calls_set_config_dir_with_empty(self):
        self.pth.config_dir.return_value = "D:/Settings/AutoHDR"
        d = self.dialog()
        d.config_reset_button.click()
        self.cfg.set_config_dir.assert_called_once_with("")

    def test_use_default_failure_shows_error(self):
        self.pth.config_dir.return_value = "D:/Settings/AutoHDR"
        self.cfg.set_config_dir.return_value = "Can't write there."
        d = self.dialog()
        d.config_reset_button.click()
        self.assertEqual(d.files_note.text(), "Can't write there.")

    def test_successful_folder_change_re_emits_settings_so_they_are_saved_there(self):
        self.pth.config_dir.return_value = "D:/Settings/AutoHDR"
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        d.config_reset_button.click()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], d._settings)

    def test_failed_folder_change_does_not_emit_settings(self):
        self.pth.config_dir.return_value = "D:/Settings/AutoHDR"
        self.cfg.set_config_dir.return_value = "Can't write there."
        d = self.dialog()
        got = []
        d.settings_changed.connect(got.append)
        d.config_reset_button.click()
        self.assertEqual(got, [])

    def test_open_settings_folder_creates_then_opens(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "AutoHDR")
            self.pth.config_dir.return_value = folder
            d = self.dialog()
            with mock.patch.object(settings_dialog.os, "startfile", create=True) as start:
                d.config_open_button.click()
            self.assertTrue(os.path.isdir(folder))
            start.assert_called_once_with(folder)

    def test_open_settings_folder_failure_is_shown_not_raised(self):
        d = self.dialog()
        with mock.patch.object(settings_dialog.os, "startfile", create=True, side_effect=OSError("nope")):
            d.config_open_button.click()
        self.assertEqual(d.files_note.text(), "Couldn't open that folder.")


if __name__ == "__main__":
    unittest.main()
