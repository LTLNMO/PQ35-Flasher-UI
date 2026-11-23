"""The Mac interface's frame (Settings → Appearance → Interface: Mac): a sidebar grouped into
Car / Tune / Data with the fault badge and a connection card, and a page header with the page
title and the car. The pages inside are the same as Classic's.

The Classic tab bar still exists, hidden: main_window keeps deciding which pages show, which
are locked (need a module) and which is current on its buttons, and the sidebar mirrors them
(sync). So both interfaces share one set of rules."""
import re
import sys

from PySide6.QtCore import QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme

GROUPS = (
    ("Car", ("home", "projects", "connect")),
    ("Tune", ("backup", "flash", "patches")),
    ("Data", ("diagnostics", "liveview", "logger", "playback", "dyno", "maps")),
)
# On a Mac, Help and Settings live in the menu bar and their own windows; elsewhere at the bottom.
FOOTER = () if sys.platform == "darwin" else ("help", "settings")
FULL_W = 210
TRAFFIC_LIGHTS_H = 40  # room left above the sidebar for the window buttons
TRAFFIC_LIGHTS_W = 80  # the window buttons' row: a page title starts after it while the sidebar is hidden
COLLAPSED_KEY = "mac:sidebar_collapsed"  # True: the sidebar is hidden
SLIDE_MS = 220  # macOS's sidebar slides in/out in about this long
ICON = 17
BADGE_TAB = "diagnostics"
# Every page has the same full-width top bar: the page's name, then its toolbar (page.mac_bar), as
# System Settings / Music / Xcode. Columns (Analyze's Channels, Diagnostics' Modules) start under it
# with their own small headings, as Mail's and Notes' do.
# RULE (2026-11-21): page.mac_bar holds only the page's main action and its readout (● Record /
# ▶ Start / Analyze's log pill + status). Since 2026-11-23 they sit at the title bar's RIGHT end (Header
# _trailing: readout, then the main action at the far right on the content margin), the title alone at
# the left, as macOS toolbars. Every other button belongs to the content: the right end of row 1.
COLUMN_PAGES = set()
HEADER_H = 52  # the mockup's .tb height
DRAG_H = 52  # the top strip that moves the window, like a title bar


