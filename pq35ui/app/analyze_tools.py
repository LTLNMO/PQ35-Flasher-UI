"""Pieces of the Analyze page kept out of playback_page.py: the Raw data table, channel presets
(smart ones found by name + your own), a menu whose ticks don't close it, and Edit log."""

import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QColor, QFontDatabase, QFontMetrics, QPainter
from PySide6.QtWidgets import QHeaderView, QMenu, QStyle, QStyleOptionHeader

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
            except ValueError:  # the header row, or a line that isn't data
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
    except ImportError:
        return
    for project in Project.all():
        logs = project.data.get("logs", [])
        if str(old) in logs:
            project.data["logs"] = [str(new) if p == str(old) else p for p in logs]
            project.save()


# ── menus: small capital headings and icons, so a dropdown's structure reads at a glance ──

SIDEBAR_GLYPH = "\U000ffe94"  # speed_4_progress, by codepoint (its name ligature draws junk)


# ── Dropdowns: every button's menu opens like Analyze's ⋮ ──
