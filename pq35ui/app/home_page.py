"""Dashboard: a big Scan button (everything starts with a scan), the
all-modules fault codes, your modules with their last backup/flash, recent
datalogs with peak values, and recent projects, in two half-width columns."""
import csv
import re
from pathlib import Path

import time

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .log_export import parse_time, split_header
from .pages import Page
from .storage import EcuRecord, datalogs_dir, modules_dir
from . import theme
from .widgets import Badge, Card, muted

PEAK_HINTS = (("rpm", ("engine speed", "rpm")), ("boost", ("boost pressure actual", "actual boost", "boost")),
              ("coolant", ("coolant",)), ("knock", ("knock", "retard")))


def _table(headers, rows=4, stretch=(0,)):
    """A Dashboard table that always shows *rows* rows (the page scrolls before they're squashed)
    and never scrolls sideways: the first column takes the spare width."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    header = t.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    for col in stretch:
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
    header.setStretchLastSection(False)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(28)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.rows = rows
    _fit_rows(t)
    return t


def _fit_rows(t):
    """Minimum height for the table's *rows* rows under its header (call again if the header grows)."""
    row_h = t.verticalHeader().defaultSectionSize()
    t.setMinimumHeight(t.horizontalHeader().sizeHint().height() + t.rows * row_h + 2 * t.frameWidth() + 2)


def log_summary(path: Path, max_bytes=8_000_000):
    """Duration and a few peak values of a datalog (cheap: skipped for huge files)."""
    out = {"duration": None, "peaks": {}}
    try:
        if path.stat().st_size > max_bytes:
            return out
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader, [])
            cols = {}
            for label, needles in PEAK_HINTS:
                # Needles in order of preference: the first one that matches any column wins.
                for needle in needles:
                    i = next((i for i, h in enumerate(headers[1:], 1) if needle in h.lower()), None)
                    if i is not None:
                        cols[label] = i
                        break
            peaks = {label: None for label in cols}
            first = last = None
            for row in reader:
                try:
                    t = parse_time(row[0])
                except (ValueError, IndexError):
                    continue
                first = t if first is None else first
                last = t
                for label, i in cols.items():
                    try:
                        v = float(row[i])
                    except (ValueError, IndexError):
                        continue
                    peaks[label] = v if peaks[label] is None else max(peaks[label], v)
        if first is not None:
            out["duration"] = last - first
        out["peaks"] = {k: (v, split_header(headers[cols[k]])[1]) for k, v in peaks.items() if v is not None}
    except OSError:
        pass
    return out


def _press(button):
    """Click a page's button if it can be used right now; False if it's greyed out."""
    if button is None or not button.isEnabled():
        return False
    button.click()
    return True


class _Columns(QWidget):
    """The Dashboard's two columns. The page only counts its sections at their minimum height,
    which misses text that wraps in a half-width card, so report the taller column with each
    card's height at its real width (else the page under-sizes the cards and they overlap)."""

    def __init__(self):
        super().__init__()
        self.columns = []  # [[card, ...], ...]
        self._last_min = None

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        tallest = 0
        for cards in self.columns:
            height = 0
            for card in cards:
                if card.isHidden():
                    continue
                need = card.minimumSizeHint().height()
                if card.hasHeightForWidth() and card.width() > 0:
                    need = max(need, card.heightForWidth(card.width()))  # wrapped lines counted
                height += need
            height += 14 * max(len(cards) - 1, 0)
            tallest = max(tallest, height)
        return QSize(hint.width(), max(hint.height(), tallest))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        current = self.minimumSizeHint()
        if current != self._last_min:  # wrapped lines changed with the width
            self._last_min = current
            self.updateGeometry()


_DYNO_CACHE = {}
# Workshop (VCDS) addresses: what people know a module by. The family's own number is its CAN id.
WORKSHOP_ADDR = {"engine": 0x01, "transmission": 0x02, "abs": 0x03, "haldex-gen4": 0x22,
                 "pq-eps": 0x44, "pq-bcm": 0x09, "bcm": 0x09}


