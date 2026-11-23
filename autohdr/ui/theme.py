"""Palettes and the application style sheet. Follows Windows light/dark; one accent colour (the system one).

Painted widgets read `colors()` while painting and repaint on `notifier.changed`; everything else is QSS.
"""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

FALLBACK_ACCENT = "#0F6CBD"
FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"]

# Hex only; accent-derived colours are computed in _build(). Text pairs are >= 4.5:1 (checked in tests/test_ui.py).
_PALETTES = {
    "light": dict(
        window="#F3F3F3", surface="#FFFFFF", surface_alt="#F7F7F7", border="#E1E1E1",
        input_border="#D0D0D0", input_border_strong="#8A8A8A",
        control="#FDFDFD", control_border="#D1D1D1", control_hover="#F4F4F4", control_pressed="#EAEAEA",
        hover="#F3F3F3", text="#1B1B1B", text2="#5A5A5A", text_disabled="#9E9E9E",
        success="#0E700E", warning="#8A5100", danger="#C42B1C",
        success_bg="#DFF3DD", warning_bg="#FBEBC8", danger_bg="#FCE7E8", danger_border="#F1B8BC",
        scroll="#C4C4C4", scroll_hover="#9A9A9A",
    ),
    "dark": dict(
        window="#202020", surface="#2B2B2B", surface_alt="#262626", border="#3C3C3C",
        input_border="#464646", input_border_strong="#9A9A9A",
        control="#343434", control_border="#4A4A4A", control_hover="#3C3C3C", control_pressed="#2F2F2F",
        hover="#333333", text="#F5F5F5", text2="#C6C6C6", text_disabled="#7A7A7A",
        success="#6CCB5F", warning="#F5C542", danger="#FF99A4",
        success_bg="#2D4A2B", warning_bg="#4D3E17", danger_bg="#4A2A2E", danger_border="#7A3B42",
        scroll="#5A5A5A", scroll_hover="#7C7C7C",
    ),
}


class Colors:
    """The active palette as QColor attributes (see _PALETTES for the names) plus the accent family."""

    def __init__(self, scheme: str, accent: QColor):
        self.scheme = scheme
        for key, hexval in _PALETTES[scheme].items():
            setattr(self, key, QColor(hexval))
        dark = scheme == "dark"
        self.accent = accent
        # Text on an accent fill: white, else near-black, else black - the first that reaches 4.5:1.
        options = [QColor("#FFFFFF"), QColor("#1B1B1B"), QColor("#000000")]
        self.accent_text = next((t for t in options if contrast(accent, t) >= 4.5), max(options, key=lambda t: contrast(accent, t)))
        # Decorative marks on the accent (switch knob, radio dot) only need 3:1, so white survives on more accents.
        self.accent_mark = QColor("#FFFFFF") if contrast(accent, QColor("#FFFFFF")) >= 3 else self.accent_text
        self.accent_hover = mix(accent, self.accent_text, 0.10)
        self.accent_pressed = mix(accent, self.accent_text, 0.20)
        # Accent used as text / strokes on the surface: pushed toward the text colour until it reads (>= 4.5:1).
        self.accent_fg = ensure_contrast(accent, self.surface, 4.5, QColor("#FFFFFF") if dark else QColor("#000000"))
        self.accent_bg = QColor(accent.red(), accent.green(), accent.blue(), 36)   # translucent tint for the converting badge
        self.focus = self.text
        self.disabled_bg = self.surface_alt if not dark else QColor("#2A2A2A")


