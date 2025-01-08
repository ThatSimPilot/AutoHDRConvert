import os
import json
import sys
import threading
import time
import logging
from tkinter import Tk, Label, Entry, Button, Checkbutton, IntVar, filedialog, messagebox
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image, ImageTk
import pystray
from pystray import MenuItem as item, Menu
import subprocess

CONFIG_FILE = "config.json"

class HDRToSDRConverter:
    def __init__(self):
        self.input_dir = ""
        self.output_dir = ""
        self.delete_original = False
        self.minimize_to_tray = False
        self.output_to_log = False
        self.observer = None
        self.load_config()
        self.is_watcher_running = False
        self.logger = None
        self.setup_logging()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                self.input_dir = config.get("input_dir", "")
                self.output_dir = config.get("output_dir", "")
                self.delete_original = config.get("delete_original", False)
                self.minimize_to_tray = config.get("minimize_to_tray", False)
                self.output_to_log = config.get("output_to_log", False)
            except Exception as e:
                logging.info(f"Error loading config: {e}")
                self.reset_config()
        else:
            self.reset_config()

    def save_config(self):
        config = {
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "delete_original": self.delete_original,
            "minimize_to_tray": self.minimize_to_tray,
            "output_to_log": self.output_to_log,
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logging.info(f"Error saving config: {e}")
    
    def setup_logging(self):
        if self.output_to_log:
            log_file = os.path.join(self.output_dir, "z_log.txt")
            logging.basicConfig(filename=log_file, filemode='w', level=logging.INFO, format="%(asctime)s - %(message)s")
            self.logger = logging.getLogger()
        else:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
            self.logger = logging.getLogger()

    def reset_config(self):
        self.input_dir = ""
        self.output_dir = ""
        self.delete_original = False
        self.minimize_to_tray = False
        self.output_to_log = False

    def start_watcher(self):
        if not self.input_dir or not self.output_dir:
            messagebox.showerror("Error", "Please specify both input and output directories.")
            return

        self.save_config()
        event_handler = ConversionHandler(self.output_dir, self.delete_original, self.minimize_to_tray, self.output_to_log)
        self.observer = Observer()
        self.observer.schedule(event_handler, self.input_dir, recursive=False)
        self.observer.start()
        self.is_watcher_running = True

    def stop_watcher(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.is_watcher_running = False


class ConversionHandler(FileSystemEventHandler):
    def __init__(self, output_dir, delete_original, minimize_to_tray, output_to_log):
        self.output_dir = output_dir
        self.delete_original = delete_original
        self.minimize_to_tray = minimize_to_tray
        self.output_to_log = output_to_log

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".jxr"):
            time.sleep(1)  # Wait for the file to be fully written
            self.convert_to_sdr(event.src_path)

    def convert_to_sdr(self, file_path):
        try:
            self.wait_for_file(file_path)

            output_file = os.path.join(self.output_dir, os.path.basename(file_path).replace(".jxr", "-sdr.jpg"))

            subprocess.run(["hdrfix", file_path, output_file], check=True)

            if self.delete_original:
                os.remove(file_path)

            logging.info(f"Converted: {file_path} -> {output_file}")
        except Exception as e:
            logging.info(f"Error processing {file_path}: {e}")
        

    def wait_for_file(self, file_path, timeout=10):
        start_time = time.time()
        while True:
            try:
                with open(file_path, 'rb'):
                    break
            except IOError:
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"File {file_path} is locked or unavailable.")
                time.sleep(0.1)


