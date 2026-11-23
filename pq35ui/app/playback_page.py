"""Data log playback: load a CSV recorded by the logger, scrub through it
with an overview timeline, and overlay channels on pyqtgraph plots."""
import csv
import sys
import warnings
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QMenu, QTabWidget,
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from . import ai, graphscale, graphstyle, log_export, log_import, mathchannels, recent_logs, units
from . import analyze_tools, legend_strip, smart_presets, theme
from .heatmap import HeatmapView
from .split_view import SplitView
from .channel_list import COLOR_ROLE, FRAC_ROLE, HIDDEN_ROLE, ChannelPicker
from .channel_sidebar import ChannelSidebar
from .storage import datalogs_dir
from .widgets import Banner, ElidedLabel, muted, pair

GRAPH_SLOTS = 6
STATS_MODES = (("view", "What's shown"), ("all", "Whole log"), ("sel", "Selection"))
# Sidebar value columns you can show or hide: (key, header, tooltip). Column 0 is the channel.
SIDEBAR_COLUMNS = (
    ("now", "Now", "Value at the cursor"),
    ("cmp", "Compared", "Compared log's value at the cursor"),
    ("delta", "Δ", "Now minus compared"),
    ("min", "Min", "Lowest value (in the selected range, if one is set)"),
    ("max", "Max", "Highest value (in the selected range, if one is set)"),
    ("avg", "Avg", "Average value (in the selected range, if one is set)"),
)
SIDEBAR_DEFAULT = ("now",)
COL = {key: i + 1 for i, (key, _h, _t) in enumerate(SIDEBAR_COLUMNS)}
ROLE = Qt.ItemDataRole.UserRole
# Axis choices -> graphscale mode: (key, menu label, graphscale mode)
AXIS_MODES = (
    ("shared", "Shared: one y axis, real units (click a line to give it its own axis)", "real"),
    ("independent", "Independent: each channel fits its own range", "each"),
)
AXIS_SCALE = {key: mode for key, _label, mode in AXIS_MODES}
RAW_COL_MIN = 150  # px: Raw columns at least this wide, so a useful part of the name shows


from .units import fmt as _fmt  # one number style on every tab
from .toolbar import ToolIcon


def _parse_csv(path: Path):
    """Read a Datazap-style CSV. Returns (headers, time_array, data_dict)."""
    with open(path, "r", encoding="utf-8") as f:
        headers = next(csv.reader([f.readline()]), None)  # readline, not iteration: keeps f.tell() usable
        if not headers or not log_export.is_log_header(headers[0]):
            raise ValueError("Not a datalog this app can read (the first column must be the time).")
        headers = headers[:1] + [log_export.current_header(h) for h in headers[1:]]  # older names
        width = len(headers)
        # Fast path (numpy's C reader, ~3.5x quicker on long logs): every cell a plain number,
        # every row as wide as the header. Anything else (blank cells, MM:SS times, a units
        # row, quoting, ragged rows) falls through to the row-by-row reader below.
        start = f.tell()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # "input contained no data" on a header-only file
                data = np.loadtxt(f, delimiter=",", dtype=float, ndmin=2, comments=None)
        except ValueError:
            data = None
        if data is not None and data.shape[0] and data.shape[1] == width:
            columns = {name: data[:, i] for i, name in enumerate(headers[1:], 1)}
            return headers[1:], data[:, 0], _aligned(path, headers[1:], data[:, 0], columns)
        f.seek(start)
        reader = csv.reader(f)
        rows = []
        for row in reader:
            try:  # time as seconds (Datazap/MegaLog/generic) or MM:SS.mmm (Integrated Engineering)
                values = [log_export.parse_time(row[0])] + [
                    float(v) if v.strip() else float("nan") for v in row[1:width]]
            except (ValueError, IndexError):
                continue  # header-like rows, e.g. a units row
            values += [float("nan")] * (width - len(values))
            rows.append(values)
    if not rows:
        raise ValueError("CSV has no data rows.")
    data = np.array(rows)
    times = data[:, 0]
    columns = {}
    for i, name in enumerate(headers[1:], 1):
        columns[name] = data[:, i] if i < data.shape[1] else np.zeros(len(times))
    return headers[1:], times, _aligned(path, headers[1:], times, columns)


def _aligned(path, names, times, columns):
    """Each channel at its real read times when the recording has a log_info companion."""
    from . import log_info
    columns, _n = log_info.align(names, times, columns, log_info.load(path), log_export.current_header)
    return columns


class _TitleLabel(ElidedLabel):
    """The log's name in the graph tabs' corner; click it for the file's details. QTabWidget
    sizes its corner by sizeHint, so the hint stays within the width left beside the tabs
    (set via setMaximumWidth)."""
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.fullText():
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(hint.width(), self.maximumWidth()), hint.height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


