"""Pieces of the Analyze page kept out of playback_page.py: the Raw data table, channel presets
(smart ones found by name + your own), a menu whose ticks don't close it, and Edit log."""

import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtCore import QObject, QRectF
from PySide6.QtGui import QFont, QColor, QFontDatabase, QFontMetrics, QPainter
from PySide6.QtWidgets import QHeaderView, QMenu, QPushButton, QStyle, QStyleOptionHeader, QToolButton

from . import log_export

# ── a menu you can tick several things in ──


class StickyMenu(QMenu):
    """QMenu whose checkable items toggle without closing it (for ticking several channels)."""

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


# ── Raw data ──


DOT_ROLE = Qt.ItemDataRole.UserRole + 10  # header: the channel's line colour, or None
DOT = 7    # px, the same as the sidebar's dot
DOT_GAP = 6


class RawHeader(QHeaderView):
    """Column headers with the channel's colour dot (sidebar size) before the name."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)

    def _label_rect(self, rect, section):
        opt = QStyleOptionHeader()
        opt.initFrom(self)
        opt.rect = rect
        opt.section = section
        return self.style().subElementRect(QStyle.SubElement.SE_HeaderLabel, opt, self)

    def paintSection(self, painter, rect, section):
        model = self.model()
        if model is None:
            return super().paintSection(painter, rect, section)
        # The style paints the section (background, bottom border) with no text; the name and
        # its dot are drawn here, centred over the column like the values under them.
        model.blank_header = True
        super().paintSection(painter, rect, section)
        model.blank_header = False
        name = model.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
        color = model.headerData(section, Qt.Orientation.Horizontal, DOT_ROLE)
        from . import theme
        label = self._label_rect(rect, section)
        font = self.font()
        mac = theme.is_mac()
        if mac:  # the Logger table's header: small, semibold, over its column like the values
            font.setPointSizeF(max(theme.font_size() - 2, 9))
            font.setWeight(QFont.Weight.DemiBold)
            label = rect.adjusted(12, 0, -12, 0)
        else:
            font.setBold(True)
        fm = QFontMetrics(font)
        room = label.width() - (DOT + DOT_GAP if color else 0)
        text = fm.elidedText(name, Qt.TextElideMode.ElideRight, max(room, 0))
        width = fm.horizontalAdvance(text) + (DOT + DOT_GAP if color else 0)
        if mac:
            x = label.left() if section == 0 else label.left() + max(label.width() - width, 0)
        else:
            x = label.left() + max((label.width() - width) / 2, 0)
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if color:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(x, rect.center().y() - DOT / 2 + 0.5, DOT, DOT))
            x += DOT + DOT_GAP
        painter.setFont(font)
        painter.setPen(QColor(theme.colors()["muted"]))
        painter.drawText(QRectF(x, rect.top(), label.right() - x + 1, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class RawModel(QAbstractTableModel):
    """Time + channels as a table; values computed on demand so 100k-row logs stay instant."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.times = None
        self.names = []
        self.cols = []
        self.colors = {}   # channel -> line colour, for the dot in its header
        self.blank_header = False  # RawHeader draws the names itself
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

    def set_data(self, times, names, columns, colors=None):
        self.beginResetModel()
        self.times = times
        self.names = list(names)
        self.cols = [columns[n] for n in self.names]
        self.colors = dict(colors or {})
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.times is None else len(self.times)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.times is None else len(self.names) + 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            r, c = index.row(), index.column()
            v = self.times[r] if c == 0 else self.cols[c - 1][r]
            return "" if np.isnan(v) else (f"{v:.2f}" if c == 0 else f"{v:.6g}")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            from . import theme
            if theme.is_mac():  # as the Logger's table: time on the left, numbers on the right
                return int((Qt.AlignmentFlag.AlignLeft if index.column() == 0 else Qt.AlignmentFlag.AlignRight)
                           | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.FontRole:
            return self.mono
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return str(section + 1) if role == Qt.ItemDataRole.DisplayRole else None
        name = "Time (s)" if section == 0 else self.names[section - 1]
        if role == Qt.ItemDataRole.DisplayRole:
            # one line; the header cuts it off with … when the column is narrower
            return "" if self.blank_header else name
        if role == Qt.ItemDataRole.ToolTipRole:
            return name
        if role == DOT_ROLE:
            return self.colors.get(name)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None


# ── presets ──

PRESETS_KEY = "playback:presets"

# Smart presets: picked from whatever channels the log has, by name, so they work on any log
# (Standard or Integrated Engineering naming). (name, description, [patterns]); a channel joins
# when any pattern matches its name. Engine speed goes in every one as the reference.
SMART_PRESETS = (
    ("Boost control", "Boost actual vs requested, wastegate (N75) duty, load",
     [r"boost", r"n75", r"wastegate", r"\bload\b", r"throttle"]),
    ("Timing and knock", "Ignition timing, per-cylinder retard, knock sensors",
     [r"ignition", r"timing", r"retard", r"knock"]),
    ("Fueling and lambda", "Lambda, fuel trims, rail pressure, injection, pump duty",
     [r"lambda", r"\bafr\b", r"fuel", r"rail", r"inject", r"trim", r"\bmaf\b", r"air mass"]),
    ("Temperatures", "Coolant, intake, oil and fuel temperatures",
     [r"temp"]),
    ("Driving", "Speed, gear, throttle, accelerator",
     [r"speed", r"gear", r"throttle", r"accelerat", r"pedal"]),
)
_RPM = re.compile(r"engine speed|\brpm\b", re.I)


def base(name):
    return log_export.split_header(name)[0].lower()


def smart_presets(headers):
    """[(name, description, [headers])] for the smart presets that find 2+ channels here."""
    rpm = [h for h in headers if _RPM.search(h)][:1]
    out = []
    for name, desc, patterns in SMART_PRESETS:
        found = [h for h in headers if h not in rpm and any(re.search(p, h, re.I) for p in patterns)]
        if len(found) >= 2:
            out.append((name, desc, rpm + found))
    return out


def saved_presets(settings):
    data = settings.get(PRESETS_KEY)
    return data if isinstance(data, dict) else {}


def save_preset(settings, name, headers):
    data = dict(saved_presets(settings))
    data[name] = [base(h) for h in headers]  # by name without unit, so °C/°F logs both match
    settings.set(PRESETS_KEY, data)


def delete_preset(settings, name):
    data = dict(saved_presets(settings))
    data.pop(name, None)
    settings.set(PRESETS_KEY, data)


def rename_preset(settings, old, new):
    """Rename a saved preset, keeping its place in the list."""
    data = saved_presets(settings)
    settings.set(PRESETS_KEY, {(new if k == old else k): v for k, v in data.items()})


def resolve(saved_names, headers):
    """A saved preset's channels that this log has, in the preset's order."""
    by_base = {base(h): h for h in headers}
    return [by_base[n] for n in saved_names if n in by_base]


# ── Edit log ──


def trim_csv(src: Path, dst: Path, a: float, b: float):
    """Copy src to dst keeping header/units rows and only the data rows timed a..b."""
    kept = 0
    with open(src, "r", encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="") as out:
        for line in f:
            first = line.split(",", 1)[0].strip().strip('"')
            try:
                t = log_export.parse_time(first)
            except Exception:
                t = None
            if t is None or not isinstance(t, (int, float)):
                out.write(line)  # header-like row (names, units, VIN line...)
                continue
            if a <= t <= b:
                out.write(line)
                kept += 1
    if not kept:
        dst.unlink(missing_ok=True)
        raise ValueError("No rows in that part of the log.")
    return kept


def relink_projects(old: Path, new: Path):
    """After a rename, point any project that lists the old file at the new one."""
    try:
        from .projects import Project
    except Exception:
        return
    for project in Project.all():
        logs = project.data.get("logs", [])
        if str(old) in logs:
            project.data["logs"] = [str(new) if p == str(old) else p for p in logs]
            project.save()


# ── menus: small capital headings and icons, so a dropdown's structure reads at a glance ──

def menu_header(menu, text, before=None):
    """Start a new group of menu items: a separator (no title; the user preferred plain
    separators). `text` names the group in the code only. Qt drops a separator at the very top
    of a menu or right after another one, so groups can start anywhere."""
    menu.setSeparatorsCollapsible(True)
    return menu.insertSeparator(before) if before is not None else menu.addSeparator()


def menu_icon(glyph, color=None):
    from .main_window import _material_icon
    from . import theme
    return _material_icon(glyph, 18, color or theme.colors()["tab_text"])


def menu_action(menu, text, glyph, slot=None, enabled=True, tip=""):
    """A menu item with a Material icon."""
    act = menu.addAction(menu_icon(glyph), text)
    act.setIconVisibleInMenu(True)  # macOS hides menu icons unless asked (AA_DontShowIconsInMenus)
    act.setEnabled(enabled)
    if tip:
        act.setToolTip(tip)
    if slot is not None:
        act.triggered.connect(slot)
    return act


def menu_toggle(menu, text, glyph, on, slot, enabled=True, tip=""):
    """An on/off item shown the way the toolbar shows its toggles: the icon in the accent colour
    while on, grey while off (an icon hides the menu's own check mark). slot gets the new state."""
    from . import theme
    c = theme.colors()
    act = menu.addAction(menu_icon(glyph, c["accent"] if on else c["tab_text"]), text)
    act.setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
    act.setEnabled(enabled)
    act.setToolTip((tip + "\n" if tip else "") + ("On: click to turn off" if on else "Off: click to turn on"))
    act.triggered.connect(lambda _=False: slot(not on))
    return act


def menu_sub(menu, text, glyph, sub=None):
    """A submenu with an icon (sub: an existing QMenu to add, else a new one)."""
    if sub is None:
        sub = menu.addMenu(text)
    else:
        menu.addMenu(sub)
    sub.menuAction().setIcon(menu_icon(glyph))
    sub.menuAction().setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
    sub.setToolTipsVisible(True)
    return sub


def tool_metrics(dropdown=False):
    """Toolbar icon buttons: (button w, h, icon strip w, glyph px). Classic: 36 (50 with ▾) x 34,
    22 px glyphs. Mac (mockup .tbtn): 30 (42 with a chevron) x 28, 16 px SF Symbols (measured
    against the mockup at 2x: its bookmark is 25 px tall, history 26 px; 18 px came out 15 % bigger)."""
    from . import theme
    if theme.is_mac():
        g = theme.icon_px("toolbar")  # 19 at 13 pt text: a step bigger than the text, as AppKit toolbars
        return (42 if dropdown else 30), 28, g + (12 if dropdown else 0), g
    return (50 if dropdown else 36), 34, (36 if dropdown else 22), 22


def tool_color(checked):
    """A toolbar icon's colour. Classic: accent while a toggle is on. Mac: always the grey glyph
    colour; an on toggle shows a soft square behind it instead (QPushButton#iconTool:checked)."""
    from . import theme
    c = theme.colors()
    if checked and not theme.is_mac():
        return c["accent"]
    return c["tab_text"]


def _optical(name):
    """Mac: (point scale, box ratio) for a toolbar symbol drawn larger than the rest, else None."""
    from . import theme
    if not theme.is_mac():
        return None
    from . import sf_symbols
    return sf_symbols.OPTICAL_SCALE.get(sf_symbols.MATERIAL_TO_SF.get(name))


def icon_size(glyph, dropdown=False):
    """The QSize a toolbar button's icon is drawn at (wider for an OPTICAL_SCALE symbol)."""
    from PySide6.QtCore import QSize
    _w, _h, strip, g = tool_metrics(dropdown)
    wide = None if dropdown else _optical(glyph.partition("@")[0])
    return QSize(round(g * wide[1]) if wide else strip, g)


def toolbar_icon(glyph, color, dropdown=False):
    """A toolbar icon (Classic: Material glyph, 22 px; Mac: SF Symbol, theme.icon_px()); dropdown: with a
    small ▾ / chevron after it."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from . import theme
    from .main_window import _material_icon
    _w, _h, strip, g = tool_metrics(dropdown)
    name, _, rot = glyph.partition("@")  # "glyph@90" = rotated glyph
    wide = _optical(name)
    if wide and not dropdown:  # Mac: a text-like symbol (f(x)) drawn as big as its neighbours
        from . import sf_symbols
        scale, ratio = wide
        sym = sf_symbols.icon(sf_symbols.MATERIAL_TO_SF[name], g, color, point=g * 0.8 * scale,
                              width=round(g * ratio))
        if sym is not None:
            return sym
    icon = _material_icon(name, g, color, int(rot or 0))
    if not dropdown:
        return icon
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    px = QPixmap(int(strip * dpr), int(g * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.drawPixmap(0, 0, icon.pixmap(QSize(g, g)))
    if theme.is_mac():  # a small chevron 2 px after the glyph
        p.drawPixmap(g + 2, max(0, (g - 10) // 2), _material_icon("arrow_drop_down", 10, color).pixmap(QSize(10, 10)))
    else:
        p.drawPixmap(20, 3, _material_icon("arrow_drop_down", 16, color).pixmap(QSize(16, 16)))
    p.end()
    return QIcon(px)


def manage_presets(parent, get, rename, delete, empty_text, tip=None):
    """Rename or delete saved presets. get() -> {name: anything}; rename(old, new); delete(name);
    tip(name, data) -> a row's tooltip."""
    from PySide6.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                                   QPushButton, QVBoxLayout)
    from .popups import InputDialog, MessageBox, OverlayDialog
    dlg = OverlayDialog(parent)
    dlg.setWindowTitle("Manage presets")
    lay = QVBoxLayout(dlg)
    lst = QListWidget()
    lst.setMinimumSize(320, 220)
    lay.addWidget(lst)
    empty = QLabel(empty_text)
    empty.setObjectName("muted")
    empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(empty)
    row = QHBoxLayout()
    rename_button = QPushButton(menu_icon("edit"), "Rename…")
    delete_button = QPushButton(menu_icon("delete"), "Delete")
    row.addWidget(rename_button)
    row.addWidget(delete_button)
    row.addStretch(1)
    lay.addLayout(row)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dlg.reject)
    box.accepted.connect(dlg.accept)
    lay.addWidget(box)

    def fill(select=None):
        lst.clear()
        for name, data in get().items():
            lst.addItem(name)
            if tip:
                lst.item(lst.count() - 1).setToolTip(tip(name, data))
            if name == select:
                lst.setCurrentRow(lst.count() - 1)
        if lst.currentRow() < 0 and lst.count():
            lst.setCurrentRow(0)
        lst.setVisible(lst.count() > 0)
        empty.setVisible(lst.count() == 0)
        rename_button.setEnabled(lst.currentRow() >= 0)
        delete_button.setEnabled(lst.currentRow() >= 0)

    def do_rename():
        it = lst.currentItem()
        if it is None:
            return
        old = it.text()
        new, ok = InputDialog.getText(parent, "Rename preset", "New name:", text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        if new in get() and MessageBox.question(
                parent, "Rename preset", f"“{new}” already exists. Replace it?") != MessageBox.StandardButton.Yes:
            return
        rename(old, new)
        fill(new)

    def do_delete():
        it = lst.currentItem()
        if it is None:
            return
        if MessageBox.question(parent, "Delete preset", f"Delete “{it.text()}”?") != MessageBox.StandardButton.Yes:
            return
        delete(it.text())
        fill()

    rename_button.clicked.connect(do_rename)
    delete_button.clicked.connect(do_delete)
    lst.itemDoubleClicked.connect(lambda _: do_rename())
    lst.currentRowChanged.connect(lambda r: (rename_button.setEnabled(r >= 0), delete_button.setEnabled(r >= 0)))
    fill()
    dlg.exec()


SIDEBAR_GLYPH = "\U000ffe94"  # speed_4_progress, by codepoint (its name ligature draws junk)


def icon_button(glyph, tip, checkable=False, dropdown=False):
    """A toolbar icon button like Analyze's (36 x 34, or 50 x 34 with a ▾): grey icon, accent
    while a checkable one is on; follows the theme."""
    from PySide6.QtWidgets import QPushButton
    from . import theme
    b = QPushButton()
    b.setObjectName("iconTool")
    b.setToolTip(tip)
    b.setCheckable(checkable)
    w, h, strip, g = tool_metrics(dropdown)
    b.setFixedSize(w, h)
    b.setIconSize(icon_size(glyph, dropdown))

    def tint(*_):
        try:
            b.setIcon(toolbar_icon(glyph, tool_color(b.isCheckable() and b.isChecked()), dropdown))
        except RuntimeError:  # deleted
            pass
    tint()
    b.toggled.connect(tint)
    theme.on_apply(tint)
    return b


class MenuIconButton(QToolButton):
    """Grey icon button (like ⚙ and the dashboards button) that opens a menu, refilled by
    fill(menu) each time it opens."""

    def __init__(self, glyph, tip, fill, parent=None, dropdown=False):
        super().__init__(parent)
        self.glyph, self._fill, self.dropdown = glyph, fill, dropdown
        self.setToolTip(tip)
        from PySide6.QtCore import QSize
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        # same as icon_button, so ⚙ matches the icons beside it (50 wide with a ▾)
        w, h, strip, g = tool_metrics(dropdown)
        self.setFixedSize(w, h)
        self.setIconSize(QSize(strip, g))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QToolButton { padding: 0; } QToolButton::menu-indicator { image: none; width: 0; }")
        self.menu_ = QMenu(self)
        self.menu_.setToolTipsVisible(True)
        self.menu_.aboutToShow.connect(lambda: (self.menu_.clear(), self._fill(self.menu_)))
        self.setMenu(self.menu_)
        anchor_menu(self.menu_, self)
        from . import theme
        self._restyle()
        theme.on_apply(self._restyle)

    def _restyle(self):
        from . import theme
        if self.dropdown:
            self.setIcon(toolbar_icon(self.glyph, theme.colors()["tab_text"], dropdown=True))
            return
        self.setIcon(toolbar_icon(self.glyph, theme.colors()["tab_text"]))


class RunButton(QPushButton):
    """Start / Stop as a round 34 px button with a filled Material Symbol: `glyph` in the theme
    colour `color` (Logger: red fiber_manual_record; Live View: green play_arrow) or, while
    running (set_running), a red stop square in a red ring. Greyed out while disabled."""

    SIZE = 34
    GLYPH = 26  # glyph box; the dot / square / triangle inside is about half that

    def __init__(self, glyph, color, parent=None, stop_glyph="stop", fill=True, frame=True):
        super().__init__(parent)
        self.glyph, self.color = glyph, color
        self.stop_glyph, self.fill = stop_glyph, fill
        # frame=False: no circle of our own; the glyph's own circle is the button (bigger glyph)
        self.frame = frame
        self.glyph_px = self.GLYPH if frame else self.SIZE
        self.running = False
        from . import theme
        self.mac = theme.is_mac()
        if self.mac:  # mockup .rec: a 28 px pill, icon + "Start" / "Record"
            from PySide6.QtGui import QFontMetrics
            self.label = "Start" if glyph == "play_arrow" else "Record"
            f = self.font()
            f.setWeight(f.Weight.Medium)
            width = max(QFontMetrics(f).horizontalAdvance(t) for t in (self.label, "Stop"))
            self.setFixedSize(9 + 12 + 7 + width + 12, 28)
        else:
            self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_running(self, on):
        self.running = on
        self.update()

    def _paint_mac(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        from . import sf_symbols, theme
        c = theme.colors()
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        key = "err" if self.running else self.color
        color = c[key] if enabled else c["muted"]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # The page's main action, so it stands out: a tinted pill in its own colour (Start green,
        # Record / Stop red); still faintly tinted while it can't be used yet.
        bg = theme._mix(c["bg"], c[key], (0.26 if hover else 0.18) if enabled else 0.08)
        p.setBrush(QColor(bg))
        color = c[key] if enabled else theme._mix(c["muted"], c[key], 0.35)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        symbol = "stop.fill" if self.running else ("play.fill" if self.color == "ok" else "circle.fill")
        icon = sf_symbols.icon(symbol, 12, color, point=9 if symbol != "circle.fill" else 10)
        if icon is not None:
            p.drawPixmap(9, (self.height() - 12) // 2, icon.pixmap(12, 12))
        f = self.font()
        f.setWeight(f.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(c["text"] if enabled else c["muted"]))
        p.drawText(QRectF(28, 0, self.width() - 28, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Stop" if self.running else self.label)
        p.end()

    set_recording = set_running  # the Logger's word for it

    def paintEvent(self, event):
        if self.mac:
            self._paint_mac()
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter, QPen
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        live = self.running and enabled
        key = "err" if self.running else self.color
        color = c[key] if enabled else c["muted"]
        tint = c["err_bg"] if key == "err" else c.get(f"{key}_bg", c["accent_bg"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.frame:
            ring = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(color if live else c["border"]), 1))
            p.setBrush(QColor(tint if (hover or live) else c["surface2"]))
            p.drawEllipse(ring)
        elif hover:
            p.setOpacity(0.8)  # the only hover hint without a circle of our own
        size = self.glyph_px
        icon = _material_icon(self.stop_glyph if self.running else self.glyph, size, color,
                              fill=self.fill)
        offset = (self.SIZE - size) // 2
        p.drawPixmap(offset, offset, icon.pixmap(size, size))
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


# ── Dropdowns: every button's menu opens like Analyze's ⋮ ──

def _place(popup, anchor, align=None, dy=0):
    """Move a shown popup under `anchor`: right edges lined up when the anchor is in the right half
    of its window (left edges otherwise), and always inside the window."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication
    win = anchor.window()
    w_left = win.mapToGlobal(QPoint(0, 0)).x()
    a = anchor.mapToGlobal(QPoint(0, 0))
    width, height = popup.width(), popup.height()
    if align is None:
        align = "right" if a.x() + anchor.width() / 2 > w_left + win.width() / 2 else "left"
    x = a.x() + anchor.width() - width if align == "right" else a.x()
    x = max(w_left + 4, min(x, w_left + win.width() - width - 4))
    y = a.y() + anchor.height() + dy
    screen = QGuiApplication.screenAt(a) or QGuiApplication.primaryScreen()
    if y + height > screen.availableGeometry().bottom():
        y = max(screen.availableGeometry().top(), a.y() - height - dy)
    popup.move(x, y)


class _MenuAnchor(QObject):
    def __init__(self, menu, anchor, align, dy):
        super().__init__(menu)
        self.anchor, self.align, self.dy = anchor, align, dy
        menu.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Show and self.anchor is not None:
            try:
                _place(obj, self.anchor, self.align, self.dy)
            except RuntimeError:  # anchor deleted
                self.anchor = None
        return False


def anchor_menu(menu, anchor, align=None, dy=0):
    """Make `menu` always open under `anchor` the app's way (see _place), however it's opened
    (QToolButton/QPushButton setMenu, or exec). Safe to call again (re-anchors)."""
    old = getattr(menu, "_pq35_anchor", None)
    if old is not None:
        old.anchor, old.align, old.dy = anchor, align, dy
    else:
        menu._pq35_anchor = _MenuAnchor(menu, anchor, align, dy)
    return menu


def popup_menu(menu, anchor, align=None, dy=0):
    """Open `menu` under `anchor`, placed the app's way, and wait for a choice."""
    anchor_menu(menu, anchor, align, dy)
    return menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
