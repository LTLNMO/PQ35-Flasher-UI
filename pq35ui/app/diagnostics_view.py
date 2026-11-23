"""Diagnostics (2026-11-10, user's design): every module's faults in a sidebar on the left, the
scan log (VCDS Auto-Scan layout, app/scanlog.py) on the right.

- Sidebar: Scan / Re-read all / Clear all…, one row per module (faults first, then OK, then
  the rest, by address). Click = jump to and highlight that module's part of the log.
  Double-click = work on that module (top-bar chip, other tabs). Right-click: Re-read,
  Clear…, Ask AI, Copy.
- Log: exactly the text that gets saved, coloured, with [Re-read] [Clear…] on each module's
  header. Copy, Save log…, History ▾ (saved scans for this car), Freeze frames.
- Every scan's log is saved to Documents/PQ35 Flasher/Scans/<VCDS-style name>.txt; a small
  summary per scan (codes per module) goes to <data dir>/scans/ for "since the last scan".
The fault data and jobs stay in health.ModuleHealthCard (state.faults); this only shows them.
"""
import copy
import html
import json
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPushButton, QSizePolicy, QSplitter, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import scanlog, theme
from .storage import data_dir, user_dir
from .widgets import Banner, Card, muted

ADDRESS_LINE = re.compile(r"^Address ([0-9A-F]{2,4}): ")
LIST_LINE = re.compile(r"^([0-9A-F]{2,4})-.* -- Status: (.*)$")
FAULT_LINE = re.compile(r"^\d{4,} - ")


def scans_dir() -> Path:
    return user_dir() / "Scans"


def _summary_dir() -> Path:
    return data_dir() / "scans"


# --- the scan as it stands now (scan result + later re-reads/clears) ---

def current_result(state):
    """The last full scan with each module's latest fault read (re-reads, clears) merged in."""
    scan = state.last_full_scan
    if not scan or not scan.get("full"):
        return None
    result = copy.deepcopy(scan)
    health = state.faults
    for module in (result.get("gateway") or {}).get("modules") or ():
        row = health.rows.get(module.get("key")) or {}
        if row.get("report") is not None and module.get("key") in health.fresh:
            module["dtc"] = row["report"]
    return result


def listed_modules(result):
    return [m for m in (result.get("gateway") or {}).get("modules") or () if scanlog._listed(m)]


def codes_by_module(result):
    """{"03": {"name": "ABS Brakes", "codes": ["00778"]}} for the compare."""
    out = {}
    for module in listed_modules(result):
        report = module.get("dtc") or {}
        out[f"{module['address']:02X}"] = {
            "name": scanlog._name(module),
            "codes": [c.get("code_text") or str(c.get("code")) for c in report.get("codes") or ()]}
    return out


class ScanStore:
    """Saved scan logs (Documents/PQ35 Flasher/Scans) and their compare summaries."""

    def save(self, result, text):
        folder = scans_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / scanlog.filename(result, result.get("started"))
        stem, count = path.stem, 2
        while path.exists():  # a second scan in the same minute: "-2", "-3"… like VCDS
            path = folder / f"{stem}-{count}.txt"
            count += 1
        path.write_bytes(scanlog.encode(text))
        summary = {"file": path.name, "vin": scanlog._vin(result), "time": result.get("started") or time.time(),
                   "modules": codes_by_module(result)}
        try:
            _summary_dir().mkdir(parents=True, exist_ok=True)
            (_summary_dir() / (path.stem + ".json")).write_text(json.dumps(summary))
        except OSError:
            pass
        return path

    def summaries(self, vin=None):
        items = []
        folder = _summary_dir()
        if not folder.is_dir():
            return items
        for path in folder.glob("Scan-*.json"):
            try:
                item = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if vin and item.get("vin") != vin:
                continue
            items.append(item)
        return sorted(items, key=lambda i: i.get("time", 0), reverse=True)

    def previous(self, vin, before):
        return next((s for s in self.summaries(vin) if s.get("time", 0) < before - 1), None)

    def history(self, vin=None, limit=20):
        """Saved logs, newest first: [(path, label)]. This car's when the VIN is known."""
        folder = scans_dir()
        if not folder.is_dir():
            return []
        files = sorted(folder.glob(f"Scan-{vin}-*.txt" if vin else "Scan-*.txt"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for path in files[:limit]:
            parts = path.stem.split("-")
            when = f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:]} {parts[3][:2]}:{parts[3][2:]}" \
                if len(parts) >= 4 and len(parts[2]) == 8 and len(parts[3]) == 4 else path.stem
            if len(parts) > 4 and parts[-1].isdigit():
                when += f" ({parts[-1]})"  # the "-2" of a second scan in the same minute
            out.append((path, when + ("" if vin else f"  ·  {parts[1]}" if len(parts) > 1 else "")))
        return out


