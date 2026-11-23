"""Main window: status card, folder settings, recent-activity list. Talks to the app only through signals/setters."""
import dataclasses
import os
import subprocess
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QScrollArea, QStackedLayout, QVBoxLayout, QWidget)

from autohdr.models import ConversionResult, Problems, Settings

from . import icons, theme
from .icons import Choice, ElidedLabel, StatusBadge, make_button
from .settings_dialog import SettingsDialog

TITLE = "Auto HDR-SDR Converter"
MAX_ROWS = 100
DEBOUNCE_MS = 400
TICK_MS = 30_000


# ---- small pure helpers (unit-tested) -------------------------------------------------------------------------

def clean_path(text: str) -> str:
    """Trim whitespace and one pair of surrounding double quotes (what Explorer's "Copy as path" pastes)."""
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def relative_time(ts: float, now: float | None = None) -> str:
    secs = max(0, int((time.time() if now is None else now) - ts))
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def elide_path(path: str, limit: int = 44) -> str:
    """Shorten a long path in the middle ("C:\\Users\\…\\Screenshots") for inline copy."""
    if len(path) <= limit:
        return path
    head = (limit - 1) // 3
    return path[:head] + "…" + path[-(limit - 1 - head):]


def _show_in_folder(path: str) -> None:
    try:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')     # does not block; explorer returns 1 normally
    except Exception:
        pass


def _open_file(path: str) -> None:
    try:
        os.startfile(path)
    except Exception:
        pass


def _default_open_log_folder() -> None:
    folder = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AutoHDRConvert")
    try:
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)
    except Exception:
        pass


def _label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def _elided(text: str = "", role: str = "", mode=Qt.TextElideMode.ElideMiddle) -> ElidedLabel:
    lab = ElidedLabel(text, mode)
    if role:
        lab.setProperty("role", role)
    return lab


# ---- activity row ---------------------------------------------------------------------------------------------

class ActivityRow(QFrame):
    retry_clicked = Signal(str)

    def __init__(self, result: ConversionResult, parent=None):
        super().__init__(parent)
        self.setObjectName("activityRow")
        self.result = result
        ok = result.ok
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(12)

        self.badge = StatusBadge(28)
        self.badge.set_state("ok" if ok else "failed")
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(1)
        first = QHBoxLayout()
        first.setSpacing(6)
        self.src_label = _elided(os.path.basename(result.src))
        first.addWidget(self.src_label)
        if ok and result.dst:
            first.addWidget(_label("→", "secondary"))
            self.dst_label = _elided(os.path.basename(result.dst))
            first.addWidget(self.dst_label)
        first.addStretch(1)
        text.addLayout(first)
        second = QHBoxLayout()
        second.setSpacing(0)
        if ok:
            self.detail_label = _elided(os.path.dirname(result.dst), "caption")
            second.addWidget(self.detail_label)
            second.addWidget(_label("   ·   " + ("Original moved to Recycle Bin" if result.original_removed else "Original kept"), "caption"))
            second.addStretch(1)       # the fixed-size phrase keeps its text; only the path gives way
        else:
            self.detail_label = _elided(result.message or "Conversion failed", "error", Qt.TextElideMode.ElideRight)
            second.addWidget(self.detail_label, 1)
        text.addLayout(second)
        lay.addLayout(text, 1)

        self.time_label = _label("", "caption")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_label.setMinimumWidth(self.time_label.fontMetrics().horizontalAdvance("59 min ago") + 4)
        lay.addWidget(self.time_label)

        self.retry_btn = self.show_btn = self.open_btn = None
        if ok:
            self.show_btn = make_button(icon_name="folder", icon_role="text2", tip="Show in folder", name="Show in folder")
            self.open_btn = make_button(icon_name="open", icon_role="text2", tip="Open image", name="Open image")
            self.show_btn.clicked.connect(lambda: _show_in_folder(result.dst))
            self.open_btn.clicked.connect(lambda: _open_file(result.dst))
            lay.addWidget(self.show_btn)
            lay.addWidget(self.open_btn)
            self.setToolTip(f"{result.src}\n→ {result.dst}")
        else:
            self.retry_btn = make_button("Retry", small=True, icon_name="retry", name=f"Retry {os.path.basename(result.src)}")
            self.retry_btn.clicked.connect(self._retry)
            lay.addWidget(self.retry_btn)
            self.setToolTip(f"{result.src}\n{result.message}")
            self._retry_timer = QTimer(self)
            self._retry_timer.setSingleShot(True)
            self._retry_timer.setInterval(3000)
            self._retry_timer.timeout.connect(lambda: self.retry_btn.setEnabled(True))
        self.refresh_time()

    def focus_widgets(self) -> list:
        return [w for w in (self.retry_btn, self.show_btn, self.open_btn) if w is not None]

    def refresh_time(self, now: float | None = None) -> None:
        r = self.result
        when = relative_time(r.finished_at, now)
        self.time_label.setText(when)
        name = os.path.basename(r.src)
        self.setAccessibleName(f"Converted {name}, {when}" if r.ok else f"Failed to convert {name}: {r.message}, {when}")

    def _retry(self) -> None:
        self.retry_btn.setEnabled(False)            # one click per attempt; a failed retry adds a fresh row anyway
        self._retry_timer.start()
        self.retry_clicked.emit(self.result.src)


