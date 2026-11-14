"""The app's activity log: every flasher message goes to a session_*.log file from start-up,
whether or not anyone is looking. Help → Troubleshooting & logs shows it (LogViewer)."""
import json
import time
import zipfile
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .storage import logs_dir, prune_logs, timestamp
from .widgets import Card, muted

MAX_LINES = 20000
ROLL_BYTES = 5 * 1024 * 1024


class SessionLog(QObject):
    """Writes the session log and keeps the recent lines for the viewer (built later, on demand)."""
    line_added = Signal(str, bool)  # stamped line, detail (protocol detail: hidden unless asked for)

    def __init__(self, pool, parent=None):
        super().__init__(parent)
        prune_logs(keep=20)
        self.stamp = timestamp()
        self.part = 1
        self.path = logs_dir() / f"session_{self.stamp}.log"
        self.file = open(self.path, "a", encoding="utf-8")
        self.lines = deque(maxlen=MAX_LINES)  # (stamped line, detail)
        pool.event.connect(self.on_event)

    def write(self, line, detail=False):
        stamped = time.strftime("%H:%M:%S ") + line
        if self.file.tell() > ROLL_BYTES:
            # Roll over so one long session can't grow without bound.
            self.file.close()
            self.part += 1
            self.path = logs_dir() / f"session_{self.stamp}_{self.part}.log"
            self.file = open(self.path, "a", encoding="utf-8")
            prune_logs(keep=20)
        self.file.write(stamped + "\n")
        self.file.flush()
        self.lines.append((stamped, detail))
        self.line_added.emit(stamped, detail)

    def on_event(self, message):
        kind = message.get("event")
        if kind == "log":
            level = message.get("level", "info")
            self.write(f"[{level}] {message.get('message', '')}", detail=level == "debug")
        elif kind == "output":
            self.write(message.get("line", ""), detail=True)
        elif kind == "progress":
            self.write(f"[{message.get('stage', '').strip()}] {message.get('percent')}% "
                       f"{message.get('detail', '')}", detail=True)


class LogViewer(QWidget):
    """Help → Troubleshooting & logs: version and updates, then the live activity log."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.log = window.session_log
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        about = Card("Version and updates")
        row = QHBoxLayout()
        for widget in (window.runtime_label, window.update_status):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(window.update_button)
        about.add(row)
        lay.addWidget(about)

        card = Card("Activity log")
        card.add(muted("What the app and the flasher did this session. If something went wrong, "
                       "save a support bundle and include it when you ask for help. Crash reports "
                       "are saved in the logs folder too."))
        row = QHBoxLayout()
        self.verbose = QCheckBox("Show protocol detail")
        self.verbose.toggled.connect(self._reload)
        row.addWidget(self.verbose)
        row.addStretch(1)
        bundle = QPushButton("Save support bundle…")
        bundle.clicked.connect(self.save_bundle)
        row.addWidget(bundle)
        folder = QPushButton("Open logs folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))))
        row.addWidget(folder)
        card.add(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(200)
        self.text.setMaximumBlockCount(MAX_LINES)
        card.add(self.text)
        lay.addWidget(card, 1)
        self._reload()
        self.log.line_added.connect(self._add)

    def _reload(self):
        show_detail = self.verbose.isChecked()
        self.text.setPlainText("\n".join(line for line, detail in self.log.lines if show_detail or not detail))
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def _add(self, line, detail):
        if not detail or self.verbose.isChecked():
            self.text.appendPlainText(line)

    def save_bundle(self):
        from .popups import MessageBox
        state = self.window_.state
        default = str(Path.home() / f"pq35-support-{timestamp()}.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Save support bundle", default, "Zip (*.zip)")
        if not path:
            return
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(self.log.path, "session.log")
            info = {"hello": state.hello, "adapter": state.adapter,
                    "family": state.family, "identity": state.identity}
            bundle.writestr("state.json", json.dumps(info, indent=2, default=str))
            if state.record:
                bundle.writestr("history.json", json.dumps(state.record.history(), indent=2))
        MessageBox.information(self, "Support bundle", f"Saved {path}.\nIt contains the session "
                                "log and module history, not your firmware files.")
