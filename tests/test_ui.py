"""UI smoke tests (offscreen). Run: .venv\\Scripts\\python.exe -m unittest tests.test_ui -v"""
import os
import sys
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

from autohdr.models import ConversionResult, Problems, Settings  # noqa: E402
from autohdr.ui import icons, main_window, theme  # noqa: E402
from autohdr.ui.main_window import MainWindow, clean_path, elide_path, relative_time  # noqa: E402
from autohdr.ui.settings_dialog import SettingsDialog  # noqa: E402

app = None


def setUpModule():
    global app
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "light")


def ok(name="a", ago=0.0, removed=False):
    return ConversionResult(True, f"C:/shots/{name}.jxr", f"C:/shots/{name}-sdr.jpg", "", removed, time.time() - ago)


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


class Helpers(unittest.TestCase):
    def test_clean_path(self):
        self.assertEqual(clean_path('  "C:\\My Pics"  '), "C:\\My Pics")
        self.assertEqual(clean_path("  C:\\x "), "C:\\x")
        self.assertEqual(clean_path('"'), '"')
        self.assertEqual(clean_path(""), "")

    def test_relative_time(self):
        self.assertEqual(relative_time(100, 101), "just now")
        self.assertEqual(relative_time(100, 112), "12s ago")
        self.assertEqual(relative_time(0, 300), "5 min ago")
        self.assertEqual(relative_time(0, 7300), "2 h ago")
        self.assertEqual(relative_time(0, 90000), "1 d ago")
        self.assertEqual(relative_time(200, 100), "just now")      # clock skew never goes negative

    def test_elide_path(self):
        self.assertEqual(elide_path("C:\\short"), "C:\\short")
        long = "C:\\Users\\Someone\\Pictures\\Screenshots\\Games\\Flight Sim\\2026"
        out = elide_path(long, 30)
        self.assertEqual(len(out), 30)
        self.assertIn("…", out)
        self.assertTrue(out.endswith("2026"))


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

    def test_retry_emits_source_and_replaces_old_failure(self):
        win = self.window()
        got = self.record(win.retry_requested)
        win.add_activity(bad("x"))
        row = win._rows[0]
        row.retry_btn.click()
        self.assertEqual(got, ["C:/shots/x.jxr"])
        self.assertFalse(row.retry_btn.isEnabled())             # no double-click while the retry is queued
        win.add_activity(ok("x"))                                # retry succeeded: the stale failed row goes away
        self.assertEqual([r.result.ok for r in win._rows], [True, False])
        win.add_activity(bad("x"))                               # another failure replaces the previous failure
        self.assertEqual(sum(1 for r in win._rows if not r.result.ok), 1)

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


class ActivityList(Base):
    def test_newest_first_and_counter(self):
        win = self.window()
        self.assertEqual(win.counter_label.text(), "0 converted this session")
        self.assertIs(win._stack.currentWidget().__class__.__name__ != "QScrollArea", True)   # empty state
        win.add_activity(ok("first"))
        win.add_activity(bad("second"))
        win.add_activity(ok("third"))
        self.assertEqual([os.path.basename(r.result.src) for r in win._rows], ["third.jxr", "second.jxr", "first.jxr"])
        self.assertEqual(win._rows_layout.itemAt(0).widget(), win._rows[0])
        self.assertEqual(win.counter_label.text(), "2 converted this session")      # failures are not conversions
        self.assertIs(win._stack.currentWidget(), win.scroll)

    def test_keeps_last_100_rows_but_counts_everything(self):
        win = self.window()
        for i in range(130):
            win.add_activity(ok(f"f{i}"))
        self.assertEqual(len(win._rows), 100)
        self.assertEqual(win._rows_layout.count(), 101)                              # rows + trailing stretch
        self.assertEqual(os.path.basename(win._rows[0].result.src), "f129.jxr")
        self.assertEqual(os.path.basename(win._rows[-1].result.src), "f30.jxr")
        self.assertEqual(win.counter_label.text(), "130 converted this session")

    def test_row_actions(self):
        win = self.window()
        win.add_activity(ok("pic", removed=True))
        row = win._rows[0]
        self.assertEqual(row.show_btn.accessibleName(), "Show in folder")
        self.assertEqual(row.open_btn.accessibleName(), "Open image")
        self.assertIn("Recycle Bin", " ".join(w.text() for w in row.findChildren(main_window.QLabel)))
        with mock.patch.object(main_window.subprocess, "Popen") as popen, \
                mock.patch.object(main_window.os, "startfile", create=True) as start:
            row.show_btn.click()
            row.open_btn.click()
        self.assertEqual(popen.call_args.args[0], 'explorer /select,"' + os.path.normpath("C:/shots/pic-sdr.jpg") + '"')
        start.assert_called_once_with("C:/shots/pic-sdr.jpg")

    def test_row_actions_never_raise(self):
        with mock.patch.object(main_window.subprocess, "Popen", side_effect=OSError("boom")), \
                mock.patch.object(main_window.os, "startfile", create=True, side_effect=OSError("boom")):
            main_window._show_in_folder("C:/x.jpg")
            main_window._open_file("C:/x.jpg")

    def test_failed_row_shows_reason_and_retry(self):
        win = self.window()
        win.add_activity(bad("broken", "hdrfix failed: unsupported"))
        row = win._rows[0]
        self.assertEqual(row.detail_label.fullText(), "hdrfix failed: unsupported")
        self.assertIsNotNone(row.retry_btn)
        self.assertIsNone(row.show_btn)

    def test_relative_times_refresh(self):
        win = self.window()
        win.add_activity(ok("t"))
        row = win._rows[0]
        self.assertEqual(row.time_label.text(), "just now")
        row.refresh_time(row.result.finished_at + 300)
        self.assertEqual(row.time_label.text(), "5 min ago")
        win.refresh_times()                                      # public hook used by the 30 s timer
        self.assertEqual(row.time_label.text(), "just now")

    def test_long_names_elide_instead_of_widening(self):
        win = self.window()
        width = win.width()
        win.add_activity(ok("s"))
        win.add_activity(ok("n" * 200))
        app.processEvents()
        long_row, short_row = win._rows
        self.assertEqual(win.width(), width)
        self.assertEqual(long_row.minimumSizeHint().width(), short_row.minimumSizeHint().width())   # names can't force width
        self.assertIn("…", long_row.src_label.text())
        self.assertEqual(long_row.src_label.fullText(), "n" * 200 + ".jxr")


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


if __name__ == "__main__":
    unittest.main()
