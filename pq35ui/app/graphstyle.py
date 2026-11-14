"""Graph appearance chosen in Settings > Graphs: line colours, line thickness,
label size and grid strength. Shared by Analyze and Live View; pages listen
to `notifier.changed` so edits apply straight away."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont

DEFAULT_COLORS = [
    "#e8820c", "#3b82f6", "#22c55e", "#ef4444", "#a855f7",
    "#06b6d4", "#f59e0b", "#ec4899", "#84cc16", "#f97316",
]
DEFAULT_WIDTH = 2.0
DEFAULT_LABEL_PT = 9
DEFAULT_GRID = 15  # % opacity

LABEL_SIZES = (("Small", 8), ("Normal", 9), ("Large", 11), ("Extra large", 13), ("Huge", 16))


class _Notifier(QObject):
    changed = Signal()


notifier = _Notifier()


def _settings(settings=None):
    if settings is not None:
        return settings
    from . import storage
    return storage._ACTIVE_SETTINGS


def colors(settings=None):
    s = _settings(settings)
    saved = (s.get("graph_colors") if s else None) or []
    out = list(DEFAULT_COLORS)
    for i, c in enumerate(saved[:len(out)]):
        if isinstance(c, str) and c.startswith("#"):
            out[i] = c
    return out


def color(i, settings=None):
    """Colour for slot i: the palette from Settings first, then endless extra colours, so
    no two channels ever share one however many are shown."""
    c = colors(settings)
    return c[i] if i < len(c) else _extras(tuple(c), i - len(c) + 1)[i - len(c)]


_EXTRA_CACHE = {}


def _extras(palette, n):
    """The first n colours after the palette. Each is picked from a wheel of hues at two
    lightness levels as the one farthest from every colour already used (palette included),
    so a new channel always gets the most different colour left."""
    from PySide6.QtGui import QColor
    out = _EXTRA_CACHE.setdefault(palette, [])
    if len(out) >= n:
        return out

    def rgb(h):
        c = QColor(h)
        return c.red(), c.green(), c.blue()

    def dist(a, b):  # "redmean": a cheap colour distance close to how different they look
        r = (a[0] + b[0]) / 2
        dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
        return ((2 + r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - r) / 256) * db * db) ** 0.5

    cands = [QColor.fromHslF(h / 60, 0.8, light).name()  # mid lightness: clear on light + dark
             for h in range(60) for light in (0.52, 0.66)]
    used = [rgb(h) for h in palette] + [rgb(h) for h in out]
    while len(out) < n:
        free = [h for h in cands if h not in out]
        if not free:  # wheel used up (over 120 channels): a fresh, unrepeated hue
            k = len(out)
            out.append(QColor.fromHslF((k * 0.381966) % 1.0, 0.7, 0.6).name())
            continue
        best = max(free, key=lambda h: min(dist(rgb(h), u) for u in used))
        out.append(best)
        used.append(rgb(best))
    return out


def lean(item, clip=True):
    """Make a long line cheap to draw: skip points off-screen (clip) and keep only each pixel's
    highest and lowest sample (looks the same; pyqtgraph's default keeps ~5 per pixel)."""
    item.opts["autoDownsampleFactor"] = 1.0  # before the calls below, which redo the points
    if clip:
        item.setClipToView(True)
    item.setDownsampling(auto=True, method="peak")
    return item


def cache(*items):
    """Keep a ready-drawn picture of items that rarely change (axes with their grid lines, the
    timeline's lines), so moving the cursor doesn't redraw them every time."""
    from PySide6.QtWidgets import QGraphicsItem
    for item in items:
        item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)


def cache_axes(plot_item):
    """Axes drawn from a cached picture, and without pyqtgraph's automatic unit prefixes (k, M…;
    ours never use them), which re-write each axis label on every zoom or range change."""
    axes = [plot_item.getAxis(name) for name in ("left", "bottom", "right", "top")]
    for axis in axes:
        axis.enableAutoSIPrefix(False)
    cache(*axes)


def width(settings=None):
    s = _settings(settings)
    try:
        return float(s.get("graph_line_width", DEFAULT_WIDTH)) if s else DEFAULT_WIDTH
    except (TypeError, ValueError):
        return DEFAULT_WIDTH


