"""Analyze (PlaybackPage), range, stats and scatter; AI on the log. A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QVBoxLayout, QWidget,
)
from . import graphstyle
from . import theme
from .split_view import SplitView
from .widgets import muted
from .units import fmt as _fmt  # one number style on every tab
from .playback_common import (COL, ROLE)


class AnalysisMixin:
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
