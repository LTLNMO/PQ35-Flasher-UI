"""Haltech-style dial and bar gauges for Live View, drawn with QPainter.

Scale comes from the channel's unit (rpm 0–8000, % 0–100, …) and widens to fit
what has been seen. Alarm limits from Live View draw as red zones, with an
amber warning band just inside them.

Needles and bars glide to each new reading (see _Animator); the number shown is always the
latest real reading."""
import math
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy

from . import theme

def _danger():
    return QColor(theme.colors()["err"])  # follows light/dark like every other red


def _warn():
    return QColor(theme.colors()["warn"])

# Typical full-scale ranges by unit (lower-case).
UNIT_RANGES = {
    "rpm": (0, 8000), "/min": (0, 8000), "1/min": (0, 8000),
    "%": (0, 100),
    "°c": (-20, 140), "c": (-20, 140), "°f": (0, 280),
    "mbar": (0, 3000), "hpa": (0, 3000), "kpa": (0, 300), "bar": (0, 3), "psi": (-15, 40),
    "km/h": (0, 260), "mph": (0, 160),
    "v": (0, 16),
    "°kw": (-10, 50), "°": (-10, 50),
    "nm": (0, 500), "lb-ft": (0, 370),
    "mg/h": (0, 1500), "mg/stk": (0, 1500), "g/s": (0, 300), "kg/h": (0, 1000),
    "ms": (0, 30), "lambda": (0.6, 1.4),
}


def _nice_up(value):
    """Smallest 'nice' number (1, 2, 2.5, 5 × 10^n) >= value, for value > 0."""
    if value <= 0:
        return 0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude * 1.0000001:
            return step * magnitude
    return value


class ScaleTracker:
    """Gauge range: unit default, widened to the observed min/max."""

    def __init__(self, unit, name=""):
        key = (unit or "").strip().lower()
        if not key and "lambda" in name.lower():
            key = "lambda"
        self.default = UNIT_RANGES.get(key)
        self.seen_lo = self.seen_hi = None

    def update(self, value):
        if value is None:
            return
        self.seen_lo = value if self.seen_lo is None else min(self.seen_lo, value)
        self.seen_hi = value if self.seen_hi is None else max(self.seen_hi, value)

    def range(self, limit=None):
        lo, hi = self.default if self.default else (None, None)
        for v in (self.seen_lo, self.seen_hi,
                  (limit or {}).get("min"), (limit or {}).get("max")):
            if v is None:
                continue
            lo = v if lo is None else min(lo, v)
            hi = v if hi is None else max(hi, v)
        if lo is None:
            return 0.0, 1.0
        if not self.default:
            lo = 0.0 if lo >= 0 else -_nice_up(-lo)
            hi = _nice_up(hi) if hi > 0 else 0.0
        elif hi > self.default[1]:
            hi = _nice_up(hi)
        if hi <= lo:
            hi = lo + 1
        return float(lo), float(hi)


def zones(limit, lo, hi):
    """[(start, end, color)] for the scale: red past limits, amber 10% inside."""
    out = []
    if not limit:
        return out
    span = hi - lo
    if limit.get("max") is not None:
        top = limit["max"]
        out.append((max(lo, top - 0.1 * span), top, _warn()))
        out.append((top, hi, _danger()))
    if limit.get("min") is not None:
        bottom = limit["min"]
        out.append((lo, bottom, _danger()))
        out.append((bottom, min(hi, bottom + 0.1 * span), _warn()))
    return [(max(lo, a), min(hi, b), c) for a, b, c in out if b > a]


class _Animator(QObject):
    """One ~60 fps clock for every gauge whose needle is still on its way to the latest reading.
    Runs only while something is moving."""

    TAU = 0.07  # seconds; the needle covers ~76% of the way in 0.1 s, ~95% in 0.2 s

    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._step)
        self.moving = set()
        self.last = 0.0

    def add(self, gauge):
        self.moving.add(gauge)
        if not self.timer.isActive():
            self.last = time.perf_counter()
            self.timer.start()

    def discard(self, gauge):
        self.moving.discard(gauge)

    def _step(self):
        now = time.perf_counter()
        k = 1 - math.exp(-(now - self.last) / self.TAU)
        self.last = now
        for gauge in list(self.moving):
            try:
                done = gauge._animate(k)
            except RuntimeError:  # widget deleted (layout changed)
                done = True
            if done:
                self.moving.discard(gauge)
        if not self.moving:
            self.timer.stop()