class SidebarItem(QPushButton):
    """Icon + name (+ a red count badge)."""

    def __init__(self, key, glyph, label, parent=None):
        super().__init__(label, parent)
        self.key, self.glyph, self.label = key, glyph, label
        self.badge = 0
        self.setObjectName("macItem")
        self.setProperty("active", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIconSize(QSize(ICON, ICON))

    def paint_icon(self):
        from . import sf_symbols
        from .main_window import _material_icon
        c = theme.colors()
        locked = self.property("locked") is True
        color = c["tab_disabled"] if locked else c["accent"]
        size = theme.icon_px("sidebar")  # 19 at 13 pt text: a little bigger than the name, as in Finder
        self.setIconSize(QSize(size, size))
        self.setIcon(sf_symbols.nav_icon(self.key, size, color) or _material_icon(self.glyph, size, color))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.badge:
            return
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = str(self.badge)
        f = self.font()
        f.setPointSizeF(max(f.pointSizeF() - 2, 8))
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        w = max(18, p.fontMetrics().horizontalAdvance(text) + 10)
        r = QRectF(self.width() - w - 8, (self.height() - 16) / 2, w, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["err"]))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class ConnectionCard(QFrame):
    """Bottom of the sidebar: adapter state, the car, the module. Click: Connect."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.setObjectName("macCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        self.line1, self.line2, self.line3 = QLabel(), QLabel(), QLabel()
        for i, lbl in enumerate((self.line1, self.line2, self.line3)):
            lbl.setTextFormat(Qt.TextFormat.RichText if i == 0 else Qt.TextFormat.PlainText)
            if i:
                lbl.setObjectName("muted")
            lay.addWidget(lbl)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.menu()

    def menu(self):
        """Interface and module choices (Classic has them as two buttons in its top bar)."""
        from PySide6.QtWidgets import QMenu
        from .analyze_tools import menu_icon, popup_menu
        w = self.window_
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        iface = menu.addMenu("Interface")
        iface.menuAction().setIcon(menu_icon("usb"))
        iface.menuAction().setIconVisibleInMenu(True)
        for act in w.interface_menu.actions():
            iface.addAction(act)
        if iface.isEmpty():
            iface.addAction("Looking for interfaces…").setEnabled(False)
        module = menu.addMenu("Module")
        module.menuAction().setIcon(menu_icon("memory"))
        module.menuAction().setIconVisibleInMenu(True)
        w.fill_module_menu(module)
        menu.addSeparator()
        act = menu.addAction(menu_icon("settings_ethernet"), "Open Connect")
        act.setIconVisibleInMenu(True)
        act.triggered.connect(lambda: w.navigate("connect"))
        cars = menu.addAction(menu_icon("directions_car"), "Cars…")  # was the header's car line
        cars.setIconVisibleInMenu(True)
        cars.triggered.connect(w._open_cars)
        popup_menu(menu, self, align="left")

    def refresh(self):
        w = self.window_
        s = w.state
        c = theme.colors()
        adapter = s.adapter or {}
        name = (adapter.get("label") or adapter.get("adapter") or "").split(" · ")[0].split(" (")[0]
        connected = bool(adapter)
        color = c["ok"] if connected else c["muted"]
        status = "Connected" if connected else "No interface"
        if s.busy:
            color, status = c["warn"], "Working…"
        self.line1.setText(f"<span style='color:{color}'>●</span>&nbsp;<b style='color:{color}'>{status}</b>"
                           + (f"<span style='color:{c['muted']}'> · {name}</span>" if name else ""))
        car = w.car_label.text().strip() if hasattr(w, "car_label") else ""
        car = re.sub(r"\s*·?\s*\b[A-HJ-NPR-Z0-9]{17}\b", "", car).strip(" ·")  # the VIN has its own line
        module = (s.family or {}).get("label", "").split(" (")[0]
        line2 = " · ".join(t for t in (car, module) if t) or "No car read yet"
        vin = (s.vin() if hasattr(s, "vin") else "") or ""
        room = FULL_W - 20 - 20 - 2  # sidebar margins, card padding
        fm = self.line2.fontMetrics()
        self.line2.setText(fm.elidedText(line2, Qt.TextElideMode.ElideRight, room))
        self.line3.setText(fm.elidedText(vin, Qt.TextElideMode.ElideRight, room))
        self.setToolTip("\n".join(t for t in (f"{status}{' · ' + name if name else ''}", line2, vin,
                                               "Click: interface, module, Connect") if t))
        for lbl in (self.line1, self.line2, self.line3):
            lbl.setVisible(bool(lbl.text()))


class Sidebar(QFrame):
    """Shown, or hidden completely, sliding out to the left as macOS's does. Like Finder there's no
    button for it: View → Show/Hide Sidebar in the menu bar (⌃⌘S). The content keeps its full width while it slides (no squashed names)."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.setObjectName("macSidebar")
        self.items = {}
        labels = {key: (glyph, label) for key, glyph, label, _cls in _nav()}
        self.inner = QWidget(self)  # slides inside the frame while the frame narrows
        self.inner.setObjectName("macSidebarInner")
        lay = QVBoxLayout(self.inner)
        lay.setContentsMargins(10, 0, 10, 10)
        lay.setSpacing(1)
        lay.addSpacing(TRAFFIC_LIGHTS_H)  # the traffic lights
        self.headings = []
        for group, keys in GROUPS:
            h = QLabel(group)
            h.setObjectName("macGroup")
            self.headings.append(h)
            lay.addWidget(h)
            for key in keys:
                self._add(lay, key, *labels[key])
        lay.addStretch(1)
        self.assistant = SidebarItem("assistant", "", "Assistant")
        self.assistant.clicked.connect(window._open_assistant)
        lay.addWidget(self.assistant)
        for key in FOOTER:
            self._add(lay, key, *labels[key])
        lay.addSpacing(8)
        self.card = ConnectionCard(window)
        lay.addWidget(self.card)
        self.hidden = bool(window.settings.get(COLLAPSED_KEY, False))
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setFixedWidth(int(v)))
        self._anim.finished.connect(self._slid)
        self.setFixedWidth(0 if self.hidden else FULL_W)
        self.setVisible(not self.hidden)
        if sys.platform != "darwin":  # on a Mac: View → Show/Hide Sidebar in the menu bar (⌃⌘S)
            QShortcut(QKeySequence("Ctrl+Shift+S"), window).activated.connect(self.toggle)
        theme.on_apply(self.sync)

    def _add(self, lay, key, glyph, label):
        item = SidebarItem(key, glyph, label)
        item.clicked.connect(lambda _=False, k=key: self.window_.navigate(k))
        self.items[key] = item
        lay.addWidget(item)

    def toggle(self):
        self.set_hidden(not self.hidden)

    def set_hidden(self, hidden, animate=True):
        if hidden == self.hidden and not self._anim.state():
            return
        self.hidden = hidden
        self.window_.settings.set(COLLAPSED_KEY, hidden)
        action = getattr(self.window_, "_sidebar_action", None)
        if action is not None:
            action.setText("Show Sidebar" if hidden else "Hide Sidebar")
        self._anim.stop()
        if not hidden:
            self.show()
        start, end = self.width(), (0 if hidden else FULL_W)
        if not animate or start == end:
            self.setFixedWidth(end)
            self._slid()
            return
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _slid(self):
        if self.hidden:
            self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The content stays FULL_W wide and slides left as the frame narrows.
        self.inner.setGeometry(self.width() - FULL_W, 0, FULL_W, self.height())
        header = getattr(self.window_, "mac_header", None)
        if header is not None:
            header.make_room(self.width())  # the title slides along and stops clear of the traffic lights

    def window_resized(self, width=None):
        """The sidebar never hides by itself (user 2026-11-20): only View → Hide Sidebar / ⌃⌘S."""

    def sync(self):
        """Mirror the (hidden) tab bar: which pages show, are locked, and which is current."""
        w = self.window_
        try:
            self.objectName()
        except RuntimeError:
            return
        for key, item in self.items.items():
            btn = w.tab_buttons.get(key)
            if btn is None:
                continue
            item.setVisible(not btn.isHidden())
            for prop in ("active", "locked"):
                if item.property(prop) != btn.property(prop):
                    item.setProperty(prop, btn.property(prop))
                    item.style().unpolish(item)
                    item.style().polish(item)
            tip = btn.toolTip() or item.label
            item.setToolTip(tip)
            item.badge = w.fault_count if key == BADGE_TAB else 0
            item.paint_icon()
            item.update()
        from . import ai
        self.assistant.setVisible(ai.enabled(w.settings))
        self.assistant.paint_icon()
        self.card.refresh()