def compare_text(now, before):
    """"2 new codes (03 ABS Brakes 00778, …)  ·  1 gone (17 Instruments 01316)", or ""."""
    new, gone = [], []
    old = (before or {}).get("modules") or {}
    for address, item in now.items():
        was = set((old.get(address) or {}).get("codes") or ())
        new += [f"{address} {item['name']} {c}" for c in item["codes"] if c not in was]
    for address, item in old.items():
        still = set((now.get(address) or {}).get("codes") or ())
        gone += [f"{address} {item['name']} {c}" for c in item.get("codes") or () if c not in still]
    parts = []
    if new:
        parts.append(f"{len(new)} new code{'s' if len(new) != 1 else ''} ({', '.join(new)})")
    if gone:
        parts.append(f"{len(gone)} gone ({', '.join(gone)})")
    return "  ·  ".join(parts)


# Mac interface: the module list's rows (status icon, "03 ABS brakes" over "2 faults").
NAME_ROLE = Qt.ItemDataRole.UserRole + 2
STATUS_ROLE = Qt.ItemDataRole.UserRole + 3
KIND_ROLE = Qt.ItemDataRole.UserRole + 4
SYMBOLS = {"fault": ("exclamationmark.circle.fill", "err"), "ok": ("checkmark.circle.fill", "ok"),
           "unreachable": ("questionmark.circle.fill", "warn"), "busy": ("ellipsis.circle.fill", "muted"),
           "other": ("circle", "muted")}


def _module_delegate():
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyle, QStyledItemDelegate

    class ModuleDelegate(QStyledItemDelegate):
        # Mockup .lrow: 1 px 8 px margin, 7 px 10 px padding, radius 6, 16 px status icon,
        # a semibold name over an 11 pt status line; selected rows in the list blue #0a64d6.
        ROW_H = 40

        def sizeHint(self, option, index):
            return QSize(option.rect.width(), self.ROW_H)

        def paint(self, p, option, index):
            from . import sf_symbols
            c = theme.colors()
            r = QRectF(option.rect).adjusted(8, 1, -8, -1)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if selected:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c.get("list_sel", c["accent"])))
                p.drawRoundedRect(r, 6, 6)
            elif option.state & QStyle.StateFlag.State_MouseOver:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c.get("btn", c["surface2"])))
                p.drawRoundedRect(r, 6, 6)
            kind = index.data(KIND_ROLE) or "other"
            symbol, color_key = SYMBOLS.get(kind, SYMBOLS["other"])
            icon = sf_symbols.icon(symbol, 16, "#ffffff" if selected else c[color_key])
            if icon is not None:
                p.drawPixmap(int(r.left()) + 10, int(r.center().y()) - 8, icon.pixmap(16, 16))
            x = r.left() + 34
            name_font = QFont(option.font)
            name_font.setWeight(QFont.Weight.DemiBold)
            p.setFont(name_font)
            fg = QColor("#ffffff") if selected else QColor(c["muted"] if index.data(Qt.ItemDataRole.UserRole + 5)
                                                           else c["text"])
            p.setPen(fg)
            top = QRectF(x, r.top() + 3, r.width() - 40, r.height() / 2 - 2)
            p.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       p.fontMetrics().elidedText(index.data(NAME_ROLE) or "", Qt.TextElideMode.ElideRight,
                                                  int(top.width())))
            small = QFont(option.font)
            small.setPointSizeF(max(small.pointSizeF() - 2, 9))
            p.setFont(small)
            p.setPen(QColor(255, 255, 255, 204) if selected else QColor(c.get("sub", c["muted"])))
            bottom = QRectF(x, r.center().y() + 1, r.width() - 40, r.height() / 2 - 2)
            p.drawText(bottom, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, index.data(STATUS_ROLE) or "")
            p.restore()

    return ModuleDelegate()