def last_dyno(settings):
    """The newest dyno session's best pull: {whp, wtq, when, pulls, name} or None (cached by file)."""
    folder = datalogs_dir() / "Dyno"
    try:
        files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:3]:  # a session without a usable pull: try the one before
        key = (str(path), path.stat().st_mtime)
        if key not in _DYNO_CACHE:
            _DYNO_CACHE[key] = _dyno_summary(settings, path)
        if _DYNO_CACHE[key]:
            return _DYNO_CACHE[key]
    return None


def _dyno_summary(settings, path):
    try:
        from . import cars, dyno, log_export
        from .playback_page import _parse_csv
        headers, times, columns = _parse_csv(path)
        chans = dyno.find_channels(headers)
        pulls = dyno.find_pulls(times, columns, chans)
        car_id = cars.for_vin(settings, log_export.log_vin(path)) or ""
        car = cars.dyno_car(cars.get(settings, car_id), dict(cars.SESSION_DEFAULTS, car_id=car_id))
        best = None
        for pull in pulls:
            res = dyno.analyse(times, columns, pull, car, chans=chans)
            if "error" not in res and (best is None or res["peaks"]["whp"] > best["peaks"]["whp"]):
                best = res
        if best is None:
            return None
        return {"whp": best["peaks"]["whp"], "wtq": best["peaks"]["wtq"], "pulls": len(pulls),
                "when": path.stat().st_mtime, "name": path.stem}
    except Exception:
        return None


def _when(ts):
    """'today 12:14', 'yesterday 18:02' or '12 Nov'."""
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d"):
        return "today " + time.strftime("%H:%M", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)):
        return "yesterday " + time.strftime("%H:%M", time.localtime(ts))
    return time.strftime("%-d %b", time.localtime(ts)) if hasattr(time, "strftime") else day


class _Tile(QFrame):
    """Mac Dashboard summary tile: small title, a big number, a detail line. Click: on_click."""

    def __init__(self, title, on_click=None):
        super().__init__()
        self.setObjectName("macTile")
        self.on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        t = QLabel(title.upper())
        t.setObjectName("cardTitle")
        lay.addWidget(t)
        self.big = QLabel("")
        self.big.setObjectName("macBig")
        self.big.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.big)
        self.detail = muted("")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        lay.addStretch(1)

    def mouseReleaseEvent(self, e):
        if self.on_click and e.button() == Qt.MouseButton.LeftButton:
            self.on_click()

    def set(self, number, color, unit, detail):
        c = theme.colors()
        self.big.setText(f"<span style='color:{color}'>{number}</span>"
                         + (f"<span style='font-size:{theme.font_size()}pt; font-weight:500; color:{c.get('sub', c['muted'])}'>"
                            f"&nbsp;{unit}</span>" if unit else ""))
        self.detail.setText(detail)


