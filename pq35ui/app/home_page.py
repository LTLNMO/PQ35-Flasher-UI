"""Dashboard: a big Scan button (everything starts with a scan), the
all-modules fault codes, your modules with their last backup/flash, recent
datalogs with peak values, and recent projects, in two half-width columns."""
import csv
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .log_export import parse_time, split_header
from .pages import Page
from .storage import EcuRecord, datalogs_dir, modules_dir
from .widgets import Card, muted

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
    t.setMinimumHeight(t.horizontalHeader().sizeHint().height() + t.rows * 28 + 2 * t.frameWidth() + 2)


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


class HomePage(Page):
    title = "Dashboard"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        # Everything starts with a scan, so the Dashboard's one big button is Scan.
        # (Interface and module already show in the top bar.)
        scan_card = Card()
        self.find_button = QPushButton("Scan")
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
            hint, usable = "Ignition on, engine off. Only reads: nothing on the car is changed.", True
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
            self.logs.horizontalHeaderItem(c).setText(
                title if not unit or unit == title else f"{title}\n{unit}")
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