def create_tray_icon(root, converter):
    def show_app():
        root.deiconify()

    def hide_app():
        root.withdraw()

    def toggle_watcher():
        if converter.is_watcher_running:
            converter.stop_watcher()
            tray_icon.title = "HDR to SDR Converter (Stopped)"
        else:
            converter.start_watcher()
            tray_icon.title = "HDR to SDR Converter (Running)"

    def exit_app():
        tray_icon.stop()
        root.quit()

    tray_menu = Menu(
        item("Show App", lambda: show_app()),
        item("Hide App", lambda: hide_app()),
        item("Toggle Watcher", lambda: toggle_watcher()),
        item("Exit", lambda: exit_app()),
    )
    icon_image = Image.open("icon.png")  # Replace with your own .png icon
    tray_icon = pystray.Icon("HDR to SDR Converter", icon_image, "HDR to SDR Converter", tray_menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()
    return tray_icon


def main():
    converter = HDRToSDRConverter()
    root = Tk()
    root.title("HDR to SDR Converter")

    def on_closing():
        setattr(converter, "input_dir", input_dir_entry.get()),
        setattr(converter, "output_dir", output_dir_entry.get()),
        setattr(converter, "delete_original", bool(delete_original_var.get())),
        setattr(converter, "minimize_to_tray", bool(minimize_to_tray_var.get()))
        setattr(converter, "output_to_log", bool(output_to_log_var.get()))
        converter.save_config()       
        if minimize_to_tray_var.get():
            root.withdraw()  # Minimize to tray instead of closing
            messagebox.showinfo("Minimized", "The app is now running in the system tray.")
        else:
            logging.info("Converter Ended")
            root.quit()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    Label(root, text="Input Directory:").grid(row=0, column=0, padx=10, pady=5, sticky="e")
    input_dir_entry = Entry(root, width=40)
    input_dir_entry.insert(0, converter.input_dir)
    input_dir_entry.grid(row=0, column=1, padx=5, pady=5)
    Button(root, text="Browse", command=lambda: input_dir_entry.insert(0, filedialog.askdirectory())).grid(row=0, column=2, padx=(20,20))

    Label(root, text="Output Directory:").grid(row=1, column=0, padx=10, pady=5, sticky="e")
    output_dir_entry = Entry(root, width=40)
    output_dir_entry.insert(0, converter.output_dir)
    output_dir_entry.grid(row=1, column=1, padx=5, pady=5)
    Button(root, text="Browse", command=lambda: output_dir_entry.insert(0, filedialog.askdirectory())).grid(row=1, column=2, padx=(20,20))

    delete_original_var = IntVar(value=1 if converter.delete_original else 0)
    Checkbutton(root, text="Delete Original Files", variable=delete_original_var).grid(row=2, column=0, padx=5, pady=5, sticky="w")

    minimize_to_tray_var = IntVar(value=1 if converter.minimize_to_tray else 0)
    Checkbutton(root, text="Minimize to Tray", variable=minimize_to_tray_var).grid(row=2, column=1, padx=5, pady=5, sticky="w")

    output_to_log_var = IntVar(value=1 if converter.output_to_log else 0)
    Checkbutton(root, text="Output to Log", variable=output_to_log_var).grid(row=2, column=2, padx=5, pady=5, sticky="w")

    watcher_status_label = Label(root, text="Auto Converter is not running", font=("Helvetica", 12), fg="red")
    watcher_status_label.grid(row=3, column=1, pady=5)

    def update_watcher_status():
        if converter.is_watcher_running:
            watcher_status_label.config(text="Auto Converter is running", fg="green")
        else:
            watcher_status_label.config(text="Auto Converter is not running", fg="red")

    Label(root, text="Click Start then Restart to Apply Log Settings").grid(row=4, column=1, padx=10, pady=5, sticky="e")

    Button(root, text="Start", command=lambda: (
        setattr(converter, "input_dir", input_dir_entry.get()),
        setattr(converter, "output_dir", output_dir_entry.get()),
        setattr(converter, "delete_original", bool(delete_original_var.get())),
        setattr(converter, "minimize_to_tray", bool(minimize_to_tray_var.get())),
        setattr(converter, "output_to_log", bool(output_to_log_var.get())),
        converter.setup_logging(),
        converter.start_watcher(),
        update_watcher_status(),
        logging.info("Converter Started"),
    )).grid(row=5, column=0, pady=10)

    Button(root, text="Pause", command=lambda: (converter.stop_watcher(), update_watcher_status(), logging.info("Converter Paused"))).grid(row=5, column=1, pady=10)
    Button(root, text="Exit", command=lambda: (converter.save_config(), logging.info("Converter Ended"), root.quit())).grid(row=5, column=2, pady=10)

    create_tray_icon(root, converter)

    root.mainloop()


if __name__ == "__main__":
    main()
