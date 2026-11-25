"""Settings dialog: close behaviour, notifications, start-up/log options, files and shortcut, and the About block."""
import dataclasses
import os
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from autohdr import config, paths, shortcut
from autohdr.models import TONE_PRESETS, Settings
from autohdr.paths import log_dir

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
        lay = _card(col, "Tone")
        self._tone_group = QButtonGroup(self)
        self.tone_choices = {}
        for key, (text, _args) in TONE_PRESETS.items():
            c = Choice(text)
            self.tone_choices[key] = c
            self._tone_group.addButton(c)
            lay.addWidget(c)
        tone_note = _label("Changes apply to the next converted file.", "caption", wrap=True)
        tone_note.setContentsMargins(36, 0, 8, 6)
        lay.addWidget(tone_note)

        col.addSpacing(8)
        lay = _card(col, "General")
        self.auto_start = Switch("Start watching when the app opens")
        self.log_switch = Switch("Write a log file")
        lay.addWidget(self.auto_start)
        _divider(lay)
        lay.addWidget(self.log_switch)
        self.log_dir_label = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.log_dir_label.setProperty("role", "secondary")
        self.log_dir_label.setContentsMargins(10, 4, 8, 4)
        lay.addWidget(self.log_dir_label)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 8, 6)
        self.log_button = make_button("Open log folder", small=True, name="Open log folder")
        self.log_button.clicked.connect(self._on_open_log)
        self.log_change_button = make_button("Change…", small=True, name="Change log folder")
        self.log_change_button.clicked.connect(self._on_change_log_dir)
        self.log_reset_button = make_button("Use default", small=True, name="Use default log folder")
        self.log_reset_button.clicked.connect(self._on_reset_log_dir)
        for b in (self.log_button, self.log_change_button, self.log_reset_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        col.addSpacing(8)
        lay = _card(col, "Files and shortcut")
        self.start_menu_switch = Switch("Start menu shortcut")
        self.start_menu_switch.setChecked(shortcut.exists())
        lay.addWidget(self.start_menu_switch)
        self.files_note = _label("", "caption", wrap=True)
        self.files_note.setContentsMargins(10, 0, 8, 4)
        self.files_note.hide()
        lay.addWidget(self.files_note)
        _divider(lay)
        self.config_dir_label = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.config_dir_label.setProperty("role", "secondary")
        self.config_dir_label.setContentsMargins(10, 4, 8, 4)
        lay.addWidget(self.config_dir_label)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 8, 6)
        self.config_open_button = make_button("Open settings folder", small=True, name="Open settings folder")
        self.config_open_button.clicked.connect(self._on_open_config_dir)
        self.config_change_button = make_button("Change…", small=True, name="Change settings folder")
        self.config_change_button.clicked.connect(self._on_change_config_dir)
        self.config_reset_button = make_button("Use default", small=True, name="Use default settings folder")
        self.config_reset_button.clicked.connect(self._on_reset_config_dir)
        for b in (self.config_open_button, self.config_change_button, self.config_reset_button):
            row.addWidget(b)
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

        self._controls = (self.quit_choice, self.tray_choice, *self.notify_choices.values(),
                          *self.tone_choices.values(), self.auto_start, self.log_switch)
        for c in self._controls:
            c.toggled.connect(self._edited)
        self.start_menu_switch.toggled.connect(self._on_start_menu)
        if not shortcut.available():
            self.start_menu_switch.setEnabled(False)
            self._show_note("Available in the built app only.", error=False)
        self._show_config_dir()
        self.set_hdrfix_path(hdrfix_path)
        self.set_settings(settings)

    # -- app -> dialog ---------------------------------------------------------------------------------------

    def set_settings(self, s: Settings) -> None:
        """Populate without emitting."""
        self._settings = dataclasses.replace(s)
        for w in self._controls:
            w.blockSignals(True)
        on_tray = s.minimize_to_tray and self._tray_available
        self.tray_choice.setChecked(on_tray)
        self.quit_choice.setChecked(not on_tray)
        self.notify_choices.get(s.notifications, self.notify_choices["failures"]).setChecked(True)
        self.tone_choices.get(s.tone_preset, next(iter(self.tone_choices.values()))).setChecked(True)
        self.auto_start.setChecked(s.auto_start)
        self.log_switch.setChecked(s.output_to_log)
        self._show_log_dir(s.log_dir)
        for w in self._controls:
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
        tone = next((k for k, c in self.tone_choices.items() if c.isChecked()), self._settings.tone_preset)
        return dataclasses.replace(
            self._settings, minimize_to_tray=self.tray_choice.isChecked(), notifications=mode, tone_preset=tone,
            auto_start=self.auto_start.isChecked(), output_to_log=self.log_switch.isChecked())

    def _edited(self, *_) -> None:
        new = self._collect()
        if new != self._settings:
            self._settings = new
            self.settings_changed.emit(dataclasses.replace(new))

    def _show_log_dir(self, configured: str) -> None:
        folder = log_dir(configured)
        self.log_dir_label.setFullText(f"Log folder: {folder}" + ("" if configured else "  (default)"))
        self.log_dir_label.setToolTip(folder)
        self.log_reset_button.setEnabled(bool(configured))

    def _set_log_dir(self, configured: str) -> None:
        if configured == self._settings.log_dir:
            return
        self._settings = dataclasses.replace(self._settings, log_dir=configured)
        self._show_log_dir(configured)
        self.settings_changed.emit(dataclasses.replace(self._settings))

    def _on_change_log_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a log folder", log_dir(self._settings.log_dir))
        if folder:
            self._set_log_dir(os.path.normpath(folder))

    def _on_reset_log_dir(self) -> None:
        self._set_log_dir("")

    def _on_open_log(self) -> None:
        if self._open_log_folder:
            try:
                self._open_log_folder()
            except Exception:
                pass

    def _show_note(self, text: str, error: bool = True) -> None:
        self.files_note.setText(text)
        self.files_note.setProperty("role", "error" if error else "caption")
        self.files_note.style().unpolish(self.files_note)
        self.files_note.style().polish(self.files_note)
        self.files_note.setVisible(bool(text))

    def _on_start_menu(self, checked: bool) -> None:
        err = shortcut.create() if checked else shortcut.remove()
        if err:
            self.start_menu_switch.blockSignals(True)
            self.start_menu_switch.setChecked(not checked)
            self.start_menu_switch.blockSignals(False)
            self.start_menu_switch.update()
            self._show_note(err)
        else:
            self._show_note("")

    def _show_config_dir(self) -> None:
        folder = paths.config_dir()
        is_default = os.path.normpath(folder).lower() == os.path.normpath(paths.app_data_dir()).lower()
        self.config_dir_label.setFullText(f"Settings folder: {folder}" + ("  (default)" if is_default else ""))
        self.config_dir_label.setToolTip(folder)
        self.config_reset_button.setEnabled(not is_default)

    def _on_open_config_dir(self) -> None:
        folder = paths.config_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except Exception:
            self._show_note("Couldn't open that folder.")

    def _apply_config_dir(self, folder: str) -> None:
        err = config.set_config_dir(folder)
        if err:
            self._show_note(err)
        else:
            self._show_note("")
            self._show_config_dir()
            # The app only saves on a change, and the new folder may hold an older config: re-emit the current
            # settings so they are written to the new location now rather than lost on the next start.
            self.settings_changed.emit(dataclasses.replace(self._settings))

    def _on_change_config_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a settings folder", paths.config_dir())
        if folder:
            self._apply_config_dir(os.path.normpath(folder))

    def _on_reset_config_dir(self) -> None:
        self._apply_config_dir("")