def _backup_pill(text, level):
    """A coloured pill in a table cell, left-aligned and vertically centred."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(4, 0, 4, 0)
    badge = Badge(text, level)
    from PySide6.QtWidgets import QSizePolicy
    badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)  # a pill, not the whole cell
    if theme.is_mac():  # mockup .badge: 11 pt semibold, 2 px 7 px
        lay.setContentsMargins(8, 0, 4, 0)
        f = badge.font()
        f.setPointSize(theme.font_size() - 2)
        f.setWeight(f.Weight.DemiBold)
        badge.setFont(f)
        badge.adjustSize()
    else:
        bold = badge.font()
        bold.setBold(True)
        from PySide6.QtGui import QFontMetrics
        badge.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 26)  # + the pill's padding
    lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addStretch(1)
    return holder


class HomePage(Page):
    title = "Dashboard"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        # Everything starts with a scan, so the Dashboard's one big button is Scan.
        # (Interface and module already show in the top bar.)
        scan_card = Card()
        self.find_button = QPushButton("Quick Scan")
        self.find_button.setObjectName("primary")
        self.find_button.setProperty("hero", True)  # theme.py: big button
        self.find_button.clicked.connect(self._find_modules)
        scan_card.add(self.find_button)
        self.find_hint = muted("")
        self.find_hint.setWordWrap(True)
        self.find_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_card.add(self.find_hint)

        # Every module's fault codes (shared with Diagnostics, which shows the chosen module).
        from .health import controller
        self.faults = controller(state)

        self.modules_card = Card("Your modules")
        self.modules_card.setToolTip("Every module you've connected to, with its last backup and flash.")
        self.modules = _table(["Module", "Backups", "Last backup", "Last flash"])
        self.modules_card.add(self.modules)

        self.logs_card = Card("Recent datalogs")
        self.logs = _table(["Log", "Length", "rpm", "Boost", "Coolant", "Knock"], 5)
        for c in range(2, 6):
            self.logs.horizontalHeaderItem(c).setToolTip("Peak value in the log")
        self.logs.cellDoubleClicked.connect(self._open_log)
        self.logs.installEventFilter(self)  # narrow card: drop Knock, then Coolant, before the names
        self.logs_card.add(self.logs)
        lrow = QHBoxLayout()
        open_log = QPushButton("Open in Analyze")
        open_log.clicked.connect(lambda: self._open_log(self.logs.currentRow(), 0))
        lrow.addWidget(open_log)
        lrow.addWidget(muted("or double-click a log"))
        lrow.addStretch(1)
        self.logs_card.add(lrow)

        self.projects_card = Card("Projects")
        self.projects = _table(["Project", "Ver.", "Updated"])
        self.projects.cellDoubleClicked.connect(lambda *_: state.open_tab.emit("projects"))
        self.projects_card.add(self.projects)
        go = QPushButton("Open Projects")
        go.clicked.connect(lambda: state.open_tab.emit("projects"))
        self.projects_card.add(go)

        self.mac = theme.is_mac()
        if self.mac:
            self._build_mac(scan_card, open_log, go)
            self._log_paths = []
            self._cache = {}
            state.changed.connect(lambda: QTimer.singleShot(0, self.refresh))
            state.faults_changed.connect(lambda *_: QTimer.singleShot(0, self._fill_tiles))
            return
        # Two half-width columns: the car right now on the left, your files on the right.
        # Spare height goes to the tables (more rows), not to an empty gap.
        columns_box = _Columns()
        columns = QHBoxLayout(columns_box)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(14)
        for cards in (((scan_card, 0), (self.faults.overview_card, 0), (self.logs_card, 1)),
                      ((self.modules_card, 2), (self.projects_card, 1))):
            col_box = QWidget()  # equal halves: neither column's contents may widen it
            col_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            col = QVBoxLayout(col_box)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(14)
            for card, stretch in cards:
                col.addWidget(card, stretch)
            columns_box.columns.append([card for card, _ in cards])
            columns.addWidget(col_box, 1)
        self.health_card = self.faults.overview_card
        self.layout_.addWidget(columns_box, 1)
        self._log_paths = []
        self._cache = {}
        # after the main window has re-enabled tabs for the new module
        state.changed.connect(lambda: QTimer.singleShot(0, self.refresh))

    # --- Mac interface layout (the mockup's Dashboard) ---

    def _build_mac(self, scan_card, open_log, go):
        """Scan card and two tiles on top; modules on the left, recent logs and projects on the right."""
        from . import sf_symbols
        scan = Card()
        scan.setProperty("tile", True)  # the mockup's KPI-card shade, a touch lighter than tables
        row = QHBoxLayout()
        row.setSpacing(18)
        icon = QLabel()
        icon.setObjectName("macScanIcon")
        icon.setFixedSize(64, 64)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sym = sf_symbols.icon("dot.radiowaves.left.and.right", 34, "#ffffff", "medium")
        if sym is not None:
            icon.setPixmap(sym.pixmap(34, 34))
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("Scan the car")
        title.setObjectName("macScanTitle")
        col.addWidget(title)
        self.find_hint.setAlignment(Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self.find_hint)
        row.addLayout(col, 1)
        self.find_button.setText("Scan")
        self.find_button.setProperty("hero", False)
        self.find_button.setMinimumWidth(96)
        row.addWidget(self.find_button, 0, Qt.AlignmentFlag.AlignVCenter)
        scan.add(row)
        scan_card.hide()

        self.fault_tile = _Tile("Fault codes", lambda: self.state.open_tab.emit("diagnostics"))
        self.fault_tile.setToolTip("Every module's fault codes from the last scan. Click: Diagnostics.")
        self.dyno_tile = _Tile("Last dyno", lambda: self.state.open_tab.emit("dyno"))
        self.dyno_tile.setToolTip("The best pull of your newest dyno session. Click: Dyno.")
        # One 4-column grid for both rows, so the card edges line up exactly
        # (two separate rows with 2:1:1 and 1:1 stretch drift apart by half a gap).
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        grid.addWidget(scan, 0, 0, 1, 2)
        grid.addWidget(self.fault_tile, 0, 2)
        grid.addWidget(self.dyno_tile, 0, 3)

        self.mac_modules = _table(["Addr", "Module", "Part", "Backup"], 5, stretch=(1,))
        self.mac_modules.verticalHeader().setDefaultSectionSize(28)
        self.modules_card.body.removeWidget(self.modules)
        self.modules.hide()
        self.modules_card.add(self.mac_modules)
        self.modules_card.title_label.setText("MODULES")
        self.logs_card.title_label.setText("RECENT LOGS")
        for w in (open_log, go):
            w.hide()
        for i in range(self.logs_card.body.count()):  # the "or double-click a log" row
            item = self.logs_card.body.itemAt(i)
            if item.layout() is not None:
                for j in range(item.layout().count()):
                    widget = item.layout().itemAt(j).widget()
                    if widget is not None:
                        widget.hide()
        self.logs.setToolTip("Double-click a log to open it in Analyze.")
        self.projects.setToolTip("Double-click to open Projects.")

        for card in (self.modules_card, self.logs_card, self.projects_card):
            # Mockup .grp: the table runs edge to edge, its title inset like the cell text.
            card.body.setContentsMargins(0, 10, 0, 4)
            card.body.setSpacing(4)
            if card.title_label is not None:
                card.title_label.setContentsMargins(14, 0, 14, 0)
        grid.addWidget(self.modules_card, 1, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self.logs_card)
        right.addWidget(self.projects_card)
        right.addStretch(1)
        grid.addLayout(right, 1, 2, 1, 2)
        grid.setRowStretch(1, 1)
        self.layout_.addLayout(grid, 1)
        # The fault controller's own card isn't shown here (the tile and Diagnostics are), but it
        # needs a parent: its banners must never pop up as windows of their own.
        holder = QWidget(self)
        holder.hide()
        QVBoxLayout(holder).addWidget(self.faults.overview_card)
        self.health_card = self.faults.overview_card

    def _fill_tiles(self):
        c = theme.colors()
        rows = getattr(self.faults, "rows", {}) or {}
        with_codes = [r for r in rows.values() if r.get("codes")]
        total = sum(len(r["codes"]) for r in with_codes)
        if not rows:
            last = self.state.settings.get("health_last") or {}
            mods = [m for m in last.get("modules") or [] if m.get("list")]
            total = sum(len(m["list"]) for m in mods)
            self.fault_tile.set(total if last else "–", c["err"] if total else c["muted"],
                                f"in {len(mods)} module{'s' if len(mods) != 1 else ''}" if total else "",
                                "From the last check" if last else "Not checked yet: scan the car")
        elif total:
            first = with_codes[0]
            code = first["codes"][0]
            self.fault_tile.set(total, c["err"], f"in {len(with_codes)} module{'s' if len(with_codes) != 1 else ''}",
                                f"{first['label']} · {code['code']} {code.get('text', '')}".strip())
        else:
            self.fault_tile.set(0, c["ok"], "", "No fault codes stored")
        d = last_dyno(self.state.settings)
        if d:
            self.dyno_tile.set(f"{d['whp']:.0f}", "#ff9f0a", "whp",
                               f"{d['wtq']:.0f} Nm · {_when(d['when'])} · {d['pulls']} pull{'s' if d['pulls'] != 1 else ''}")
        else:
            self.dyno_tile.set("–", c["muted"], "", "No dyno session yet")

    def _fill_mac_modules(self, keys):
        t = self.mac_modules
        t.rows = max(len(keys), 3)  # every module shows, no inner scrolling
        t.setRowCount(max(len(keys), 1))
        if not keys:
            t.setSpan(0, 0, 1, 4)
            t.setItem(0, 0, QTableWidgetItem("None yet: scan the car, then back up each module."))
            return
        t.clearSpans()
        today = time.strftime("%Y-%m-%d")
        for r, key in enumerate(keys):
            hist = EcuRecord(key).history()
            backups = [e for e in hist if e.get("kind") == "backup"]
            flashes = [e for e in hist if e.get("kind") == "flash"]
            family_name, _sep, part = key.partition("_")
            family = self.state.family_by_name(family_name) or {}
            addr = WORKSHOP_ADDR.get(family_name, family.get("module"))
            name = re.sub(r"\s*\(.*?\)", "", family.get("label", family_name))
            for c, text in enumerate((f"{addr:02X}" if isinstance(addr, int) else "", name, part)):
                item = QTableWidgetItem(text)
                if c == 0:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                t.setItem(r, c, item)
            if backups:
                when = backups[-1].get("time", "")
                day = when[:10]
                if day == today:
                    label = "Backed up today"
                else:
                    try:
                        label = "Backed up " + time.strftime("%-d %b", time.strptime(day, "%Y-%m-%d"))
                    except ValueError:
                        label = "Backed up"
                pill = _backup_pill(label, "ok")
            else:
                pill = _backup_pill("Never", "warning")
            tip = (f"{len(backups)} backup{'s' if len(backups) != 1 else ''}"
                   + (f" · last flash {flashes[-1].get('time', '')}" if flashes else " · never flashed"))
            pill.setToolTip(tip)
            sizer = QTableWidgetItem((label if backups else "Never") + "          ")  # sizes the column
            sizer.setForeground(Qt.GlobalColor.transparent)
            t.setItem(r, 3, sizer)
            t.setCellWidget(r, 3, pill)
        _fit_rows(t)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "logs", None) and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_log_columns)
        return super().eventFilter(obj, event)

    def _fit_log_columns(self):
        """Keep about 140 px for the log name: hide the right-most peak columns when it's tight."""
        t, header = self.logs, self.logs.horizontalHeader()
        room = t.viewport().width() - 140
        for c in range(1, t.columnCount()):
            width = max(header.sectionSizeHint(c), t.sizeHintForColumn(c))
            fits = room >= width or c < 4  # Length, rpm and Boost always stay
            t.setColumnHidden(c, not fits)
            room -= width if fits else 0
            if not fits:
                room = -1  # once one is hidden, hide the rest too (keep the order)

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()

    def _find_modules(self):
        """Open Connect (where modules are found and chosen) and start its scan there."""
        self.state.open_tab.emit("connect")
        window = self.window()
        page = getattr(window, "pages", {}).get("connect")
        if page is None or window.stack.currentWidget() is not page:
            return  # the window already explained why it couldn't open

        def run():
            banner = getattr(page, "scan_banner", None)
            if not _press(page.scan_button) and banner is not None:
                banner.show_message("The scan can't start yet. Check the interface, "
                                         "then press Scan here.", "warning")
        QTimer.singleShot(60, run)

    def refresh(self):
        s = self.state
        if s.families and not getattr(self, "_named", False) and self.isVisible():
            self._named = True  # module names need the module list, which arrives just after start-up
            QTimer.singleShot(0, self.reload)
        if not s.adapter:
            hint, usable = "Choose your interface at the top right first.", False
        elif s.busy:
            hint, usable = "Busy: wait for the current job to finish.", False
        else:
            from . import quick_scan
            hint, usable = ("Ignition on, engine off. Reads " + quick_scan.describe(s.settings)
                            + " (Full Scan is on Connect). Only reads: nothing on the car is changed."), True
        self.find_button.setEnabled(usable)
        self.find_hint.setText(hint)

    def reload(self):
        self.refresh()
        # Modules
        keys = sorted(p.name for p in modules_dir().iterdir() if p.is_dir()) if modules_dir().is_dir() else []
        self.modules.setRowCount(len(keys))
        for r, key in enumerate(keys):
            hist = EcuRecord(key).history()
            backups = [e for e in hist if e.get("kind") == "backup"]
            flashes = [e for e in hist if e.get("kind") == "flash"]
            last_flash = flashes[-1] if flashes else {}
            result = last_flash.get("status") or ("error" if last_flash.get("error") else "")
            family_name, _sep, part = key.partition("_")
            family = self.state.family_by_name(family_name)
            short = re.sub(r"\s*\(.*?\)", "", family["label"]) if family else ""
            name = f"{short} · {part}" if family and part else key
            flashed = last_flash.get("time", "never") + (f" ({result})" if result else "")
            for c, text in enumerate((name, str(len(backups)),
                                      backups[-1].get("time", "") if backups else "never", flashed)):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setToolTip(key)
                if c == 2 and text == "never":
                    item.setToolTip("No backup yet: make one before flashing.")
                self.modules.setItem(r, c, item)
        if not keys:
            self.modules.setRowCount(1)
            self.modules.setItem(0, 0, QTableWidgetItem("None yet: connect to a module on the Connect tab."))
        if self.mac:
            self._fill_mac_modules(keys)
            self._fill_tiles()
        # Logs
        # Logs in Datalogs (every recording lands there) and dyno sessions, newest first
        found = []
        if datalogs_dir().is_dir():
            found += datalogs_dir().glob("*.csv")
            found += (datalogs_dir() / "Dyno").glob("*.csv")
        logs = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        self._log_paths = logs
        self.logs.setRowCount(len(logs))
        summaries = []
        for p in logs:
            key = (str(p), p.stat().st_mtime)
            summaries.append(self._cache.get(key) or log_summary(p))
            self._cache[key] = summaries[-1]
        # Units go in the header when every log agrees (saves width for the log names).
        shared = {}
        for c, (label, title) in enumerate((("rpm", "rpm"), ("boost", "Boost"), ("coolant", "Coolant"),
                                            ("knock", "Knock")), 2):
            units = {sm["peaks"][label][1] for sm in summaries if label in sm["peaks"]}
            shared[label] = next(iter(units)) if len(units) == 1 else None
            unit = shared[label]
            sep = " " if self.mac else "\n"  # Mac: one line, like the mockup ("Boost bar")
            self.logs.horizontalHeaderItem(c).setText(
                title if not unit or unit == title else f"{title}{sep}{unit}")
        _fit_rows(self.logs)
        QTimer.singleShot(0, self._fit_log_columns)
        for r, (p, summary) in enumerate(zip(logs, summaries)):
            dur = summary["duration"]
            cells = [p.stem, f"{int(dur // 60)}:{int(dur % 60):02d}" if dur is not None else "–"]
            for label in ("rpm", "boost", "coolant", "knock"):
                peak = summary["peaks"].get(label)
                if not peak:
                    cells.append("–")
                    continue
                number = f"{peak[0]:.0f}" if abs(peak[0]) >= 100 else f"{peak[0]:.1f}"
                cells.append(number if shared[label] else f"{number} {peak[1]}".strip())
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setToolTip(f"{p.name}\n{p.parent}")
                self.logs.setItem(r, c, item)
        # Projects
        from .projects import Project
        projects = Project.all()[:6]
        self.projects.setRowCount(len(projects))
        for r, p in enumerate(projects):
            d = p.data
            for c, text in enumerate((d["name"], str(len(d["versions"])),
                                      d.get("updated", d.get("created", ""))[:10])):
                item = QTableWidgetItem(text)
                if c == 0 and d.get("car"):
                    item.setToolTip(d["car"])
                self.projects.setItem(r, c, item)

    def _open_log(self, row, _col):
        if 0 <= row < len(self._log_paths):
            self.state.open_in_playback.emit(str(self._log_paths[row]))
