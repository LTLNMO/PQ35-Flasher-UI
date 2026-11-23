"""Analyze (PlaybackPage), the channel list's value columns (Now, Compared, Δ, Min, Max, Avg). A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from . import graphscale, graphstyle
from . import theme
from .channel_list import COLOR_ROLE, FRAC_ROLE, HIDDEN_ROLE
from .units import fmt as _fmt  # one number style on every tab
from . import menus
from .playback_common import (STATS_MODES, COL, ROLE)


class ColumnsMixin:
    # --- sidebar value columns ---

    def _columns_menu_extra(self, menu):
        """Analyze's part of the sidebar's value-columns menu: what Min / Max / Avg cover."""
        menus.menu_header(menu, "Stats")
        self._stats_menu(menu)

    def _columns_changed(self):
        self._update_now_values()
        self._update_stats()

    def _stats_menu(self, parent):
        """'Min / Max / Avg use' submenu: what part of the log the stats, heatmap and scatter use."""
        text = self.range_label.text()
        sub = menus.menu_sub(parent, "Min / Max / Avg use" + (f"  ({text})" if text else ""), "analytics")
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
