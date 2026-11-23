"""Dyno tab: the virtual dyno in one place. Record (a dyno session: only what the dyno needs, every
full-throttle pull saved), the car it's worked out for (a Cars profile, picked here) and this
session's people, cargo and fuel, the sessions recorded so far, and the dyno sheet itself
(dyno_view.DynoView, maths in dyno.py), live while recording.

Recording is dyno_recorder.DynoRecorder, the tab's own (independent of the Logger page). Sessions
are saved straight into Datalogs/Dyno (Recent logs mode "dyno")."""
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from . import analyze_tools as T
from . import cars, dyno, recent_logs, theme
from .dyno_recorder import DynoRecorder
from .dyno_view import DynoView, imperial
from . import units as unitconv
from .widgets import Banner, collapsible_sidebar, muted
from .toolbar import ToolIcon

PATH_ROLE = Qt.ItemDataRole.UserRole


def _parse(path):
    """(headers, times, columns) of a Standard log (Analyze's reader)."""
    from .playback_page import _parse_csv
    return _parse_csv(path)


class DynoPage(QWidget):
    LIVE_MS = 1000  # how often a session being recorded is read again

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self.path = None             # the log shown (a session, or any log opened)
        self.recording_path = None   # the session being recorded
        self._pull_counts = {}       # (path, mtime) -> pulls found, for the session list

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)
        self.banner = Banner()
        outer.addWidget(self.banner)

        # ── Top: Record + readout | the car and the load (of the session shown, or the next one) ──
        top = QHBoxLayout()
        top.setSpacing(8)
        self.sidebar_button = ToolIcon("list_alt", "Show or hide the sessions sidebar", checkable=True)
        top.addWidget(self.sidebar_button)
        self.record_button = T.RunButton("fiber_manual_record", "err")
        self.record_button.clicked.connect(self.toggle)
        top.addWidget(self.record_button)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        top.addWidget(self.status, 1)
        # The car and what was in it, behind one car icon (the sheet's details line shows both)
        self.load_button = ToolIcon("directions_car", "", dropdown=True)
        self.load_button.clicked.connect(self._open_load)
        self.more_button = ToolIcon("more_vert", "More: export the graph, its numbers or the log",
                                    menu=self._fill_more)
        theme.on_apply(self._show_meta)
        from .mac_shell import CONTENT_MARGINS, adopt_bar
        if adopt_bar(self, top):
            outer.setContentsMargins(*CONTENT_MARGINS)
        else:
            outer.addLayout(top)
        self._units = unitconv.system(state.settings)
        self.kg = dyno.KG_LB if self._units == "imperial" else 1.0
        self.load_panel = None

        # ── Body: sessions | the dyno sheet ──
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        side = QFrame()
        side.setObjectName("card")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("SESSIONS")
        title.setObjectName("cardName")
        head.addWidget(title, 1)
        self.open_button = ToolIcon("file_open", "Open any log (a Datazap / Standard or Integrated "
                                         "Engineering CSV) and find its full-throttle pulls")
        self.open_button.clicked.connect(self._open_file)
        head.addWidget(self.open_button)
        sl.addLayout(head)
        self.sessions = QListWidget()
        self.sessions.setWordWrap(True)
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sessions.itemSelectionChanged.connect(self._session_picked)
        self.sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._session_menu)
        sl.addWidget(self.sessions, 1)
        note = muted("Saved in Datalogs/Dyno as you record. Nothing is deleted unless you discard it.")
        note.setWordWrap(True)
        sl.addWidget(note)
        side.setMinimumWidth(200)
        split.addWidget(side)
        self.view = DynoView(state.settings, self._car_for, self)
        split.addWidget(self.view)
        # The column's toggle right after the pull picker (as Analyze's after Select channels)
        top.removeWidget(self.sidebar_button)
        self.view.control_row.insertWidget(1, self.sidebar_button)
        # The controls row ends with the icons in the one right-hand order (both interfaces)
        from .mac_shell import row_icons
        row = self.view.control_row
        row_icons(row, tools=(self.load_button,), more=self.more_button)
        for i in range(row.count()):  # controls at their own width, icons at the right
            row.setStretch(i, 0)
        row.insertStretch(row.indexOf(self.load_button.parentWidget()), 1)
        if theme.is_mac():  # mac_shell's layout rules: Sessions a full-height column, the controls row 1 beside it
            self.sidebar_button.setProperty("plain", True)
            from .mac_shell import column, first_row, main_layout
            first_row(row)
            main_layout(self.view.layout())
            outer.setContentsMargins(0, 0, 0, 0)  # the column runs to the window's edges
            outer.setSpacing(0)
            title.setText("Sessions")  # a column heading, as Analyze's and Diagnostics' (no capitals)
            title.setObjectName("macPaneTitle")
            first_row(head)
            from .mac_shell import ROW_GAP
            sl.setSpacing(ROW_GAP)  # the list starts where every page's content does (rule 3)
            column(side)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 900])
        if theme.is_mac():
            from .mac_shell import column_split
            column_split(split)
        outer.addWidget(split, 1)
        collapsible_sidebar(split, state.settings, "dyno", "Sessions", button=self.sidebar_button)

        self.recorder = DynoRecorder(state, self)
        self.recorder.started.connect(self._started)
        self.recorder.finished.connect(self._finished)
        self.recorder.message.connect(self.banner.show_message)
        self.filler = cars.filler(state)
        self.filler.changed.connect(self._cars_changed)

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(self.LIVE_MS)
        self._live_timer.timeout.connect(self._live_tick)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._show_status)

        state.changed.connect(self.refresh)
        state.busy_changed.connect(lambda _busy: self.refresh())
        state.car_changed.connect(lambda _vin: (self._show_meta(), self.view.car_changed()))
        self._fill_sessions(select_first=True)
        self._show_meta()
        self.refresh()

    # --- the car and the load ---
    # Every session remembers the car and load (people, cargo, fuel) it was recorded with, in its
    # Recent logs entry ("dyno"); a log opened from elsewhere keeps them in settings dyno.log_meta.
    # With no session shown (or before recording) the controls set the NEXT session's.

    def _context(self):
        """The log whose car / load the controls show: the one recording, else the one shown, else
        None (the next session)."""
        return self.recording_path or self.path

    def _next_meta(self):
        opts = self.state.settings.get("dyno") or {}
        car_id = opts.get("next_car") or ""
        if not cars.get(self.state.settings, car_id):
            car_id = cars.for_vin(self.state.settings, self.state.vin()) or ""
        return dict(cars.SESSION_DEFAULTS, **(opts.get("session") or {}), car_id=car_id)

    def _stored(self, path):
        if recent_logs.is_recent(path):
            return recent_logs.info(path).get("dyno")
        return ((self.state.settings.get("dyno") or {}).get("log_meta") or {}).get(str(path))

    def meta(self):
        """{car_id, people, cargo_kg, fuel_frac} for what's shown."""
        path = self._context()
        if path is None:
            return self._next_meta()
        stored = self._stored(path)
        if stored:
            return {**cars.SESSION_DEFAULTS, "car_id": "", **stored}
        # recorded before sessions remembered this: the log's car (by VIN) and the current load
        from . import log_export
        m = self._next_meta()
        m["car_id"] = cars.for_vin(self.state.settings, log_export.log_vin(Path(path))) or m["car_id"]
        return m

    def _set_meta(self, **changes):
        path = self._context()
        settings = self.state.settings
        opts = dict(settings.get("dyno") or {})
        if path is None:
            if "car_id" in changes:
                opts["next_car"] = changes["car_id"]
            load = {k: changes[k] for k in cars.SESSION_DEFAULTS if k in changes}
            if load:
                opts["session"] = dict(self._next_meta(), **load)
                opts["session"].pop("car_id", None)
            settings.set("dyno", opts)
        else:
            m = dict(self.meta(), **changes)
            if not recent_logs.update(path, dyno=m):
                opts["log_meta"] = dict(opts.get("log_meta") or {}, **{str(path): m})
                settings.set("dyno", opts)
        self._show_meta()
        self.view.car_changed()

    def _car_for(self, _vin):
        m = self.meta()
        car = cars.get(self.state.settings, m["car_id"])
        return cars.dyno_car(car, m), car is not None

    def _show_meta(self):
        """The car picker and the Load button for what's shown."""
        m = self.meta()
        settings = self.state.settings
        if self.load_panel is None:
            self._build_load_panel()
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        for cid, car in cars.all_cars(settings).items():
            self.car_combo.addItem(cars.title(car), cid)
            self.car_combo.setItemData(self.car_combo.count() - 1, car.get("vin") or "no VIN",
                                       Qt.ItemDataRole.ToolTipRole)
        if self.car_combo.findData(m["car_id"]) < 0:
            self.car_combo.insertItem(0, "Typical car (no saved profile)", "")
        self.car_combo.setCurrentIndex(max(self.car_combo.findData(m["car_id"]), 0))
        self.car_combo.blockSignals(False)
        whose = "this session" if self._context() is not None else "the next session"
        self.car_combo.setToolTip(f"The car {whose} is worked out for (from its VIN). Change it only if it's "
                                  "wrong or the log had no VIN.")
        car = cars.get(settings, m["car_id"])
        mass = dyno.car_mass(cars.dyno_car(car, m))
        imp = imperial(settings)
        people = int(m["people"])
        who = "driver" if people == 1 else f"{people} people"
        fuel = dict((f, t) for f, t in cars.FUEL_LEVELS).get(float(m["fuel_frac"]), "")
        fuel = f"{fuel.lower()} tank" if fuel and fuel != "Nearly empty" else "nearly empty"
        cargo = f" + {m['cargo_kg'] * self.kg:.0f} {'lb' if imp else 'kg'}" if m["cargo_kg"] else ""
        total = f"{mass * dyno.KG_LB:,.0f} lb" if imp else f"{mass:,.0f} kg"
        name = cars.title(car) if car else "No saved car: typical figures"
        self.load_button.set_color("text" if car else "warn")
        self.load_button.setToolTip(f"Car and load for {whose}:\n{name}\n{who}{cargo} · {fuel} · {total}\n\n"
                                    "Click to change the people, cargo or fuel, or the car if it's wrong "
                                    "(weight matters most: 5% off is 5% off).")
        if self.load_panel is not None and self.load_panel.isVisible():
            self._fill_load_panel()

    def _car_picked(self, *_):
        self._set_meta(car_id=self.car_combo.currentData() or "")

    def _open_load(self):
        if self.load_panel is None:
            self._build_load_panel()
        self._fill_load_panel()
        self.load_panel.open_at(self.load_button, align="right")

    def _build_load_panel(self):
        from .widgets import PopupCard
        imp = imperial(self.state.settings)
        panel = self.load_panel = PopupCard(self, min_width=380)
        box = QVBoxLayout(panel.body)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        self.load_heading = QLabel("")
        self.load_heading.setObjectName("cardName")
        box.addWidget(self.load_heading)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        car_row = QHBoxLayout()
        self.car_combo = QComboBox()
        self.car_combo.setMinimumContentsLength(18)
        self.car_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.car_combo.currentIndexChanged.connect(self._car_picked)
        car_row.addWidget(self.car_combo, 1)
        edit = QPushButton("Edit car…")
        edit.setToolTip("Cars: weight, tyres, gearbox, drag; add a car, or fill one in with AI")
        edit.clicked.connect(lambda: (panel.close(), self._open_cars()))
        car_row.addWidget(edit)
        form.addRow("Car", car_row)
        self.people = QSpinBox()
        self.people.setRange(1, 7)
        self.people.setToolTip(f"Driver and passengers, {dyno.PERSON_KG * self.kg:.0f} {'lb' if imp else 'kg'} each")
        form.addRow("People", self.people)
        self.cargo = QDoubleSpinBox()
        self.cargo.setRange(0, 1000)
        self.cargo.setDecimals(0)
        self.cargo.setSingleStep(5)
        self.cargo.setSuffix(" lb" if imp else " kg")
        self.cargo.setToolTip("Anything else on board: luggage, tools")
        form.addRow("Cargo", self.cargo)
        self.fuel = QComboBox()
        for frac, text in cars.FUEL_LEVELS:
            self.fuel.addItem(text, frac)
        self.fuel.setToolTip("How full the tank was (the car's kerb weight assumes full)")
        form.addRow("Fuel", self.fuel)
        box.addLayout(form)
        self.load_note = muted("")
        self.load_note.setWordWrap(True)
        box.addWidget(self.load_note)
        self.people.valueChanged.connect(self._load_edited)
        self.cargo.valueChanged.connect(self._load_edited)
        self.fuel.currentIndexChanged.connect(self._load_edited)

    def _fill_load_panel(self):
        m = self.meta()
        self._filling_load = True
        self.people.setValue(int(m["people"]))
        self.cargo.setValue(float(m["cargo_kg"]) * self.kg)
        here = float(m["fuel_frac"])
        self.fuel.setCurrentIndex(min(range(len(cars.FUEL_LEVELS)),
                                      key=lambda i: abs(cars.FUEL_LEVELS[i][0] - here)))
        self._filling_load = False
        recording = self.recording_path is not None
        if self._context() is None:
            self.load_heading.setText("NEXT SESSION")
            self.load_note.setText("Set it before you press record: the session keeps it.")
        else:
            self.load_heading.setText("THIS SESSION" + (" (RECORDING)" if recording else ""))
            self.load_note.setText("Change it if it was different on the day: only this session is worked out again.")

    def _load_edited(self, *_):
        if getattr(self, "_filling_load", False):
            return
        self._set_meta(people=self.people.value(), cargo_kg=self.cargo.value() / self.kg,
                       fuel_frac=self.fuel.currentData())

    def _open_cars(self):
        from .cars_dialog import CarsDialog
        CarsDialog(self.state, self.filler, select=self.meta()["car_id"] or None, parent=self.window()).exec()
        self._cars_changed("")

    def _cars_changed(self, _cid):
        self._show_meta()
        self.view.car_changed()
        self._fill_sessions()

    # --- recording ---

    def recording(self):
        return self.recorder.running

    def toggle(self):
        if self.recorder.running:
            self.record_button.setEnabled(False)  # until it has stopped and saved
            self.recorder.stop()
            return
        if self.state.need_adapter("log"):
            return  # no empty session
        self.banner.hide()
        self.recorder.start()

    def _started(self, path):
        recent_logs.update(path, dyno=self._next_meta())  # the session keeps its car and load
        self.recording_path = self.path = Path(path)
        self.sessions.clearSelection()
        self.view.set_log(None, None, vin=self.state.vin(), live="waiting")
        self._show_meta()
        self._live_timer.start()
        self._status_timer.start()
        self.banner.show_message("● Recording. Floor it in one gear (3rd is ideal) from low rpm to near redline, "
                                 "on a flat, empty road; each pull shows here a few seconds in. A run each way "
                                 "cancels slope and wind. Press ■ to stop.", "info")
        self.refresh()

    def _finished(self, path, rows, text, level):
        self._live_timer.stop()
        self._status_timer.stop()
        self.record_button.setEnabled(True)
        self.recording_path = None
        if path is not None:
            self._fill_sessions()
            self._select(Path(path), load=False)
            self._load(Path(path))  # always read it again: the finished session, whole (not the last live read)
            n = len(self.view.pulls)
            from .projects import project_for_log
            project = project_for_log(path)
            filed = f" Added to the project “{project.name}”." if project else ""
            if text:  # stopped by an error, with pulls kept
                self.banner.show_message(text + filed, level)
            else:
                self.banner.show_message((f"Saved {n} pull{'s' if n != 1 else ''} ({rows:,} rows) as a session."
                                          if n else "Saved, but no complete pull was found in it.") + filed,
                                         "ok" if n else "warning")
        else:
            self._load(None)
            self.banner.show_message(text, level)
        self.refresh()

    def _live_tick(self):
        if not self.recorder.running or self.recording_path is None:
            return
        try:
            _headers, times, cols = _parse(self.recording_path)
        except (OSError, ValueError, IndexError, StopIteration):
            return  # no complete rows yet
        if times is None or not len(times):
            return
        first = self.view.live_state == "waiting"
        self.view.set_log(times, dict(cols), vin=self.state.vin(), live="recording", new=first)

    def _show_status(self):
        rec = self.recorder
        if not rec.running:
            return
        elapsed = time.time() - rec.started_at
        rate = rec.samples / elapsed if elapsed > 0 else 0
        writer = rec.writer
        events = getattr(writer, "events", 0)
        if not getattr(writer, "ready", True):
            what = "starting: reading every value once"
        elif getattr(writer, "triggered", False):
            what = f"recording pull {events}"
        else:
            what = "waiting for full throttle" + (f" · {events} pull{'s' if events != 1 else ''} so far" if events else "")
        self.status.setText(f"● {int(elapsed // 60)}:{int(elapsed % 60):02d} · {rate:.0f} reads/s · {what}"
                            + (f" · {rec.errors} errors" if rec.errors else ""))

    # --- sessions ---

    def _pulls_in(self, path):
        """How many pulls a session has (cached by file and its modified time)."""
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return None
        if key not in self._pull_counts:
            try:
                _h, times, cols = _parse(path)
                self._pull_counts[key] = len(dyno.find_pulls(times, cols))
            except Exception:
                self._pull_counts[key] = None
        return self._pull_counts[key]

    def _fill_sessions(self, select_first=False):
        current = self.path
        settings = self.state.settings
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for path, entry in recent_logs.entries():
            if entry.get("mode") != "dyno" or not (entry.get("rows") or 0) or path == self.recording_path:
                continue
            try:
                started = entry.get("started") or path.stat().st_mtime
            except OSError:
                continue
            when = time.strftime("%a %d %b · %H:%M", time.localtime(started))
            n = self._pulls_in(path)
            car = cars.get(settings, (entry.get("dyno") or {}).get("car_id")) or \
                cars.get(settings, cars.for_vin(settings, entry.get("vin")))
            what = (f"{n} pull{'s' if n != 1 else ''}" if n is not None else f"{entry.get('rows', 0):,} rows")
            item = QListWidgetItem(f"{when} · {what}" + (f"\n{cars.title(car)}" if car else ""))
            item.setData(PATH_ROLE, str(path))
            item.setToolTip(f"{path.name}\n{recent_logs.describe(entry)}")
            self.sessions.addItem(item)
        if self.sessions.count() == 0:
            item = QListWidgetItem("No dyno sessions yet.\nPress ● to record one.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sessions.addItem(item)
        self.sessions.blockSignals(False)
        if current is not None and not self.recorder.running:
            self._select(current, load=False)
        elif select_first and self.sessions.item(0).data(PATH_ROLE):
            self.sessions.setCurrentRow(0)  # loads it

    def _select(self, path, load=True):
        for i in range(self.sessions.count()):
            item = self.sessions.item(i)
            if item.data(PATH_ROLE) and Path(item.data(PATH_ROLE)) == Path(path):
                self.sessions.blockSignals(not load)
                self.sessions.setCurrentRow(i)
                self.sessions.blockSignals(False)
                return
        if load:
            self._load(Path(path))

    def _session_picked(self):
        if self.recorder.running:
            return  # the recording keeps the view; pick a session after Stop
        items = self.sessions.selectedItems()
        if items and items[0].data(PATH_ROLE):
            self._load(Path(items[0].data(PATH_ROLE)))

    def _session_menu(self, pos):
        item = self.sessions.itemAt(pos)
        if item is None or not item.data(PATH_ROLE):
            return
        path = Path(item.data(PATH_ROLE))
        menu = QMenu(self)
        T.menu_action(menu, "Open in Analyze", "troubleshoot", lambda: self.state.open_in_playback.emit(str(path)))
        from .widgets import reveal_file
        T.menu_action(menu, "Show in Finder" if sys.platform == "darwin" else "Show in folder", "folder_open",
                      lambda: reveal_file(path))
        T.menu_action(menu, "Discard…", "delete", lambda: self._discard(path))
        menu.exec(self.sessions.viewport().mapToGlobal(pos))

    def _discard(self, path):
        from PySide6.QtWidgets import QMessageBox
        from .popups import MessageBox
        answer = MessageBox.question(self, "Discard session", f"Delete {path.name}? This can't be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            recent_logs.discard(path)
        except OSError as e:
            self.banner.show_message(f"Couldn't delete {path.name}: {e}", "error")
            return
        if self.path is not None and Path(self.path) == path:
            self._load(None)
        self._fill_sessions(select_first=True)

    def _open_file(self):
        from .storage import datalogs_dir
        name, _f = QFileDialog.getOpenFileName(self, "Open a log for the dyno", str(datalogs_dir()),
                                               "Logs (*.csv *.CSV)")
        if name:
            self.sessions.clearSelection()
            self._load(Path(name))

    def _load(self, path):
        """Show a log (a session, or any log): find its pulls and draw."""
        if path is None:
            self.path = None
            self.view.set_log(None, None)
            self._show_meta()
            return
        from . import log_export, log_import
        try:
            native, _fmt = log_import.native_path(path)
            _headers, times, cols = _parse(native)
        except Exception as e:  # a log we can't read: say so, keep what was shown
            self.banner.show_message(f"Couldn't open {Path(path).name}: {e}", "error")
            return
        self.path = Path(path)
        self.view.set_log(times, dict(cols), vin=log_export.log_vin(Path(path)) or "")
        self._show_meta()

    # --- export ---

    def _fill_more(self, menu):
        idle = not self.recording()
        sheet = self.view.can_export() and idle
        tip = "" if sheet else "Pick a session with a pull first"
        T.menu_header(menu, "This pull")
        T.menu_action(menu, "Export graph as image…", "image", self._export_png, enabled=sheet,
                      tip=tip or "A PNG picture of the sheet as shown: the figures, details and graph, to share")
        T.menu_action(menu, "Export numbers as table…", "table_chart", self._export_csv, enabled=sheet,
                      tip=tip or "A CSV of the curves shown (power and torque at each rpm), for a spreadsheet")
        T.menu_header(menu, "This session")
        T.menu_action(menu, "Export log…", "ios_share", self._export_log, enabled=self.path is not None and idle,
                      tip="The whole recording (every reading) as Standard CSV, Integrated Engineering, "
                          "MegaLogViewer or generic CSV, e.g. for Analyze or another tool")

    def _export_name(self, suffix, ext):
        from .storage import datalogs_dir
        import re
        stem = Path(self.path).stem if self.path else "dyno"
        # exports say they're dyno: engine_2026-11-14_121055 → dyno_2026-11-14_121055_graph.png
        m = re.match(r"^[A-Za-z]+_(\d{4}-\d{2}-\d{2}_\d{6}.*)$", stem)
        stem = "dyno_" + m.group(1) if m else stem if stem.startswith("dyno") else "dyno_" + stem
        name, _f = QFileDialog.getSaveFileName(self, "Export", str(datalogs_dir() / f"{stem}_{suffix}.{ext}"),
                                               f"{ext.upper()} (*.{ext})")
        return Path(name) if name else None

    def _export_png(self):
        path = self._export_name("graph", "png")
        if path is None:
            return
        if self.view.sheet_image().save(str(path), "PNG"):
            self.banner.show_message(f"Exported the dyno sheet as {path.name}.", "ok")
        else:
            self.banner.show_message(f"Couldn't save {path.name}.", "error")

    def _export_csv(self):
        path = self._export_name("table", "csv")
        if path is None:
            return
        try:
            self.view.export_csv(path)
        except OSError as e:
            self.banner.show_message(f"Couldn't save {path.name}: {e}", "error")
            return
        self.banner.show_message(f"Exported the numbers shown as {path.name}.", "ok")

    def _export_log(self):
        from . import log_export, log_import
        native, _fmt = log_import.native_path(Path(self.path))
        log_export.export_dialog(self, self.state, Path(native), self.banner, stem=Path(self.path).stem)

    def _units_check(self):
        """Metric / imperial switched in Settings while away: relabel everything."""
        system = unitconv.system(self.state.settings)
        if system == self._units:
            return
        self._units = system
        self.kg = dyno.KG_LB if system == "imperial" else 1.0
        if self.load_panel is not None:
            self.load_panel.deleteLater()
            self.load_panel = None  # rebuilt with the new units by _show_meta
        self._show_meta()
        self._fill_sessions()
        self.view.units_changed()

    # --- state ---

    def refresh(self):
        rec = self.recording()
        self.record_button.set_recording(rec)
        other = self.state.busy and not rec  # a Logger recording, Live View or another job
        can = bool(self.state.family and self.state.family.get("can_log"))
        from .needs_car import gate
        if rec:
            self.record_button.setEnabled(True)  # Stop must always work
        else:
            gate(self.record_button, self.state, can and not other)
        if rec:
            self.record_button.setToolTip("Stop recording dyno pulls")
        elif other:
            self.record_button.setToolTip("Something else is using the module (Logger, Live View or another job)")
        elif not can:
            self.record_button.setToolTip("Connect an interface and choose the engine on the Connect page to record")
        else:
            self.record_button.setToolTip("Record dyno pulls: floor it in one gear (3rd is ideal) from low rpm to "
                                          "near redline; each full-throttle pull is saved")
        if not rec:
            self.status.setText("Ready: press ● and floor it in one gear." if can and not other else
                                "The module is busy (Logger, Live View or another job)." if other else
                                "Connect and choose the engine to record; sessions can be viewed any time.")
        self.open_button.setEnabled(not rec)

    def showEvent(self, event):
        super().showEvent(event)
        self._units_check()
        self._fill_sessions()
        self.refresh()
