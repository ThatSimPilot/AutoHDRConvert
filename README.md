# Auto HDR-SDR Converter

I built Auto HDR-SDR Converter because HDR screenshots weren't being recognised by Volanta, and converting them by hand every time was a chore. It was made for Flight Simulator screenshots taken with NVIDIA, but it works with any `.jxr` image from any game.

Point it at a folder, and every new HDR screenshot (`.jxr`) is converted to an SDR `.jpg` for you. The conversion is done by HDRFix, which is bundled inside the app, so there is nothing extra to install. HDRFix was developed by Brooke Vibber: https://github.com/bvibber/hdrfix

<a href="https://www.buymeacoffee.com/thatsimpilot" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

## Install and use

1. Download the latest release from https://github.com/ThatSimPilot/Auto-HDR-SDR-Converter/releases.
2. Run `AutoHDRConvert.exe`. No other files are needed.
3. Pick the **Watch folder** (where your screenshots are saved) and the **Save JPGs to** folder (it defaults to the same folder as the originals). Under **After converting**, choose whether to keep the original or move it to the Recycle Bin.
4. Press **Start**. New screenshots are converted as they arrive.

In **Settings** you can also choose:
* **Tone preset**: Natural, Punchy or Bright (see below).
* **When you close the window**: quit the app, or keep it running in the system tray.
* **Notifications**: Failures only, All conversions, or Off.
* **Start watching when the app opens**.
* **Write a log file**, handy for troubleshooting.

### Tone presets
* **Natural**: the HDRFix defaults.
* **Punchy**: ACES tone curve, with more contrast.
* **Bright**: half a stop brighter.

### How it behaves
* Output files are named `<name>-sdr.jpg`. If that name is already taken, a number is added (`-sdr-1.jpg`) instead of overwriting.
* Only **new** `.jxr` files are converted. Files already in the folder when watching starts are left alone.
* Only the top level of the watch folder is watched, not its subfolders. `.jxr` is matched whatever the case, and files renamed or moved into the folder are picked up.
* The original is moved to the Recycle Bin only after the JPG has been verified, and only for files on fixed local drives. Otherwise the original is kept, and the activity list says so.
* Failures show in the recent activity list and, by default, as a notification.
* Only one copy of the app runs at a time. Opening it again brings the open window forward.
* If you chose to keep running in the tray, closing the window hides the app there. Single-click the tray icon to open the window again.

### Using a newer HDRFix (optional)
The app looks for `hdrfix.exe` next to `AutoHDRConvert.exe` first, then the copy bundled inside the app, then your PATH. To use a newer HDRFix, drop its `hdrfix.exe` next to `AutoHDRConvert.exe`.

## Where settings and logs live

* Settings: `%APPDATA%\AutoHDRConvert\config.json`. If that file doesn't exist yet, a `config.json` beside the exe (from older versions) is read instead.
* Log (only when **Write a log file** is on): `%APPDATA%\AutoHDRConvert\AutoHDRConvert.log`. It rotates at about 1 MB and keeps one older copy.

Paste `%APPDATA%\AutoHDRConvert` into the Windows Explorer address bar to jump there.

## Troubleshooting

* **Nothing is converting**: check the Watch folder is the one your screenshot tool saves to, and that the status says Watching. Only new files are converted, so files already in the folder won't be picked up.
* **Some images fail**: turn on **Write a log file** in Settings, try again, and check the failed row in the activity list. The log has the details.
* **Windows SmartScreen warns about the app**: the exe is unsigned. Click **More info**, then **Run anyway**.

## Building from source

You will need Python on Windows. The build needs a `hdrfix.exe` in the repository folder.
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

Further development will be limited by time and workload, but I will try to implement the following when I get a chance:
* More HDRFix settings beyond the tone presets
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
