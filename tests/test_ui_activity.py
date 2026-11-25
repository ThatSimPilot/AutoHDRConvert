"""Activity list/row tests (offscreen). Run: .venv\\Scripts\\python.exe -m unittest tests.test_ui_activity -v"""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))   # offscreen has no fonts by default
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from autohdr.models import ConversionResult, Settings  # noqa: E402
from autohdr.ui import activity, theme  # noqa: E402
from autohdr.ui.activity import ActivityList, elide_path, relative_time  # noqa: E402
from autohdr.ui.main_window import MainWindow  # noqa: E402

app = None
KEPT = "Converted, but the original was kept (Recycle Bin isn't available on that drive)"


def setUpModule():
    global app
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "light")


def ok(name="a", ago=0.0, removed=False, msg=""):
    return ConversionResult(True, f"C:/shots/{name}.jxr", f"C:/shots/{name}-sdr.jpg", msg, removed, time.time() - ago)


def bad(name="a", msg="hdrfix failed"):
    return ConversionResult(False, f"C:/shots/{name}.jxr", "", msg, False, time.time())


class Base(unittest.TestCase):
    def window(self, settings=None):
        win = MainWindow(settings or Settings(input_dir="C:/shots"), True)
        self.addCleanup(self._dispose, win)
        win.show()
        return win

    @staticmethod
    def _dispose(win):
        win.hide()
        win.deleteLater()
        app.processEvents()


class Helpers(unittest.TestCase):
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


class ActivityListTests(Base):
    def test_newest_first_and_counter(self):
        win = self.window()
        self.assertEqual(win.counter_label.text(), "0 converted this session")
        self.assertIsNot(win.activity._stack.currentWidget(), win.activity.scroll)    # empty state
        win.add_activity(ok("first"))
        win.add_activity(bad("second"))
        win.add_activity(ok("third"))
        self.assertEqual([os.path.basename(r.result.src) for r in win._rows], ["third.jxr", "second.jxr", "first.jxr"])
        self.assertEqual(win.activity._rows_layout.itemAt(0).widget(), win._rows[0])
        self.assertEqual(win.counter_label.text(), "2 converted this session")      # failures are not conversions
        self.assertIs(win.activity._stack.currentWidget(), win.activity.scroll)

    def test_keeps_last_100_rows_but_counts_everything(self):
        win = self.window()
        for i in range(130):
            win.add_activity(ok(f"f{i}"))
        self.assertEqual(len(win._rows), 100)
        self.assertEqual(win.activity._rows_layout.count(), 101)                     # rows + trailing stretch
        self.assertEqual(os.path.basename(win._rows[0].result.src), "f129.jxr")
        self.assertEqual(os.path.basename(win._rows[-1].result.src), "f30.jxr")
        self.assertEqual(win.counter_label.text(), "130 converted this session")

    def test_empty_state_names_the_watch_folder(self):
        lst = ActivityList()
        self.addCleanup(lst.deleteLater)
        lst.set_folder("")
        self.assertIn("in the watch folder", lst.empty_text.text())
        lst.set_folder("C:/shots")
        self.assertIn("C:/shots", lst.empty_text.text())

    def test_row_actions(self):
        win = self.window()
        win.add_activity(ok("pic", removed=True))
        row = win._rows[0]
        self.assertEqual(row.show_btn.accessibleName(), "Show in folder")
        self.assertEqual(row.open_btn.accessibleName(), "Open image")
        self.assertIn("Recycle Bin", " ".join(w.text() for w in row.findChildren(QLabel)))
        with mock.patch.object(activity.subprocess, "Popen") as popen, \
                mock.patch.object(activity.os, "startfile", create=True) as start:
            row.show_btn.click()
            row.open_btn.click()
        self.assertEqual(popen.call_args.args[0], 'explorer /select,"' + os.path.normpath("C:/shots/pic-sdr.jpg") + '"')
        start.assert_called_once_with("C:/shots/pic-sdr.jpg")

    def test_row_actions_never_raise(self):
        with mock.patch.object(activity.subprocess, "Popen", side_effect=OSError("boom")), \
                mock.patch.object(activity.os, "startfile", create=True, side_effect=OSError("boom")):
            activity._show_in_folder("C:/x.jpg")
            activity._open_file("C:/x.jpg")

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
        win.add_activity(ok("n" * 200, msg=KEPT * 3))
        app.processEvents()
        long_row, short_row = win._rows
        self.assertEqual(win.width(), width)
        self.assertEqual(long_row.minimumSizeHint().width(), short_row.minimumSizeHint().width())   # names/notes can't force width
        self.assertIn("…", long_row.src_label.text())
        self.assertEqual(long_row.src_label.fullText(), "n" * 200 + ".jxr")


