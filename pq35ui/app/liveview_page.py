"""Live View: real-time value cards + scrolling graphs, vwlog-style.

Configurable panel layout: pick a grid arrangement via icons, then
set each section to Gauges or Graph via its header dropdown."""
import bisect
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QSize, QRectF
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMenu, QPushButton, QScrollArea, QSplitter,
    QApplication, QGraphicsItem, QVBoxLayout, QWidget, QWidgetAction,
)

from . import alarms, channel_info, graphscale, graphstyle, mathchannels, theme, units
from .dashboards import DashboardButton
from .gauges import BarGauge, DialGauge
from .presets import PresetButton
from .channel_list import (CATEGORY_ROLE, FIXED_ROLE, RAW_CATEGORY, RAW_GROUP_ROLE, ROLE, VALUE_ROLE,
                           ChannelDropdown, ChannelTree, ask_raw_group, groups_summary)
from . import analyze_tools as T
from .widgets import Banner, discard, muted, scrollable

WINDOW_SECONDS = (("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300))
MAX_POINTS = 6000

# Grid arrangements: (name, groups, columns). Normally each group is a row
# with N slots; with columns=True each group is a column with N slots stacked.
# The slot contents (gauges/graph) are chosen independently per slot.
# New arrangements go at the END: saved dashboards store the index.
GRIDS = (
    ("1",     [[1]], False),                # single panel
    ("1 | 1", [[1], [1]], False),           # two stacked
    ("1 · 1", [[1, 1]], False),             # two side by side
    ("2 + 1", [[1], [1, 1]], False),        # one on top, two below
    ("1 + 2", [[1, 1], [1]], False),        # two on top, one below
    ("2 × 2", [[1, 1], [1, 1]], False),     # four panels
    ("2 | 1", [[1, 1], [1]], True),         # two stacked on the left, one tall on the right
    ("1 | 2", [[1], [1, 1]], True),         # one tall on the left, two stacked on the right
)
# Order the layouts appear in the picker (indexes into GRIDS): 2 × 2 last.
GRID_ORDER = (0, 1, 2, 3, 4, 6, 7, 5)
# Mac: macOS's own tiling icons (Window ▸ Move & Resize), one SF Symbol per layout in GRIDS
SF_LAYOUTS = (
    "inset.filled.rectangle",
    "inset.filled.tophalf.bottomhalf.rectangle",
    "inset.filled.lefthalf.righthalf.rectangle",
    "inset.filled.tophalf.bottomleft.bottomright.rectangle",
    "inset.filled.topleft.topright.bottomhalf.rectangle",
    "inset.filled.topleft.topright.bottomleft.bottomright.rectangle",
    "inset.filled.topleft.bottomleft.righthalf.rectangle",
    "inset.filled.lefthalf.topright.bottomright.rectangle",
)
DEFAULT_GRID = 3   # "2 + 1" — gauges top, two graphs below
DEFAULT_TYPES = ["gauges", "graph", "graph"]


def _paint_grid(p, rect, groups, columns, color, gap=2):
    """Draw a panel-arrangement icon into rect. Classic: rounded cells. Mac: macOS's window-tiling
    look (Window ▸ Move & Resize): one rounded outline, the panels as filled blocks inside it."""
    from . import theme
    if theme.is_mac():
        rect, gap = _paint_mac_frame(p, rect, color)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
    radius = 1.0 if theme.is_mac() else 2.0
    x0, y0, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    n_groups = len(groups)
    if columns:  # groups are columns: split the width, stack within each
        group_w = (w - gap * (n_groups - 1)) / n_groups
        x = x0
        for col in groups:
            cell_h = (h - gap * (len(col) - 1)) / len(col)
            y = y0
            for _ in col:
                p.drawRoundedRect(QRectF(x, y, group_w, cell_h), radius, radius)
                y += cell_h + gap
            x += group_w + gap
    else:        # groups are rows: split the height, side by side within each
        row_h = (h - gap * (n_groups - 1)) / n_groups
        y = y0
        for row in groups:
            col_w = (w - gap * (len(row) - 1)) / len(row)
            x = x0
            for _ in row:
                p.drawRoundedRect(QRectF(x, y, col_w, row_h), radius, radius)
                x += col_w + gap
            y += row_h + gap


def _paint_mac_frame(p, rect, color):
    """The outline of a macOS tiling icon; returns the area for the panels and the gap between
    them (both scale with the icon, so the toolbar glyph and the menu's big ones match)."""
    from PySide6.QtGui import QPen
    # macOS's tiles are 1.28 : 1 (measured, 50 x 39 px): fit that shape, centred, never stretched
    w = min(rect.width(), rect.height() * 1.28)
    h = w / 1.28
    rect = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
    line = max(1.2, rect.height() * 0.09)
    p.setPen(QPen(color, line))
    p.setBrush(Qt.BrushStyle.NoBrush)
    outer = rect.adjusted(line / 2, line / 2, -line / 2, -line / 2)
    p.drawRoundedRect(outer, rect.height() * 0.22, rect.height() * 0.22)
    pad = line + max(1.0, rect.height() * 0.06)  # outline + a clear gap before the panels
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    return rect.adjusted(pad, pad, -pad, -pad), max(1.0, rect.height() * 0.06)


class LayoutButton(QPushButton):
    """A small button that paints a grid icon representing a panel arrangement."""

    W, H = 46, 36  # same height as the regular buttons (Start, Clear…) in the row
    ICON_W, ICON_H = 28, 20

    def __init__(self, grid_rows, columns=False, parent=None, flat=False):
        super().__init__(parent)
        self.grid_rows = grid_rows  # e.g. [[1], [1, 1]]
        self.columns = columns      # groups are columns instead of rows
        # The global QPushButton style sets min-height + padding, which forces the
        # button to 34px tall (overriding the fixed height) and pushes it down so
        # its bottom gets clipped. Cancel those for this button only.
        # flat: a choice inside the layout menu, just the icon (soft highlight on hover).
        extra = ("QPushButton { background: transparent; border: none; border-radius: 6px; }"
                 "QPushButton:hover { background: rgba(128, 128, 128, 0.18); }"
                 "QPushButton:checked { background: transparent; }") if flat else ""
        self.setStyleSheet("QPushButton { min-height: 0px; padding: 0px; }"
                           "QPushButton::menu-indicator { image: none; width: 0px; }" + extra)
        self.setFixedSize(self.W, self.H)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        from . import theme
        if flat and theme.is_mac():  # the menu's choices: the size macOS's Window menu shows them
            self.ICON_W = self.ICON_H = 25

    # The stylesheet's button padding inflates the default hint; report the real
    # size so the control row centres these icons with everything else.
    def sizeHint(self):
        return QSize(self.W, self.H)

    def minimumSizeHint(self):
        return QSize(self.W, self.H)

    def _icon_color(self):
        from . import theme
        c = theme.colors()
        return QColor(c["accent"] if self.isChecked() else c["muted"])

    def _icon_rect(self):
        return QRectF((self.width() - self.ICON_W) / 2, (self.height() - self.ICON_H) / 2,
                      self.ICON_W, self.ICON_H)

    def _symbol(self, box):
        """Mac: this layout's SF Symbol as a QIcon in a *box* px square (None: draw it ourselves)."""
        from . import theme
        if not theme.is_mac():
            return None
        index = next((i for i, (_n, rows, cols) in enumerate(GRIDS)
                      if rows == self.grid_rows and cols == self.columns), None)
        if index is None:
            return None
        from . import sf_symbols
        return sf_symbols.icon(SF_LAYOUTS[index], box, self._icon_color().name())

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._icon_rect()
        box = round(rect.height() / 0.8)  # sf_symbols draws at 0.8 x the box: as tall as ICON_H
        icon = self._symbol(box)
        if icon is not None:
            c = rect.center()
            p.drawPixmap(round(c.x() - box / 2), round(c.y() - box / 2), icon.pixmap(QSize(box, box)))
        else:
            _paint_grid(p, rect, self.grid_rows, self.columns, self._icon_color())
        p.end()


class LayoutPicker(LayoutButton):
    """One button showing the current panel layout; click it for a menu of all
    layouts. Saves the row space a full strip of layout buttons takes up. Sized like
    Analyze's dropdown icon buttons (Presets): 50 x 34, a 22 px icon, then a small ▾."""

    W, H = 50, 34
    ICON_W, ICON_H = 21, 15  # the grid, about as big as the 22 px icons beside it

    def __init__(self, on_pick, parent=None):
        super().__init__(GRIDS[0][1], GRIDS[0][2], parent)
        from . import theme
        if theme.is_mac():  # a plain toolbar glyph + chevron, like the icons beside it
            from .analyze_tools import tool_metrics
            self.W, self.H = tool_metrics(dropdown=True)[:2]
            g = tool_metrics()[3]  # the toolbar's SF Symbol box: same size as the icons beside it
            self.ICON_W = self.ICON_H = g * 0.8
            self.setFixedSize(self.W, self.H)
            self.setObjectName("iconTool")
        self.setCheckable(False)
        self._on_pick = on_pick
        self.current = 0
        self.menu_ = QMenu(self)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(4)
        self.options = {}
        for pos, idx in enumerate(GRID_ORDER):
            name, rows, columns = GRIDS[idx]
            btn = LayoutButton(rows, columns, flat=True)
            btn.setToolTip(name)
            btn.clicked.connect(lambda _checked, i=idx: self._picked(i))
            grid.addWidget(btn, pos // 4, pos % 4)
            self.options[idx] = btn
        action = QWidgetAction(self.menu_)
        action.setDefaultWidget(holder)
        self.menu_.addAction(action)
        self.setMenu(self.menu_)
        from .analyze_tools import anchor_menu
        anchor_menu(self.menu_, self)
        self.set_current(0)

    def _picked(self, index):
        self.menu_.close()
        self._on_pick(index)

    def set_current(self, index):
        self.current = index
        name, self.grid_rows, self.columns = GRIDS[index]
        self.setToolTip(f"Panel layout: {name}")
        for idx, btn in self.options.items():
            btn.setChecked(idx == index)
        self.update()

    def _icon_color(self):
        from . import theme
        c = theme.colors()
        return QColor(c["tab_text"] if theme.is_mac() else c["accent"])

    def _icon_rect(self):
        # same spot as the glyph in Analyze's dropdown buttons: the icon+caret strip centred
        from .analyze_tools import tool_metrics
        _w, _h, strip, g = tool_metrics(dropdown=True)
        left = (self.width() - strip) / 2
        return QRectF(left + (g - self.ICON_W) / 2, (self.height() - self.ICON_H) / 2,
                      self.ICON_W, self.ICON_H)

    def paintEvent(self, event):
        super().paintEvent(event)
        from . import analyze_tools, theme
        p = QPainter(self)
        _w, _h, strip, g = analyze_tools.tool_metrics(dropdown=True)
        # the ▾ exactly as Analyze draws it: its dropdown icon with a blank glyph
        full = analyze_tools.toolbar_icon("\u0020", theme.colors()["tab_text"], dropdown=True)
        p.drawPixmap(int((self.width() - strip) / 2), int((self.height() - g) / 2), full.pixmap(QSize(strip, g)))
        p.end()


class ValueCard(QFrame):
    """A single live-value card: big number + small unit label."""

    @staticmethod
    def _alarm_style(state):
        if state is None:
            return ""
        err = theme.colors()["err"]
        fill = f" background: {theme.colors()['err_bg']};" if state == "on" else ""
        return f"QFrame#card {{ border: 2px solid {err};{fill} }}"

    def __init__(self, name, unit="", key=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.name = name
        self.key = key
        self._alarm = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.name_label = QLabel(name)
        self.name_label.setObjectName("cardName")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setAlignment(Qt.AlignmentFlag.AlignBaseline)
        self.value_label = QLabel("–")
        self.value_label.setStyleSheet("font-size: 22pt; font-weight: 700;")
        row.addWidget(self.value_label)
        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("muted")
        self.unit_label.setStyleSheet("font-size: 11pt;")
        row.addWidget(self.unit_label)
        row.addStretch(1)
        layout.addLayout(row)

    def set_value(self, text):
        self.value_label.setText(text)

    def set_alarm(self, state):
        """None, "on" or "dim" (alternated by the page to flash)."""
        if state != self._alarm:
            self._alarm = state
            self.setStyleSheet(self._alarm_style(state))


class PanelChannels:
    """Which of the read channels a panel shows: chosen in Select channels (its panel segment).
    shown = None: all of them (the default)."""
    shown = None
    on_shown_changed = None  # set by the page: remembers the choice

    def _all_keys(self):
        return [ch[0] for ch in self.all_channels]

    def _shown_keys(self):
        keys = self._all_keys()
        return keys if self.shown is None else [k for k in keys if k in self.shown]

    def set_shown(self, keys, notify=True):
        """None (or every read channel): all of them; otherwise just these keys."""
        if keys is not None and set(keys) >= set(self._all_keys()) and self._all_keys():
            keys = None
        self.shown = None if keys is None else set(keys)
        self._shown_changed()
        if notify and self.on_shown_changed is not None:
            self.on_shown_changed()


class GaugesPanel(PanelChannels, QFrame):
    """Responsive grid of ValueCards. Cards always fill width (no horizontal
    scroll). Scrolls vertically only when rows overflow."""

    PREFERRED_CARD_W = 140  # target width used to calculate column count
    MAX_COLS = 3            # cap columns for balanced cards
    CARD_CLASS = ValueCard

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value_cards = {}
        self._prev_cols = 0
        self.all_channels = []  # every channel being read: [(key, name, unit)]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        # Mac: cards flush with the page's left / right edges and top, like the toolbar and graph
        # panels (a top margin put the tiles lower than the graph card beside them).
        self.grid.setContentsMargins(*((0, 0, 0, 4) if theme.is_mac() else (4, 4, 4, 4)))
        self.grid.setSpacing(6)
        self.scroll.setWidget(self.inner)
        layout.addWidget(self.scroll)

    def set_channels(self, channels, channel_data):
        self.all_channels = list(channels)
        self._build_cards()

    def _shown_changed(self):
        self._build_cards()

    def _build_cards(self):
        self._clear_grid()
        shown = set(self._shown_keys())
        for i, (key, name, unit) in enumerate(self.all_channels):
            if key not in shown:
                continue
            self.value_cards[key] = card = self.CARD_CLASS(name, unit, key)
            card.color = graphstyle.color(i)  # the Mac arc gauge draws its arc in the channel's colour
        self._prev_cols = 0
        # Defer reflow so viewport has its final size
        QTimer.singleShot(0, self._reflow)

    def _clear_grid(self):
        while self.grid.count():
            self.grid.takeAt(0)
        # every old card, also ones not placed yet (the reflow is deferred); hidden at once
        for w in self.value_cards.values():
            discard(w)
        self.value_cards = {}
        self._reset_stretches()

    def _reset_stretches(self):
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)

    def _reflow(self):
        n = len(self.value_cards)
        if n == 0:
            return
        avail = self.scroll.viewport().width() - 8
        # never narrower than a tile's minimum width, gaps included (else the last one is clipped)
        tile = max(self.PREFERRED_CARD_W, getattr(self.CARD_CLASS, "MIN_W", 0), 1) + self.grid.spacing()
        cols = max(1, min(n, self.MAX_COLS, (avail + self.grid.spacing()) // tile))
        if cols == self._prev_cols:
            return  # nothing changed
        self._prev_cols = cols
        # Detach all cards without destroying
        while self.grid.count():
            self.grid.takeAt(0)
        self._reset_stretches()
        # Re-add in new arrangement
        last_row = 0
        for i, key in enumerate(self.value_cards):
            r, c = divmod(i, cols)
            self.grid.addWidget(self.value_cards[key], r, c)
            last_row = r
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        # Pin cards to top — scroll vertically if needed
        self.grid.setRowStretch(last_row + 1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def update_values(self, channel_data, alarm_states=None, limits=None):
        alarm_states = alarm_states or {}
        limits = limits or {}
        for key, card in self.value_cards.items():
            ch = channel_data.get(key)
            if ch:
                if hasattr(card, "set_reading"):
                    card.set_reading(ch["last"], limits.get(key))
                else:
                    card.set_value(ch["last_text"])
            card.set_alarm(alarm_states.get(key))


class DialsPanel(GaugesPanel):
    """Round dial gauges (Mac interface: the mockup's arc gauges, up to six in a row)."""
    CARD_CLASS = DialGauge

    @property
    def PREFERRED_CARD_W(self):
        return 150 if theme.is_mac() else 190

    @property
    def MAX_COLS(self):
        return 6 if theme.is_mac() else 4

    def _reflow(self):
        super()._reflow()
        if theme.is_mac():  # tiles keep their size; spare height goes to the graphs
            tile = 200
            for card in self.value_cards.values():
                card.setFixedHeight(tile)
            cols = max(self._prev_cols, 1)
            rows = max(1, -(-len(self.value_cards) // cols))
            m = self.grid.contentsMargins()
            cap = (rows * tile + (rows - 1) * self.grid.spacing() + m.top() + m.bottom() + 4
                   if self.value_cards else 16777215)
            section = self.parentWidget()
            while section is not None and not isinstance(section, SectionWrapper):
                section = section.parentWidget()
            stacked = section is not None and isinstance(section.parentWidget(), QSplitter) \
                and section.parentWidget().orientation() == Qt.Orientation.Vertical
            if not stacked:
                # Beside other panels: a capped height would be centred next to the full-height
                # panel beside it (sitting lower). Keep the full height; the tiles stay at the top.
                self.setMaximumHeight(16777215)
                if section is not None:
                    section.setMaximumHeight(16777215)
                return
            self.setMaximumHeight(cap)
            if section is not None:  # the splitter gives the rest of the height to the graphs
                m = section.layout().contentsMargins() if section.layout() else None
                extra = (m.top() + m.bottom()) if m is not None else 8
                section.setMaximumHeight(cap + extra if self.value_cards else 16777215)
                split = section.parentWidget()
                if isinstance(split, QSplitter) and split.orientation() == Qt.Orientation.Vertical \
                        and self.value_cards:
                    sizes = split.sizes()
                    i = split.indexOf(section)
                    total = sum(sizes)
                    if 0 <= i < len(sizes) and len(sizes) > 1 and sizes[i] > cap + extra:
                        rest = (total - cap - extra) / (len(sizes) - 1)
                        split.setSizes([cap + extra if j == i else int(rest) for j in range(len(sizes))])
            return
        # Dials fill the panel: let rows stretch instead of pinning to the top.
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 1 if r < (len(self.value_cards) + max(self._prev_cols, 1) - 1)
                                    // max(self._prev_cols, 1) else 0)


class BarsPanel(GaugesPanel):
    """Horizontal bar gauges, one column."""
    PREFERRED_CARD_W = 10_000
    MAX_COLS = 1
    CARD_CLASS = BarGauge


class GraphPanel(PanelChannels, QFrame):
    """One graph: a line per channel it shows (all the read ones, or those picked for this panel
    in Select channels)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from . import theme
        mac = theme.is_mac()
        # Mac: no box around the graph: the graph itself is the card, as on Analyze (graphstyle
        # rounds it, pads it and draws its numbers inside). Classic: the card it always had.
        self.setObjectName("liveGraph" if mac else "card")
        self.setMinimumWidth(100)
        self.all_channels = []  # [(key, label)]
        self.colors = {}
        self.curves = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((0, 0, 0, 0) if mac else (12, 12, 12, 12)))
        layout.setSpacing(8)
        c = theme.colors()
        self.plot = pg.PlotWidget()
        self.plot.setBackground(c["surface"])
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "")
        axis = self.plot.getAxis("bottom")
        axis.setStyle(showValues=True)
        axis.setTickSpacing(10, 5)
        axis.tickStrings = lambda values, scale, spacing: [
            f"{v:.0f}s" for v in values
        ]
        self.plot.setMinimumHeight(100)
        self.plot.setContentsMargins(4, 4, 4, 4)
        # Axes (and the grid lines they draw) are cached as pixmaps and only redrawn when their
        # range or style changes, not on every frame as the lines scroll: ~40% less drawing.
        for side in ("left", "bottom"):
            self.plot.getAxis(side).setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        graphstyle.inside_labels(self.plot.getPlotItem())  # Mac: numbers inside, as on Analyze
        graphstyle.apply(self.plot)
        graphstyle.notifier.changed.connect(self._restyle)
        layout.addWidget(self.plot, 1)
        # The lines and their live values in a rounded box at the top left: Analyze's readout box
        # (dot, name, value) instead of pyqtgraph's legend.
        self.readout = graphstyle.readout_box(self.plot)
        self.readout.hide()
        self._readout_html = None
        self._readout_at = 0.0
        self._style_readout()
        theme.on_apply(self._style_readout)
        self.scale_mode = "auto"  # set by the Live View page (Scale dropdown)
        self.ranges = {}  # line -> (lo, hi) seen since the line was added; only grows
        self._fitted = None
        self._labels = {}
        self._names = {}

    def set_channels(self, channels, colors=None):
        """channels = [(key, label)] (every read channel); colors = {key: colour} (the channel
        list's, so a line has the same colour as its dot)."""
        self.all_channels = list(channels)
        self.colors = colors or {}
        self._rebuild_curves()

    def _shown_changed(self):
        self._rebuild_curves()

    def _style_readout(self):
        from . import theme
        try:
            c = theme.colors()
            bg = QColor(c["surface"])
            bg.setAlpha(225)
            self.readout.set_colors(bg, c["text"], c.get("border", "#555555"))
        except RuntimeError:  # deleted (layout switched)
            pass

    def _show_readout(self, data=None, force=False):
        """The box's rows: one per line, its colour dot, name (with the fitted range) and value.
        Rewritten at most 8 times a second: values change every frame, reading them doesn't."""
        now = time.monotonic()
        if not force and now - self._readout_at < 0.125:
            return
        self._readout_at = now
        if not self.curves:
            self.readout.hide()
            return
        pt = graphstyle.label_pt()
        rows = []
        for i, (key, _curve) in self.curves.items():
            color = self.colors.get(key) or graphstyle.color(i)
            d = (data or {}).get(key) or {}
            value = d.get("last_text", "–")
            name = self._labels.get(i) or self._names.get(i, key)
            rows.append((color, name, value))
        html = graphstyle.readout_html(rows, pt)  # the same box as Analyze's
        if html != self._readout_html:
            self._readout_html = html
            self.readout.setText(html)  # sizes itself to the text
        inset = graphstyle.INSET if self.objectName() == "liveGraph" else 0
        self.readout.move(inset + 8, inset + 8)
        self.readout.show()
        self.readout.raise_()

    def _rebuild_curves(self):
        self.plot.clear()
        self.curves = {}
        self.ranges = {}
        self._fitted = None
        self._labels = {}
        labels = dict(self.all_channels)
        self._names = {}
        for i, key in enumerate(self._shown_keys()):
            color = self.colors.get(key) or graphstyle.color(i)
            pen = pg.mkPen(color=color, width=graphstyle.width())
            curve = self.plot.plot([], [], pen=pen)
            self.curves[i] = (key, curve)
            self._names[i] = labels.get(key, key)
        self._show_readout(force=True)

    def _restyle(self):
        try:
            graphstyle.apply(self.plot)
        except RuntimeError:  # panel already deleted (layout switched)
            return
        width = graphstyle.width()
        for i, (key, curve) in self.curves.items():
            curve.setPen(pg.mkPen(color=self.colors.get(key) or graphstyle.color(i), width=width))
        self._readout_html = None  # the box's text size follows the graph text size
        self._show_readout(getattr(self, "_last_data", None), force=True)

    def set_scale_mode(self, mode):
        self.scale_mode = mode
        self._fitted = None  # redo the axis on the next paint

    def update_data(self, data, now, window):
        # Only the points inside the time window (plus one just before it, so the line runs in
        # from the left edge) are converted and drawn: a 30 s window is ~10% of the buffer.
        visible = {}
        for i, (key, _curve) in self.curves.items():
            d = data.get(key)
            if d and d["x"]:
                start = max(bisect.bisect_left(d["x"], now - window) - 1, 0)
                visible[i] = (d["x"][start:], d["y"][start:])
        # Ranges only grow while running, so lines don't jump as values come and go. A new line
        # starts from everything buffered; after that the visible points are enough, since
        # anything older was already counted while it was on screen.
        for i, (key, _curve) in self.curves.items():
            d = data.get(key)
            if d and d["y"]:
                old = self.ranges.get(i)
                seen = graphscale.value_range(d["y"] if old is None or i not in visible else visible[i][1])
                if seen:
                    self.ranges[i] = seen if old is None else (min(old[0], seen[0]), max(old[1], seen[1]))
        fitted = graphscale.should_fit(self.scale_mode, self.ranges.values())
        if fitted != self._fitted:
            graphscale.set_axis(self.plot, fitted)
            self._fitted = fitted
        for i, (key, curve) in self.curves.items():
            d = data.get(key)
            rng = self.ranges.get(i) if fitted else None
            if i in visible:
                xs, ys = visible[i]
                curve.setData(np.asarray(xs, dtype=float) - now, graphscale.scale(ys, rng))
            self._labels[i] = graphscale.label(self._names.get(i, key), rng)
        self.plot.setXRange(-window, 0, padding=0)
        self._last_data = data
        self._show_readout(data)


class SectionWrapper(QFrame):
    """Wraps a panel (Gauges or Graph). Right-click to switch type."""

    def __init__(self, slot_index, on_type_changed, on_alarm=None, parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.on_type_changed = on_type_changed
        self.on_alarm = on_alarm
        self.panel = None
        self.ptype = "graph"
        self.setMinimumWidth(100)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.panel_holder = QVBoxLayout()
        self.panel_holder.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.panel_holder, 1)

    def set_type(self, ptype):
        self.ptype = ptype
        self._replace_panel(ptype)

    def _card_at(self, pos):
        widget = self.childAt(pos)
        while widget is not None and getattr(widget, "key", None) is None:
            widget = widget.parentWidget()
        return widget

    def _show_menu(self, pos):
        menu = QMenu(self)
        card = self._card_at(pos)
        if card is not None and self.on_alarm is not None:
            menu.addAction(f"Set alarm for {card.name}…").triggered.connect(
                lambda: self.on_alarm(card.key))
            menu.addSeparator()
        for label, key in (("Numbers", "gauges"), ("Dials", "dials"), ("Bars", "bars"), ("Graph", "graph")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.ptype)
            action.triggered.connect(lambda checked, k=key: self._switch(k))
        menu.exec(self.mapToGlobal(pos))

    def _switch(self, ptype):
        if ptype == self.ptype:
            return
        self.ptype = ptype
        self._replace_panel(ptype)
        self.on_type_changed(self.slot_index, ptype)

    def _replace_panel(self, ptype):
        if self.panel is not None:
            self.panel_holder.removeWidget(self.panel)
            discard(self.panel)
        if ptype == "gauges":
            self.panel = GaugesPanel()
        elif ptype == "dials":
            self.panel = DialsPanel()
        elif ptype == "bars":
            self.panel = BarsPanel()
        else:
            self.panel = GraphPanel()
        self.panel_holder.addWidget(self.panel, 1)


class LiveViewPage(QWidget):
    """Real-time graphing of live ECU data — vwlog style with configurable layout."""

    title = "Live View"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        self.catalog = []
        self.catalog_module = None
        self.catalog_ecu = None  # whose channel list is loaded (state.ecu_key)
        self.channel_data = {}
        self.started = 0.0
        self.running = False
        self.mirroring = False  # showing the Logger's recording (_start_mirror)
        self.sections = []  # list of SectionWrapper instances
        self.current_grid = DEFAULT_GRID
        self.section_types = list(DEFAULT_TYPES)
        self.alarm_active = set()
        self._alarm_text = None

        state.changed.connect(self.refresh)
        state.busy_changed.connect(lambda _: self.refresh())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The channel selector, Analyze style, in a dropdown (Select channels ▾ at the left of the
        # toolbar): search, Select all / Hide all, every channel under its category with an
        # on/off switch. The tree behind it isn't shown: it keeps the channels, state, colours.
        self.tree = ChannelTree(self)
        self.tree.hide()
        self.summary = muted("")
        self.summary.setWordWrap(True)
        self.channels = ChannelDropdown(self.tree, self._channels_changed, self, footer=self.summary,
                                        describe=self._describe)
        self.selector = self.channels.selector
        self.selector.scope_source = self  # All · 1 Dials · 2 Graph …: each panel's own channels
        self.selector.menu_requested.connect(self._channel_menu)
        # Raw groups: any measuring group by number, its fields unnamed ("Group 115 · field 3").
        # {group: [{"field": n, "unit": u}, …]}, or [] until the group has been read once.
        self.raw_groups = {}
        self.selector.raw_groups_enabled = True
        self.selector.add_raw_group.connect(self._add_raw)
        self.selector.remove_raw_group.connect(self._remove_raw)

        right = QWidget()
        right.setObjectName("page")
        right.setMinimumWidth(0)
        self.right_layout = QVBoxLayout(right)
        self.right_layout.setContentsMargins(16, 16, 16, 16)
        self.right_layout.setSpacing(10)
        outer.addWidget(scrollable(right), 1)

        # Controls row: channels, Start / Clear on the left; layout, dashboards and ⚙ on the right
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.addWidget(self.channels)
        self.start_button = T.RunButton("play_arrow", "ok")  # ▶ start (green, like "stopped") / ■ stop
        self.start_button.setToolTip("Start: show live values (nothing is saved)")
        self.start_button.clicked.connect(self.toggle)
        ctrl.addWidget(self.start_button)

        self.clear_button = T.icon_button("clear_all", "Clear: empty the graphs and readings "
                                          "(keeps the channels)")
        self.clear_button.clicked.connect(self._clear_data)
        ctrl.addWidget(self.clear_button)
        # Sample rate: its changing width only eats into the stretch, so nothing moves.
        self.rate_label = muted("")
        self.rate_label.setToolTip("Readings received per second from the module")
        self.rate_label.hide()
        ctrl.addSpacing(4)
        ctrl.addWidget(self.rate_label)

        # Time shown and Scale keep their values in these (never shown) dropdowns, which the
        # graphs and saved dashboards read; the ⚙ menu sets them.
        self.window_combo = QComboBox(self)
        for label, secs in WINDOW_SECONDS:
            self.window_combo.addItem(label, secs)
        self.window_combo.setCurrentIndex(0)
        self.window_combo.hide()
        self.scale_combo = graphscale.combo(state.settings, self._scale_changed)
        self.scale_combo.setParent(self)
        self.scale_combo.hide()
        self.options = T.MenuIconButton("instant_mix", "Live View settings: time shown, scale",
                                      self._fill_options)
        # Right side: how the panels look (layout, saved dashboards) next to ⚙.
        ctrl.addStretch(1)
        # Math channels, then Presets (both as on Analyze), then layout, dashboards, ⚙
        self.math_button = T.icon_button("function", "Math channels: make channels from formulas, "
                                         "e.g. boost in psi or AFR")
        self.math_button.clicked.connect(self._edit_math)
        ctrl.addWidget(self.math_button)
        self.presets = PresetButton(state, lambda: {v["id"] for v in self._checked_values()},
                                    lambda ids: self._build_tree(ids))
        ctrl.addWidget(self.presets)
        self.layout_picker = LayoutPicker(self._grid_clicked)  # one button; arrangements in its menu
        ctrl.addWidget(self.layout_picker)
        self.dashboard_button = DashboardButton(self)
        ctrl.addWidget(self.dashboard_button)
        ctrl.addWidget(self.options)

        from . import theme
        if theme.is_mac():
            self._mac_rows(ctrl)
        else:
            self.right_layout.addLayout(ctrl)

        self.banner = Banner()
        self.right_layout.addWidget(self.banner)

        # Panel area — rebuilt when layout changes
        self.panel_container = QWidget()
        self.panel_container_layout = QVBoxLayout(self.panel_container)
        self.panel_container_layout.setContentsMargins(0, 0, 0, 0)
        self.panel_container_layout.setSpacing(0)
        self.right_layout.addWidget(self.panel_container, 1)

        # Repaint timer
        self.ticker = QTimer(self)
        self.ticker.setInterval(33)  # ~30 fps: graphs scroll smoothly (alarm flashing is clock-based)
        self.ticker.timeout.connect(self._paint)
        self.samples = 0

        state.sample.connect(self.on_sample)
        # While the Logger records, show its readings here too (the module answers one job at a
        # time, so Live View can't run its own reads then; it just uses the Logger's)
        state.logger_recording.connect(lambda on: self._start_mirror() if on else self._end_mirror())

        # Build initial layout
        self._build_layout()

    # --- layout ---

    def _grid_clicked(self, index):
        if index == self.current_grid:
            return
        self.current_grid = index
        _, rows, _cols = GRIDS[index]
        n_slots = sum(len(r) for r in rows)
        # Extend or trim section types to match new slot count
        while len(self.section_types) < n_slots:
            self.section_types.append("graph")
        self.section_types = self.section_types[:n_slots]
        before = self.dashboard_state()  # each panel keeps its channels in the new layout
        self._build_layout()
        self._populate_panels()
        self._apply_panel_channels(before)
        self._remember_layout()

    def _section_type_changed(self, slot_index, ptype):
        self.section_types[slot_index] = ptype
        self._populate_panels()
        self._remember_layout()

    # --- dashboards ---

    def dashboard_state(self):
        panels = {str(index): sorted(section.panel.shown) for index, section in enumerate(self.sections)
                  if isinstance(section.panel, PanelChannels) and section.panel.shown is not None}
        return {
            "grid": self.current_grid,
            "types": list(self.section_types),
            "channels": sorted(v["id"] for v in self._checked_values()),
            "panels": panels,  # the channels each panel shows (missing: all the read ones)
            "window": self.window_combo.currentData(),
        }

    def apply_dashboard(self, dash, channels=True, relayout=True):
        """relayout=False: keep the panels that are there (only their channels change)."""
        if self.running:
            self.banner.show_message("Stop Live View before switching dashboards.", "warning")
            return
        self._applying = True
        try:
            grid = dash.get("grid", self.current_grid)
            if 0 <= grid < len(GRIDS):
                self.current_grid = grid
            types = [t for t in dash.get("types", []) if t in ("gauges", "dials", "bars", "graph")]
            if types:
                self.section_types = types
            if relayout:
                self._build_layout()
            if channels and dash.get("channels") is not None:
                self._build_tree(set(dash["channels"]))  # also fills the panels
            else:
                self._populate_panels()
            self._apply_panel_channels(dash)
            window = self.window_combo.findData(dash.get("window"))
            if window >= 0:
                self.window_combo.setCurrentIndex(window)
        finally:
            self._applying = False
        self._remember_layout()

    def _apply_panel_channels(self, dash):
        """Each panel's own channels from a saved layout. Older layouts kept a graph's lines as its
        four dropdowns ("graphs": {slot: [key or None, …]}): those become the graph's channels."""
        panels = {str(k): v for k, v in (dash.get("panels") or {}).items()}
        for index, keys in (dash.get("graphs") or {}).items():
            picked = [k for k in keys or () if k]
            if picked and str(index) not in panels:
                panels[str(index)] = picked
        for index, keys in panels.items():
            index = int(index)
            if index < len(self.sections) and isinstance(self.sections[index].panel, PanelChannels):
                self.sections[index].panel.set_shown(keys, notify=False)

    def _remember_layout(self):
        """The last layout per module comes back next time."""
        if getattr(self, "_applying", False) or not self.state.setup_family or not self.catalog:
            return
        self.state.settings.set(f"liveview_layout:{self.state.setup_family['name']}", self.dashboard_state())

    # --- each panel's channels (Select channels' panel segments) ---

    TYPE_NAMES = {"gauges": "Numbers", "dials": "Dials", "bars": "Bars", "graph": "Graph"}

    def scopes(self):
        """[(scope, label, tooltip)]: All, then each panel in reading order."""
        out = [(None, "All", "The channels read from the module. Pick a panel to choose which of "
                             "them it shows.")]
        for i, section in enumerate(self.sections):
            name = self.TYPE_NAMES.get(section.ptype, "Panel")
            out.append((i, f"Panel {i + 1}", f"Panel {i + 1} ({name}, panels count left to right, "
                                             "top to bottom): switch on the channels it shows"))
        return out

    # All = what the panels show together: a channel is read while at least one panel shows it.
    # A panel switching one on reads it; the last panel switching it off stops reading it; All
    # switching one on/off adds it to / takes it from every panel (_sync_panels_to_read).

    def _read_keys(self):
        return {it.data(0, ROLE) for it in self.tree.checked_items() if not it.data(0, FIXED_ROLE)}

    def _panels(self):
        return [s.panel for s in self.sections if isinstance(s.panel, PanelChannels)]

    def is_on(self, scope, key):
        panel = self.sections[scope].panel if scope < len(self.sections) else None
        if not isinstance(panel, PanelChannels):
            return False
        return key in self._read_keys() if panel.shown is None else key in panel.shown

    def set_on(self, scope, keys):
        if scope >= len(self.sections) or not isinstance(self.sections[scope].panel, PanelChannels):
            return
        read = self._read_keys()
        for panel in self._panels():  # "all of them" means what's read now, not what's added next
            if panel.shown is None:
                panel.shown = set(read)
        self.sections[scope].panel.shown = set(keys)
        wanted = set().union(*(p.shown for p in self._panels()))
        self.tree.blockSignals(True)
        for item in self.tree.items():
            if item.data(0, FIXED_ROLE):
                continue
            on = item.data(0, ROLE) in wanted
            if (item.checkState(0) == Qt.CheckState.Checked) != on:
                item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._read_before = wanted  # not an All change: don't spread it to the other panels
        for panel in self._panels():  # every read channel shown: back to "all" (tidy saved layouts)
            if panel.shown >= wanted:
                panel.shown = None
        self._channels_changed()
        self.channels.update_count()

    def _sync_panels_to_read(self):
        """After All (or a preset) changed what's read: new channels join every panel, dropped ones
        leave every panel."""
        read = self._read_keys()
        before = getattr(self, "_read_before", None)
        self._read_before = read
        if before is None:
            return
        added, removed = read - before, before - read
        for panel in self._panels():
            if panel.shown is not None and (added or removed):
                panel.shown = (panel.shown | added) - removed

    def _build_layout(self):
        """Tear down and rebuild the panel area based on the selected grid."""
        self.sections = []

        self.layout_picker.set_current(self.current_grid)

        # Remove old content
        while self.panel_container_layout.count():
            item = self.panel_container_layout.takeAt(0)
            discard(item.widget())

        _, rows, columns = GRIDS[self.current_grid]
        n_slots = sum(len(r) for r in rows)
        while len(self.section_types) < n_slots:
            self.section_types.append("graph")

        slot = 0
        built_rows = []
        for row_def in rows:
            row_sections = []
            for _ in row_def:
                section = SectionWrapper(slot, self._section_type_changed, self._edit_alarm)
                section.set_type(self.section_types[slot])
                self.sections.append(section)
                row_sections.append(section)
                slot += 1
            built_rows.append(row_sections)

        # Rows: outer split is vertical, each row split horizontally.
        # Columns: the other way round.
        outer_o = Qt.Orientation.Horizontal if columns else Qt.Orientation.Vertical
        inner_o = Qt.Orientation.Vertical if columns else Qt.Orientation.Horizontal
        if len(built_rows) == 1 and len(built_rows[0]) == 1:
            self.panel_container_layout.addWidget(built_rows[0][0], 1)
        elif len(built_rows) == 1:
            hsplit = QSplitter(Qt.Orientation.Horizontal)
            hsplit.setObjectName("panelSplit")
            hsplit.setChildrenCollapsible(False)
            for sec in built_rows[0]:
                hsplit.addWidget(sec)
            hsplit.setSizes([500] * len(built_rows[0]))
            self.panel_container_layout.addWidget(hsplit, 1)
        else:
            vsplit = QSplitter(outer_o)
            vsplit.setObjectName("panelSplit")
            vsplit.setChildrenCollapsible(False)
            for row_secs in built_rows:
                if len(row_secs) == 1:
                    vsplit.addWidget(row_secs[0])
                else:
                    hsplit = QSplitter(inner_o)
                    hsplit.setObjectName("panelSplit")
                    hsplit.setChildrenCollapsible(False)
                    for sec in row_secs:
                        hsplit.addWidget(sec)
                    hsplit.setSizes([500] * len(row_secs))
                    vsplit.addWidget(hsplit)
            vsplit.setSizes([500] * len(built_rows))
            self.panel_container_layout.addWidget(vsplit, 1)

    def _populate_panels(self):
        """Push current channel data to all panels."""
        values = self._checked_values()
        self._setup_channels(values)

    @property
    def gauge_panels(self):
        return [s.panel for s in self.sections
                if s.panel is not None and isinstance(s.panel, GaugesPanel)]

    @property
    def graph_panels(self):
        return [s.panel for s in self.sections
                if s.panel is not None and isinstance(s.panel, GraphPanel)]

    # --- catalog / tree ---

    def _mac_rows(self, ctrl):
        """Mac: the same split as every page (see playback_page._mac_toolbar). Title bar: [▶ Start]
        Clear, the reading rate … Presets, settings. Row 2: Select channels ▾ … how the panels
        look (Math, layout, dashboards)."""
        from .mac_shell import CONTENT_MARGINS, page_bar
        while ctrl.count():
            ctrl.takeAt(0)
        self.right_layout.setContentsMargins(*CONTENT_MARGINS)
        self.mac_bar, bar = page_bar()
        bar.addWidget(self.start_button)
        bar.addWidget(self.clear_button)
        bar.addSpacing(4)
        bar.addWidget(self.rate_label)
        bar.addStretch(1)
        from .mac_shell import first_row
        row2 = first_row(QHBoxLayout())  # everything but Start / Clear belongs to the panels: row 2's right end
        row2.addWidget(self.channels)
        row2.addStretch(1)
        from .mac_shell import row_icons  # rule 7: the one right-hand order
        row_icons(row2, tools=(self.math_button, self.layout_picker, self.dashboard_button),
                  presets=self.presets, settings=self.options)
        self.right_layout.insertLayout(0, row2)

    def refresh(self):
        family = self.state.setup_family
        module = family["module"] if family and family.get("can_log") else None
        ecu = self.state.catalog_params(module).get("ecu") if module is not None else None
        if (module, ecu) != (self.catalog_module, self.catalog_ecu) and not self.running \
                and not self.mirroring:
            self.catalog_module, self.catalog_ecu = module, ecu
            self._read_before = None  # another module's channels: nothing to spread to the panels
            self.catalog = []
            self.raw_groups = {}
            self._build_tree()
            if module is not None:
                self.state.offline("catalog", self.state.catalog_params(module), self._catalog_loaded,
                                   lambda e: self.banner.show_message(self.error_text(e), "error"))
        from .needs_car import gate
        if self.running:
            self.start_button.setEnabled(True)  # Stop must always work
        else:
            picked = self.state.family is not None and self.state.family.get("can_log")
            gate(self.start_button, self.state, bool(picked) and not self.state.busy)  # plugged in: needs a real pick
        # channels can't change while running (the module is reading those groups), but the list
        # stays live: it shows the values
        live = self.running or self.mirroring
        self.channels.setEnabled(not live)
        if live:
            self.channels.close_picker()
        self.presets.setEnabled(not live and self.state.setup_family is not None)
        if self.mirroring:
            self.start_button.setToolTip("Live View is showing the Logger's recording. "
                                         "Stop it in the Logger to read on your own.")
        if module is None and not live:
            self.banner.show_message("Choose a module that supports logging on the Connect page.", "info")
        elif not live and self.banner.property("level") == "info":
            self.banner.hide()

    def error_text(self, error):
        return error.get("message", str(error)) if isinstance(error, dict) else str(error)

    def _catalog_loaded(self, values):
        self.catalog = values or []
        self.raw_groups = self._load_raw()
        saved = self.state.settings.get(self._settings_key()) or []
        layout = self.state.settings.get(f"liveview_layout:{self.state.setup_family['name']}") \
            if self.state.setup_family else None
        if layout:
            self.apply_dashboard(layout, channels=False)
        self._build_tree(set(saved))
        if layout:
            # each panel's channels, now the list exists; the panels built above stay
            self.apply_dashboard({"graphs": layout.get("graphs"), "panels": layout.get("panels")},
                                 channels=False, relayout=False)
        if not self.catalog:
            self.summary.setText("No named values available for this module.")

    def _settings_key(self):
        return f"liveview:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _conv(self, value):
        return units.conversion(value.get("unit", ""), self.state.settings)

    def showEvent(self, event):
        super().showEvent(event)
        # Units may have been switched on the Settings page.
        if getattr(self, "_units_built", None) not in (None, units.system(self.state.settings)) \
                and not self.running and self.catalog:
            self._build_tree({v["id"] for v in self._checked_values()})
        if self.running or self.mirroring:
            self._paint()  # drawing pauses while the page is hidden: catch up straight away

    def _build_tree(self, checked=()):
        """Every channel (catalog + math) as a row, known by its key; the ticked ones show,
        each with its colour, live value and range bar (like Analyze's list)."""
        self._units_built = units.system(self.state.settings)
        limits = self._limits()
        self.tree.blockSignals(True)
        self.tree.clear()
        order = []
        with self.tree.bulk():
            for value in self.catalog + self._math_values():
                key = self._vkey(value)
                name = ("\u0192 " if value.get("formula") else "") + value["name"]
                item = self.tree.new_item(name, key)
                item.setData(0, VALUE_ROLE, value)
                item.setData(0, CATEGORY_ROLE, value.get("category") or "Other")
                item.setToolTip(0, self._tip(value, limits))
                if value["id"] in checked:
                    item.setCheckState(0, Qt.CheckState.Checked)
                order.append(key)
            named = {self._vkey(v) for v in self.catalog}
            for group in sorted(self.raw_groups):
                fields = self.raw_groups[group]
                if not fields:  # not read yet: one row for the group (read while it's in the list)
                    item = self.tree.new_item(f"Group {group} · fields show once read", f"raw:{group}")
                    item.setData(0, VALUE_ROLE, {"id": f"rawgroup:{group}", "raw_group": group, "group": group})
                    item.setData(0, FIXED_ROLE, True)
                    item.setData(0, RAW_GROUP_ROLE, group)
                    item.setData(0, CATEGORY_ROLE, RAW_CATEGORY)
                    item.setCheckState(0, Qt.CheckState.Checked)
                    continue
                for f in fields:
                    key = f"{group}.{f['field']}"
                    if key in named:
                        continue  # already in the list under its name
                    value = {"id": f"raw:{key}", "name": f"Group {group} · field {f['field']}", "group": group,
                             "field": f["field"], "unit": f.get("unit", ""), "category": RAW_CATEGORY, "raw": True}
                    item = self.tree.new_item(value["name"], key)
                    item.setData(0, VALUE_ROLE, value)
                    item.setData(0, CATEGORY_ROLE, RAW_CATEGORY)
                    item.setData(0, RAW_GROUP_ROLE, group)
                    item.setToolTip(0, self._tip(value, limits))
                    if value["id"] in checked or f.get("new"):
                        item.setCheckState(0, Qt.CheckState.Checked)
                    order.append(key)
        self.tree.log_order = order  # the selector lists them in catalog order, by category
        self.tree.blockSignals(False)
        self._channels_changed()
        self.channels.refresh()

    def _tip(self, value, limits):
        shown, conv = self._conv(value)
        key = self._vkey(value)
        lines = [value["name"] + (f" ({shown})" if shown else "")]
        lines.append(f"Formula: {value['expr']}" if value.get("formula") else f"Group {key}")
        alarm = alarms.describe(units.limit_to_display(limits.get(key), conv))
        lines.append(f"Alarm: {alarm}" if alarm else "No alarm (right-click to set one)")
        return "\n".join(lines)

    def _channels_changed(self):
        """After switching channels on or off, or a preset: colours, then the panels."""
        self.colors = self.tree.sync_rows(self.state.settings)
        self._sync_panels_to_read()
        self._selection_changed()

    # --- math channels ---

    @staticmethod
    def _vkey(value):
        return value.get("key") or f"{value['group']}.{value['field']}"

    def _math_values(self):
        """Math channels whose referenced channels all exist on this module."""
        by_name = {v["name"]: v for v in self.catalog}
        out = []
        for definition, formula in mathchannels.compiled(self.state.settings):
            deps = [by_name.get(r) for r in formula.refs]
            if not self.catalog or None in deps:
                continue
            key = f"math:{definition['name']}"
            out.append({"id": key, "key": key, "name": definition["name"],
                        "unit": definition.get("unit", ""), "category": "Math",
                        "expr": definition["expr"], "formula": formula,
                        "deps": [self._vkey(d) for d in deps], "groups": [d["group"] for d in deps]})
        return out

    def _groups(self, values):
        groups = set()
        for v in values:
            groups.update(v["groups"] if v.get("groups") else [v["group"]])
        return sorted(groups)

    def _fill_options(self, menu):
        """⚙: Time shown ▸ and Scale ▸ (✓ on the current choice)."""
        from . import analyze_tools as T
        for title, glyph, box in (("Time shown", "schedule", self.window_combo),
                                  ("Scale", "height", self.scale_combo)):
            sub = T.menu_sub(menu, title, glyph)
            for i in range(box.count()):
                act = sub.addAction(box.itemText(i))
                act.setCheckable(True)
                act.setChecked(i == box.currentIndex())
                tip = box.itemData(i, Qt.ItemDataRole.ToolTipRole)
                if tip:
                    act.setToolTip(tip)
                act.triggered.connect(lambda _=False, b=box, k=i: b.setCurrentIndex(k))

    def _edit_math(self):
        if self.running:
            self.banner.show_message("Stop Live View before editing math channels.", "warning")
            return
        dialog = mathchannels.MathChannelsDialog(self.state.settings, [v["name"] for v in self.catalog], self)
        if dialog.exec():
            self._build_tree({v["id"] for v in self._checked_values()})

    def _checked_values(self):
        return [it.data(0, VALUE_ROLE) for it in self.tree.checked_items()]

    def _selection_changed(self):
        values = self._checked_values()
        groups = self._groups(values)
        shown = [v for v in values if "raw_group" not in v]
        waiting = len(values) - len(shown)  # raw groups not read yet
        if values:
            self.summary.setText(groups_summary(len(shown), groups, waiting))
        elif self.catalog:
            self.summary.setText("Tick the values you want to monitor.")
        key = self._settings_key()
        if key and self.catalog:
            self.state.settings.set(key, sorted(v["id"] for v in values))
        if not self.running:
            self._setup_channels(shown)
            self._remember_layout()

    # --- raw groups ---

    def _raw_key(self):
        return f"liveview_raw:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _load_raw(self):
        key = self._raw_key()
        saved = (self.state.settings.get(key) or {}) if key else {}
        return {int(g): list(fields or []) for g, fields in saved.items()}

    def _save_raw(self):
        key = self._raw_key()
        if key:
            self.state.settings.set(key, {str(g): [{"field": f["field"], "unit": f.get("unit", "")}
                                                   for f in fields]
                                          for g, fields in self.raw_groups.items()})

    def _add_raw(self):
        if self.running or not self.catalog_module:
            return
        group = ask_raw_group(self, self.raw_groups)
        if group is None:
            return
        self.raw_groups[group] = []
        self._save_raw()
        self._build_tree({v["id"] for v in self._checked_values()})
        self.banner.show_message(f"Group {group} added. Press Start: its fields show up in Select "
                                 "channels once it's been read.", "info")

    def _remove_raw(self, group):
        if self.running:
            return
        self.raw_groups.pop(group, None)
        self._save_raw()
        self._build_tree({v["id"] for v in self._checked_values()})

    def _learn_raw(self, group, fields):
        """First read of a raw group: its fields become channels (switched on) right away."""
        learned = [{"field": f["index"], "unit": f.get("unit", ""), "new": True}
                   for f in fields if f.get("index") is not None]
        if not learned:
            return
        self.raw_groups[group] = learned
        self._save_raw()
        checked = {v["id"] for v in self._checked_values()}
        self._build_tree(checked)
        for f in learned:
            f.pop("new", None)
        self._setup_channels([v for v in self._checked_values() if "raw_group" not in v])
        named = {self._vkey(v) for v in self.catalog}
        new = [f for f in learned if f"{group}.{f['field']}" not in named]
        already = len(learned) - len(new)
        self.banner.show_message(
            f"Group {group} has {len(learned)} field{'s' if len(learned) != 1 else ''}: "
            + (f"{len(new)} added under {RAW_CATEGORY} in Select channels" if new else "nothing new")
            + (f" ({already} already in the list by name)" if already else "") + ".", "info")

    def _describe(self, key):
        """The Select channels description box: the channel under the mouse."""
        from html import escape
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key), None)
        value = item.data(0, VALUE_ROLE) if item is not None else None
        if not isinstance(value, dict):
            return ""
        if "raw_group" in value:
            return (f"<b>Group {value['raw_group']}</b> · not read yet<br>Press Start: once the group "
                    "has been read, each of its fields shows here as its own channel. ✕ removes the group.")
        shown, conv = self._conv(value)
        alarm = alarms.describe(units.limit_to_display(self._limits().get(self._vkey(value)), conv))
        alarm_line = f"<br>Alarm: {escape(alarm)}" if alarm else "<br>No alarm (right-click to set one)"
        if value.get("raw"):
            unit = f" · {escape(shown)}" if shown else ""
            return (f"<b>{escape(value['name'])}</b>{unit}<br>An unnamed field. Watch it while you "
                    "change something (rpm, throttle, a switch) to work out what it shows. ✕ removes "
                    "the group." + alarm_line)
        if value.get("formula"):
            unit = f" · {escape(shown)}" if shown else ""
            return f"<b>{escape(value['name'])}</b>{unit}<br>Math channel: {escape(value['expr'])}" + alarm_line
        return channel_info.tooltip(value) + alarm_line

    # --- alarms ---

    def _alarms_key(self):
        return f"alarms:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _limits(self):
        key = self._alarms_key()
        return (self.state.settings.get(key) or {}) if key else {}

    def _channel_menu(self, key, global_pos):
        """Right-click a channel in the selector: alarm, math channels."""
        from . import analyze_tools as T
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key), None)
        value = item.data(0, VALUE_ROLE) if item is not None else None
        if not value or "raw_group" in value:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        # the dialogs open over the page, so the dropdown closes first
        T.menu_action(menu, f"Set alarm for {value['name']}…", "notifications",
                      lambda: (self.channels.close_picker(), self._edit_alarm(key)))
        T.menu_header(menu, "More")
        T.menu_action(menu, "Math channels…", "function",
                      lambda: (self.channels.close_picker(), self._edit_math()))
        menu.exec(global_pos)

    def _edit_alarm(self, key):
        settings_key = self._alarms_key()
        if not settings_key:
            return
        value = next((v for v in self.catalog + self._math_values() if self._vkey(v) == key), None)
        if value is None:
            return
        limits = dict(self._limits())
        shown, conv = self._conv(value)
        dialog = alarms.AlarmDialog(value["name"], shown, units.limit_to_display(limits.get(key), conv), self)
        if not dialog.exec():
            return
        limit = units.limit_to_raw(dialog.limit(), conv)
        if limit is None:
            limits.pop(key, None)
        else:
            limits[key] = limit
        self.state.settings.set(settings_key, limits)
        self._update_alarm_column()

    def _update_alarm_column(self):
        """Alarms show in each channel's tooltip."""
        limits = self._limits()
        for item in self.tree.items():
            item.setToolTip(0, self._tip(item.data(0, VALUE_ROLE), limits))
        self.channels.refresh()

    def _check_alarms(self):
        """Which channels are past their limits; beep on newly tripped ones."""
        limits = self._limits()
        active = {key for key, ch in self.channel_data.items()
                  if alarms.tripped(limits.get(key), ch["last_raw"])}
        new = active - self.alarm_active
        # showing the Logger's recording: the Logger beeps (and pops up) for these itself
        if not self.mirroring and any(limits[key].get("beep", True) for key in new):
            QApplication.beep()
        self.alarm_active = active
        flash = "on" if int(time.time() * 4) % 2 == 0 else "dim"
        if active:
            parts = []
            for key in sorted(active):
                ch = self.channel_data[key]
                unit = f" {ch['unit']}" if ch["unit"] else ""
                shown_limit = units.limit_to_display(limits[key], ch["conv"])
                parts.append(f"{ch['name']} {ch['last_text']}{unit} ({alarms.describe(shown_limit)})")
            text = "Alarm: " + " · ".join(parts)
            if text != self._alarm_text:
                self.banner.show_message(text, "error")
        elif self._alarm_text is not None:
            self.banner.show_message("Live view running. Data is displayed only, not saved.", "info")
            text = None
        else:
            text = None
        self._alarm_text = text
        return {key: flash for key in active}

    def _reset_alarms(self):
        self.alarm_active = set()
        self._alarm_text = None
        for gp in self.gauge_panels:
            gp.update_values(self.channel_data, {})

    # --- start / stop ---

    def toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.state.need_adapter("log"):
            return
        if self.state.family is None:
            self.banner.show_message("No module picked yet. Press Scan on the Connect page (or "
                                     "Select manually there) first.", "warning",
                                     actions={"Go to Connect": lambda: self.state.open_tab.emit("connect")})
            return
        values = self._checked_values()
        groups = self._groups(values)
        if not groups:
            self.banner.show_message("Tick at least one value to monitor.", "warning")
            return
        self._remember_layout()
        self._setup_channels([v for v in values if "raw_group" not in v])
        self._clear_data()
        self.started = time.time()
        self.samples = 0
        self._rate_tick = None
        self.banner.show_message("Live view running. Data is displayed only, not saved.", "info")
        self.running = self.state.run(
            "log", {"groups": groups, "interval_ms": 0},
            self._finished, self._failed)
        if self.running:
            self.start_button.set_running(True)
            self.start_button.setToolTip("Stop live values")
            self.ticker.start()
        self.refresh()

    def _stop(self):
        self.start_button.setEnabled(False)  # greys out until stopped
        self.start_button.setToolTip("Stopping…")
        self.state.stop()

    def _setup_channels(self, values):
        """Populate all panels with current channel data."""
        self.channel_data = {}
        channels_tuples = []
        channels_labels = []

        self.math_active = [v for v in values if v.get("formula")]
        self.latest_raw = {}
        for v in values:
            key = self._vkey(v)
            name = v["name"]
            unit, conv = self._conv(v)
            label = f"{name} ({unit})" if unit else name
            self.channel_data[key] = {
                "x": [], "y": [], "last": None, "last_raw": None,
                "last_text": "–", "name": name, "unit": unit, "conv": conv,
            }
            channels_tuples.append((key, name, unit))
            channels_labels.append((key, label))

        for gp in self.gauge_panels:
            gp.on_shown_changed = self._remember_layout
            gp.set_channels(channels_tuples, self.channel_data)
        for gp in self.graph_panels:
            gp.on_shown_changed = self._remember_layout
            gp.set_channels(channels_labels, getattr(self, "colors", {}))

    def _scale_changed(self):
        for gp in self.graph_panels:
            gp.set_scale_mode(self.scale_combo.currentData())
        self._paint()

    def _clear_data(self):
        for d in self.channel_data.values():
            d["x"].clear()
            d["y"].clear()
            d["last"] = d["last_raw"] = None
            d["last_text"] = "–"
        for gp in self.gauge_panels:
            for card in gp.value_cards.values():
                card.set_value("–")
        for gp in self.graph_panels:
            gp._rebuild_curves()

    # --- sample handling ---

    def on_sample(self, sample):
        if not (self.running or self.mirroring):
            return
        self.samples += 1
        if sample.get("error"):
            return
        group = sample["group"]
        t = sample.get("wall_time", time.time()) - self.started
        if group in self.raw_groups and not self.raw_groups[group]:
            self._learn_raw(group, sample.get("fields") or ())
        for field in sample.get("fields") or ():
            key = f"{group}.{field['index']}"
            value = field.get("value")
            if not isinstance(value, (int, float)):
                text = str(field.get("text") or value or "").split(" ")[0]
                try:
                    value = float(text)
                except (ValueError, TypeError):
                    continue
            self.latest_raw[key] = value
            self._push(key, t, value)
        for m in getattr(self, "math_active", ()):
            if group not in m["groups"]:
                continue
            inputs = [self.latest_raw.get(k) for k in m["deps"]]
            if None in inputs:
                continue
            try:
                result = float(m["formula"].evaluate(inputs))
            except (TypeError, ValueError):
                continue
            if result == result:  # not NaN
                self._push(m["key"], t, result)

    def ai_context(self):
        """What the Assistant sees of this page (None: no live values yet)."""
        rows = []
        for ch in self.channel_data.values():
            ys = ch["y"]
            if not ys:
                continue
            rows.append(f"- {ch['name']}: now {ch['last_text']} {ch['unit']}".rstrip()
                        + f" (recent min {min(ys):.4g}, max {max(ys):.4g}, {len(ys)} readings)")
        if not rows:
            return None
        family = self.state.setup_family or {}
        tripped = [self.channel_data[k]["name"] for k in getattr(self, "alarm_active", ()) if k in self.channel_data]
        context = (f"Module: {family.get('label', '?')}, part {self.state.part() or '?'}\n"
                   f"Live View {'running' if self.running else 'stopped'}; values shown in "
                   f"{units.system(self.state.settings)} units.\n"
                   + (f"Alarms tripped right now: {', '.join(tripped)}\n" if tripped else "")
                   + "Values (the last few minutes):\n" + "\n".join(rows))
        return {"title": "Live View", "context": context,
                "question": "Explain what these live values say about the car right now: what's normal, what "
                            "looks off, and what to watch or check next."}

    def _push(self, key, t, value):
        ch = self.channel_data.get(key)
        if ch is None:
            return
        ch["last_raw"] = value
        value = units.to_display(value, ch["conv"])
        ch["x"].append(t)
        ch["y"].append(value)
        ch["last"] = value
        ch["last_text"] = units.fmt(value)
        if len(ch["x"]) > MAX_POINTS:
            ch["x"] = ch["x"][-MAX_POINTS:]
            ch["y"] = ch["y"][-MAX_POINTS:]

    def _paint(self):
        states = self._check_alarms() if (self.running or self.mirroring) else {}
        if not self.isVisible():
            return  # another tab is showing (e.g. the Logger recording): alarms only, no drawing
        limits = {k: units.limit_to_display(l, self.channel_data[k]["conv"])
                  for k, l in self._limits().items() if k in self.channel_data}
        for gp in self.gauge_panels:
            gp.update_values(self.channel_data, states, limits)

        now = time.time() - self.started
        window = self.window_combo.currentData()
        mode = self.scale_combo.currentData()
        for gp in self.graph_panels:
            if gp.scale_mode != mode:  # new panels (layout changes) follow the Scale dropdown
                gp.set_scale_mode(mode)
            gp.update_data(self.channel_data, now, window)

        elapsed = time.time() - self.started
        # Twice a second is plenty to read; every frame (30/s) would just flicker.
        if elapsed > 0 and int(elapsed * 2) != getattr(self, "_rate_tick", None):
            self._rate_tick = int(elapsed * 2)
            self.rate_label.setText(f"Receiving {self.samples / elapsed:.1f} samples/s")
            self.rate_label.show()

    # --- showing the Logger's recording ---

    def _start_mirror(self):
        """The Logger started recording: the gauges, graphs and alarms follow its readings.
        Values the Logger isn't reading get no data (they stay at –)."""
        if self.running:
            return  # can't happen (one job at a time), but never mix two sources
        self.mirroring = True
        self._remember_layout()
        self._setup_channels([v for v in self._checked_values() if "raw_group" not in v])
        self._clear_data()
        self.started = time.time()
        self.samples = 0
        self._rate_tick = None
        self.banner.show_message("Showing the Logger's recording: Live View isn't reading the module "
                                 "itself, so values the Logger isn't logging stay at –. Stop recording "
                                 "in the Logger.", "info",
                                 actions={"Go to Logger": lambda: self.state.open_tab.emit("logger")})
        self.ticker.start()
        self.refresh()

    def _end_mirror(self):
        if not self.mirroring:
            return
        self.mirroring = False
        self.ticker.stop()
        self._paint()
        self._reset_alarms()
        self.start_button.setToolTip("Start: show live values (nothing is saved)")
        self.banner.show_message("The Logger stopped recording.", "ok")
        self.refresh()

    def _end(self):
        self.running = False
        self.ticker.stop()
        self._paint()
        self._reset_alarms()
        self.start_button.set_running(False)
        self.start_button.setToolTip("Start: show live values (nothing is saved)")
        self.refresh()

    def _finished(self, result):
        self._end()
        if result.get("error"):
            self.banner.show_message("Stopped: " + str(result["error"]), "error")
        else:
            self.banner.show_message("Live view stopped.", "ok")

    def _failed(self, error):
        self._end()
        self.banner.show_message(self.error_text(error), "error")
