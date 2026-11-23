"""Graph appearance chosen in Settings > Graphs: line colours, line thickness,
label size and grid strength. Shared by Analyze and Live View; pages listen
to `notifier.changed` so edits apply straight away."""
from PySide6.QtCore import QEvent, QObject, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsView

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
    if theme.is_mac():
        _round(plot)  # a card like every other: 10 px corners, hairline border
    else:
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


INSET = 8      # Mac: the plot's padding inside its card, so nothing reaches the rounded corners
RADIUS = 10.0  # Mac: the cards' corner radius (theme.py #card)
_CARDS = ("card", "macTile", "macPanel")


class _CardPainter(QObject):
    """Mac: paints a graph as one of the app's cards (rounded fill + hairline border) under the
    plot, and pads the plot INSET px inside it. A graph already inside a card (Live View's panels)
    stays plain: no second box. Decided once, on first show, when its parents are known."""

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.Show and isinstance(obj, QGraphicsView) and obj.property("card") is None:
            self._setup(obj)
        elif t == QEvent.Type.Paint and isinstance(obj.parent(), QGraphicsView):
            self._paint(obj)
        return False

    @staticmethod
    def _setup(view):
        w, plain = view.parentWidget(), False
        while w is not None and not plain:
            plain = w.objectName() in _CARDS
            w = w.parentWidget()
        view.setProperty("card", not plain)
        if not plain and view.property("mac_inset") != "manual":  # "manual": the owner pads it; "h": sideways only (the timeline's band fills its height; 1 px clears the border)
            v = 1 if view.property("mac_inset") == "h" else INSET
            view.getPlotItem().setContentsMargins(INSET, v, INSET, v)
        view.viewport().update()

    @staticmethod
    def _paint(viewport):
        from . import theme
        c = theme.colors()
        p = QPainter(viewport)
        if viewport.parent().property("card"):
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(c.get("card_border", c["border"])), 1))
            p.setBrush(QColor(c["surface"]))
            p.drawRoundedRect(QRectF(viewport.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        else:
            p.fillRect(viewport.rect(), QColor(c["surface"]))
        p.end()


_card_painter = None


def _round(plot):
    """Mac: *plot* drawn as a card (see _CardPainter). Safe to call again on a theme change."""
    global _card_painter
    if _card_painter is None:
        _card_painter = _CardPainter()
    plot.setBackground(None)  # _CardPainter paints it
    if not plot.property("card_filter"):
        plot.setProperty("card_filter", True)
        plot.setFrameShape(plot.Shape.NoFrame)
        plot.setStyleSheet("background: transparent; border: none;")
        plot.viewport().setAutoFillBackground(False)
        plot.installEventFilter(_card_painter)
        plot.viewport().installEventFilter(_card_painter)
        if plot.isVisible():
            _card_painter._setup(plot)
    plot.viewport().update()


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



def dot_cell(color, pt):
    """A table cell with a channel's small round dot (the sidebar's size), centred on the text
    beside it: the start of every readout row (Analyze, Split, Live View)."""
    return (f"<td valign='middle' style='color:{color}; font-size:{pt * 0.6:.1f}pt; "
            f"padding-right:5px'>&#9679;</td>")


def readout_html(rows, pt):
    """The readout box's text: one row per line, [(colour, name, value)] → dot · name · bold value."""
    cells = "".join(f"<tr>{dot_cell(color, pt)}<td valign='middle'>{name}</td>"
                    f"<td valign='middle' align='right' style='padding-left:14px'><b>{value}</b></td></tr>"
                    for color, name, value in rows)
    return f"<table cellspacing='0' cellpadding='1' style='font-size:{pt}pt'>{cells}</table>"


def readout_box(parent):
    """The readout box as a plain widget over a graph (Live View): the same rounded box and rows
    as readout_html, painted from a cached QTextDocument. A rich-text QLabel re-measured its
    table on every update (adjustSize ~20 ms each); laying the document out once is ~2 ms."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument
    from PySide6.QtWidgets import QWidget
    import math

    class _Box(QWidget):
        PAD_X, PAD_Y, RADIUS = 7, 5, 9

        def __init__(self, parent):
            super().__init__(parent)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._doc = QTextDocument(self)
            self._doc.setDocumentMargin(0)
            self._bg, self._fg, self._border = QColor(0, 0, 0, 225), QColor("white"), QColor("#555555")
            self._font = None
            self._pixmap = None  # the drawn box; the graph under it repaints ~30x a second

        def set_colors(self, bg, fg, border):
            self._bg, self._fg, self._border = QColor(bg), QColor(fg), QColor(border)
            self._pixmap = None
            self.update()

        def setText(self, html):
            font = self.font()
            if font != self._font:  # costly, and the font rarely changes
                self._font = font
                self._doc.setDefaultFont(font)
            self._doc.setHtml(html)
            size = self._doc.size()
            self.resize(math.ceil(size.width()) + 2 * (self.PAD_X + 1),
                        math.ceil(size.height()) + 2 * (self.PAD_Y + 1))
            self._pixmap = None
            self.update()

        def _render(self):
            from PySide6.QtGui import QPixmap
            ratio = self.devicePixelRatioF()
            pm = QPixmap(self.size() * ratio)
            pm.setDevicePixelRatio(ratio)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(self._border, 1))
            p.setBrush(self._bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
            p.translate(self.PAD_X + 1, self.PAD_Y + 1)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(QPalette.ColorRole.Text, self._fg)
            self._doc.documentLayout().draw(p, ctx)
            p.end()
            return pm

        def paintEvent(self, _event):
            if self._pixmap is None or self._pixmap.deviceIndependentSize().toSize() != self.size():
                self._pixmap = self._render()
            p = QPainter(self)
            p.drawPixmap(0, 0, self._pixmap)
            p.end()

    return _Box(parent)


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


# Mac: tick numbers drawn just inside the plot area, so the axis lines sit near the card's edges.
INSIDE_STRIP = 40  # px just inside the value axis (its numbers): scrolling there zooms the values


def inside_labels(plot_item):
    """Mac: draw the left and bottom axes' numbers inside the plot area (left-aligned just right
    of the value axis, and just above the time axis), and drop the axis titles. Classic: no-op."""
    from . import theme
    if not theme.is_mac():
        return
    from pyqtgraph import AxisItem
    from PySide6.QtCore import Qt
    left, bottom = plot_item.getAxis("left"), plot_item.getAxis("bottom")
    for axis in (left, bottom):
        if getattr(axis, "_inside", False):
            continue
        axis._inside = True
        axis.showLabel(False)
        axis.showLabel = lambda show=True, a=axis: AxisItem.showLabel(a, False)  # titles stay off
        axis.setStyle(tickTextOffset=-60)  # computed over the view, then placed below

        def bounding(a=axis):  # always reach over the view (with the grid off too), or the
            rect = a.mapRectFromParent(a.geometry())  # numbers would be clipped away
            view = a.linkedView()
            return rect | view.mapRectToItem(a, view.boundingRect()) if view is not None else rect
        axis.boundingRect = bounding

        default = axis.generateDrawSpecs

        def specs(p, a=axis, default=default):
            result = default(p)
            if result is None:
                return result
            axis_spec, tick_specs, text_specs = result
            p1, p2 = axis_spec[1], axis_spec[2]
            placed = []
            for rect, _flags, text in text_specs:
                if a.orientation == "left":
                    rect.moveLeft(p1.x() + 4)
                    if rect.bottom() > max(p1.y(), p2.y()) - 2:  # keep the corner for the time
                        continue
                    if rect.top() < min(p1.y(), p2.y()) + 20:  # the top-left: readout / row names
                        continue
                    flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                else:
                    rect.moveBottom(p1.y() - 2)
                    lo, hi = min(p1.x(), p2.x()), max(p1.x(), p2.x())
                    if rect.left() < lo + 28 or rect.right() > hi - 2:  # clear of the value numbers
                        continue
                    flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
                placed.append((rect, flags | Qt.TextFlag.TextDontClip, text))
            return axis_spec, tick_specs, placed
        axis.generateDrawSpecs = specs
    # No room outside the plot: the card's padding is then the same on all four sides.
    left.setWidth(0)
    bottom.setHeight(0)
    if not getattr(left, "_strip", False):
        left._strip = True
        default_wheel = left.wheelEvent

        def wheel(ev, a=left):  # over the numbers = over the value axis (zoom the values only)
            view = a.linkedView()
            if view is not None:
                r = view.sceneBoundingRect()
                pos = ev.scenePos()
                if r.contains(pos) and pos.x() - r.left() < INSIDE_STRIP:
                    view.wheelEvent(ev, axis=1)
                    ev.accept()
                    return
            default_wheel(ev)
        left.wheelEvent = wheel
