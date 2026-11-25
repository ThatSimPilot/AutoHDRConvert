"""Start menu shortcut for the built exe. Pure Python (no Qt). Every function returns or reports; none raises."""
import os
import subprocess
import sys

SHORTCUT_NAME = "Auto HDR-SDR Converter"


def shortcut_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs", SHORTCUT_NAME + ".lnk")


def available() -> bool:
    """Only the built exe can have a shortcut: it must point at the real exe (sys.executable)."""
    return bool(getattr(sys, "frozen", False))


def exists() -> bool:
    try:
        return os.path.isfile(shortcut_path())
    except Exception:
        return False


def _ps_quote(text: str) -> str:
    """A PowerShell single-quoted string literal: only ' needs escaping, and it is doubled."""
    return "'" + str(text).replace("'", "''") + "'"


def _script(path: str, target: str) -> str:
    workdir = os.path.dirname(target)
    return "\n".join([
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(path)})",
        f"$s.TargetPath = {_ps_quote(target)}",
        f"$s.WorkingDirectory = {_ps_quote(workdir)}",
        f"$s.IconLocation = {_ps_quote(target + ',0')}",
        f"$s.Description = {_ps_quote(SHORTCUT_NAME)}",
        "$s.Save()",
    ])


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def create() -> str:
    """Create the Start menu shortcut. Returns "" on success, else a short error."""
    try:
        if not available():
            return "Only the built app can add a shortcut."
        path = shortcut_path()
        target = sys.executable
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-Command", _script(path, target)]
        result = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30, shell=False)
        if os.path.isfile(path):
            return ""
        detail = _last_line(result.stderr) or _last_line(result.stdout)
        return detail or "Couldn't create the shortcut."
    except Exception as e:
        return _last_line(str(e)) or "Couldn't create the shortcut."


def remove() -> str:
    """Delete the shortcut if present. Returns "" on success or when already absent, else a short error."""
    try:
        path = shortcut_path()
        if os.path.isfile(path):
            os.remove(path)
        return ""
    except Exception as e:
        return f"Couldn't remove the shortcut: {e}" if str(e) else "Couldn't remove the shortcut."
