"""Graph auto-scaling: put channels of very different sizes (rpm in the
thousands next to a duty cycle in single digits) on one graph so every line
uses the full height. Readouts, cursor tables and stats keep the real values;
only the lines are drawn scaled."""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

from . import graphstyle

# (key, label, tooltip)
MODES = (
    ("auto", "Auto", "Fit each channel to the graph height when their sizes are very different "
                     "(e.g. rpm and load), real values when they're alike (e.g. boost actual vs requested)."),
    ("each", "Fit each channel", "Every line is stretched to its own min-max, 0-100 % of the graph height."),
    ("real", "Real values", "All lines on one shared axis in their real units."),
)
SETTING = "graph_scale"
RATIO = 8  # Auto fits each channel once the biggest is this many times the smallest


def combo(settings, on_change):
    """A Scale dropdown that remembers its choice (shared by Live View and Analyze)."""
    box = QComboBox()
    for key, label, tip in MODES:
        box.addItem(label, key)
        box.setItemData(box.count() - 1, tip, 3)  # Qt.ToolTipRole
    box.setToolTip("How lines of different sizes share the graph.\n" +
                   "\n".join(f"{label}: {tip}" for _key, label, tip in MODES))
    saved = settings.get(SETTING) or "auto"
    box.setCurrentIndex(max(box.findData(saved), 0))

    def changed(_):
        settings.set(SETTING, box.currentData())
        on_change()
    box.currentIndexChanged.connect(changed)
    return box


def value_range(*arrays):
    """(lo, hi) over the finite values of the arrays, or None."""
    parts = [np.asarray(a, dtype=float) for a in arrays if a is not None and len(a)]
    if not parts:
        return None
    values = np.concatenate(parts)
    values = values[np.isfinite(values)]
    if not values.size:
        return None
    return float(values.min()), float(values.max())


def should_fit(mode, ranges):
    """Whether to draw each channel scaled to its own range."""
    ranges = [r for r in ranges if r is not None]
    if mode == "each":
        return len(ranges) >= 1
    if mode != "auto" or len(ranges) < 2:
        return False
    sizes = [max(abs(lo), abs(hi), hi - lo) for lo, hi in ranges]
    sizes = [s for s in sizes if s > 0] or [1.0]
    return max(sizes) / min(sizes) >= RATIO


SMALL_SWING = 0.10  # a channel moving less than 10 % of its size keeps zero on its scale


def fit_range(rng):
    """The range a line is stretched over. A channel that barely moves (boost
    holding 2120-2180 mbar) keeps zero in it, so its wobble isn't blown up to
    full height; channels that really swing (rpm 1600-6700) use their own min-max."""
    if rng is None:
        return None
    lo, hi = rng
    size = max(abs(lo), abs(hi))
    if size > 0 and (hi - lo) < SMALL_SWING * size:
        return (min(lo, 0.0), max(hi, 0.0))
    return rng


def scale(values, rng):
    """values mapped to 0-100 % of fit_range(rng); a flat channel sits at 50 %."""
    values = np.asarray(values, dtype=float)
    if rng is None:
        return values
    lo, hi = fit_range(rng)
    if hi - lo <= 0:
        return np.where(np.isfinite(values), 50.0, np.nan)
    return (values - lo) / (hi - lo) * 100.0


def fmt(v):
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}"


def label(name, rng):
    """Legend text when scaled: the channel and the real range the line spans."""
    return f"{name}  [{fmt(rng[0])} – {fmt(rng[1])}]" if rng else name


def set_axis(plot, fitted, real_label=""):
    """Left axis: 0-100 % of each channel's range when fitted, else real units."""
    plot.setLabel("left", "% of each channel's range" if fitted else real_label)
    if fitted:
        plot.enableAutoRange(axis="y", enable=False)
        plot.setYRange(-4, 106, padding=0)  # room above 100 % so the top of a line isn't clipped
    else:
        plot.enableAutoRange(axis="y", enable=True)


# --- per-channel layout shared by Analyze's Single graph and Split rows (Datazap-style axes) ---

def plan(mode, ranges, own=()):
    """How each channel's line is drawn on one plot.

    mode: graphscale mode ("real", "each", "auto"); ranges: {name: (lo, hi) or None};
    own: names given their own y axis (only used when lines aren't fitted, i.e. Shared).
    Returns (fitted, maps, extra): maps[name] turns real values into line values;
    extra[name] = (a, b, lo, hi) for each channel drawn on its own axis (real lo..hi spans
    the shared axis's a..b)."""
    fitted = should_fit(mode, ranges.values())
    if fitted:
        return True, {n: (lambda v, r=r: scale(v, r)) for n, r in ranges.items()}, {}
    own_names = [n for n in ranges if n in own and ranges[n] is not None]
    shared = [n for n in ranges if n not in own_names]
    if not shared and own_names:
        shared.append(own_names.pop(0))  # one channel has to own the left axis
    rng = value_range(*[np.array(ranges[n]) for n in shared if ranges[n] is not None])
    a, b = rng if rng else (0.0, 1.0)
    if b <= a:
        a, b = a - 1.0, b + 1.0
    maps = {n: (lambda v: np.asarray(v, dtype=float)) for n in shared}
    extra = {}
    for n in own_names:
        lo, hi = ranges[n]
        if hi <= lo:
            pad = max(abs(lo), 1.0) * 0.5
            lo, hi = lo - pad, hi + pad
        extra[n] = (a, b, lo, hi)
        maps[n] = lambda v, a=a, b=b, lo=lo, hi=hi: a + (np.asarray(v, dtype=float) - lo) / (hi - lo) * (b - a)
    return False, maps, extra


