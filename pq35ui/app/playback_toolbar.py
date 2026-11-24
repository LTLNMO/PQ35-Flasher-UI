"""Analyze (PlaybackPage), the toolbar and its menus (Classic row, Mac title bar + row 1). A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QMenu, QHBoxLayout, QPushButton,
    QVBoxLayout,
)
from . import ai, log_export, recent_logs
from . import analyze_tools, smart_presets, theme
from .storage import datalogs_dir
from .widgets import muted
from .toolbar import ToolIcon
from . import menus
from .playback_common import (ROLE, AXIS_MODES, AXIS_SCALE)


class ToolbarMixin:
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

    OPEN_TIP = ("Open a log: a recent recording, or a file from this app or another tool (the logs "
                "Datazap takes: COBB, MHD, VCDS, ME7Logger, Torque, MegaLogViewer, Integrated Engineering …)")

    def _classic_toolbar(self, rl):
        """Classic: [channels toggle] … legend, timeline, ƒ, Presets ▾, Axis, ⋮; the compare row and
        the banner; the view buttons, the log's name, Open ▾, Compare ▾; smart presets; the views."""
        self.open_button = ToolIcon("file_open", self.OPEN_TIP, dropdown=True)
        self.open_button.clicked.connect(self._open_menu)
        # speed_4_progress, by codepoint: the glyph is in the font but its name ligature draws junk.
        # It and Select channels are a pair at the top left (ChannelSidebar.install).
        self.sidebar_button = ToolIcon("\U000ffe94", "Show or hide the channels sidebar", checkable=True)
        ctrl = self.ctrl_row = QHBoxLayout()
        ctrl.setSpacing(8)  # one gap for every toolbar row (Logger, Live View too)
        ctrl.addWidget(self.sidebar_button)
        ctrl.addStretch(1)
        for w in (self.legend_button, self.timeline_button, self.math_button, self.presets_button,
                  self.axis_button, self.more_button):
            ctrl.addWidget(w)
        rl.addLayout(ctrl)
        rl.addWidget(self.compare_row)
        rl.addWidget(self.banner)
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(2)
        for b in self.view_buttons:
            view_row.addWidget(b)
        view_row.addSpacing(12)
        view_row.addWidget(self.file_label, 1)  # takes the rest of the row; long names get …
        view_row.addSpacing(6)
        view_row.addWidget(self.open_button)
        view_row.addSpacing(6)
        view_row.addWidget(self.compare_button)
        rl.addLayout(view_row)
        rl.addWidget(self.smart_bar)
        rl.addWidget(self.tabs, 1)

    def _mac_toolbar(self, rl):
        """Mac: the log's name as a button at the title bar's right end; row 1 = the views as a
        segmented control … Axis [Independent ▾] ◧ Compare ƒ Presets ▾ ⋯; the compare row, the
        smart presets, the banner, the views. Same buttons as Classic, placed once for the Mac
        (legend and timeline are ⋯ menu items here, never buttons)."""
        from PySide6.QtWidgets import QFrame, QSizePolicy
        from .mac_shell import adopt_bar, first_row, row_icons
        from .widgets import segment_track
        self.file_label.setParent(self)
        self.file_label.hide()
        pill = QPushButton(menus.menu_icon("description"), "Open a log…")
        pill.setObjectName("macFilePill")
        pill.setToolTip(self.OPEN_TIP)
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.clicked.connect(self._open_menu)
        self.open_button = pill
        seg = QFrame()
        segment_track(seg, QHBoxLayout(seg), self.view_buttons)  # the macOS segmented control
        self.sidebar.mac_header_buttons()  # list buttons into the column's heading row
        self.sidebar_button = ToolIcon("left_panel_close", "Show or hide the channels column", checkable=True)
        self.sidebar_button.setProperty("plain", True)  # a sidebar toggle: no grey square when on
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(pill)
        top.addStretch(1)
        adopt_bar(self, top)  # the title bar's right end (mac_shell.Header)
        # Row 1: Select channels ▾ parks at its front while the column is hidden (never in the title
        # bar); the column's toggle is the first right-hand icon, so it never moves (user 2026-11-23).
        views = self.ctrl_row = first_row(QHBoxLayout())
        views.addWidget(seg)
        views.addStretch(1)
        views.addWidget(muted("Axis"))
        self.axis_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        views.addWidget(self.axis_button)
        views.addSpacing(6)
        row_icons(views, tools=(self.sidebar_button, self.compare_button, self.math_button),
                  presets=self.presets_button, more=self.more_button)
        for w in (self.legend_button, self.timeline_button):  # switched from ⋯ instead
            w.setParent(self)
            w.hide()
        smart = QHBoxLayout()
        smart.setSpacing(10)
        smart.addWidget(self.smart_bar, 1)
        rl.addLayout(views)
        rl.addWidget(self.compare_row)
        rl.addLayout(smart)
        rl.addWidget(self.banner)
        rl.addWidget(self.tabs, 1)
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
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menus.menu_header(menu, "View")
        menus.menu_action(menu, "Reset view", "fit_screen", self._reset_view,
                      tip="The whole log across, every line back in view")
        sub = analyze_tools.StickyMenu("Channels", menu)
        menus.menu_sub(menu, "Channels", "checklist", sub)
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
        menus.menu_action(sub, "Remove all", "clear_all", lambda: self._set_all_checked(False))
        menus.menu_sub(menu, "Axis", "height", self._axis_menu(QMenu("Axis", menu)))
        menus.menu_header(menu, "Show")
        menus.menu_toggle(menu, "Legend", "list", self.legend_button.isChecked(), self.legend_button.setChecked)
        menus.menu_toggle(menu, "Timeline", "acute", self.timeline_button.isChecked(), self.timeline_button.setChecked)
        menus.menu_toggle(menu, "Follow mouse", "mouse", self.follow_check.isChecked(), self.follow_check.setChecked,
                      tip="Move the cursor as the mouse moves over the graph. Off: click to place it.")
        menus.menu_header(menu, "Stats")
        self._stats_menu(menu)
        menus.menu_header(menu, "More")
        menus.menu_action(menu, "Export graph…", "image", self._export_graph,
                      tip="Save the graph as an image (PNG, SVG) or its lines as CSV, or print it")
        menus.menu_sub(menu, "Axes and mouse", "open_with", self._view_menu())
        menus.menu_sub(menu, "Plot options", "tune", self.plot.getPlotItem().ctrlMenu)
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
        menus.menu_header(menu, "Smart presets")
        gen = self.smart
        menus.menu_action(menu, "Ask AI for a preset…", "auto_awesome", lambda: smart_presets.ask_dialog(self, gen),
                      enabled=self.times is not None and not gen.busy,
                      tip="Say what you want to look at; your AI picks the channels connected to it")
        for layout in gen.layouts:
            rows = len(layout["charts"])
            glyph = "warning" if layout.get("fault") else "auto_awesome" if layout.get("asked") \
                else smart_presets.icon_for(layout["name"])
            menus.menu_action(menu, f"{layout['name']}  ({rows} row{'s' if rows != 1 else ''})", glyph, lambda _=False, l=layout: self._apply_smart(l),
                          tip=layout.get("description", ""))
        if not gen.layouts:
            menu.addAction("Open a log to see them" if self.times is None else "None for this log").setEnabled(False)
        sm = menus.menu_sub(menu, "Smart preset options", "auto_awesome")
        menus.menu_action(sm, "Generating…" if gen.busy else "Regenerate with AI", "refresh",
                              lambda: (smart_presets.forget(gen.headers), gen.generate(self)),
                              enabled=bool(gen.headers) and ai.enabled(self.state.settings) and not gen.busy,
                              tip="Ask your AI again for this log's channels" if ai.enabled(self.state.settings)
                              else "Choose an AI in Settings → AI first")
        menus.menu_toggle(sm, "Show smart preset chips", "view_carousel",
                      smart_presets.bar_on(self.state.settings), self._smart_bar_toggled)
        menus.menu_toggle(sm, "Generate with AI automatically", "auto_awesome",
                      bool(self.state.settings.get(smart_presets.AI_KEY, True)),
                      lambda on: self.state.settings.set(smart_presets.AI_KEY, bool(on)),
                      tip="When a log with new channels opens, ask your AI for layouts (only channel names are sent)")
        if any(l.get("asked") for l in gen.layouts):
            menus.menu_action(sm, "Remove my Ask AI presets", "delete", gen.forget_asked)
        if gen.error:
            sm.addSeparator()
            sm.addAction(f"Last AI attempt: {gen.error[:70]}").setEnabled(False)
        saved = T.saved_presets(self.state.settings)
        menus.menu_header(menu, "Your presets")
        for name, names in saved.items():
            chans = T.resolve(names, headers)
            menus.menu_action(menu, f"{name}  ({len(chans)}/{len(names)})", "bookmark",
                          lambda _=False, c=chans: self._apply_channels(c), bool(chans),
                          tip="" if chans else "None of its channels are in this log")
        if not saved:
            menu.addAction("None saved yet").setEnabled(False)
        menu.addSeparator()
        have = bool(self._checked_channels())
        menus.menu_action(menu, "Save preset…", "bookmark_add", self._save_preset, have,
                      tip="Save the channels on the graph as a new preset")
        over = menu.addMenu(menus.menu_icon("save_as"), "Overwrite preset")
        over.menuAction().setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
        over.setToolTipsVisible(True)
        # Always opens, saying why when there's nothing to pick (a greyed item explains nothing).
        if not saved:
            over.addAction("No saved presets yet: use Save preset… first").setEnabled(False)
        elif not have:
            over.addAction("Put some channels on the graph first").setEnabled(False)
        for name in saved:
            menus.menu_action(over, name, "bookmark", lambda _=False, n=name: self._overwrite_preset(n), have)
        menus.menu_action(menu, "Manage presets…", "tune", self._manage_presets,
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
        menus.manage_presets(
            self, lambda: analyze_tools.saved_presets(settings),
            lambda old, new: analyze_tools.rename_preset(settings, old, new),
            lambda name: analyze_tools.delete_preset(settings, name),
            "No presets saved yet.\nPut channels on the graph, then Presets → Save preset….",
            tip=lambda _name, names: ", ".join(names))

    def _more_menu(self):
        menu = QMenu(self)
        loaded = self.times is not None
        menus.menu_header(menu, "This log")
        menus.menu_action(menu, "Log details", "info", lambda: self._show_file_details(self.more_button), loaded)
        menus.menu_action(menu, "Edit log…", "edit", self._edit_log, loaded)
        menus.menu_action(menu, f"Export… ({log_export.format_label(log_export.export_style(self.state.settings))})",
                          "ios_share", self._export, loaded,
                          tip="Save a copy in another format or place (the style is your last choice, or ⚙ → Saving in Logger)")
        menus.menu_header(menu, "View")
        menus.menu_action(menu, "Reset view", "fit_screen", self._reset_view, loaded)
        if theme.is_mac():  # their toolbar buttons are off the Mac top bar (four icons, as in the mockup)
            menus.menu_toggle(menu, "Legend", "list", self.legend_button.isChecked(), self.legend_button.setChecked,
                          tip="The channel chips under the graph")
            menus.menu_toggle(menu, "Timeline", "acute", self.timeline_button.isChecked(),
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
