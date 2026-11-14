"""Split view for Analyze (like Datazap's Split): stacked chart rows sharing the time axis.
Each row starts with one channel on its own real-unit y axis. Drag a channel's name (or one
from the sidebar) onto the middle of a row to plot them together, or near a row's top / bottom
edge for a row of its own there; the grip at a row's top left drags the whole row the same
way. The right-click menu does the same. Drag the gaps between rows to resize them; rows keep
their heights when channels move. Rows with several channels follow the Axis setting (Shared / Independent),
like the Single graph. Zoom, pan and the cursor stay in step with the Single graph, so the
timeline, stats range and sidebar values work the same."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QFont
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsPixmapItem, QGraphicsSimpleTextItem,
                               QGraphicsTextItem, QLabel, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from . import graphscale, graphstyle
from .channel_list import CHANNEL_MIME

ROW_MIN_H = 90      # px per row; more rows than fit scroll
AXIS_W = 56         # fixed left-axis width so every row's plot area lines up
OWN_AXIS_W = 52     # each extra (own) axis on the right; every row reserves the same room
MIME = CHANNEL_MIME  # channel names, one per line (Split labels, row grips and the sidebar)
EDGE = 0.28         # top / bottom share of a row where a drop makes a new row instead of joining


from .units import fmt as _fmt  # one number style on every tab


def _drag_picture(names, colors):
    """What follows the pointer while dragging: a rounded chip with each channel's dot + name."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFontMetrics, QPainter, QPen, QPixmap
    from . import theme
    c = theme.colors()
    f = QApplication.font()
    fm = QFontMetrics(f)
    shown = names[:4] + ([f"+{len(names) - 4} more"] if len(names) > 4 else [])
    texts = [fm.elidedText(n, Qt.TextElideMode.ElideRight, 260) for n in shown]
    line_h = fm.height() + 4
    w = int(max(fm.horizontalAdvance(t) for t in texts) + 34)
    h = int(line_h * len(texts) + 10)
    dpr = QApplication.instance().devicePixelRatio()
    px = QPixmap(int(w * dpr), int(h * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(c["accent"]), 1.5))
    bg = QColor(c["surface"])
    bg.setAlpha(235)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 9, 9)
    p.setFont(f)
    for k, (name, text) in enumerate(zip(shown, texts)):
        y = 5 + k * line_h
        if name in colors:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colors[name]))
            p.drawEllipse(QRectF(11, y + line_h / 2 - 4, 8, 8))
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(26, y, w - 30, line_h), Qt.AlignmentFlag.AlignVCenter, text)
    p.end()
    return px


class _Draggable:
    """Press and drag to move `names` (channel names) to another place in Split."""

    TIP = ""

    def _setup(self, view, names):
        self.view, self.names = view, list(names)  # view = this item's _Row
        self._press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(self.TIP)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, ev):
        self._press = ev.screenPos()
        ev.accept()  # don't move the cursor or pan

    def mouseMoveEvent(self, ev):
        if self._press is None or (ev.screenPos() - self._press).manhattanLength() < \
                QApplication.startDragDistance():
            return
        self._press = None
        mime = QMimeData()
        mime.setData(MIME, "\n".join(self.names).encode())
        split = self.view.view  # self.view is this item's row; its .view is the SplitView
        drag = QDrag(self.view)
        drag.setMimeData(mime)
        pic = _drag_picture(self.names, {n: split.page.channel_color(n) for n in self.names})
        drag.setPixmap(pic)
        drag.setHotSpot(QPoint(14, int(pic.height() / pic.devicePixelRatio() / 2)))
        split._dragging(self.view, True)
        drag.exec(Qt.DropAction.MoveAction)
        split._dragging(self.view, False)
        # The drop only noted where the channel goes: rebuilding the rows now would delete this
        # item (and its row) while Qt is still inside their mouse handler → crash. So apply it
        # once this event has fully unwound.
        QTimer.singleShot(0, split.apply_pending_move)

    def mouseReleaseEvent(self, ev):
        self._press = None


