"""Click-through preview of the real main window with fake data (no watching, no conversion).

    .venv\\Scripts\\python.exe tools\\ui_preview.py [--scheme light|dark] [--no-tray]

The small "Preview controls" window switches the status card between states, adds fake activity rows and flips
light/dark. The main window's own buttons work too: Pause/Resume toggles, Retry converts "successfully" after a moment.
"""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QPushButton, QWidget  # noqa: E402

from autohdr.models import ConversionResult, Problems, Settings  # noqa: E402
from autohdr.ui import icons, theme  # noqa: E402
from autohdr.ui.main_window import MainWindow  # noqa: E402

SHOTS = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
NAMES = ["MSFS_2026-11-23_21-04-55", "Starfield_001", "Cyberpunk2077_night", "Forza_Horizon_sunset", "ACC_Spa_rain",
         "Cockpit_view_final_cinematic_extra_long_name_2026", "DCS_F18_carrier", "Hades2_boss"]
REASONS = ["hdrfix failed: unsupported pixel format", "The file never finished saving", "Timed out after 120 s"]


class Preview:
    def __init__(self, app: QApplication, scheme: str | None, tray: bool):
        self.app, self.tray = app, tray
        self.settings = Settings(input_dir=SHOTS, output_dir="", minimize_to_tray=tray)
        self.running, self.problems, self.error, self.converting = True, Problems(), "", None
        self.window = None
        self._new_window()
        self._controls(scheme)

    # -- window ---------------------------------------------------------------------------------------------

    def _new_window(self) -> None:
        old, geometry = self.window, self.window.geometry() if self.window else None
        w = MainWindow(self.settings, self.tray)
        w.set_hdrfix_path(r"C:\Program Files\AutoHDRConvert\hdrfix.exe")
        w.toggle_requested.connect(self._toggle)
        w.retry_requested.connect(self._retry)
        w.settings_changed.connect(self._settings_changed)
        w.quit_requested.connect(self.app.quit)
        w.hidden_to_tray.connect(lambda: print("hidden to tray: the window would reappear on a tray click"))
        self.window = w
        if geometry:
            w.setGeometry(geometry)
        self._apply_state()
        w.show()
        if old:
            old.hide()
            old.deleteLater()

    def _apply_state(self) -> None:
        self.window.set_problems(self.problems)
        self.window.set_running(self.running, self.error)
        self.window.set_converting(self.converting)

    def _state(self, running=True, error="", problems=None, converting=None, settings=None) -> None:
        self.running, self.error, self.converting = running, error, converting
        self.problems = problems or Problems()
        if settings is not None:
            self.settings = settings
            self.window.set_settings(settings)
        self._apply_state()

    def _toggle(self) -> None:
        self._state(running=not self.running, problems=self.problems)

    def _settings_changed(self, s: Settings) -> None:
        self.settings = s
        print("settings_changed:", s)

    def _retry(self, src: str) -> None:
        self.window.set_converting(src)
        QTimer.singleShot(1200, lambda: (self.window.set_converting(None), self.window.add_activity(self._ok(src))))

    # -- fake data ------------------------------------------------------------------------------------------

    def _ok(self, src=None, ago=0.0) -> ConversionResult:
        src = src or os.path.join(SHOTS, random.choice(NAMES) + ".jxr")
        return ConversionResult(True, src, os.path.splitext(src)[0] + "-sdr.jpg", "", self.settings.delete_original, time.time() - ago)

    def _failed(self, ago=0.0) -> ConversionResult:
        return ConversionResult(False, os.path.join(SHOTS, random.choice(NAMES) + ".jxr"), "", random.choice(REASONS), False, time.time() - ago)

    def _sample(self) -> None:
        for r in (self._ok(ago=26000), self._ok(ago=3000), self._failed(ago=900), self._ok(ago=300), self._ok(ago=40), self._ok(ago=1)):
            self.window.add_activity(r)

    def _many(self) -> None:
        for i in range(120):
            self.window.add_activity(self._failed(i * 40.0) if i % 9 == 4 else self._ok(ago=i * 40.0))

    def _clear(self) -> None:
        self._new_window()

    # -- controls -------------------------------------------------------------------------------------------

    def _controls(self, scheme) -> None:
        p = QWidget()
        p.setWindowTitle("Preview controls")
        p.setWindowIcon(icons.app_icon())
        g = QGridLayout(p)
        g.setContentsMargins(16, 16, 16, 16)
        g.setSpacing(8)
        items = [
            ("Status", [
                ("Watching", lambda: self._state()),
                ("Converting", lambda: self._state(converting=os.path.join(SHOTS, NAMES[0] + ".jxr"))),
                ("Paused", lambda: self._state(running=False)),
                ("Can't watch", lambda: self._state(running=False, error="The folder was removed or is no longer accessible.")),
                ("No hdrfix", lambda: self._state(running=False, problems=Problems(tool="hdrfix.exe wasn't found. Put it next to the app or add it to PATH."))),
                ("No folder", lambda: self._state(running=False, settings=Settings(), problems=Problems(input="Choose a folder to watch."))),
                ("Bad folders", lambda: self._state(running=False, settings=Settings(input_dir=r"C:\Nope\Missing", output_dir=r"D:\Locked\Out"),
                                                    problems=Problems(input="This folder doesn't exist.", output="Can't write to this folder."))),
                ("Good folders", lambda: self._state(settings=Settings(input_dir=SHOTS, minimize_to_tray=self.tray))),
            ]),
            ("Activity", [
                ("Add success", lambda: self.window.add_activity(self._ok())),
                ("Add failure", lambda: self.window.add_activity(self._failed())),
                ("Sample list", self._sample),
                ("120 rows", self._many),
                ("Clear", self._clear),
            ]),
            ("Theme and dialogs", [
                ("Light", lambda: theme.apply(self.app, "light")),
                ("Dark", lambda: theme.apply(self.app, "dark")),
                ("Follow Windows", lambda: theme.apply(self.app)),
                ("Settings dialog", lambda: self.window.open_settings()),
                ("Show window", lambda: self.window.show_and_raise()),
            ]),
        ]
        row = 0
        for title, buttons in items:
            lab = QLabel(title)
            lab.setProperty("role", "section")
            g.addWidget(lab, row, 0, 1, 4)
            row += 1
            for i, (text, fn) in enumerate(buttons):
                b = QPushButton(text)
                b.clicked.connect(lambda _=False, fn=fn: fn())
                g.addWidget(b, row + i // 4, i % 4)
            row += (len(buttons) + 3) // 4
        self.controls = p
        theme.apply(self.app, scheme)
        p.show()
        p.move(self.window.frameGeometry().right() + 16, self.window.y())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scheme", choices=("light", "dark"), help="start in this scheme (default: follow Windows)")
    ap.add_argument("--no-tray", action="store_true", help="pretend the system tray is unavailable")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    theme.apply(app, args.scheme)
    app.setWindowIcon(icons.app_icon())
    preview = Preview(app, args.scheme, tray=not args.no_tray)   # noqa: F841 (kept alive by the event loop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
