# Auto HDR-SDR Converter — UI rebuild and code split

Date: 2026-11-23 · Status: approved to build by the project owner ("spin up agents to complete the UI rebuild … happy to split the code")

## Intent
The app watches a folder for HDR screenshots (`.jxr`), converts each to a JPG with `hdrfix.exe`, and can recycle the original. The 2025 version works but is a single 430-line Tkinter file with the default Windows-95 look, no feedback (you can't tell it converted anything or that something failed), and a "press Start every launch" workflow. Goal: a polished, professional Windows app that feels automatic and shows what it is doing.

Audience: the owner plus other gamers / flight-sim users who download a release.

## Goals
- Modern Windows 11 look, light/dark following Windows, one system-accent colour, crisp at any DPI.
- Live status ("Watching…", "Converting x.jxr…", "Paused", errors) and a scrollable activity list (success/failure, output name, relative time, Retry, Show in folder / Open).
- Proper tray icon (state shown, a click opens the window, Pause/Resume, Quit) and toast notifications.
- Settings apply live (no "Start then Restart"); inline validation of folders and a missing hdrfix.
- Watching starts automatically when the app opens (setting, default on).
- Second launch brings the existing window to the front instead of an error box.
- Code split into a small package with a Qt-free core that is unit-tested.

## Non-goals (do NOT build)
hdrfix tone-map/exposure settings or presets, "Start with Windows", converting files already in the folder, multiple watch folders, subfolder watching, auto-updater, code signing, installer changes, version-resource metadata, history persisted across launches, any new permanent-delete option.

## Decisions (owner delegated the "how"; these are my calls)
- **Stay in Python; UI on PySide6 (Qt 6)** — `PySide6-Essentials` (already in `.venv`, requirement `PySide6-Essentials`). Qt gives a native tray, toasts, signals/slots across threads, dark-mode. A rewrite in another language gains nothing: hdrfix does the real work. Drop Tkinter, pystray and Pillow.
- **Core stays Qt-free** (plain Python + `watchdog` + `send2trash`) so it is testable without a display. UI layer talks to it via dataclass events.
- **One worker thread + queue** converts files (watchdog's dispatch thread only enqueues). The in-flight key set covers queued + running paths, so duplicate events are really deduped.
- Windows only. Python 3.14 in `.venv` (`.venv\Scripts\python.exe`). Run tests with `QT_QPA_PLATFORM=offscreen`.
- Config keys stay backward compatible with the existing `%APPDATA%\AutoHDRConvert\config.json`; new keys are additive.
- `output_dir == ""` now means "same folder as the watch folder" (the output is `.jpg`, so no loop).
- "Delete Original Files" is shown to users as **After converting: ○ Keep the original ● Move original to Recycle Bin** (stored as `delete_original`). There is no permanent delete.

## Layout
```
AutoHDRConvert.py          thin entry: from autohdr.app import main; raise SystemExit(main())
autohdr/
  __init__.py              __version__ = "2.0.0.dev0"  (placeholder; owner sets at release)
  models.py                shared dataclasses (OWNED BY LEAD — do not edit; report needed changes)
  paths.py                 app_dir, resource_path, app_data_dir, config_path, log_path, find_hdrfix   [core]
  config.py                load_settings / save_settings (+ legacy fallback)                          [core]
  logs.py                  setup_logging(output_to_log)                                                [core]
  convert.py               make_output_path, wait_for_file, trash_original, convert_file               [core]
  service.py               ConversionService, validate_settings                                        [core]
  ui/
    __init__.py
    theme.py               palettes, QSS, follows Windows light/dark + accent                          [ui]
    icons.py               vector-painted glyphs + tray/app icons (no external assets)                 [ui]
    main_window.py         MainWindow + activity list                                                  [ui]
    settings_dialog.py     SettingsDialog                                                              [ui]
    tray.py                Tray (QSystemTrayIcon wrapper)                                              [shell]
  instance.py              single-instance guard via QLocalServer/QLocalSocket                         [shell]
  app.py                   main(): QApplication, wiring, auto-start, quit                              [shell]
tests/                     unittest; core tests per module [core], tests/test_ui.py [ui], tests/test_app.py [shell]
```
Ownership is strict: an agent edits only the files tagged with its role.

## Shared contract (`autohdr/models.py`, already written — import, don't redefine)
`Settings(input_dir, output_dir, delete_original, minimize_to_tray, output_to_log, notifications: "failures"|"all"|"off", auto_start)`
`ConversionResult(ok, src, dst, message, original_removed, finished_at)` — `message` is "" normally, a short human-readable reason on failure, or a note when `ok=True` but the original was kept (e.g. Recycle Bin unavailable on that drive).
`Problems(input, output, tool)` — "" means fine; `.any` is True if any is set.
Events delivered to `on_event` (called from NON-UI threads): `StateChanged(running, error)`, `ConversionStarted(src)`, `ConversionFinished(result)`.

### Core API (`[core]`)
```python
# paths.py   (same behaviour as the current AutoHDRConvert.py helpers; APPDATA read at call time)
app_dir(); resource_path(name); app_data_dir(); config_path(); log_path(); find_hdrfix() -> str | None
# config.py
load_settings() -> Settings      # new file, else legacy config.json beside the exe, else defaults; wrong types/unknown keys ignored; corrupt -> defaults; never raises
save_settings(s: Settings) -> bool
# logs.py
setup_logging(output_to_log: bool) -> None   # rotating 1 MB file in %APPDATA%\AutoHDRConvert\AutoHDRConvert.log; no StreamHandler if sys.stderr is None; idempotent
# convert.py
make_output_path(src, output_dir) -> str                 # <stem>-sdr.jpg, then -sdr-1.jpg ...
wait_for_file(path, timeout=10, settle=0.5) -> None
trash_original(src, dst) -> bool
convert_file(src, output_dir, hdrfix_path, delete_original) -> ConversionResult
    # NEVER raises. wait_for_file -> hdrfix (list argv, shell=False, CREATE_NO_WINDOW, capture_output, timeout=120)
    # -> verify dst exists AND non-empty -> optionally trash_original. On ANY failure: delete a partial/empty dst we created,
    # keep the original, return ok=False with a short message (hdrfix stderr tail, timeout, file never appeared, ...).
# service.py
validate_settings(s: Settings) -> Problems    # input exists & is a dir; output creatable (blank = same as input); hdrfix found (tool)
class ConversionService:
    def __init__(self, on_event: Callable[[ServiceEvent], None], settings: Settings | None = None)
    running: bool  (property)
    def configure(self, settings: Settings) -> None   # live-apply: output_dir/delete_original take effect for the next file; a changed input_dir restarts the observer if running
    def start(self) -> str      # "" on success, else a user-facing error; emits StateChanged(running, error). Idempotent: restarts cleanly if already running.
    def stop(self) -> None      # non-blocking from the caller's view (no UI stall), idempotent; emits StateChanged(False, "")
    def retry(self, src: str) -> None                 # re-enqueue a failed source if it still exists
    def shutdown(self, timeout: float = 3.0) -> None  # stop + join observer/worker for app quit
# Watches the top level only; on_created + on_moved(dest); .jxr matched case-insensitively; every ConversionStarted is followed by exactly one ConversionFinished.
```

### UI API (`[ui]`) — what the shell relies on
```python
class MainWindow(QMainWindow):
    # window -> app
    settings_changed = Signal(object)     # Settings; emitted on any user edit (debounce text edits ~400 ms)
    toggle_requested = Signal()           # Pause / Resume button
    retry_requested  = Signal(str)        # src path of a failed row
    quit_requested   = Signal()           # Quit button / Ctrl+Q / close when not minimising to tray
    hidden_to_tray   = Signal()           # close button hid the window (shell shows a one-time toast)
    # app -> window
    def __init__(self, settings: Settings, tray_available: bool)
    def set_settings(self, s: Settings) -> None        # populate without emitting settings_changed
    def set_running(self, running: bool, error: str = "") -> None
    def set_converting(self, src: str | None) -> None  # header shows "Converting <name>…" or reverts
    def add_activity(self, result: ConversionResult) -> None   # newest on top; keeps last 100; counts "N converted this session"
    def set_problems(self, p: Problems) -> None        # inline messages under the fields; disables Resume when input/tool broken
    def show_and_raise(self) -> None
class SettingsDialog(QDialog)   # opened from the window's Settings button; edits write back through MainWindow.settings_changed
theme.apply(app: QApplication) -> None         # installs palette+QSS, re-applies on colorSchemeChanged / accent change
icons.app_icon() -> QIcon;  icons.tray_icon(running: bool) -> QIcon   # paused = desaturated + pause badge
```
```python
class Tray(QObject):                           # [shell]
    show_requested = Signal(); toggle_requested = Signal(); quit_requested = Signal()
    def __init__(self, running: bool); def set_running(self, running: bool); def available() -> bool
    def notify(self, title: str, message: str, error: bool = False)
```
Qt signals emitted from worker threads are queued automatically — the shell bridges `on_event` into a `QObject` signal; **no widget is ever touched off the main thread**.

## UI spec
Window ~560×640, resizable, min 520×560, native title bar, app icon. 8-px spacing grid, 12-px radius cards, Segoe UI Variable / Segoe UI, system accent for the primary action (fallback `#0F6CBD`). Text ≥ 4.5:1 contrast in both schemes, visible keyboard focus, logical tab order, accessible names on icon-only buttons.

```
┌──────────────────────────────────────────────┐
│ ┌──────────────────────────────────────────┐ │  status card  (state: running | paused | error | idle)
│ │ ● Watching for HDR screenshots   [Pause] │ │   title + subtitle (watched path, elided middle)
│ │   C:\Users\…\Screenshots                 │ │   or "Converting <file>…" while busy
│ └──────────────────────────────────────────┘ │
│  Watch folder   [ C:\…\Screenshots   ][Browse]│  inline error text under field when invalid
│  Save JPGs to   [ Same folder as originals ][Browse]│  placeholder when blank; small "Reset" clears
│  After converting  ◉ Keep the original  ○ Move original to Recycle Bin │
│  Recent activity                  12 converted this session │
│ ┌──────────────────────────────────────────┐ │
│ │ ✓ MSFS_2026-11-23.jxr → …-sdr.jpg  2s ago│ │  row actions on hover/focus: Show in folder · Open
│ │ ✗ broken.jxr — hdrfix failed   [Retry]   │ │  failed rows: error colour + Retry
│ └──────────────────────────────────────────┘ │  empty state: "No conversions yet. New .jxr files in <folder> appear here."
│  [⚙ Settings]                         [Quit] │
└──────────────────────────────────────────────┘
```
Copy: Running "Watching for HDR screenshots" · Paused "Paused — new screenshots won't be converted" (button "Resume") · Idle/unconfigured "Choose a folder to start" · Error "Can't watch this folder" + reason · Missing hdrfix "hdrfix.exe wasn't found. Put it next to the app or add it to PATH." Relative times ("just now", "2s ago", "5 min ago") refresh every 30 s. Settings dialog: *Close button* (○ Quit ◉ Keep running in the system tray — disabled with a note if no tray), *Notifications* (Failures only · All conversions · Off), *Start watching when the app opens*, *Write a log file* + "Open log folder", About (app name, version, hdrfix path found, HDRFix credit line + link `https://github.com/bvibber/hdrfix`).

## Behaviour
- Launch: load settings → build service → if `auto_start` and validation passes → `service.start()`; otherwise idle with inline problems. Single instance (second launch raises the first window and exits 0).
- Any settings edit → `settings_changed` → save (debounced) → `service.configure` → revalidate → `set_problems`; changing the watch folder while running restarts watching.
- Close button: tray available and `minimize_to_tray` → hide + one-time toast; else quit. Tray: double-click/Open, Pause/Resume, Quit; tooltip "Auto HDR-SDR Converter — Watching/Paused". Quit = stop service, save, hide tray, exit.
- Toasts: failures always (unless Off); successes only when "All conversions".
- Failures never delete the original or leave partial JPGs. Delete = Recycle Bin only, after a verified output.
- No blocking call on the UI thread (no `observer.join`, no sleeps).

## Testing
Core: unit tests per module (migrate and extend the existing 30 in `tests/test_core.py`, which import the old module and will be deleted once migrated). UI/shell: `QT_QPA_PLATFORM=offscreen` unittest smoke tests; builders must also render the window to PNG via `widget.grab()` in both light and dark and inspect the images (scratchpad). Run everything: `.venv\Scripts\python.exe -m unittest discover -s tests`.

## Left to the builders
Exact QSS values/colours, icon drawing, row widget structure, debounce timings, internal helper names — as long as the contracts above hold.
