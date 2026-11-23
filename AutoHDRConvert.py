import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from tkinter import Tk, Label, Entry, Button, Checkbutton, IntVar, filedialog, messagebox
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image
import pystray
from pystray import MenuItem as item, Menu
from send2trash import send2trash

APP_NAME = "AutoHDRConvert"
TRAY_TITLE = "HDR to SDR Converter"
MUTEX_NAME = "Local\\AutoHDRConvert_SingleInstance"
CONFIG_DEFAULTS = {
    "input_dir": "",
    "output_dir": "",
    "delete_original": False,
    "minimize_to_tray": False,
    "output_to_log": False,
}

log = logging.getLogger(APP_NAME)
_log_handlers = []


def app_dir():
    # Frozen: the folder holding the exe, so users can drop in a newer hdrfix.exe next to it.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


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


def find_hdrfix():
    """Full path to hdrfix.exe: app folder, then bundled copy, then PATH; None if not found."""
    for base in (app_dir(), getattr(sys, "_MEIPASS", None)):
        if base and os.path.isfile(os.path.join(base, "hdrfix.exe")):
            return os.path.join(base, "hdrfix.exe")
    return shutil.which("hdrfix")


def read_config():
    """Config dict with defaults filled in. Uses the legacy config beside the exe only if the new one doesn't exist."""
    path = config_path()
    if not os.path.exists(path):
        path = os.path.join(app_dir(), "config.json")
    if not os.path.exists(path):
        return dict(CONFIG_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: data.get(k, d) for k, d in CONFIG_DEFAULTS.items()}
    except Exception as e:
        log.error(f"Error loading config {path}: {e}")
        return dict(CONFIG_DEFAULTS)


