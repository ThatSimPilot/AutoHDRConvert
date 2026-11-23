"""Converting one file: wait for it to settle, run hdrfix, verify the output, optionally recycle the original.

No Qt, no tkinter. convert_file never raises.
"""
import ctypes
import logging
import os
import subprocess
import time

from send2trash import send2trash

from .models import ConversionResult
from .paths import APP_NAME, HDRFIX_MISSING

log = logging.getLogger(APP_NAME)

HDRFIX_TIMEOUT = 120
DRIVE_FIXED = 3  # GetDriveTypeW: only fixed local drives have a Recycle Bin we can trust
REMOVED_MSG = "The file was removed before it could be converted"
NEVER_WRITTEN_MSG = "The file never finished being written"
NO_BIN_MSG = "Converted, but the original was kept (Recycle Bin isn't available on that drive)"
TRASH_FAILED_MSG = "Converted, but couldn't move the original to the Recycle Bin"


def make_output_path(src_path, output_dir):
    """<stem>-sdr.jpg in output_dir, or <stem>-sdr-1.jpg, -2, ... if that already exists."""
    stem = os.path.splitext(os.path.basename(src_path))[0]
    path = os.path.join(output_dir, f"{stem}-sdr.jpg")
    n = 0
    while os.path.exists(path):
        n += 1
        path = os.path.join(output_dir, f"{stem}-sdr-{n}.jpg")
    return path


def wait_for_file(file_path, timeout=10, settle=0.5):
    """Block until the file is non-empty and its size has been unchanged for `settle` seconds.

    FileNotFoundError at once if the file is missing. TimeoutError once the size has not changed for `timeout`
    seconds without the file becoming usable (the clock restarts on every size change, so a slow copy that keeps
    growing is never failed for being slow).
    """
    last_size, last_change = -1, time.monotonic()
    while True:
        try:
            size = os.path.getsize(file_path)
        except FileNotFoundError:
            raise
        except OSError:  # e.g. a sharing violation while the writer still holds it: keep waiting
            size = -1
        now = time.monotonic()
        if size != last_size:
            last_size, last_change = size, now
        elif size > 0 and now - last_change >= settle:
            return
        if now - last_change > timeout:
            raise TimeoutError(f"File {file_path} is still changing or unavailable.")
        time.sleep(0.1)


def _drive_type(path):
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def _recycle(src_path, output_path):
    """Recycle src_path only if output_path is non-empty and src is on a fixed drive. Returns "" or a UI-ready reason.

    send2trash's legacy Windows backend (no pywin32, e.g. frozen builds) can permanently delete when the bin can't
    take a file (network/removable drive, over the bin's cap), so anything but a fixed drive keeps the original.
    """
    try:
        if os.path.getsize(output_path) <= 0:
            raise OSError("output file is empty")
        if _drive_type(src_path) != DRIVE_FIXED:
            log.error(f"Original kept, not recycled ({src_path}): not on a fixed local drive")
            return NO_BIN_MSG
        send2trash(os.path.abspath(src_path))
        return ""
    except Exception as e:
        log.error(f"Original kept, not deleted ({src_path}): {e}")
        return TRASH_FAILED_MSG


def trash_original(src_path, output_path):
    """Recycle src_path (see _recycle for the safety rules). Returns True if it was recycled."""
    return _recycle(src_path, output_path) == ""


def _tail(text, limit=200):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


def _kill(proc):
    try:
        proc.kill()
        proc.communicate()  # reap it and close the pipes
    except Exception as e:
        log.error(f"Could not stop hdrfix: {e}")


def convert_file(src, output_dir, hdrfix_path, delete_original, on_process=None):
    """Convert one .jxr to <stem>-sdr.jpg. NEVER raises; returns a ConversionResult.

    on_process(proc) is called with the hdrfix child when it starts and with None when it is done, so the caller can
    terminate it. On any failure a partial or empty output is deleted and the original is kept.
    """
    dst = ""

    def failed(message):
        if dst and os.path.exists(dst):  # make_output_path picked a name that didn't exist, so whatever is there is ours
            try:
                os.remove(dst)
            except OSError as e:
                log.error(f"Could not remove partial output {dst}: {e}")
        if not os.path.exists(src):
            message = REMOVED_MSG
        log.error(f"Conversion failed for {src}: {message}")
        return ConversionResult(False, src, "", message, False, time.time())

    try:
        try:
            wait_for_file(src)
        except FileNotFoundError:
            return failed(REMOVED_MSG)
        except TimeoutError:
            return failed(NEVER_WRITTEN_MSG)
        os.makedirs(output_dir, exist_ok=True)  # the folder may have been deleted since the service started
        dst = make_output_path(src, output_dir)
        try:
            proc = subprocess.Popen(
                [hdrfix_path, src, dst], shell=False, creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            return failed(HDRFIX_MISSING)
        except OSError as e:
            return failed(f"Couldn't run hdrfix: {e.strerror or e}")
        if on_process:
            on_process(proc)
        try:
            out, err = proc.communicate(timeout=HDRFIX_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return failed(f"hdrfix timed out after {HDRFIX_TIMEOUT} s")
        except Exception:
            _kill(proc)
            raise
        finally:
            if on_process:
                on_process(None)
        detail = _tail(err) or _tail(out)
        if proc.returncode != 0:
            return failed(detail or f"hdrfix failed (exit {proc.returncode})")
        # hdrfix exits 0 on some failures (bad or missing input) and only prints to stderr, so trust the output file.
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 0):
            return failed(detail or "hdrfix didn't produce an image")
        note = _recycle(src, dst) if delete_original else ""
        log.info(f"Converted: {src} -> {dst}")
        return ConversionResult(True, src, dst, note, bool(delete_original) and not note, time.time())
    except Exception as e:
        log.error(f"Unexpected error converting {src}", exc_info=True)
        return failed(f"Unexpected error: {e}")
