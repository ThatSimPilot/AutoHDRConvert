"""Logging setup: console (when there is one) plus an optional rotating file in %APPDATA%."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .paths import APP_NAME, app_data_dir, log_path

log = logging.getLogger(APP_NAME)
_log_handlers = []


def setup_logging(output_to_log: bool) -> None:
    """(Re)configure root logging. logging.basicConfig is a no-op once handlers exist, so manage ours explicitly."""
    root = logging.getLogger()
    for h in _log_handlers:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.INFO)
    handlers, error = [], None
    if sys.stderr is not None:  # windowed frozen builds have no stderr
        handlers.append(logging.StreamHandler())
    if output_to_log:
        try:
            os.makedirs(app_data_dir(), exist_ok=True)
            handlers.append(RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
        except OSError as e:
            error = e
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    _log_handlers[:] = handlers
    if error:
        log.error(f"Cannot open log file {log_path()}: {error}")