class AutoScanView(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.health = state.faults
        self.health.dialog_parent = self
        self.store = ScanStore()
        self.highlight = None    # address (int) of the module shown highlighted in the log
        self.viewing = None      # a saved log being looked at instead of the current scan
        self.text = ""
        self._viewing_text = (None, "")
        self.saved_path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(10)
        layout.addWidget(self.split, 1)
        self.mac = theme.is_mac()
        if self.mac:  # the Mockups/Swift native layout: a full-height Modules column, the log beside it
            self.split.setHandleWidth(0)
            self._build_mac_sidebar()
            self._build_mac_log()
        else:
            self._build_sidebar()
            self._build_log()
        if self.mac:
            from .mac_shell import column_split
            column_split(self.split)
        else:
            self.split.setSizes([330, 900])
        self.split.setStretchFactor(1, 1)
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self.refresh)
        self.health.updated.connect(self._timer.start)
        state.changed.connect(self._timer.start)
        state.busy_changed.connect(lambda _b: self._timer.start())
        state.modules_scanned.connect(self._scanned)
        theme.on_apply(self._timer.start)
        self.refresh()

    # --- layout ---

    def _build_sidebar(self):
        side = Card()  # no title or collapse button (user: the list explains itself)
        side.setMinimumWidth(270)
        row = QHBoxLayout()
        self.scan_button = QPushButton("Full Scan")
        self.scan_button.setObjectName("primary")
        self.scan_button.setToolTip("Read every module on the gateway's list: what it is and its fault "
                                    "codes. Read-only.")
        self.scan_button.clicked.connect(self.state.request_full_scan.emit)
        self.reread_button = QPushButton("Re-read all")
        self.reread_button.setToolTip("Read every module's fault codes again (read-only).")
        self.reread_button.clicked.connect(self.health.start)
        self.clear_button = QPushButton("Clear all…")
        self.clear_button.setToolTip("Clear the stored codes on every module that has some, then read "
                                     "each one back.")
        self.clear_button.clicked.connect(self.health.clear_all)
        for button in (self.scan_button, self.reread_button, self.clear_button):
            row.addWidget(button)
        side.add(row)
        # Never narrower than its buttons (they'd be cut off otherwise).
        margins = side.body.contentsMargins()
        side.setMinimumWidth(row.sizeHint().width() + margins.left() + margins.right() + 4)
        self.summary = side.add(muted(""))
        self.summary.setWordWrap(True)
        self.message = side.add(Banner())
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(lambda item, _c: self.show_module(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.work_on(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.setToolTip("Click: show it in the log · double-click: work on it (top bar) · "
                             "right-click: Re-read, Clear…, Ask AI, Copy")
        side.add(self.tree)
        side.body.setStretchFactor(self.tree, 1)
        self.split.addWidget(side)

    def _build_mac_sidebar(self):
        from PySide6.QtWidgets import QFrame
        from .toolbar import ToolIcon
        side = QFrame()
        side.setObjectName("macPanel")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setMinimumWidth(270)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 16, 0, 0)  # rows inset themselves 8 px (ModuleDelegate)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(24, 0, 10, 0)  # text 24 px in (mac_shell rule 5)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("Modules")
        title.setObjectName("macPaneTitle")
        col.addWidget(title)
        self.summary = muted("")
        self.summary.setWordWrap(True)
        col.addWidget(self.summary)
        head.addLayout(col, 1)
        self.reread_button = ToolIcon("refresh", "Re-read every module's fault codes (read-only)")
        self.reread_button.clicked.connect(self.health.start)
        head.addWidget(self.reread_button, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.message = Banner()
        lay.addWidget(self.message)
        self.message.setContentsMargins(8, 0, 8, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setColumnHidden(1, True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setMouseTracking(True)
        self.tree.setItemDelegate(_module_delegate())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(lambda item, _c: self.show_module(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.work_on(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.setToolTip("Click: show it in the log · double-click: work on it · right-click: more")
        self.tree.itemSelectionChanged.connect(lambda: self._timer.start() if hasattr(self, "_timer") else None)
        lay.addWidget(self.tree, 1)
        line = QFrame()
        line.setObjectName("macFootLine")
        line.setFixedHeight(1)
        lay.addWidget(line)
        foot = QHBoxLayout()
        foot.setContentsMargins(24, 10, 16, 20)  # buttons level with Re-read / Clear faults… (20 from the bottom)
        self.scan_button = QPushButton("Scan")
        self.scan_button.setObjectName("primary")
        self.scan_button.setToolTip("Read every module on the gateway's list: what it is and its fault "
                                    "codes. Read-only.")
        self.scan_button.clicked.connect(self.state.request_full_scan.emit)
        self.clear_button = QPushButton("Clear all…")
        self.clear_button.setToolTip("Clear the stored codes on every module that has some, then read "
                                     "each one back.")
        self.clear_button.clicked.connect(self.health.clear_all)
        foot.addWidget(self.scan_button)
        foot.addWidget(self.clear_button)
        foot.addStretch(1)
        lay.addLayout(foot)
        self.split.addWidget(side)

    def _build_mac_log(self):
        right = QWidget()
        lay = QVBoxLayout(right)
        from .mac_shell import main_layout
        main_layout(lay)  # rules 2–4: row 1, the log 10 px under it
        # The log's name and its buttons: the first row of the log's pane (nothing in the title
        # bar's right side, which holds only page-wide things).
        from .mac_shell import first_row
        head = first_row(QHBoxLayout())
        self.info = muted("")
        self.info.setWordWrap(False)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setMinimumWidth(0)
        head.addWidget(self.info, 1)
        freeze, history, more = self._log_buttons()
        from .mac_shell import row_icons  # rule 7: the one right-hand order
        row_icons(head, tools=(freeze,), recent=history, more=more)
        lay.addLayout(head)
        self.compare = Banner()
        lay.addWidget(self.compare)
        self.viewing_banner = Banner()
        lay.addWidget(self.viewing_banner)
        self.log = QTextBrowser()
        self.log.setObjectName("macLog")
        self.log.setOpenLinks(False)
        self.log.setOpenExternalLinks(False)
        self.log.anchorClicked.connect(self._link)
        self.log.setMinimumHeight(320)
        lay.addWidget(self.log, 1)
        # The selected module's actions, under the log (the right-click menu has them too).
        from .analyze_tools import menu_icon
        row = QHBoxLayout()
        self.module_reread = QPushButton(menu_icon("refresh"), "Re-read")
        self.module_reread.clicked.connect(lambda: self._selected() and self.health.reread([self._selected()]))
        self.module_clear = QPushButton(menu_icon("delete"), "Clear faults…")
        self.module_clear.clicked.connect(lambda: self._selected() and self.health.clear_module(self._selected()))
        self.module_ai = QPushButton(menu_icon("auto_awesome"), "Ask AI")
        self.module_ai.clicked.connect(lambda: self._selected() and self.health.ask_module(self._selected()))
        for b in (self.module_reread, self.module_clear, self.module_ai):
            b.setToolTip("For the module selected in the list")
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.split.addWidget(right)

    def _selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _log_buttons(self):
        from .analyze_tools import menu_action
        from .toolbar import ToolIcon

        def fill_more(menu):
            has = bool(self.text)
            menu_action(menu, "Copy log", "content_copy", lambda: QGuiApplication.clipboard().setText(self.text),
                        enabled=has, tip="Copy the whole log, e.g. to paste into a forum post")
            menu_action(menu, "Export scan log…", "ios_share", self.save_as, enabled=has,
                        tip="Copy this scan log somewhere else (every scan is saved to Documents/PQ35 Flasher/Scans "
                            "on its own)")
        self.more_button = ToolIcon("more_vert", "More: copy or export the scan log", menu=fill_more)
        self.history_button = ToolIcon("history", "History: earlier scans of this car",
                                       menu=self._fill_history, dropdown=True)
        self.history_menu = self.history_button.menu_
        self.freeze = ToolIcon("frame_inspect", "", checkable=True)
        self.freeze.setChecked(bool(self.state.settings.get("scan_freeze_frames", True)))

        def freeze_toggled(on):
            self.state.settings.set("scan_freeze_frames", on)
            self.freeze.setToolTip(
                f"Freeze frames: {'on' if on else 'off'}. When on, scans and re-reads also read each "
                "fault's snapshot (how often, at what mileage, readings at that moment); about half "
                "a second extra per fault. Click to turn " + ("off" if on else "on") + ".")
        self.freeze.toggled.connect(freeze_toggled)
        freeze_toggled(self.freeze.isChecked())
        return self.freeze, self.history_button, self.more_button

    def _build_log(self):
        right = Card()
        head = QHBoxLayout()
        title = QLabel("Scan log")
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        self.info = muted("")
        self.info.setWordWrap(False)
        # Gives way when the window is narrow (full text in its tooltip) instead of pushing the
        # buttons off the edge.
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setMinimumWidth(0)
        head.addWidget(self.info, 1)
        # Icon buttons like Analyze's toolbar.
        from .analyze_tools import menu_action
        from .toolbar import ToolIcon

        def fill_more(menu):
            has = bool(self.text)
            menu_action(menu, "Copy log", "content_copy", lambda: QGuiApplication.clipboard().setText(self.text),
                        enabled=has, tip="Copy the whole log, e.g. to paste into a forum post")
            menu_action(menu, "Export scan log…", "ios_share", self.save_as, enabled=has,
                        tip="Copy this scan log somewhere else (every scan is saved to Documents/PQ35 Flasher/Scans "
                            "on its own)")
        self.more_button = ToolIcon("more_vert", "More: copy or export the scan log", menu=fill_more)
        self.history_button = ToolIcon("history", "History: earlier scans of this car",
                                       menu=self._fill_history, dropdown=True)
        self.history_menu = self.history_button.menu_
        # Freeze frames: a toggle icon (frame_inspect: a captured frame, looked at closely; accent = on).
        self.freeze = ToolIcon("frame_inspect", "", checkable=True)
        self.freeze.setChecked(bool(self.state.settings.get("scan_freeze_frames", True)))

        def freeze_toggled(on):
            self.state.settings.set("scan_freeze_frames", on)
            self.freeze.setToolTip(
                f"Freeze frames: {'on' if on else 'off'}. When on, scans and re-reads also read each "
                "fault's snapshot (how often, at what mileage, readings at that moment); about half "
                "a second extra per fault. Click to turn " + ("off" if on else "on") + ".")
        self.freeze.toggled.connect(freeze_toggled)
        freeze_toggled(self.freeze.isChecked())
        for widget in (self.freeze, self.history_button, self.more_button):
            head.addWidget(widget)
        right.add(head)
        self.compare = right.add(Banner())
        self.viewing_banner = right.add(Banner())
        self.log = QTextBrowser()
        self.log.setOpenLinks(False)
        self.log.setOpenExternalLinks(False)
        self.log.anchorClicked.connect(self._link)
        self.log.setMinimumHeight(320)
        right.add(self.log)
        right.body.setStretchFactor(self.log, 1)
        self.split.addWidget(right)

    # --- data ---

    def _result(self):
        return current_result(self.state)

    def _build_text(self, result):
        from .. import __version__
        adapter = self.state.adapter or {}
        return scanlog.build(result, started=result.get("started"), elapsed=result.get("elapsed"),
                             app_version=__version__, interface=adapter.get("label", ""),
                             rows=self.health.rows)

    def _scanned(self, result):
        """A scan just finished: once the fault rows have it, save its log and compare."""
        if result.get("error") or not result.get("full"):
            return
        self.viewing = None
        QTimer.singleShot(80, self._save_new)

    def _save_new(self):
        result = self._result()
        if result is None:
            return
        text = self._build_text(result)
        vin = scanlog._vin(result)
        before = self.store.previous(vin, result.get("started") or time.time()) if vin else None
        try:
            self.saved_path = self.store.save(result, text)
        except OSError as exc:
            self.saved_path = None
            self.message.show_message(f"Couldn't save the scan log: {exc}", "error")
        diff = compare_text(codes_by_module(result), before) if before else ""
        if before and diff:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(before.get("time", 0)))
            self.compare.show_message(f"Since the scan on {when}:  {diff}", "warning")
        elif before:
            self.compare.show_message("Same fault codes as the scan on "
                                      + time.strftime("%Y-%m-%d %H:%M", time.localtime(before.get("time", 0)))
                                      + ".", "ok")
        else:
            self.compare.hide()
        self.refresh()

    # --- showing it ---

    def refresh(self):
        idle = bool(self.state.adapter) and not self.state.busy and not self.health.running
        from .needs_car import gate
        gate(self.scan_button, self.state, not self.state.busy)
        self.reread_button.setEnabled(idle and bool(self.state.last_scan))
        self.clear_button.setEnabled(idle and bool(self.health.faulty()))
        if self.mac:
            key = self._selected()
            row = self.health.rows.get(key) or {}
            self.module_reread.setEnabled(idle and bool(row.get("dtc")))
            self.module_clear.setEnabled(idle and bool(row.get("codes")) and key in self.health.fresh)
            self.module_ai.setEnabled(bool(row.get("codes")))
        self._mirror_message()
        self._fill_sidebar()
        self._fill_log()

    def _mirror_message(self):
        source = self.health.banner
        level = source.property("level") or "info"
        if self.mac and level not in ("error",):  # the scan summary is in the list itself (mockup)
            self.message.hide()
            return
        if source.active:
            self.message.show_message(source.text(), source.property("level") or "info")
        else:
            self.message.hide()

    def _modules(self):
        """(key, address, name, codes, status, stale) for the sidebar, from the scan or saved rows."""
        rows = self.health.rows
        result = self._result()
        items = []
        if result is not None:
            for module in listed_modules(result):
                row = rows.get(module.get("key")) or {}
                # VCDS's names, like the scan log beside it (user's choice for Diagnostics).
                items.append((module.get("key"), module["address"], scanlog._name(module),
                              len(row.get("codes") or ()), row.get("status", ""),
                              module.get("key") not in self.health.fresh))
        else:
            for key, row in rows.items():
                if isinstance(row.get("address"), int):
                    items.append((key, row["address"], row["label"].split(" ", 1)[-1],
                                  len(row.get("codes") or ()), row.get("status", ""), True))
        return items

    def _fill_sidebar(self):
        selected_key = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.tree.currentItem() else None
        working = self.state.family["name"] if self.state.family else None
        self.tree.clear()
        items = self._modules()
        order = {"OK": 1, "Cleared": 1}
        items.sort(key=lambda i: (0 if i[3] else order.get(i[4], 2), i[1]))
        text = self.tree.palette().text().color()
        c = theme.colors()
        RED, GREEN, AMBER = c["err"], c["ok"], c["warn"]
        grey = QColor(c["muted"])
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for key, address, name, count, status, stale in items:
            if count:
                mark, color = f"⚠ {count}", RED
            elif status in ("OK", "Cleared"):
                mark, color = "✓", GREEN
            elif status in ("Can't be reached", "No answer"):
                mark, color = "–", AMBER
            elif status in ("Waiting…", "Reading…", "Clearing…"):
                mark, color = "…", grey.name()
            else:
                mark, color = "·", grey.name()
            item = QTreeWidgetItem(self.tree, [f"{address:02X}  {name}" + ("  ●" if key == working else ""), mark])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, address)
            kind = ("fault" if count else "ok" if status in ("OK", "Cleared") else
                    "unreachable" if status in ("Can't be reached", "No answer") else
                    "busy" if status in ("Waiting…", "Reading…", "Clearing…") else "other")
            line = (f"{count} fault{'s' if count != 1 else ''}" if count else
                    "No faults" if status in ("OK", "Cleared") else status or "Listed")
            item.setData(0, NAME_ROLE, f"{address:02X} {name}" + ("  ●" if key == working else ""))
            item.setData(0, STATUS_ROLE, line + ("  (earlier check)" if stale else ""))
            item.setData(0, KIND_ROLE, kind)
            item.setData(0, Qt.ItemDataRole.UserRole + 5, bool(stale))
            item.setForeground(1, QColor(grey if stale else QColor(color)))
            item.setForeground(0, grey if stale or status in ("Can't be reached", "Not coded", "Found") else text)
            if count:
                item.setFont(1, bold)
            item.setToolTip(0, status + ("  (from an earlier check)" if stale else ""))
            if key == selected_key:
                self.tree.setCurrentItem(item)
        answered = sum(1 for i in items if i[4] not in ("Can't be reached", "Not coded", "No answer"))
        faulty = sum(1 for i in items if i[3])
        when = ""
        result = self._result()
        if self.mac and result is not None:
            from .home_page import _when
            when = "Scanned " + _when(result.get("started") or time.time())
        self.summary.setText(when if when else
                             f"{len(items)} on the gateway list · {answered} answered · "
                             f"{faulty} with faults" if items else
                             "No scan yet. Press Scan to read every module." if self.state.adapter else
                             "Connect an interface, then press Scan.")

    def _fill_log(self):
        result = self._result()
        if self.viewing is not None:
            try:
                if self._viewing_text[0] != self.viewing:  # read each saved log once
                    self._viewing_text = (self.viewing, self.viewing.read_bytes().decode("latin-1"))
                self.text = self._viewing_text[1]
            except OSError:
                self.viewing = None
                return self._fill_log()
            back = {"Back to the latest scan": self._back} if result is not None else None
            self.viewing_banner.show_message(f"Showing the saved scan {self.viewing.name}.", "info",
                                             actions=back)
            self.info.setText("")
        elif result is not None:
            self.text = self._build_text(result)
            self.viewing_banner.hide()
            started = time.strftime("%Y-%m-%d %H:%M", time.localtime(result.get("started") or time.time()))
            elapsed = int(result.get("elapsed") or 0)
            saved = f"  ·  saved as Scans/{self.saved_path.name}" if self.saved_path else ""
            self.info.setText(f"{started}  ·  {scanlog._vin(result)}  ·  {elapsed // 60}:{elapsed % 60:02d}{saved}")
            self.info.setToolTip(self.info.text() + (f"\n{self.saved_path}" if self.saved_path else ""))
        else:
            latest = self.store.history(self.state.known_vin() or None, 1) or self.store.history(None, 1)
            if latest:
                self.viewing = latest[0][0]
                return self._fill_log()
            self.text = ""
            self.info.setText("")
            self.viewing_banner.hide()
            self.log.setHtml(f'<p style="color:{theme.colors()["muted"]};">No scan yet. Press <b>Scan</b> '
                             "to read every module; the log appears here and is saved to "
                             "Documents/PQ35 Flasher/Scans.</p>")
            return
        bar = self.log.verticalScrollBar()
        position = bar.value()
        self.log.setHtml(self._html(self.text, live=self.viewing is None))
        bar.setValue(position)

    def _html(self, text, live=True):
        c = theme.colors()
        muted_color = c["muted"]
        RED, GREEN, AMBER = c["err"], c["ok"], c["warn"]
        keys = {}
        rows = self.health.rows
        if live:
            for key, row in rows.items():
                if isinstance(row.get("address"), int):
                    keys[row["address"]] = key
        mono = ("font-family: Menlo, Consolas, 'DejaVu Sans Mono', monospace; font-size: 12px; "
                "white-space: pre;")
        sections = [[]]
        for line in text.split("\n"):
            if line == scanlog.SEPARATOR:
                sections.append([])
            sections[-1].append(line)
        out = []
        for section in sections:
            address = None
            for line in section[:2]:
                match = ADDRESS_LINE.match(line)
                if match:
                    address = int(match.group(1), 16)
            lit = address is not None and address == self.highlight
            # Mac: the selection shows in the list only, as in the mockup; the log stays plain.
            style = (f"background:{c['accent_bg']}; border-left: 3px solid {c['accent']};"
                     if lit and not self.mac else "")
            body = []
            for line in section:
                escaped = html.escape(line)
                color = None
                weight = ""
                match = LIST_LINE.match(line)
                if match:
                    status = match.group(2)
                    color = RED if status.startswith("Malfunction") else GREEN if status.startswith("OK") \
                        else AMBER if status.startswith("Cannot") else muted_color
                elif re.match(r"^\d+ Faults? Found:$", line) or FAULT_LINE.match(line):
                    color = RED
                elif line.startswith("No fault code found."):
                    color = GREEN
                elif line.startswith("Cannot be reached"):
                    color = AMBER
                elif "Still present:" in line:
                    color, weight = RED, "font-weight:700;"
                elif ADDRESS_LINE.match(line):
                    weight = "font-weight:700;"
                span = f'<span style="{"color:" + color + ";" if color else ""}{weight}">{escaped}</span>' \
                    if color or weight else escaped
                if ADDRESS_LINE.match(line) and address is not None:
                    span = f'<a name="m{address:02X}"></a>' + span
                    key = keys.get(address)
                    row = rows.get(key) or {}
                    if live and key and row.get("dtc"):
                        span += f'   <a href="reread:{key}" style="color:{c["accent"]};">[Re-read]</a>'
                        if row.get("codes") and key in self.health.fresh:
                            span += f'  <a href="clear:{key}" style="color:{c["accent"]};">[Clear…]</a>'
                body.append(span)
            out.append(f'<div style="{mono} color:{c["text"]}; {style}">' + "\n".join(body) + "</div>")
        return "".join(out)

    # --- actions ---

    def show_module(self, key):
        address = (self.health.rows.get(key) or {}).get("address")
        if not isinstance(address, int):
            return
        self.highlight = address
        self.health.focused = key
        self._fill_log()
        self.log.scrollToAnchor(f"m{address:02X}")

    def work_on(self, key):
        """Make it the module the other tabs (and the top-bar chip) work on."""
        family = self.state.family_by_name(key)
        if family is None or self.state.busy:
            return
        identity = family.get("scan_identity")
        if identity is None:
            entry = next((e for e in (self.state.last_scan or {}).get("result") or []
                          if e.get("family") == key and e.get("status") == "ok"), None)
            identity = (entry or {}).get("identity")
            if entry is None:
                return
        self.state.select_module(family, identity)

    def _link(self, url: QUrl):
        action, _, key = url.toString().partition(":")
        if action == "reread":
            self.health.reread([key])
        elif action == "clear":
            self.health.clear_module(key)

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        row = self.health.rows.get(key) or {}
        idle = bool(self.state.adapter) and not self.state.busy
        menu = QMenu(self.tree)
        reread = menu.addAction("Re-read")
        reread.setEnabled(idle and bool(row.get("dtc")))
        reread.triggered.connect(lambda: self.health.reread([key]))
        clear = menu.addAction("Clear…")
        clear.setEnabled(idle and bool(row.get("codes")) and key in self.health.fresh)
        clear.triggered.connect(lambda: self.health.clear_module(key))
        menu.addSeparator()
        if row.get("codes"):
            menu.addAction("Ask AI about these codes…").triggered.connect(lambda: self.health.ask_module(key))
        menu.addAction("Copy this module").triggered.connect(lambda: self._copy_module(row.get("address")))
        work = menu.addAction("Work on this module")
        work.setEnabled(self.state.family_by_name(key) is not None and not self.state.busy)
        work.triggered.connect(lambda: self.work_on(key))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _copy_module(self, address):
        if not isinstance(address, int):
            return
        sections = self.text.split(scanlog.SEPARATOR + "\n")
        part = next((s for s in sections if s.startswith(f"Address {address:02X}:")), "")
        QGuiApplication.clipboard().setText(scanlog.SEPARATOR + "\n" + part if part else "")

    def save_as(self):
        if not self.text:
            return
        result = self._result()
        name = self.viewing.name if self.viewing is not None else (
            scanlog.filename(result, result.get("started")) if result else "Scan.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save scan log", str(scans_dir() / name), "Text (*.txt)")
        if path:
            try:
                Path(path).write_bytes(scanlog.encode(self.text))
            except OSError as exc:
                self.message.show_message(f"Couldn't save: {exc}", "error")

    def _fill_history(self, menu=None):
        menu = menu if menu is not None else self.history_menu
        menu.clear()
        vin = self.state.known_vin() or None
        items = self.store.history(vin)
        if not items:
            menu.addAction("No saved scans yet").setEnabled(False)
        for path, label in items:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(path == self.viewing)
            action.triggered.connect(lambda _=False, p=path: self._view(p))
        menu.addSeparator()
        menu.addAction("Open the Scans folder").triggered.connect(
            lambda: (scans_dir().mkdir(parents=True, exist_ok=True),
                     QDesktopServices.openUrl(QUrl.fromLocalFile(str(scans_dir())))))

    def _view(self, path):
        self.viewing = path
        self.highlight = None
        self._fill_log()
        self.log.verticalScrollBar().setValue(0)

    def _back(self):
        self.viewing = None
        self._fill_log()