def _axis_class():
    import pyqtgraph as pg

    class RealAxis(pg.AxisItem):
        """A y axis for one channel drawn on the shared axis's scale: shows its real values."""

        def __init__(self, a, b, lo, hi, orientation="right"):
            super().__init__(orientation)
            self.enableAutoSIPrefix(False)
            self._k = (hi - lo) / (b - a)
            self._a, self._lo = a, lo

        def _real(self, y):
            return self._lo + (y - self._a) * self._k

        def _line(self, v):
            return self._a + (v - self._lo) / self._k

        def tickValues(self, minVal, maxVal, size):
            r0, r1 = sorted((self._real(minVal), self._real(maxVal)))
            return [(spacing / self._k, [self._line(v) for v in values])
                    for spacing, values in super().tickValues(r0, r1, size)]

        def tickStrings(self, values, scale, spacing):
            return super().tickStrings([self._real(v) for v in values], scale, spacing * self._k)

    return RealAxis


_REAL_AXIS = None


def real_axis(a, b, lo, hi, color, label="", orientation="right"):
    """An extra y axis in the channel's colour, to add beside a plot and link to its view."""
    global _REAL_AXIS
    if _REAL_AXIS is None:
        _REAL_AXIS = _axis_class()
    import pyqtgraph as pg
    axis = _REAL_AXIS(a, b, lo, hi, orientation)
    axis.setPen(pg.mkPen(color))
    axis.setTextPen(pg.mkPen(color))
    if label:
        axis.setLabel(label, color=color)
    return axis


VALUE_AXIS_TIP = ("Scroll here to stretch or flatten the lines (values only). "
                  "Double-click to put the values back.")


def axis_zoom(vb, reset_values):
    """Scrolling over a value axis zooms the values only (stretch / flatten the lines), even on
    views whose values the mouse can't otherwise move (Split rows). Everything else scrolls as
    pyqtgraph does. `reset_values()` runs on a double-click on a value axis (value_axis)."""
    default = vb.wheelEvent

    def wheel(ev, axis=None):
        if axis != 1 or vb.state["mouseEnabled"][1]:
            return default(ev, axis)
        s = 1.02 ** (ev.delta() * vb.state["wheelScaleFactor"])  # pyqtgraph's own step
        vb._resetTarget()
        vb.scaleBy([None, s], vb.mapSceneToView(ev.scenePos()))
        ev.accept()
        vb.sigRangeChangedManually.emit([False, True])

    vb.wheelEvent = wheel
    vb.reset_values = reset_values


def value_axis(axis):
    """Tooltip + double-click reset on a value (y) axis linked to a view set up by axis_zoom."""
    axis.setToolTip(VALUE_AXIS_TIP)
    default = axis.mouseClickEvent

    def click(ev):
        lv = axis.linkedView()
        if ev.button() == Qt.MouseButton.LeftButton and ev.double() and \
                getattr(lv, "reset_values", None):
            ev.accept()
            lv.reset_values()
            return
        default(ev)

    axis.mouseClickEvent = click


def add_axes(plot_item, axes, first_col=3):
    """Put extra axes to the right of a PlotItem's plot area, linked to its view."""
    for k, axis in enumerate(axes):
        plot_item.layout.addItem(axis, 2, first_col + k)
        axis.linkToView(plot_item.vb)
        value_axis(axis)
        graphstyle.cache(axis)  # only redraws on zoom, not per cursor move


def remove_axes(plot_item, axes):
    for axis in axes:
        plot_item.layout.removeItem(axis)
        axis.unlinkFromView()
        if axis.scene() is not None:
            axis.scene().removeItem(axis)


def line_at(vb, scene_pos, times, lines, px=6):
    """The name of the line drawn within px pixels of a click, or None.
    lines: {name: y values in line coordinates, same length as times}."""
    from PySide6.QtCore import QPointF
    if times is None or not len(times) or not vb.sceneBoundingRect().contains(scene_pos):
        return None
    view = vb.mapSceneToView(scene_pos)
    left = vb.mapSceneToView(QPointF(scene_pos.x() - px, scene_pos.y())).x()
    right = vb.mapSceneToView(QPointF(scene_pos.x() + px, scene_pos.y())).x()
    i0 = max(int(np.searchsorted(times, left)) - 1, 0)
    i1 = min(int(np.searchsorted(times, right)) + 1, len(times))
    # how many line units px pixels is, up and down
    dy = abs(vb.mapSceneToView(QPointF(scene_pos.x(), scene_pos.y() + px)).y() - view.y())
    best, best_d = None, None
    for name, ys in lines.items():
        seg = np.asarray(ys[i0:i1], dtype=float)
        seg = seg[np.isfinite(seg)]
        if not seg.size:
            continue
        lo, hi = float(seg.min()), float(seg.max())
        d = 0.0 if lo <= view.y() <= hi else min(abs(view.y() - lo), abs(view.y() - hi))
        if d <= dy and (best_d is None or d < best_d):
            best, best_d = name, d
    return best