class Header(QFrame):
    """The mockup's top row on every page: page title (and the car under it when the page has no
    toolbar), then that page's toolbar (page.mac_bar), which moves up here once and stays."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QStackedWidget
        self.setObjectName("macHeader")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*HEADER_MARGINS)
        lay.setSpacing(14)
        # page.mac_lead: a column's show/hide button, left of the title (as Finder / Mail / Xcode)
        self.leads = QStackedWidget()
        self._no_lead = QWidget()
        self.leads.addWidget(self._no_lead)
        # Room for the traffic lights while the sidebar is (partly) hidden
        self.window_ = window
        self.lights_space = QWidget()
        self.lights_space.setFixedWidth(0)
        lay.addWidget(self.lights_space)
        title_row = QHBoxLayout()  # lead + title sit closer than the bar's 14 px gaps
        title_row.setSpacing(8)
        title_row.addWidget(self.leads, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addStretch(1)
        self.title = QLabel("")
        self.title.setObjectName("macTitle")
        col.addWidget(self.title)
        window.car_label.setParent(None)
        self.car_label = window.car_label
        col.addWidget(window.car_label)
        col.addStretch(1)
        title_row.addLayout(col)
        lay.addLayout(title_row)
        self.bars = QStackedWidget()
        self.bars.setObjectName("macBars")
        self._empty = QWidget()
        self.bars.addWidget(self._empty)
        lay.addWidget(self.bars, 1)
        # (Interface and module: the connection card at the bottom of the sidebar, as in the mockup.)
        self.setFixedHeight(HEADER_H)

    def make_room(self, sidebar_w):
        """Keep the title clear of the traffic lights: the header starts at the sidebar's edge."""
        lights = TRAFFIC_LIGHTS_W if sys.platform == "darwin" else 0
        need = lights - sidebar_w - HEADER_MARGINS[0]
        self.lights_space.setFixedWidth(max(0, need))
        self.lights_space.setVisible(need > 0)

    def set_page(self, label, page=None):
        self.title.setText(label)
        lead = getattr(page, "mac_lead", None)
        if lead is not None and self.leads.indexOf(lead) < 0:
            lead.setParent(None)
            self.leads.addWidget(lead)
        self.leads.setCurrentWidget(lead if lead is not None else self._no_lead)
        self.leads.setVisible(lead is not None)
        if lead is not None:
            self.leads.setFixedSize(lead.sizeHint())
        bar = getattr(page, "mac_bar", None)
        if bar is not None and self.bars.indexOf(bar) < 0:
            bar.setParent(None)
            _trailing(bar)
            self.bars.addWidget(bar)
        self.bars.setCurrentWidget(bar if bar is not None else self._empty)
        self.car_label.setVisible(False)  # one title line on every page; the car is in the sidebar card


