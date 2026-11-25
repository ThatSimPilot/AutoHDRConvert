"""Main window: status card, folder settings, recent-activity list. Talks to the app only through signals/setters."""
import dataclasses
import os
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QMainWindow,
                               QVBoxLayout, QWidget)

from autohdr.models import ConversionResult, Problems, Settings
from autohdr.paths import clean_path

from . import icons, theme
from .activity import ActivityList, make_elided as _elided, make_label as _label
from .icons import Choice, StatusBadge, make_button
from .settings_dialog import SettingsDialog

TITLE = "Auto HDR-SDR Converter"
DEBOUNCE_MS = 400
TICK_MS = 30_000


def _default_open_log_folder() -> None:
    folder = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AutoHDRConvert")
    try:
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)
    except Exception:
        pass


class MainWindow(QMainWindow):
    settings_changed = Signal(object)     # Settings; emitted on any user edit (text edits debounced)
    toggle_requested = Signal()
    retry_requested = Signal(str)
    quit_requested = Signal()
    hidden_to_tray = Signal()

    def __init__(self, settings: Settings, tray_available: bool):
        super().__init__()
        self._settings = dataclasses.replace(settings)
        self._tray_available = tray_available
        self._running = False
        self._error = ""
        self._converting: str | None = None
        self._problems = Problems()
        self._converted = 0
        self._quitting = False
        self._hdrfix_path = ""
        self._open_log_folder: Callable[[], None] = _default_open_log_folder
        self._dialog: SettingsDialog | None = None

        self.setWindowTitle(TITLE)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(520, 560)
        self.resize(560, 640)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_if_changed)
        self._build()
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self.refresh_times)
        self._tick.start()
        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self._request_quit)

        self._populate(self._settings)
        self._update_counter()
        self._refresh_status()
        self._fix_tab_order()

    @property
    def _rows(self) -> list:
        return self.activity.rows

    # -- construction ---------------------------------------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)   # initial focus lands here, so no focus ring until the user tabs
        self.setCentralWidget(root)
        root.setFocus()
        col = QVBoxLayout(root)
        col.setContentsMargins(24, 24, 24, 20)
        col.setSpacing(16)

        # status card
        self.status_card = QFrame()
        self.status_card.setObjectName("statusCard")
        sc = QHBoxLayout(self.status_card)
        sc.setContentsMargins(16, 16, 16, 16)
        sc.setSpacing(16)
        self.badge = StatusBadge(44)
        sc.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = _elided("", "title", Qt.TextElideMode.ElideMiddle)
        self.subtitle_label = _elided("", "secondary")
        self.detail_label = _label("", "secondary", wrap=True)   # wraps: error reasons must not be cut off
        text.addWidget(self.title_label)
        text.addWidget(self.subtitle_label)
        text.addWidget(self.detail_label)
        sc.addLayout(text, 1)
        self.toggle_button = make_button("Pause", name="Pause or resume watching")
        self.toggle_button.setMinimumWidth(96)
        self.toggle_button.clicked.connect(self.toggle_requested)
        sc.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self.status_card)

        # folder settings card
        card = QFrame()
        card.setObjectName("card")
        g = QGridLayout(card)
        g.setContentsMargins(16, 16, 16, 16)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.setColumnMinimumWidth(0, 104)
        g.setColumnStretch(1, 1)

        self.in_edit = QLineEdit()
        self.in_edit.setPlaceholderText("Folder your HDR screenshots are saved to")
        self.in_edit.setAccessibleName("Watch folder")
        self.in_browse = make_button("Browse", name="Browse for the watch folder")
        self.in_err = _label("", "error", wrap=True)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Same folder as originals")
        self.out_edit.setAccessibleName("Save JPGs to")
        self.out_browse = make_button("Browse", name="Browse for the output folder")
        self.out_err = _label("", "error", wrap=True)
        self.out_reset = make_button("Reset", small=True, ghost=True, name="Reset output folder to the same folder as originals")
        for b in (self.in_browse, self.out_browse):
            b.setMinimumWidth(88)
        for lab in (self.in_err, self.out_err):
            lab.hide()                                  # shown by set_problems(); hidden labels take no layout space
        lab_in, lab_out = _label("Watch folder", "secondary"), _label("Save JPGs to", "secondary")
        lab_in.setBuddy(self.in_edit)
        lab_out.setBuddy(self.out_edit)
        lab_after = _label("After converting", "secondary")
        lab_after.setMinimumHeight(32)
        g.addWidget(lab_in, 0, 0)
        g.addWidget(self.in_edit, 0, 1)
        g.addWidget(self.in_browse, 0, 2)
        g.addWidget(self.in_err, 1, 1, 1, 2)
        g.addWidget(lab_out, 2, 0)
        g.addWidget(self.out_edit, 2, 1)
        g.addWidget(self.out_browse, 2, 2)
        g.addWidget(self.out_err, 3, 1)
        g.addWidget(self.out_reset, 3, 2, Qt.AlignmentFlag.AlignRight)
        self.keep_choice = Choice("Keep the original")
        self.bin_choice = Choice("Move original to Recycle Bin")
        self._after_group = QButtonGroup(self)
        self._after_group.addButton(self.keep_choice)
        self._after_group.addButton(self.bin_choice)
        g.addWidget(lab_after, 4, 0, Qt.AlignmentFlag.AlignTop)
        choices = QVBoxLayout()
        choices.setSpacing(0)
        choices.addWidget(self.keep_choice)
        choices.addWidget(self.bin_choice)
        g.addLayout(choices, 4, 1, 1, 2)
        col.addWidget(card)

        self.in_edit.textEdited.connect(self._debounce_start)
        self.out_edit.textEdited.connect(self._debounce_start)
        self.in_edit.editingFinished.connect(lambda: self._finish_edit(self.in_edit))
        self.out_edit.editingFinished.connect(lambda: self._finish_edit(self.out_edit))
        self.in_browse.clicked.connect(lambda: self._browse(self.in_edit, "Choose the folder to watch"))
        self.out_browse.clicked.connect(lambda: self._browse(self.out_edit, "Choose where to save the JPGs"))
        self.out_reset.clicked.connect(self._reset_output)
        self.bin_choice.toggled.connect(lambda _: self._emit_if_changed())

        # activity
        head = QHBoxLayout()
        head.addWidget(_label("Recent activity", "section"))
        head.addStretch(1)
        self.counter_label = _label("", "caption")
        head.addWidget(self.counter_label)
        col.addLayout(head)

        self.activity = ActivityList()
        self.activity.retry_requested.connect(self.retry_requested)
        col.addWidget(self.activity, 1)

        # footer
        foot = QHBoxLayout()
        self.settings_button = make_button("Settings", icon_name="gear", name="Settings")
        self.settings_button.clicked.connect(self.open_settings)
        self.quit_button = make_button("Quit", name="Quit")
        self.quit_button.clicked.connect(self._request_quit)
        foot.addWidget(self.settings_button)
        foot.addStretch(1)
        foot.addWidget(self.quit_button)
        col.addLayout(foot)

    def _fix_tab_order(self) -> None:
        """Status -> fields -> activity row actions -> footer (rows are created later, so Qt's default would put them last)."""
        chain = [self.toggle_button, self.in_edit, self.in_browse, self.out_edit, self.out_browse, self.out_reset,
                 self.keep_choice, self.bin_choice, *self.activity.focus_widgets(), self.settings_button, self.quit_button]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    # -- settings plumbing ----------------------------------------------------------------------------------

    def _populate(self, s: Settings) -> None:
        """Put settings into the widgets without emitting anything."""
        self._debounce_stop()
        for w in (self.in_edit, self.out_edit, self.keep_choice, self.bin_choice):
            w.blockSignals(True)
        self.in_edit.setText(s.input_dir)
        self.out_edit.setText(s.output_dir)
        self.in_edit.setCursorPosition(0)       # show the start of a long path, not its tail
        self.out_edit.setCursorPosition(0)
        self.keep_choice.setChecked(not s.delete_original)
        self.bin_choice.setChecked(s.delete_original)
        for w in (self.in_edit, self.out_edit, self.keep_choice, self.bin_choice):
            w.blockSignals(False)
        self.out_reset.setVisible(bool(s.output_dir))
        self.activity.set_folder(s.input_dir)

    def _collect(self) -> Settings:
        return dataclasses.replace(
            self._settings, input_dir=clean_path(self.in_edit.text()), output_dir=clean_path(self.out_edit.text()),
            delete_original=self.bin_choice.isChecked())

    def _debounce_start(self, *_) -> None:
        self._debounce.start()

    def _debounce_stop(self) -> None:
        self._debounce.stop()

    def _emit_if_changed(self) -> None:
        self._debounce_stop()
        new = self._collect()
        if new == self._settings:
            return
        if (new.input_dir, new.output_dir) != (self._settings.input_dir, self._settings.output_dir):
            self._error = ""        # a folder edit makes the old "can't watch" card stale; a failed restart reports anew
        self._settings = new
        self.out_reset.setVisible(bool(new.output_dir))
        self._refresh_status()
        self.activity.set_folder(new.input_dir)
        self.settings_changed.emit(dataclasses.replace(new))

    def _finish_edit(self, edit: QLineEdit) -> None:
        cleaned = clean_path(edit.text())
        if cleaned != edit.text():                  # tidy "quoted " pastes once the user is done (never mid-typing)
            edit.setText(cleaned)
        self._emit_if_changed()

    def _browse(self, edit: QLineEdit, caption: str) -> None:
        start = clean_path(edit.text()) or (self._settings.input_dir if edit is self.out_edit else "")
        folder = QFileDialog.getExistingDirectory(self, caption, start)
        if folder:
            edit.setText(os.path.normpath(folder))
            edit.setCursorPosition(0)
            self._emit_if_changed()

    def _reset_output(self) -> None:
        self.out_edit.setText("")
        self._emit_if_changed()
        self.out_edit.setFocus()

    def _on_dialog_changed(self, s: Settings) -> None:
        """The dialog owns only the 'App' settings; keep the folder fields as they are."""
        self._settings = dataclasses.replace(
            self._settings, minimize_to_tray=s.minimize_to_tray, notifications=s.notifications,
            auto_start=s.auto_start, output_to_log=s.output_to_log, tone_preset=s.tone_preset)
        self.settings_changed.emit(dataclasses.replace(self._settings))

    # -- public API (app -> window) -------------------------------------------------------------------------

    def set_settings(self, s: Settings) -> None:
        self._settings = dataclasses.replace(s)
        self._populate(self._settings)
        self._refresh_status()
        if self._dialog is not None:
            self._dialog.set_settings(self._settings)

    def set_hdrfix_path(self, path: str) -> None:
        self._hdrfix_path = path or ""
        if self._dialog is not None:
            self._dialog.set_hdrfix_path(self._hdrfix_path)

    def set_open_log_folder(self, fn: Callable[[], None]) -> None:
        self._open_log_folder = fn
        if self._dialog is not None:
            self._dialog.set_open_log_folder(fn)

    def set_running(self, running: bool, error: str = "") -> None:
        self._running = running
        self._error = "" if running else error
        self._refresh_status()

    def set_converting(self, src: str | None) -> None:
        self._converting = src or None
        self._refresh_status()

    def set_problems(self, p: Problems) -> None:
        old = self._problems
        self._problems = p
        # The "can't watch" error is stale once the folder/tool problem that stopped the watcher is fixed. Only an
        # input/tool problem going away counts: those stop the service, so no fresh error can be in flight with it.
        if (old.input or old.tool) and not (p.input or p.tool):
            self._error = ""
        for label, edit, text in ((self.in_err, self.in_edit, p.input), (self.out_err, self.out_edit, p.output)):
            label.setText(text)
            label.setVisible(bool(text))
            edit.setProperty("invalid", bool(text))
            theme.repolish(edit)
        self._refresh_status()

    def add_activity(self, result: ConversionResult) -> None:
        if result.ok:
            self._converted += 1
        self.activity.add(result)
        self._update_counter()
        self._fix_tab_order()

    def show_and_raise(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if self.windowHandle():
            self.windowHandle().requestActivate()

    def refresh_times(self) -> None:
        self.activity.refresh_times()

    def open_settings(self) -> None:
        if self._dialog is None:
            d = SettingsDialog(self._settings, self._tray_available, self._hdrfix_path, self._open_log_folder, self)
            d.settings_changed.connect(self._on_dialog_changed)
            self._dialog = d
        self._dialog.set_settings(self._settings)
        self._dialog.open()

    # -- state ----------------------------------------------------------------------------------------------

    def _update_counter(self) -> None:
        self.counter_label.setText(f"{self._converted} converted this session")

    def _derive_state(self) -> tuple[str, str, str, str]:
        """(state, title, subtitle, detail) for the status card."""
        p, s = self._problems, self._settings
        folder = s.input_dir or "No folder chosen"
        if self._error and not self._running:
            return "error", "Can't watch this folder", "", self._error
        if self._converting:
            return "converting", f"Converting {os.path.basename(self._converting)}…", folder, ""
        if self._running:
            return "running", "Watching for HDR screenshots", folder, ""
        if p.tool:
            return "error", "Can't convert yet", "", p.tool
        if p.input or not s.input_dir.strip():
            return "idle", "Choose a folder to start", "Pick your HDR screenshots folder.", ""
        return "paused", "Paused", "New screenshots won't be converted", ""

    def _refresh_status(self) -> None:
        state, title, subtitle, detail = self._derive_state()
        self.status_card.setProperty("state", state)
        theme.repolish(self.status_card)
        self.badge.set_state(state)
        self.title_label.setFullText(title)
        self.subtitle_label.setFullText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))
        self.subtitle_label.setToolTip(self._settings.input_dir if state in ("running", "converting") else "")
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        running = self._running
        label = "Pause" if running else ("Start" if state == "idle" else "Resume")
        can_start = not (self._problems.input or self._problems.tool or not self._settings.input_dir.strip())
        self.toggle_button.setText(label)
        self.toggle_button.setEnabled(running or can_start)
        self.toggle_button.setToolTip("" if running or can_start else "Fix the problems below first")
        self.toggle_button.setProperty("primary", not running)
        theme.repolish(self.toggle_button)

    # -- closing --------------------------------------------------------------------------------------------

    def _request_quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._emit_if_changed()                     # never lose a half-debounced edit
        self.quit_requested.emit()

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
        elif self._tray_available and self._settings.minimize_to_tray:
            self._emit_if_changed()
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
        else:
            self._request_quit()
            event.accept()