class PlaybackPage(QWidget):
    title = "Analyze"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        self.headers = []
        self.times = None
        self.columns = {}
        self.csv_path = None      # the Standard log on screen (an imported log's converted copy)
        self.source_path = None   # the file that was opened (shown, revealed, named after)
        self.source_format = None # e.g. "VCDS" when it came from another tool; None for ours
        self._ranges = {}
        self._live = None  # following a recording in progress (follow): path, offset, raw data

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(6)
        outer.addWidget(self.splitter, 1)

        # ── Left sidebar: the channels list (channel_sidebar, shared with the Logger) ──
        self.select_button = QPushButton("Select channels")
        self.select_button.setToolTip("Choose which channels are on the graph")
        from .widgets import pulldown
        pulldown(self.select_button)  # text + chevron, as every Select channels
        self.sidebar = ChannelSidebar(
            state, "playback", SIDEBAR_COLUMNS, self.select_button,
            empty_text="No channels on the graph.\nUse Select channels above to add some.",
            columns_tip="Choose which values the list shows: Now, Compared, Δ, Min, Max, Avg",
            bars_tip="Show or hide the range bars under each channel (where the value at the "
                     "cursor sits between the log's lowest and highest)",
            extra_menu=self._columns_menu_extra)
        self.sidebar.columns_changed.connect(self._columns_changed)
        self.tree = self.sidebar.tree
        self.tree.itemChanged.connect(lambda *_: self._rebuild_curves())
        self.tree.reordered.connect(self._rebuild_curves)
        self.tree.itemSelectionChanged.connect(self._highlight_selected)
        self.tree.customContextMenuRequested.connect(self._channel_menu)
        self.picker = ChannelPicker(self.tree, self._rebuild_curves, self, describe=self._describe_channel)
        self._known = None  # channel name (lower case) -> catalog entry, from every loggable module
        self.select_button.clicked.connect(lambda: self.picker.open_for(self.select_button))

        # ── Right content: controls + graph ──
        right = QWidget()
        right.setObjectName("page")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 16, 16, 16)
        rl.setSpacing(10)

        # Top controls. Left: sidebar. Right: legend, timeline, math, presets, axis, more.
        # (Open and compare sit on the view-tab row, right of the log's name.)
        ctrl = self.ctrl_row = QHBoxLayout()
        ctrl.setSpacing(8)  # one gap for every toolbar row (Logger, Live View too)
        self.open_button = ToolIcon("file_open", "Open a log: a recent recording, or a file "
                                             "from this app or another tool (the logs Datazap takes: "
                                             "COBB, MHD, VCDS, ME7Logger, Torque, MegaLogViewer, "
                                             "Integrated Engineering …)", dropdown=True)
        self.open_button.clicked.connect(self._open_menu)
        # speed_4_progress, by codepoint: the glyph is in the font but its name ligature draws junk
        self.sidebar_button = ToolIcon("\U000ffe94", "Show or hide the channels "
                                                "sidebar", checkable=True)
        self._right_layout = rl  # its left margin follows the sidebar (ChannelSidebar._shown)
        ctrl.addWidget(self.sidebar_button)
        # It and Select channels are a pair at the top left (ChannelSidebar.install): the column's
        # first row while it shows, the front of this row while it's hidden.
        self.compare_button = ToolIcon("compare", "Compare logs: overlay a second log on the "
                                                "graph, or compare several logs with AI", dropdown=True)
        self.compare_button.clicked.connect(
            lambda: analyze_tools.popup_menu(self._compare_menu(), self.compare_button))
        ctrl.addStretch(1)

        self.legend_button = ToolIcon("list", "Show or hide the legend under the graph "
                                               "(click a channel there to hide or show its line)",
                                               checkable=True)
        self.legend_button.setChecked(bool(state.settings.get("playback:legend", True)))
        self.legend_button.toggled.connect(self._legend_toggled)
        ctrl.addWidget(self.legend_button)
        self.timeline_button = ToolIcon("acute", "Show or hide the timeline under "
                                                 "the graph (the whole log in miniature)", checkable=True)
        self.timeline_button.setChecked(bool(state.settings.get("playback:timeline", True)))
        self.timeline_button.toggled.connect(self._timeline_toggled)
        ctrl.addWidget(self.timeline_button)
        self.math_button = ToolIcon("function", "Math channels: add channels computed "
                                             "from formulas")
        self.math_button.clicked.connect(self._edit_math)
        ctrl.addWidget(self.math_button)
        self.presets_button = ToolIcon("bookmark", "Presets: put a ready-made set of "
                                                "channels on the graph, or save your own",
                                                dropdown=True)
        self.presets_button.clicked.connect(lambda: analyze_tools.popup_menu(self._presets_menu(), self.presets_button))
        ctrl.addWidget(self.presets_button)
        self.axis_button = QPushButton()
        self.axis_button.setToolTip("Shared: every channel on one y axis in its real units.\n"
                                    "Independent: every channel stretched to its own range, so small "
                                    "and large values both fill the graph.\n"
                                    "In Shared, click a line to give that channel its own y axis.\n"
                                    "Applies to the Single graph and to Split rows holding several channels.")
        self.axis_button.clicked.connect(lambda: analyze_tools.popup_menu(self._axis_menu(), self.axis_button))
        ctrl.addWidget(self.axis_button)
        self.more_button = ToolIcon("more_vert", "Log details, edit, reset view, download")
        self.more_button.clicked.connect(lambda: analyze_tools.popup_menu(self._more_menu(), self.more_button))
        self._more_menu_width = 0
        ctrl.addWidget(self.more_button)
        rl.addLayout(ctrl)
        self._update_axis_button()

        # Settings without a control of their own: kept as plain widgets (not shown) so the
        # rest of the page reads them as before; menus switch them.
        self.stats_mode = QComboBox()
        for key, label in STATS_MODES:
            self.stats_mode.addItem(label, key)
        saved_mode = state.settings.get("playback:stats_mode", "view")
        self.stats_mode.setCurrentIndex(max(self.stats_mode.findData(saved_mode), 0))
        self.stats_mode.currentIndexChanged.connect(lambda _: self._stats_mode_changed())
        self.range_label = QLabel("")
        self.follow_check = QCheckBox("Follow mouse")
        self.follow_check.setChecked(bool(state.settings.get("playback:follow_mouse", True)))
        self.follow_check.toggled.connect(self._follow_toggled)

        # Compare row: only while a second log is overlaid
        self.compare_row = QWidget()
        tools = QHBoxLayout(self.compare_row)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-100000, 100000)
        self.offset.setDecimals(2)
        self.offset.setSuffix(" s")
        self.offset.setToolTip("Move the compared log in time to line up the runs")
        self.offset.valueChanged.connect(lambda _: self._rebuild_curves())
        tools.addWidget(pair(muted("Shift"), self.offset))
        self.align_button = QPushButton("Auto-align")
        self.align_button.setToolTip("Line up both logs where the first ticked channel first "
                                     "crosses halfway between its min and max (e.g. throttle, rpm)")
        self.align_button.clicked.connect(self._auto_align)
        tools.addWidget(self.align_button)
        self.clear_compare_button = QPushButton("Clear")
        self.clear_compare_button.setToolTip("Stop comparing")
        self.clear_compare_button.clicked.connect(self._clear_compare)
        tools.addWidget(self.clear_compare_button)
        tools.addStretch(1)
        rl.addWidget(self.compare_row)
        self.compare = None
        from PySide6.QtCore import QTimer
        self._stats_timer = QTimer(self)  # recompute stats/heatmap/scatter once dragging pauses
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(60)
        self._stats_timer.timeout.connect(self._region_moved)
        self._set_compare_enabled()

        self.banner = Banner()
        rl.addWidget(self.banner)

        # Graph
        from . import theme
        c = theme.colors()
        self.plot = pg.PlotWidget()
        self.plot.setBackground(c["surface"])
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "Time (s)")
        self.plot.setMinimumHeight(120)
        self.plot.setMenuEnabled(False)  # our own right-click menu instead of pyqtgraph's
        # Scrolling works as pyqtgraph's (over the graph: both; over an axis: that one only);
        # double-clicking the value axis puts the values back.
        graphscale.axis_zoom(self.plot.getPlotItem().vb, self._reset_values)
        graphscale.value_axis(self.plot.getPlotItem().getAxis("left"))
        # Same fixed left-axis width as the Split rows, so the plot area starts at the same x
        # in both views (and the timeline under it doesn't jump when switching).
        from .split_view import AXIS_W
        self.plot.getPlotItem().getAxis("left").setWidth(AXIS_W)
        graphstyle.cache_axes(self.plot.getPlotItem())  # axes + grid only redraw on zoom
        graphstyle.inside_labels(self.plot.getPlotItem())  # Mac: numbers inside, axes at the edges
        # ⚙ in the graph's top-right corner: the same menu as right-click, so it can be found.
        # Faint until the mouse is over the graph, so it doesn't sit on top of the lines.
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self.graph_gear = QPushButton(self.plot)
        self.graph_gear.setObjectName("graphGear")
        self.graph_gear.setToolTip("Graph settings: channels, reset view, axis, legend, timeline, "
                                   "export… (also on right-click)")
        self.graph_gear.setFixedSize(30, 30)
        self.graph_gear.setIconSize(QSize(20, 20))
        self.graph_gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.graph_gear.clicked.connect(lambda: self._graph_menu().exec(
            self.graph_gear.mapToGlobal(self.graph_gear.rect().bottomRight())
            - QPoint(self._graph_menu_width(), -4)))
        self._gear_fade = QGraphicsOpacityEffect(self.graph_gear)
        self._gear_fade.setOpacity(0.45)
        self.graph_gear.setGraphicsEffect(self._gear_fade)
        # Under the ⚙: fit the whole log back in view (replaces pyqtgraph's little "A" button).
        self.graph_fit = QPushButton(self.plot)
        self.graph_fit.setObjectName("graphGear")
        self.graph_fit.setToolTip("Fit: the whole log across, every line back in view")
        self.graph_fit.setFixedSize(30, 30)
        self.graph_fit.setIconSize(QSize(20, 20))
        self.graph_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.graph_fit.clicked.connect(self._reset_view)
        self._fit_fade = QGraphicsOpacityEffect(self.graph_fit)
        self._fit_fade.setOpacity(0.45)
        self.graph_fit.setGraphicsEffect(self._fit_fade)
        self.plot.hideButtons()
        # Room above and below auto-scaled lines so a peak isn't drawn on the plot's edge.
        self.plot.getPlotItem().vb.setDefaultPadding(0.06)
        self._restyle_gear()
        theme.on_apply(self._restyle_gear)
        self.plot.installEventFilter(self)  # keeps it in the corner; brightens it on hover
        graphstyle.apply(self.plot, state.settings)
        # Single tab: the graph with the slim timeline under it, as one panel
        self.time_page = QWidget()
        self.time_layout = QVBoxLayout(self.time_page)
        self.time_layout.setContentsMargins(0, 0, 0, 0)
        self.time_layout.setSpacing(0)
        self.time_layout.addWidget(self.plot, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("viewTabs")  # its buttons: QPushButton#viewTab in theme.py
        self.tabs.setIconSize(QSize(20, 20))
        self.heatmap = HeatmapView(state.settings, lambda: self._update_analysis(), self)
        # (key, widget, label, Material icon). Code asks which view is showing by key, not position.
        self._views = (
            ("single", self.time_page, "Single", "show_chart"),
            ("split", self._build_split(), "Split", "view_agenda"),
            ("heatmap", self._scroll_sideways(self.heatmap), "Heatmap", "grid_on"),
            ("scatter", self._build_scatter(c), "Scatter", "scatter_plot"),
            ("raw", self._build_raw(), "Raw", "description"),
        )
        for _key, widget, label, _glyph in self._views:
            self.tabs.addTab(widget, label)
        # The views are picked with buttons styled like the app's top-bar tabs, always showing
        # their icon and name (no hover animation); the tab bar itself is hidden.
        self.tabs.tabBar().hide()

        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(2)
        self.view_buttons = []
        from .widgets import segment_button
        for i, (_key, _w, label, _glyph) in enumerate(self._views):
            b = segment_button(label)  # no tooltip: like the top bar's tabs
            b.clicked.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))
            view_row.addWidget(b)
            self.view_buttons.append(b)
        self.tabs.currentChanged.connect(lambda _: self._tint_tabs())
        self._tint_tabs()
        theme.on_apply(self._tint_tabs)
        self.tabs.currentChanged.connect(lambda _: self._update_analysis())
        self.tabs.currentChanged.connect(lambda _: self._view_changed())
        # The log's name, length and car sit at the right of the tab row, like a document heading.
        self.file_label = _TitleLabel("")
        self.file_label.setObjectName("muted")
        self.file_label.setMinimumWidth(0)
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.file_label.setContentsMargins(0, 2, 0, 0)  # letters line up with the tab text
        self.file_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_label.clicked.connect(lambda: self._show_file_details())
        view_row.addSpacing(12)
        view_row.addWidget(self.file_label, 1)  # takes the rest of the row; long names get …
        view_row.addSpacing(6)
        view_row.addWidget(self.open_button)
        view_row.addSpacing(6)
        view_row.addWidget(self.compare_button)
        rl.addLayout(view_row)
        # Smart presets (like Datazap's): chips of ready-made layouts for this log, above the graph.
        self.smart = smart_presets.Generator(state.settings, self)
        self.smart_bar = smart_presets.SmartBar(self, self.smart)
        self.smart_bar.apply.connect(self._apply_smart)
        self.smart_bar.hide()
        rl.addWidget(self.smart_bar)
        rl.addWidget(self.tabs, 1)
        if theme.is_mac():
            self._mac_toolbar(rl, ctrl, view_row)

        # Overview timeline: the whole log in miniature. Drag the band to zoom the graph,
        # drag its edges or scroll to zoom, click to move the cursor, double-click for all.
        self.overview = pg.PlotWidget()
        self.overview.setBackground(c["surface"])
        self.overview.setFixedHeight(legend_strip.BAND_H)  # as tall as the legend (fit_to_font)
        self.overview.hideAxis("left")
        self.overview.hideAxis("bottom")
        self.overview.hideButtons()
        self.overview.setMenuEnabled(False)
        self.overview.setMouseEnabled(x=False, y=False)
        # No padding around the plot area, so the zoom band and selection reach the strip's edges.
        ov_item = self.overview.getPlotItem()
        ov_item.setContentsMargins(0, 0, 0, 0)
        ov_item.layout.setContentsMargins(0, 0, 0, 0)
        ov_item.layout.setSpacing(0)
        self.overview.setContentsMargins(0, 0, 0, 0)
        self.overview.setYRange(-4, 104, padding=0)
        self.overview.setToolTip("Whole log. Drag the band to zoom the graph, drag its edges or "
                                 "scroll to zoom in or out, click to move the cursor, double-click "
                                 "to show it all.")
        self.zoom_band = pg.LinearRegionItem(brush=pg.mkBrush(QColor(c["accent"]).red(),
                                             QColor(c["accent"]).green(), QColor(c["accent"]).blue(), 45))
        self.zoom_band.setZValue(10)
        self.zoom_band.sigRegionChanged.connect(self._zoom_band_moved)
        self.ov_cursor = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(c["text"], width=1.5))
        self.ov_cursor.setZValue(20)
        self.overview.scene().sigMouseClicked.connect(self._overview_clicked)
        ov_item.vb.wheelEvent = self._overview_wheel  # wheel over the timeline zooms the graph
        self._syncing_zoom = False
        # Under the graph: the timeline right below the time axis, trimmed to line up with the
        # graph's plot area so it reads as part of the graph, then the legend (channel chips) at
        # the bottom. Both move with the view between Single and Split; each has its own
        # show/hide toolbar button.
        self.hidden = set()  # channels ticked but with their line hidden (legend click)
        self.legend = legend_strip.LegendStrip(self)
        self.legend.setVisible(self.legend_button.isChecked())
        self.bottom_band = QWidget()
        bl = QVBoxLayout(self.bottom_band)
        bl.setContentsMargins(0, 8 if theme.is_mac() else 2, 0, 0)  # Mac: two cards, a clear gap
        bl.setSpacing(8)
        self.ov_box = QWidget()  # its side margins line the timeline up with the plot area
        ob = QHBoxLayout(self.ov_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.addWidget(self.overview)
        bl.addWidget(self.ov_box)
        bl.addWidget(self.legend)
        self.time_layout.addWidget(self.bottom_band)
        self.bottom_band.installEventFilter(self)  # re-align the timeline on resize
        self.ov_box.setVisible(self.timeline_button.isChecked())
        self.cursor_idx = 0

        self.sidebar.install(self.splitter, right, self._right_layout, self.ctrl_row, self.sidebar_button,
                             toggle_moves=not theme.is_mac())  # Mac: the toggle is a right-hand icon
        if theme.is_mac():  # mac_shell rule 6: the view segments go icon-only when row 1 doesn't fit
            from .mac_shell import fit_row
            self._row_fit = fit_row(self.splitter.widget(1), self.ctrl_row, self.view_buttons)

        # Cursor line on graph
        self.cursor_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(c["text"], width=1.5))
        self.cursor_line.setZValue(20)
        self.cursor_line.sigPositionChanged.connect(self._cursor_moved)
        # While it follows the mouse the line is always under the pointer; if it could be grabbed it
        # would take every drag meant for the selection band, so it's only draggable with Follow off.
        self.cursor_line.setMovable(not self.follow_check.isChecked())
        self.plot.addItem(self.cursor_line)
        self.cursor_line.hide()
        # Values at the cursor, drawn on the graph: a dot where each line crosses it and a box of readings.
        self.cursor_dots = pg.ScatterPlotItem(size=9, pxMode=True)
        self.cursor_dots.setZValue(21)
        self.readout = graphstyle.rounded_text_item(anchor=(0, 0), fill=pg.mkBrush(QColor(c["surface"]).red(),
                                   QColor(c["surface"]).green(), QColor(c["surface"]).blue(), 225),
                                   border=pg.mkPen(c.get("border", "#555555")))
        self.readout.setZValue(22)
        self._readout_items = (self.cursor_dots, self.readout)
        self.readout.hide()
        self._add_cursor_items()
        # Stats selection: a blue band on the graph, mirrored (read-only) on the overview.
        sel = QColor(c["info"])
        self.sel_band = pg.LinearRegionItem(brush=pg.mkBrush(sel.red(), sel.green(), sel.blue(), 40),
                                            pen=pg.mkPen(sel, width=1.5))
        self.sel_band.setZValue(-10)
        self.sel_band.sigRegionChanged.connect(self._selection_moved)
        self.ov_sel = pg.LinearRegionItem(movable=False, brush=pg.mkBrush(sel.red(), sel.green(), sel.blue(), 70),
                                          pen=pg.mkPen(sel, width=1))
        self.ov_sel.setZValue(5)
        self.overview.setProperty("mac_inset", "manual")  # Mac: padding set by _align_overview
        graphstyle.theme_plot(self.overview)
        self._retheme_overlays()
        theme.on_apply(self._retheme_overlays)
        self.sel_band.hide()
        self.ov_sel.hide()
        self._sel_drag_start = None
        vb = self.plot.getPlotItem().vb
        default_drag = vb.mouseDragEvent

        def drag(ev, axis=None):  # Shift-drag across the graph draws a selection; plain drag pans
            if ev.button() == Qt.MouseButton.LeftButton and (
                    ev.modifiers() & Qt.KeyboardModifier.ShiftModifier or self._sel_drag_start is not None):
                ev.accept()
                x = float(vb.mapSceneToView(ev.scenePos()).x())
                if ev.isStart():
                    self._sel_drag_start = float(vb.mapSceneToView(ev.buttonDownScenePos()).x())
                    if self.stats_mode.currentData() != "sel":
                        self.stats_mode.setCurrentIndex(self.stats_mode.findData("sel"))
                if self._sel_drag_start is not None:
                    a, b = sorted((self._sel_drag_start, x))
                    self.sel_band.setRegion((a, b))
                if ev.isFinish():
                    self._sel_drag_start = None
                return
            default_drag(ev, axis=axis)
        vb.mouseDragEvent = drag
        self.plot.getPlotItem().vb.sigRangeChanged.connect(lambda *_: self._place_readout())
        self._hover_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=60,
                                           slot=self._mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._mouse_clicked)
        self.plot.getPlotItem().vb.sigXRangeChanged.connect(lambda *_: self._graph_range_changed())
        self.plot.getPlotItem().vb.sigResized.connect(lambda *_: self._align_overview_soon())
        self.plot.getPlotItem().vb.sigXRangeChanged.connect(lambda *_: self._split_if_shown("sync_from_page"))
        # Arrow keys step the cursor one sample (Shift: ten) once the graph has focus.
        for w in (self.plot, self.overview, self.split.graphs):
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(self)

        self.curves = {}
        self.curve_data = {}  # name -> (scaled y, colour) of the drawn line, for the dots
        self._fitted = False
        self.own_axis = set()  # Shared axis: channels clicked to get their own y axis
        self._own_axes = []
        graphstyle.notifier.changed.connect(self._restyle)
        state.open_in_playback.connect(lambda path: self.load_csv(Path(path)))
        # Logger → Watch in Analyze: follow the recording live; "" = it ended
        state.follow_log.connect(lambda path: self.follow(Path(path)) if path else self.end_follow())

    def refresh(self):
        pass  # no hardware dependency

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "smart_bar"):
            self._sync_smart_bar()  # Settings → Data may have switched it
        system = units.system(self.state.settings)
        if self.csv_path is not None and getattr(self, "_units_loaded", system) != system:
            compare = self.compare["path"] if self.compare else None
            checked = {log_export.split_header(n)[0] for n in self._checked_channels()}
            self.load_csv(self.csv_path)
            self._restore_checked(lambda name: log_export.split_header(name)[0] in checked)
            if compare:
                self.load_compare(compare)
        self._units_loaded = system

    def _load_known(self):
        """Ask the worker once for each loggable module's catalog, so a log's channels can be
        described (Select channels → the box under the list)."""
        self._known = {}

        def got(values):
            for v in values or ():
                self._known.setdefault(v["name"].lower(), v)

        for family in self.state.families or ():
            if family.get("can_log") and family.get("module"):
                try:
                    self.state.offline("catalog", {"module": family["module"], "all": True}, got,
                                       lambda _e: None)  # every channel: logs from any car
                except Exception:
                    pass

    def _describe_channel(self, key):
        """The value under the mouse in Select channels: the ECU's description when the name is
        a known value, else what this log says about it."""
        from html import escape
        from . import channel_info
        if self._known is None:
            self._load_known()
        name, unit = log_export.split_header(key or "")
        if key.startswith("\u0192 "):
            return f"<b>{escape(key[2:])}</b><br>A math channel (⚙ → Math channels…)."
        value = (self._known or {}).get(name.lower())
        if value:
            return channel_info.tooltip(value)
        rng = self._ranges.get(key)
        span = f"<br>In this log: {rng[0]:.4g} to {rng[1]:.4g}" if rng else ""
        where = f" (log format: {escape(self.source_format)})" if self.source_format else ""
        return (f"<b>{escape(name)}</b>" + (f" · {escape(unit)}" if unit else "")
                + f"<br>No description for this channel{where}." + span)

    def _open_menu(self):
        """Open ▾: Open file…, then the recent recordings (✓ = exported)."""
        T = analyze_tools
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        T.menu_action(menu, "Open file…", "file_open", self._open_file,
                      tip="A log from this app or another tool")
        items = recent_logs.entries(limit=recent_logs.KEEP)
        T.menu_header(menu, "Recent logs")
        if not items:
            menu.addAction("No recordings yet").setEnabled(False)
        for path, entry in items:
            act = T.menu_action(menu, f"{path.stem}   {recent_logs.describe(entry)}",
                                "speed" if entry.get("mode") == "dyno" else "description",
                                lambda _=False, p=path: self.load_csv(p))
            act.setToolTip(str(path))
        analyze_tools.popup_menu(menu, self.open_button)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open datalog", str(datalogs_dir()), log_import.OPEN_FILTER)
        if not path:
            return
        self.load_csv(Path(path))

    def load_csv(self, path: Path):
        path = Path(path)
        if self._live and path != self._live["path"]:
            self._stop_follow()  # another log opened: stop following the recording
        reload = self.csv_path is not None and path == self.csv_path
        try:
            native, fmt = (path, self.source_format) if reload else log_import.native_path(path)
            self.headers, self.times, self.columns = _parse_csv(native)
        except Exception as e:
            self.banner.show_message(f"Couldn't open {path.name}: {e}", "error")
            return
        if not reload:
            self.source_path, self.source_format = path, fmt
        path = native
        self._add_math_columns()
        self.headers, self.columns = self._to_display_units(self.headers, self.columns)
        # The lines drawn are the previous log's until _rebuild_curves below; forget them so a
        # cursor move in between doesn't look up channels this log doesn't have.
        self.curves = {}
        self.curve_data = {}
        self.csv_path = path
        self._update_title()
        if self.source_format and not reload:
            self.banner.show_message(f"Converted from {self.source_format} to the Standard format "
                                     "(the original file isn't changed).", "info")
        else:
            self.banner.hide()

        # Populate channel tree with stats
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.log_order = list(self.headers)
        self._ranges = {}
        with self.tree.bulk():
            for name in self.headers:
                self.tree.new_item(name)
                col = self.columns[name]
                valid = col[~np.isnan(col)]
                self._ranges[name] = (float(valid.min()), float(valid.max())) if len(valid) else None
        self.tree.blockSignals(False)
        self._update_stats()
        self._fill_analysis_combos()

        # Check first few channels by default (signals blocked: the graphs are drawn once, below,
        # not once per tick)
        self.tree.blockSignals(True)
        for i in range(min(3, self.tree.topLevelItemCount())):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False)

        self.cursor_idx = 0

        self.cursor_line.show()
        self.cursor_line.setValue(float(self.times[0]))  # Integrated Engineering logs start part-way (e.g. 9.78 s)
        self._set_compare_enabled()
        t0, t1 = float(self.times[0]), float(self.times[-1])
        self.sel_band.setBounds((t0, t1))
        self.sel_band.setRegion((t0 + (t1 - t0) * 0.4, t0 + (t1 - t0) * 0.6))
        self._rebuild_curves()
        self._show_all()
        self._stats_mode_changed(save=False)
        self.smart.load(list(self.headers))
        self._sync_smart_bar()

    # --- following a recording live (Logger → Watch in Analyze) ---

    LIVE_MS = 1000  # how often a followed recording is read for new rows

    def follow(self, path):
        """Open the Logger's recording in progress and keep adding the rows it writes: the graph
        grows, and while the view is at the end it moves along with the newest data."""
        from PySide6.QtCore import QTimer
        path = Path(path)
        if self._live and self._live["path"] == path:
            return
        self._stop_follow()
        if not hasattr(self, "_live_timer"):
            self._live_timer = QTimer(self)
            self._live_timer.setInterval(self.LIVE_MS)
            self._live_timer.timeout.connect(self._live_tick)
        self._live = {"path": path, "started": False, "ended": False}
        if not self._live_open():
            self.banner.show_message("Live: waiting for the recording's first rows…", "info",
                                     actions={"Stop recording": self.state.stop_logging.emit,
                                              "Back to Logger": lambda: self.state.open_tab.emit("logger")})
        self._live_timer.start()

    def end_follow(self):
        """The Logger stopped: read the last rows, then the log is an ordinary one."""
        if not self._live:
            return
        self._live["ended"] = True
        self._live_tick()

    def _stop_follow(self):
        if getattr(self, "_live_timer", None):
            self._live_timer.stop()
        self._live = None

    def _live_open(self):
        """First read of a followed recording (False while it has no rows yet)."""
        live = self._live
        try:
            headers, times, cols = _parse_csv(live["path"])
            with open(live["path"], "rb") as f:  # where the next unread row starts
                f.readline()
                for _ in range(len(times)):
                    f.readline()
                live["pos"] = f.tell()
        except (OSError, ValueError):
            return False
        live.update(started=True, headers=headers, times=times, cols=dict(cols))
        self.load_csv(live["path"])
        if self.times is None:
            return False
        self.cursor_idx = len(self.times) - 1  # the cursor rides the newest row
        self.cursor_line.setValue(float(self.times[-1]))
        self._live_banner()
        return True

    def _live_banner(self):
        self.banner.show_message("● Live: following the recording as it's written. Scroll back to "
                                 "look around; go back to the end to follow again.", "info",
                                 actions={"Stop recording": self.state.stop_logging.emit,
                                          "Back to Logger": lambda: self.state.open_tab.emit("logger")})

    def _live_tick(self):
        live = self._live
        if not live:
            return
        if not live["started"]:
            if not self._live_open() and live["ended"]:
                self._stop_follow()
                self.banner.show_message("The recording ended before any rows were written.", "warning")
            return
        rows = self._live_read()
        if rows is not None:
            self._live_append(rows)
        if live["ended"]:
            self._live_align()
            self._stop_follow()
            self.banner.show_message("Recording finished: this is the whole log.", "info")

    def _live_align(self):
        """The recording just stopped and its log_info companion exists: put each channel at its
        real read times, as opening the log would, without moving the view."""
        live = self._live
        cols = _aligned(live["path"], live["headers"], live["times"], live["cols"])
        if all(cols[n] is live["cols"][n] for n in live["headers"]):
            return
        self.headers, self.times, self.columns = list(live["headers"]), live["times"], dict(cols)
        self._add_math_columns()
        self.headers, self.columns = self._to_display_units(self.headers, self.columns)
        for name in self.headers:
            col = self.columns[name]
            valid = col[~np.isnan(col)]
            self._ranges[name] = (float(valid.min()), float(valid.max())) if len(valid) else None
        if not self._refresh_curves_in_place():
            self._rebuild_curves()

    def _live_read(self):
        """The whole rows added since the last read, as an array (None: nothing new)."""
        live = self._live
        try:
            with open(live["path"], "rb") as f:
                f.seek(live["pos"])
                chunk = f.read()
        except OSError:
            return None
        end = chunk.rfind(b"\n")
        if end < 0:
            return None
        live["pos"] += end + 1
        width = len(live["headers"]) + 1
        rows = []
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            cells = line.split(",")
            if len(cells) != width:
                continue
            try:
                rows.append([float(c) for c in cells])
            except ValueError:
                continue
        return np.array(rows) if rows else None

    def _live_append(self, rows):
        live = self._live
        vb = self.plot.getPlotItem().vb
        a, b = vb.viewRange()[0]
        t0, old_end = float(self.times[0]), float(self.times[-1])
        slack = max(old_end - t0, 1.0) * 0.02
        at_end = b >= old_end - slack           # the view shows the newest data: keep following
        showing_all = a <= t0 + slack
        cursor_at_end = self.cursor_idx >= len(self.times) - 1
        live["times"] = np.concatenate([live["times"], rows[:, 0]])
        for i, name in enumerate(live["headers"], 1):
            live["cols"][name] = np.concatenate([live["cols"][name], rows[:, i]])
        # The same steps as load_csv after reading the file: math channels, display units
        self.headers, self.times, self.columns = list(live["headers"]), live["times"], dict(live["cols"])
        self._add_math_columns()
        self.headers, self.columns = self._to_display_units(self.headers, self.columns)
        for name in self.headers:
            col = self.columns[name]
            valid = col[~np.isnan(col)]
            self._ranges[name] = (float(valid.min()), float(valid.max())) if len(valid) else None
        new_end = float(self.times[-1])
        self.sel_band.setBounds((t0, new_end))
        if not self._refresh_curves_in_place():
            self._rebuild_curves()  # before moving the cursor: its readout looks up the new lines
        if cursor_at_end:
            self.cursor_idx = len(self.times) - 1
            self.cursor_line.setValue(new_end)
        self._update_stats()
        if at_end:
            if showing_all:
                self._show_all()
            else:  # zoomed in: slide along, same width
                self.plot.setXRange(new_end - (b - a), new_end, padding=0)

    def _smart_bar_toggled(self, on):
        self.state.settings.set(smart_presets.BAR_KEY, bool(on))
        self._sync_smart_bar()

    def _sync_smart_bar(self):
        """The chip row shows on Single / Split with a log open, unless switched off."""
        on = smart_presets.bar_on(self.state.settings)  # switched in Presets ▾ (or Settings)
        self.smart_bar.setVisible(on and self.times is not None and self._view() in ("single", "split"))
        if hasattr(self, "smart_label"):
            self.smart_label.setVisible(self.smart_bar.isVisible())

    def _apply_smart(self, layout):
        """A smart preset: its channels on the graph, one Split row per chart (Single for one)."""
        charts = [c["channels"] for c in layout["charts"] if c["channels"]]
        if not charts:
            return
        self.hidden.clear()
        self._mark_hidden()
        if len(charts) > 1:
            self.split.groups = [list(c) for c in charts]
            self.split._heights = {}
        self._apply_channels([n for c in charts for n in c])
        self.tabs.setCurrentWidget(self.split_page if len(charts) > 1 else self.time_page)

    def _add_math_columns(self):
        """Append a computed column per math channel whose inputs are in this log."""
        by_name = {}
        for header in self.headers:
            by_name.setdefault(header, header)
            by_name.setdefault(mathchannels.strip_unit(header), header)
        for definition, formula in mathchannels.compiled(self.state.settings):
            cols = [by_name.get(r) for r in formula.refs]
            if None in cols:
                continue
            unit = definition.get("unit", "")
            title = "\u0192 " + definition["name"] + (f" ({unit})" if unit else "")
            result = formula.evaluate([self.columns[c] for c in cols])
            result = np.broadcast_to(np.asarray(result, dtype=float), self.times.shape).copy()
            self.columns[title] = result
            self.headers.append(title)

    def _to_display_units(self, headers, columns):
        """Rename 'X (°C)' -> 'X (°F)' etc. and convert, when imperial is chosen."""
        new_headers, new_columns = [], {}
        for header in headers:
            name, unit = log_export.split_header(header)
            shown, conv = units.conversion(unit, self.state.settings)
            title = f"{name} ({shown})" if conv != units.IDENTITY else header
            new_headers.append(title)
            new_columns[title] = units.to_display(columns[header], conv) if conv != units.IDENTITY else columns[header]
        return new_headers, new_columns

    def _edit_math(self):
        names = [mathchannels.strip_unit(h) for h in self.headers if not h.startswith("\u0192 ")]
        dialog = mathchannels.MathChannelsDialog(self.state.settings, names, self)
        if dialog.exec() and self.csv_path is not None:
            checked = set(self._checked_channels())
            self.load_csv(self.csv_path)
            self._restore_checked(lambda name: name in checked)

    def _export(self):
        if self.csv_path is None:
            return
        log_export.export_dialog(self, self.state, self.csv_path, self.banner,
                                 stem=(self.source_path or self.csv_path).stem)

    # --- toolbar ---

    def _legend_toggled(self, on):
        self.state.settings.set("playback:legend", on)
        self.legend.setVisible(on)

    def _timeline_toggled(self, on):
        self.state.settings.set("playback:timeline", on)
        if hasattr(self, "ov_box"):
            self.ov_box.setVisible(on)

    def _axis(self):
        mode = self.state.settings.get("playback:axis")
        if mode == "single":  # renamed to Independent
            mode = "independent"
        return mode if mode in AXIS_SCALE else "independent"  # also an old saved "auto"

    def _set_axis(self, mode):
        self.state.settings.set("playback:axis", mode)
        self._update_axis_button()
        self._rebuild_curves()

    @staticmethod
    def _scroll_sideways(widget):
        """A view whose controls need more width than the window has scrolls sideways on its own,
        instead of making every other view (and the whole page) that wide. (The Heatmap's control
        row needs ~970 px: it used to cut off Analyze's toolbar below ~1450 px windows.)"""
        from PySide6.QtWidgets import QFrame, QScrollArea
        holder = QScrollArea()
        holder.setFrameShape(QFrame.Shape.NoFrame)
        holder.setWidgetResizable(True)
        holder.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder.setWidget(widget)
        return holder

    def _update_axis_button(self):
        if theme.is_mac():  # "Axis" is its own label; a pop-up button's ⌃⌄ (mockup .pop)
            from . import sf_symbols
            c = theme.colors()
            self.axis_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.axis_button.setIconSize(QSize(12, 12))
            sym = sf_symbols.icon("chevron.up.chevron.down", 12, c.get("sub", c["muted"]), "semibold", point=8.5)
            if sym is not None:
                self.axis_button.setIcon(sym)
            self.axis_button.setText(f"{self._axis().capitalize()} ")
            return
        self.axis_button.setText(f"Axis: {self._axis().capitalize()}  \u25be")

    def _mac_toolbar(self, rl, ctrl, view_row):
        """Mac interface (Mockups/Swift native, Analyze): one top bar with the log's name as a
        button, the views as a segmented control and the icons on the right; under it Axis and the
        smart presets. Same buttons as Classic, only placed differently."""
        from PySide6.QtWidgets import QFrame
        for lay in (ctrl, view_row):
            while lay.count():
                lay.takeAt(0)
            rl.removeItem(lay)
        rl.removeWidget(self.smart_bar)
        self.file_label.hide()
        old_open = self.open_button
        old_open.hide()
        pill = QPushButton(analyze_tools.menu_icon("description"), "Open a log…")
        pill.setObjectName("macFilePill")
        pill.setToolTip(old_open.toolTip())
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.clicked.connect(self._open_menu)
        self.open_button = pill
        from .widgets import segment_track
        seg = QFrame()
        segment_track(seg, QHBoxLayout(seg), self.view_buttons)  # the macOS segmented control
        # Every page: the title bar = what you're working on + page actions; the row under it =
        # the content's own controls. Analyze's "what" is the log, so the file sits where the
        # Logger's Record / Live View's Start sit.
        # Title bar: Analyze [📄 log ▾] (nothing at the right: no button there is page-wide)
        # Channels column: Channels · n of N [cols][bars] / Select channels ▾ / search / list
        # Content: [◧][Single|Split|Heatmap|Scatter|Raw] … Axis [Independent ▾] [Compare] [ƒ] [Presets ▾] [⋯];
        # then smart presets.
        self.sidebar.mac_header_buttons()  # list buttons into the column's heading row
        old_toggle = self.sidebar_button
        old_toggle.setParent(self)
        old_toggle.hide()
        self.sidebar_button = ToolIcon(
            "left_panel_close", "Show or hide the channels column", checkable=True)
        self.sidebar_button.setProperty("plain", True)  # a sidebar toggle: no grey square when on
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(pill)
        top.addStretch(1)  # only the log up here: Compare, ƒ, Presets, ⋯ are the graph's (row 2's end)
        # Row 2: Select channels ▾ parks at its front while the column is hidden (never in the title
        # bar); the column's toggle is the first right-hand icon, so it never moves (user 2026-11-23).
        from .mac_shell import first_row
        views = first_row(QHBoxLayout())
        views.addWidget(seg)
        views.addStretch(1)
        views.addWidget(muted("Axis"))
        views.addWidget(self.axis_button)
        views.addSpacing(6)
        from .mac_shell import row_icons  # rule 7: the one right-hand order
        row_icons(views, tools=(self.sidebar_button, self.compare_button, self.math_button),
                  presets=self.presets_button, more=self.more_button)
        for w in (self.legend_button, self.timeline_button):  # switched from ⋮ instead
            w.setParent(self)
            w.hide()
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.smart_label = muted("Smart presets")
        row2.addWidget(self.smart_label)
        row2.addWidget(self.smart_bar, 1)
        from PySide6.QtWidgets import QSizePolicy
        self.axis_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        from .mac_shell import adopt_bar
        adopt_bar(self, top)  # the title bar, beside "Analyze"
        rl.insertLayout(0, views)
        rl.insertLayout(2, row2)  # after the compare row
        self.ctrl_row = views  # Select channels parks here (after the toggle) while the column is hidden
        self._update_axis_button()

    def _axis_menu(self, menu=None):
        menu = menu if menu is not None else QMenu(self)
        for key, label, _mode in AXIS_MODES:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._axis() == key)
            act.triggered.connect(lambda _=False, k=key: self._set_axis(k))
        return menu

    def _reset_values(self):
        """Values back to normal (after stretching / flattening), time zoom left as it is."""
        if self._fitted:
            self.plot.setYRange(-4, 106, padding=0)
        else:
            self.plot.getPlotItem().vb.enableAutoRange(axis="y", enable=True)

    def _reset_view(self):
        """Whole log across, every line back in view."""
        if self.times is None:
            return
        self._show_all()
        self.plot.getPlotItem().vb.enableAutoRange(axis="y", enable=True)
        self._rebuild_curves()

    def _graph_menu(self):
        """Right-click on the graph (and the ⚙): view, what's shown, stats, export."""
        T = analyze_tools
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        T.menu_header(menu, "View")
        T.menu_action(menu, "Reset view", "fit_screen", self._reset_view,
                      tip="The whole log across, every line back in view")
        sub = analyze_tools.StickyMenu("Channels", menu)
        T.menu_sub(menu, "Channels", "checklist", sub)
        items = self.tree.items()
        order = getattr(self.tree, "log_order", []) or []
        items.sort(key=lambda it: order.index(it.data(0, ROLE)) if it.data(0, ROLE) in order else 0)
        for it in items:
            act = sub.addAction(it.data(0, ROLE))
            act.setCheckable(True)
            act.setChecked(it.checkState(0) == Qt.CheckState.Checked)
            act.toggled.connect(lambda on, item=it: item.setCheckState(
                0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked))
        if not items:
            sub.addAction("Open a log first").setEnabled(False)
        sub.addSeparator()
        T.menu_action(sub, "Remove all", "clear_all", lambda: self._set_all_checked(False))
        T.menu_sub(menu, "Axis", "height", self._axis_menu(QMenu("Axis", menu)))
        T.menu_header(menu, "Show")
        T.menu_toggle(menu, "Legend", "list", self.legend_button.isChecked(), self.legend_button.setChecked)
        T.menu_toggle(menu, "Timeline", "acute", self.timeline_button.isChecked(), self.timeline_button.setChecked)
        T.menu_toggle(menu, "Follow mouse", "mouse", self.follow_check.isChecked(), self.follow_check.setChecked,
                      tip="Move the cursor as the mouse moves over the graph. Off: click to place it.")
        T.menu_header(menu, "Stats")
        self._stats_menu(menu)
        T.menu_header(menu, "More")
        T.menu_action(menu, "Export graph…", "image", self._export_graph,
                      tip="Save the graph as an image (PNG, SVG) or its lines as CSV, or print it")
        T.menu_sub(menu, "Axes and mouse", "open_with", self._view_menu())
        T.menu_sub(menu, "Plot options", "tune", self.plot.getPlotItem().ctrlMenu)
        self._last_graph_menu_width = menu.sizeHint().width()
        return menu

    def _view_menu(self):
        """pyqtgraph's view menu (View all, X axis, Y axis, Mouse mode), kept for our menu."""
        if getattr(self, "_vb_menu", None) is None:
            from pyqtgraph.graphicsItems.ViewBox.ViewBoxMenu import ViewBoxMenu
            self._vb_menu = ViewBoxMenu(self.plot.getPlotItem().vb)
            self._vb_menu.setTitle("Axes and mouse")
            self._vb_menu.aboutToShow.connect(self._vb_menu.updateState)
        return self._vb_menu

    def _export_graph(self):
        scene = self.plot.scene()
        scene.contextMenuItem = self.plot.getPlotItem()
        scene.showExportDialog()

    def _presets_menu(self):
        T = analyze_tools
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        headers = list(self.headers) if self.times is not None else []
        T.menu_header(menu, "Smart presets")
        gen = self.smart
        for layout in gen.layouts:
            rows = len(layout["charts"])
            T.menu_action(menu, f"{layout['name']}  ({rows} row{'s' if rows != 1 else ''})",
                          smart_presets.icon_for(layout["name"]), lambda _=False, l=layout: self._apply_smart(l),
                          tip=layout.get("description", ""))
        if not gen.layouts:
            menu.addAction("Open a log to see them" if self.times is None else "None for this log").setEnabled(False)
        sm = T.menu_sub(menu, "Smart preset options", "auto_awesome")
        T.menu_action(sm, "Generating…" if gen.busy else "Regenerate with AI", "refresh",
                              lambda: (smart_presets.forget(gen.headers), gen.generate(self)),
                              enabled=bool(gen.headers) and ai.enabled(self.state.settings) and not gen.busy,
                              tip="Ask your AI again for this log's channels" if ai.enabled(self.state.settings)
                              else "Choose an AI in Settings → AI first")
        T.menu_toggle(sm, "Show smart preset chips", "view_carousel",
                      smart_presets.bar_on(self.state.settings), self._smart_bar_toggled)
        T.menu_toggle(sm, "Generate with AI automatically", "auto_awesome",
                      bool(self.state.settings.get(smart_presets.AI_KEY, True)),
                      lambda on: self.state.settings.set(smart_presets.AI_KEY, bool(on)),
                      tip="When a log with new channels opens, ask your AI for layouts (only channel names are sent)")
        if gen.error:
            sm.addSeparator()
            sm.addAction(f"Last AI attempt: {gen.error[:70]}").setEnabled(False)
        saved = T.saved_presets(self.state.settings)
        T.menu_header(menu, "Your presets")
        for name, names in saved.items():
            chans = T.resolve(names, headers)
            T.menu_action(menu, f"{name}  ({len(chans)}/{len(names)})", "bookmark",
                          lambda _=False, c=chans: self._apply_channels(c), bool(chans),
                          tip="" if chans else "None of its channels are in this log")
        if not saved:
            menu.addAction("None saved yet").setEnabled(False)
        menu.addSeparator()
        have = bool(self._checked_channels())
        T.menu_action(menu, "Save preset…", "bookmark_add", self._save_preset, have,
                      tip="Save the channels on the graph as a new preset")
        over = menu.addMenu(T.menu_icon("save_as"), "Overwrite preset")
        over.menuAction().setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
        over.setToolTipsVisible(True)
        # Always opens, saying why when there's nothing to pick (a greyed item explains nothing).
        if not saved:
            over.addAction("No saved presets yet: use Save preset… first").setEnabled(False)
        elif not have:
            over.addAction("Put some channels on the graph first").setEnabled(False)
        for name in saved:
            T.menu_action(over, name, "bookmark", lambda _=False, n=name: self._overwrite_preset(n), have)
        T.menu_action(menu, "Manage presets…", "tune", self._manage_presets,
                      tip="Rename or delete your presets")
        return menu

    def _apply_channels(self, names):
        """Put exactly these channels on the graph, in this order."""
        wanted = list(names)
        self.tree.blockSignals(True)
        items = {it.data(0, ROLE): it for it in self.tree.items()}
        for name, it in items.items():
            it.setCheckState(0, Qt.CheckState.Checked if name in wanted else Qt.CheckState.Unchecked)
        for name in reversed(wanted):  # listed in the preset's order, at the top
            it = items.get(name)
            if it is not None:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
                self.tree.insertTopLevelItem(0, it)
        self.tree.blockSignals(False)
        self._rebuild_curves()

    def _save_preset(self):
        from .popups import InputDialog
        name, ok = InputDialog.getText(self, "Save preset", "Name for these channels:")
        name = (name or "").strip()
        if ok and name:
            analyze_tools.save_preset(self.state.settings, name, self._checked_channels())

    def _overwrite_preset(self, name):
        """Replace a saved preset's channels with the ones on the graph now."""
        from .popups import MessageBox
        n = len(self._checked_channels())
        if MessageBox.question(self, "Overwrite preset",
                               f"Replace “{name}” with the {n} channel{'s' if n != 1 else ''} on the graph now?"
                               ) != MessageBox.StandardButton.Yes:
            return
        analyze_tools.save_preset(self.state.settings, name, self._checked_channels())

    def _manage_presets(self):
        """Rename or delete saved presets."""
        settings = self.state.settings
        analyze_tools.manage_presets(
            self, lambda: analyze_tools.saved_presets(settings),
            lambda old, new: analyze_tools.rename_preset(settings, old, new),
            lambda name: analyze_tools.delete_preset(settings, name),
            "No presets saved yet.\nPut channels on the graph, then Presets → Save preset….",
            tip=lambda _name, names: ", ".join(names))

    def _more_menu(self):
        T = analyze_tools
        menu = QMenu(self)
        loaded = self.times is not None
        T.menu_header(menu, "This log")
        T.menu_action(menu, "Log details", "info", lambda: self._show_file_details(self.more_button), loaded)
        T.menu_action(menu, "Edit log…", "edit", self._edit_log, loaded)
        T.menu_action(menu, "Export…", "ios_share", self._export, loaded)
        T.menu_header(menu, "View")
        T.menu_action(menu, "Reset view", "fit_screen", self._reset_view, loaded)
        if theme.is_mac():  # their toolbar buttons are off the Mac top bar (four icons, as in the mockup)
            T.menu_toggle(menu, "Legend", "list", self.legend_button.isChecked(), self.legend_button.setChecked,
                          tip="The channel chips under the graph")
            T.menu_toggle(menu, "Timeline", "acute", self.timeline_button.isChecked(),
                          self.timeline_button.setChecked, tip="The whole log in miniature under the graph")
        self._more_menu_width = menu.sizeHint().width()
        return menu

    def _edit_log(self):
        """Rename the log, or save the selected part as a new log."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QRadioButton
        from .popups import MessageBox, OverlayDialog
        path = self.csv_path
        if path is None:
            return
        imported = bool(self.source_format)
        shown = (self.source_path or path).stem
        dlg = OverlayDialog(self)
        dlg.setWindowTitle("Edit log")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        name_edit = QLineEdit(shown)
        form.addRow("Name", name_edit)
        lay.addLayout(form)
        rename = QRadioButton("Rename this log")
        rename.setChecked(True)
        trim = QRadioButton("Save part of it as a new log (this one is kept)")
        rng = self._stats_range() if self.stats_mode.currentData() == "sel" else None
        if rng is None:
            (x0, x1), _ = self.plot.getPlotItem().vb.viewRange()
            t0, t1 = float(self.times[0]), float(self.times[-1])
            rng = (max(x0, t0), min(x1, t1))
            what = "what the graph shows"
        else:
            what = "the selection"
        trim.setText(f"Save {what} ({rng[0]:.1f} \u2013 {rng[1]:.1f} s) as a new log; this one is kept")
        trim.toggled.connect(lambda on: name_edit.setText(shown + "_part" if on else shown))
        if imported:  # the original belongs to the other tool; parts go to Datalogs
            rename.setEnabled(False)
            rename.setToolTip("A log from another tool isn't renamed here: Export… saves it under any name.")
            trim.setChecked(True)
        lay.addWidget(rename)
        lay.addWidget(trim)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        stem = name_edit.text().strip().removesuffix(".csv")
        if not stem or any(ch in stem for ch in "/\\:"):
            MessageBox.warning(self, "Edit log", "Please enter a file name without / \\ or :.")
            return
        # Parts of an imported log go to Datalogs; others beside the log. Parts of the app's own
        # recordings (and of imported logs) join Recent logs.
        folder = datalogs_dir() if imported else path.parent
        register = imported or recent_logs.is_recent(path)
        new = folder / (stem + ".csv") if trim.isChecked() else path.with_name(stem + ".csv")
        if new != path and new.exists():
            MessageBox.warning(self, "Edit log", f"There's already a log called {new.name}.")
            return
        try:
            if trim.isChecked():
                if new == path:
                    MessageBox.warning(self, "Edit log", "Give the new log a different name.")
                    return
                rows = analyze_tools.trim_csv(path, new, *rng)
                if register:
                    recent_logs.add_existing(new, like=path)
                self.load_csv(new)
                self.banner.show_message(f"Saved {rows:,} rows as {new.name}", "ok")
            elif new != path:
                path.rename(new)
                recent_logs.rename(path, new)
                analyze_tools.relink_projects(path, new)
                self.csv_path = self.source_path = new
                self._update_title()
                self.banner.show_message(f"Renamed to {new.name}", "ok")
        except Exception as e:
            self.banner.show_message(f"Couldn't save: {e}", "error")

    # --- raw data ---

    def _build_raw(self):
        from PySide6.QtWidgets import QTableView
        from . import theme
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(1, 1, 1, 1)  # flush inside the view's frame, like the graph
        lay.setSpacing(0)
        if theme.is_mac():  # the Logger table's rounded card (theme #macPanel[table="true"])
            w.setObjectName("macPanel")
            w.setProperty("table", True)
            w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            lay.setContentsMargins(1, 1, 1, 8)  # the zebra rows stay clear of the rounded corners
        self.raw_model = analyze_tools.RawModel(self)
        self.raw_view = QTableView()
        self.raw_view.setObjectName("rawTable")
        self.raw_view.setHorizontalHeader(analyze_tools.RawHeader(self.raw_view))
        self.raw_view.setShowGrid(False)
        self.raw_view.setFrameShape(QTableView.Shape.NoFrame)
        self.raw_view.setWordWrap(False)
        self.raw_view.horizontalHeader().setHighlightSections(False)
        self.raw_view.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.raw_view.viewport().installEventFilter(self)  # resize: columns fill the width
        self._raw_widths = []
        self._style_raw()
        theme.on_apply(self._style_raw)
        self.raw_view.setModel(self.raw_model)
        self.raw_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.raw_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.raw_view.verticalHeader().setDefaultSectionSize(26)
        self.raw_view.verticalHeader().setVisible(False)
        self.raw_view.horizontalHeader().setResizeContentsPrecision(200)
        self.raw_view.setAlternatingRowColors(True)
        self.raw_view.clicked.connect(lambda idx: self._move_cursor_to(float(self.times[idx.row()])))
        lay.addWidget(self.raw_view, 1)
        self._raw_key = None
        return w

    def _update_raw(self, force=False):
        if self.times is None:
            return
        names = self._checked_channels() or list(self.headers)  # the graph's channels, else all
        colors = {it.data(0, ROLE): it.data(0, COLOR_ROLE) for it in self.tree.items()
                  if it.data(0, COLOR_ROLE)}
        key = (id(self.times), tuple(names), tuple(sorted(colors.items())))
        if force or key != self._raw_key:
            self._raw_key = key
            self.raw_model.set_data(self.times, names, self.columns, colors)
            # Columns as wide as their values (not their names): long names are cut off with …
            # and shown in full on hover.
            view = self.raw_view
            self._raw_widths = [max(view.sizeHintForColumn(c) + 16, RAW_COL_MIN)
                                for c in range(self.raw_model.columnCount())]
            self._fit_raw_columns()
        self._raw_follow_cursor()

    def _fit_raw_columns(self):
        """Columns at least as wide as their values; when they don't fill the table, share the
        spare width out evenly so there's no empty space on the right."""
        widths = self._raw_widths
        if not widths:
            return
        spare = self.raw_view.viewport().width() - sum(widths)
        extra = [spare // len(widths)] * len(widths) if spare > 0 else [0] * len(widths)
        if spare > 0:
            extra[-1] += spare - sum(extra)
        header = self.raw_view.horizontalHeader()
        header.blockSignals(True)
        for c, (w, e) in enumerate(zip(widths, extra)):
            self.raw_view.setColumnWidth(c, w + e)
        header.blockSignals(False)

    def _style_raw(self):
        from . import theme
        c = theme.colors()
        if theme.is_mac():  # the Logger table: transparent on its card, zebra rows, no column lines
            self.raw_view.setStyleSheet(f"""
QTableView#rawTable {{ background: transparent; alternate-background-color: {c.get('zebra', c['bg'])};
    border: none; outline: none; color: {c['text']};
    selection-background-color: {c['accent_bg']}; selection-color: {c['text']}; }}
QTableView#rawTable::item {{ border: none; padding: 0 12px; }}
QTableView#rawTable::item:selected {{ background: {c['accent_bg']}; color: {c['text']}; }}
QTableView#rawTable QHeaderView {{ background: transparent; border: none; }}
QTableView#rawTable QHeaderView::section {{ background: transparent; border: none;
    border-bottom: 1px solid {c.get('th_line', c['border'])}; padding: 7px 12px; }}
QTableView#rawTable QTableCornerButton::section {{ background: transparent; border: none; }}
""")
            return
        self.raw_view.setStyleSheet(f"""
QTableView#rawTable {{ background: {c['surface']}; alternate-background-color: {c['bg']};
    border: none; outline: none; color: {c['text']};
    selection-background-color: {c['accent_bg']}; selection-color: {c['text']}; }}
QTableView#rawTable::item {{ border: none; border-right: 1px solid {c['border']}; padding: 0 6px; }}
QTableView#rawTable::item:selected {{ background: {c['accent_bg']}; color: {c['text']}; }}
QTableView#rawTable QHeaderView {{ background: {c['surface']}; border: none; }}
QTableView#rawTable QHeaderView::section {{ background: {c['surface']}; border: none;
    border-bottom: 1px solid {c['border']}; border-right: 1px solid {c['border']};
    padding: 8px 10px; color: {c['muted']}; font-weight: 700; }}
QTableView#rawTable QTableCornerButton::section {{ background: {c['surface']}; border: none; }}
""")

    def _raw_follow_cursor(self):
        if self._view() != "raw" or self.raw_model.rowCount() == 0:
            return
        idx = self.raw_model.index(self._cursor_index(), 0)
        if self.raw_view.currentIndex().row() != idx.row():
            self.raw_view.selectRow(idx.row())
            self.raw_view.scrollTo(idx, self.raw_view.ScrollHint.PositionAtCenter)

    def _checked_channels(self):
        checked = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                checked.append(item.data(0, ROLE))
        return checked

    def _sync_rows(self):
        """Show only ticked channels; give each a colour slot it keeps while ticked (so adding,
        removing or reordering others never recolours it). Returns {name: colour}."""
        colors = self.tree.sync_rows(self.state.settings)  # shared with Logger and Live View
        total = len(self.tree.items())
        self.select_button.setText(f"Select channels ({len(colors)}/{total})" if total
                                   else "Select channels")
        if theme.is_mac():  # the count also shows under the column's heading
            self.sidebar.set_count(len(colors), total)
        self.sidebar.fit_width()
        return colors

    def _selected_channel(self):
        items = self.tree.selectedItems()
        return items[0].data(0, ROLE) if items and not items[0].isHidden() else None

    def _highlight_selected(self):
        """Draw the selected row's line twice as thick."""
        chosen = self._selected_channel()
        width = graphstyle.width(self.state.settings)
        for name, curve in getattr(self, "curves", {}).items():
            color = self.curve_data[name][1]
            curve.setPen(pg.mkPen(color=color, width=width * (2 if name == chosen else 1)))
        self._split_if_shown("highlight")

    def _channel_menu(self, pos):
        T = analyze_tools
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if item is not None:
            name = item.data(0, ROLE)
            T.menu_header(menu, "This channel")
            hidden = name in self.hidden
            T.menu_action(menu, "Show line" if hidden else "Hide line",
                          "visibility" if hidden else "visibility_off", lambda: self.toggle_hidden(name))
            T.menu_toggle(menu, "Own y axis", "stacked_line_chart", name in self.own_axis,
                          lambda _on: self.toggle_own_axis(name), enabled=not self._fitted,
                          tip="Draw this channel on its own y axis in its colour (Shared axis). "
                              "Same as clicking its line on the graph.")
            T.menu_action(menu, "Remove from graph", "close",
                          lambda: item.setCheckState(0, Qt.CheckState.Unchecked))
        T.menu_header(menu, "All channels")
        T.menu_action(menu, "Select channels…", "checklist", lambda: self.picker.open_for(self.select_button))
        T.menu_action(menu, "Remove all", "clear_all", lambda: self._set_all_checked(False))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _restore_checked(self, wanted):
        """Tick the channels wanted(name) says, then draw once (ticking each with signals on
        rebuilt every line per tick: seconds with many channels on a long log)."""
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.CheckState.Checked if wanted(item.data(0, ROLE))
                               else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._rebuild_curves()

    def _set_all_checked(self, on):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)
        self.tree.blockSignals(False)
        self._rebuild_curves()

    def _add_cursor_items(self):
        for item in self._readout_items:
            self.plot.addItem(item, ignoreBounds=True)

    def _restyle(self):
        graphstyle.apply(self.plot, self.state.settings)
        for p in (self.scatter_plot,):
            graphstyle.apply(p, self.state.settings)
        self._rebuild_curves()

    def _rebuild_curves(self):
        self.plot.clear()
        self.curves = {}
        self.curve_data = {}
        self.plot.addItem(self.cursor_line)
        self._add_cursor_items()
        self.plot.addItem(self.sel_band, ignoreBounds=True)
        if self.times is None:
            return
        ticked = self._checked_channels()
        self.hidden &= set(ticked)  # unticking a channel forgets that it was hidden
        checked = [n for n in ticked if self.columns.get(n) is not None and n not in self.hidden]
        colors = self._sync_rows()
        # One range per channel over this log and the compared one, so both runs share a scale.
        ranges = {n: graphscale.value_range(self.columns[n], self._compare_column(n)) for n in checked}
        fitted, maps, extra = graphscale.plan(AXIS_SCALE[self._axis()], ranges, self.own_axis)
        units_shown = {log_export.split_header(n)[1] for n in checked if n not in extra}
        graphscale.set_axis(self.plot, fitted, units_shown.pop() if len(units_shown) == 1 else "")
        self._fitted = fitted
        pi = self.plot.getPlotItem()
        graphscale.remove_axes(pi, self._own_axes)
        self._own_axes = []
        width = graphstyle.width(self.state.settings)
        for i, name in enumerate(checked):
            col = self.columns[name]
            rng = ranges[name] if fitted else None
            color = colors.get(name) or graphstyle.color(i, self.state.settings)
            pen = pg.mkPen(color=color, width=width * (2 if name == self._selected_channel() else 1))
            scaled = maps[name](col)
            # lean: only what can show is drawn, else every cursor move redraws every sample
            curve = graphstyle.lean(self.plot.plot(self.times, scaled, pen=pen,
                                                   name=graphscale.label(name, rng)))
            self.curves[name] = curve
            self.curve_data[name] = (scaled, color)
            if name in extra:
                self._own_axes.append(graphscale.real_axis(
                    *extra[name], color, log_export.split_header(name)[1] or name))
            other = self._compare_column(name)
            if other is not None:
                graphstyle.lean(self.plot.plot(
                    self.compare["times"] + self.offset.value(), maps[name](other),
                    pen=pg.mkPen(color=color, width=max(width * 0.75, 1), style=Qt.PenStyle.DashLine),
                    name=f"{name} · {self.compare['path'].name}"))
        graphscale.add_axes(pi, self._own_axes)
        self.legend.rebuild()
        self._rebuild_overview(checked)
        # What this layout depends on besides the data, for _refresh_curves_in_place
        self._layout_sig = (tuple(checked), fitted, tuple(sorted(extra)), self.compare is None,
                            {n: ranges[n] for n in checked} if (fitted or extra) else None)
        self._after_curves_changed()

    def _after_curves_changed(self):
        self._split_dirty = True
        self._split_if_shown("rebuild")
        self._update_now_values()
        self._update_readout()
        self._update_analysis()

    def _refresh_curves_in_place(self):
        """New rows of a followed recording: hand the longer data to the lines already drawn
        instead of clearing and rebuilding the graph (that got slow on long recordings). Only when
        nothing else changed: same channels, same scaling, same legend; else False and the caller
        rebuilds."""
        sig = getattr(self, "_layout_sig", None)
        if self.times is None or not sig or not sig[3] or self.compare is not None:
            return False
        checked = list(sig[0])
        if list(self.curves) != checked or any(self.columns.get(n) is None for n in checked):
            return False
        ranges = {n: graphscale.value_range(self.columns[n]) for n in checked}
        fitted, maps, extra = graphscale.plan(AXIS_SCALE[self._axis()], ranges, self.own_axis)
        if (fitted, tuple(sorted(extra))) != sig[1:3]:
            return False
        if (fitted or extra) and ranges != sig[4]:
            return False  # the legend shows each range / own axes span it: rebuild those
        for name in checked:
            scaled = maps[name](self.columns[name])
            self.curves[name].setData(self.times, scaled)
            self.curve_data[name] = (scaled, self.curve_data[name][1])
        ov_items = getattr(self, "_ov_curves", {})
        for name in checked:
            col = self.columns[name]
            if name in ov_items:
                ov_items[name].setData(self.times, graphscale.scale(col, ranges[name]))
        self.overview.setXRange(float(self.times[0]), float(self.times[-1]), padding=0)
        self._after_curves_changed()
        return True

    # --- overview timeline ---

    def _rebuild_overview(self, checked=()):
        ov = self.overview
        ov.clear()
        ov.addItem(self.zoom_band)
        ov.addItem(self.ov_cursor)
        ov.addItem(self.ov_sel)
        self._ov_curves = {}
        if self.times is None:
            return
        for i, name in enumerate(checked):
            col = self.columns[name]
            color = QColor(self.curve_data[name][1] if name in self.curve_data
                           else graphstyle.color(i, self.state.settings))
            color.setAlpha(170)
            item = ov.plot(self.times, graphscale.scale(col, graphscale.value_range(col)),
                           pen=pg.mkPen(color, width=1))
            graphstyle.lean(item, clip=False)  # the timeline always shows the whole log
            graphstyle.cache(item)  # drawn once; moving the cursor marker doesn't redraw it
            self._ov_curves[name] = item
        ov.setXRange(float(self.times[0]), float(self.times[-1]), padding=0)
        ov.setYRange(-4, 104, padding=0)
        self.ov_cursor.setValue(float(self.times[self._cursor_index()]))
        self._tint_zoom_band()

    def _tint_zoom_band(self):
        """The timeline's zoom band: always filled with solid edges (even showing the whole log),
        so it's clear what part the graph shows and the edges are always there to drag."""
        from . import theme
        acc = QColor(theme.colors()["accent"])
        self.zoom_band.setBrush(pg.mkBrush(acc.red(), acc.green(), acc.blue(), 45))
        for line in self.zoom_band.lines:
            line.setPen(pg.mkPen(acc, width=2))
        self.zoom_band.update()

    def _align_overview_soon(self):
        from PySide6.QtCore import QTimer
        if not getattr(self, "_align_pending", False):
            self._align_pending = True
            QTimer.singleShot(0, self._align_overview)

    def _align_overview(self):
        """Line the timeline's ends up with the plot area of the graph above it (Single, or the
        Split rows), so its zoom band sits right under the part of the graph it shows."""
        self._align_pending = False
        if not hasattr(self, "ov_box"):
            return
        target = None
        if self._view() == "split" and getattr(self.split, "rows", None):
            r = self.split.rows[-1]
            target = (r["widget"], r["plot"].vb)
        elif self._view() == "single":
            target = (self.plot, self.plot.getPlotItem().vb)
        if target is None:
            return
        widget, vb = target
        rect = widget.mapFromScene(vb.sceneBoundingRect()).boundingRect()
        if rect.width() <= 0 or not self.bottom_band.isVisible():
            return
        band_x = self.bottom_band.mapToGlobal(QPoint(0, 0)).x()
        left = widget.mapToGlobal(rect.topLeft()).x() - band_x
        right = band_x + self.bottom_band.width() - widget.mapToGlobal(rect.topRight()).x()
        if theme.is_mac():
            # Its card runs the full width like the graph's; the band is padded inside it to
            # line up with the plot area (1 px top/bottom clears the card's border).
            self.ov_box.layout().setContentsMargins(0, 0, 0, 0)
            self.overview.getPlotItem().setContentsMargins(max(left, 1), 1, max(right, 1), 1)
            return
        self.ov_box.layout().setContentsMargins(max(left, 0), 0, max(right, 0), 0)

    def _show_all(self):
        if self.times is None:
            return
        self.plot.setXRange(float(self.times[0]), float(self.times[-1]), padding=0)

    def _zoom_band_moved(self):
        if self._syncing_zoom or self.times is None:
            return
        a, b = self.zoom_band.getRegion()
        if b - a <= 0:
            return
        self._syncing_zoom = True
        self.plot.setXRange(a, b, padding=0)
        self._syncing_zoom = False
        self._stats_timer.start()

    def _graph_range_changed(self):
        """Zooming or panning the graph moves the band on the overview to match."""
        if self._syncing_zoom or self.times is None:
            return
        (x0, x1), _ = self.plot.getPlotItem().vb.viewRange()
        t0, t1 = float(self.times[0]), float(self.times[-1])
        self._syncing_zoom = True
        self.zoom_band.setBounds((t0, t1))
        self.zoom_band.setRegion((max(x0, t0), min(x1, t1)))
        self._syncing_zoom = False
        self._tint_zoom_band()
        self._stats_timer.start()

    def _overview_wheel(self, event, axis=None):
        """Mouse wheel over the timeline zooms the graph in or out around the time under the
        pointer (same step as the graph's own wheel zoom), kept within the log."""
        event.accept()
        if self.times is None or len(self.times) < 2:
            return
        delta = event.delta()
        if not delta:
            return
        t0, t1 = float(self.times[0]), float(self.times[-1])
        a, b = self.plot.getPlotItem().vb.viewRange()[0]
        a, b = max(a, t0), min(b, t1)
        t = float(self.overview.getPlotItem().vb.mapSceneToView(event.scenePos()).x())
        t = min(max(t, t0), t1)
        s = 1.02 ** (-delta / 8)  # pyqtgraph's wheel step: 120 → ×0.74
        min_w = 10 * (t1 - t0) / (len(self.times) - 1)  # no closer than ~10 samples
        w = min(max((b - a) * s, min_w), t1 - t0)
        if b - a > 0:
            na = t - (t - a) * w / (b - a)
        else:
            na = t - w / 2
        na = min(max(na, t0), t1 - w)
        self.plot.setXRange(na, na + w, padding=0)

    def _overview_clicked(self, event):
        if self.times is None or event.button() != Qt.MouseButton.LeftButton:
            return
        vb = self.overview.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(event.scenePos()):
            return
        if event.double():
            self._show_all()
            return
        t = float(vb.mapSceneToView(event.scenePos()).x())
        self._move_cursor_to(t)
        a, b = self.plot.getPlotItem().vb.viewRange()[0]
        if not a <= t <= b:  # cursor jumped outside the zoomed view: bring it into the middle
            half = (b - a) / 2
            self.plot.setXRange(t - half, t + half, padding=0)

    def _restyle_gear(self):
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        self.graph_gear.setIcon(_material_icon("settings", 20, c["tab_text"]))
        self.graph_fit.setIcon(_material_icon("fit_screen", 20, c["tab_text"]))
        for b in (self.graph_gear, self.graph_fit):
            b.setStyleSheet(
                f"QPushButton#graphGear {{ background: {c['surface']}; border: 1px solid {c['border']}; "
                f"border-radius: 8px; padding: 0; }}"
                f"QPushButton#graphGear:hover {{ background: {c['surface2']}; }}")

    def _place_gear(self):
        vb_rect = self.plot.mapFromScene(self.plot.getPlotItem().vb.sceneBoundingRect()).boundingRect()
        self.graph_gear.move(vb_rect.right() - self.graph_gear.width() - 6, vb_rect.top() + 6)
        self.graph_gear.raise_()
        self.graph_fit.move(self.graph_gear.x(), self.graph_gear.y() + self.graph_gear.height() + 6)
        self.graph_fit.raise_()

    def _graph_menu_width(self):
        return self._last_graph_menu_width if getattr(self, "_last_graph_menu_width", 0) else 220

    def eventFilter(self, obj, event):
        if obj is getattr(self, "plot", None) and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Enter, QEvent.Type.Leave):
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._place_gear)  # once the plot has laid out its axes
            elif event.type() == QEvent.Type.Enter:
                self._gear_fade.setOpacity(1.0)
                self._fit_fade.setOpacity(1.0)
            else:
                self._gear_fade.setOpacity(0.45)
                self._fit_fade.setOpacity(0.45)
            return False
        if obj is getattr(self, "bottom_band", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._align_overview_soon()
            return False
        if obj is getattr(getattr(self, "raw_view", None), "viewport", lambda: None)() and \
                event.type() == QEvent.Type.Resize:
            self._fit_raw_columns()
            return False
        if obj is getattr(self, "tabs", None) and event.type() == QEvent.Type.Resize:
            self._fit_title()  # keep the log's name within the free space beside the graph tabs
            return False
        if event.type() == QEvent.Type.KeyPress and self.times is not None and \
                event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self._step_cursor(-step if event.key() == Qt.Key.Key_Left else step)
            return True
        return super().eventFilter(obj, event)

    def _step_cursor(self, step):
        idx = min(max(self._cursor_index() + step, 0), len(self.times) - 1)
        t = float(self.times[idx])
        self.cursor_line.setValue(t)
        a, b = self.plot.getPlotItem().vb.viewRange()[0]
        if not a <= t <= b:  # keep the cursor in view while stepping through a zoomed graph
            shift = t - b if t > b else t - a
            self.plot.setXRange(a + shift, b + shift, padding=0)

    # --- sidebar value columns ---

    def _columns_menu_extra(self, menu):
        """Analyze's part of the sidebar's value-columns menu: what Min / Max / Avg cover."""
        analyze_tools.menu_header(menu, "Stats")
        self._stats_menu(menu)

    def _columns_changed(self):
        self._update_now_values()
        self._update_stats()

    def _stats_menu(self, parent):
        """'Min / Max / Avg use' submenu: what part of the log the stats, heatmap and scatter use."""
        text = self.range_label.text()
        sub = analyze_tools.menu_sub(parent, "Min / Max / Avg use" + (f"  ({text})" if text else ""), "analytics")
        tips = {"view": "The part the graph shows (zoom in to narrow it)",
                "all": "Everything, however far you zoom",
                "sel": "The blue band on the graph. Drag it or its edges, or Shift-drag across "
                       "the graph to draw a new one"}
        for key, label in STATS_MODES:
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.stats_mode.currentData() == key)
            act.setToolTip(tips[key])
            act.triggered.connect(lambda _=False, k=key: self.stats_mode.setCurrentIndex(
                self.stats_mode.findData(k)))
        return sub

    def _compare_value(self, name, t):
        """The compared log's value of a channel at time t (shift applied), or None."""
        other = self._compare_column(name)
        if other is None:
            return None
        ct = self.compare["times"] + self.offset.value()
        if not ct[0] <= t <= ct[-1]:
            return None
        j = min(int(np.searchsorted(ct, t)), len(ct) - 1)
        return None if np.isnan(other[j]) else float(other[j])

    def _update_now_values(self):
        """Fill the Now / Compared / Δ columns for every channel at the cursor."""
        if self.times is None:
            return
        shown = set(self.sidebar.visible_columns()) & {"now", "cmp", "delta"}
        idx = self._cursor_index()
        t = float(self.times[idx])
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.isHidden():
                continue
            name = item.data(0, ROLE)
            val = self.columns[name][idx]
            now = None if np.isnan(val) else float(val)
            rng = self._ranges.get(name)
            frac = None if now is None or not rng else (
                1.0 if rng[1] == rng[0] else (now - rng[0]) / (rng[1] - rng[0]))
            if item.data(0, FRAC_ROLE) != frac:
                item.setData(0, FRAC_ROLE, frac)
            if not shown:
                continue
            cmp = self._compare_value(name, t)
            texts = {"now": now, "cmp": cmp,
                     "delta": (now - cmp) if now is not None and cmp is not None else None}
            for key in shown:
                v = texts[key]
                text = "–" if v is None else _fmt(v, signed=key == "delta")
                if item.text(COL[key]) != text:
                    item.setText(COL[key], text)
                    item.setTextAlignment(COL[key], Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tree.blockSignals(False)

    def _nearest_index(self, t):
        """Index of the sample closest to time t."""
        i = int(np.searchsorted(self.times, t))
        if i <= 0:
            return 0
        if i >= len(self.times):
            return len(self.times) - 1
        return i - 1 if (t - self.times[i - 1]) <= (self.times[i] - t) else i

    def _cursor_moved(self):
        if self.times is None or len(self.times) == 0:
            return
        idx = self._nearest_index(self.cursor_line.value())
        if self.cursor_line.value() != float(self.times[idx]):  # sit exactly on the sample shown
            self.cursor_line.blockSignals(True)
            self.cursor_line.setValue(float(self.times[idx]))
            self.cursor_line.blockSignals(False)
        self.cursor_idx = idx
        self.ov_cursor.setValue(float(self.times[idx]))
        if hasattr(self, "raw_model"):
            self._raw_follow_cursor()
        self._update_now_values()
        self._update_readout()
        self._split_if_shown("set_cursor")
        if theme.is_mac():
            self.sidebar.set_cursor(float(self.times[idx]))

    def _move_cursor_to(self, t):
        """Put the cursor on the sample nearest t; the timeline and sidebar follow."""
        if self.times is None or len(self.times) == 0:
            return
        t0, t1 = float(self.times[0]), float(self.times[-1])
        self.cursor_line.setValue(min(max(t, t0), t1))

    def _scene_to_time(self, pos):
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(pos):
            return None
        return float(vb.mapSceneToView(pos).x())

    def _mouse_moved(self, event):
        if self.times is None or self._view() != "single" or not self.follow_check.isChecked():
            return
        if self.plot.scene().mouseGrabberItem() is not None:
            return  # dragging the cursor, the range band or panning: leave it alone
        t = self._scene_to_time(event[0])
        if t is not None:
            self._move_cursor_to(t)

    def _mouse_clicked(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            self._graph_menu().exec(event.screenPos().toPoint())
            return
        if self.times is None or event.button() != Qt.MouseButton.LeftButton or event.double():
            return
        if not self._fitted and len(self.curve_data) > 1:
            hit = graphscale.line_at(self.plot.getPlotItem().vb, event.scenePos(), self.times,
                                     {n: y for n, (y, _c) in self.curve_data.items()})
            if hit is not None:  # Shared: clicking a line gives it its own axis (again: back)
                self.toggle_own_axis(hit)
                return
        t = self._scene_to_time(event.scenePos())
        if t is not None:
            self.plot.setFocus()  # so the arrow keys step the cursor
            self._move_cursor_to(t)

    def channel_color(self, name):
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == name), None)
        color = item.data(0, COLOR_ROLE) if item is not None else None
        return color or (self.curve_data[name][1] if name in self.curve_data else "#888888")

    def _mark_hidden(self):
        """Sidebar rows of hidden channels get a hollow dot."""
        self.tree.blockSignals(True)
        for it in self.tree.items():
            on = it.data(0, ROLE) in self.hidden
            if bool(it.data(0, HIDDEN_ROLE)) != on:
                it.setData(0, HIDDEN_ROLE, on)
        self.tree.blockSignals(False)
        self.tree.viewport().update()

    def toggle_hidden(self, name):
        """Legend click: hide a channel's line (it stays ticked and in the list), or show it again."""
        self.hidden ^= {name}
        self._mark_hidden()
        self._rebuild_curves()

    def show_only(self, name):
        """Hide every line but this one; None shows them all."""
        self.hidden = set() if name is None else {n for n in self._checked_channels() if n != name}
        self._mark_hidden()
        self._rebuild_curves()

    def highlight_channel(self, name):
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == name), None)
        if item is not None:
            self.tree.setCurrentItem(item)
            item.setSelected(True)

    def remove_channel(self, name):
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == name), None)
        if item is not None:
            item.setCheckState(0, Qt.CheckState.Unchecked)

    def toggle_own_axis(self, name):
        """Give a channel its own y axis on the shared scale, or put it back on the shared one."""
        self.own_axis ^= {name}
        self._rebuild_curves()

    def _follow_toggled(self, on):
        self.state.settings.set("playback:follow_mouse", on)
        self.cursor_line.setMovable(not on)

    def _cursor_index(self):
        return min(self.cursor_idx, len(self.times) - 1)

    def _update_readout(self):
        """Dots where each line meets the cursor, and a box with the real values."""
        if self.times is None or not self.curve_data:
            self.cursor_dots.setData([], [])
            self.readout.hide()
            return
        idx = self._cursor_index()
        t = float(self.times[idx])
        xs, ys, brushes = [], [], []
        pt = graphstyle.label_pt(self.state.settings)
        rows = []  # no time line: the cursor's place on the x axis and timeline shows it
        for name, (scaled, color) in self.curve_data.items():
            val = self.columns[name][idx]
            if np.isfinite(scaled[idx]):
                xs.append(t)
                ys.append(float(scaled[idx]))
                brushes.append(pg.mkBrush(color))
            text = "–" if np.isnan(val) else f"{val:.6g}"
            cmp = self._compare_value(name, t)
            if cmp is not None:
                text += f"  (compared {cmp:.6g})"
            rows.append((color, name, text))
        self.cursor_dots.setData(xs, ys, brush=brushes, pen=pg.mkPen("#00000080", width=1))
        self.readout.setHtml(graphstyle.readout_html(rows, pt))
        self.readout.show()
        self._place_readout()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._place_readout)  # again once a just-rebuilt legend has its size

    def _place_readout(self):
        """Keep the box at the top of the graph, beside the cursor, flipping sides near the right edge."""
        if self.times is None or not self.readout.isVisible():
            return
        (x0, x1), (_y0, y1) = self.plot.getPlotItem().vb.viewRange()
        t = self.cursor_line.value()
        right_half = (t - x0) > 0.55 * (x1 - x0)
        self.readout.setAnchor((1.0, 0.0) if right_half else (0.0, 0.0))
        gap = 0.008 * (x1 - x0)
        x = t - gap if right_half else t + gap
        self.readout.setPos(x, y1)
        # Things at the top of the graph the box mustn't hide under: the legend, the ⚙ button.
        obstacles = []
        legend = self.plot.getPlotItem().legend
        if legend is not None and legend.isVisible() and legend.items:
            obstacles.append(legend.sceneBoundingRect())
        for gear in (getattr(self, "graph_gear", None), getattr(self, "graph_fit", None)):
            if gear is not None and gear.isVisible():
                obstacles.append(self.plot.mapToScene(gear.geometry()).boundingRect())
        if obstacles:
            # The box's own scene rect lags until it repaints, so work it out from its size.
            from PySide6.QtCore import QRectF
            vb = self.plot.getPlotItem().vb
            size = self.readout.textItem.boundingRect().size()
            top_left = vb.mapViewToScene(QPointF(x, y1))
            left = top_left.x() - (size.width() if right_half else 0)
            box = QRectF(left, top_left.y(), size.width(), size.height())
            for rect in obstacles:
                if box.intersects(rect):  # drop below it instead of hiding under it
                    box.moveTop(rect.bottom() + 4)
            if box.top() != top_left.y():
                self.readout.setPos(x, vb.mapSceneToView(QPointF(0, box.top())).y())

    # --- analysis: range, stats, scatter ---

    def _build_scatter(self, c):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.scatter_x, self.scatter_y, self.scatter_color = QComboBox(), QComboBox(), QComboBox()
        for label, combo in (("X", self.scatter_x), ("Y", self.scatter_y), ("Colour by", self.scatter_color)):
            row.addWidget(muted(label))
            combo.setMinimumWidth(100)
            combo.currentIndexChanged.connect(lambda _: self._update_analysis())
            row.addWidget(combo, 1)
        layout.addLayout(row)
        self.scatter_plot = pg.PlotWidget()
        self.scatter_plot.setBackground(c["surface"])
        self.scatter_plot.showGrid(x=True, y=True, alpha=0.15)
        self.scatter_plot.addLegend()
        graphstyle.apply(self.scatter_plot, self.state.settings)
        layout.addWidget(self.scatter_plot, 1)
        return box

    def _retheme_overlays(self):
        """Recolour the graph overlays drawn with theme colours (the zoom band, cursor lines, value
        box and selection bands); graph backgrounds are handled by graphstyle."""
        from . import theme
        c = theme.colors()
        sel = QColor(c["info"])
        for line in self.zoom_band.lines:  # edges were pyqtgraph's default yellow
            line.setHoverPen(pg.mkPen(c["text"], width=3))
        self._tint_zoom_band()  # fill + edge colour (clear while the whole log shows)
        self.ov_cursor.setPen(pg.mkPen(c["text"], width=1.5))
        self.cursor_line.setPen(pg.mkPen(c["text"], width=1.5))
        bg = QColor(c["surface"])
        self.readout.fill = pg.mkBrush(bg.red(), bg.green(), bg.blue(), 235)
        self.readout.border = pg.mkPen(c["border"])
        self.readout.setColor(c["text"])  # default is light grey, unreadable on Light
        self.readout.update()
        for band, alpha, width in ((self.sel_band, 40, 1.5), (self.ov_sel, 70, 1)):
            band.setBrush(pg.mkBrush(sel.red(), sel.green(), sel.blue(), alpha))
            for line in band.lines:
                line.setPen(pg.mkPen(sel, width=width))
        self.zoom_band.update()

    def _fill_analysis_combos(self):
        def fill(combo, names, none=None, pick=0):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            if none:
                combo.addItem(none, None)
            for n in names:
                combo.addItem(n, n)
            i = combo.findText(current)
            combo.setCurrentIndex(i if i >= 0 else min(pick, combo.count() - 1))
            combo.blockSignals(False)
        names = list(self.headers)
        fill(self.scatter_x, names, pick=0)
        fill(self.scatter_y, names, pick=1)
        fill(self.scatter_color, names, none="None")
        self.heatmap.set_names(names)

    def _stats_range(self):
        """(a, b) the stats use: the zoomed part of the log, or None for the whole log."""
        if self.times is None:
            return None
        mode = self.stats_mode.currentData()
        if mode == "all":
            return None
        a, b = (self.sel_band if mode == "sel" else self.zoom_band).getRegion()
        t0, t1 = float(self.times[0]), float(self.times[-1])
        tol = max((t1 - t0) * 0.002, 1e-6)
        if a <= t0 + tol and b >= t1 - tol:
            return None
        return max(a, t0), min(b, t1)

    def _stats_mode_changed(self, save=True):
        mode = self.stats_mode.currentData()
        if save:
            self.state.settings.set("playback:stats_mode", mode)
        on = mode == "sel" and self.times is not None
        if on:  # a selection off-screen is no use: put it in the middle of what's shown
            (x0, x1), _ = self.plot.getPlotItem().vb.viewRange()
            a, b = self.sel_band.getRegion()
            if b < x0 or a > x1:
                self.sel_band.setRegion((x0 + (x1 - x0) * 0.4, x0 + (x1 - x0) * 0.6))
        self.sel_band.setVisible(on)
        self.ov_sel.setVisible(on)
        self._selection_moved()

    def _selection_moved(self):
        self.ov_sel.setRegion(self.sel_band.getRegion())
        self._stats_timer.start()

    def _region_moved(self):
        rng = self._stats_range()
        if self.times is None:
            self.range_label.setText("")
        elif rng is None:
            self.range_label.setText("whole log")
        else:
            a, b = rng
            self.range_label.setText(f"{a:.1f} \u2013 {b:.1f} s ({b - a:.1f} s)")
        self._update_stats()
        self._update_analysis()

    def _range_mask(self, times=None):
        times = self.times if times is None else times
        rng = self._stats_range()
        if rng is None:
            return np.ones(len(times), dtype=bool)
        a, b = rng
        return (times >= a) & (times <= b)

    def _update_stats(self):
        if self.times is None:
            return
        mask = self._range_mask()
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            col = self.columns[item.data(0, ROLE)][mask]
            valid = col[~np.isnan(col)]
            for key, fn in (("min", np.min), ("max", np.max), ("avg", np.mean)):
                item.setText(COL[key], _fmt(float(fn(valid))) if len(valid) else "–")
                item.setTextAlignment(COL[key], Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tree.blockSignals(False)

    def _update_analysis(self):
        if self.times is None or not hasattr(self, "scatter_plot"):
            return
        view = self._view()
        mask = self._range_mask()
        if view == "heatmap":
            self.heatmap.draw(self.columns, mask)
        elif view == "scatter":
            self._draw_scatter(mask)
        elif view == "raw":
            self._update_raw()

    def _view(self):
        """Key of the view showing: single, split, heatmap, scatter or raw."""
        widget = self.tabs.currentWidget()
        return next((k for k, w, _l, _g in getattr(self, "_views", ()) if w is widget), "single")

    def _build_split(self):
        self.split = SplitView(self)
        self._split_dirty = True
        self.split_page = QWidget()
        lay = QVBoxLayout(self.split_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.split, 1)
        return self.split_page

    def _split_if_shown(self, method):
        """Pass a change on to the Split view, only while it's showing (rebuilt when shown)."""
        if self._view() != "split":
            return
        if method == "rebuild":
            self._split_dirty = False
        getattr(self.split, method)()

    def _view_changed(self):
        # The timeline sits under whichever of Single / Split is showing.
        view = self._view()
        graph = view in ("single", "split")
        for b in (self.legend_button, self.timeline_button, self.axis_button):
            if theme.is_mac() and b in (self.legend_button, self.timeline_button):
                continue  # Mac: these two are ⋯ menu items, never shown as buttons (no row holds them)
            b.setVisible(graph)  # graph-only
        if hasattr(self, "smart_bar"):
            self._sync_smart_bar()
        if view in ("single", "split"):
            layout = self.time_layout if view == "single" else self.split_page.layout()
            if layout.indexOf(self.bottom_band) < 0:
                layout.addWidget(self.bottom_band)
            self._align_overview_soon()
        if view == "split":
            if self._split_dirty:
                self._split_if_shown("rebuild")
            else:
                self.split.sync_from_page()
                self.split.set_cursor()

    def _tint_tabs(self):
        """Mark the view showing (accent) and colour every button's icon to match."""
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        current = self.tabs.currentIndex()
        for i, (b, (_k, _w, _l, glyph)) in enumerate(zip(self.view_buttons, self._views)):
            active = i == current
            if b.property("active") != ("true" if active else "false"):
                b.setProperty("active", "true" if active else "false")
                b.style().unpolish(b)
                b.style().polish(b)
            if theme.is_mac():  # grey "on" segment: icon in the text colour, segment-sized
                b.setIcon(_material_icon(glyph, theme.icon_px("control"), c["text"]))
            else:
                b.setIcon(_material_icon(glyph, 20, c["on_accent"] if active else c["tab_text"]))

    def _draw_scatter(self, mask):
        plot = self.scatter_plot
        plot.clear()
        xn, yn, cn = self.scatter_x.currentData(), self.scatter_y.currentData(), self.scatter_color.currentData()
        if not xn or not yn:
            return
        plot.setLabel("bottom", xn)
        plot.setLabel("left", yn)
        x, y = self.columns[xn][mask], self.columns[yn][mask]
        ok = ~(np.isnan(x) | np.isnan(y))
        if cn:
            cv = self.columns[cn][mask][ok]
            lo, hi = np.nanmin(cv) if len(cv) else 0, np.nanmax(cv) if len(cv) else 1
            cmap = pg.colormap.get("viridis")
            frac = (cv - lo) / (hi - lo) if hi > lo else np.zeros_like(cv)
            brushes = [pg.mkBrush(col) for col in cmap.map(np.nan_to_num(frac), mode="qcolor")]
            plot.addItem(pg.ScatterPlotItem(x[ok], y[ok], size=5, pen=None, brush=brushes,
                                            name=f"this log (colour: {cn} {lo:.3g}–{hi:.3g})"))
        else:
            plot.addItem(pg.ScatterPlotItem(x[ok], y[ok], size=5, pen=None,
                                            brush=pg.mkBrush(graphstyle.color(0)), name="this log"))
        ox, oy = self._compare_column(xn), self._compare_column(yn)
        if ox is not None and oy is not None:
            cmask = self._range_mask(self.compare["times"] + self.offset.value())
            ox, oy = ox[cmask], oy[cmask]
            ok = ~(np.isnan(ox) | np.isnan(oy))
            plot.addItem(pg.ScatterPlotItem(ox[ok], oy[ok], size=5, pen=None,
                                            brush=pg.mkBrush(graphstyle.color(1)),
                                            name=self.compare["path"].name))

    # --- AI ---

    def log_context(self, max_rows=120, max_cols=16):
        """What an AI reads about the loaded log (log_digest: pulls, checks, rpm tables, rows
        sampled densely in the pulls), limited to the selection when one is set. With a log
        overlaid, both side by side instead."""
        if self.times is None:
            return ""
        from . import log_digest
        mask = self._range_mask()
        if not mask.any():
            return "The selected range is empty."
        lines = []
        if self._stats_range() is not None:
            a, b = self._stats_range()
            lines.append(f"Only the selected range {a:.1f}–{b:.1f} s is included.")
        checked = self._checked_channels()
        if checked:
            lines.append("Channels the user has on the graph: " + ", ".join(checked))
        if self.compare is None:
            lines.append(log_digest.digest(self.csv_path.name, self.headers, self.times, self.columns,
                                           mask=mask, focus=checked, max_rows=max_rows, max_cols=max_cols))
        else:
            ct = self.compare["times"] + self.offset.value()
            lines.append(f"The user is comparing two logs on the graph (B shifted {self.offset.value():+.2f} s "
                         "to line the runs up).")
            lines.append(log_digest.compare([
                (self.csv_path.name, self.headers, self.times, self.columns, mask),
                (self.compare["path"].name, self.compare["headers"], ct, self.compare["columns"],
                 self._range_mask(ct))]))
        return "\n".join(lines)

    def ai_context(self):
        """What the Assistant sees of this page (None: no log open)."""
        if self.times is None:
            return None
        question = ("Review this datalog. Is anything wrong or worth watching (boost vs target, timing/"
                    "knock, temperatures, lambda, voltage)? What should I check or change next?")
        if self.compare is not None:
            question = ("Compare these two runs. What changed between them, is the second one better or "
                        "worse, and is anything risky?")
        from . import log_digest
        return {"title": f"log {self.csv_path.name}", "context": self.log_context(), "question": question,
                "extra": "The data is a measuring-block datalog from a VW PQ35 module, logged over "
                         "KWP2000/TP2.0. Values are in the units given in each channel name.\n"
                         + log_digest.ANALYSIS_SYSTEM}

    # --- compare log ---

    def _update_title(self):
        """Tab-row heading: just the log's file name (details on hover)."""
        if not hasattr(self, "file_label"):  # called once while the page is still being built
            return
        path = getattr(self, "csv_path", None)
        if path is None or self.times is None:
            self.file_label.setText("")
            self.file_label.setToolTip("")
            return
        shown = self.source_path or path
        self.file_label.setText(shown.name)
        if theme.is_mac():  # the file button, with long names shortened in the middle
            fm = self.open_button.fontMetrics()
            # Mac: the mockup shows the whole name (engine_2026-11-14_121055 ≈ 190 px); only very long ones shorten
            self.open_button.setText(fm.elidedText(shown.stem, Qt.TextElideMode.ElideMiddle,
                                                   260 if theme.is_mac() else 170))
            self.open_button.setToolTip(f"{shown.name}\nClick: open another log")
        self.file_label.setToolTip("")  # details on click, so nothing pops over the graph by accident
        self._fit_title()

    def _show_file_details(self, anchor=None):
        """Click on the log's name: a small panel with where it is, how long, which car."""
        from PySide6.QtWidgets import QFormLayout
        from .vehicle import decode
        from .widgets import PopupCard, reveal_file
        path = getattr(self, "csv_path", None)
        if path is None or self.times is None:
            return
        secs = float(self.times[-1] - self.times[0]) if len(self.times) else 0.0
        vin = log_export.log_vin(self.source_path or path) or log_export.log_vin(path)
        path = self.source_path or path
        car = decode(vin) if vin else None
        card = PopupCard(self, min_width=320)
        box = QVBoxLayout(card.body)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        name = QLabel(path.name)
        name.setStyleSheet("font-weight: 700;")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(name)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        where = str(path.parent)
        rows = [("Folder", where), ("Length", f"{int(secs // 60)}:{int(secs % 60):02d}"
                                              f"  ({len(self.times):,} rows)")]
        if self.source_format:
            rows.append(("Format", f"{self.source_format}, converted to Standard"))
        elif recent_logs.is_recent(path):
            entry = recent_logs.info(path)
            if entry.get("exported"):
                rows.append(("Exported", ", ".join(Path(x).name for x in entry["exported"])))
        if car:
            rows += [("Car", car["label"]), ("VIN", vin)]
        if self.compare is not None:
            rows.append(("Compared with", self.compare["path"].name))
        for label, value in rows:
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(muted(label), v)
        box.addLayout(form)
        reveal = QPushButton("Show in Finder" if sys.platform == "darwin" else "Show in folder")
        reveal.clicked.connect(lambda: (card.close(), reveal_file(path)))
        row = QHBoxLayout()
        row.addWidget(reveal)
        row.addStretch(1)
        box.addLayout(row)
        card.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        card.open_at(anchor or self.file_label, align="right")

    def _fit_title(self):
        """The name sits in the view-button row with stretch, so the layout elides it."""
        self.file_label.updateGeometry()


    def _set_compare_enabled(self):
        has = self.compare is not None
        for w in (self.offset, self.align_button, self.clear_compare_button):
            w.setEnabled(has)
        self._update_title()
        self.compare_row.setVisible(has)

    def _compare_menu(self):
        T = analyze_tools
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        loaded = self.times is not None
        T.menu_action(menu, "Overlay a log on the graph…", "stacked_line_chart", self._open_compare,
                      enabled=loaded, tip="Show a second log as dashed lines over this one" if loaded
                      else "Open a log first")
        T.menu_action(menu, "Compare logs with AI…", "auto_awesome", self._compare_with_ai,
                      tip="Pick any number of logs; your AI compares them side by side, by rpm")
        if self.compare is not None:
            T.menu_header(menu, "Overlay")
            T.menu_action(menu, "Stop overlaying", "close", self._clear_compare)
        return menu

    def _load_for_ai(self, path):
        headers, times, columns = _parse_csv(log_import.native_path(path)[0])
        headers, columns = self._to_display_units(headers, columns)
        return headers, times, columns

    def _compare_with_ai(self):
        from . import log_compare_ai
        paths = [p for p in (self.csv_path if self.times is not None else None,
                             self.compare["path"] if self.compare else None) if p]
        log_compare_ai.open_compare(self, self.state.settings, self._load_for_ai, paths)

    def _open_compare(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with datalog", str(datalogs_dir()), log_import.OPEN_FILTER)
        if path:
            self.load_compare(Path(path))

    def load_compare(self, path: Path):
        try:
            headers, times, columns = _parse_csv(log_import.native_path(path)[0])
        except Exception as e:
            self.banner.show_message(f"Couldn't open the log to compare: {e}", "error")
            return
        headers, columns = self._to_display_units(headers, columns)
        self.compare = {"path": path, "headers": headers, "times": times, "columns": columns}
        shared = [h for h in self.headers if h in columns]
        if not shared:
            self.banner.show_message("The logs have no channels in common, so there's nothing to overlay.",
                                     "warning")
        else:
            self.banner.show_message(f"Comparing with {path.name}: dashed lines. {len(shared)} channel(s) "
                                     "in common. Use Shift or Auto-align to line the runs up.", "info")
        self.offset.blockSignals(True)
        self.offset.setValue(0)
        self.offset.blockSignals(False)
        self._set_compare_enabled()
        self._rebuild_curves()

    def _clear_compare(self):
        self.compare = None
        self._set_compare_enabled()
        self.banner.hide()
        self._rebuild_curves()

    def _compare_column(self, name):
        if self.compare is None:
            return None
        return self.compare["columns"].get(name)

    def _auto_align(self):
        checked = [n for n in self._checked_channels() if self._compare_column(n) is not None]
        if not checked:
            self.banner.show_message("Tick a channel both logs have (throttle or rpm works best).", "warning")
            return
        name = checked[0]

        def first_cross(times, col):
            valid = col[~np.isnan(col)]
            if not len(valid):
                return None
            level = valid.min() + 0.5 * (valid.max() - valid.min())
            hits = np.nonzero(col > level)[0]
            return float(times[hits[0]]) if len(hits) else None

        a = first_cross(self.times, self.columns[name])
        b = first_cross(self.compare["times"], self._compare_column(name))
        if a is None or b is None:
            self.banner.show_message(f"Couldn't find a clear rise in {name} in both logs.", "warning")
            return
        self.offset.setValue(a - b)
        self.banner.show_message(f"Aligned on {name}: compared log shifted {a - b:+.2f} s.", "ok")
