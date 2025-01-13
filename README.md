# Auto HDR-SDR Converter

Auto HDR-SDR Converter is a simple, lightweight application I developed to solve my issue of taking screenshots when using HDR. The desire for this app came as a solution to HDR files not being recognised by Volanta, and the repetitive and troublesome process of manually converting these files into a recognisable format.

Although developed for Flight Simulator screenshots taken with NVIDIA, the app works with any .jxr images taken from any game.

The application automatically scans a selected directory for HDR screenshots (.jxr) and converts them to SDR format (.jpg), optionally deleting the orignal after conversion.

Due to a lack of support with .jxr in the Python language, an external app is used for the image conversion. HDRFix was developped by Brooke Vibber and more information surrounding this can be found in the repository - https://github.com/bvibber/hdrfix

## Installation and Usage

To install the HDR-SDR Converter, simply follow the below steps.
1. Download the latest release from https://github.com/ThatSimPilot/Auto-HDR-SDR-Converter/releases
2. Extract from the .zip and place the folder in a desired location (anywhere). AutoHDRConverter.exe and hdrfix.exe MUST be in the same folder.
3. Run AutoHDRConverter.exe and select your desired input and output directories, and any other desired settings
4. Hit Start and any new images/screenshots will automatically be converted.

## Future Plans (Roadmap)

Further development will be limited due to time and workload constraints, however I will try to implement the following when I get a chance:
* Option to adjust HDRFix settings and parameters

Additionally, any suggestions, requests or issues can be left by submitting a pull request and I will try and look at implementing.

## Credits - HDRFix

hdrfix (hdrfix.exe) is copyright 2021-2025 by Brooke Vibber
```sh
THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

HDRFix also uses BSD-licensed code from Microsoft and the license is found below or at https://github.com/bvibber/jpegxr
```sh
New BSD License (BSD)

Copyright (c) 2009, Microsoft All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
