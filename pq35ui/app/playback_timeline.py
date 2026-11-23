"""Analyze (PlaybackPage), the overview timeline under the graph. A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor
from . import graphscale, graphstyle
from . import theme


class TimelineMixin:
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