def _trailing(bar):
    """A page's title-bar actions at the right end, as macOS toolbars have them (user 2026-11-23):
    readouts first (right-aligned, next to the buttons), then the buttons with the page's main
    action (declared first) at the far right, its edge on the content's right margin. Pages
    declare their actions in any order of importance; only this places them."""
    from PySide6.QtWidgets import QLabel
    lay = bar.layout()
    if lay is None:
        return
    widgets = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is not None:
            widgets.append((w, lay.stretch(i)))
    while lay.count():
        lay.takeAt(0)  # spacers go: the stretch below replaces them
    labels = [(w, st) for w, st in widgets if isinstance(w, QLabel)]
    buttons = [(w, st) for w, st in widgets if not isinstance(w, QLabel)]
    lay.addStretch(1)
    for w, st in labels:
        w.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(w, st)
    if labels and buttons:
        lay.addSpacing(4)
    for w, st in reversed(buttons):
        lay.addWidget(w, st)
    m = lay.contentsMargins()
    lay.setContentsMargins(m.left(), m.top(), max(0, CONTENT_MARGINS[2] - HEADER_MARGINS[2]), m.bottom())


def adopt_bar(page, layout):
    """Mac: a page's top toolbar row (a QHBoxLayout not yet in any layout) becomes page.mac_bar,
    shown at the right end of the window header (Header._trailing). Returns True if it was taken."""
    if not theme.is_mac():
        return False
    w = QWidget()
    w.setObjectName("macBar")
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    page.mac_bar = w
    return True


# One spacing grid for every Mac page (user 2026-11-20: "alignment uniform… width, spacing, margins"):
# the title bar's text starts 24 px in and its icons end 16 px from the edge (their glyphs then
# line up with the content's 24 px edge); content: 24 px sides, 16 top, 20 bottom; a column's
# heading, rows and footer sit 16 px inside it, its footer buttons level with the content's.
HEADER_MARGINS = (24, 0, 16, 0)
CONTENT_MARGINS = (24, 16, 24, 20)
COLUMN_PAD = 16
ROW_H = 28  # the content's first row (Select channels, segments, …): one height, so it sits at the same y everywhere


