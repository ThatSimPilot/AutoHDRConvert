"""Where things live: app folder, bundled resources, %APPDATA% files, hdrfix.exe. No Qt, no tkinter."""
import os
import shutil
import sys

APP_NAME = "AutoHDRConvert"
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
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)


def config_path():
    return os.path.join(app_data_dir(), "config.json")


def log_path():
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
