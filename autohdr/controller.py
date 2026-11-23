"""Wires the conversion service, main window and tray together.

Imports no concrete UI or core modules (everything is injected), so it can be tested with fakes.
"""
import os
import time

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from autohdr.models import ConversionFinished, ConversionStarted, StateChanged

TITLE = "Auto HDR-SDR Converter"


class Controller(QObject):
    SAVE_DELAY_MS = 500     # debounce for save_settings
    TOAST_GAP_S = 5.0       # at most one toast per this many seconds; extras collapse into one follow-up

    _event = Signal(object)  # private: carries service events from worker threads onto the UI thread

    def __init__(self, service, window, tray, settings, *, save_settings, setup_logging, validate_settings,
                 open_folder=None):
        super().__init__()
        self._service, self._window, self._tray = service, window, tray
        self._settings = settings
        self._save_settings = save_settings
        self._setup_logging = setup_logging
        self._validate = validate_settings
        self._open_folder = open_folder
        self._quitting = False
        self._tray_hint_shown = False
        self._clock = time.monotonic
        self._last_toast = float("-inf")
        self._pending_ok = self._pending_failed = 0
        self._toast_folder = ""   # what a click on the current toast opens

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._flush_toasts)

        # Emitted from any thread, delivered on the thread this object lives in (the UI thread).
        self._event.connect(self._handle_event)
        window.settings_changed.connect(self._on_settings_changed)
        window.toggle_requested.connect(self.toggle)
        window.retry_requested.connect(service.retry)
        window.quit_requested.connect(self.quit)
        window.hidden_to_tray.connect(self._on_hidden_to_tray)
        tray.show_requested.connect(window.show_and_raise)
        tray.toggle_requested.connect(self.toggle)
        tray.quit_requested.connect(self.quit)
        tray.message_clicked.connect(self._on_message_clicked)

    # -- service events -------------------------------------------------------------------------

    def on_event(self, event) -> None:
        """ConversionService(on_event=...) callback. Safe to call from any thread."""
        self._event.emit(event)

    def _handle_event(self, event) -> None:
        if self._quitting:
            return
        if isinstance(event, StateChanged):
            self._window.set_running(event.running, event.error)
            self._tray.set_running(event.running)
        elif isinstance(event, ConversionStarted):
            self._window.set_converting(event.src)
        elif isinstance(event, ConversionFinished):
            self._window.set_converting(None)
            self._window.add_activity(event.result)
            self._toast_result(event.result)

    # -- user actions ---------------------------------------------------------------------------

    def start_initial(self) -> None:
        """Called once at launch: show problems, and start watching if configured and possible."""
        problems = self._validate(self._settings)
        self._window.set_problems(problems)
        if self._settings.auto_start and not problems.any:
            self._start()

    def toggle(self) -> None:
        if self._service.running:
            self._service.stop()
        else:
            self._start()

    def _start(self) -> None:
        error = self._service.start()
        if error:
            self._window.set_running(False, error)

    def _on_settings_changed(self, settings) -> None:
        self._settings = settings
        self._save_timer.start(self.SAVE_DELAY_MS)
        self._setup_logging(settings.output_to_log)
        self._service.configure(settings)
        problems = self._validate(settings)
        self._window.set_problems(problems)
        # configure() may already have stopped a failed restart; only stop what is still running.
        if self._service.running and (problems.input or problems.tool):
            self._service.stop()

    def _flush_save(self) -> None:
        self._save_settings(self._settings)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._save_timer.stop()
        self._toast_timer.stop()
        self._window.hide()          # the 3 s join in shutdown() must not look like a frozen window
        self._tray.hide()            # before quitting, so Windows doesn't leave a ghost icon
        try:
            self._service.shutdown()
        finally:
            self._save_settings(self._settings)
            QCoreApplication.quit()

    # -- toasts ---------------------------------------------------------------------------------

    def _on_hidden_to_tray(self) -> None:
        if not self._tray_hint_shown:     # a one-time hint, deliberately outside the coalescing gate
            self._tray_hint_shown = True
            self._notify(TITLE, "Still running in the system tray", False, "")

    def _toast_result(self, r) -> None:
        mode = self._settings.notifications
        if mode == "off" or (r.ok and mode != "all"):
            return
        folder = os.path.dirname(r.dst or r.src)
        now = self._clock()
        if not (self._pending_ok or self._pending_failed) and now - self._last_toast >= self.TOAST_GAP_S:
            self._last_toast = now
            name = os.path.basename(r.src)
            if r.ok:
                body = f"{name} → {os.path.basename(r.dst)}" + (f"\n{r.message}" if r.message else "")
                self._notify("Converted", body, False, folder)
            else:
                self._notify("Conversion failed", f"{name}: {r.message or 'unknown error'}", True, folder)
            return
        if r.ok:
            self._pending_ok += 1
        else:
            self._pending_failed += 1
        self._toast_folder = folder
        if not self._toast_timer.isActive():
            self._toast_timer.start(max(0, int((self._last_toast + self.TOAST_GAP_S - now) * 1000)))

    def _flush_toasts(self) -> None:
        ok, failed = self._pending_ok, self._pending_failed
        self._pending_ok = self._pending_failed = 0
        if not (ok or failed) or self._settings.notifications == "off":
            return
        parts = []
        if failed:
            parts.append(f"{failed} more conversion{'s' if failed != 1 else ''} failed")
        if ok:
            parts.append(f"{ok} more converted")
        self._last_toast = self._clock()
        self._notify("Conversion failed" if failed else "Converted", ", ".join(parts), bool(failed), self._toast_folder)

    def _notify(self, title: str, message: str, error: bool, folder: str) -> None:
        self._toast_folder = folder
        self._tray.notify(title, message, error=error)

    def _on_message_clicked(self) -> None:
        if self._open_folder and self._toast_folder:
            self._open_folder(self._toast_folder)
