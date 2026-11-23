"""Settings dialog: close behaviour, notifications, start-up/log options and the About block."""
import dataclasses
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from autohdr.models import Settings

from .icons import Choice, ElidedLabel, Switch, make_button

HDRFIX_URL = "https://github.com/bvibber/hdrfix"
APP_NAME = "Auto HDR-SDR Converter"


def _version() -> str:
    try:
        from autohdr import __version__
        return __version__
    except Exception:
        return ""


def _label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def _card(parent_layout: QVBoxLayout, heading: str) -> QVBoxLayout:
    parent_layout.addWidget(_label(heading, "section"))
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(0)
    parent_layout.addWidget(frame)
    return lay


def _divider(lay: QVBoxLayout) -> None:
    d = QFrame()
    d.setObjectName("cardDivider")
    d.setFixedHeight(1)
    lay.addSpacing(4)
    lay.addWidget(d)
    lay.addSpacing(4)


class SettingsDialog(QDialog):
    settings_changed = Signal(object)       # full Settings; the main window takes the fields this dialog owns

    def __init__(self, settings: Settings, tray_available: bool, hdrfix_path: str = "",
                 open_log_folder: Callable[[], None] | None = None, parent=None):
        super().__init__(parent)
        self._settings = dataclasses.replace(settings)
        self._tray_available = tray_available
        self._open_log_folder = open_log_folder
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 20)
        col.setSpacing(8)

        lay = _card(col, "When you close the window")
        self.quit_choice = Choice("Quit the app")
        self.tray_choice = Choice("Keep running in the system tray")
        self._close_group = QButtonGroup(self)
        for c in (self.quit_choice, self.tray_choice):
            self._close_group.addButton(c)
            lay.addWidget(c)
        self.tray_note = _label("Click the tray icon to open the window again." if tray_available
                                else "The system tray isn't available on this system.", "caption", wrap=True)
        self.tray_note.setContentsMargins(36, 0, 8, 6)
        lay.addWidget(self.tray_note)
        self.tray_choice.setEnabled(tray_available)

        col.addSpacing(8)
        lay = _card(col, "Notifications")
        self._notify_group = QButtonGroup(self)
        self.notify_choices = {}
        for value, text in (("failures", "Failures only"), ("all", "All conversions"), ("off", "Off")):
            c = Choice(text)
            self.notify_choices[value] = c
            self._notify_group.addButton(c)
            lay.addWidget(c)

        col.addSpacing(8)
        lay = _card(col, "General")
        self.auto_start = Switch("Start watching when the app opens")
        self.log_switch = Switch("Write a log file")
        lay.addWidget(self.auto_start)
        _divider(lay)
        lay.addWidget(self.log_switch)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 8, 6)
        self.log_button = make_button("Open log folder", small=True, name="Open log folder")
        self.log_button.clicked.connect(self._on_open_log)
        row.addWidget(self.log_button)
        row.addStretch(1)
        lay.addLayout(row)

        col.addSpacing(8)
        lay = _card(col, "About")
        about = QVBoxLayout()
        about.setContentsMargins(10, 4, 10, 6)
        about.setSpacing(4)
        v = _version()
        about.addWidget(_label(f"{APP_NAME}   {v}" if v else APP_NAME, "section"))
        self.hdrfix_label = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        about.addWidget(self.hdrfix_label)
        credit = _label(f'Conversion by HDRFix, by Brooke Vibber<br><a href="{HDRFIX_URL}">github.com/bvibber/hdrfix</a>',
                        "secondary", wrap=True)
        credit.setOpenExternalLinks(True)
        credit.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        about.addWidget(credit)
        lay.addLayout(about)

        col.addSpacing(8)
        foot = QHBoxLayout()
        foot.addStretch(1)
        done = make_button("Done", primary=True)
        done.setMinimumWidth(96)
        done.clicked.connect(self.accept)
        done.setDefault(True)
        foot.addWidget(done)
        col.addLayout(foot)

        for c in (self.quit_choice, self.tray_choice, *self.notify_choices.values(), self.auto_start, self.log_switch):
            c.toggled.connect(self._edited)
        self.set_hdrfix_path(hdrfix_path)
        self.set_settings(settings)

    # -- app -> dialog ---------------------------------------------------------------------------------------

    def set_settings(self, s: Settings) -> None:
        """Populate without emitting."""
        self._settings = dataclasses.replace(s)
        widgets = (self.quit_choice, self.tray_choice, *self.notify_choices.values(), self.auto_start, self.log_switch)
        for w in widgets:
            w.blockSignals(True)
        on_tray = s.minimize_to_tray and self._tray_available
        self.tray_choice.setChecked(on_tray)
        self.quit_choice.setChecked(not on_tray)
        self.notify_choices.get(s.notifications, self.notify_choices["failures"]).setChecked(True)
        self.auto_start.setChecked(s.auto_start)
        self.log_switch.setChecked(s.output_to_log)
        for w in widgets:
            w.blockSignals(False)
            w.update()

    def set_hdrfix_path(self, path: str) -> None:
        if path:
            self.hdrfix_label.setFullText(f"hdrfix: {path}")
            self.hdrfix_label.setProperty("role", "secondary")
            self.hdrfix_label.setToolTip(path)
        else:
            self.hdrfix_label.setFullText("hdrfix.exe wasn't found. Put it next to the app or add it to PATH.")
            self.hdrfix_label.setProperty("role", "error")
            self.hdrfix_label.setToolTip("")
        self.hdrfix_label.style().unpolish(self.hdrfix_label)
        self.hdrfix_label.style().polish(self.hdrfix_label)

    def set_open_log_folder(self, fn: Callable[[], None]) -> None:
        self._open_log_folder = fn

    # -- dialog -> app ---------------------------------------------------------------------------------------

    def _collect(self) -> Settings:
        mode = next((k for k, c in self.notify_choices.items() if c.isChecked()), self._settings.notifications)
        return dataclasses.replace(
            self._settings, minimize_to_tray=self.tray_choice.isChecked(), notifications=mode,
            auto_start=self.auto_start.isChecked(), output_to_log=self.log_switch.isChecked())

    def _edited(self, *_) -> None:
        new = self._collect()
        if new != self._settings:
            self._settings = new
            self.settings_changed.emit(dataclasses.replace(new))

    def _on_open_log(self) -> None:
        if self._open_log_folder:
            try:
                self._open_log_folder()
            except Exception:
                pass