def write_config(config):
    try:
        os.makedirs(app_data_dir(), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        log.error(f"Error saving config: {e}")
        return False


def setup_logging(output_to_log):
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
    """Block until the file is non-empty and its size has been unchanged for `settle` seconds."""
    start = time.monotonic()
    last_size, last_change = -1, start
    while True:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = -1
        now = time.monotonic()
        if size != last_size:
            last_size, last_change = size, now
        elif size > 0 and now - last_change >= settle:
            return
        if now - start > timeout:
            raise TimeoutError(f"File {file_path} is still changing or unavailable.")
        time.sleep(0.1)


def trash_original(src_path, output_path):
    """Recycle src_path only if output_path exists and is non-empty; otherwise keep it. Returns True if recycled."""
    try:
        if os.path.getsize(output_path) <= 0:
            raise OSError("output file is empty")
        send2trash(os.path.abspath(src_path))
        return True
    except Exception as e:
        log.error(f"Original kept, not deleted ({src_path}): {e}")
        return False


def another_instance_running(name=MUTEX_NAME):
    """Take a named mutex (held until process exit); True if another instance already owns it."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


class HDRToSDRConverter:
    def __init__(self, on_error=None):
        self.input_dir = ""
        self.output_dir = ""
        self.delete_original = False
        self.minimize_to_tray = False
        self.output_to_log = False
        self.observer = None
        self.is_watcher_running = False
        self.on_error = on_error
        self.load_config()

    def load_config(self):
        config = read_config()
        self.input_dir = config["input_dir"]
        self.output_dir = config["output_dir"]
        self.delete_original = config["delete_original"]
        self.minimize_to_tray = config["minimize_to_tray"]
        self.output_to_log = config["output_to_log"]

    def save_config(self):
        write_config({k: getattr(self, k) for k in CONFIG_DEFAULTS})

    def _fail_start(self, message):
        log.error(message)
        if threading.current_thread() is threading.main_thread():  # never pop Tk dialogs from other threads
            messagebox.showerror("Error", message)
        return False

    def start_watcher(self):
        """(Re)start watching with the current settings. Returns True on success."""
        self.stop_watcher()  # so pressing Start again applies changed settings instead of leaking an observer
        if not self.input_dir or not os.path.isdir(self.input_dir):
            return self._fail_start(f"Input directory does not exist or is not a folder:\n{self.input_dir}")
        if not self.output_dir:
            return self._fail_start("Please specify an output directory.")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            return self._fail_start(f"Cannot create output directory:\n{self.output_dir}\n{e}")
        hdrfix = find_hdrfix()
        if hdrfix is None:
            return self._fail_start("hdrfix.exe was not found. Put it next to this app or add it to PATH.")

        observer = Observer()
        try:
            handler = ConversionHandler(self.output_dir, self.delete_original, hdrfix, self.on_error)
            observer.schedule(handler, self.input_dir, recursive=False)
            observer.start()
        except Exception as e:
            return self._fail_start(f"Could not start watching {self.input_dir}:\n{e}")
        self.observer = observer
        self.is_watcher_running = True
        self.save_config()
        return True

    def stop_watcher(self):
        observer, self.observer = self.observer, None
        self.is_watcher_running = False
        if observer:
            observer.stop()
            # join() can block for a whole conversion, so keep it off the UI thread
            threading.Thread(target=observer.join, daemon=True).start()


class ConversionHandler(FileSystemEventHandler):
    def __init__(self, output_dir, delete_original, hdrfix_path, on_error=None):
        self.output_dir = output_dir
        self.delete_original = delete_original
        self.hdrfix_path = hdrfix_path
        self.on_error = on_error
        self._inflight = set()
        self._lock = threading.Lock()

    def on_created(self, event):
        self._handle(event.src_path, event.is_directory)

    def on_moved(self, event):  # files are often renamed into place
        self._handle(event.dest_path, event.is_directory)

    def _handle(self, path, is_directory):
        if is_directory or os.path.splitext(path)[1].lower() != ".jxr":
            return
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        try:
            self.convert_to_sdr(path)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _fail(self, message):
        log.error(message)
        if self.on_error:
            self.on_error(message)

    def convert_to_sdr(self, file_path):
        name = os.path.basename(file_path)
        try:
            wait_for_file(file_path)
            output_file = make_output_path(file_path, self.output_dir)
            subprocess.run(
                [self.hdrfix_path, file_path, output_file],
                shell=False, creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True, text=True, errors="replace", timeout=120, check=True,
            )
            log.info(f"Converted: {file_path} -> {output_file}")
            if self.delete_original:
                trash_original(file_path, output_file)
        except subprocess.CalledProcessError as e:
            self._fail(f"hdrfix failed for {name} (exit {e.returncode}): {(e.stderr or '').strip()}")
        except Exception as e:
            self._fail(f"Error processing {name}: {e}")


def create_tray_icon(root, show_app, hide_app, toggle_watcher, exit_app):
    def ui(fn):
        # pystray callbacks run on its own thread; Tk may only be touched from the main thread.
        return lambda: root.after(0, fn)

    tray_menu = Menu(
        item("Show App", ui(show_app), default=True),
        item("Hide App", ui(hide_app)),
        item("Toggle Watcher", ui(toggle_watcher)),
        item("Exit", ui(exit_app)),
    )
    icon_image = Image.open(resource_path("icon.png"))
    tray_icon = pystray.Icon(TRAY_TITLE, icon_image, TRAY_TITLE, tray_menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()
    return tray_icon


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    if another_instance_running():
        ctypes.windll.user32.MessageBoxW(0, "Auto HDR-SDR Converter is already running (check the system tray).", TRAY_TITLE, 0x40)
        return

    converter = HDRToSDRConverter()
    setup_logging(converter.output_to_log)
    root = Tk()
    root.title(TRAY_TITLE)
    tray_hint_shown = False

    def apply_settings():
        converter.input_dir = input_dir_entry.get()
        converter.output_dir = output_dir_entry.get()
        converter.delete_original = bool(delete_original_var.get())
        converter.minimize_to_tray = bool(minimize_to_tray_var.get())
        converter.output_to_log = bool(output_to_log_var.get())

    def update_watcher_status():
        if converter.is_watcher_running:
            watcher_status_label.config(text="Auto Converter is running", fg="green")
            tray_icon.title = f"{TRAY_TITLE} (Running)"
        else:
            watcher_status_label.config(text="Auto Converter is not running", fg="red")
            tray_icon.title = f"{TRAY_TITLE} (Stopped)"

    def start():
        apply_settings()
        setup_logging(converter.output_to_log)
        if converter.start_watcher():
            log.info("Converter Started")
        update_watcher_status()

    def pause():
        converter.stop_watcher()
        update_watcher_status()
        log.info("Converter Paused")

    def toggle_watcher():
        if converter.is_watcher_running:
            pause()
        else:
            start()

    def exit_app():
        apply_settings()
        converter.stop_watcher()
        converter.save_config()
        log.info("Converter Ended")
        tray_icon.stop()
        root.quit()

    def show_app():
        root.deiconify()
        root.lift()

    def notify_error(message):
        try:
            tray_icon.notify(message[:250], TRAY_TITLE)  # balloon text is length-limited; support varies by platform
        except Exception as e:
            log.warning(f"Tray notification failed: {e}")

    def on_closing():
        nonlocal tray_hint_shown
        apply_settings()
        converter.save_config()
        if not converter.minimize_to_tray:
            exit_app()
            return
        root.withdraw()  # Minimize to tray instead of closing
        if not tray_hint_shown:
            tray_hint_shown = True
            try:
                tray_icon.notify("The app is still running in the system tray.", TRAY_TITLE)
            except Exception as e:
                log.warning(f"Tray notification failed: {e}")

    root.protocol("WM_DELETE_WINDOW", on_closing)

    Label(root, text="Input Directory:").grid(row=0, column=0, padx=10, pady=5, sticky="e")
    input_dir_entry = Entry(root, width=40)
    input_dir_entry.insert(0, converter.input_dir)
    input_dir_entry.grid(row=0, column=1, padx=5, pady=5)
    Button(root, text="Browse", command=lambda: (
        new_dir := filedialog.askdirectory(),
        input_dir_entry.delete(0, "end") if new_dir else None,  # Clear only if new_dir is not empty
        input_dir_entry.insert(0, new_dir) if new_dir else None  # Insert only if new_dir is not empty
    )).grid(row=0, column=2, padx=(20, 20))

    Label(root, text="Output Directory:").grid(row=1, column=0, padx=10, pady=5, sticky="e")
    output_dir_entry = Entry(root, width=40)
    output_dir_entry.insert(0, converter.output_dir)
    output_dir_entry.grid(row=1, column=1, padx=5, pady=5)
    Button(root, text="Browse", command=lambda: (
        new_dir := filedialog.askdirectory(),
        output_dir_entry.delete(0, "end") if new_dir else None,
        output_dir_entry.insert(0, new_dir) if new_dir else None
    )).grid(row=1, column=2, padx=(20, 20))

    delete_original_var = IntVar(value=1 if converter.delete_original else 0)
    Checkbutton(root, text="Delete Original Files", variable=delete_original_var).grid(row=2, column=0, padx=5, pady=5, sticky="w")

    minimize_to_tray_var = IntVar(value=1 if converter.minimize_to_tray else 0)
    Checkbutton(root, text="Minimize to Tray", variable=minimize_to_tray_var).grid(row=2, column=1, padx=5, pady=5)

    output_to_log_var = IntVar(value=1 if converter.output_to_log else 0)
    Checkbutton(root, text="Output to Log", variable=output_to_log_var).grid(row=2, column=2, padx=5, pady=5)

    watcher_status_label = Label(root, text="Auto Converter is not running", font=("Helvetica", 12), fg="red")
    watcher_status_label.grid(row=3, column=1, pady=5)

    Label(root, text="Start applies any changed settings").grid(row=4, column=1, padx=10, pady=5, sticky="e")

    Button(root, text="Start", command=start).grid(row=5, column=0, pady=10)
    Button(root, text="Pause", command=pause).grid(row=5, column=1, pady=10)
    Button(root, text="Exit", command=exit_app).grid(row=5, column=2, pady=10)

    tray_icon = create_tray_icon(root, show_app, root.withdraw, toggle_watcher, exit_app)
    converter.on_error = notify_error
    update_watcher_status()

    root.mainloop()


if __name__ == "__main__":
    main()
