"""Converting one file: wait for it to settle, run hdrfix, verify the output, optionally recycle the original.

No Qt, no tkinter. convert_file never raises.
"""
import ctypes
import logging
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager

from send2trash import send2trash

from .models import TONE_PRESETS, ConversionResult
from .paths import APP_NAME, HDRFIX_MISSING

log = logging.getLogger(APP_NAME)

HDRFIX_TIMEOUT = 120
DRIVE_FIXED = 3  # GetDriveTypeW: only fixed local drives have a Recycle Bin we can trust
REMOVED_MSG = "The file was removed before it could be converted"
NEVER_WRITTEN_MSG = "The file never finished being written"
NO_BIN_MSG = "Converted, but the original was kept (Recycle Bin isn't available on that drive)"
INCOMPLETE_MSG = "Converted, but the original was kept (output looked incomplete)"
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


def _looks_complete(path):
    """True if the file starts with the JPEG start marker (FF D8) and ends with the end marker (FF D9)."""
    with open(path, "rb") as f:
        head = f.read(2)
        if len(head) < 2:
            return False
        f.seek(-2, os.SEEK_END)
        return head == b"\xff\xd8" and f.read(2) == b"\xff\xd9"


def _recycle(src_path, output_path):
    """Recycle src_path only if output_path is a whole JPEG and src is on a fixed drive. Returns "" or a UI-ready reason.

    send2trash's legacy Windows backend (no pywin32, e.g. frozen builds) can permanently delete when the bin can't
    take a file (network/removable drive, over the bin's cap), so anything but a fixed drive keeps the original.
    """
    try:
        if not _looks_complete(output_path):
            log.error(f"Original kept, not recycled ({src_path}): {output_path} doesn't look like a complete JPEG")
            return INCOMPLETE_MSG
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


@contextmanager
def _hdrfix_input(src):
    """The path to hand hdrfix. It only accepts a lowercase ".jxr" ("Invalid input file type" for .JXR or .Jxr), so
    any other spelling gets a lowercase-named hard link (or a copy, across volumes) in a temp folder, removed on exit.
    """
    if os.path.splitext(src)[1] == ".jxr":
        yield src
        return
    tmp = tempfile.mkdtemp(prefix="autohdr-")
    try:
        link = os.path.join(tmp, "input.jxr")  # a short fixed name: the original's could push the path past MAX_PATH
        try:
            os.link(src, link)
        except OSError:
            shutil.copyfile(src, link)
        yield link
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_hdrfix(src, dst, hdrfix_path, tone_preset, on_process, on_ready):
    """Run hdrfix once. Returns "" on success, a short reason on failure, or None if on_ready() said not to start."""
    with _hdrfix_input(src) as hdr_src:
        if on_ready and not on_ready():  # checked as late as possible, right before the child exists
            return None
        try:
            proc = subprocess.Popen(
                [hdrfix_path, hdr_src, dst, *TONE_PRESETS.get(tone_preset, TONE_PRESETS["natural"])[1]],
                shell=False, creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            return HDRFIX_MISSING
        except OSError as e:
            return f"Couldn't run hdrfix: {e.strerror or e}"
        if on_process:
            on_process(proc)
        try:
            out, err = proc.communicate(timeout=HDRFIX_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return f"hdrfix timed out after {HDRFIX_TIMEOUT} s"
        except Exception:
            _kill(proc)
            raise
        finally:
            if on_process:
                on_process(None)
    detail = _tail(err) or _tail(out)
    if proc.returncode != 0:
        return detail or f"hdrfix failed (exit {proc.returncode})"
    # hdrfix exits 0 on some failures (bad or missing input) and only prints to stderr, so trust the output file.
    if not (os.path.isfile(dst) and os.path.getsize(dst) > 0):
        return detail or "hdrfix didn't produce an image"
    return ""


def convert_file(src, output_dir, hdrfix_path, delete_original, on_process=None, tone_preset="natural", on_ready=None):
    """Convert one .jxr to <stem>-sdr.jpg. NEVER raises; returns a ConversionResult, or None when there is nothing to
    report: the file vanished while waiting for it to settle, or on_ready() returned False.

    on_ready() is called once the file has settled, right before hdrfix starts; return False to not start it (the app is
    closing). on_process(proc) is called with the hdrfix child when it starts and with None when it is done, so the
    caller can terminate it. On any failure a partial or empty output is deleted and the original is kept. tone_preset
    is a key of TONE_PRESETS whose extra hdrfix options follow the two paths; an unknown key means "natural" (no extras).
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
            log.info(f"Skipped {src}: it was removed before conversion started")
            return None
        except TimeoutError:
            return failed(NEVER_WRITTEN_MSG)
        os.makedirs(output_dir, exist_ok=True)  # the folder may have been deleted since the service started
        dst = make_output_path(src, output_dir)
        error = _run_hdrfix(src, dst, hdrfix_path, tone_preset, on_process, on_ready)
        if error is None:
            return None
        if error:
            return failed(error)
        note = _recycle(src, dst) if delete_original else ""
        log.info(f"Converted: {src} -> {dst}")
        return ConversionResult(True, src, dst, note, bool(delete_original) and not note, time.time())
    except Exception as e:
        log.error(f"Unexpected error converting {src}", exc_info=True)
        return failed(f"Unexpected error: {e}")
