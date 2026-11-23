"""Vector-painted glyphs, app/tray icons and the small painted controls (no image assets except icon.png, if present).

Glyphs are drawn on a 24x24 grid so they stay crisp at any size/DPI and follow the theme colours.
"""
import math
import os

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QIcon, QIconEngine, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QRadioButton, QSizePolicy, QWidget

from . import theme

# ---- glyphs ---------------------------------------------------------------------------------------------------

def _poly(p, pts, closed=False):
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    if closed:
        path.closeSubpath()
    p.drawPath(path)


def _filled(p, color, fn):
    p.save()
    p.setBrush(color)
    fn()
    p.restore()


def _check(p, c): _poly(p, [(5, 12.5), (10, 17.5), (19, 7)])
def _cross(p, c): _poly(p, [(6.5, 6.5), (17.5, 17.5)]); _poly(p, [(17.5, 6.5), (6.5, 17.5)])


def _pause(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    _filled(p, c, lambda: (p.drawRoundedRect(QRectF(6.5, 5, 4, 14), 1.4, 1.4), p.drawRoundedRect(QRectF(13.5, 5, 4, 14), 1.4, 1.4)))


def _play(p, c): _filled(p, c, lambda: _poly(p, [(8, 5.5), (19, 12), (8, 18.5)], True))


def _alert(p, c):
    _poly(p, [(12, 5.5), (12, 13.5)])
    p.setPen(Qt.PenStyle.NoPen)
    _filled(p, c, lambda: p.drawEllipse(QPointF(12, 18), 1.5, 1.5))


def _folder(p, c): _poly(p, [(3, 6.5), (9.5, 6.5), (11.5, 9), (21, 9), (21, 19), (3, 19)], True)


def _open(p, c):
    _poly(p, [(10, 5), (5, 5), (5, 19), (19, 19), (19, 14)])
    _poly(p, [(13, 4.5), (19.5, 4.5), (19.5, 11)])
    _poly(p, [(19.5, 4.5), (11, 13)])


def _gear(p, c):
    pts = []
    for i in range(8):
        a = i * math.tau / 8
        for da, r in ((-0.30, 7.4), (-0.17, 10.2), (0.17, 10.2), (0.30, 7.4)):
            pts.append(QPointF(12 + r * math.cos(a + da), 12 + r * math.sin(a + da)))
    p.drawPolygon(QPolygonF(pts))
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)


def _retry(p, c):
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 280 * 16)
    _filled(p, c, lambda: _poly(p, [(17.8, 2.8), (18.2, 8.6), (12.6, 7.4)], True))   # arrow head at the arc's open end


def _eye(p, c):
    path = QPainterPath(QPointF(2, 12))
    path.quadTo(12, 2, 22, 12)
    path.quadTo(12, 22, 2, 12)
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)


def _inbox(p, c):
    _poly(p, [(3, 13), (7, 13), (8.5, 16), (15.5, 16), (17, 13), (21, 13)])
    _poly(p, [(5.5, 13), (8, 5), (16, 5), (18.5, 13), (18.5, 19), (5.5, 19), (5.5, 13)])


_GLYPHS = {"check": _check, "cross": _cross, "pause": _pause, "play": _play, "alert": _alert, "folder": _folder,
           "open": _open, "gear": _gear, "retry": _retry, "eye": _eye, "inbox": _inbox}


def draw_glyph(p: QPainter, name: str, rect, color: QColor, width: float = 1.8) -> None:
    rect = QRectF(rect)
    side = min(rect.width(), rect.height())
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    p.scale(side / 24, side / 24)
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _GLYPHS[name](p, color)
    p.restore()


class _GlyphEngine(QIconEngine):
    """Paints the glyph on demand at whatever size/DPR is asked for, in the current theme's colour."""

    def __init__(self, name: str, role: str):
        super().__init__()
        self._name, self._role = name, role

    def paint(self, painter, rect, mode, state):
        c = theme.colors()
        col = getattr(c, self._role)
        if mode == QIcon.Mode.Disabled:
            col = c.text_disabled
        elif mode == QIcon.Mode.Active and self._role == "text2":
            col = c.text
        draw_glyph(painter, self._name, rect, col)

    def pixmap(self, size, mode, state):
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(QPoint(0, 0), size), mode, state)
        p.end()
        return pm

    def clone(self):
        return _GlyphEngine(self._name, self._role)


