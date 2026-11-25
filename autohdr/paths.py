r"""Where things live: app folder, bundled resources, per-user data, hdrfix.exe. No Qt, no tkinter.

One standalone exe, no installer. Settings live in %APPDATA%\AutoHDRConvert (or in a folder the user picked in the
Settings dialog, recorded in %APPDATA%\AutoHDRConvert\config_location.txt). Logs live in
%LOCALAPPDATA%\AutoHDRConvert\logs (or a folder the user picked). Nothing is written beside the exe.
"""
import os
import shutil
import sys

APP_NAME = "AutoHDRConvert"
CONFIG_LOCATION_NAME = "config_location.txt"
HDRFIX_MISSING = "hdrfix.exe wasn't found. Put it next to the app or add it to PATH."


def app_dir():
    # Frozen: the folder holding the exe, so users can drop in a newer hdrfix.exe next to it.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root, beside AutoHDRConvert.py


def resource_path(name):
    # Bundled resources live in PyInstaller's temp dir (_MEIPASS), not beside the exe.
    base = sys._MEIPASS if getattr(sys, "frozen", False) else app_dir()
    return os.path.join(base, name)


def app_data_dir():
    """The default per-user settings folder: %APPDATA%\\AutoHDRConvert. Also holds the pointer file and the last-resort log."""
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)


def config_location_file():
    """Pointer file that names a user-chosen settings folder. Absent means the default folder."""
    return os.path.join(app_data_dir(), CONFIG_LOCATION_NAME)


def config_dir():
    """The folder holding config.json: the folder named in config_location.txt, else app_data_dir(). Never raises.
    A blank, relative or otherwise garbage pointer is ignored."""
    try:
        with open(config_location_file(), "r", encoding="utf-8", errors="replace") as f:
            chosen = clean_path(f.read())
        if chosen and os.path.isabs(chosen):
            return chosen
    except Exception:   # missing, unreadable or garbage pointer: use the default
        pass
    return app_data_dir()


def config_path():
    return os.path.join(config_dir(), "config.json")


def default_log_dir():
    """%LOCALAPPDATA%\\AutoHDRConvert\\logs: logs are machine-local data, so not in the roaming APPDATA."""
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, APP_NAME, "logs")


def log_dir(configured=""):
    """The folder logs go in: the user's choice, else %LOCALAPPDATA%\\AutoHDRConvert\\logs."""
    return clean_path(configured) or default_log_dir()


def log_path(configured=""):
    return os.path.join(log_dir(configured), "AutoHDRConvert.log")


def fallback_log_path():
    """Used when the chosen log folder can't be written: %APPDATA%\\AutoHDRConvert\\AutoHDRConvert.log."""
    return os.path.join(app_data_dir(), "AutoHDRConvert.log")


def clean_path(p):
    """Trim whitespace and one pair of surrounding double quotes (what 'Copy as path' pastes)."""
    p = (p or "").strip()
    if len(p) >= 2 and p[0] == p[-1] == '"':
        p = p[1:-1].strip()
    return p


def find_hdrfix():
    """Full path to hdrfix.exe: app folder, then bundled copy, then PATH; None if not found."""
    for base in (app_dir(), getattr(sys, "_MEIPASS", None)):
        if base and os.path.isfile(os.path.join(base, "hdrfix.exe")):
            return os.path.join(base, "hdrfix.exe")
    return shutil.which("hdrfix")
