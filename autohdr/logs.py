"""Logging setup: console (when there is one) plus an optional rotating file (default %LOCALAPPDATA%\\AutoHDRConvert\\logs)."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .paths import APP_NAME, fallback_log_path, log_path

log = logging.getLogger(APP_NAME)
_log_handlers = []
_active_path = ""   # the file the last setup_logging settled on; "" until it has run


def active_log_path(configured="") -> str:
    """The log file in use (or, before setup_logging has run, the one it would use)."""
    return _active_path or log_path(configured)


def active_log_folder(configured="") -> str:
    return os.path.dirname(active_log_path(configured))


def _open_file_handler(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return RotatingFileHandler(path, maxBytes=1_000_000, backupCount=1, encoding="utf-8")


def setup_logging(output_to_log: bool, log_dir: str = "") -> None:
    """(Re)configure root logging. logging.basicConfig is a no-op once handlers exist, so manage ours explicitly.

    The file goes in log_dir ("" = %LOCALAPPDATA%\\AutoHDRConvert\\logs). If that can't be opened (a folder the user
    removed, a drive that went away, no write access) fall back to %APPDATA%\\AutoHDRConvert\\AutoHDRConvert.log
    rather than losing the log.
    """
    global _active_path
    root = logging.getLogger()
    for h in _log_handlers:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.INFO)
    handlers, errors, _active_path = [], [], ""
    if sys.stderr is not None:  # windowed frozen builds have no stderr
        handlers.append(logging.StreamHandler())
    if output_to_log:
        wanted = log_path(log_dir)
        for path in dict.fromkeys((wanted, fallback_log_path())):   # distinct, in order
            try:
                handlers.append(_open_file_handler(path))
                _active_path = path
                break
            except OSError as e:
                errors.append(f"Cannot open log file {path}: {e}")
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    _log_handlers[:] = handlers
    for message in errors:
        log.error(message)
    if _active_path and errors:
        log.warning(f"Logging to {_active_path} instead")
