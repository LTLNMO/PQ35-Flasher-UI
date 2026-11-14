"""Projects: one place per car or tune that groups the modules it touches,
labelled tune versions, datalogs, the modules' backup/flash history and notes.

Stored under Documents/PQ35 Flasher/Projects/<name>/project.json. Tune versions
are copied into the project's Versions folder so they can't go missing;
datalogs are linked where they already are."""
import json
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from .storage import EcuRecord, datalogs_dir, modules_dir, user_dir
from .widgets import Banner, h1, muted, scrollable
from .popups import InputDialog, MessageBox


def projects_dir() -> Path:
    return user_dir() / "Projects"


def _slug(name):
    return re.sub(r"[^A-Za-z0-9_. -]+", "-", name).strip(" .-") or "Project"


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "project.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("name", root.name)
        for key in ("modules", "versions", "logs"):
            self.data.setdefault(key, [])
        self.data.setdefault("car", "")
        self.data.setdefault("notes", "")

    @classmethod
    def create(cls, name):
        root = projects_dir() / _slug(name)
        n = 2
        while root.exists():
            root = projects_dir() / f"{_slug(name)} {n}"
            n += 1
        root.mkdir(parents=True)
        project = cls(root)
        project.data.update(name=name, created=time.strftime("%Y-%m-%d %H:%M"))
        project.save()
        return project

    @classmethod
    def all(cls):
        if not projects_dir().is_dir():
            return []
        found = [cls(p) for p in projects_dir().iterdir() if (p / "project.json").is_file()]
        return sorted(found, key=lambda p: p.data.get("updated", p.data.get("created", "")), reverse=True)

    @property
    def name(self):
        return self.data["name"]

    def save(self):
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M")
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def add_version(self, source: Path, label: str, notes=""):
        folder = self.root / "Versions"
        folder.mkdir(exist_ok=True)
        target = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{_slug(label)}{source.suffix}"
        shutil.copyfile(source, target)
        self.data["versions"].append({"label": label, "file": str(target), "source": str(source),
                                      "notes": notes, "added": time.strftime("%Y-%m-%d %H:%M")})
        self.save()

    def module_history(self):
        """Backups and flashes of the linked modules, newest first."""
        rows = []
        for key in self.data["modules"]:
            if not (modules_dir() / key).is_dir():
                continue
            for entry in EcuRecord(key).history():
                if entry.get("kind") in ("backup", "flash", "verify"):
                    rows.append(dict(entry, module=key))
        return sorted(rows, key=lambda e: e.get("time", ""), reverse=True)


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return table


