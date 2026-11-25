"""Shared data types for the core, UI and app shell. Pure data: no Qt, no I/O."""
import math
from dataclasses import dataclass


@dataclass
class Settings:
    input_dir: str = ""
    output_dir: str = ""              # "" = same folder as input_dir
    delete_original: bool = False     # True = move the original to the Recycle Bin after a verified conversion
    minimize_to_tray: bool = False    # close button hides to the tray instead of quitting
    output_to_log: bool = False
    notifications: str = "failures"   # "failures" | "all" | "off"
    auto_start: bool = True           # start watching when the app opens
    tone_preset: str = "natural"      # key into TONE_PRESETS
    log_dir: str = ""                 # "" = <install folder>\logs


def _faithful(sdr_white_nits):
    """hdrfix options that reproduce the HDR image as it looks on a display whose SDR white is `sdr_white_nits`.

    Captures are scRGB (1.0 = 80 nits), so SDR white sits at sdr_white_nits / 80 and exposure drops by that many stops.
    hdrfix can't show more than SDR white, so the highlights (sunlit cloud, glints on paint) have to be compressed, and
    its tone curves have no "linear up to a knee" mode. These settings are the best balance found by searching
    tone-curve, white point and gamma combinations against sample captures:
      * "--tone-map reinhard --hdr-max 650": a high white point so highlights stay distinct instead of all landing on
        the output ceiling (a low one flattened clouds into one grey slab; cloud detail correlation 0.25, now 0.88).
      * "--pre-gamma 1.2 --post-gamma 0.95" bend the curve back toward linear below SDR white, which keeps the
        brightness of mid-bright surfaces such as a sunlit fuselage (0.77 of the HDR, vs about 0.69 without them).
      * "--color-map darken" fixes out-of-range colours by darkening instead of clipping, which would bleach every
        coloured highlight to neutral white, and "--saturation 3" stops the tone curve desaturating them.
      * The +0.45 stop trim puts the mid-tones back to the HDR's brightness.
    Result: mid-tone brightness within 1%, HSV saturation within 1% and hue error under 1 degree everywhere.
    Known limits: highlights above SDR white are necessarily dimmer than the HDR, and this hdrfix build never writes a
    channel above 246/255, whatever the settings. The exposure uses the --name=value form: hdrfix reads
    "--exposure -1.2" as a flag.
    """
    exposure = 0.45 - math.log2(sdr_white_nits / 80)
    return ["--tone-map", "reinhard", "--hdr-max", "650", f"--exposure={exposure:.3f}",
            "--pre-gamma", "1.2", "--post-gamma", "0.95", "--saturation", "3", "--color-map", "darken"]


# key -> (label shown in the UI, extra hdrfix arguments)
TONE_PRESETS = {
    "natural": ("Match original", _faithful(203)),    # 203 nits is the standard reference SDR white
    "bright": ("Brighter", _faithful(150)),           # like a dimmer SDR white: about 0.4 stop brighter
}


@dataclass(frozen=True)
class ConversionResult:
    ok: bool
    src: str
    dst: str                          # "" when no output was produced
    message: str                      # "" normally; a short human-readable reason on failure, or a note when ok=True but the original was kept
    original_removed: bool
    finished_at: float                # time.time()


@dataclass(frozen=True)
class Problems:
    input: str = ""                   # "" means fine
    output: str = ""
    tool: str = ""                    # hdrfix missing

    @property
    def any(self) -> bool:
        return bool(self.input or self.output or self.tool)


class ServiceEvent:
    """Base class for events the ConversionService reports (always from non-UI threads)."""


@dataclass(frozen=True)
class StateChanged(ServiceEvent):
    running: bool
    error: str = ""


@dataclass(frozen=True)
class ConversionStarted(ServiceEvent):
    src: str


@dataclass(frozen=True)
class ConversionFinished(ServiceEvent):
    result: ConversionResult
