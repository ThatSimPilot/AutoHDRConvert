"""Settings persistence: JSON in %APPDATA%\\AutoHDRConvert\\config.json, with a legacy fallback. Never raises."""
import json
import logging
import os
from dataclasses import asdict, fields

from .models import Settings
from .paths import APP_NAME, app_data_dir, app_dir, clean_path, config_path

log = logging.getLogger(APP_NAME)
NOTIFICATION_MODES = ("failures", "all", "off")
PATH_FIELDS = ("input_dir", "output_dir")


def _acceptable(name, default, value):
    if name == "notifications":
        return value in NOTIFICATION_MODES
    return type(value) is type(default)  # exact: 1 is not a bool and "yes" is not a bool


def load_settings() -> Settings:
    """New config file, else the legacy config.json beside the exe, else defaults. Bad fields fall back one by one."""
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
        os.makedirs(app_data_dir(), exist_ok=True)
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
