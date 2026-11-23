"""Projects: one place per car or tune that groups the modules it touches,
labelled tune versions, datalogs, the modules' backup/flash history and notes.

Stored under Documents/PQ35 Flasher/Projects/<name>/project.json. Tune versions
are copied into the project's Versions folder so they can't go missing;
datalogs are linked where they already are.

A project belongs to one of your Cars (data "car_id", a cars.py id). Each car can have one active
project (the car's "active_project", a project folder name): every new log and dyno session
recorded on that car is linked into it (attach → recent_logs.on_finished), and Recent logs keeps
any log a project links to."""
import json
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
    QCheckBox, QComboBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import cars, recent_logs, theme
from .storage import EcuRecord, datalogs_dir, modules_dir, user_dir
from .widgets import Banner, h1, muted, scrollable
from .popups import InputDialog, MessageBox
from .toolbar import ToolIcon


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


# --- projects and cars ---

def linked_logs():
    """Every log some project links to."""
    return {p for project in Project.all() for p in project.data.get("logs", [])}


def project_for_log(path):
    """The first project that links this log, or None."""
    return next((p for p in Project.all() if str(path) in p.data.get("logs", [])), None)


def active_project(settings, car_id):
    """The car's active project, or None (also None if its folder is gone)."""
    car = cars.get(settings, car_id) if car_id else None
    name = (car or {}).get("active_project")
    if not name or not (projects_dir() / name / "project.json").is_file():
        return None
    return Project(projects_dir() / name)


def set_active(settings, car_id, project):
    """Make project the car's active one (None: no active project)."""
    car = cars.get(settings, car_id)
    if car is None:
        return
    car["active_project"] = project.root.name if project else ""
    cars.save(settings, car_id, car, touch=False)


def auto_link(settings, path, entry):
    """A recording finished: link it into its car's active project. Returns that project or None."""
    project = active_project(settings, cars.for_vin(settings, entry.get("vin")))
    if project is None or str(path) in project.data["logs"]:
        return None
    project.data["logs"].append(str(path))
    project.save()
    return project


