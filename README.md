# Auto HDR-SDR Converter

Auto HDR-SDR Converter is a simple, lightweight application I developed to solve my issue of taking screenshots when using HDR. The desire for this app came as a solution to HDR files not being recognised by Volanta, and the repetitive and troublesome process of manually converting these files into a recognisable format.

Although developed for Flight Simulator screenshots taken with NVIDIA, the app works with any .jxr images taken from any game.

The application watches a folder you choose for new HDR screenshots (.jxr) and converts them to SDR format (.jpg), optionally sending the original to the Recycle Bin after conversion.

Due to a lack of support for .jxr in the Python language, an external tool is used for the image conversion. HDRFix was developed by Brooke Vibber and more information can be found in its repository - https://github.com/bvibber/hdrfix. HDRFix is bundled inside the app, so there is nothing extra to install.

<a href="https://www.buymeacoffee.com/thatsimpilot" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

## Installation and Usage

1. Download the latest release from https://github.com/ThatSimPilot/Auto-HDR-SDR-Converter/releases (use the installer, or extract the .zip anywhere you like).
2. Run `AutoHDRConvert.exe`. It is self-contained, so no other files are needed.
3. Choose your input folder (where your screenshots are saved) and your output folder, plus any other settings:
   * **Delete Original Files** - once the JPG is confirmed to exist, the original .jxr is moved to the **Recycle Bin** (not permanently deleted). If anything goes wrong, the original is kept.
   * **Minimize to Tray** - closing the window hides the app in the system tray. Double-click the tray icon to reopen it.
   * **Output to Log** - writes a log file, handy for troubleshooting (see below).
4. Hit **Start**. Any new screenshots will automatically be converted.

### Good to know
* Output files are named `<name>-sdr.jpg`. If that name already exists, a number is added (`-sdr-1.jpg`) instead of overwriting.
* Only **new** .jxr files are converted. Files already in the folder when you press Start are left alone.
* Only the top level of the input folder is watched, not its subfolders. `.jxr` is matched regardless of upper/lower case, and files renamed or moved into the folder are picked up.
* Pressing **Start** while it is already running restarts the watcher and applies any settings you changed. Only one copy of the app can run at a time.
* If a conversion fails, you will get a notification from the system tray.

### Using a newer HDRFix (optional)
The app looks for `hdrfix.exe` in this order: next to `AutoHDRConvert.exe`, then the copy bundled inside the app, then your PATH. To use a newer HDRFix, just drop its `hdrfix.exe` next to `AutoHDRConvert.exe`.

## Where settings and logs live

* Settings: `%APPDATA%\AutoHDRConvert\config.json`. Older versions kept `config.json` next to the exe; it is read once as a fallback.
* Log (only when **Output to Log** is on): `%APPDATA%\AutoHDRConvert\AutoHDRConvert.log`. It rotates at about 1 MB, and no longer writes `z_log.txt` into your output folder.

Paste `%APPDATA%\AutoHDRConvert` into the Windows Explorer address bar to jump there.

## Troubleshooting

* **Nothing is converting** - check the input folder is the one your game or screenshot tool saves to, and that the app says it is running (press Start). Remember that only new files are converted, not ones already in the folder.
* **Some images fail** - turn on **Output to Log**, press Start again, and check the log file above for the error.
* **Windows SmartScreen warns about the app** - the exe is not code-signed. Click **More info**, then **Run anyway**.

## Building from source

You will need Python on Windows.
```
pip install -r requirements-dev.txt
pyinstaller AutoHDRConvert.spec
```
The built `AutoHDRConvert.exe` ends up in `dist`. The spec bundles `hdrfix.exe`, `icon.png` and `icon.ico` from the repository folder.

To run the tests:
```
python -m unittest discover -s tests
```

## Future Plans (Roadmap)

Further development will be limited due to time and workload constraints, but I will try to implement the following when I get a chance:
* Option to adjust HDRFix settings and parameters
* Modern UI refresh
* Start with Windows / start minimised

Any suggestions, requests or issues can be left by opening an issue or submitting a pull request, and I will try to take a look.

## Credits - HDRFix
### HDRFix
hdrfix is copyright 2021-2025 by Brooke Vibber

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### JPEGXR Library
HDRFix also uses BSD-licensed code from Microsoft and the license is found below or at https://github.com/bvibber/jpegxr

New BSD License (BSD)

Copyright (c) 2009, Microsoft All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## License

MIT License

Copyright (c) 2025 Hayden Hookham

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
