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
from .widgets import Banner, muted, scrollable

WINDOW_SECONDS = (("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300))
MAX_POINTS = 6000
GRAPH_SLOTS = 4  # max channel dropdowns per graph

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
DEFAULT_GRID = 3   # "2 + 1" — gauges top, two graphs below
DEFAULT_TYPES = ["gauges", "graph", "graph"]


def _paint_grid(p, rect, groups, columns, color, gap=2):
    """Draw a panel-arrangement icon (rounded cells) into rect."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    x0, y0, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    n_groups = len(groups)
    if columns:  # groups are columns: split the width, stack within each
        group_w = (w - gap * (n_groups - 1)) / n_groups
        x = x0
        for col in groups:
            cell_h = (h - gap * (len(col) - 1)) / len(col)
            y = y0
            for _ in col:
                p.drawRoundedRect(int(x), int(y), int(group_w), int(cell_h), 2, 2)
                y += cell_h + gap
            x += group_w + gap
    else:        # groups are rows: split the height, side by side within each
        row_h = (h - gap * (n_groups - 1)) / n_groups
        y = y0
        for row in groups:
            col_w = (w - gap * (len(row) - 1)) / len(row)
            x = x0
            for _ in row:
                p.drawRoundedRect(int(x), int(y), int(col_w), int(row_h), 2, 2)
                x += col_w + gap
            y += row_h + gap


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

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_grid(p, self._icon_rect(), self.grid_rows, self.columns, self._icon_color())
        p.end()


class LayoutPicker(LayoutButton):
    """One button showing the current panel layout; click it for a menu of all
    layouts. Saves the row space a full strip of layout buttons takes up. Sized like
    Analyze's dropdown icon buttons (Presets): 50 x 34, a 22 px icon, then a small ▾."""

    W, H = 50, 34
    ICON_W, ICON_H = 21, 15  # the grid, about as big as the 22 px icons beside it

    def __init__(self, on_pick, parent=None):
        super().__init__(GRIDS[0][1], GRIDS[0][2], parent)
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
        return QColor(theme.colors()["accent"])

    def _icon_rect(self):
        # same spot as the glyph in Analyze's dropdown buttons: a 36 px icon+caret strip centred
        left = (self.width() - 36) / 2
        return QRectF(left + (22 - self.ICON_W) / 2, (self.height() - self.ICON_H) / 2,
                      self.ICON_W, self.ICON_H)

    def paintEvent(self, event):
        super().paintEvent(event)
        from . import theme
        from .main_window import _material_icon
        p = QPainter(self)
        caret = _material_icon("arrow_drop_down", 16, theme.colors()["tab_text"])
        p.drawPixmap(int((self.width() - 36) / 2 + 20), int((self.height() - 22) / 2 + 3),
                     caret.pixmap(QSize(16, 16)))  # the ▾ exactly as Analyze draws it
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


class GaugesPanel(QFrame):
    """Responsive grid of ValueCards. Cards always fill width (no horizontal
    scroll). Scrolls vertically only when rows overflow."""

    PREFERRED_CARD_W = 140  # target width used to calculate column count
    MAX_COLS = 3            # cap columns for balanced cards
    CARD_CLASS = ValueCard

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value_cards = {}
        self._prev_cols = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(6)
        self.scroll.setWidget(self.inner)
        layout.addWidget(self.scroll)

    def set_channels(self, channels, channel_data):
        self._clear_grid()
        for key, name, unit in channels:
            self.value_cards[key] = self.CARD_CLASS(name, unit, key)
        self._prev_cols = 0
        # Defer reflow so viewport has its final size
        QTimer.singleShot(0, self._reflow)

    def _clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
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
        cols = max(1, min(n, self.MAX_COLS, avail // max(self.PREFERRED_CARD_W, 1)))
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
    """Round dial gauges."""
    PREFERRED_CARD_W = 190
    MAX_COLS = 4
    CARD_CLASS = DialGauge

    def _reflow(self):
        super()._reflow()
        # Dials fill the panel: let rows stretch instead of pinning to the top.
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 1 if r < (len(self.value_cards) + max(self._prev_cols, 1) - 1)
                                    // max(self._prev_cols, 1) else 0)


class BarsPanel(GaugesPanel):
    """Horizontal bar gauges, one column."""
    PREFERRED_CARD_W = 10_000
    MAX_COLS = 1
    CARD_CLASS = BarGauge


class GraphPanel(QFrame):
    """One graph with up to GRAPH_SLOTS channel dropdowns above it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(100)
        self.all_channels = []
        self.colors = {}
        self.curves = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.selectors = []
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        for i in range(GRAPH_SLOTS):
            combo = QComboBox()
            combo.setMinimumWidth(60)
            combo.setToolTip(f"Channel for line {i + 1} on this graph (— for none).")
            combo.currentIndexChanged.connect(lambda _, idx=i: self._channel_changed(idx))
            sel_row.addWidget(combo)
        sel_row.addStretch(1)
        self.selectors = [sel_row.itemAt(i).widget() for i in range(GRAPH_SLOTS)]
        layout.addLayout(sel_row)

        from . import theme
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
        bg = QColor(c["surface"])
        self.legend = self.plot.addLegend(offset=(6, 6), labelTextSize="8pt",
                                          brush=pg.mkBrush(bg.red(), bg.green(), bg.blue(), 200))
        graphstyle.apply(self.plot, legend=self.legend)
        graphstyle.notifier.changed.connect(self._restyle)
        layout.addWidget(self.plot, 1)
        self.scale_mode = "auto"  # set by the Live View page (Scale dropdown)
        self.ranges = {}  # slot -> (lo, hi) seen since the line was added; only grows
        self._fitted = None

    def set_channels(self, channels, colors=None):
        """Update dropdown options. channels = [(key, label)]; colors = {key: colour} (the
        channel list's, so a line has the same colour as its dot)."""
        self.all_channels = channels
        self.colors = colors or {}
        for combo in self.selectors:
            combo.blockSignals(True)
            current = combo.currentData()
            combo.clear()
            combo.addItem("—", None)
            for key, label in channels:
                combo.addItem(label, key)
            if current:
                for i in range(combo.count()):
                    if combo.itemData(i) == current:
                        combo.setCurrentIndex(i)
                        break
            combo.blockSignals(False)
        self._rebuild_curves()

    def _channel_changed(self, slot_idx):
        self._rebuild_curves()

    def _rebuild_curves(self):
        self.plot.clear()
        self.legend.clear()
        self.curves = {}
        self.ranges = {}
        self._fitted = None
        self._labels = {}
        for i, combo in enumerate(self.selectors):
            key = combo.currentData()
            if key and key not in [k for k, _ in self.curves.values()]:
                color = self.colors.get(key) or graphstyle.color(i)
                pen = pg.mkPen(color=color, width=graphstyle.width())
                curve = self.plot.plot([], [], pen=pen, name=combo.currentText())
                self.curves[i] = (key, curve)

    def _restyle(self):
        try:
            graphstyle.apply(self.plot, legend=self.legend)
        except RuntimeError:  # panel already deleted (layout switched)
            return
        width = graphstyle.width()
        for i, (key, curve) in self.curves.items():
            curve.setPen(pg.mkPen(color=self.colors.get(key) or graphstyle.color(i), width=width))
        for i, (_key, curve) in self.curves.items():  # legend swatches/text pick up the new style
            label = self.legend.getLabel(curve)
            if label is not None:
                label.setText(label.text, size=f"{graphstyle.label_pt()}pt")

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
            text = graphscale.label(self.selectors[i].currentText(), rng)
            if self._labels.get(i) != text:
                label = self.legend.getLabel(curve)
                if label is not None:
                    label.setText(text)
                self._labels[i] = text
        self.plot.setXRange(-window, 0, padding=0)


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
            self.panel.setParent(None)
            self.panel.deleteLater()
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
        self._build_layout()
        self._populate_panels()
        self._remember_layout()

    def _section_type_changed(self, slot_index, ptype):
        self.section_types[slot_index] = ptype
        self._populate_panels()
        self._remember_layout()

    # --- dashboards ---

    def dashboard_state(self):
        graphs = {}
        for index, section in enumerate(self.sections):
            if isinstance(section.panel, GraphPanel):
                graphs[str(index)] = [combo.currentData() for combo in section.panel.selectors]
        return {
            "grid": self.current_grid,
            "types": list(self.section_types),
            "channels": sorted(v["id"] for v in self._checked_values()),
            "graphs": graphs,
            "window": self.window_combo.currentData(),
        }

    def apply_dashboard(self, dash, channels=True):
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
            self._build_layout()
            if channels and dash.get("channels") is not None:
                self._build_tree(set(dash["channels"]))  # also fills the panels
            else:
                self._populate_panels()
            for index, keys in (dash.get("graphs") or {}).items():
                index = int(index)
                if index < len(self.sections) and isinstance(self.sections[index].panel, GraphPanel):
                    for combo, key in zip(self.sections[index].panel.selectors, keys):
                        found = combo.findData(key) if key else 0
                        combo.setCurrentIndex(max(found, 0))
            window = self.window_combo.findData(dash.get("window"))
            if window >= 0:
                self.window_combo.setCurrentIndex(window)
        finally:
            self._applying = False
        self._remember_layout()

    def _remember_layout(self):
        """The last layout per module comes back next time."""
        if getattr(self, "_applying", False) or not self.state.family or not self.catalog:
            return
        self.state.settings.set(f"liveview_layout:{self.state.family['name']}", self.dashboard_state())

    def _build_layout(self):
        """Tear down and rebuild the panel area based on the selected grid."""
        self.sections = []

        self.layout_picker.set_current(self.current_grid)

        # Remove old content
        while self.panel_container_layout.count():
            item = self.panel_container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

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

    def refresh(self):
        family = self.state.family
        module = family["module"] if family and family.get("can_log") else None
        if module != self.catalog_module and not self.running and not self.mirroring:
            self.catalog_module = module
            self.catalog = []
            self.raw_groups = {}
            self._build_tree()
            if module is not None:
                self.state.offline("catalog", {"module": module}, self._catalog_loaded,
                                   lambda e: self.banner.show_message(self.error_text(e), "error"))
        self.start_button.setEnabled(bool(module is not None and self.state.adapter)
                                     and (not self.state.busy or self.running))
        # channels can't change while running (the module is reading those groups), but the list
        # stays live: it shows the values
        live = self.running or self.mirroring
        self.channels.setEnabled(not live)
        if live:
            self.channels.close_picker()
        self.presets.setEnabled(not live and self.state.family is not None)
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
        layout = self.state.settings.get(f"liveview_layout:{self.state.family['name']}") \
            if self.state.family else None
        if layout:
            self.apply_dashboard(layout, channels=False)
        self._build_tree(set(saved))
        if layout:
            self.apply_dashboard({"graphs": layout.get("graphs")}, channels=False)
        if not self.catalog:
            self.summary.setText("No named values available for this module.")

    def _settings_key(self):
        return f"liveview:{self.state.family['name']}" if self.state.family else None

    def _conv(self, value):
        return units.conversion(value.get("unit", ""), self.state.settings)

    def showEvent(self, event):
        super().showEvent(event)
        # Units may have been switched on the Settings page.
        if getattr(self, "_units_built", None) not in (None, units.system(self.state.settings)) \
                and not self.running and self.catalog:
            self._build_tree({v["id"] for v in self._checked_values()})

    def _build_tree(self, checked=()):
        """Every channel (catalog + math) as a row, known by its key; the ticked ones show,
        each with its colour, live value and range bar (like Analyze's list)."""
        self._units_built = units.system(self.state.settings)
        limits = self._limits()
        self.tree.blockSignals(True)
        self.tree.clear()
        order = []
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
        return f"liveview_raw:{self.state.family['name']}" if self.state.family else None

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
        return f"alarms:{self.state.family['name']}" if self.state.family else None

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
        if any(limits[key].get("beep", True) for key in new):
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
            gp.set_channels(channels_tuples, self.channel_data)
        for gp in self.graph_panels:
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
        family = self.state.family or {}
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
