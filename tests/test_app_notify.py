"""Tests for the Controller's toast policy and 5 s coalescing (with fakes). Run with QT_QPA_PLATFORM=offscreen."""
import time
import unittest

from tests.app_fakes import ControllerCase, result, spin  # first: sets QT_QPA_PLATFORM and sys.path

from autohdr.models import Settings  # noqa: E402

KEPT = "Converted, but the original was kept"


class TestNotifications(ControllerCase):
    def test_policy_matrix(self):
        # (mode, kind) -> toast expected?
        cases = {
            ("failures", "ok"): False, ("failures", "failed"): True, ("failures", "ok+note"): False,
            ("all", "ok"): True, ("all", "failed"): True, ("all", "ok+note"): True,
            ("off", "ok"): False, ("off", "failed"): False, ("off", "ok+note"): False,
        }
        for (mode, kind), expected in cases.items():
            with self.subTest(mode=mode, kind=kind):
                self.calls.clear()
                c = self.make(Settings(notifications=mode))
                r = {"ok": result(), "failed": result(ok=False, message="hdrfix failed"),
                     "ok+note": result(message=KEPT)}[kind]
                self.emit_result(c, r)
                self.assertEqual(len(self.toasts()), 1 if expected else 0)

    def test_toast_content(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result(ok=False, src=r"C:\shots\bad.jxr", message="hdrfix exited 1"))
        (title, body, error), = self.toasts()
        self.assertTrue(error)
        self.assertIn("bad.jxr", body)
        self.assertIn("hdrfix exited 1", body)

        self.now += 10
        self.calls.clear()
        self.emit_result(c, result(message=KEPT))
        (title, body, error), = self.toasts()
        self.assertFalse(error)
        self.assertIn("a.jxr", body)
        self.assertIn("a-sdr.jpg", body)
        self.assertIn("original was kept", body)

    def test_original_kept_note_toasts_in_all_mode_with_the_note_in_the_body(self):
        c = self.make(Settings(notifications="all"))
        r = result(message=KEPT)
        self.assertFalse(r.original_removed)
        self.emit_result(c, r)
        (title, body, error), = self.toasts()
        self.assertEqual((title, error), ("Converted", False))
        self.assertTrue(body.endswith(KEPT), body)

    def test_original_kept_note_is_silent_in_failures_mode(self):
        c = self.make(Settings(notifications="failures"))
        self.emit_result(c, result(message=KEPT))
        self.assertEqual(self.toasts(), [])
        self.assertEqual(c._pending_notes, [], "nothing is held back for later either")

    def test_note_folded_into_a_follow_up_is_not_dropped(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result())                                  # immediate toast, opens the gap
        self.emit_result(c, result(src=r"C:\shots\b.jxr", message=KEPT))
        self.emit_result(c, result(src=r"C:\shots\c.jxr", message=KEPT))   # same note twice: said once
        self.emit_result(c, result(src=r"C:\shots\d.jxr"))
        self.assertEqual(len(self.toasts()), 1)
        self.now += 5
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual(body, f"3 more converted\n{KEPT}")
        self.assertFalse(error)
        self.now += 10                                                # the note is not repeated by the next follow-up
        self.emit_result(c, result())
        self.emit_result(c, result())
        c._flush_toasts()
        self.assertEqual(self.toasts()[-1][1], "1 more converted")

    def test_note_is_kept_next_to_failures_in_a_follow_up(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result(ok=False, message="first"))
        self.emit_result(c, result(ok=False, message="again"))
        self.emit_result(c, result(message=KEPT))
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual(body, f"1 more conversion failed, 1 more converted\n{KEPT}")
        self.assertTrue(error)

    def test_unknown_mode_behaves_like_failures_only(self):
        c = self.make(Settings(notifications="bogus"))
        self.emit_result(c, result())
        self.assertEqual(self.toasts(), [])
        self.emit_result(c, result(ok=False, message="x"))
        self.assertEqual(len(self.toasts()), 1)

    def test_hidden_to_tray_toast_is_shown_once(self):
        c = self.make(Settings(notifications="off"))
        self.window.hidden_to_tray.emit()
        self.window.hidden_to_tray.emit()
        (title, body, error), = self.toasts()
        self.assertEqual(body, "Still running in the system tray")
        self.assertFalse(error)

    def test_burst_of_failures_collapses_into_one_follow_up(self):
        c = self.make()
        for _ in range(4):
            self.emit_result(c, result(ok=False, message="bad"))
            self.now += 0.5
        self.assertEqual(len(self.toasts()), 1, "only the first toast is immediate")
        self.assertTrue(c._toast_timer.isActive())
        self.now += 5
        c._flush_toasts()
        self.assertEqual(len(self.toasts()), 2)
        title, body, error = self.toasts()[1]
        self.assertEqual(body, "3 more conversions failed")
        self.assertTrue(error)

    def test_follow_up_wording_singular_and_mixed(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result())
        self.emit_result(c, result(ok=False, message="bad"))
        c._flush_toasts()
        self.assertEqual(self.toasts()[1][1], "1 more conversion failed")

        self.now += 10
        self.calls.clear()
        self.emit_result(c, result())
        self.emit_result(c, result())
        self.emit_result(c, result(ok=False, message="bad"))
        self.emit_result(c, result())
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual(body, "1 more conversion failed, 2 more converted")
        self.assertTrue(error)

    def test_successes_only_follow_up_is_not_an_error(self):
        c = self.make(Settings(notifications="all"))
        for _ in range(3):
            self.emit_result(c, result())
        c._flush_toasts()
        title, body, error = self.toasts()[1]
        self.assertEqual((body, error), ("2 more converted", False))

    def test_follow_up_restarts_the_gap(self):
        c = self.make()
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.now += 6
        c._flush_toasts()
        self.emit_result(c, result(ok=False, message="c"))   # right after the follow-up: pending again
        self.assertEqual(len(self.toasts()), 2)
        self.now += 6
        self.emit_result(c, result(ok=False, message="d"))   # gap has passed but "c" is still pending: stay ordered
        self.assertEqual(len(self.toasts()), 2)
        c._flush_toasts()
        self.assertEqual(self.toasts()[2][1], "2 more conversions failed")

    def test_pending_toasts_are_dropped_if_notifications_get_turned_off(self):
        c = self.make()
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.window.settings_changed.emit(Settings(notifications="off"))
        c._flush_toasts()
        self.assertEqual(len(self.toasts()), 1)

    def test_follow_up_fires_from_the_real_timer(self):
        c = self.make()
        c.TOAST_GAP_S = 0.05
        c._clock = time.monotonic
        c._last_toast = float("-inf")
        self.emit_result(c, result(ok=False, message="a"))
        self.emit_result(c, result(ok=False, message="b"))
        self.assertTrue(spin(lambda: len(self.toasts()) == 2))
        self.assertEqual(self.toasts()[1][1], "1 more conversion failed")

    def test_clicking_a_toast_opens_its_folder(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result(ok=False, src=r"C:\shots\bad.jxr", message="x"))
        self.tray.message_clicked.emit()
        self.now += 10
        self.emit_result(c, result(src=r"C:\shots\a.jxr", dst=r"C:\out\a-sdr.jpg"))
        self.tray.message_clicked.emit()
        self.assertEqual(self.opened, [r"C:\shots", r"C:\out"])

    def test_clicking_the_tray_hint_opens_nothing_and_missing_opener_is_fine(self):
        c = self.make(Settings(notifications="all"))
        self.emit_result(c, result())
        self.window.hidden_to_tray.emit()
        self.tray.message_clicked.emit()
        self.assertEqual(self.opened, [])
        c2 = self.make(open_folder=False)
        c2._toast_folder = r"C:\x"
        self.tray.message_clicked.emit()   # must not raise


if __name__ == "__main__":
    unittest.main()