# ── LAYOUT RULES: every Mac page (user 2026-11-23: "make rules … a standard layout"; Logger is the
# reference for the content, and side lists are full-height columns: "i liked the full size column").
# Pages hand their parts to the helpers below instead of placing things themselves.
#  1. Title bar: the page name + its main action only (see the RULE at the top of this file).
#  2. Row 1 (optional): the content's first row, ROW_H tall, CONTENT_MARGINS in (x 24, 16 under the
#     title bar). Left: the page's picker (Select channels ▾, pull, tool switch…), then its side
#     list's toggle, then segments. Right: icon buttons, ⋯ last.
#  3. Content: ROW_GAP under row 1 (at row 1's place when there's none), in rounded cards.
#  4. Margins: CONTENT_MARGINS (24 sides, 20 bottom). Nothing runs past them (main_layout).
#  5. A side list is a full-height column (column()): flush left, title bar to window bottom, panel
#     colour, divider on its right, COLUMN_W wide. Its first row is ROW_H at row 1's y, and its
#     contents start 24 px in, so they line up with row 1 on pages without a column. The main
#     area beside it follows 2–4. Its toggle hides the column; if the picker sat in the column's
#     first row, it moves to the front of row 1 at the same spot.
#  6. Row 1 never runs past the margin: when it doesn't fit, its segments show icons only (fit_row).
#     Row controls are ROW_SPACING apart (first_row), so icons land on the same spots on every page.
#  7. Row 1's right-hand icons, from the right: ⋯ More, ⚙ the tab's settings, 🔖 Presets, 🕘 Recent,
#     then the tab's own tools, each in an ICON_SLOT slot (glyph at its left) so they land on the
#     same columns on every tab. A tab skips what it doesn't have (no empty slots); the last slot
#     ends on the right margin, all at row 1's y. Placed by row_icons, never by hand.
ROW_GAP = 10    # row 1 → content
COLUMN_W = 300  # a side column's starting width
COLUMN_MARGINS = (24, 16, 16, 20)


def main_layout(layout):
    """Rules 2–4 on a content area's QVBoxLayout (a page's, or the main area beside a column)."""
    layout.setContentsMargins(*CONTENT_MARGINS)
    layout.setSpacing(ROW_GAP)
    return layout


def column(widget, margins=COLUMN_MARGINS):
    """Rule 5: a side list as a full-height column (theme #macPanel). margins: inside the column
    (pass your own when rows inset themselves, keeping 24 px to the text)."""
    widget.setObjectName("macPanel")
    widget.setProperty("table", False)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    if widget.layout() is not None:
        widget.layout().setContentsMargins(*margins)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def column_split(splitter, width=None):
    """Rule 5: column | main area in a splitter: no gap (the column has its divider), the column
    COLUMN_W wide (or `width`), extra room to the main area. The page's own layout has no margins."""
    splitter.setHandleWidth(1)
    splitter.setChildrenCollapsible(False)
    splitter.setStretchFactor(0, 0)  # without this Qt grows the column proportionally on first show
    splitter.setStretchFactor(1, 1)
    w = max(COLUMN_W, width or 0)  # size hints are tiny before the page is styled: don't trust them
    splitter.setSizes([w, max(sum(splitter.sizes()), w * 3) - w])
    splitter._column_fit = _ColumnFit(splitter)  # …so check again once it's shown
    return splitter


class _ColumnFit(QObject):
    """column_split's check on first show: never narrower than the column's contents (no sideways
    scrolling, nothing cut off), now that the page is styled and its hints are real."""

    def __init__(self, splitter):
        super().__init__(splitter)
        self.splitter = splitter
        splitter.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent, QTimer
        if event.type() == QEvent.Type.Show:
            obj.removeEventFilter(self)
            QTimer.singleShot(0, self.fit)
        return False

    def fit(self):
        sp = self.splitter
        side = sp.widget(0)
        inner = side.widget() if hasattr(side, "widget") and callable(side.widget) else side
        need = inner.minimumSizeHint().width() if inner is not None else 0
        sizes = sp.sizes()
        if len(sizes) == 2 and 0 < sizes[0] < need:
            sp.setSizes([need, max(sum(sizes) - need, 0)])