class OneRowPerSource(Base):
    def test_retry_emits_source_and_a_success_replaces_the_failed_row(self):
        win = self.window()
        got = []
        win.retry_requested.connect(got.append)
        win.add_activity(bad("x"))
        row = win._rows[0]
        row.retry_btn.click()
        self.assertEqual(got, ["C:/shots/x.jxr"])
        self.assertFalse(row.retry_btn.isEnabled())             # no double-click while the retry is queued
        win.add_activity(ok("x"))                                # retry succeeded: the stale failed row goes away
        self.assertEqual([r.result.ok for r in win._rows], [True])
        self.assertEqual(win.activity._rows_layout.count(), 2)   # the row + trailing stretch: nothing left behind
        self.assertEqual(win.counter_label.text(), "1 converted this session")

    def test_failure_after_failure_still_keeps_one_failed_row(self):
        win = self.window()
        win.add_activity(bad("x", "first"))
        win.add_activity(bad("x", "second"))
        self.assertEqual([(r.result.ok, r.result.message) for r in win._rows], [(False, "second")])
        win.add_activity(ok("x"))
        win.add_activity(bad("x", "third"))                      # a later failure of the same file is a new, separate outcome
        self.assertEqual([r.result.ok for r in win._rows], [False, True])

    def test_success_only_replaces_failures_of_the_same_source(self):
        win = self.window()
        win.add_activity(bad("a"))
        win.add_activity(bad("b"))
        win.add_activity(ok("c"))
        win.add_activity(ok("b"))
        self.assertEqual([(os.path.basename(r.result.src), r.result.ok) for r in win._rows],
                         [("b.jxr", True), ("c.jxr", True), ("a.jxr", False)])
        self.assertEqual(win.counter_label.text(), "2 converted this session")

    def test_cap_and_order_hold_with_replacements(self):
        win = self.window()
        win.add_activity(bad("retry-me"))
        for i in range(99):
            win.add_activity(ok(f"f{i}"))
        self.assertEqual(len(win._rows), 100)                    # failed row is still the oldest of exactly 100
        win.add_activity(ok("retry-me"))                         # replaces it: 100 stay 100, nothing else is evicted
        self.assertEqual(len(win._rows), 100)
        self.assertEqual(os.path.basename(win._rows[0].result.src), "retry-me.jxr")
        self.assertEqual(os.path.basename(win._rows[-1].result.src), "f0.jxr")
        self.assertEqual(win.activity._rows_layout.count(), 101)

    def test_tab_order_has_no_stale_row(self):
        win = self.window()
        win.add_activity(bad("x"))
        stale = win._rows[0].retry_btn
        win.add_activity(ok("x"))
        self.assertNotIn(stale, win.activity.focus_widgets())
        self.assertEqual(len(win.activity.focus_widgets()), 2)   # show + open of the one remaining row


class KeptOriginalNote(Base):
    def test_message_shows_in_row_and_tooltip(self):
        win = self.window()
        win.add_activity(ok("pic", msg=KEPT))
        row = win._rows[0]
        self.assertEqual(row.note_label.text(), KEPT)               # wraps (never elides) so the reason stays readable
        self.assertIn("Original kept", " ".join(w.text() for w in row.findChildren(QLabel)))
        self.assertIn(KEPT, row.toolTip())
        self.assertIn(KEPT, row.accessibleName())
        self.assertTrue(row.note_label.isVisibleTo(win))

    def test_plain_success_has_no_note(self):
        win = self.window()
        win.add_activity(ok("pic", removed=True))
        win.add_activity(ok("pic2"))
        for row in win._rows:
            self.assertIsNone(row.note_label)
            self.assertEqual(row.toolTip().count("\n"), 1)       # just "src\n→ dst"

    def test_note_never_widens_the_window(self):
        win = self.window()
        win.add_activity(ok("short"))
        app.processEvents()
        base = win._rows[0].minimumSizeHint().width()
        win.add_activity(ok("pic", msg=KEPT * 4))
        app.processEvents()
        self.assertEqual(win._rows[0].minimumSizeHint().width(), base)
        self.assertGreater(win._rows[0].height(), win._rows[1].height())     # the note is its own line


if __name__ == "__main__":
    unittest.main()