class _ChannelLabel(_Draggable, QGraphicsTextItem):
    """A row's channel name + value; drag it onto another row to plot them together, or to a
    row's top / bottom edge for a row of its own."""

    TIP = ("Drag onto another row to plot them together, or near a row's top or bottom edge "
           "for a row of its own. Right-click for more.")

    def __init__(self, view, names, parent):
        QGraphicsTextItem.__init__(self, parent)
        self._setup(view, names)
        # The dot + name are set once (restyle); only these two plain texts change as the cursor
        # moves, which is far cheaper than re-laying-out the formatted text every time.
        self.value_item = QGraphicsSimpleTextItem(self)
        self.cmp_item = QGraphicsSimpleTextItem(self)
        for item in (self.value_item, self.cmp_item):
            item.setToolTip(self.TIP)
        self._shown = None

    def restyle(self, color):
        """Dot (channel colour) + name, in the current theme and font."""
        from . import theme
        c = theme.colors()
        # table cells so the small dot sits centred on the name, not on its baseline
        self.setHtml(
            f"<table cellspacing='0' cellpadding='0'><tr>"
            f"<td valign='middle' style='color:{color}; "
            f"font-size:{self.font().pointSizeF() * 0.6:.1f}pt; padding-right:5px'>&#9679;</td>"
            f"<td valign='middle' style='color:{c['text']}'>{self.names[0]}&nbsp;&nbsp;</td>"
            f"</tr></table>")
        bold = QFont(self.font())
        bold.setBold(True)
        self.value_item.setFont(bold)
        self.value_item.setBrush(QColor(c["text"]))
        self.cmp_item.setFont(self.font())
        self.cmp_item.setBrush(QColor(c["muted"]))
        self._shown = None

    def set_value(self, text, compared=""):
        if self._shown == (text, compared):
            return
        self._shown = (text, compared)
        self.value_item.setText(text)
        self.cmp_item.setText(compared)
        rect = self.boundingRect()
        x = rect.width() - self.document().documentMargin()
        y = (rect.height() - self.value_item.boundingRect().height()) / 2
        self.value_item.setPos(x, y)
        self.cmp_item.setPos(x + self.value_item.boundingRect().width() + 6, y)


GRIP = 22  # px: the row grip's size; the channel labels start right after it


class _RowGrip(_Draggable, QGraphicsPixmapItem):
    """Grip at a row's top left, before its channel names: drags the whole row (to reorder it,
    or onto another row to join them)."""

    TIP = ("Drag to move this row: near another row's top or bottom edge to put it there, "
           "onto the middle to join them.")

    def __init__(self, view, names, parent):
        QGraphicsPixmapItem.__init__(self, parent)
        self._setup(view, names)
        self.recolor()

    def recolor(self):
        from . import theme
        from .main_window import _material_icon
        self.setPixmap(_material_icon("\ue945", GRIP, theme.colors()["muted"]).pixmap(GRIP, GRIP))


class _Row(pg.PlotWidget):
    """One Split row; takes channels dropped onto it."""

    def __init__(self, view, index):
        super().__init__()
        self.view, self.index = view, index
        self.setAcceptDrops(True)
        self.bar = QFrame(self)  # drop hint: a line on the edge where the new row would go
        self.bar.setFixedHeight(3)
        self.bar.hide()

    def _ours(self, ev):
        return ev.mimeData().hasFormat(MIME)

    def _zone(self, ev):
        y = ev.position().y()
        h = max(self.height(), 1)
        return "above" if y < h * EDGE else "below" if y > h * (1 - EDGE) else "join"

    def dragEnterEvent(self, ev):
        if self._ours(ev):
            ev.acceptProposedAction()
            self.view._drop_hint(self.index, self._zone(ev))
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if self._ours(ev):
            ev.acceptProposedAction()
            self.view._drop_hint(self.index, self._zone(ev))
        else:
            super().dragMoveEvent(ev)

    def dragLeaveEvent(self, ev):
        self.view._drop_hint(self.index, None)
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev):
        if not self._ours(ev):
            return super().dropEvent(ev)
        ev.acceptProposedAction()
        self.view._drop_hint(self.index, None)
        names = [n for n in bytes(ev.mimeData().data(MIME)).decode().split("\n") if n]
        self.view._pending_move = (names, self.index, self._zone(ev))
        # Dragged from a Split label or grip: that label applies it once its drag has ended
        # (rebuilding now would delete it mid-drag → crash). From the sidebar: apply it here,
        # once this drop event has unwound.
        if not isinstance(ev.source(), _Row):
            QTimer.singleShot(0, self.view.apply_pending_move)


