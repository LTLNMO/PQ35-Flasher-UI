"""The channels list, shared by Analyze (as its sidebar) and the Logger (as its main view,
see as_main) — one widget, so both stay the same:
Select channels ▾ + value-columns + range-bars + × buttons on top, then the channel list
(colour dot, name with its unit, value columns, a bar under each row).

The page owns the channels (which rows exist, which are ticked, what the values and bars
show) and the right-click menu on the list; this widget owns the look, the columns, the bars
switch, and showing / hiding the sidebar in its splitter. Settings are per page:
"<prefix>:sidebar_columns", "<prefix>:range_bars", "<prefix>:sidebar"."""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QMenu, QVBoxLayout, QWidget

from . import analyze_tools as T
from . import theme
from .channel_list import ChannelTree
from .widgets import collapsible_sidebar, scrollable
from .toolbar import ToolIcon

COLUMN_WIDTH = 76
SIDEBAR_WIDTH = 370  # room for Channel + Now / Min / Max
COLUMN_GLYPHS = {"group": "tag", "now": "my_location", "cmp": "compare", "delta": "change_history",
                 "min": "arrow_downward", "max": "arrow_upward", "avg": "functions"}


class ChannelSidebar(QWidget):
    """columns: ((key, header, tooltip), ...) value columns after the channel name.
    select_button: the page's Select channels ▾ button. With install(), it and the sidebar's
    toggle are a pair at the top left: the sidebar's first row while it shows, the front of the
    page's toolbar while it's hidden (the same spot as Logger's and Live View's either way); or set_select_button() later. extra_menu(menu): adds page-only items
    at the end of the value-columns menu."""

    columns_changed = Signal()

    def __init__(self, state, prefix, columns, select_button=None, empty_text="", columns_tip="",
                 bars_tip="", extra_menu=None, default=("now",), parent=None):
        super().__init__(parent)
        self.state, self.prefix, self.columns = state, prefix, tuple(columns)
        self.default, self.extra_menu = tuple(default), extra_menu
        self.col = {key: i + 1 for i, (key, _h, _t) in enumerate(self.columns)}
        self.select_button = select_button
        # Mac interface: a full-height column of its own (theme.MAC_STYLE #macPanel), as in the mockup.
        self.setObjectName("macPanel" if theme.is_mac() else "page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # paint the panel colour
        self.setMinimumWidth(240)
        sb = QVBoxLayout(self)
        sb.setContentsMargins(16, 16, 16 if theme.is_mac() else 8, 16)
        sb.setSpacing(10)

        self.tree = ChannelTree()
        self.tree.setHeaderLabels(["Channel"] + [h for _k, h, _t in self.columns])
        self.tree.empty_text = empty_text
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(36)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for key, _h, tip in self.columns:
            header.setSectionResizeMode(self.col[key], QHeaderView.ResizeMode.Fixed)
            header.resizeSection(self.col[key], COLUMN_WIDTH)
            self.tree.headerItem().setToolTip(self.col[key], tip)
            self.tree.headerItem().setTextAlignment(self.col[key], Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self.columns_menu().exec(header.mapToGlobal(pos)))
        self.apply_columns()
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.pick_row = QHBoxLayout()
        self.pick_row.setSpacing(8)
        if select_button is not None:
            self.pick_row.addWidget(select_button, 1)
        self.columns_button = ToolIcon("avg_pace", columns_tip or "Choose which values the "
                                            "list shows")
        self.columns_button.clicked.connect(lambda: T.popup_menu(self.columns_menu(), self.columns_button))
        self.pick_row.addWidget(self.columns_button)
        self.bars_button = ToolIcon("notes", bars_tip or "Show or hide the bars under each "
                                         "channel", checkable=True)
        self.bars_button.setChecked(bool(state.settings.get(f"{prefix}:range_bars", True)))
        self.tree.set_show_bars(self.bars_button.isChecked())
        self.bars_button.toggled.connect(self._bars_toggled)
        self.pick_row.addWidget(self.bars_button)
        # × closes the sidebar (the toolbar's sidebar button brings it back)
        self.close_button = ToolIcon("close", "Hide the channels sidebar")
        self.pick_row.addWidget(self.close_button)
        self.count_label = self.cursor_label = self.pane_title = self.search = None
        if theme.is_mac():  # Mockups/Swift native: "Channels · 3 of 27 shown", a search box, the cursor below
            from PySide6.QtWidgets import QLabel, QLineEdit
            from .widgets import muted
            self.mac_head = QHBoxLayout()
            self.mac_head.setSpacing(2)
            head = QVBoxLayout()
            head.setSpacing(0)
            title = self.pane_title = QLabel("Channels")
            title.setObjectName("macPaneTitle")
            head.addWidget(title)
            self.count_label = muted("")
            head.addWidget(self.count_label)
            self.mac_head.addLayout(head, 1)
            sb.addLayout(self.mac_head)
            self.search = QLineEdit()
            self.search.setPlaceholderText("Search channels")
            self.search.setClearButtonEnabled(True)
            self.search.textChanged.connect(self._filter)
            sb.addWidget(self.search)
        sb.addLayout(self.pick_row)
        sb.addWidget(self.tree, 1)
        if theme.is_mac():
            from .widgets import muted
            self.cursor_label = muted("")
            sb.addWidget(self.cursor_label)
        self.splitter = self.toolbar = self.content_layout = None

    # --- Mac interface extras ---

    def mac_header_buttons(self):
        """Mac (Analyze), mac_shell's layout rule 5: the list is a full-height column. Its first row
        (ROW_H, at row 1's y, 24 px in) is Select channels ▾ + the toggle (install / _shown), then
        the list's buttons (columns, bars) at the right; the search, the list. Select channels
        (3/20) says how many are shown, so no "Channels · n of N" heading."""
        from .mac_shell import column, first_row
        for w in (self.columns_button, self.bars_button, self.close_button):
            self.pick_row.removeWidget(w)
        self.close_button.hide()
        self.pane_title.hide()
        self.count_label.hide()
        self.count_label = None
        if self.select_button is not None:
            self.pick_row.removeWidget(self.select_button)
        self.layout().removeItem(self.pick_row)
        self.mac_head.setSpacing(8)
        first_row(self.mac_head)
        for w in (self.columns_button, self.bars_button):
            self.mac_head.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        column(self)
        self._home = self.mac_head

    def set_count(self, shown, total):
        if self.count_label is not None:
            self.count_label.setText(f"{shown} of {total} shown" if total else "No log open")

    def set_cursor(self, seconds):
        if self.cursor_label is not None:
            self.cursor_label.setText(f"Cursor {seconds:.1f} s")

    def _filter(self, text):
        """Search box: show only the rows whose name contains the text."""
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(bool(needle) and needle not in item.text(0).lower())

    # --- placing it in the page ---

    def set_select_button(self, button):
        """For pages whose Select channels button needs the tree first (it's made after this)."""
        self.select_button = button
        self.pick_row.insertWidget(0, button, 1)

    def as_main(self):
        """Use the list as a page's main view instead of a sidebar (Logger): no frame margins,
        no ×; the page puts columns_button and bars_button (and its Select channels) in its
        own toolbar."""
        self.layout().setContentsMargins(0, 0, 0, 0)
        for w in (self.columns_button, self.bars_button, self.close_button):
            self.pick_row.removeWidget(w)
        self.close_button.hide()
        self.layout().removeItem(self.pick_row)
        self.setMinimumWidth(0)
        # Mac: the page header names the page; the list is the whole page (the mockup's Logger).
        for w in (self.pane_title, self.count_label, self.search, self.cursor_label):
            if w is not None:
                w.hide()

    def install(self, splitter, content, content_layout, toolbar, sidebar_button):
        """Put the sidebar and the page's content in `splitter` and wire up showing / hiding:
        `sidebar_button` (checkable) toggles it. Select channels ▾ and the toggle go together, top
        left: the sidebar's first row while it shows, the front of `toolbar` while it's hidden
        (user 2026-11-23: Select channels in one spot on every page, its toggle beside it)."""
        self.splitter, self.toolbar, self.content_layout = splitter, toolbar, content_layout
        self.toggle_button = sidebar_button
        if not hasattr(self, "_home"):  # Classic: the row above the list
            self._home = self.pick_row
            self.close_button.hide()  # the toggle is right there beside Select channels
        splitter.addWidget(scrollable(self))
        splitter.addWidget(scrollable(content))
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        if theme.is_mac():
            from .mac_shell import column_split
            column_split(splitter)
        else:
            splitter.setSizes([SIDEBAR_WIDTH, 930])
        splitter.setCollapsible(1, False)
        self.close_button.clicked.connect(lambda: sidebar_button.setChecked(False))
        # The Mac interface keeps its own width (its column is narrower, as in the mockup).
        key = self.prefix + (":mac" if theme.is_mac() else "")
        collapsible_sidebar(splitter, self.state.settings, key, "Channels", button=sidebar_button)
        splitter.sidebar_toggle.shown_changed.connect(self._shown)
        self._shown(not splitter.sidebar_toggle.collapsed())

    def _shown(self, shown):
        """The sidebar has just appeared (start of its slide in) or gone (end of its slide out).
        The content's left edge needs the full 16px gutter while it's hidden; that happens at the
        same moment the 8px divider comes or goes, so nothing jumps. Select channels stays put."""
        if theme.is_mac():
            from .mac_shell import main_layout
            main_layout(self.content_layout)  # the main area beside the column (mac_shell rules)
        else:
            self.content_layout.setContentsMargins(8 if shown else 16, 16, 16, 16)
        pair = [w for w in (self.select_button, self.toggle_button) if w is not None]
        for w in pair:
            self._home.removeWidget(w)
            self.toolbar.removeWidget(w)
        for i, w in enumerate(pair):
            if shown:
                stretch = 1 if w is self.select_button and self._home is self.pick_row else 0
                self._home.insertWidget(i, w, stretch)
            else:
                self.toolbar.insertWidget(i, w)
            w.show()

    def fit_width(self):
        """Never narrower than the top row (Select channels + its buttons), so that row isn't
        cut off and the sidebar doesn't scroll sideways. Call after Select channels' text changes."""
        def fit():
            toggle = getattr(self.splitter, "sidebar_toggle", None)
            sizes = self.splitter.sizes() if self.splitter else []
            if toggle is None or toggle.collapsed() or not sizes:
                return
            need = toggle._content_width()
            if 0 < sizes[0] < need:
                self.splitter.setSizes([need, max(sum(sizes) - need, 0)])
        QTimer.singleShot(0, fit)  # once the new text has been measured

    # --- value columns and bars ---

    def visible_columns(self):
        saved = self.state.settings.get(f"{self.prefix}:sidebar_columns")
        keys = [k for k, _h, _t in self.columns]
        return [k for k in (saved if isinstance(saved, list) else self.default) if k in keys]

    def apply_columns(self):
        shown = set(self.visible_columns())
        for key, _h, _t in self.columns:
            self.tree.setColumnHidden(self.col[key], key not in shown)
        # One value per row reads like a label; a header only helps once there are several.
        self.tree.setHeaderHidden(len(shown) <= 1)

    def columns_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        shown = self.visible_columns()
        T.menu_header(menu, "Values in the list")
        for key, header, tip in self.columns:
            T.menu_toggle(menu, f"{header}  —  {tip}", COLUMN_GLYPHS.get(key, "view_column"), key in shown,
                          lambda on, k=key: self._toggle_column(k, on))
        names = [h for k, h, _t in self.columns if k in self.default]
        T.menu_action(menu, "Reset to " + (" + ".join(names) if len(names) > 1 else f"{names[0]} only"),
                      "restart_alt", lambda: self.set_columns(list(self.default)))
        if self.extra_menu:
            self.extra_menu(menu)
        return menu

    def _toggle_column(self, key, on):
        shown = [k for k in self.visible_columns() if k != key]
        if on:
            shown.append(key)
        order = [k for k, _h, _t in self.columns]
        self.set_columns(sorted(shown, key=order.index))

    def set_columns(self, keys):
        self.state.settings.set(f"{self.prefix}:sidebar_columns", keys)
        self.apply_columns()
        self.columns_changed.emit()

    def _bars_toggled(self, on):
        self.state.settings.set(f"{self.prefix}:range_bars", on)
        self.tree.set_show_bars(on)
