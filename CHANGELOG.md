# Changelog

All notable changes to Auto HDR-SDR Converter. Version 2 is a rebuild of the original single-file script (v1).

## [2.0.0] - 2026-11-25

### Highlights
* **Colour-accurate conversion.** v1 ran HDRFix with its defaults, which dimmed the image and bleached bright areas (sunlit sky, clouds, paint) toward white. v2 uses tone presets tuned against the original HDR data, so brightness, colour and saturation stay much closer to the HDR screenshot.
* **New interface.** The Tk window is replaced by a Qt (PySide6) interface with a status card, folder settings, a recent-activity list and a proper Settings dialog.
* **One standalone exe.** No installer. Download, put it anywhere, run it. Settings, log location and the Start menu shortcut are all controlled from inside the app.

### Added
* **Tone presets** in Settings:
  * **Match original** (default): reproduces the HDR image as closely as an SDR file allows, assuming a 203 nit SDR white level.
  * **Brighter**: the same, as if SDR white were 150 nits (about 0.4 stop brighter).
  * Both keep the colour of bright areas instead of clipping them to white, and keep cloud and highlight detail. Changes apply to the next converted file.
* **Settings dialog** with: close behaviour (quit or keep running in the system tray), notifications (failures only, all conversions, or off), start watching when the app opens, tone preset, log options and an About block crediting HDRFix.
* **Settings folder option.** Move the settings file to a folder of your choice, or return to the default. The existing settings are copied across and saved there straight away.
* **Log folder option.** Choose where the log is written, or use the default. If the chosen folder can't be written, the app falls back to `%APPDATA%\AutoHDRConvert` instead of losing the log.
* **Start menu shortcut** switch in Settings (adds or removes the shortcut; available in the built exe).
* **Open log folder** and **Open settings folder** buttons.
* **Recent-activity list** showing each conversion result and any failure reason.
* **Status and problem reporting**: the window shows whether the watch folder, the output folder or HDRFix is the problem, and keeps watching once the problem is fixed.
* **Notifications** for failures (default) or every conversion, as a tray notification.
* **Start watching automatically** when the app opens (on by default; can be turned off).
* **Startup crash reporting.** If the app can't start, the error is written to the log and shown in a message box instead of silently disappearing.
* **Unhandled-error handling.** Errors on any thread are logged, with a single notification, so a windowed build no longer fails invisibly.
* **Build tooling**: `build.cmd` / `build.ps1` build the exe in one step.
* **Automated test suite** (330 tests) covering conversion, file watching, settings, logging, paths, the shortcut and the UI.
* **Changelog** and a v1 vs v2 comparison in the README.

### Changed
* **Conversion safety.** The original is now recycled only after the JPG has been checked to be a complete file, and only for files on fixed local drives. Otherwise the original is kept and the activity list says why. v1 only checked that the output existed.
* **Reliable file handling.** The app waits until a new screenshot has finished being written before converting it, handles `.JXR`, `.Jxr` and other capitalisations (HDRFix itself only accepts lowercase), and never overwrites an existing JPG (a number is added: `-sdr-1.jpg`). Partial or empty outputs are removed on failure.
* **Settings are more robust.** Each setting is validated on its own, so one bad value no longer resets the rest. Settings are saved atomically (a crash can't leave a half-written file) and edits are debounced.
* **Default file locations follow Windows conventions:**
  * Settings: `%APPDATA%\AutoHDRConvert\config.json` (unchanged location; a `config.json` beside the exe from older versions is still read if there is no new one).
  * Log: `%LOCALAPPDATA%\AutoHDRConvert\logs\AutoHDRConvert.log` (v1 used `%APPDATA%\AutoHDRConvert`). It still rotates at about 1 MB.
* **Single-instance handling.** Opening the app a second time brings the open window forward instead of showing an error.
* **Code structure.** The 430-line single script is split into a modular package (`autohdr/`): conversion core, file-watching service, settings, logging, paths, controller and UI, with the UI kept separate from the logic so the logic is testable.
* **Tray behaviour.** Single-click the tray icon to reopen the window. The tray is optional and the settings say clearly when it isn't available.
* README rewritten for v2, including where files live and how to build.

### Removed
* The separate installer. Everything it did (choosing a location, creating a Start menu shortcut) is either "put the exe where you like" or a switch in Settings.
* The Tk interface and its "Delete Original Files / Minimize to Tray / Output to Log" checkbox row (replaced by the new window and Settings dialog; the same options remain).

### Known limitations
* Highlights brighter than SDR white can't be shown in an SDR file, so very bright glints (for example sun reflecting off a fuselage) come out dimmer than they look on an HDR display.
* The bundled HDRFix never writes a pure 255 white. The presets balance cloud detail against bright-surface brightness within that limit.
* The exe is unsigned, so Windows SmartScreen may warn on first run (click **More info**, then **Run anyway**).
* Only one scene was used to tune the presets. Feedback from other games and scenes is welcome.

### Planned
* A native converter (V3) that doesn't need HDRFix, with proper control over highlight roll-off.
* MSFS auto start: launch the converter when Microsoft Flight Simulator starts.
* Start with Windows / start minimised.

## [1.x] - original script

* Single-file Tk application with a system tray icon (pystray).
* Watches a folder for new `.jxr` screenshots and converts them with HDRFix's default settings.
* Options: input and output folders, delete original (Recycle Bin), minimise to tray, output to log.
* Distributed as an exe with an installer.