def label_pt(settings=None):
    s = _settings(settings)
    try:
        return int(s.get("graph_label_pt", DEFAULT_LABEL_PT)) if s else DEFAULT_LABEL_PT
    except (TypeError, ValueError):
        return DEFAULT_LABEL_PT


def grid_alpha(settings=None):
    s = _settings(settings)
    try:
        return int(s.get("graph_grid", DEFAULT_GRID)) / 100.0 if s else DEFAULT_GRID / 100.0
    except (TypeError, ValueError):
        return DEFAULT_GRID / 100.0


def font(settings=None, delta=0):
    f = QFont()
    f.setPointSize(max(6, label_pt(settings) + delta))
    return f


# Every graph that's been styled, so a theme change can recolour them all (they paint their own
# background, so the app stylesheet doesn't reach them).
_themed = []


def theme_plot(plot):
    """Give *plot* the theme's background / axis / legend colours now and on every theme change."""
    if not any(p is plot for p in _themed):
        _themed.append(plot)
        plot.destroyed.connect(lambda *_: _forget(plot))
    _paint(plot)


def _forget(plot):
    _themed[:] = [p for p in _themed if p is not plot]


def _paint(plot):
    from PySide6.QtGui import QColor
    from . import theme
    c = theme.colors()
    plot.setBackground(c["surface"])
    item = plot.getPlotItem()
    for name in ("left", "bottom", "right", "top"):
        axis = item.getAxis(name)
        if axis is not None:
            axis.setPen(c["muted"])       # axis line, ticks and (faded) grid
            axis.setTextPen(c["muted"])
    legend = item.legend
    if legend is not None:
        bg = QColor(c["surface"])
        bg.setAlpha(200)
        legend.setBrush(bg)
        legend.setLabelTextColor(c["text"])
        for _sample, label in legend.items:  # existing entries keep the colour they were made with
            label.setText(label.text, color=c["text"])


def _retheme_all():
    for plot in list(_themed):
        try:
            _paint(plot)
        except RuntimeError:  # deleted underneath us
            _forget(plot)


def apply(plot, settings=None, legend=None):
    """Style a pyqtgraph PlotWidget's axes, grid and legend text (and its theme colours)."""
    theme_plot(plot)
    f = font(settings)
    item = plot.getPlotItem()
    for name in ("left", "bottom", "right", "top"):
        axis = item.getAxis(name)
        if axis is None:
            continue
        axis.setStyle(tickFont=f)
        axis.label.setFont(font(settings, 1))
    a = grid_alpha(settings)
    plot.showGrid(x=a > 0, y=a > 0, alpha=a)
    legend = legend if legend is not None else item.legend
    if legend is not None:
        legend.setLabelTextSize(f"{label_pt(settings)}pt")


def changed():
    notifier.changed.emit()


def _hook_theme():
    from . import theme
    if _retheme_all not in theme._listeners:
        theme.on_apply(_retheme_all)


_hook_theme()



def rounded_text_item(**kw):
    """A pyqtgraph TextItem drawn as a rounded box like the app's dropdown menus (9 px corners,
    surface fill, 1 px border) instead of pyqtgraph's square one."""
    import pyqtgraph as pg

    class RoundedTextItem(pg.TextItem):
        RADIUS = 9

        def paint(self, p, *args):
            s = self.scene()
            if s is not self._lastScene:
                if self._lastScene is not None:
                    self._lastScene.sigPrepareForPaint.disconnect(self.updateTransform)
                self._lastScene = s
                if s is not None:
                    s.sigPrepareForPaint.connect(self.updateTransform)
                self.updateTransform()
                p.setTransform(self.sceneTransform())
            p.setPen(self.border)
            p.setBrush(self.fill)
            p.setRenderHint(p.RenderHint.Antialiasing, True)
            rect = self.textItem.mapRectToParent(self.textItem.boundingRect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

    item = RoundedTextItem(**kw)
    item.textItem.document().setDocumentMargin(7)  # room inside the box, like a menu's padding
    return item
