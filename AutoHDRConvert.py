"""Auto HDR-SDR Converter entry point (kept at this path so the PyInstaller spec is unchanged)."""
from autohdr.app import main

if __name__ == "__main__":
    raise SystemExit(main())