ROW_SPACING = 10  # between a row's controls and icons (Logger's): icons land on the same spots


ICON_SLOT = 42  # every right-hand icon's slot: an icon with a ▾ fills it, a plain one sits at its left


def icon_slot():
    """A right-hand icon's slot in this interface: as wide as an icon with a ▾ (Mac ICON_SLOT)."""
    from .analyze_tools import tool_metrics
    return tool_metrics(dropdown=True)[0]


class _SlotFollow(QObject):
    """A slot shows and hides with its icon (a hidden icon leaves no gap)."""

    def __init__(self, cell, button):
        super().__init__(cell)
        self.cell = cell
        button.installEventFilter(self)
        cell.setVisible(not button.isHidden())

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.cell.setVisible(not obj.isHidden())
        return False


def row_icons(row, tools=(), recent=None, presets=None, settings=None, more=None):
    """Rule 7: row 1's right-hand icons in the one order every tab uses: the tab's own `tools`,
    then 🕘 `recent`, 🔖 `presets`, ⚙ `settings`, ⋯ `more` (each optional; none leaves a gap). Each
    sits in an ICON_SLOT-wide slot with its glyph at the slot's left, so icons land on the same
    columns on every tab whatever mix of plain and ▾ icons it has. Call it last on the row, after
    its stretch, so the last slot ends on the right margin."""
    from .toolbar import ToolIcon
    icons = [w for w in (*tools, recent, presets, settings, more) if w is not None]
    for w in icons:
        if not isinstance(w, ToolIcon):  # one size rule and one paint path for every row icon
            raise TypeError(f"row_icons takes ToolIcon buttons, not {type(w).__name__}")
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)  # full row height, glyph at the slot's left
        width = w.sizeHint().width()  # Mac 30 plain, 42 with ▾; Classic 36 / 50
        # the last slot is just its icon (⋯ / ⚙, plain on every tab), so it ends on the margin
        cell.setFixedWidth(width if w is icons[-1] else max(icon_slot(), width))
        _SlotFollow(cell, w)
        row.addWidget(cell)
    return row


class _RowFit(QObject):
    """fit_row's watcher: segments show their names while row 1 fits, icons only when it doesn't."""

    def __init__(self, area, row, buttons):
        super().__init__(area)
        self.area, self.row, self.buttons = area, row, list(buttons)
        self.labels = [b.text() for b in self.buttons]
        self.tips = [b.toolTip() for b in self.buttons]  # kept: a segment's own description
        self.full = None  # the row's width with every name shown (measured while they are)
        self.compact = False
        area.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Resize:
            self.fit()
        return False

    def fit(self):
        if not self.compact:  # what the row needs with names shown (stretch labels at their least)
            self.full = self.row.minimumSize().width()
        room = self.area.width() - CONTENT_MARGINS[0] - CONTENT_MARGINS[2]
        compact = bool(self.full) and self.full > room
        if compact == self.compact:
            return
        self.compact = compact
        for b, label, tip in zip(self.buttons, self.labels, self.tips):
            b.setText("" if compact else label)
            b.setToolTip((f"{label}: {tip}" if tip else label) if compact else tip)


def fit_row(area, row, buttons):
    """Rule 6: when row 1 is wider than `area` (the main area's visible part, e.g. a splitter's
    scroll area) allows, the segment `buttons` drop their names and show icons only (names in their
    tooltips), as macOS toolbars do, instead of the row running past the window."""
    return _RowFit(area, row, buttons)