def luminance(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(*(round(x + (y - x) * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def ensure_contrast(fg: QColor, bg: QColor, ratio: float, toward: QColor) -> QColor:
    for i in range(21):
        c = mix(fg, toward, i / 20)
        if contrast(c, bg) >= ratio:
            return c
    return toward


def rgba(c: QColor, alpha: float = 1.0) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{round(alpha * 255)})"


class _Notifier(QObject):
    changed = Signal()      # emitted after a scheme change; painted widgets connect their update() to it


notifier = _Notifier()
_state = {"colors": None, "override": None, "accent": None, "accent_override": None, "connected": False}


def colors() -> Colors:
    if _state["colors"] is None:        # before apply(): usable defaults so widgets can be built in isolation
        _state["colors"] = Colors("light", QColor(FALLBACK_ACCENT))
    return _state["colors"]


def scheme() -> str:
    return colors().scheme


def repolish(widget) -> None:
    """Re-evaluate the style sheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _system_accent(app) -> QColor:
    c = app.palette().color(QPalette.ColorRole.Accent)
    return c if c.isValid() and c.alpha() and c.rgb() & 0xFFFFFF else QColor(FALLBACK_ACCENT)


def apply(app, scheme: str | None = None, accent: str | None = None) -> None:
    """Install palette + QSS. `scheme` ("light"/"dark") overrides Windows (preview/tests); None follows Windows."""
    if _state["accent"] is None:                       # read before we replace the app palette
        _state["accent"] = _system_accent(app)
    _state["override"] = scheme if scheme in ("light", "dark") else None
    if accent:
        _state["accent_override"] = QColor(accent)
    hints = app.styleHints()
    if not _state["connected"]:
        _state["connected"] = True
        app.setStyle("Fusion")                         # predictable QSS rendering; the palette below does the theming
        hints.colorSchemeChanged.connect(lambda *_: _install(app))
    if _state["override"] and hasattr(hints, "setColorScheme"):   # also darkens the native title bar
        hints.setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)
    elif hasattr(hints, "unsetColorScheme"):
        hints.unsetColorScheme()
    _install(app)


def _install(app) -> None:
    override = _state["override"]
    name = override or ("dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light")
    c = Colors(name, _state["accent_override"] or _state["accent"])
    _state["colors"] = c

    pal = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    for role, col in {
        R.Window: c.window, R.WindowText: c.text, R.Base: c.surface, R.AlternateBase: c.surface_alt, R.Text: c.text,
        R.Button: c.control, R.ButtonText: c.text, R.Highlight: c.accent, R.HighlightedText: c.accent_text,
        R.PlaceholderText: c.text2, R.ToolTipBase: c.surface, R.ToolTipText: c.text, R.Link: c.accent_fg,
        R.LinkVisited: c.accent_fg, R.Accent: c.accent, R.BrightText: c.text,
        R.Light: c.surface, R.Midlight: c.control_hover, R.Mid: c.border, R.Dark: c.input_border_strong, R.Shadow: QColor("#000000"),
    }.items():
        pal.setColor(role, col)
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(G.Disabled, role, c.text_disabled)
    app.setPalette(pal)

    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(14)
    app.setFont(font)
    app.setStyleSheet(_qss(c))
    notifier.changed.emit()


def _qss(c: Colors) -> str:
    h = lambda col: col.name()          # noqa: E731
    return f"""
QMainWindow, QDialog {{ background: {h(c.window)}; }}
QWidget {{ color: {h(c.text)}; }}
QToolTip {{ background: {h(c.surface)}; color: {h(c.text)}; border: 1px solid {h(c.border)}; padding: 4px 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="secondary"] {{ color: {h(c.text2)}; }}
QLabel[role="caption"] {{ color: {h(c.text2)}; font-size: 12px; }}
QLabel[role="title"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="error"] {{ color: {h(c.danger)}; font-size: 12px; }}

QFrame#card, QFrame#statusCard {{
    background: {h(c.surface)}; border: 1px solid {h(c.border)}; border-radius: 12px;
}}
QFrame#statusCard[state="error"] {{ background: {h(c.danger_bg)}; border-color: {h(c.danger_border)}; }}
QFrame#cardDivider {{ background: {h(c.border)}; border: none; min-height: 1px; max-height: 1px; }}
QFrame#activityRow {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#activityRow:hover {{ background: {h(c.hover)}; }}

QLineEdit {{
    background: {h(c.surface)}; color: {h(c.text)}; border: 1px solid {h(c.input_border)};
    border-bottom: 1px solid {h(c.input_border_strong)}; border-radius: 6px; padding: 5px 9px; min-height: 20px;
    selection-background-color: {h(c.accent)}; selection-color: {h(c.accent_text)}; placeholder-text-color: {h(c.text2)};
}}
QLineEdit:hover {{ background: {h(c.control_hover)}; }}
QLineEdit:focus {{ background: {h(c.surface)}; border: 2px solid {h(c.accent_fg)}; padding: 4px 8px; }}
QLineEdit[invalid="true"] {{ border: 1px solid {h(c.danger)}; border-bottom: 2px solid {h(c.danger)}; padding: 5px 9px 4px 9px; }}
QLineEdit[invalid="true"]:focus {{ border: 2px solid {h(c.danger)}; padding: 4px 8px; }}
QLineEdit:disabled {{ background: {h(c.disabled_bg)}; color: {h(c.text_disabled)}; }}

QPushButton {{
    background: {h(c.control)}; color: {h(c.text)}; border: 1px solid {h(c.control_border)};
    border-radius: 6px; padding: 5px 16px; min-height: 20px;
}}
QPushButton:hover {{ background: {h(c.control_hover)}; }}
QPushButton:pressed {{ background: {h(c.control_pressed)}; }}
QPushButton:focus {{ border: 2px solid {h(c.focus)}; padding: 4px 15px; }}
QPushButton:disabled {{ background: {h(c.disabled_bg)}; color: {h(c.text_disabled)}; border-color: {h(c.border)}; }}
QPushButton[primary="true"] {{ background: {h(c.accent)}; color: {h(c.accent_text)}; border: 1px solid {h(c.accent)}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {h(c.accent_hover)}; border-color: {h(c.accent_hover)}; }}
QPushButton[primary="true"]:pressed {{ background: {h(c.accent_pressed)}; border-color: {h(c.accent_pressed)}; }}
QPushButton[primary="true"]:focus {{ border: 2px solid {h(c.focus)}; }}
QPushButton[primary="true"]:disabled {{ background: {h(c.control_pressed)}; color: {h(c.text_disabled)}; border-color: {h(c.border)}; }}
QPushButton[ghost="true"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[ghost="true"]:hover {{ background: {h(c.control_hover)}; }}
QPushButton[ghost="true"]:pressed {{ background: {h(c.control_pressed)}; }}
QPushButton[ghost="true"]:focus {{ border: 2px solid {h(c.focus)}; }}
QPushButton[small="true"] {{ padding: 3px 12px; min-height: 18px; border-radius: 5px; }}
QPushButton[small="true"]:focus {{ padding: 2px 11px; }}
QPushButton[iconOnly="true"] {{ padding: 4px; min-height: 0; min-width: 0; border-radius: 6px; }}
QPushButton[iconOnly="true"]:focus {{ padding: 3px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: {h(c.scroll)}; border-radius: 3px; min-height: 28px; margin: 0 3px; }}
QScrollBar::handle:vertical:hover {{ background: {h(c.scroll_hover)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
