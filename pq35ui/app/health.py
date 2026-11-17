"""Fault codes: the one shared controller (state.faults, see controller()).

It holds a row per module from the last scan (rows, keyed by family name, or "addr:XX" for
modules the app has no family for, which carry their TP2 ID and are read/cleared by it) and
runs the fault-code jobs one module at a time. Two views show it:
- the Dashboard card (overview_card, built here): one header line, a row for each module with
  codes or a problem, the rest named in one line; double-click = work on it in Diagnostics;
- Diagnostics (diagnostics_view.AutoScanView): the module list and the scan log, following
  `updated`.

Every scan reads every module's codes (load_full_scan takes them straight from the scan).
Clearing reads the module back straight away; a code that's still there after a confirmed
clear is marked "still active". Results are saved (settings "health_last") and shown greyed out
next session until read again.
"""
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QTreeWidget,
                               QTreeWidgetItem)

from .popups import MessageBox
from .widgets import Banner, Card, muted

def _color(role):
    """Theme status colours (light/dark aware): "ok", "warn", "err", or "muted" for grey."""
    from . import theme
    return QColor(theme.colors()[role])


LEVEL_ROLE = {"ok": "ok", "warn": "warn", "bad": "err"}

BUSY_RETRY_MS = 300
BUSY_PATIENCE = 60  # ~18 s waiting for another job before giving up
# Rows the Dashboard card lists on their own even without codes.
PROBLEM_STATUSES = ("No answer", "Can't be reached", "In loader", "Skipped", "Clear not confirmed",
                    "Waiting…", "Reading…", "Clearing…")


def _code_text(code):
    return str(code.get("code_text") or code.get("code"))


def _status_text(code):
    return str(code.get("status_text") or f"status 0x{code.get('status', 0):02X}")


def _label(base, address):
    """A module's name with its workshop (VCDS) address in front when known: "22 AWD"."""
    return f"{address:02X} {base}" if isinstance(address, int) else base


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _error_text(error):
    return error.get("message", str(error)) if isinstance(error, dict) else str(error)


def _row(key, label, status, dtc=False, codes=None, note="", active=None, level="grey",
         tp2=None, address=None, when=""):
    return {"family": key, "label": label, "status": status, "dtc": dtc, "codes": list(codes or []),
            "note": note, "active": list(active or []), "level": level, "tp2": tp2,
            "address": address, "time": when}


def controller(state):
    """The one fault-code controller, shared by the Dashboard and Diagnostics."""
    if getattr(state, "faults", None) is None:
        state.faults = ModuleHealthCard(state)
    return state.faults


