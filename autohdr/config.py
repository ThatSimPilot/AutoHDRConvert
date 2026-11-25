"""Settings persistence: JSON in %APPDATA%\\AutoHDRConvert\\config.json (or the folder the user picked, see
set_config_dir), with a legacy fallback to a config.json beside the exe. Never raises."""
import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, fields

from .models import TONE_PRESETS, Settings
from .paths import APP_NAME, app_data_dir, app_dir, clean_path, config_dir, config_location_file, config_path

log = logging.getLogger(APP_NAME)
NOTIFICATION_MODES = ("failures", "all", "off")
PATH_FIELDS = ("input_dir", "output_dir", "log_dir")


def _acceptable(name, default, value):
    if name == "notifications":
        return value in NOTIFICATION_MODES
    if name == "tone_preset":
        return isinstance(value, str) and value in TONE_PRESETS  # str check first: a JSON list/dict is unhashable
    return type(value) is type(default)  # exact: 1 is not a bool and "yes" is not a bool


def load_settings() -> Settings:
    """New config file (config_path()), else the legacy config.json beside the exe, else defaults.
    Bad fields fall back one by one."""
    path = config_path()
    if not os.path.exists(path):
        path = os.path.join(app_dir(), "config.json")  # a corrupt new file must not resurrect this, hence only when absent
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return Settings()
    except Exception as e:
        log.error(f"Error loading config {path}: {e}")
        return Settings()
    if not isinstance(data, dict):
        log.error(f"Ignoring config {path}: expected a JSON object")
        return Settings()
    defaults, values = Settings(), {}
    for f in fields(Settings):
        default = getattr(defaults, f.name)
        value = data.get(f.name, default)
        if not _acceptable(f.name, default, value):
            log.warning(f"Ignoring invalid config value for {f.name}: {value!r}")
            value = default
        values[f.name] = clean_path(value) if f.name in PATH_FIELDS else value
    return Settings(**values)


def save_settings(s: Settings) -> bool:
    """Atomic: write a temp file in the same folder, then os.replace, so a crash can't leave a half-written config."""
    tmp = config_path() + ".tmp"
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(s), f, indent=4)
        os.replace(tmp, config_path())
        return True
    except Exception as e:
        log.error(f"Error saving config: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _same_folder(a, b):
    return os.path.normcase(os.path.normpath(os.path.abspath(a))) == os.path.normcase(os.path.normpath(os.path.abspath(b)))


def set_config_dir(new_dir: str) -> str:
    """Point the settings at new_dir ('' = back to the default folder). Returns '' on success or a short
    human-readable error. Never raises.

    The current config is copied into the new folder (unless one is already there); the old file is left in place
    as a backup. The pointer file config_location.txt records the choice; the default needs no pointer."""
    try:
        new_dir = clean_path(new_dir)
        default = app_data_dir()
        target = new_dir or default
        old_file = config_path()
        try:
            os.makedirs(target, exist_ok=True)
            fd, probe = tempfile.mkstemp(dir=target, prefix=".autohdr-write-test-", suffix=".tmp")
            os.close(fd)
            os.remove(probe)
        except OSError as e:
            return f"Can't use that folder: {e.strerror or e}"
        target_file = os.path.join(target, "config.json")
        try:
            if os.path.isfile(old_file) and not os.path.exists(target_file):
                shutil.copyfile(old_file, target_file)
        except OSError as e:
            return f"Can't copy the settings into that folder: {e.strerror or e}"
        pointer = config_location_file()
        try:
            if not new_dir or _same_folder(target, default):
                if os.path.exists(pointer):
                    os.remove(pointer)
            else:
                os.makedirs(app_data_dir(), exist_ok=True)
                with open(pointer, "w", encoding="utf-8") as f:
                    f.write(os.path.normpath(os.path.abspath(target)))
        except OSError as e:
            return f"Can't record the new settings folder: {e.strerror or e}"
        return ""
    except Exception as e:   # the Settings dialog must never crash on a bad folder
        return f"Can't use that folder: {e}"
