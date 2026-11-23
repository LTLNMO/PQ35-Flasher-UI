"""Analyze (PlaybackPage), the Raw data view. A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMenu, QVBoxLayout, QWidget,
)
from . import graphscale, graphstyle, log_export
from . import analyze_tools, theme
from .channel_list import COLOR_ROLE
from . import menus
from .playback_common import (ROLE, AXIS_SCALE, RAW_COL_MIN)


class RawMixin:
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
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if item is not None:
            name = item.data(0, ROLE)
            menus.menu_header(menu, "This channel")
            hidden = name in self.hidden
            menus.menu_action(menu, "Show line" if hidden else "Hide line",
                          "visibility" if hidden else "visibility_off", lambda: self.toggle_hidden(name))
            menus.menu_toggle(menu, "Own y axis", "stacked_line_chart", name in self.own_axis,
                          lambda _on: self.toggle_own_axis(name), enabled=not self._fitted,
                          tip="Draw this channel on its own y axis in its colour (Shared axis). "
                              "Same as clicking its line on the graph.")
            menus.menu_action(menu, "Remove from graph", "close",
                          lambda: item.setCheckState(0, Qt.CheckState.Unchecked))
        menus.menu_header(menu, "All channels")
        menus.menu_action(menu, "Select channels…", "checklist", lambda: self.picker.open_for(self.select_button))
        menus.menu_action(menu, "Remove all", "clear_all", lambda: self._set_all_checked(False))
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