class ProjectsPage(QWidget):
    title = "Projects"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        self.project = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(6)
        outer.addWidget(split, 1)

        side = QWidget()
        side.setObjectName("page")
        sb = QVBoxLayout(side)
        sb.setContentsMargins(16, 16, 8, 16)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda item, _p: self._open(item))
        sb.addWidget(self.list, 1)
        row = QHBoxLayout()
        new = QPushButton("New project…")
        new.setObjectName("primary")
        new.clicked.connect(self._new)
        row.addWidget(new)
        self.rename_button = QPushButton("Rename…")
        self.rename_button.clicked.connect(self._rename)
        row.addWidget(self.rename_button)
        sb.addLayout(row)
        folder = QPushButton("Open projects folder")
        folder.clicked.connect(lambda: (projects_dir().mkdir(parents=True, exist_ok=True),
                                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(projects_dir())))))
        sb.addWidget(folder)
        split.addWidget(scrollable(side))

        right = QWidget()
        right.setObjectName("page")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 16, 16, 16)
        self.heading = h1("")
        rl.addWidget(self.heading)
        car_row = QHBoxLayout()
        car_row.addWidget(muted("Car"))
        self.car = QLineEdit()
        self.car.setPlaceholderText("e.g. 2008 Golf R32, DSG, Haldex Gen4")
        self.car.editingFinished.connect(self._save_fields)
        car_row.addWidget(self.car, 1)
        rl.addLayout(car_row)
        self.banner = Banner()
        rl.addWidget(self.banner)

        self.tabs = QTabWidget()
        # Versions
        vbox = QWidget()
        vl = QVBoxLayout(vbox)
        self.versions = _table(["Label", "Added", "File", "Notes"])
        self.versions.setToolTip("Tune files for this project, each with a label. Files are copied into "
                                 "the project so they can't go missing.")
        self.versions.itemSelectionChanged.connect(self._update_buttons)
        vl.addWidget(self.versions, 1)
        vrow = QHBoxLayout()
        for text, slot, name in (("Add version…", self._add_version, None),
                                 ("Open in Flash", self._flash_version, "flash_button"),
                                 ("Compare with previous", self._compare_previous, "compare_button"),
                                 ("Open in map viewer", self._map_version, "map_button"),
                                 ("Remove", self._remove_version, "remove_version_button")):
            b = QPushButton(text)
            b.clicked.connect(slot)
            vrow.addWidget(b)
            if name:
                setattr(self, name, b)
        vrow.addStretch(1)
        from .pages import hidden_features
        self.features_changed(hidden_features(state.settings))
        vl.addLayout(vrow)
        self.tabs.addTab(vbox, "Tune versions")
        # Modules + history
        mbox = QWidget()
        ml = QVBoxLayout(mbox)
        self.modules = QListWidget()
        self.modules.setToolTip("Modules in this project. Their backups and flashes show below.")
        self.modules.setMaximumHeight(110)
        ml.addWidget(self.modules)
        mrow = QHBoxLayout()
        self.link_button = QPushButton("Add the current module")
        self.link_button.clicked.connect(self._link_module)
        mrow.addWidget(self.link_button)
        pick = QPushButton("Add a module from the library…")
        pick.clicked.connect(self._pick_module)
        mrow.addWidget(pick)
        unlink = QPushButton("Remove")
        unlink.clicked.connect(self._unlink_module)
        mrow.addWidget(unlink)
        mrow.addStretch(1)
        ml.addLayout(mrow)
        self.history = _table(["When", "Module", "What", "Result", "File"])
        ml.addWidget(self.history, 1)
        self.tabs.addTab(mbox, "Modules && history")
        # Logs
        lbox = QWidget()
        ll = QVBoxLayout(lbox)
        self.logs = _table(["Log", "Size", "Saved"])
        self.logs.setToolTip("Datalogs that belong to this project (they stay where they are).")
        ll.addWidget(self.logs, 1)
        lrow = QHBoxLayout()
        for text, slot in (("Add logs…", self._add_logs), ("Open in Analyze", self._play_log),
                           ("Remove", self._remove_log)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            lrow.addWidget(b)
        lrow.addStretch(1)
        ll.addLayout(lrow)
        self.tabs.addTab(lbox, "Logs")
        # Notes
        nbox = QWidget()
        nl = QVBoxLayout(nbox)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes: what you changed, how it drove, what to try next…")
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(800)
        self._notes_timer.timeout.connect(self._save_fields)
        self.notes.textChanged.connect(self._notes_timer.start)
        nl.addWidget(self.notes)
        nl.addWidget(muted("Saved automatically."))
        self.tabs.addTab(nbox, "Notes")
        rl.addWidget(self.tabs, 1)
        split.addWidget(scrollable(right))
        split.setSizes([280, 800])
        self.reload()

    # --- list ---

    def reload(self, select=None):
        current = select or (self.project.root if self.project else None)
        self.list.blockSignals(True)
        self.list.clear()
        for p in Project.all():
            item = QListWidgetItem(p.name + (f"\n{p.data['car']}" if p.data.get("car") else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(p.root))
            self.list.addItem(item)
        self.list.blockSignals(False)
        row = 0
        for i in range(self.list.count()):
            if current and self.list.item(i).data(Qt.ItemDataRole.UserRole) == str(current):
                row = i
        if self.list.count():
            self.list.setCurrentRow(row)
            self._open(self.list.item(row))
        else:
            self._open(None)

    def refresh(self):
        self.link_button.setEnabled(self.project is not None and self.state.record is not None)

    def showEvent(self, event):
        super().showEvent(event)
        if self.project:
            self._fill()

    def _new(self):
        name, ok = InputDialog.getText(self, "New project", "Project name (e.g. R32 Stage 1):")
        if ok and name.strip():
            project = Project.create(name.strip())
            if self.state.record:
                project.data["modules"].append(self.state.record.key)
                project.save()
            self.reload(project.root)

    def _rename(self):
        if not self.project:
            return
        name, ok = InputDialog.getText(self, "Rename project", "Name:", text=self.project.name)
        if ok and name.strip():
            self.project.data["name"] = name.strip()
            self.project.save()
            self.reload()

    def _open(self, item):
        self._save_fields()
        self.project = Project(Path(item.data(Qt.ItemDataRole.UserRole))) if item else None
        self._fill()

    def _fill(self):
        on = self.project is not None
        self.tabs.setEnabled(on)
        self.car.setEnabled(on)
        self.rename_button.setEnabled(on)
        if not on:
            self.heading.setText("No project yet")
            self.banner.show_message("Press New project… to start one. The module you're connected to "
                                     "is added automatically.", "info")
            for table in (self.versions, self.history, self.logs):
                table.setRowCount(0)
            self.modules.clear()
            return
        self.banner.hide()
        d = self.project.data
        self.heading.setText(d["name"])
        self.car.setText(d.get("car", ""))
        self.notes.blockSignals(True)
        self.notes.setPlainText(d.get("notes", ""))
        self.notes.blockSignals(False)
        self.versions.setRowCount(len(d["versions"]))
        for r, v in enumerate(d["versions"]):
            for c, text in enumerate((v["label"], v.get("added", ""), Path(v["file"]).name, v.get("notes", ""))):
                self.versions.setItem(r, c, QTableWidgetItem(text))
        self.modules.clear()
        for key in d["modules"]:
            self.modules.addItem(key)
        hist = self.project.module_history()
        self.history.setRowCount(len(hist))
        for r, e in enumerate(hist):
            what = {"backup": "Backup", "flash": "Flash", "verify": "Check after flash"}.get(e["kind"], e["kind"])
            result = e.get("status") or ("error: " + str(e["error"]) if e.get("error") else "")
            if e["kind"] == "verify":
                result = e.get("sw_version", "")
            for c, text in enumerate((e.get("time", ""), e["module"], what, str(result or ""),
                                      Path(e.get("image") or "").name)):
                self.history.setItem(r, c, QTableWidgetItem(text))
        logs = [Path(p) for p in d["logs"]]
        self.logs.setRowCount(len(logs))
        for r, p in enumerate(logs):
            exists = p.is_file()
            size = f"{p.stat().st_size / 1024:.0f} KiB" if exists else "missing"
            saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)) if exists else ""
            for c, text in enumerate((p.name, size, saved)):
                self.logs.setItem(r, c, QTableWidgetItem(text))
        self._update_buttons()
        self.refresh()

    def _save_fields(self):
        if not self.project:
            return
        car, notes = self.car.text().strip(), self.notes.toPlainText()
        if car != self.project.data.get("car") or notes != self.project.data.get("notes"):
            self.project.data["car"] = car
            self.project.data["notes"] = notes
            self.project.save()

    # --- versions ---

    def _selected_version(self):
        rows = self.versions.selectionModel().selectedRows() if self.project else []
        return rows[0].row() if rows else None

    def _update_buttons(self):
        row = self._selected_version()
        for b in (self.flash_button, self.map_button, self.remove_version_button):
            b.setEnabled(row is not None)
        self.compare_button.setEnabled(row is not None and row > 0)

    def _add_version(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add tune version", str(user_dir()),
                                              "ECU images (*.bin *.ori *.mod *.sgo);;All files (*)")
        if not path:
            return
        label, ok = InputDialog.getText(self, "Version label", "Label (e.g. Stock, Stage 1 v2):",
                                         text=Path(path).stem)
        if not ok or not label.strip():
            return
        notes, _ = InputDialog.getText(self, "Version notes", "Notes (optional):")
        try:
            self.project.add_version(Path(path), label.strip(), notes.strip())
        except OSError as e:
            self.banner.show_message(f"Couldn't copy the file: {e}", "error")
            return
        self._fill()

    def _version_file(self, row):
        return self.project.data["versions"][row]["file"]

    def _flash_version(self):
        row = self._selected_version()
        if row is not None:
            self.state.open_in_flash.emit(self._version_file(row))

    def features_changed(self, hidden):
        """Both of these open Maps; hide them while Maps is turned off (Settings → Features)."""
        for button in (self.map_button, self.compare_button):
            button.setVisible("maps" not in hidden)

    def _map_version(self):
        row = self._selected_version()
        if row is not None:
            self.state.open_in_maps.emit(self._version_file(row), "")

    def _compare_previous(self):
        row = self._selected_version()
        if row:
            self.state.open_in_maps.emit(self._version_file(row - 1), self._version_file(row))

    def _remove_version(self):
        row = self._selected_version()
        if row is None:
            return
        v = self.project.data["versions"][row]
        if MessageBox.question(self, "Remove version",
                                f"Remove “{v['label']}” from the project? Its copy in the project folder "
                                "is deleted; the original file isn't touched.") != MessageBox.StandardButton.Yes:
            return
        try:
            Path(v["file"]).unlink(missing_ok=True)
        except OSError:
            pass
        self.project.data["versions"].pop(row)
        self.project.save()
        self._fill()

    # --- modules ---

    def _link_module(self):
        if self.project and self.state.record and self.state.record.key not in self.project.data["modules"]:
            self.project.data["modules"].append(self.state.record.key)
            self.project.save()
            self._fill()

    def _pick_module(self):
        if not self.project:
            return
        keys = sorted(p.name for p in modules_dir().iterdir() if p.is_dir()) if modules_dir().is_dir() else []
        keys = [k for k in keys if k not in self.project.data["modules"]]
        if not keys:
            MessageBox.information(self, "Add module", "Every module in your library is already in this project.")
            return
        key, ok = InputDialog.getItem(self, "Add module", "Module:", keys, 0, False)
        if ok:
            self.project.data["modules"].append(key)
            self.project.save()
            self._fill()

    def _unlink_module(self):
        item = self.modules.currentItem()
        if self.project and item:
            self.project.data["modules"].remove(item.text())
            self.project.save()
            self._fill()

    # --- logs ---

    def _add_logs(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add datalogs", str(datalogs_dir()), "Datalogs (*.csv)")
        if paths:
            for p in paths:
                if p not in self.project.data["logs"]:
                    self.project.data["logs"].append(p)
            self.project.save()
            self._fill()

    def _selected_log(self):
        rows = self.logs.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _play_log(self):
        row = self._selected_log()
        if row is not None:
            self.state.open_in_playback.emit(self.project.data["logs"][row])

    def _remove_log(self):
        row = self._selected_log()
        if row is not None:
            self.project.data["logs"].pop(row)
            self.project.save()
            self._fill()