_animator = None


def _anim():
    global _animator
    if _animator is None:
        _animator = _Animator()
    return _animator


from .units import fmt as _fmt  # one number style on every tab


class _GaugeBase(QFrame):
    MIN_W, MIN_H = 150, 120

    def __init__(self, name, unit="", key=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.name = name
        self.unit = unit
        self.key = key
        self.value = None   # latest reading: the number shown, alarms
        self.shown = None   # where the needle / bar is drawn; glides toward value
        self.limit = None
        self.alarm = None
        self.scale = ScaleTracker(unit, name)
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # Same interface as ValueCard
    def set_value(self, text):
        if text == "–":
            self.set_reading(None, self.limit)

    def set_alarm(self, state):
        if state != self.alarm:
            self.alarm = state
            self.update()

    def set_reading(self, value, limit=None):
        if value == self.value and limit == self.limit:
            return  # Live View calls this every frame; nothing new
        self.value = value
        self.limit = limit
        self.scale.update(value)
        if value is None or self.shown is None or not self.isVisible():
            self.shown = value  # first reading, cleared, or off screen: no glide
            _anim().discard(self)
        else:
            _anim().add(self)
        self.update()

    def _animate(self, k):
        """One animation step (k = fraction of the remaining way). True when arrived."""
        if self.value is None or self.shown is None or not self.isVisible():
            self.shown = self.value
            self.update()
            return True
        lo, hi = self.scale.range(self.limit)
        diff = self.value - self.shown
        done = abs(diff) <= (hi - lo) * 0.001
        self.shown = self.value if done else self.shown + diff * k
        self.update()
        return done

    def _colors(self):
        c = theme.colors()
        return QColor(c["text"]), QColor(c["muted"]), QColor(c["accent"]), QColor(c.get("surface2", c["surface"]))

    def _paint_frame(self, p):
        if self.alarm:
            fill = QColor(_danger())
            fill.setAlpha(80 if self.alarm == "on" else 0)
            p.setPen(QPen(_danger(), 2))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)