def first_row(layout):
    """Mac: a page's first content row: the shared height (its controls centre on the same line on
    every page, whatever their own heights) and the shared gap between its controls. Takes a
    QHBoxLayout or a wrapping FlowLayout (each of its lines then gets the row height)."""
    from .widgets import FlowLayout
    if isinstance(layout, FlowLayout):
        layout.min_line_height = ROW_H
        layout._spacing = ROW_SPACING
    else:
        layout.addStrut(ROW_H)
        layout.setSpacing(ROW_SPACING)
    return layout


def page_bar():
    """A toolbar holder for page.mac_bar: one row, the mockup's 10 px gaps, no margins."""
    w = QWidget()
    w.setObjectName("macBar")
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    return w, row


def _shortcut_text():
    return "⌃⌘S" if sys.platform == "darwin" else "Ctrl+Shift+S"


def _nav():
    from .main_window import NAV
    return NAV


# ── window: content runs up under the traffic lights ──

def full_size_content(widget):
    """Let the window's content fill the title bar area (title hidden, bar see-through), like
    Finder. No-op off the cocoa platform or on error: the normal title bar then stays."""
    from PySide6.QtGui import QGuiApplication
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return False
    try:
        import ctypes
        from .widgets import _objc
        o = _objc()
        lib = o["lib"]
        sel = lib.sel_registerName
        addr = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
        get_ulong = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(addr)
        set_ulong = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(addr)
        set_long = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(addr)
        win = o["id"](int(widget.winId()), sel(b"window"))
        if not win:
            return False
        mask = get_ulong(win, sel(b"styleMask"))
        set_ulong(win, sel(b"setStyleMask:"), mask | (1 << 15))  # NSWindowStyleMaskFullSizeContentView
        o["set_bool"](win, sel(b"setTitlebarAppearsTransparent:"), True)
        set_long(win, sel(b"setTitleVisibility:"), 1)  # NSWindowTitleHidden
        return True
    except Exception:
        return False


# ── Mac: Settings and Help in their own windows, and the menu bar ──

class SideWindow(QWidget):
    """A page (Settings or Help) in a window of its own, like a Mac app's Settings window."""

    def __init__(self, window, key, label):
        super().__init__(window, Qt.WindowType.Window)
        self.setObjectName("sideWindow")
        self.key, self.window_ = key, window
        self.setWindowTitle(label)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        page = window._ensure_page(key)[0]
        window.stack.removeWidget(page)
        bar = getattr(page, "side_bar", None)  # Settings: its toolbar (title + section tabs) on top
        if bar is not None:
            lay.addWidget(bar)
        lay.addWidget(page)
        page.show()
        saved = window.settings.get(f"mac:window:{key}")
        self.resize(*(saved if isinstance(saved, list) and len(saved) == 2 else (980, 720)))
        self._full_size = False
        if sys.platform == "darwin":
            # Like the main window: content up under the traffic lights (Help's sidebar, Settings'
            # toolbar), title bar see-through; the top strip still moves the window.
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
            from PySide6.QtWidgets import QApplication
            self._drag = WindowDrag(self)
            QApplication.instance().installEventFilter(self._drag)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._full_size:
            self._full_size = full_size_content(self) or True

    def closeEvent(self, event):
        self.window_.settings.set(f"mac:window:{self.key}", [self.width(), self.height()])
        super().closeEvent(event)


def open_side_window(window, key, label):
    wins = window.__dict__.setdefault("_side_windows", {})
    win = wins.get(key)
    if win is None:
        win = wins[key] = SideWindow(window, key, label)
    win.show()
    win.raise_()
    win.activateWindow()
    return win