def attach(state):
    """Once, at start-up: file new recordings into projects and keep the logs projects link to."""
    if getattr(state, "_projects_attached", False):
        return
    state._projects_attached = True
    recent_logs.on_finished(lambda path, entry: auto_link(state.settings, path, entry))
    recent_logs.keep_also(linked_logs)


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
        self.delete_button = QPushButton("Delete…")
        self.delete_button.setToolTip("Move this project's folder (its notes and tune version copies) to the Trash")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        sb.addLayout(row)
        folder = QPushButton("Open projects folder")
        folder.clicked.connect(lambda: (projects_dir().mkdir(parents=True, exist_ok=True),
                                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(projects_dir())))))
        sb.addWidget(folder)
        if not theme.is_mac():
            split.addWidget(scrollable(side))

        right = QWidget()
        right.setObjectName("page")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 16, 16, 16)
        self.heading = h1("")
        rl.addWidget(self.heading)
        car_row = QHBoxLayout()
        car_row.addWidget(muted("Car"))
        self.car_combo = QComboBox()
        self.car_combo.setMinimumContentsLength(24)
        self.car_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.car_combo.setToolTip("The car this project is for (one of your Cars)")
        self.car_combo.currentIndexChanged.connect(self._car_picked)
        car_row.addWidget(self.car_combo)
        self.cars_button = QPushButton("Cars…")
        self.cars_button.setToolTip("Your saved cars: add one, or change its weight, tyres, gearbox…")
        self.cars_button.clicked.connect(self._open_cars)
        car_row.addWidget(self.cars_button)
        self.active = QCheckBox("Active project for this car")
        self.active.setToolTip("New logs and dyno sessions recorded on this car are added to this project "
                               "automatically (one active project per car). Untick to stop.")
        self.active.toggled.connect(self._active_toggled)
        car_row.addWidget(self.active)
        car_row.addStretch(1)
        rl.addLayout(car_row)
        self.car_info = muted("")
        self.car_info.setWordWrap(True)
        self.car_info.hide()  # shown once it has something to say
        rl.addWidget(self.car_info)
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
        self.logs = _table(["Log", "Kind", "Size", "Saved"])
        self.logs.setToolTip("Datalogs and dyno sessions that belong to this project (they stay where they are, "
                             "in Datalogs). The active project gets new ones by itself.")
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
        if theme.is_mac():
            self._mac_layout(split, side, sb, row, folder, rl, car_row)
        split.addWidget(scrollable(right))
        split.setSizes([280, 800])
        if theme.is_mac():
            from .mac_shell import column_split
            column_split(split)
        cars.filler(state).changed.connect(lambda _cid: self._fill_cars() if self.project else None)
        state.car_changed.connect(self._car_connected)
        self.reload()

    # --- list ---

    def _mac_layout(self, split, side, sb, row, folder, rl, car_row):
        """Mac (Mockups/Swift native 02-projects): a Projects column (title, +, ⋮, the list, where
        they're kept) and the project's name, car and Active on one row above the tabs."""
        from PySide6.QtWidgets import QFrame
        from . import analyze_tools as T
        _pdir = projects_dir
        panel = QFrame()
        panel.setObjectName("macPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(340)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 8, 0, 0)  # the list starts near the top (no heading: the title bar names it)
        lay.setSpacing(8)
        # + and ⋯ under the list, as macOS lists keep their add buttons (not in the title bar:
        # they act on the list)
        head = QHBoxLayout()
        head.setContentsMargins(20, 6, 12, 0)  # the + glyph's left edge 24 px in (mac_shell rule 5)
        head.setSpacing(4)
        add = ToolIcon("add", "New project…")
        add.clicked.connect(self._new)
        head.addWidget(add)

        def fill_more(menu):
            T.menu_action(menu, "Rename…", "edit", self._rename, self.rename_button.isEnabled())
            T.menu_action(menu, "Delete…", "delete", self._delete, self.delete_button.isEnabled(),
                          tip=self.delete_button.toolTip())
            menu.addSeparator()
            T.menu_action(menu, "Open projects folder", "folder_open", lambda: (
                _pdir().mkdir(parents=True, exist_ok=True),
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(_pdir())))))
        head.addWidget(ToolIcon("more_horiz", "Rename, delete, open the projects folder", menu=fill_more))
        sb.removeWidget(self.list)
        lay.addWidget(self.list, 1)
        line = QFrame()
        line.setObjectName("macFootLine")
        line.setFixedHeight(1)
        lay.addWidget(line)
        head.addStretch(1)
        lay.addLayout(head)
        foot = muted("Each project is a folder in Documents/PQ35 Flasher/Projects.")
        foot.setWordWrap(True)
        foot.setContentsMargins(24, 4, 16, 20)
        lay.addWidget(foot)
        side.setParent(self)  # keeps Rename/Delete (their enabled state drives the menu)
        side.hide()
        split.insertWidget(0, panel)
        split.setHandleWidth(1)
        # Right: name, car and Active on one row, then the tabs; the mockup's 26 px sides.
        from .mac_shell import main_layout
        main_layout(rl)  # rules 2–4 beside the column
        rl.removeWidget(self.heading)
        self.heading.setObjectName("macPaneTitle")
        rl.removeItem(car_row)
        car_label = car_row.takeAt(0).widget()  # "Car": the pop-up shows the car's name already
        car_label.hide()
        car_row.insertWidget(0, self.heading)
        car_row.insertSpacing(1, 6)
        from .mac_shell import first_row
        rl.insertLayout(0, first_row(car_row))
        self.car_combo.setMinimumContentsLength(14)

    def reload(self, select=None):
        current = select or (self.project.root if self.project else None)
        self.list.blockSignals(True)
        self.list.clear()
        for p in Project.all():
            car = cars.get(self.state.settings, p.data.get("car_id"))
            second = cars.title(car) if car else p.data.get("car", "")
            star = "  ★" if car and car.get("active_project") == p.root.name else ""
            item = QListWidgetItem(p.name + star + (f"\n{second}" if second else ""))
            item.setToolTip("★ the active project for its car: new logs and dyno sessions are added here" if star
                            else "")
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
        if self.project:  # read it again: new recordings may have been filed into it meanwhile
            self._save_fields()
            self.reload(self.project.root)

    def _new(self):
        name, ok = InputDialog.getText(self, "New project", "Project name (e.g. R32 Stage 1):")
        if ok and name.strip():
            project = Project.create(name.strip())
            if self.state.record:
                project.data["modules"].append(self.state.record.key)
            car_id = cars.for_vin(self.state.settings, self.state.vin())
            if car_id:  # the connected car; the new project becomes its active one
                project.data["car_id"] = car_id
                set_active(self.state.settings, car_id, project)
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

    def _delete(self):
        """The project's folder goes to the Trash (it holds the tune version copies), so it can be put
        back. Linked logs stay where they are (in Datalogs)."""
        if not self.project:
            return
        from PySide6.QtCore import QFile
        d = self.project.data
        n = len(d.get("versions", []))
        extra = f" and its {n} tune version cop{'ies' if n != 1 else 'y'}" if n else ""
        if MessageBox.question(self, "Delete project",
                               f"Move “{self.project.name}” (its notes{extra}) to the Trash? The original tune files "
                               "and the logs it links stay where they are.") != MessageBox.StandardButton.Yes:
            return
        car_id = d.get("car_id")
        car = cars.get(self.state.settings, car_id) if car_id else None
        root = self.project.root
        result = QFile.moveToTrash(str(root))
        moved = result[0] if isinstance(result, tuple) else bool(result)
        if not moved:
            self.banner.show_message(f"Couldn't move {root.name} to the Trash. Nothing was deleted.", "error")
            return
        if car and car.get("active_project") == root.name:
            set_active(self.state.settings, car_id, None)
        self.project = None
        self.reload()
        self.banner.show_message(f"“{d['name']}” moved to the Trash.", "ok")

    def _open(self, item):
        self._save_fields()
        self.project = Project(Path(item.data(Qt.ItemDataRole.UserRole))) if item else None
        self._fill()

    def _fill(self):
        on = self.project is not None
        self.tabs.setEnabled(on)
        for w in (self.car_combo, self.cars_button, self.active):
            w.setEnabled(on)
        self.rename_button.setEnabled(on)
        self.delete_button.setEnabled(on)
        if not on:
            self.heading.setText("No project yet")
            self.banner.show_message("Press New project… to start one. The module you're connected to "
                                     "is added automatically.", "info")
            for table in (self.versions, self.history, self.logs):
                table.setRowCount(0)
            self.modules.clear()
            self.car_info.setText("")
            self.car_info.hide()  # no empty line above the content
            return
        self.banner.hide()
        d = self.project.data
        self.heading.setText(d["name"])
        self._fill_cars()
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
            kind = "Dyno session" if recent_logs.info(p).get("mode") == "dyno" else "Log"
            for c, text in enumerate((p.name, kind, size, saved)):
                self.logs.setItem(r, c, QTableWidgetItem(text))
        self._update_buttons()
        self.refresh()

    def _save_fields(self):
        if not self.project:
            return
        notes = self.notes.toPlainText()
        if notes != self.project.data.get("notes"):
            self.project.data["notes"] = notes
            self.project.save()

    # --- the car ---

    def _fill_cars(self):
        """The car picker, the active tick and the car's summary for the project shown."""
        d = self.project.data
        settings = self.state.settings
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        self.car_combo.addItem("No car", "")
        for cid, car in cars.all_cars(settings).items():
            self.car_combo.addItem(cars.title(car), cid)
        i = self.car_combo.findData(d.get("car_id", ""))
        self.car_combo.setCurrentIndex(i if i >= 0 else 0)
        self.car_combo.blockSignals(False)
        car = cars.get(settings, d.get("car_id"))
        self.active.blockSignals(True)
        self.active.setChecked(bool(car and car.get("active_project") == self.project.root.name))
        self.active.setEnabled(car is not None)
        self.active.blockSignals(False)
        if car:
            bits = [car.get("vin") or "no VIN", f"{car['curb_kg']:,.0f} kg", car.get("tyre", "")]
            others = [p.name for p in Project.all() if p.data.get("car_id") == d.get("car_id") and
                      p.root != self.project.root]
            info = " · ".join(b for b in bits if b)
            if others:
                info += f" · also in: {', '.join(others)}"
        elif d.get("car"):
            info = f"Was described as “{d['car']}”: pick the car above (add it in Cars… if it isn't there)."
        else:
            info = "Pick the car this project is for: then its new logs and dyno sessions can come here by themselves."
        self.car_info.setText(info)
        self.car_info.setVisible(bool(info))

    def _car_picked(self, *_):
        if not self.project:
            return
        settings = self.state.settings
        old = self.project.data.get("car_id", "")
        new = self.car_combo.currentData() or ""
        if old == new:
            return
        if old and (cars.get(settings, old) or {}).get("active_project") == self.project.root.name:
            set_active(settings, old, None)  # it was the old car's active project
        self.project.data["car_id"] = new
        if new and active_project(settings, new) is None:
            set_active(settings, new, self.project)  # the car had none: this is it
        self.project.save()
        self.reload(self.project.root)

    def _active_toggled(self, on):
        car_id = self.project.data.get("car_id") if self.project else None
        if not car_id:
            return
        set_active(self.state.settings, car_id, self.project if on else None)
        self.reload(self.project.root)

    def _car_connected(self, vin):
        """A car was plugged in: show its active project (or its newest one)."""
        car_id = cars.for_vin(self.state.settings, vin) if vin else None
        if not car_id:
            return
        project = active_project(self.state.settings, car_id) or next(
            (p for p in Project.all() if p.data.get("car_id") == car_id), None)
        if project is not None and (self.project is None or self.project.root != project.root):
            self._save_fields()
            self.reload(project.root)

    def _open_cars(self):
        from .cars_dialog import CarsDialog
        CarsDialog(self.state, cars.filler(self.state), select=self.project.data.get("car_id") or None,
                   parent=self.window()).exec()
        if self.project:
            self.reload(self.project.root)

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