class DialGauge(_GaugeBase):
    """Round dial: 240° sweep, ticks, zones, needle, digital readout."""
    MIN_W, MIN_H = 160, 160
    SWEEP = 240.0
    START = 210.0  # degrees, Qt convention (0 = 3 o'clock, counter-clockwise)

    def _geometry(self, lo, hi):
        w, h = self.width(), self.height()
        size = min(w, h - 22) - 16
        cx, cy = w / 2, 14 + size / 2 + 4
        r = size / 2
        arc = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        band = max(6.0, r * 0.10)

        def angle(v):
            frac = 0 if hi == lo else (min(max(v, lo), hi) - lo) / (hi - lo)
            return self.START - frac * self.SWEEP

        return w, h, cx, cy, r, arc, band, angle

    def _static_layer(self, lo, hi, text, muted, track):
        """Everything that doesn't move with the reading (track, zones, ticks, labels, unit,
        name), drawn once into a pixmap and reused until size, scale, limits or theme change."""
        zone_list = zones(self.limit, lo, hi)
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, lo, hi, tuple((a, b, c.name()) for a, b, c in zone_list),
               text.name(), muted.name(), track.name(), self.font().key(), self.unit, self.name)
        if getattr(self, "_static_key", None) == key:
            return self._static_pix
        w, h, cx, cy, r, arc, band, angle = self._geometry(lo, hi)
        pix = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Track
        p.setPen(QPen(track, band, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(arc.adjusted(band / 2, band / 2, -band / 2, -band / 2),
                  int(self.START * 16), int(-self.SWEEP * 16))
        # Zones
        for a, b, color in zone_list:
            p.setPen(QPen(color, band, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(arc.adjusted(band / 2, band / 2, -band / 2, -band / 2),
                      int(angle(a) * 16), int((angle(b) - angle(a)) * 16))
        # Ticks + labels
        font = QFont(self.font())
        font.setPixelSize(max(9, int(r * 0.12)))
        p.setFont(font)
        for i in range(11):
            v = lo + (hi - lo) * i / 10
            rad = math.radians(angle(v))
            major = i % 2 == 0
            r1, r2 = r - band - 2, r - band - (9 if major else 5)
            p.setPen(QPen(muted, 2 if major else 1))
            p.drawLine(QPointF(cx + r1 * math.cos(rad), cy - r1 * math.sin(rad)),
                       QPointF(cx + r2 * math.cos(rad), cy - r2 * math.sin(rad)))
            if major and 0 < i < 10 and r > 55:
                rl = r - band - 20
                label = f"{v:.3g}" if abs(v) < 1000 else f"{v / 1000:.3g}k"
                p.drawText(QRectF(cx + rl * math.cos(rad) - 20, cy - rl * math.sin(rad) - 8, 40, 16),
                           Qt.AlignmentFlag.AlignCenter, label)
        # Unit
        small = QFont(self.font())
        small.setPixelSize(max(9, int(r * 0.12)))
        p.setFont(small)
        p.setPen(muted)
        p.drawText(QRectF(cx - r * 0.6, cy + r * 0.64, r * 1.2, r * 0.2), Qt.AlignmentFlag.AlignCenter, self.unit)
        # Name
        p.setPen(text)
        p.drawText(QRectF(4, h - 20, w - 8, 18), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, w - 8))
        p.end()
        self._static_key, self._static_pix = key, pix
        return pix

    def paintEvent(self, event):
        if theme.is_mac():
            return self._paint_arc(event)
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_frame(p)
        text, muted, accent, track = self._colors()
        lo, hi = self.scale.range(self.limit)
        p.drawPixmap(0, 0, self._static_layer(lo, hi, text, muted, track))
        w, h, cx, cy, r, arc, band, angle = self._geometry(lo, hi)
        # Value fill (follows the gliding needle)
        if self.shown is not None:
            in_alarm = bool(self.alarm)
            p.setPen(QPen(_danger() if in_alarm else accent, band * 0.45, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.FlatCap))
            p.drawArc(arc.adjusted(band / 2, band / 2, -band / 2, -band / 2),
                      int(self.START * 16), int((angle(self.shown) - self.START) * 16))
        # Needle
        if self.shown is not None:
            rad = math.radians(angle(self.shown))
            p.setPen(QPen(_danger() if self.alarm else text, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx, cy), QPointF(cx + (r - band - 4) * math.cos(rad),
                                                cy - (r - band - 4) * math.sin(rad)))
        p.setBrush(text)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4, 4)
        # Readout: the real latest reading, not the gliding one
        big = QFont(self.font())
        big.setPixelSize(max(12, int(r * 0.26)))
        big.setBold(True)
        p.setFont(big)
        p.setPen(_danger() if self.alarm else text)
        p.drawText(QRectF(cx - r * 0.6, cy + r * 0.30, r * 1.2, r * 0.34), Qt.AlignmentFlag.AlignCenter, _fmt(self.value))
        p.end()


    # ── Mac interface: the mockup's arc gauge ──
    ARC_START, ARC_SWEEP = 225.0, 270.0  # open at the bottom

    def _paint_arc(self, event):
        """A thick round-capped arc in the channel's colour over a grey track, the value big in
        the middle with its unit under it, the name below (Mockups/Swift native, Live View)."""
        QFrame.paintEvent(self, event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_frame(p)
        c = theme.colors()
        text, muted = QColor(c["text"]), QColor(c["muted"])
        color = _danger() if self.alarm else QColor(getattr(self, "color", None) or c["accent"])
        w, h = self.width(), self.height()
        name_h = 26
        size = max(40.0, min(w - 28, h - name_h - 22))
        band = max(7.0, size * 0.085)
        cx, cy = w / 2, 12 + size / 2
        rect = QRectF(cx - size / 2 + band / 2, cy - size / 2 + band / 2, size - band, size - band)
        p.setPen(QPen(QColor("#3a3a3c") if theme.mode() != "light" else QColor("#d8d8dc"), band,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, int(self.ARC_START * 16), int(-self.ARC_SWEEP * 16))
        lo, hi = self.scale.range(self.limit)
        if self.shown is not None and hi > lo:
            frac = min(max((self.shown - lo) / (hi - lo), 0.0), 1.0)
            if frac > 0.002:
                p.setPen(QPen(color, band, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(rect, int(self.ARC_START * 16), int(-self.ARC_SWEEP * frac * 16))
        big = QFont(self.font())
        big.setPixelSize(max(14, int(size * 0.22)))
        big.setWeight(QFont.Weight.Bold)
        p.setFont(big)
        p.setPen(_danger() if self.alarm else text)
        p.drawText(QRectF(cx - size / 2, cy - size * 0.2, size, size * 0.26), Qt.AlignmentFlag.AlignCenter,
                   _fmt(self.value))
        small = QFont(self.font())
        small.setPixelSize(max(10, int(size * 0.09)))
        p.setFont(small)
        p.setPen(muted)
        p.drawText(QRectF(cx - size / 2, cy + size * 0.07, size, size * 0.14), Qt.AlignmentFlag.AlignCenter,
                   self.unit)
        label = QFont(self.font())
        label.setWeight(QFont.Weight.DemiBold)
        p.setFont(label)
        p.setPen(text)
        p.drawText(QRectF(6, h - name_h - 4, w - 12, name_h), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, w - 12))
        p.end()


class BarGauge(_GaugeBase):
    """Horizontal bar with zones, min/max scale labels and a digital readout."""
    MIN_W, MIN_H = 200, 74

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.MIN_H)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_frame(p)
        text, muted, accent, track = self._colors()
        lo, hi = self.scale.range(self.limit)
        w = self.width()
        pad = 12
        name_font = QFont(self.font())
        p.setFont(name_font)
        p.setPen(text)
        readout = _fmt(self.value) + (f" {self.unit}" if self.unit else "")
        big = QFont(self.font())
        big.setBold(True)
        big.setPixelSize(int(name_font.pixelSize() * 1.3) if name_font.pixelSize() > 0 else 16)
        p.setFont(big)
        rw = p.fontMetrics().horizontalAdvance(readout) + 4
        p.setPen(_danger() if self.alarm else text)
        p.drawText(QRectF(w - pad - rw, 6, rw, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, readout)
        p.setFont(name_font)
        p.setPen(text)
        p.drawText(QRectF(pad, 6, w - 3 * pad - rw, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, int(w - 3 * pad - rw)))
        bar = QRectF(pad, 32, w - 2 * pad, 14)

        def x(v):
            frac = 0 if hi == lo else (min(max(v, lo), hi) - lo) / (hi - lo)
            return bar.left() + frac * bar.width()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(bar, 4, 4)
        for a, b, color in zones(self.limit, lo, hi):
            c = QColor(color)
            c.setAlpha(110)
            p.setBrush(c)
            p.drawRect(QRectF(x(a), bar.top(), x(b) - x(a), bar.height()))
        if self.shown is not None:
            p.setBrush(_danger() if self.alarm else accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top() + 3, max(2.0, x(self.shown) - bar.left()),
                                     bar.height() - 6), 3, 3)
        small = QFont(self.font())
        small.setPixelSize(10)
        p.setFont(small)
        p.setPen(muted)
        p.drawText(QRectF(pad, 48, 80, 16), Qt.AlignmentFlag.AlignLeft, _fmt(lo))
        p.drawText(QRectF(w - pad - 80, 48, 80, 16), Qt.AlignmentFlag.AlignRight, _fmt(hi))
        p.end()
