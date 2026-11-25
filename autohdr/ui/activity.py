"""Recent-activity list for the main window: the card with its empty state, and one row per converted/failed file."""
import os
import subprocess
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedLayout, QVBoxLayout, QWidget

from autohdr.models import ConversionResult

from .icons import ElidedLabel, StatusBadge, make_button

MAX_ROWS = 100


# ---- small helpers (shared with the main window) --------------------------------------------------------------

def make_label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def make_elided(text: str = "", role: str = "", mode=Qt.TextElideMode.ElideMiddle) -> ElidedLabel:
    lab = ElidedLabel(text, mode)
    if role:
        lab.setProperty("role", role)
    return lab


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
        self.src_label = make_elided(os.path.basename(result.src))
        first.addWidget(self.src_label)
        if ok and result.dst:
            first.addWidget(make_label("→", "secondary"))
            self.dst_label = make_elided(os.path.basename(result.dst))
            first.addWidget(self.dst_label)
        first.addStretch(1)
        text.addLayout(first)
        second = QHBoxLayout()
        second.setSpacing(0)
        self.note_label = None
        if ok:
            self.detail_label = make_elided(os.path.dirname(result.dst), "caption")
            second.addWidget(self.detail_label)
            second.addWidget(make_label("   ·   " + ("Original moved to Recycle Bin" if result.original_removed else "Original kept"), "caption"))
            second.addStretch(1)       # the fixed-size phrase keeps its text; only the path gives way
        else:
            self.detail_label = make_elided(result.message or "Conversion failed", "error", Qt.TextElideMode.ElideRight)
            second.addWidget(self.detail_label, 1)
        text.addLayout(second)
        if ok and result.message:      # why the original was kept, etc.; its own wrapped line so the path keeps its room
            self.note_label = make_label(result.message, "caption", wrap=True)
            text.addWidget(self.note_label)
        lay.addLayout(text, 1)

        self.time_label = make_label("", "caption")
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
            self.setToolTip(f"{result.src}\n→ {result.dst}" + (f"\n{result.message}" if result.message else ""))
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
        if r.ok:
            self.setAccessibleName(f"Converted {name}, {r.message + ', ' if r.message else ''}{when}")
        else:
            self.setAccessibleName(f"Failed to convert {name}: {r.message}, {when}")

    def _retry(self) -> None:
        self.retry_btn.setEnabled(False)            # one click per attempt; a failed retry replaces this row anyway
        self._retry_timer.start()
        self.retry_clicked.emit(self.result.src)


# ---- activity list --------------------------------------------------------------------------------------------

class ActivityList(QFrame):
    """The "Recent activity" card: an empty state until the first result, then newest-first rows (at most MAX_ROWS)."""
    retry_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(150)
        self.rows: list[ActivityRow] = []
        self._stack = QStackedLayout(self)

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(24, 16, 24, 16)
        el.setSpacing(6)
        el.addStretch(1)
        self.empty_badge = StatusBadge(48)
        self.empty_badge.set_state("empty")
        el.addWidget(self.empty_badge, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addSpacing(6)
        empty_title = make_label("No conversions yet", "section")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_text = make_label("", "secondary", wrap=True)
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
        host = QWidget()
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(8, 8, 8, 8)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        self.scroll.setWidget(host)
        self._stack.addWidget(self.scroll)

    def set_folder(self, folder: str) -> None:
        where = f"in {elide_path(folder, 36)}" if folder else "in the watch folder"
        self.empty_text.setText(f"New .jxr files {where} will appear here.")
        self.empty_text.setToolTip(folder)

    def add(self, result: ConversionResult) -> None:
        # One row per source, showing its latest outcome: a retry (ok or failed again) replaces the old failed row.
        for old in [r for r in self.rows if not r.result.ok and r.result.src == result.src]:
            self._drop(old)
        row = ActivityRow(result)
        row.retry_clicked.connect(self.retry_requested)
        self._rows_layout.insertWidget(0, row)
        self.rows.insert(0, row)
        while len(self.rows) > MAX_ROWS:
            self._drop(self.rows[-1])
        self._stack.setCurrentWidget(self.scroll)

    def _drop(self, row: ActivityRow) -> None:
        self.rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()

    def refresh_times(self) -> None:
        now = time.time()
        for row in self.rows:
            row.refresh_time(now)

    def focus_widgets(self) -> list:
        return [w for row in self.rows for w in row.focus_widgets()]
