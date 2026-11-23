"""Data log playback: load a CSV recorded by the logger, scrub through it
with an overview timeline, and overlay channels on pyqtgraph plots."""
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QMenu, QTabWidget,
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)
from . import graphscale, graphstyle, log_export, log_import, recent_logs, units
from . import legend_strip, smart_presets
from .heatmap import HeatmapView
from .channel_list import ChannelPicker
from .channel_sidebar import ChannelSidebar
from .storage import datalogs_dir
from .widgets import Banner, ElidedLabel, muted, pair
from .toolbar import ToolIcon
from . import menus
from .playback_common import (STATS_MODES, SIDEBAR_COLUMNS, _parse_csv)
from .playback_live import LiveFollowMixin
from .playback_toolbar import ToolbarMixin
from .playback_raw import RawMixin
from .playback_timeline import TimelineMixin
from .playback_columns import ColumnsMixin
from .playback_analysis import AnalysisMixin
from .playback_compare import CompareMixin


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


class PlaybackPage(LiveFollowMixin, ToolbarMixin, RawMixin, TimelineMixin, ColumnsMixin, AnalysisMixin, CompareMixin, QWidget):
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
            lambda: menus.popup_menu(self._compare_menu(), self.compare_button))
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
        self.presets_button.clicked.connect(lambda: menus.popup_menu(self._presets_menu(), self.presets_button))
        ctrl.addWidget(self.presets_button)
        self.axis_button = QPushButton()
        self.axis_button.setToolTip("Shared: every channel on one y axis in its real units.\n"
                                    "Independent: every channel stretched to its own range, so small "
                                    "and large values both fill the graph.\n"
                                    "In Shared, click a line to give that channel its own y axis.\n"
                                    "Applies to the Single graph and to Split rows holding several channels.")
        self.axis_button.clicked.connect(lambda: menus.popup_menu(self._axis_menu(), self.axis_button))
        ctrl.addWidget(self.axis_button)
        self.more_button = ToolIcon("more_vert", "Log details, edit, reset view, download")
        self.more_button.clicked.connect(lambda: menus.popup_menu(self._more_menu(), self.more_button))
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
        self.smart.asked.connect(self._apply_smart)  # an Ask AI answer: shown at once
        self.smart_bar.on_ask = lambda: smart_presets.ask_dialog(self, self.smart)
        state.faults_changed.connect(lambda _n: self.times is not None and self._load_smart())
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
                except Exception:  # names only make the list nicer; never stop a log opening
                    import logging
                    logging.getLogger(__name__).exception("catalog request for %s failed", family.get("module"))

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
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menus.menu_action(menu, "Open file…", "file_open", self._open_file,
                      tip="A log from this app or another tool")
        items = recent_logs.entries(limit=recent_logs.KEEP)
        menus.menu_header(menu, "Recent logs")
        if not items:
            menu.addAction("No recordings yet").setEnabled(False)
        for path, entry in items:
            act = menus.menu_action(menu, f"{path.stem}   {recent_logs.describe(entry)}",
                                "speed" if entry.get("mode") == "dyno" else "description",
                                lambda _=False, p=path: self.load_csv(p))
            act.setToolTip(str(path))
        menus.popup_menu(menu, self.open_button)

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
        self._load_smart()
