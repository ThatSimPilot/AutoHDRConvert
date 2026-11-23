"""Shared data types for the core, UI and app shell. Pure data: no Qt, no I/O."""
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
