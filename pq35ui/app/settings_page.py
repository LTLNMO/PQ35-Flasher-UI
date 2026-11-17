"""Settings page: theme (dark/light/black/system), accent & tint, font size, and more."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QListWidget, QListWidgetItem, QRadioButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .pages import Page
from .widgets import Card, muted

ACCENT_COLORS = (
    ("Orange", "#e8820c"),
    ("Amber", "#f5a623"),
    ("Red", "#ef4444"),
    ("Pink", "#ec4899"),
    ("Purple", "#a855f7"),
    ("Indigo", "#818cf8"),
    ("Blue", "#3b82f6"),
    ("Sky", "#38bdf8"),
    ("Teal", "#14b8a6"),
    ("Green", "#22c55e"),
)

THEMES = (("Dark", "dark"), ("Light", "light"), ("Black", "black"), ("System", "system"))

FONT_SIZES = (
    ("Small", 11),
    ("Normal", 13),
    ("Large", 15),
    ("Extra Large", 17),
)


class TabOrderList(QListWidget):
    """The tab bar as a strip of chips: drag to reorder (dividers too), untick to hide.
    Saves "tab_order" and "hidden_features", then calls on_change."""

    def __init__(self, settings, on_change):
        super().__init__()
        from PySide6.QtWidgets import QAbstractItemView, QListView
        self.settings, self.on_change = settings, on_change
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(3)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._fill()
        self.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._save))
        self.itemChanged.connect(lambda _item: self._save())
        from . import theme
        theme.on_apply(self._restyle)
        self._restyle()

    def _restyle(self):
        from . import theme
        c = theme.colors()
        self.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; outline: none; }}"
            f"QListWidget::item {{ background: {c['surface2']}; border: 1px solid {c['border']}; "
            f"border-radius: 8px; padding: 4px 8px; color: {c['text']}; }}"
            f"QListWidget::item:selected {{ border-color: {c['accent']}; background: {c['accent_bg']}; "
            f"color: {c['text']}; }}")

    def _fill(self):
        from .main_window import DIVIDER, NAV, tab_order
        from .pages import OPTIONAL_FEATURES, hidden_features
        labels = {k: label for k, _i, label, _c in NAV}
        optional = {k for k, _l in OPTIONAL_FEATURES}
        hidden = hidden_features(self.settings)
        self.blockSignals(True)
        self.clear()
        for key in tab_order(self.settings):
            item = QListWidgetItem("│" if key == DIVIDER else labels[key])
            item.setData(Qt.ItemDataRole.UserRole, key)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
            if key in optional:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setCheckState(Qt.CheckState.Unchecked if key in hidden else Qt.CheckState.Checked)
            if key == DIVIDER:
                item.setToolTip("Divider: drag it to group the tabs")
            elif key not in optional:
                item.setToolTip(f"{labels[key]} is always shown")
            item.setFlags(flags)
            self.addItem(item)
        self.blockSignals(False)
        QTimer.singleShot(0, self._fit)

    def _save(self):
        order, hidden = [], []
        for i in range(self.count()):
            item = self.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            order.append(key)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.checkState() != Qt.CheckState.Checked:
                hidden.append(key)
        self.settings.set("tab_order", order)
        self.settings.set("hidden_features", hidden)
        self.on_change()

    def reset_order(self):
        self.settings.set("tab_order", None)
        self._fill()
        self._save()

    # Height follows the wrapped rows (no inner scroll bar).
    def _fit(self):
        self.doItemsLayout()
        height = self.contentsSize().height() + 2 * self.frameWidth() + 6
        if height > 10 and height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit)


class SettingsPage(Page):
    title = "Settings"
    fit_to_window = False  # nothing here should shrink: natural sizes, scroll when needed

    def __init__(self, state, parent=None):
        super().__init__(state, parent)

        from . import theme as _theme

        # ── Appearance ──
        self._section("Appearance")
        rows = self._rows(self.add(Card("Colours")))
        self.theme_group = QButtonGroup(self)
        self._row(rows, "Theme", self._choices(self.theme_group, [label for label, _ in THEMES]),
                  "Dark, light, or pure black backgrounds. System follows your computer.")
        saved_theme = state.settings.get("theme", "dark")
        values = [v for _, v in THEMES]
        self.theme_group.button(values.index(saved_theme) if saved_theme in values else 0).setChecked(True)
        self.theme_group.idClicked.connect(self._theme_changed)

        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        self.color_buttons = []
        for name, hex_color in ACCENT_COLORS:
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            btn.setToolTip(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=hex_color: self._accent_changed(c))
            swatches.addWidget(btn)
            self.color_buttons.append((btn, hex_color))
        self.custom_color_button = QPushButton("Custom…")
        self.custom_color_button.setToolTip("Pick any color")
        self.custom_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.custom_color_button.clicked.connect(self._pick_custom_accent)
        swatches.addWidget(self.custom_color_button)
        swatches.addStretch(1)
        self._row(rows, "Accent", swatches, "The color of buttons, tabs and highlights.")

        tint = QHBoxLayout()
        tint.addWidget(muted("Neutral"))
        self.tint_slider = QSlider(Qt.Orientation.Horizontal)
        self.tint_slider.setRange(0, 100)
        self.tint_slider.setPageStep(10)
        self.tint_slider.setMaximumWidth(360)
        self.tint_slider.setToolTip("How strongly the backgrounds take on the accent color")
        self.tint_slider.setValue(_theme.tint())
        tint.addWidget(self.tint_slider, 1)
        tint.addWidget(muted("Strong"))
        self.tint_value = QLabel()
        self.tint_value.setMinimumWidth(40)
        tint.addWidget(self.tint_value)
        tint.addStretch(1)
        self._row(rows, "Tint", tint, "Pulls the backgrounds toward the accent color.")
        self.tint_slider.valueChanged.connect(self._tint_moved)
        self.tint_slider.sliderReleased.connect(self._tint_committed)

        rows = self._rows(self.add(Card("Layout")))
        self.font_combo = QComboBox()
        for label, size in FONT_SIZES:
            self.font_combo.addItem(f"{label} ({size}pt)", size)
        saved_size = state.settings.get("font_size", 13)
        for i, (_, size) in enumerate(FONT_SIZES):
            if size == saved_size:
                self.font_combo.setCurrentIndex(i)
                break
        self.font_combo.currentIndexChanged.connect(self._font_changed)
        self._row(rows, "Font size", self._left(self.font_combo))

        self.tab_group = QButtonGroup(self)
        self._row(rows, "Tab style", self._choices(self.tab_group, ["Icons", "Text"]),
                  "Icons (the name slides out on hover) or the tab names.")
        self.tab_group.button(0 if state.settings.get("tab_style", "icons") == "icons" else 1).setChecked(True)
        self.tab_group.idClicked.connect(self._tab_style_changed)
        self._refresh_accent_controls()
        _theme.on_apply(self._sync_theme_controls)  # stay right if the theme changes from elsewhere

        self.window_group = QButtonGroup(self)
        self._row(rows, "Window", self._choices(self.window_group, ["Windowed", "Full screen"]),
                  "Open in a window or full screen. F11 (⌃⌘F on a Mac) switches at any time.")
        self.sync_window_mode()
        self.window_group.idClicked.connect(self._window_mode_changed)
        tabs_box = QVBoxLayout()
        tabs_box.setSpacing(6)
        self.tab_list = TabOrderList(state.settings, self._tabs_changed)
        tabs_box.addWidget(self.tab_list)
        foot = QHBoxLayout()
        how = muted("Drag to reorder. Untick to hide.")
        how.setWordWrap(False)
        foot.addWidget(how)
        foot.addStretch(1)
        reset_order = QPushButton("Reset order")
        reset_order.setToolTip("Put the tabs and dividers back in their original order (keeps which are shown).")
        reset_order.clicked.connect(self.tab_list.reset_order)
        foot.addWidget(reset_order)
        tabs_box.addLayout(foot)
        self._row(rows, "Tabs", tabs_box, "Drag tabs (and the │ dividers) into the order you like. Untick "
                                          "tabs you don't use and they disappear from the tab bar; nothing "
                                          "is deleted. Dashboard and Connect always stay.", center=False)

        # ── General ──
        self._section("General")
        rows = self._rows(self.add(Card("Start-up & tips")))
        self.tooltips_check = QCheckBox("Show tips when you hover over buttons and settings")
        self.tooltips_check.setChecked(bool(state.settings.get("tooltips", True)))
        self.tooltips_check.toggled.connect(self._tooltips_toggled)
        self._row(rows, "Tooltips", self.tooltips_check)
        from .disclaimer import CAUTION_HIDE
        self.caution_check = QCheckBox("Show the caution when the app opens")
        self.caution_check.setChecked(not state.settings.get(CAUTION_HIDE, False))
        self.caution_check.toggled.connect(lambda on: state.settings.set(CAUTION_HIDE, not on))
        self._row(rows, "Caution", self.caution_check,
                  "The safety caution shows on every start unless you ticked “Don't show this again” "
                  "on it. Tick this to bring it back.")
        self._build_quick_scan_card()
        from .security import SecurityCard
        self.add(SecurityCard(state.settings))
        self._build_demo_card()

        # ── Data ──
        self._section("Data")
        rows = self._rows(self.add(Card("Logs")))
        self.units_group = QButtonGroup(self)
        self._row(rows, "Units", self._choices(self.units_group, ["Metric (°C, mbar, km/h, Nm)",
                                                                  "Imperial (°F, psi, mph, lb-ft)"]),
                  "How Live View, the Logger table and Analyze show values. Logs are always saved in the "
                  "module's own (metric) units; formulas and alarm limits keep working when you switch.")
        self.units_group.button(1 if state.settings.get("units") == "imperial" else 0).setChecked(True)
        self.units_group.idClicked.connect(
            lambda i: state.settings.set("units", "imperial" if i == 1 else "metric"))

        logs = QVBoxLayout()
        logs.setSpacing(6)
        self.logs_path = QLabel("")
        self.logs_path.setWordWrap(True)
        self.logs_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        logs.addWidget(self.logs_path)
        buttons = QHBoxLayout()
        choose = QPushButton("Choose folder…")
        choose.setToolTip("Pick the folder new datalogs are saved in (e.g. Documents or a synced folder).")
        choose.clicked.connect(self._choose_logs_dir)
        self.logs_default = QPushButton("Use default folder")
        self.logs_default.setToolTip("Go back to the app's own Datalogs folder.")
        self.logs_default.clicked.connect(lambda: self._set_logs_dir(None))
        show = QPushButton("Open folder")
        show.setToolTip("Show the datalogs folder in Finder / Explorer.")
        show.clicked.connect(self._open_logs_dir)
        for b in (choose, self.logs_default, show):
            buttons.addWidget(b)
        buttons.addStretch(1)
        logs.addLayout(buttons)
        self._row(rows, "Datalogs", logs, "Where the Logger saves datalogs, and where Analyze and the "
                                          "Dashboard look for them. Logs already saved stay where they are.")
        self._show_logs_dir()

        rows = self._rows(self.add(Card("Analyze")))
        smart = QVBoxLayout()
        smart.setSpacing(6)
        self.smart_ai = QCheckBox("Generate with AI automatically when a log opens")
        self.smart_ai.setChecked(bool(state.settings.get("smart_presets:ai", True)))
        self.smart_ai.toggled.connect(lambda on: state.settings.set("smart_presets:ai", bool(on)))
        self.smart_bar = QCheckBox("Show the smart preset chips above the Analyze graph")
        self.smart_bar.setChecked(bool(state.settings.get("playback:smart_bar", True)))
        self.smart_bar.toggled.connect(lambda on: state.settings.set("playback:smart_bar", bool(on)))
        smart.addWidget(self.smart_ai)
        smart.addWidget(self.smart_bar)
        self._row(rows, "Smart presets", smart,
                  "Ready-made Analyze layouts for each log, like Datazap's. With AI on (Settings → AI), "
                  "your AI suggests them from the log's channel names (only the names are sent, never the "
                  "values) the first time those channels are seen; otherwise built-in ones are used.")

        # ── Graphs ──
        self._section("Graphs")
        self._build_graphs_card()

        # ── AI ──
        self._section("AI")
        from .ai import AISettingsCard
        ai_card = self.add(AISettingsCard(state.settings))
        self._style_form(ai_card.form)

        # ── About ──
        self._section("About")
        self._build_about()

        self.finish()

    # --- sections: a few cards each, picked with buttons like Analyze's views, plus search ---

    # (title, Material icon, what it's for: shown under the buttons)
    SECTIONS = (
        ("Appearance", "palette", "How the app looks: colours, text size, the tab bar and the window."),
        ("General", "tune", "Everyday behaviour: hover tips, the start-up caution, the password lock and "
                            "demo mode."),
        ("Data", "folder_open", "Units, where datalogs are saved, and Analyze's smart presets."),
        ("Graphs", "show_chart", "How lines, labels and the grid look on the Live View and Analyze graphs."),
        ("AI", "auto_awesome", "Use your own AI for feedback on logs, maps, fault codes and flash plans, "
                               "and for the Assistant. Nothing is sent until you ask."),
        ("About", "info", "Version and updates, the app's folders, and putting these settings back to "
                          "their defaults."),
    )
    SECTION_KEY = "settings:section"
    CONTENT_W = 0     # 0: the cards go all the way across (user prefers full width)

    def _section(self, title):
        """Cards added from now on go in this section."""
        if not hasattr(self, "_groups"):
            self._groups = {}
        self._current = title
        self._groups.setdefault(title, [])

    def add(self, widget):
        """Cards are collected here, by section; finish() lays them out."""
        if not hasattr(self, "_current"):
            self._section(self.SECTIONS[0][0])
        self._groups[self._current].append(widget)
        return widget

    def finish(self):
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QLineEdit, QListWidget, QStackedWidget
        # One column, capped in width (left-aligned), holding everything.
        column = QWidget()
        if self.CONTENT_W:
            column.setMaximumWidth(self.CONTENT_W)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        holder = QHBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.addWidget(column, 1)
        holder.addStretch(0)
        self.layout_.addLayout(holder)
        self.layout_.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.sections = QStackedWidget()
        self.section_buttons, self._section_titles, self._section_desc = [], [], []
        self._index = []  # search: one entry per setting
        for i, (title, icon, desc) in enumerate(s for s in self.SECTIONS if s[0] in self._groups):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            for card in self._groups[title]:
                label = getattr(card, "title_label", None)
                if label is not None:
                    if label.text() == title:
                        label.hide()  # one card named like its section: the button names it
                    else:
                        label.setObjectName("settingsGroup")
                        label.setStyleSheet("")  # the theme styles it
                lay.addWidget(card)
                self._index_card(card, i, title)
            lay.addStretch(1)
            self.sections.addWidget(page)
            b = QPushButton(title.replace("&", "&&"))  # "&" alone would underline the next letter
            b.setObjectName("viewTab")
            b.setFixedHeight(34)
            b.setProperty("active", "false")
            b.setProperty("glyph", icon)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setIconSize(QSize(20, 20))
            b.clicked.connect(lambda _=False, i=i: self.show_section(i))
            row.addWidget(b)
            self.section_buttons.append(b)
            self._section_titles.append(title)
            self._section_desc.append(desc)
        row.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(210)
        self.search.setToolTip("Find a setting by name or what it does, e.g. “dark”, “mph”, “password”")
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(lambda: self._open_result(self.results.item(0)))
        row.addWidget(self.search)
        col.addLayout(row)
        self.section_note = muted("")
        self.section_note.setWordWrap(True)
        col.addWidget(self.section_note)
        col.addWidget(self.sections)
        self.results = QListWidget()
        self.results.setObjectName("settingsResults")
        self.results.itemClicked.connect(self._open_result)
        self.results.hide()
        col.addWidget(self.results)
        from . import theme as _theme
        _theme.on_apply(self._style_sections)
        saved = self.state.settings.get(self.SECTION_KEY)
        start = self._section_titles.index(saved) if saved in self._section_titles else 0
        self.show_section(start, remember=False)

    def show_section(self, index, remember=True):
        """Show one section (by position or title, e.g. show_section("AI"))."""
        if isinstance(index, str):
            index = self._section_titles.index(index) if index in self._section_titles else 0
        self.sections.setCurrentIndex(index)
        # Only the shown section counts for the page's height: no empty scroll below a short one.
        for i in range(self.sections.count()):
            p = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.sections.widget(i).setSizePolicy(QSizePolicy.Policy.Preferred, p)
        self.sections.adjustSize()
        self.section_note.setText(self._section_desc[index])
        if remember:
            self.state.settings.set(self.SECTION_KEY, self._section_titles[index])
        self._style_sections()

    def _style_sections(self):
        from . import theme as _theme
        from .main_window import _material_icon
        c = _theme.colors()
        searching = bool(self.search.text().strip()) if hasattr(self, "search") else False
        current = -1 if searching else self.sections.currentIndex()
        for i, b in enumerate(self.section_buttons):
            active = i == current
            b.setProperty("active", "true" if active else "false")
            b.style().unpolish(b)
            b.style().polish(b)
            b.setIcon(_material_icon(b.property("glyph") or "tune", 20,
                                     c["on_accent"] if active else c["tab_text"]))

    # --- search ---

    @staticmethod
    def _words(widget):
        """Everything a setting says about itself: its texts, choices and tips."""
        import re
        from PySide6.QtWidgets import QAbstractButton
        parts = [widget.toolTip()]
        if isinstance(widget, (QLabel, QAbstractButton)):
            parts.append(widget.text())
        if isinstance(widget, QComboBox):
            parts += [widget.itemText(i) for i in range(widget.count())]
        for child in widget.findChildren(QWidget):
            parts.append(child.toolTip())
            if isinstance(child, (QLabel, QAbstractButton)):
                parts.append(child.text())
            elif isinstance(child, QComboBox):
                parts += [child.itemText(i) for i in range(child.count())]
        return re.sub(r"<[^>]+>", " ", " ".join(p for p in parts if p)).lower()

    def _index_card(self, card, section, section_title):
        """Search entries for a card: one per labelled row, or the card as a whole."""
        from PySide6.QtWidgets import QFormLayout
        title_label = getattr(card, "title_label", None)
        group = title_label.text() if title_label is not None else section_title
        forms = [lay for lay in card.findChildren(QFormLayout)]
        lay = card.layout()
        stack = [lay] if lay is not None else []
        while stack:  # forms added as nested layouts aren't QObject children of the card
            item = stack.pop()
            if isinstance(item, QFormLayout) and item not in forms:
                forms.append(item)
            for k in range(item.count()):
                sub = item.itemAt(k).layout()
                if sub is not None:
                    stack.append(sub)
        rows = 0
        for form in forms:
            for r in range(form.rowCount()):
                li = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
                fi = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
                label = li.widget() if li else None
                field = fi.widget() if fi else None
                if not isinstance(label, QLabel) or not label.text():
                    continue
                text = f"{label.text()} {group} {section_title} " + self._words(label) + \
                    (" " + self._words(field) if field is not None else "")
                self._index.append({"name": label.text(), "section": section, "where": section_title
                                    if group == section_title else f"{section_title} › {group}",
                                    "label": label, "field": field, "text": text.lower()})
                rows += 1
        if not rows:  # a card laid out by hand (the Lock): one entry for all of it
            self._index.append({"name": group, "section": section, "where": section_title,
                                "label": title_label, "field": card,
                                "text": f"{group} {section_title} {self._words(card)}".lower()})

    def _search_changed(self, text):
        words = text.lower().split()
        searching = bool(words)
        self.sections.setVisible(not searching)
        self.section_note.setVisible(not searching)
        self.results.setVisible(searching)
        self.results.clear()
        if searching:
            from PySide6.QtWidgets import QListWidgetItem
            hits = [e for e in self._index
                    if all(w in e["text"] for w in words)
                    and not (e["label"] is not None and e["label"].isHidden() and e["field"] is not None
                             and e["field"].isHidden())]
            # Names that match come first, then their group's (e.g. "demo": Demo mode's row).
            hits.sort(key=lambda e: (not all(w in e["name"].lower() for w in words),
                                     not all(w in e["where"].lower() for w in words), e["section"]))
            for e in hits:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, e)
                item.setToolTip(f"{e['name']} · {e['where']}")
                line = QWidget()
                lay = QHBoxLayout(line)
                lay.setContentsMargins(10, 6, 10, 6)
                lay.setSpacing(12)
                name = QLabel(e["name"])
                name.setStyleSheet("font-weight: 600; background: transparent;")
                lay.addWidget(name)
                where = muted(e["where"])
                where.setStyleSheet("background: transparent;")
                lay.addWidget(where)
                lay.addStretch(1)
                item.setSizeHint(line.sizeHint())
                self.results.addItem(item)
                self.results.setItemWidget(item, line)
            if not hits:
                item = QListWidgetItem("No settings match. Try another word.")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.results.addItem(item)
            rows = max(self.results.count(), 1)
            self.results.setFixedHeight(min(rows, 12) * (max(self.results.sizeHintForRow(0), 30) + 2) + 10)
        self._style_sections()

    def _open_result(self, item):
        e = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not e:
            return
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._search_changed("")
        self.show_section(e["section"])
        target = e["label"] if e["label"] is not None and not e["label"].isHidden() else e["field"]
        QTimer.singleShot(60, lambda: self._flash(target))

    def _flash(self, widget):
        """Scroll a setting into view and light it up for a moment."""
        if widget is None:
            return
        from . import theme as _theme
        self.ensureWidgetVisible(widget, 40, 80)
        old = widget.styleSheet()
        accent = _theme.colors()["accent"]
        widget.setStyleSheet(old + f"; color: {accent}; font-weight: 700;" if isinstance(widget, QLabel)
                             else old + f" QFrame#card {{ border: 1px solid {accent}; }}")
        QTimer.singleShot(1600, lambda: widget.setStyleSheet(old))

    # --- layout helpers: cards of label | control rows ---

    LABEL_W = 110

    def _rows(self, card):
        from PySide6.QtWidgets import QFormLayout
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(form.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        card.add(form)
        return form

    def _row(self, form, label, field, tip="", center=True):
        from PySide6.QtWidgets import QLayout
        if isinstance(field, QLayout):
            holder = QWidget()
            field.setContentsMargins(0, 0, 0, 0)
            holder.setLayout(field)
            field = holder
        name = QLabel(label)
        name.setObjectName("muted")
        name.setFixedWidth(self.LABEL_W)
        # As tall as a one-line control, text centred: lines up with buttons and pills beside it.
        if center:
            name.setMinimumHeight(min(field.sizeHint().height(), 40))
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | (Qt.AlignmentFlag.AlignVCenter if center
                                                         else Qt.AlignmentFlag.AlignTop))
        if tip:
            name.setToolTip(tip)
            field.setToolTip(field.toolTip() or tip)
        form.addRow(name, field)
        return field

    def _style_form(self, form):
        """Give a form built elsewhere (the AI card) the same label column."""
        from PySide6.QtWidgets import QFormLayout
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            label = item.widget() if item else None
            if isinstance(label, QLabel):
                label.setObjectName("muted")
                label.setFixedWidth(self.LABEL_W)

    @staticmethod
    def _choices(group, labels):
        row = QHBoxLayout()
        for i, label in enumerate(labels):
            rb = QRadioButton(label)
            rb.setObjectName("option")
            group.addButton(rb, i)
            row.addWidget(rb)
        row.addStretch(1)
        return row

    @staticmethod
    def _left(widget):
        row = QHBoxLayout()
        row.addWidget(widget)
        row.addStretch(1)
        return row

    def refresh(self):
        if hasattr(self, "demo_toggle"):
            self.demo_toggle.setEnabled(not self.state.busy)

    def _tooltips_toggled(self, on):
        from .tooltips import TooltipGate
        self.state.settings.set("tooltips", bool(on))
        TooltipGate.install(self.state.settings)  # adds or removes the app-wide filter

    # --- about ---

    # What "Reset settings to defaults" forgets: everything this page sets. Car profiles, presets,
    # layouts, AI keys, the lock and your files are kept.
    RESET_KEYS = ("theme", "accent_color", "tint", "palette", "font_size", "tab_style", "tab_order",
                  "hidden_features", "window_mode", "tooltips", "caution_hide", "units", "datalogs_dir",
                  "smart_presets:ai", "playback:smart_bar", "graph_colors", "graph_line_width",
                  "graph_label_pt", "graph_grid", "settings:section")

    def _build_about(self):
        import os
        from . import updates
        from .storage import data_dir, user_dir
        rows = self._rows(self.add(Card("PQ35 Flasher")))
        v = updates.local_version()
        text = f"Version {v['version']}" + (f" · {v['commit'][:7]}" if v.get("commit") else "") + \
            (" · demo mode" if os.environ.get("PQ35_DEMO") else "")
        version = QLabel(text)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._row(rows, "Version", self._left(version))
        upd = QHBoxLayout()
        self.about_update = QPushButton("Check for updates")
        self.about_update.clicked.connect(self._check_updates)
        upd.addWidget(self.about_update)
        upd.addSpacing(8)
        self.about_update_status = muted("")
        upd.addWidget(self.about_update_status)
        upd.addStretch(1)
        self._row(rows, "Updates", upd, "Looks on GitHub for a newer version (also in Help → Troubleshooting "
                                        "& logs). Updating closes the app, updates it and reopens it.")
        folders = QHBoxLayout()
        for label, where, tip in (
                ("Your files", user_dir, "Documents/PQ35 Flasher: module records, backups, images, datalogs "
                                         "and projects."),
                ("Settings folder", data_dir, "Where the app keeps settings.json, recent logs and its lock "
                                              "files.")):
            b = QPushButton(label)
            b.setToolTip(f"{tip}\n{where()}")
            b.clicked.connect(lambda _=False, where=where: self._open_folder(where()))
            folders.addWidget(b)
        folders.addStretch(1)
        self._row(rows, "Folders", folders, "Open the app's folders in Finder / Explorer.")

        rows = self._rows(self.add(Card("Reset")))
        reset = QPushButton("Reset settings to defaults…")
        reset.clicked.connect(self._reset_all)
        self._row(rows, "Settings", self._left(reset),
                  "Puts everything on this Settings page back to how it came and restarts the app. Car "
                  "profiles, presets, Live View layouts, AI keys, the password lock and your files are kept.")

    def _check_updates(self):
        window = self.window()
        if not hasattr(window, "update_button"):
            return
        if not getattr(self, "_update_hooked", False):
            window.checker.finished.connect(lambda *_: QTimer.singleShot(0, self._sync_update))
            self._update_hooked = True
        window.update_button.click()  # the same check (or "Update now") as Help's
        self._sync_update()

    def _sync_update(self):
        window = self.window()
        if hasattr(window, "update_button") and hasattr(self, "about_update"):
            self.about_update.setText(window.update_button.text())
            self.about_update.setEnabled(window.update_button.isEnabled())
            self.about_update_status.setText(window.update_status.text())

    @staticmethod
    def _open_folder(folder):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reset_all(self):
        import os
        from .popups import MessageBox
        if self.state.busy:
            MessageBox.warning(self, "Reset settings", "Wait for the current operation to finish first.")
            return
        answer = MessageBox.question(
            self, "Reset settings",
            "Put everything on the Settings page back to its default (theme, colours, tabs, units, "
            "graphs, datalogs folder…) and restart the app?\n\nCar profiles, presets, Live View layouts, "
            "AI keys, the password lock and your files are kept.")
        if answer != MessageBox.StandardButton.Yes:
            return
        self.state.settings.remove(*self.RESET_KEYS)
        self.state.restart_app.emit(bool(os.environ.get("PQ35_DEMO")))

    # --- demo mode ---

    def _build_quick_scan_card(self):
        """Settings → General → Scan: which modules the quick Scan reads."""
        from PySide6.QtWidgets import QGridLayout
        rows = self._rows(self.add(Card("Scan")))
        box = QVBoxLayout()
        box.setSpacing(8)
        self.quick_grid = QGridLayout()
        self.quick_grid.setHorizontalSpacing(18)
        self.quick_grid.setVerticalSpacing(6)
        box.addLayout(self.quick_grid)
        buttons = QHBoxLayout()
        default = QPushButton("Use default")
        default.setToolTip("Engine, Transmission, ABS Brakes, AWD and Steering Assist.")
        default.clicked.connect(self._quick_scan_default)
        buttons.addWidget(default)
        buttons.addStretch(1)
        box.addLayout(buttons)
        self._row(rows, "Quick scan", box,
                  "The modules Scan (Connect) and Quick Scan (Dashboard) read. Full Scan always reads "
                  "every module on the gateway's list. Modules a Full Scan found on your car are added "
                  "to this list.", center=False)
        self._fill_quick_scan()
        self.state.modules_scanned.connect(lambda _r: self._fill_quick_scan())

    def _fill_quick_scan(self):
        from . import quick_scan
        while self.quick_grid.count():
            w = self.quick_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        picked = set(quick_scan.addresses(self.state.settings))
        self.quick_checks = {}
        choices = quick_scan.choices(self.state)
        per_col = (len(choices) + 2) // 3
        for i, (address, name) in enumerate(choices):
            check = QCheckBox(f"{address:02X}  {name}")
            check.setChecked(address in picked)
            check.toggled.connect(self._quick_scan_changed)
            self.quick_checks[address] = check
            self.quick_grid.addWidget(check, i % per_col, i // per_col)

    def _quick_scan_changed(self):
        from . import quick_scan
        picked = [a for a, c in self.quick_checks.items() if c.isChecked()]
        if not picked:  # a scan of nothing: keep at least one (the engine, or what was last ticked)
            check = self.quick_checks.get(0x01) or next(iter(self.quick_checks.values()))
            check.blockSignals(True)
            check.setChecked(True)
            check.blockSignals(False)
            picked = [a for a, c in self.quick_checks.items() if c.isChecked()]
        quick_scan.set_addresses(self.state.settings, picked)
        self.state.changed.emit()  # Connect's and the Dashboard's hints name the modules

    def _quick_scan_default(self):
        from . import quick_scan
        quick_scan.set_addresses(self.state.settings, quick_scan.DEFAULT)
        self._fill_quick_scan()
        self.state.changed.emit()

    def _build_demo_card(self):
        import os
        demo = self.add(Card("Demo mode"))
        demo.hint("Try the app with pretend modules. Nothing talks to real hardware, and "
                  "demo files are kept apart in Documents/PQ35 Flasher/Demo. Switching "
                  "restarts the app.")
        self.demo_toggle = QCheckBox("On (restarts the app)")
        self.demo_toggle.setChecked(bool(os.environ.get("PQ35_DEMO")))
        self.demo_toggle.clicked.connect(self._demo_clicked)
        row = QHBoxLayout()
        row.addWidget(self.demo_toggle)
        row.addSpacing(16)
        if os.environ.get("PQ35_DEMO"):
            reset = QPushButton("Reset demo fault codes")
            reset.setToolTip("Put all the pretend fault codes back on Engine and Transmission "
                             "(some clear, some come straight back as \"still active\").")
            reset.clicked.connect(self._reset_demo_faults)
            row.addWidget(reset)
            self.demo_note = muted("")
            row.addWidget(self.demo_note, 1)
        row.addStretch(1)
        self._row(self._rows(demo), "Demo", row)

    def _reset_demo_faults(self):
        from .storage import data_dir
        for path in (data_dir() / "demo").glob("*.dtc.json"):
            path.unlink(missing_ok=True)
        self.demo_note.setText("Done. Re-read all on the Dashboard (or Scan on Connect) to see them.")

    def _demo_clicked(self, on):
        import os
        from .popups import MessageBox
        current = bool(os.environ.get("PQ35_DEMO"))
        self.demo_toggle.setChecked(current)  # only changes once the restart is confirmed
        if on == current:
            return
        answer = MessageBox.question(
            self, "Demo mode",
            ("Restart in demo mode? Only pretend modules are offered; nothing talks to real "
             "hardware." if on else
             "Leave demo mode? The app restarts and offers your real interfaces again."))
        if answer == MessageBox.StandardButton.Yes:
            self.state.restart_app.emit(on)

    # --- graphs ---

    def _build_graphs_card(self):
        from PySide6.QtWidgets import QDoubleSpinBox
        from . import graphstyle as gs
        s = self.state.settings
        card = self.add(Card("Graphs"))
        card.hint("How the lines and labels look on the Live View and Analyze graphs.")
        rows = self._rows(card)
        self.graph_width = QDoubleSpinBox()
        self.graph_width.setRange(0.5, 8.0)
        self.graph_width.setSingleStep(0.5)
        self.graph_width.setDecimals(1)
        self.graph_width.setSuffix(" px")
        self.graph_width.setValue(gs.width(s))
        self.graph_width.valueChanged.connect(lambda v: self._graph_set("graph_line_width", v))
        self._row(rows, "Line thickness", self._left(self.graph_width))
        self.graph_labels = QComboBox()
        for label, pt in gs.LABEL_SIZES:
            self.graph_labels.addItem(f"{label} ({pt}pt)", pt)
        index = self.graph_labels.findData(gs.label_pt(s))
        if index < 0:
            self.graph_labels.addItem(f"Custom ({gs.label_pt(s)}pt)", gs.label_pt(s))
            index = self.graph_labels.count() - 1
        self.graph_labels.setCurrentIndex(index)
        self.graph_labels.currentIndexChanged.connect(
            lambda _: self._graph_set("graph_label_pt", self.graph_labels.currentData()))
        self._row(rows, "Label size", self._left(self.graph_labels))
        self.graph_grid = QSlider(Qt.Orientation.Horizontal)
        self.graph_grid.setRange(0, 60)
        self.graph_grid.setFixedWidth(200)
        self.graph_grid.setToolTip("Grid line strength (left = off)")
        self.graph_grid.setValue(int(round(gs.grid_alpha(s) * 100)))
        self.graph_grid.valueChanged.connect(lambda v: self._graph_set("graph_grid", v))
        grid = QHBoxLayout()
        grid.addWidget(muted("Off"))
        grid.addWidget(self.graph_grid)
        grid.addWidget(muted("Strong"))
        grid.addStretch(1)
        self._row(rows, "Grid", grid)
        crow = QHBoxLayout()
        crow.setSpacing(6)
        self.graph_swatches = []
        for i in range(len(gs.DEFAULT_COLORS)):
            btn = QPushButton(str(i + 1))
            btn.setFixedSize(30, 30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self._pick_graph_color(idx))
            crow.addWidget(btn)
            self.graph_swatches.append(btn)
        crow.addSpacing(10)
        reset = QPushButton("Reset graph settings")
        reset.setToolTip("Back to the default colours, thickness, label size and grid.")
        reset.clicked.connect(self._reset_graphs)
        crow.addWidget(reset)
        crow.addStretch(1)
        self._row(rows, "Line colours", crow, "In order: line 1 is the first ticked channel. Click one to "
                                              "change it.")

        import numpy as np
        import pyqtgraph as pg
        from . import theme
        self.graph_preview = pg.PlotWidget()
        self.graph_preview.setBackground(theme.colors()["surface"])
        self.graph_preview.setFixedHeight(120)
        self.graph_preview.setMouseEnabled(x=False, y=False)
        self.graph_preview.hideButtons()
        self.graph_preview.setMenuEnabled(False)
        self.graph_preview.addLegend(offset=(6, 4))
        self._preview_x = np.linspace(0, 10, 200)
        gs.theme_plot(self.graph_preview)
        card.add(self.graph_preview)
        self._refresh_graph_preview()

    def _graph_set(self, key, value):
        from . import graphstyle
        self.state.settings.set(key, value)
        self._refresh_graph_preview()
        graphstyle.changed()

    def _pick_graph_color(self, idx):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        from . import graphstyle
        current = graphstyle.colors(self.state.settings)
        chosen = QColorDialog.getColor(QColor(current[idx]), self, f"Colour for line {idx + 1}")
        if not chosen.isValid():
            return
        current[idx] = chosen.name()
        self._graph_set("graph_colors", current)

    def _reset_graphs(self):
        from . import graphstyle as gs
        s = self.state.settings
        for key in ("graph_colors", "graph_line_width", "graph_label_pt", "graph_grid"):
            s.values.pop(key, None)
        s.set("graph_colors", None)
        for w, value in ((self.graph_width, gs.DEFAULT_WIDTH), (self.graph_grid, gs.DEFAULT_GRID)):
            w.blockSignals(True)
            w.setValue(value)
            w.blockSignals(False)
        self.graph_labels.blockSignals(True)
        self.graph_labels.setCurrentIndex(max(self.graph_labels.findData(gs.DEFAULT_LABEL_PT), 0))
        self.graph_labels.blockSignals(False)
        self._refresh_graph_preview()
        gs.changed()

    def _refresh_graph_preview(self):
        import numpy as np
        import pyqtgraph as pg
        from . import graphstyle as gs
        s = self.state.settings
        colors = gs.colors(s)
        for btn, c in zip(self.graph_swatches, colors):
            text = "#000000" if pg.mkColor(c).lightness() > 140 else "#ffffff"
            btn.setStyleSheet(f"background: {c}; color: {text}; font-weight: 700; "
                              f"border-radius: 15px; border: 2px solid transparent; min-height: 0; padding: 0;")
            btn.setToolTip(c)
        plot = self.graph_preview
        plot.clear()
        gs.apply(plot, s)
        x = self._preview_x
        for i, name in enumerate(("Engine speed", "Boost", "Timing")):
            y = 50 + 35 * np.sin(x * (0.6 + 0.35 * i) + i) * np.exp(-0.03 * x)
            plot.plot(x, y, pen=pg.mkPen(colors[i], width=gs.width(s)), name=name)

    def showEvent(self, event):
        super().showEvent(event)
        # "Don't show this again" on the startup caution may have changed it since this was built
        from .disclaimer import CAUTION_HIDE
        self.caution_check.blockSignals(True)
        self.caution_check.setChecked(not self.state.settings.get(CAUTION_HIDE, False))
        self.caution_check.blockSignals(False)
        if hasattr(self, "about_update"):
            self._sync_update()  # a check may have run from Help since

    def sync_window_mode(self):
        full = self.state.settings.get("window_mode", "windowed") == "fullscreen"
        self.window_group.button(1 if full else 0).setChecked(True)

    def _window_mode_changed(self, btn_id):
        mode = "fullscreen" if btn_id == 1 else "windowed"
        self.state.settings.set("window_mode", mode)
        window = self.window()
        if hasattr(window, "apply_window_mode"):
            window.apply_window_mode(mode)

    def _theme_changed(self, btn_id):
        self.state.settings.set("theme", THEMES[btn_id][1])
        self._apply_theme()
        self._refresh_accent_controls()

    def _font_changed(self, index):
        size = self.font_combo.currentData()
        self.state.settings.set("font_size", size)
        self._apply_theme()

    def _accent_changed(self, color):
        self.state.settings.set("accent_color", color)
        self.state.settings.set("palette", None)  # old named schemes are folded into accent + tint
        self._apply_theme()
        self._refresh_accent_controls()

    def _sync_theme_controls(self):
        from . import theme as _theme
        saved = self.state.settings.get("theme", "dark")
        values = [v for _, v in THEMES]
        button = self.theme_group.button(values.index(saved) if saved in values else 0)
        if not button.isChecked():
            button.setChecked(True)
        if not self.tint_slider.isSliderDown() and self.tint_slider.value() != _theme.tint():
            self.tint_slider.blockSignals(True)
            self.tint_slider.setValue(_theme.tint())
            self.tint_slider.blockSignals(False)
        self._refresh_accent_controls()

    def _pick_custom_accent(self):
        from . import theme as _theme
        color = QColorDialog.getColor(QColor(_theme.accent()), self, "Accent color")
        if color.isValid():
            self._accent_changed(color.name())

    def _tint_moved(self, value):
        self._refresh_accent_controls(value)
        if not self.tint_slider.isSliderDown():  # keyboard / click on the track: commit right away
            self._tint_committed()

    def _tint_committed(self):
        from . import theme as _theme
        value = self.tint_slider.value()
        if value != _theme.tint() or self.state.settings.get("tint") is None:
            self.state.settings.set("tint", value)
            if self.state.settings.get("accent_color") is None:
                self.state.settings.set("accent_color", _theme.accent())
            self.state.settings.set("palette", None)
            self._apply_theme()
            self._refresh_accent_controls()

    def _refresh_accent_controls(self, tint_amount=None):
        """Restyle the swatches, tint readout and preview (preview follows the slider while dragging)."""
        from . import theme as _theme
        accent = _theme.accent()
        live = _theme.colors()
        ring = live["text"]
        known = False
        for btn, hex_color in self.color_buttons:
            selected = hex_color.lower() == accent.lower()
            known = known or selected
            btn.setStyleSheet(
                f"QPushButton {{ background: {hex_color}; border: 3px solid {ring if selected else 'transparent'};"
                f" border-radius: 15px; padding: 0; min-height: 0; }}"
                f"QPushButton:hover {{ border-color: {live['muted'] if not selected else ring}; }}")
        # Custom… shows the picked colour as a dot, and a ring when it's the active choice.
        dot = QPixmap(14, 14)
        dot.fill(Qt.GlobalColor.transparent)
        painter = QPainter(dot)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(live["muted"] if known else accent))
        painter.drawEllipse(1, 1, 12, 12)
        painter.end()
        self.custom_color_button.setIcon(QIcon(dot))
        self.custom_color_button.setStyleSheet("" if known else f"QPushButton {{ border: 2px solid {ring}; }}")
        t = self.tint_slider.value() if tint_amount is None else tint_amount
        self.tint_value.setText("Off" if t == 0 else f"{t}%")

    def _tabs_changed(self):
        window = self.window()
        if hasattr(window, "layout_tabs"):
            window.layout_tabs()
            window._state_changed()

    def _tab_style_changed(self, btn_id):
        value = ("icons", "text")[btn_id]
        self.state.settings.set("tab_style", value)
        # Find the MainWindow and apply
        from PySide6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if hasattr(w, '_apply_tab_style'):
                w._apply_tab_style()
                break

    def _apply_theme(self):
        """Restyle the app shortly after the last change. A restyle re-polishes every widget, so a
        burst of clicks (swatches, theme, font) becomes one restyle; the preview updates instantly."""
        from PySide6.QtCore import QTimer
        if not hasattr(self, "_theme_timer"):
            self._theme_timer = QTimer(self)
            self._theme_timer.setSingleShot(True)
            self._theme_timer.setInterval(120)
            self._theme_timer.timeout.connect(self._apply_theme_now)
        self._theme_timer.start()

    def _apply_theme_now(self):
        from PySide6.QtWidgets import QApplication
        from . import theme
        from .main import app_icon
        a = QApplication.instance()
        theme.apply(a)
        icon = app_icon()
        a.setWindowIcon(icon)
        for w in a.topLevelWidgets():
            if w.isWindow() and w.isVisible():
                w.setWindowIcon(icon)

    # --- datalogs folder ---

    def _show_logs_dir(self):
        from .storage import datalogs_dir
        from pathlib import Path
        custom = self.state.settings.get("datalogs_dir")
        path, home = str(datalogs_dir()), str(Path.home())
        short = "~" + path[len(home):] if path.startswith(home + "/") else path  # ~/Documents/…
        self.logs_path.setText(short + ("" if custom else "  (default)"))
        self.logs_path.setToolTip(path)
        self.logs_default.setEnabled(bool(custom))

    def _choose_logs_dir(self):
        from PySide6.QtWidgets import QFileDialog
        from .storage import datalogs_dir
        path = QFileDialog.getExistingDirectory(self, "Save datalogs in", str(datalogs_dir()))
        if path:
            self._set_logs_dir(path)

    def _set_logs_dir(self, path):
        self.state.settings.set("datalogs_dir", path)
        self._show_logs_dir()

    def _open_logs_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .storage import datalogs_dir
        folder = datalogs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