# ---- main window ----------------------------------------------------------------------------------------------

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
        self._rows: list[ActivityRow] = []
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

        self.activity_card = QFrame()
        self.activity_card.setObjectName("card")
        self._stack = QStackedLayout(self.activity_card)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(24, 16, 24, 16)
        el.setSpacing(6)
        el.addStretch(1)
        self.empty_badge = StatusBadge(48)
        self.empty_badge.set_state("empty")
        el.addWidget(self.empty_badge, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addSpacing(6)
        empty_title = _label("No conversions yet", "section")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_text = _label("", "secondary", wrap=True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        el.addWidget(empty_title)
        el.addWidget(self.empty_text)
        el.addStretch(1)
        self._stack.addWidget(empty)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAccessibleName("Recent activity")
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(8, 8, 8, 8)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        self.scroll.setWidget(self._rows_host)
        self._stack.addWidget(self.scroll)
        self.activity_card.setMinimumHeight(150)
        col.addWidget(self.activity_card, 1)

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
                 self.keep_choice, self.bin_choice]
        for row in self._rows:
            chain += row.focus_widgets()
        chain += [self.settings_button, self.quit_button]
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
        self._update_empty_text()

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
        self._settings = new
        self.out_reset.setVisible(bool(new.output_dir))
        self._refresh_status()
        self._update_empty_text()
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
            auto_start=s.auto_start, output_to_log=s.output_to_log)
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
        self._problems = p
        for label, edit, text in ((self.in_err, self.in_edit, p.input), (self.out_err, self.out_edit, p.output)):
            label.setText(text)
            label.setVisible(bool(text))
            edit.setProperty("invalid", bool(text))
            theme.repolish(edit)
        self._refresh_status()

    def add_activity(self, result: ConversionResult) -> None:
        if result.ok:
            self._converted += 1
        else:                                       # a retry that fails again replaces its old row instead of stacking
            for old in [r for r in self._rows if not r.result.ok and r.result.src == result.src]:
                self._drop_row(old)
        row = ActivityRow(result)
        row.retry_clicked.connect(self.retry_requested)
        self._rows_layout.insertWidget(0, row)
        self._rows.insert(0, row)
        while len(self._rows) > MAX_ROWS:
            self._drop_row(self._rows[-1])
        self._stack.setCurrentWidget(self.scroll)
        self._update_counter()
        self._fix_tab_order()

    def _drop_row(self, row: ActivityRow) -> None:
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()

    def show_and_raise(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if self.windowHandle():
            self.windowHandle().requestActivate()

    def refresh_times(self) -> None:
        now = time.time()
        for row in self._rows:
            row.refresh_time(now)

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

    def _update_empty_text(self) -> None:
        folder = self._settings.input_dir
        where = f"in {elide_path(folder, 36)}" if folder else "in the watch folder"
        self.empty_text.setText(f"New .jxr files {where} will appear here.")
        self.empty_text.setToolTip(folder)

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