def icon(name: str, role: str = "text") -> QIcon:
    """A themed glyph icon; `role` is a theme.Colors attribute (text, text2, danger, accent_fg, ...)."""
    return QIcon(_GlyphEngine(name, role))


# ---- app / tray icons -----------------------------------------------------------------------------------------

def _icon_png() -> str:
    try:
        from autohdr.paths import resource_path
        path = resource_path("icon.png")
    except Exception:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "icon.png")
    return path if os.path.isfile(path) else ""


def _source_image(size: int) -> QImage:
    """The icon at size x size with transparent corners: icon.png cropped to its tile, or a painted stand-in."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    tile = QRectF(0, 0, size, size)
    clip = QPainterPath()
    clip.addRoundedRect(tile, size * 0.22, size * 0.22)
    src = QImage(_icon_png()) if _icon_png() else QImage()
    if not src.isNull():
        p.setClipPath(clip)
        w = src.width()
        p.drawImage(tile, src, QRectF(w * 0.165, w * 0.16, w * 0.675, w * 0.675))   # the tile inside the grey canvas
    else:
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#F7B733"))
        grad.setColorAt(0.5, QColor("#E5486B"))
        grad.setColorAt(1, QColor("#1E88E5"))
        p.fillPath(clip, QBrush(grad))
        draw_glyph(p, "eye", QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6), QColor("white"), 1.6)
    p.end()
    return img


def _grey(img: QImage) -> QImage:
    grey = img.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(grey)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, img)
    p.end()
    return grey


def _build_icon(paused: bool) -> QIcon:
    out = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        img = _source_image(size)
        if paused:
            img = _grey(img)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            d = size * 0.52
            badge = QRectF(size - d, size - d, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(badge.adjusted(-size * 0.04, -size * 0.04, size * 0.04, size * 0.04))
            p.setBrush(QColor("#E08A00"))
            p.drawEllipse(badge)
            p.setBrush(QColor("#FFFFFF"))
            bw, bh = d * 0.16, d * 0.46
            for dx in (-0.2, 0.2):
                p.drawRect(QRectF(badge.center().x() + d * dx - bw / 2, badge.center().y() - bh / 2, bw, bh))
            p.end()
        out.addPixmap(QPixmap.fromImage(img))
    return out


_cache: dict = {}


def app_icon() -> QIcon:
    if "app" not in _cache:
        _cache["app"] = _build_icon(False)
    return _cache["app"]


def tray_icon(running: bool) -> QIcon:
    """Normal icon while watching; desaturated with a pause badge while paused."""
    key = "tray_on" if running else "tray_off"
    if key not in _cache:
        _cache[key] = _build_icon(not running)
    return _cache[key]


# ---- painted controls -----------------------------------------------------------------------------------------

def make_button(text: str = "", *, primary=False, ghost=False, small=False, icon_name: str = "", icon_role="text",
                tip: str = "", name: str = "") -> QPushButton:
    """A themed button. Focus ring shows for keyboard focus only (TabFocus), like Windows 11 apps."""
    b = QPushButton(text)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setProperty("primary", primary)
    b.setProperty("ghost", ghost or not text)
    b.setProperty("small", small)
    b.setProperty("iconOnly", bool(icon_name) and not text)
    if icon_name:
        b.setIcon(icon(icon_name, icon_role))
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    if name or (icon_name and not text):
        b.setAccessibleName(name or tip)
    return b


class ElidedLabel(QLabel):
    """A one-line label that elides with "…" instead of forcing the layout wider; full text kept for tooltip/a11y."""

    def __init__(self, text: str = "", mode=Qt.TextElideMode.ElideMiddle, parent=None):
        super().__init__(parent)
        self._full, self._mode = "", mode
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFullText(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setAccessibleName(text)
        self._elide()
        self.updateGeometry()

    def fullText(self) -> str:
        return self._full

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, self.fontMetrics().height() + 2)

    def minimumSizeHint(self):
        return QSize(24, self.fontMetrics().height() + 2)

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, max(0, self.width() - 2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.FontChange, e.Type.StyleChange):
            self._elide()


class _Painted:
    """Shared bits of Switch/Choice: whole-row hit area, repaint on theme change, keyboard-only focus ring."""

    def _init_painted(self):
        self._ring = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.notifier.changed.connect(self.update)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def focusInEvent(self, e):
        self._ring = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                    Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._ring = False
        super().focusOutEvent(e)

    def _paint_frame(self, p, c, hovered):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._ring and self.hasFocus():
            p.setPen(QPen(c.focus, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)

    def _paint_text(self, p, c, rect):
        p.setFont(self.font())
        p.setPen(c.text if self.isEnabled() else c.text_disabled)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(rect.width()))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)


class Switch(_Painted, QCheckBox):
    """Windows 11 style toggle: label on the left, switch on the right, the whole row is the click target."""
    TRACK_W, TRACK_H = 40, 20

    def __init__(self, text: str = "", parent=None):
        QCheckBox.__init__(self, text, parent)
        self._init_painted()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + self.TRACK_W + 24, 36)

    def minimumSizeHint(self):
        return QSize(self.TRACK_W + 80, 36)

    def paintEvent(self, e):
        p = QPainter(self)
        c = theme.colors()
        self._paint_frame(p, c, False)
        on, en = self.isChecked(), self.isEnabled()
        r = QRectF(self.rect())
        track = QRectF(r.right() - self.TRACK_W - 8, (r.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        p.setOpacity(1.0 if en else 0.45)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c.accent)
            p.drawRoundedRect(track, 10, 10)
            thumb, col, d = QPointF(track.right() - 10, track.center().y()), c.accent_mark, 14
        else:
            p.setPen(QPen(c.text2, 1.2))
            p.setBrush(c.control_hover if self.underMouse() else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(0.6, 0.6, -0.6, -0.6), 9.4, 9.4)
            thumb, col, d = QPointF(track.left() + 10, track.center().y()), c.text2, 12
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(thumb, d / 2, d / 2)
        p.setOpacity(1.0)
        self._paint_text(p, c, QRectF(10, 0, track.left() - 20, r.height()))


class Choice(_Painted, QRadioButton):
    """Radio button painted to match: accent disc with a centre dot when selected."""

    def __init__(self, text: str = "", parent=None):
        QRadioButton.__init__(self, text, parent)
        self._init_painted()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 18 + 28, 32)

    def minimumSizeHint(self):
        return QSize(80, 32)

    def paintEvent(self, e):
        p = QPainter(self)
        c = theme.colors()
        self._paint_frame(p, c, False)
        r = QRectF(self.rect())
        disc = QRectF(8, (r.height() - 18) / 2, 18, 18)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c.accent)
            p.drawEllipse(disc)
            p.setBrush(c.accent_mark)
            p.drawEllipse(disc.center(), 4, 4)
        else:
            p.setPen(QPen(c.text2, 1.2))
            p.setBrush(c.control_hover if self.underMouse() else Qt.BrushStyle.NoBrush)
            p.drawEllipse(disc.adjusted(0.6, 0.6, -0.6, -0.6))
        p.setOpacity(1.0)
        self._paint_text(p, c, QRectF(disc.right() + 10, 0, r.width() - disc.right() - 14, r.height()))


class StatusBadge(QWidget):
    """Round badge with a state glyph. States: running, paused, error, idle, converting (spinner), ok, failed, empty."""
    _STATES = {"running": ("eye", "success"), "paused": ("pause", "warning"), "error": ("alert", "danger"),
               "idle": ("folder", "text2"), "converting": ("", "accent_fg"), "ok": ("check", "success"),
               "failed": ("cross", "danger"), "empty": ("inbox", "text2")}

    def __init__(self, diameter: int = 44, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._state, self._angle = "idle", 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._spin)
        theme.notifier.changed.connect(self.update)
        self.setAccessibleName("")

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        if self._state == "converting" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def _spin(self) -> None:
        self._angle = (self._angle - 12) % 360
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_timer()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.colors()
        glyph, role = self._STATES[self._state]
        col = getattr(c, role)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 46))
        p.drawEllipse(r)
        if self._state == "converting":
            w = max(2.0, r.width() * 0.075)
            arc = r.adjusted(r.width() * 0.26, r.width() * 0.26, -r.width() * 0.26, -r.width() * 0.26)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 70), w))
            p.drawEllipse(arc)
            p.setPen(QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc, self._angle * 16, 100 * 16)
        else:
            side = r.width() * 0.5
            draw_glyph(p, glyph, QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side), col, 2.0)