def build_menu_bar(window):
    """macOS menu bar for the Mac interface: Settings… (⌘,) and About in the app menu, Open Log,
    the sidebar and every page with ⌘1… under View, Help."""
    from PySide6.QtGui import QAction
    bar = window.menuBar()
    bar.setNativeMenuBar(True)
    app_menu = bar.addMenu("PQ35 Flasher")  # macOS moves these items into the app menu
    about = QAction("About PQ35 Flasher", window)
    about.setMenuRole(QAction.MenuRole.AboutRole)
    about.triggered.connect(lambda: _settings_section(window, "About"))
    app_menu.addAction(about)
    prefs = QAction("Settings…", window)
    prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
    prefs.setShortcut(QKeySequence("Ctrl+,"))  # ⌘, (Qt's Ctrl is ⌘ on a Mac)
    prefs.triggered.connect(lambda: window.navigate("settings"))
    app_menu.addAction(prefs)

    file_menu = bar.addMenu("File")
    open_log = file_menu.addAction("Open Log…")
    open_log.setShortcut(QKeySequence.StandardKey.Open)
    open_log.triggered.connect(lambda: _open_log(window))

    view = bar.addMenu("View")
    side = view.addAction("Show Sidebar" if window.sidebar.hidden else "Hide Sidebar")
    side.setShortcut(QKeySequence("Meta+Ctrl+S"))  # ⌃⌘S, as in Finder and Mail
    side.triggered.connect(lambda: window.sidebar.toggle())
    window._sidebar_action = side
    view.addSeparator()
    labels = {key: label for key, _g, label, _c in _nav()}
    n = 0
    for _group, keys in GROUPS:
        for key in keys:
            n += 1
            act = view.addAction(labels[key])
            if n <= 9:
                act.setShortcut(QKeySequence(f"Ctrl+{n}"))
            act.triggered.connect(lambda _=False, k=key: window.navigate(k))
        view.addSeparator()

    help_menu = bar.addMenu("Help")
    guide = help_menu.addAction("PQ35 Flasher Help")
    guide.triggered.connect(lambda: window.navigate("help"))
    assistant = help_menu.addAction("Assistant (⌘K)")
    assistant.triggered.connect(window._open_assistant)
    return bar


def _settings_section(window, section):
    window.navigate("settings")
    page = window.pages.get("settings")
    if hasattr(page, "show_section"):
        page.show_section(section)


def _open_log(window):
    window.navigate("playback")
    page = window.pages.get("playback")
    if hasattr(page, "_open_file"):
        page._open_file()


# ── Mac: table headers read left to right, like Finder ──

class HeaderAlign(QObject):
    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QHeaderView
        from PySide6.QtWidgets import QTableView
        if event.type() == QEvent.Type.Polish:
            if isinstance(obj, QHeaderView) and obj.orientation() == Qt.Orientation.Horizontal:
                obj.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            elif isinstance(obj, QTableView) and type(obj).__name__ != "RawTable":
                obj.setShowGrid(False)  # no lines: zebra rows, like Finder's lists
                obj.setAlternatingRowColors(True)
        return False


class WindowDrag(QObject):
    """The content covers the title bar, so empty space in the window's top strip moves the
    window (and a double-click zooms it), as a Mac title bar does. Buttons and fields there keep
    their clicks: only bare backgrounds (plain widgets, frames, labels) start a move."""
    _BARE = ("QWidget", "QFrame", "QLabel", "QScrollArea", "_FitToWindow", "Header", "Sidebar")

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return False
        if not obj.isWidgetType() or obj.window() is not self.window_ \
                or event.button() != Qt.MouseButton.LeftButton:
            return False
        if type(obj).__name__ not in self._BARE and obj.objectName() not in ("page", "macHeader"):
            return False
        pos = obj.mapTo(self.window_, event.position().toPoint())
        if pos.y() > DRAG_H:
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            w = self.window_
            w.showNormal() if w.isMaximized() else w.showMaximized()
            return True
        handle = self.window_.windowHandle()
        if handle is not None:
            handle.startSystemMove()
            return True
        return False


def install_app_tweaks(app, window=None):
    f = HeaderAlign(app)
    app.installEventFilter(f)
    app._pq35_header_align = f
    if window is not None and sys.platform == "darwin":
        d = WindowDrag(window)
        app.installEventFilter(d)
        app._pq35_window_drag = d