class SplitView(QWidget):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        from . import theme
        self.page = page
        self.rows = []          # dicts: names, plot, widget, curves, lines (line values), labels...
        self.groups = []        # channel names per row, kept across rebuilds
        self._heights = {}      # row heights the user dragged to, by channel name
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.graphs = QSplitter(Qt.Orientation.Vertical)
        self.graphs.setChildrenCollapsible(False)
        self.graphs.setHandleWidth(5)
        self.graphs.splitterMoved.connect(lambda *_: self._remember_heights())
        self.graphs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scroll.setWidget(self.graphs)
        layout.addWidget(self.scroll, 1)
        self._paint()
        theme.on_apply(self._paint)

    # --- rows ---

    def _sync_groups(self, names):
        """Keep the user's rows and their order, drop unticked channels, and give newly ticked
        ones a row each at the bottom (in sidebar order)."""
        shown = set(names)
        groups = [[n for n in g if n in shown] for g in self.groups]
        groups = [g for g in groups if g]
        placed = {n for g in groups for n in g}
        groups += [[n] for n in names if n not in placed]
        self.groups = groups

    def apply_pending_move(self):
        move, self._pending_move = getattr(self, "_pending_move", None), None
        if move:
            self.move_channels(*move)

    def move_channel(self, name, target):
        """Move a channel into row `target`, or to a row of its own under its current one."""
        if target is None:
            here = next((i for i, g in enumerate(self.groups) if name in g), len(self.groups) - 1)
            self.move_channels([name], here, "below")
        else:
            self.move_channels([name], target, "join")

    def move_channels(self, names, target, where="join"):
        """Move channels to row `target`: into it ("join"), or as a new row of their own just
        above or below it ("above" / "below"). A whole row moved keeps its height."""
        names = [n for n in names if any(n in g for g in self.groups)]
        if not names or not 0 <= target < len(self.groups):
            return
        tgt = self.groups[target]
        whole_row = any(set(g) == set(names) for g in self.groups)
        if where == "join" and set(names) <= set(tgt):
            return
        if where != "join" and set(tgt) == set(names):
            return  # a row dropped on its own edge: already there
        groups, tgt_new = [], None
        for g in self.groups:
            ng = [n for n in g if n not in names]
            if g is tgt:
                tgt_new = ng
            groups.append(ng)
        if where == "join":
            tgt_new.extend(names)
        else:
            pos = next(i for i, g in enumerate(groups) if g is tgt_new)
            groups.insert(pos + (where == "below"), list(names))
        if where == "join" or not whole_row:
            for n in names:  # they're in a different row now: that row's height applies
                self._heights.pop(n, None)
        self.groups = [g for g in groups if g]
        self.page._split_dirty = True
        self.rebuild()

    def move_row(self, index, step):
        """Swap a row with the one above (step -1) or below (+1)."""
        j = index + step
        if 0 <= index < len(self.groups) and 0 <= j < len(self.groups):
            self.groups[index], self.groups[j] = self.groups[j], self.groups[index]
            self.rebuild()

    def split_row(self, index):
        """Break a row into one row per channel."""
        g = self.groups[index]
        self.groups[index:index + 1] = [[n] for n in g]
        for n in g:
            self._heights.pop(n, None)
        self.rebuild()

    def equal_heights(self):
        self._heights = {}
        if self.rows:
            self.graphs.setSizes([1000] * len(self.rows))

    def _remember_heights(self):
        for r, h in zip(self.rows, self.graphs.sizes()):
            for n in r["names"]:
                self._heights[n] = h

    def _row_sizes(self):
        """Each row's remembered height (from any of its channels); new rows get the average."""
        known = [next((self._heights[n] for n in g if n in self._heights), None) for g in self.groups]
        have = [h for h in known if h]
        fill = sum(have) / len(have) if have else 1000
        return [int(h or fill) for h in known]

    # --- building ---

    def rebuild(self):
        """Redraw every row from the page's ticked channels (colours as on the Single graph)."""
        page = self.page
        for r in self.rows:
            r["widget"].setParent(None)
            r["widget"].deleteLater()
        self.rows = []
        while self.graphs.count():
            w = self.graphs.widget(0)
            w.setParent(None)
            w.deleteLater()
        if page.times is None:
            return
        names = [n for n in page._checked_channels()
                 if page.columns.get(n) is not None and n not in page.hidden]
        if not names:
            empty = QLabel("No channels on the graph. Use Select channels to add some.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.graphs.addWidget(empty)
            self.graphs.setMinimumHeight(0)
            return
        self._sync_groups(names)
        from .playback_page import AXIS_SCALE
        mode = AXIS_SCALE[page._axis()]
        plans = []
        for g in self.groups:
            ranges = {n: graphscale.value_range(page.columns[n], page._compare_column(n)) for n in g}
            # A row of one channel always shows it in its real units, on its own axis.
            plans.append(graphscale.plan(mode, ranges, page.own_axis) if len(g) > 1
                         else graphscale.plan("real", ranges))
        n_extra = max((len(p[2]) for p in plans), default=0)
        width = graphstyle.width(page.state.settings)
        chosen = page._selected_channel()
        f = graphstyle.font(page.state.settings)
        first = None
        last = len(self.groups) - 1
        # Built hidden: otherwise every added row re-lays out (and resizes) all the others.
        hidden_before = self.graphs.isHidden()
        self.graphs.hide()
        for i, (g, (fitted, maps, extra)) in enumerate(zip(self.groups, plans)):
            widget = _Row(self, i)
            widget.setMinimumHeight(ROW_MIN_H)
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.installEventFilter(page)  # arrow keys step the cursor
            plot = widget.getPlotItem()
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.setMouseEnabled(x=True, y=False)
            plot.showGrid(x=True, y=True, alpha=graphstyle.grid_alpha(page.state.settings))
            left = plot.getAxis("left")
            left.setWidth(AXIS_W)
            left.setStyle(tickFont=f)
            bottom = plot.getAxis("bottom")
            bottom.setStyle(tickFont=f, showValues=i == last)
            if i == last:
                plot.setLabel("bottom", "Time (s)")
            else:
                bottom.setHeight(6)
            curves, lines, colors = {}, {}, {}
            for name in g:
                color = page.curve_data[name][1] if name in page.curve_data else \
                    graphstyle.color(len(colors), page.state.settings)
                colors[name] = color
                ys = maps[name](page.columns[name])
                lines[name] = ys
                curve = plot.plot(page.times, ys,
                                  pen=pg.mkPen(color=color, width=width * (2 if name == chosen else 1)))
                graphstyle.lean(curve)
                curves[name] = curve
                other = page._compare_column(name)
                if other is not None:
                    cmp = plot.plot(page.compare["times"] + page.offset.value(), maps[name](other),
                                    pen=pg.mkPen(color=color, width=max(width * 0.75, 1),
                                                 style=Qt.PenStyle.DashLine))
                    graphstyle.lean(cmp)
            axes = [graphscale.real_axis(*extra[n], colors[n]) for n in g if n in extra]
            for axis in axes:
                axis.setWidth(OWN_AXIS_W)
                axis.setStyle(tickFont=f)
            while len(axes) < n_extra:  # same room on every row, so the time axes line up
                blank = pg.AxisItem("right")
                blank.setWidth(OWN_AXIS_W)
                blank.setStyle(showValues=False)
                blank.setPen(pg.mkPen(None))
                axes.append(blank)
            graphscale.add_axes(plot, axes)
            graphstyle.cache_axes(plot)  # axes + grid only redraw on zoom, not per cursor move
            vb = plot.getViewBox()

            def reset_values(vb=vb, fitted=fitted):
                if fitted:
                    vb.enableAutoRange(axis="y", enable=False)
                    vb.setYRange(-4, 106, padding=0)
                else:
                    vb.setAutoVisible(y=True)   # each row fits the part of its lines on screen
                    vb.setDefaultPadding(0.08)  # with room above / below so peaks aren't on the edge
                    vb.enableAutoRange(axis="y", enable=True)
            reset_values()
            # scroll over the graph = time; over the value axis = this row's values only
            graphscale.axis_zoom(vb, reset_values)
            graphscale.value_axis(left)
            # Time comes from the Single graph; a row auto-ranging x would push the whole log
            # onto the others (they're linked) and reset the zoom.
            vb.enableAutoRange(axis="x", enable=False)
            line = pg.InfiniteLine(angle=90, movable=False)
            line.setZValue(20)
            plot.addItem(line, ignoreBounds=True)
            dots = pg.ScatterPlotItem(size=8, pxMode=True)
            dots.setZValue(21)
            plot.addItem(dots, ignoreBounds=True)
            labels = {}
            for name in g:
                label = _ChannelLabel(widget, [name], vb)  # child of the view box: stays put while panning
                label.setZValue(30)
                label.setFont(graphstyle.font(page.state.settings, 1))
                labels[name] = label
            grip = _RowGrip(widget, g, vb)  # top left, before the names: drags the whole row
            grip.setZValue(30)
            scene = widget.scene()
            scene.sigMouseClicked.connect(lambda ev, k=i: self._mouse_clicked(ev, k))
            widget._hover = pg.SignalProxy(scene.sigMouseMoved, rateLimit=60,
                                           slot=lambda ev, k=i: self._mouse_moved(ev, k))
            if first is None:
                first = plot
                vb.sigXRangeChanged.connect(lambda *_: self._split_range_changed())
            else:
                plot.setXLink(first)
            self.graphs.addWidget(widget)
            self.rows.append({"names": g, "plot": plot, "widget": widget, "curves": curves,
                              "lines": lines, "colors": colors, "line": line, "dots": dots,
                              "labels": labels, "fitted": fitted, "extra": extra, "grip": grip})
        self.graphs.setMinimumHeight(ROW_MIN_H * len(self.rows))
        self.graphs.setVisible(not hidden_before)
        self.graphs.setSizes(self._row_sizes())
        self._paint()
        self.sync_from_page()
        self.set_cursor()

    def highlight(self):
        width = graphstyle.width(self.page.state.settings)
        chosen = self.page._selected_channel()
        for r in self.rows:
            for name, curve in r["curves"].items():
                curve.setPen(pg.mkPen(color=r["colors"][name], width=width * (2 if name == chosen else 1)))

    def _paint(self):
        from . import theme
        c = theme.colors()
        self.graphs.setStyleSheet(f"QSplitter::handle {{ background: {c['border']}; }}"
                                  f"QSplitter {{ background: {c['surface']}; }}")
        for r in self.rows:
            r["widget"].setBackground(c["surface"])
            for name in ("left", "bottom"):
                axis = r["plot"].getAxis(name)
                axis.setPen(c["muted"])
                axis.setTextPen(c["muted"])
            r["line"].setPen(pg.mkPen(c["text"], width=1.5))
            r["grip"].recolor()
            for name, label in r["labels"].items():
                label.restyle(r["colors"][name])
            r["widget"].bar.setStyleSheet(f"background: {c['accent']}; border-radius: 1px;")
        self.set_cursor()

    def _dragging(self, row, on):
        """While a row's channel (or the row) is being dragged, show that row faded."""
        from . import theme
        c = theme.colors()
        self._drag_row = row if on else None
        if on:
            row.setBackground(self._faded())
        else:
            try:
                row.setBackground(c["surface"])
            except RuntimeError:  # row already rebuilt away
                pass

    def _faded(self):
        from . import theme
        faded = QColor(theme.colors()["border"])
        faded.setAlpha(90)
        return faded

    def _drop_hint(self, index, zone):
        """Show where a drop would go: the row tinted (join it) or a line on its top / bottom
        edge (a new row there). zone None clears it."""
        from . import theme
        if index >= len(self.rows):
            return
        c = theme.colors()
        w = self.rows[index]["widget"]
        rest = self._faded() if w is getattr(self, "_drag_row", None) else c["surface"]
        w.setBackground(c["accent_bg"] if zone == "join" else rest)
        if zone in ("above", "below"):
            w.bar.setGeometry(0, 0 if zone == "above" else w.height() - 3, w.width(), 3)
            w.bar.show()
            w.bar.raise_()
        else:
            w.bar.hide()

    # --- zoom and cursor, kept in step with the Single graph ---

    def sync_from_page(self):
        """Show the same stretch of time as the Single graph."""
        if not self.rows or self._syncing:
            return
        (x0, x1), _ = self.page.plot.getPlotItem().vb.viewRange()
        self._syncing = True
        self.rows[0]["plot"].setXRange(x0, x1, padding=0)
        self._syncing = False

    def _split_range_changed(self):
        if self._syncing or not self.rows:
            return
        (x0, x1), _ = self.rows[0]["plot"].getViewBox().viewRange()
        self._syncing = True
        self.page.plot.setXRange(x0, x1, padding=0)  # the timeline and stats follow from there
        self._syncing = False

    def set_cursor(self):
        page = self.page
        if page.times is None or not self.rows:
            return
        idx = page._cursor_index()
        t = float(page.times[idx])
        outline = pg.mkPen("#00000080", width=1)
        for r in self.rows:
            r["line"].setValue(t)
            xs, ys, brushes = [], [], []
            y = 0.0
            for name in r["names"]:
                val = page.columns[name][idx]
                drawn = r["lines"][name][idx]
                if np.isfinite(drawn):
                    xs.append(t)
                    ys.append(float(drawn))
                    brushes.append(pg.mkBrush(r["colors"][name]))
                cmp = page._compare_value(name, t)
                label = r["labels"][name]
                label.set_value("–" if not np.isfinite(val) else _fmt(float(val)),
                                f"(compared {_fmt(cmp)})" if cmp is not None else "")
                label.setPos(GRIP + 2, y)
                y += label.boundingRect().height() - 6
            first = next(iter(r["labels"].values()), None)
            if first is not None:  # grip centred on the first name's line
                r["grip"].setPos(4, max((first.boundingRect().height() - GRIP) / 2, 0))
            r["dots"].setData(xs, ys, brush=brushes, pen=outline)

    def _time_at(self, k, pos):
        vb = self.rows[k]["plot"].getViewBox() if k < len(self.rows) else None
        if vb is not None and vb.sceneBoundingRect().contains(pos):
            return float(vb.mapSceneToView(pos).x())
        return None

    def _mouse_moved(self, event, k):
        page = self.page
        if page.times is None or not page.follow_check.isChecked() or k >= len(self.rows):
            return
        if self.rows[k]["widget"].scene().mouseGrabberItem() is not None:
            return  # panning
        t = self._time_at(k, event[0])
        if t is not None:
            page._move_cursor_to(t)

    def _row_menu(self, k, menu):
        """Row items on top of the page's right-click menu."""
        from . import analyze_tools as T
        first = menu.actions()[0] if menu.actions() else None
        r = self.rows[k]
        added = []
        for name in r["names"]:
            sub = T.menu_sub(menu, f"Move {name} to", "low_priority")
            for j, other in enumerate(self.rows):
                if j == k:
                    continue
                T.menu_action(sub, f"Row {j + 1}: " + ", ".join(other["names"]), "splitscreen",
                              lambda _=False, n=name, j=j: self.move_channel(n, j))
            if len(r["names"]) > 1:
                T.menu_action(sub, "A row of its own", "add", lambda _=False, n=name: self.move_channel(n, None))
            if sub.isEmpty():
                sub.addAction("Only one row").setEnabled(False)
            added.append(sub.menuAction())
        if k > 0:
            added.append(T.menu_action(menu, "Move row up", "arrow_upward", lambda: self.move_row(k, -1)))
        if k < len(self.rows) - 1:
            added.append(T.menu_action(menu, "Move row down", "arrow_downward", lambda: self.move_row(k, 1)))
        if len(self.rows) > 1:
            added.append(T.menu_action(menu, "Equal row heights", "density_medium", self.equal_heights))
        if len(r["names"]) > 1:
            added.append(T.menu_action(menu, "Split this row (one channel per row)", "call_split",
                                       lambda: self.split_row(k)))
            if not r["fitted"]:
                for name in r["names"]:
                    added.append(T.menu_toggle(menu, f"Own y axis: {name}", "stacked_line_chart",
                                               name in r["extra"],
                                               lambda _on, n=name: self.page.toggle_own_axis(n)))
        if first is not None:  # move them above the page's items, a separator between
            for act in added:
                menu.removeAction(act)
                menu.insertAction(first, act)
            menu.insertSeparator(first)

    def _mouse_clicked(self, event, k):
        if k >= len(self.rows):
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            menu = self.page._graph_menu()
            self._row_menu(k, menu)
            menu.exec(event.screenPos().toPoint())
            return
        if self.page.times is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if event.double():
            self.page._show_all()
            return
        r = self.rows[k]
        if not r["fitted"] and len(r["names"]) > 1:
            hit = graphscale.line_at(r["plot"].getViewBox(), event.scenePos(), self.page.times, r["lines"])
            if hit is not None:  # Shared: clicking a line gives it its own axis (again: back)
                self.page.toggle_own_axis(hit)
                return
        t = self._time_at(k, event.scenePos())
        if t is not None:
            r["widget"].setFocus()  # arrow keys step the cursor
            self.page._move_cursor_to(t)
