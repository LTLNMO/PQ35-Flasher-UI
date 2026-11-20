"""Compare logs with AI: pick any number of datalogs, the AI gets them side by side.

Each log is digested (log_digest): full-throttle pulls found, channels matched by what they
are (so Standard and Integrated Engineering logs line up), averaged per 500 rpm in one table
per channel with a column per log, plus objective checks. Then the usual Ask AI window opens
with one-click questions for comparing runs."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QVBoxLayout,
)

from . import ai, analyze_tools, log_digest, log_import, recent_logs
from .popups import OverlayDialog
from .storage import datalogs_dir
from .widgets import muted

PATH_ROLE = Qt.ItemDataRole.UserRole
PULLS_KEY = "compare_ai:pulls_only"
MAX_LOGS = 12  # more than this and the tables get too wide to be useful

SUGGESTIONS = [
    ("Rank the runs", "Rank these runs from best to worst, once for power and once for engine safety. "
     "Back each place with numbers from the rpm tables."),
    ("What changed?", "What changed between A and each other run (boost, timing and retard, fuelling, "
     "airflow, temperatures), where in the rev range, and what's the likely reason?"),
    ("Which is safest?", "Which run is the safest for the engine and which is the riskiest, and why? "
     "Look at retard per cylinder, boost overshoot, rail pressure against target, lambda control and "
     "intake temperature."),
    ("Real change or noise?", "Are these runs comparable? Separate real differences from noise and from "
     "different conditions (intake temperature, gear, rpm range, pull length, log rate)."),
    ("What next?", "Based on all these runs, what should I change or check next, in order, and what "
     "should I log to confirm it?"),
]
QUESTION = ("Compare these runs: summarise each in a line, show the differences that matter by rpm, "
            "say which run is best and which is safest, and what I should check or change next.")


class CompareLogsDialog(OverlayDialog):
    """The list of logs to compare. load(path) -> (headers, times, columns) or raises."""

    CARD_ICON = "auto_awesome"

    def __init__(self, parent, settings, load, paths=()):
        super().__init__(parent)
        self.settings, self.load = settings, load
        self.data = {}  # path -> (headers, times, columns)
        self.setWindowTitle("Compare logs with AI")
        self.setMinimumSize(620, 440)
        lay = QVBoxLayout(self)
        lay.addWidget(muted("Add the logs to compare; drag to reorder. A (the first) is the baseline. Channels "
                            "are matched by what they are, so Standard and IE logs line up. The AI gets "
                            "tables by rpm and checks, not the files."))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.model().rowsMoved.connect(lambda *_: self._relabel())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton(analyze_tools.menu_icon("file_open"), "Add logs…")
        add.setToolTip("Choose one or more datalog files")
        add.clicked.connect(self._add_files)
        row.addWidget(add)
        self.recent = QPushButton(analyze_tools.menu_icon("history"), "Recent ▾")
        self.recent.setToolTip("Add one of the logs you recorded recently")
        self.recent.clicked.connect(self._recent_menu)
        row.addWidget(self.recent)
        self.remove = QPushButton(analyze_tools.menu_icon("delete"), "Remove")
        self.remove.setToolTip("Take the selected logs off the list (the files stay)")
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        row.addStretch(1)
        lay.addLayout(row)
        self.pulls_only = QCheckBox("Compare full-throttle pulls only (when a log has them)")
        self.pulls_only.setToolTip("Only the rows at full throttle with rpm climbing go into the comparison: "
                                   "cruising and idle would blur the averages.")
        self.pulls_only.setChecked(bool(settings.get(PULLS_KEY, True)))
        lay.addWidget(self.pulls_only)
        self.status = muted("")
        lay.addWidget(self.status)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Analyze with AI")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for p in paths:
            self._add(Path(p))
        self._relabel()

    # -- list --
    def paths(self):
        return [Path(self.list.item(i).data(PATH_ROLE)) for i in range(self.list.count())]

    def _add(self, path):
        path = Path(path)
        if path in self.paths():
            return
        if self.list.count() >= MAX_LOGS:
            self.status.setText(f"At most {MAX_LOGS} logs at a time.")
            return
        try:
            headers, times, columns = self.load(path)
            line = log_digest.summary(path.name, headers, times, columns)
        except Exception as e:
            self.status.setText(f"Couldn't open {path.name}: {e}")
            return
        self.data[path] = (headers, times, columns)
        item = QListWidgetItem()
        item.setData(PATH_ROLE, str(path))
        item.setData(Qt.ItemDataRole.UserRole + 1, line)
        item.setToolTip(str(path))
        self.list.addItem(item)
        self.status.setText("")

    def _relabel(self):
        for i in range(self.list.count()):
            it = self.list.item(i)
            letter = chr(ord("A") + i) if i < 26 else f"L{i + 1}"
            it.setText(f"{letter}   {Path(it.data(PATH_ROLE)).name}    —  {it.data(Qt.ItemDataRole.UserRole + 1)}")
        n = self.list.count()
        self.ok.setEnabled(n >= 2)
        self.ok.setToolTip("" if n >= 2 else "Add at least two logs")
        self.remove.setEnabled(n > 0)

    def _add_files(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, "Add logs to compare", str(datalogs_dir()),
                                                log_import.OPEN_FILTER)
        for f in files:
            self._add(Path(f))
        self._relabel()

    def _recent_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        have = set(self.paths())
        entries = recent_logs.entries(20)
        for path, entry in entries:
            act = menu.addAction(f"{path.name}    {recent_logs.describe(entry)}")
            act.setEnabled(path not in have)
            act.triggered.connect(lambda _=False, p=path: (self._add(p), self._relabel()))
        if not entries:
            menu.addAction("No recent recordings").setEnabled(False)
        analyze_tools.popup_menu(menu, self.recent)

    def _remove(self):
        for it in self.list.selectedItems() or ([self.list.currentItem()] if self.list.currentItem() else []):
            self.data.pop(Path(it.data(PATH_ROLE)), None)
            self.list.takeItem(self.list.row(it))
        self._relabel()

    def accept(self):
        self.settings.set(PULLS_KEY, self.pulls_only.isChecked())
        super().accept()

    def context(self):
        logs = [(p.name, *self.data[p]) for p in self.paths() if p in self.data]
        return log_digest.compare(logs, pulls_only=self.pulls_only.isChecked())


def open_compare(parent, settings, load, paths=()):
    """Pick logs, then open Ask AI on them. Returns the Ask AI window or None."""
    dlg = CompareLogsDialog(parent, settings, load, paths)
    if not dlg.exec():
        return None
    context = dlg.context()
    n = dlg.list.count()
    win = ai.ask(parent, settings, f"compare {n} logs", context, QUESTION,
                 extra_system=log_digest.ANALYSIS_SYSTEM, effort="high")
    if win is not None:
        win.set_suggestions(SUGGESTIONS)
    return win