class ModuleHealthCard(QObject):
    """Owns the fault-code rows and jobs, and the Dashboard card (overview_card)."""

    updated = Signal()  # rows or messages changed

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self.queue, self.batch = [], []
        self.running = False
        self.patience = BUSY_PATIENCE
        self.rows = {}
        self.fresh = set()      # read since the app started; saved results show greyed out
        self.checked_at = ""
        self.selected = None    # the module the other tabs work on (state.family)
        self.focused = None     # the module clicked in Diagnostics ("this code" for the Assistant)
        self.dialog_parent = None  # Diagnostics' view: where Clear/lock/AI dialogs open when shown
        self._build_card()
        self._load_last()
        state.changed.connect(self.refresh)
        state.busy_changed.connect(lambda _: self.refresh())
        self.refresh()

    # --- the Dashboard card ---

    def _build_card(self):
        card = self.overview_card = Card()
        head = QHBoxLayout()
        title = QLabel("Fault codes")
        title.setStyleSheet("font-weight: 600;")
        title.setToolTip("Every module the scan found and the codes it has stored. The one you're "
                         "working on is marked ●. Double-click a module to open it in Diagnostics.")
        head.addWidget(title)
        self.summary = muted("")
        self.summary.setWordWrap(False)
        # half-width card on the Dashboard: the summary gives way (full text in its tooltip)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self.summary, 1)
        self.button = QPushButton("Re-read all")
        self.button.setToolTip("Read the fault codes again from every module the scan found (read-only).")
        self.button.clicked.connect(self.start)
        self.clear_all_button = QPushButton("Clear all…")
        self.clear_all_button.setToolTip("Clear the stored codes on every module that has some, "
                                         "then read each one back.")
        self.clear_all_button.clicked.connect(self.clear_all)
        head.addWidget(self.button)
        head.addWidget(self.clear_all_button)
        card.add(head)
        self.banner = card.add(Banner())
        tree = self.tree = QTreeWidget()
        tree.setHeaderLabels(["Module", "Status", "Fault codes"])
        header = tree.header()
        header.setStretchLastSection(False)
        for col in range(3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 2
                                        else QHeaderView.ResizeMode.ResizeToContents)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.setToolTip("Double-click a module to open it in Diagnostics. The arrow shows each code.")
        tree.itemDoubleClicked.connect(self._open_module)
        tree.itemExpanded.connect(lambda _i: self._fit())
        tree.itemCollapsed.connect(lambda _i: self._fit())
        tree.customContextMenuRequested.connect(self._menu)
        card.add(tree)
        self.rest = card.add(muted(""))

    def _fit(self):
        """Show every row: the page scrolls, not the list."""
        tree = self.tree
        height = tree.header().sizeHint().height() + 2 * tree.frameWidth() + 4
        item = tree.topLevelItem(0)
        while item is not None:
            height += tree.visualItemRect(item).height() or 26
            item = tree.itemBelow(item)
        tree.setFixedHeight(height)

    def _status_color(self, key, row):
        return _color("muted" if key not in self.fresh else LEVEL_ROLE.get(row["level"], "muted"))

    def _render(self):
        tree = self.tree
        tree.clear()
        text = tree.palette().text().color()
        rest = {}  # summary group -> labels
        for key, row in self.rows.items():
            stale = key not in self.fresh
            mine = key == self.selected
            label = ("● " if mine else "") + row["label"]
            if not row["codes"] and row["status"] not in PROBLEM_STATUSES:
                group = ("No faults" if row["dtc"] or row["status"] in ("OK", "Cleared")
                         else row["note"] or row["status"])
                rest.setdefault(group, []).append(label)
                continue
            active = set(row["active"])
            codes = ", ".join(c["code"] + (" (still active)" if c["code"] in active else "")
                              for c in row["codes"]) or row["note"]
            item = QTreeWidgetItem(tree, [label, row["status"], codes])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setForeground(0, _color("muted") if stale else text)
            item.setForeground(1, self._status_color(key, row))
            item.setForeground(2, _color("muted") if stale else _color("err") if active else text)
            if mine:
                bold = QFont(tree.font())
                bold.setBold(True)
                item.setFont(0, bold)
            when = row.get("time") or self.checked_at
            if stale and when:
                item.setToolTip(1, f"From the check on {when}, not read since.")
            elif row["note"] and row["status"] in ("Can't be reached", "No answer"):
                item.setToolTip(1, row["note"])
            for code in row["codes"]:  # behind the arrow
                still = code["code"] in active
                child = QTreeWidgetItem(item, ["", code["code"], code["text"] + (" · still active" if still else "")])
                if stale or still:
                    for col in (1, 2):
                        child.setForeground(col, _color("muted" if stale else "err"))
        tree.setVisible(tree.topLevelItemCount() > 0)
        groups = sorted(rest, key=lambda g: (g != "No faults", g == "Not found", g))
        if not self.rows:
            self.rest.setText("Not checked yet. A scan reads every module's codes.")
        elif tree.topLevelItemCount() == 0:
            self.rest.setText("All modules report no fault codes. "
                              + " · ".join(f"{g}: {', '.join(rest[g])}" for g in groups if g != "No faults"))
        else:
            self.rest.setText(" · ".join(f"{g}: {', '.join(rest[g])}" for g in groups))
        self.rest.setVisible(bool(self.rest.text()))
        codes = sum(len(r["codes"]) for r in self.rows.values() if r["dtc"])
        active = sum(len(r["active"]) for r in self.rows.values() if r["dtc"])
        parts = ([f"Last check {self.checked_at}"] if self.checked_at else []) + \
            ([_plural(codes, "code")] if any(r["dtc"] for r in self.rows.values()) else []) + \
            ([f"{active} still active"] if active else [])
        self.summary.setText(" · ".join(parts))
        self.summary.setToolTip(self.summary.text())
        QTimer.singleShot(0, self._fit)
        self.updated.emit()

    def _open_module(self, item, _column=0):
        """Double-click on the Dashboard: work on that module, in Diagnostics."""
        while item.parent() is not None:
            item = item.parent()
        key = item.data(0, Qt.ItemDataRole.UserRole)
        family = self.state.family_by_name(key) if key else None
        if family is None:
            self.banner.show_message("Scan first, then double-click a module that answered.", "info")
            return
        identity = family.get("scan_identity")
        if identity is None:
            entry = next((e for e in (self.state.last_scan or {}).get("result") or []
                          if e.get("family") == key and e.get("status") == "ok"), None)
            if entry is None:
                self.banner.show_message("Scan first, then double-click a module that answered.", "info")
                return
            identity = entry.get("identity")
        if not self.state.busy:
            self.state.select_module(family, identity)
            self.state.open_tab.emit("diagnostics")

    # --- rows ---

    def module_for(self, key):
        """What state.run needs to reach a row's module: its family, or for another gateway
        module a stand-in with its TP2 ID as the alias."""
        family = self.state.family_by_name(key)
        if family is not None:
            return family
        row = self.rows.get(key) or {}
        if row.get("tp2") is None:
            return None
        return {"name": key, "label": row["label"], "alias": f"0x{row['tp2']:02X}", "module": row["tp2"]}

    def faulty(self):
        """Modules with codes read this session (never clear on the strength of old results)."""
        return [key for key, row in self.rows.items() if row["dtc"] and row["codes"] and key in self.fresh]

    def _parent(self):
        view = self.dialog_parent
        return view if view is not None and view.isVisible() else self.overview_card

    # --- saved results ---

    def _load_last(self):
        last = self.state.settings.get("health_last") or {}
        self.checked_at = last.get("time", "")
        for m in last.get("modules") or []:
            if m.get("family"):  # older versions saved text only: skipped
                self.rows[m["family"]] = _row(m["family"], m.get("module", m["family"]), m.get("status", ""),
                                              m.get("dtc", False), m.get("list"), m.get("note", ""),
                                              m.get("active"), m.get("level", "grey"), m.get("tp2"),
                                              m.get("address"), m.get("time", ""))
        if self.rows:
            self.banner.show_message("Saved from the last check (grey) until a scan reads the car "
                                     "again.", "info")

    def _save(self):
        self.checked_at = _now()
        modules = [{"module": row["label"], "status": row["status"],
                    "codes": ", ".join(c["code"] for c in row["codes"]) or row["note"],
                    "family": key, "dtc": row["dtc"], "list": row["codes"], "note": row["note"],
                    "active": row["active"], "level": row["level"], "tp2": row.get("tp2"),
                    "address": row.get("address"), "time": row.get("time") or self.checked_at}
                   for key, row in self.rows.items()]
        self.state.settings.set("health_last", {"time": self.checked_at, "modules": modules})
        self.state.fault_summary = {key: (len(row["codes"]), len(row["active"]))
                                    for key, row in self.rows.items() if row["dtc"] and key in self.fresh}
        self.state.faults_changed.emit(sum(len(r["codes"]) for r in self.rows.values() if r["dtc"]))

    # --- a scan's results ---

    def load_full_scan(self, result):
        """Show a scan's results: every gateway module with its codes (no extra reads)."""
        if self.running or result.get("error"):
            return
        gateway = result.get("gateway") or {}
        # Uncoded addresses that didn't answer aren't part of the car (VCDS doesn't list them).
        modules = [m for m in gateway.get("modules") or []
                   if not (m.get("coded") is False and m.get("status") != "ok")]
        previous = {key: row["active"] for key, row in self.rows.items()}
        when = _now()
        if result.get("full", True):
            self.rows, self.fresh = {}, set()
        else:
            # A quick scan read only some modules: update those, keep the rest as they were.
            self.rows, self.fresh = dict(self.rows), set()
        for m in modules:
            key = m["key"]
            family = self.state.family_by_name(m.get("family")) if m.get("family") else None
            base = family["label"] if family else (m.get("name") or f"0x{m['tp2']:02X}")
            row = self.rows[key] = _row(key, _label(base, m.get("address")), "", tp2=m.get("tp2"),
                                        address=m.get("address"), active=previous.get(key), when=when)
            self.fresh.add(key)
            status, report = m.get("status"), m.get("dtc") or {}
            if status == "ok" and "dtc" in m and not report.get("error"):
                row["dtc"] = True
                self._apply_codes(row, report, "dtc")
            elif status == "ok":
                row.update(status="Found", note="Doesn't report fault codes")
            elif status == "bootloader":
                row.update(status="In loader", note="Recover it from the Connect page", level="bad")
            elif m.get("coded") is False:
                row.update(status="Not coded", note="")
            else:
                row.update(status="Can't be reached", level="warn",
                           note="Listed by the gateway but didn't answer: not fitted, asleep or on "
                                "another bus.")
        # Supported modules with no row yet: only those nothing answered for. (One whose address
        # holds some other controller already has its "addr:XX" row.)
        covered = {m.get("family") for m in modules} | {m.get("tp2") for m in modules}
        for entry in result.get("result") or []:
            if entry["family"] not in covered and entry["module"] not in covered:
                family = self.state.family_by_name(entry["family"]) or {}
                self.rows[entry["family"]] = _row(entry["family"], family.get("label", entry["family"]),
                                                  "Not found", when=when)
                self.fresh.add(entry["family"])
        self._save()
        reached = [r for r in self.rows.values() if r["status"] == "Can't be reached"]
        faulty = [r for r in self.rows.values() if r["codes"]]
        parts = [f"Scan: {_plural(len(modules), 'module')} on the gateway list",
                 _plural(len(faulty), "module") + " with fault codes" if faulty else "no fault codes"]
        if reached:
            parts.append(f"{len(reached)} couldn't be reached")
        self.banner.show_message(", ".join(parts) + ".", "warning" if faulty or reached else "ok")
        self.refresh()

    # --- re-reading and clearing ---

    def start(self):
        """Re-read every module that gives fault codes. Before a scan this session, scan first."""
        if self.running:
            return
        if not self.state.last_scan:
            self.state.request_scan.emit()
            return
        self.reread([key for key, row in self.rows.items() if row["dtc"] and self.module_for(key)])

    def reread(self, keys):
        jobs = [("dtc", key) for key in keys]
        jobs.sort(key=lambda job: job[1] != self.selected)  # the module you're working on first
        for _op, key in jobs:
            self.rows[key].update(status="Waiting…", level="grey")
        self._enqueue(jobs)

    def clear_module(self, key):
        row = self.rows.get(key)
        if row is None or not self._allowed():
            return
        answer = MessageBox.question(
            self._parent(), "Clear fault codes",
            f"Clear all stored fault codes on the {row['label']}? This also deletes their "
            "freeze-frame data. The module is read again straight after.")
        if answer == MessageBox.StandardButton.Yes:
            self._enqueue([("dtc_clear", key)])

    def clear_all(self):
        faulty = self.faulty()
        if not faulty:
            self.banner.show_message("No module has stored fault codes to clear.", "info")
            self.updated.emit()
            return
        if not self._allowed():
            return
        lines = "".join(f"\n• {self.rows[k]['label']}: {', '.join(c['code'] for c in self.rows[k]['codes'])}"
                        for k in faulty)
        answer = MessageBox.question(
            self._parent(), "Clear all fault codes",
            f"Clear the stored fault codes on {_plural(len(faulty), 'module')}?{lines}\n\nThis also "
            "deletes their freeze-frame data. Each module is read again straight after.")
        if answer == MessageBox.StandardButton.Yes:
            self._enqueue([("dtc_clear", k) for k in faulty])

    def _allowed(self):
        from . import security
        return security.require(self._parent(), self.state.settings, "dtc_clear")

    # --- job queue (one module at a time; waits for other jobs to finish) ---

    def _enqueue(self, jobs):
        if self.running:
            self.queue.extend(jobs)
            self.batch.extend(jobs)
            return
        self.running = True
        self.patience = BUSY_PATIENCE
        self.queue, self.batch = list(jobs), list(jobs)
        self.refresh()
        self._next()

    def _next(self):
        if not self.queue:
            return self._done()
        if self.state.busy:
            self.patience -= 1
            if self.patience <= 0:
                for _op, key in self.queue:
                    row = self.rows.get(key)
                    if row is not None and row["status"] in ("Waiting…", "Reading…"):
                        row.update(status="Skipped", note="The adapter was busy")
                self.queue = []
                return self._done(skipped=True)
            QTimer.singleShot(BUSY_RETRY_MS, self._next)
            return
        operation, key = self.queue.pop(0)
        module = self.module_for(key)
        if module is None or key not in self.rows:
            return self._next()
        clearing = operation == "dtc_clear"
        self.banner.show_message(f"{'Clearing' if clearing else 'Reading'} fault codes on the "
                                 f"{self.rows[key]['label']}…", "info")
        self.rows[key].update(status="Clearing…" if clearing else "Reading…", level="grey")
        self._render()
        params = {"freeze_frames": bool(self.state.settings.get("scan_freeze_frames", True))}
        started = self.state.run(operation, params, lambda r, k=key, o=operation: self._result(k, o, r),
                                 lambda e, k=key, o=operation: self._result(k, o, {"error": _error_text(e)}),
                                 module=module)
        if not started:
            self.queue.insert(0, (operation, key))
            QTimer.singleShot(BUSY_RETRY_MS, self._next)

    def _result(self, key, operation, result):
        row = self.rows[key]
        self.fresh.add(key)
        if result.get("error"):
            row.update(status="No answer", level="warn", note=str(result["error"]))
        else:
            row["time"] = _now()
            self._apply_codes(row, result.get("result") or {}, operation)
        self._render()
        self._next()

    @staticmethod
    def _apply_codes(row, report, operation):
        """Put a fault-code read (or clear + read back) into a row."""
        previous_active = set(row["active"])
        row["report"] = report  # the worker's full read (status bytes, freeze frames) for the scan log
        codes = [{"code": _code_text(c), "text": _status_text(c)} for c in report.get("codes") or []]
        present = {c["code"] for c in codes}
        row["codes"] = codes
        row["note"] = "" if codes else "No fault codes"
        if operation == "dtc_clear":
            cleared = (report.get("clear") or {}).get("cleared")
            if not cleared:
                row["active"] = sorted(previous_active & present)
                row.update(status="Clear not confirmed", level="warn")
            elif codes:
                row["active"] = sorted(present)
                row.update(status=f"{len(codes)} still active", level="bad")
            else:
                row["active"] = []
                row.update(status="Cleared", level="ok")
        else:
            row["active"] = sorted(previous_active & present)  # stays marked while it's still there
            if row["active"]:
                extra = len(codes) - len(row["active"])
                row.update(status=(f"{_plural(len(codes), 'fault')}, {len(row['active'])} still active"
                                   if extra else f"{len(row['active'])} still active"), level="bad")
            elif codes:
                row.update(status=_plural(len(codes), "fault"), level="bad")
            else:
                row.update(status="OK", level="ok")

    def _done(self, skipped=False):
        self.running = False
        self._save()
        rows = [self.rows[k] for _o, k in self.batch if k in self.rows]
        active = [r for r in rows if r["active"]]
        if skipped:
            self.banner.show_message("Stopped: another job kept the adapter busy. Try again in a moment.",
                                     "warning")
        elif active:
            self.banner.show_message(f"{_plural(sum(len(r['active']) for r in active), 'code')} came "
                                     "straight back after clearing: the fault is still there. Fix the "
                                     "cause, then clear again.", "error")
        elif any(r["status"] == "Cleared" for r in rows) and not any(r["codes"] for r in rows):
            self.banner.show_message("Cleared. No fault codes stored.", "ok")
        else:
            self.banner.hide()  # the rows and the summary say it
        self.refresh()

    def refresh(self):
        idle = bool(self.state.adapter) and not self.state.busy and not self.running
        self.button.setText("Re-read all" if self.state.last_scan else "Quick Scan")
        self.button.setEnabled(idle)
        self.clear_all_button.setEnabled(idle and bool(self.faulty()))
        for key, row in self.rows.items():  # saved rows keep their old names otherwise
            family = self.state.family_by_name(key)
            if family is not None:
                row["label"] = _label(family["label"], row.get("address"))
        self.selected = (self.state.family or {}).get("name")
        self._render()

    # --- Ask AI ---

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.tree.itemAt(pos)
        menu = QMenu(self.tree)
        top = item
        while top is not None and top.parent() is not None:
            top = top.parent()
        key = top.data(0, Qt.ItemDataRole.UserRole) if top is not None else None
        if key and self.rows.get(key, {}).get("codes"):
            menu.addAction(f"Ask AI about the {self.rows[key]['label']} codes…").triggered.connect(
                lambda: self.ask_module(key))
        if any(r["codes"] for r in self.rows.values()):
            menu.addAction("Ask AI about all modules…").triggered.connect(self._ask_all)
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def ask_module(self, key):
        from . import ai
        row = self.rows[key]
        codes = "\n".join(f"- {c['code']}: {c['text']}" for c in row["codes"])
        ai.ask(self._parent(), self.state.settings, f"{row['label']} codes",
               f"Module: {row['label']}\nCodes:\n{codes}",
               "Explain these fault codes: what each means, whether they're related, how serious, and "
               "what to check first.")

    def _ask_all(self):
        from . import ai
        ai.ask(self._parent(), self.state.settings, "all-module check",
               "Scan results:\n" + "\n".join(self.context_lines()),
               "Summarise the health of this car from these results. Explain any fault codes, how they "
               "might be related across modules, and what to check first.")

    def context_lines(self):
        """Plain-text results for Ask AI (also used by the assistant)."""
        lines = []
        for key, row in self.rows.items():
            mark = " (the module being worked on)" if key == self.selected else ""
            old = "" if key in self.fresh else f" [from the check on {self.checked_at}]"
            lines.append(f"- {row['label']}{mark}: {row['status']} {'' if row['codes'] else row['note']}".rstrip() + old)
            active = set(row["active"])
            for c in row["codes"]:
                lines.append(f"    - {c['code']}: {c['text']}"
                             + (" (still active after clearing)" if c["code"] in active else ""))
        return lines

    def selected_code(self):
        """"<code> (<status>) on the <module>": the first code of the module clicked in
        Diagnostics, so the Assistant can say "this code"."""
        row = self.rows.get(self.focused) or {}
        if not row.get("codes"):
            return None
        code = row["codes"][0]
        return f"{code['code']} ({code['text']}) on the {row['label']}"
