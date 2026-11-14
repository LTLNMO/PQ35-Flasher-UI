"""The screens behind the left-hand navigation."""
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QMenu, QPushButton, QRadioButton,
    QScrollArea, QSpinBox, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .storage import images_dir, patched_dir, timestamp
from .widgets import Badge, Banner, Card, DropZone, FlowRow, HoldButton, ProgressPanel, kib, muted, restyle
from .popups import InputDialog, MessageBox

HALDEX, EPS, BCM = "haldex-gen4", "pq-eps", "pq-bcm"
# Modules whose full firmware can be read back over OBD and diffed, so the Flash
# page can require and compare against a backup. EPS and BCM can't be read back
# in full, so flashing isn't gated on a backup for them.
BACKUP_FAMILIES = {HALDEX}
# Modules the Back up page can snapshot. The BCM snapshot is the boot page plus
# identity/coding (not the full firmware), kept before flashing.
BACKUP_CAPABLE = {HALDEX, BCM}

# Tabs you can turn off in Settings → Features (Dashboard, Connect, Help and Settings always stay).
OPTIONAL_FEATURES = (("projects", "Projects"), ("backup", "Back up"), ("flash", "Flash"),
                     ("patches", "Patches"), ("diagnostics", "Diagnostics"), ("liveview", "Live View"),
                     ("logger", "Logger"), ("playback", "Analyze"), ("maps", "Maps"))
DEFAULT_HIDDEN = ("maps",)  # Maps is read-only and not much use yet


def hidden_features(settings) -> set:
    saved = settings.get("hidden_features")
    return set(DEFAULT_HIDDEN if saved is None else saved)


class Page(QScrollArea):
    title = ""
    # Fit the page to the window (graphs and tables shrink; scroll only when it can't fit).
    # Pages of plain controls (Settings) set this False: natural sizes, scroll when longer.
    fit_to_window = True

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("page")
        from .widgets import _FitToWindow
        self.setWidget(_FitToWindow(inner) if self.fit_to_window else inner)
        self.layout_ = QVBoxLayout(inner)
        self.layout_.setContentsMargins(28, 22, 28, 22)
        self.layout_.setSpacing(14)
        # No title in the page body: the tab bar keeps the current tab expanded with its name.
        state.changed.connect(self.refresh)
        state.busy_changed.connect(lambda _busy: self.refresh())

    def add(self, widget):
        self.layout_.addWidget(widget)
        return widget

    def finish(self):
        self.layout_.addStretch(1)

    def refresh(self):
        pass

    def error_text(self, error):
        return error.get("message", str(error)) if isinstance(error, dict) else str(error)


def convert_sgo_then(page, path, family_name, banner, then):
    """EPS SGOs become a 384 KiB .bin in Documents/PQ35 Flasher/Images first;
    everything after that (checks, patches, flashing) works on the .bin."""
    if not str(path).lower().endswith(".sgo"):
        then(path)
        return
    if family_name != EPS:
        banner.show_message("SGO files are supported for EPS racks only. Use a .bin image.", "error")
        return
    out = images_dir() / f"{Path(path).stem}.bin"
    if out.exists():
        out = images_dir() / f"{Path(path).stem}_{timestamp()}.bin"

    def done(result):
        banner.show_message(f"Converted the SGO ({result.get('source_name') or Path(path).name}, "
                            f"{result['part']}) to {Path(result['out']).name} in your Images "
                            "folder.", "info")
        then(result["out"])

    page.state.offline("convert_sgo", {"path": str(path), "out": str(out)}, done,
                       lambda e: banner.show_message(page.error_text(e), "error"))


def identity_rows(identity):
    rows = [("Part number", identity.get("software_part_number") or identity.get("part_number")),
            ("Software version", identity.get("sw_version")),
            ("Description", identity.get("system_desc")),
            ("Flash attempts / successes", f"{identity.get('flash_attempts')} / {identity.get('flash_counter')}"),
            ("Last flashed", identity.get("flash_date") or "never / unknown")]
    return [(k, str(v)) for k, v in rows if v not in (None, "")]


SCAN_TEXT = {
    "ok": ("Found", "ok"),
    "bootloader": ("In its bootloader", "warning"),
    "responding": ("Answers, but won't identify", "warning"),
    "unexpected": ("Answers as a different module", "error"),
    "absent": ("Not found", "idle"),
    None: ("Not checked", "idle"),
}
CARD_COLUMNS = 2
ART_SIZE = 56  # module picture on each card (the user's own images, see module_art)
# Cards are listed in this order; "absent" never gets a card.
STATUS_ORDER = {"ok": 0, "bootloader": 1, "responding": 2, "unexpected": 3, None: 4}


class ModuleCard(Card):
    """One module that answered (or that you picked): what it is and what to do."""

    def __init__(self, family, page):
        super().__init__()
        self.family = family
        self.page = page
        self.entry = None
        # The module's picture on the left; the card's text and buttons to its right.
        row = QHBoxLayout()
        row.setSpacing(14)
        self.art = QLabel()
        self.art.setFixedSize(ART_SIZE, ART_SIZE)
        self.art.setToolTip(family["label"])
        self.art.hide()  # shown when there's an image for this module (paint_art)
        row.addWidget(self.art, 0, Qt.AlignmentFlag.AlignVCenter)
        inner = QVBoxLayout()
        inner.setSpacing(self.body.spacing())
        row.addLayout(inner, 1)
        self.body.addLayout(row)
        self.body = inner  # Card.add() now fills the right-hand column
        head = QHBoxLayout()
        title = QLabel(family["label"])
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        self.address = muted(f"TP2 0x{family['module']:02X}")
        self.address.setWordWrap(False)
        head.addWidget(self.address)
        head.addStretch(1)
        self.faults = Badge("", "error")
        self.faults.setCursor(Qt.CursorShape.PointingHandCursor)
        self.faults.mousePressEvent = lambda _e: self._open_faults()
        self.faults.hide()
        head.addWidget(self.faults)
        self.badge = Badge("", "idle")
        head.addWidget(self.badge)
        self.add(head)
        self.detail = self.add(muted(""))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(24)
        self.add(self.grid)
        row = QHBoxLayout()
        self.use = QPushButton("Select")
        self.use.setObjectName("primary")
        self.use.clicked.connect(lambda: page.choose(self.family, self.entry))
        self.identify = QPushButton("Re-read info")
        self.identify.clicked.connect(page.identify)
        self.recover = QPushButton("Recover…")
        self.recover.setObjectName("danger")
        self.recover.clicked.connect(lambda: page.recover(self.family))
        for button in (self.use, self.identify, self.recover):
            row.addWidget(button)
        row.addStretch(1)
        self.add(row)

    def status(self):
        return self.entry["status"] if self.entry else None

    def natural_height(self, width):
        """The height this card's content needs at this width (wrapped text counted)."""
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            return layout.totalHeightForWidth(width)
        return self.sizeHint().height()

    def hasHeightForWidth(self):
        # Connect sizes every card itself (ConnectPage._even_heights, from natural_height), so
        # the column must use that minimum height, not ask the card's layout directly (which
        # ignores it and made a grown card overlap the one below).
        return False

    def _open_faults(self):
        """Work on this module and show its codes on Diagnostics."""
        if self.page.state.busy:
            return
        self.page.choose(self.family, self.entry)
        self.page.state.open_tab.emit("diagnostics")

    def set_faults(self, counts):
        """Fault codes the last check found on this module (read this session)."""
        codes, active = counts or (0, 0)
        if codes:
            self.faults.set(f"⚠ {codes} fault code{'s' if codes != 1 else ''}"
                            + (f" · {active} still active" if active else ""), "error")
            self.faults.setToolTip("Work on this module and see its codes on Diagnostics.")
        self.faults.setVisible(bool(codes))

    def paint_art(self):
        from . import module_art
        address = ((self.entry or {}).get("workshop") or {}).get("address")
        picture = module_art.pixmap(self.family, address, ART_SIZE, self.art.devicePixelRatioF())
        if picture is not None:
            self.art.setPixmap(picture)
        self.art.setVisible(picture is not None)

    def show_entry(self, entry, selected, identity):
        self.entry = entry
        self.paint_art()
        status = self.status()
        text, level = SCAN_TEXT.get(status, SCAN_TEXT[None])
        if selected and identity:
            text, level = "Selected", "info"
        self.badge.set(text, level)
        # The selected card grows to show its full identity; it heads its column and only that
        # column moves down (ConnectPage keeps the other cards one size).
        found = (entry or {}).get("identity") or {}
        part = found.get("software_part_number") or found.get("part_number") or ""
        if selected and identity:
            detail = ""
        elif status == "ok" and self.family.get("generic") and not self.family.get("has_dtc"):
            detail = ((f"{part} {found.get('sw_version', '')}  ·  {found.get('system_desc', '')}  ·  "
                       if part else "") + "Doesn't report fault codes.")
        elif status == "ok" and not part:
            detail = "Answers, but didn't say what it is. Its fault codes still read."
        elif status == "ok":
            detail = f"{part} {found.get('sw_version', '')}  ·  {found.get('system_desc', '')}"
        elif status == "bootloader":
            detail = ("Something at this address answers but isn't running its application"
                      + (f" ({found.get('system_desc')})" if found.get("system_desc") else "")
                      + ". It needs a recovery flash.")
        elif status == "responding":
            detail = ("A controller opened a channel here but didn't identify. Usually that's a "
                      "loader waiting for a flash.")
        elif status == "unexpected":
            detail = (f"Identifies as {part} {found.get('sw_version', '')} "
                      f"({found.get('system_desc', '')}), not the {self.family['label']} module this app supports.")
        else:
            detail = "Selected manually. Press Read info to check it's there."
        workshop = (entry or {}).get("workshop")
        tp2 = f"TP2 0x{self.family['module']:02X}"
        self.address.setText(f"address {workshop['address_text']} · {tp2}" if workshop else tp2)
        if (entry or {}).get("in_gateway") is False and status not in (None, "absent"):
            detail = (detail + " " if detail else "") + "Not in the gateway's installation list."
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))

        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()
        if selected and identity:
            for row, (key, value) in enumerate(identity_rows(identity)):
                value_label = QLabel(value)
                value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.grid.addWidget(muted(key), row, 0)
                self.grid.addWidget(value_label, row, 1)

        stuck = status in ("bootloader", "responding")
        self.use.setVisible(not selected and status == "ok"
                            and not (self.family.get("generic") and not self.family.get("has_dtc")))
        self.identify.setText("Re-read info" if identity else "Read info")
        # Fault-code-only modules: their identity comes from the scan (no Re-read info).
        self.identify.setVisible(selected and not stuck and not self.family.get("generic"))
        self.recover.setVisible(stuck and self.family.get("can_flash", False))
        self.setProperty("selected", "true" if selected else "false")
        restyle(self)


class ConnectPage(Page):
    title = "Connect"
    # Natural sizes + scroll: fit-to-window under-counts the wrapped intro text and took the
    # shortfall out of the card columns, so a grown (selected) card overlapped the next one.
    fit_to_window = False

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.scan_results = {}
        self.cards = {}
        self.generic_cards = {}  # other gateway modules from the last scan ("addr:XX")

        find = self.add(Card())
        find.add(muted("Ignition on, engine off. Scan reads every module on the gateway's list: "
                       "what it is and its fault codes. It only reads; nothing is written."))
        row = QHBoxLayout()
        self.scan_button = QPushButton("Scan")
        self.scan_button.setObjectName("primary")
        self.scan_button.setToolTip("Like a VCDS Auto-Scan: opens every module the gateway lists and "
                                    "reads who it is and its fault codes. Read-only; about 1–2 s per "
                                    "module.")
        self.scan_button.clicked.connect(lambda: self.scan())
        row.addWidget(self.scan_button)
        self.pick_button = QToolButton()
        self.pick_button.setText("Select manually  ▾")
        self.pick_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.pick_menu = QMenu(self)
        self.pick_button.setMenu(self.pick_menu)
        row.addWidget(self.pick_button)
        self.adapter_text = muted("")
        row.addWidget(self.adapter_text, 1)
        find.add(row)
        self.adapter_help = find.add(Banner())
        self.bring_up = QPushButton("")
        self.bring_up.setToolTip("Bring the SocketCAN adapter up at 500 kbit/s (asks for your password).")
        self.bring_up.clicked.connect(self.bring_up_socketcan)
        self.bring_up.hide()
        row = QHBoxLayout()
        row.addWidget(self.bring_up)
        row.addStretch(1)
        find.add(row)
        self.bring_up_process = None
        self.scan_banner = find.add(Banner())
        self.faults_banner = find.add(Banner())
        state.faults_changed.connect(lambda _n: self._refresh_cards())
        from . import theme
        theme.on_apply(lambda: [c.paint_art() for c in {**self.cards, **self.generic_cards}.values()])

        # Module cards in two equal columns (user's choice, 2026-11-09): easy to read, and a
        # scan's ~17 cards fit without much scrolling.
        # Two independent columns (not a grid): when the selected card grows, only its own
        # column moves down.
        self.cards_box = QHBoxLayout()
        self.cards_box.setContentsMargins(0, 0, 0, 0)
        self.cards_box.setSpacing(10)
        self.card_columns = []
        for _ in range(CARD_COLUMNS):
            column = QVBoxLayout()
            column.setSpacing(10)
            self.cards_box.addLayout(column, 1)
            self.card_columns.append(column)
        holder = QWidget()
        holder.setLayout(self.cards_box)
        self.add(holder)
        self.absent = self.add(muted(""))
        self.others = self.add(muted(""))
        self.others.setWordWrap(True)
        self.gateway = None
        self.banner = self.add(Banner())
        self.next_hint = self.add(Banner())
        self.finish()

    # --- module cards ---

    def _build_cards(self):
        if self.cards or not self.state.families:
            return
        self.pick_menu.clear()
        for family in self.state.families:
            self.cards[family["name"]] = ModuleCard(family, self)
            self.pick_menu.addAction(f"{family['label']}  (0x{family['module']:02X})",
                                     lambda f=family: self.choose(f, self.scan_results.get(f["name"])))

    @staticmethod
    def generic_family(module):
        """A family-like dict for a gateway module the app has no family for: it can be
        selected to read and clear its fault codes, nothing else."""
        identity = dict(module.get("identity") or {})
        identity.update(connected=True, module=module["tp2"], controller_family=module["key"])
        report = module.get("dtc") or {}
        has_dtc = module.get("status") == "ok" and "dtc" in module and not report.get("error")
        return {"name": module["key"], "label": module.get("name") or f"Address {module['address']:02X}",
                "module": module["tp2"], "alias": f"0x{module['tp2']:02X}", "address": module["address"],
                "operations": ["dtc", "dtc_clear"] if has_dtc else [], "part_prefixes": [],
                "can_flash": False, "read_pageff": False, "can_backup": False,
                "can_check_preconditions": False, "can_log": False, "has_dtc": has_dtc,
                "generic": True, "scan_identity": identity}

    def _set_generic(self, result):
        """A card for every other gateway module the scan read (replacing the last scan's)."""
        modules = (result.get("gateway") or {}).get("modules")
        for name, card in list(self.generic_cards.items()):
            self.scan_results.pop(name, None)
            self._take(card)
            card.deleteLater()
        self.generic_cards = {}
        self.state.extra_families = {}
        for module in modules or ():
            if module.get("family") or module.get("address") is None:
                continue  # supported modules have their own cards
            family = self.generic_family(module)
            name = family["name"]
            self.state.extra_families[name] = family
            answered = module.get("status") == "ok"
            self.scan_results[name] = {
                "family": name, "module": module["tp2"], "status": "ok" if answered else "absent",
                "identity": family["scan_identity"] if answered else None, "in_gateway": True,
                "workshop": {"address": module["address"], "address_text": f"{module['address']:02X}"}}
            card = self.generic_cards[name] = ModuleCard(family, self)
            card.setEnabled(not self.state.busy)
            card.hide()  # placed by _refresh_cards

    def _refresh_cards(self):
        self._build_cards()
        selected = self.state.family["name"] if self.state.family else None
        shown = []
        for name, card in {**self.cards, **self.generic_cards}.items():
            entry = self.scan_results.get(name)
            if name == selected and self.state.identity:
                entry = dict(entry or {}, status="ok", identity=self.state.identity)
            status = entry["status"] if entry else None
            if status == "absent" and name != selected:
                status = "hidden"
            elif status == "unexpected" and any(c.family["module"] == card.family["module"]
                                                for c in self.generic_cards.values()):
                status = "hidden"  # the scan gave it a fault-code card of its own
            elif status is None and name != selected:
                status = "hidden"
            card.show_entry(entry, name == selected,
                            self.state.identity if name == selected else None)
            card.set_faults(self.state.fault_summary.get(name))
            if status != "hidden":
                faults = (self.state.fault_summary.get(name) or (0, 0))[0]
                address = ((entry or {}).get("workshop") or {}).get("address", 0x100)
                shown.append((STATUS_ORDER.get(status, 5), name != selected, not faults, address, card))
            card.setVisible(status != "hidden")
        # Selected first, then modules with fault codes, then by address (VCDS order).
        for column in self.card_columns:  # empty both columns (cards and the closing stretch)
            while column.count():
                column.takeAt(0)
        # Left, right, left, right…: the selected card is first (top-left). It grows to show
        # its details and pushes only its own column down.
        for index, (*_, card) in enumerate(sorted(shown, key=lambda item: item[:4])):
            self.card_columns[index % CARD_COLUMNS].addWidget(card)
        for column in self.card_columns:
            column.addStretch(1)
        QTimer.singleShot(0, self._even_heights)
        every = {**self.cards, **self.generic_cards}
        missing = [(every[n].family, e.get("workshop")) for n, e in self.scan_results.items()
                   if e["status"] == "absent" and n != selected and n in every]
        self.absent.setText("Not found: " + ", ".join(
            f"{f['label']} ({'address ' + w['address_text'] + ', ' if w else ''}TP2 0x{f['module']:02X})"
            for f, w in missing) if missing else "")
        self.absent.setVisible(bool(missing))
        rows = getattr(getattr(self.state, "faults", None), "rows", {}) or {}
        faulty = [(rows.get(n, {}).get("label") or (every[n].family["label"] if n in every else n), c)
                  for n, (c, _a) in self.state.fault_summary.items() if c]
        if faulty:
            self.faults_banner.show_message(
                f"Fault codes on {len(faulty)} module{'s' if len(faulty) != 1 else ''}: "
                + ", ".join(f"{label} ({count})" for label, count in faulty) + ".", "warning",
                actions={"See them on the Dashboard": lambda: self.state.open_tab.emit("home")})
        else:
            self.faults_banner.hide()
        self._show_gateway()

    def _show_gateway(self):
        """One line under the cards: how many modules the gateway lists and how many answered."""
        gateway = self.gateway
        if not gateway:
            self.others.hide()
            return
        if not gateway.get("available"):
            self.others.setText("No gateway installation list (no gateway answered, e.g. on a "
                                "bench), so only the supported modules were tried.")
            self.others.show()
            return
        modules = [m for m in gateway.get("modules") or ()
                   if not (m.get("coded") is False and m.get("status") != "ok")]
        answered = sum(1 for m in modules if m.get("status") == "ok")
        self.others.setText(f"{len(modules)} modules on the gateway list, {answered} answered. "
                            "Every module's scan log and fault codes are on Diagnostics.")
        self.others.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Text wraps differently at a new width: even the card heights again (once settled).
        if not hasattr(self, "_even_timer"):
            self._even_timer = QTimer(self, singleShot=True, interval=50)
            self._even_timer.timeout.connect(self._even_heights)
        self._even_timer.start()

    def _take(self, card):
        for column in self.card_columns:
            column.removeWidget(card)

    def _even_heights(self):
        """Every card as tall as the tallest, so the grid stays one size whatever each card shows.
        The selected card is left out: it grows to fit its details."""
        selected = self.state.family["name"] if self.state.family else None
        for card in self.cards.get(selected), self.generic_cards.get(selected):
            if card is not None:  # never squeezed: always room for its details
                card.setMinimumHeight(0)
                card.setMinimumHeight(card.natural_height(card.width()))
        cards = [c for c in {**self.cards, **self.generic_cards}.values()
                 if c.isVisible() and c.family["name"] != selected]
        for card in cards:
            card.setMinimumHeight(0)
        tallest = max((card.natural_height(card.width()) for card in cards), default=0)
        for card in cards:
            card.setMinimumHeight(tallest)
        # New minimums: make the columns and the page wrapper measure again, or the column
        # keeps its old height and the grown card overlaps the one below it.
        self.cards_box.invalidate()
        self.cards_box.parentWidget().updateGeometry()
        if self.widget() is not None:
            self.widget().updateGeometry()

    def choose(self, family, entry):
        identity = (entry or {}).get("identity") if (entry or {}).get("status") == "ok" else None
        self.banner.hide()
        self.state.select_module(family, identity)

    def recover(self, family):
        self.state.select_module(family)
        self.state.start_recovery.emit()

    def scan(self):
        """The scan: every module on the gateway's list, its identity and fault codes."""
        self.scan_banner.show_message("Reading every module on the gateway list…", "info")
        self.banner.hide()

        def done(result):
            entries = result.get("result") or []
            if result.get("error"):
                self.scan_banner.show_message("The scan stopped: " + str(result["error"]), "error")
                return
            self.scan_results = {e["family"]: e for e in entries}
            self.gateway = result.get("gateway")
            self._set_generic(result)
            car = (self.gateway or {}).get("vehicle") or {}
            self.state.set_vehicle(car.get("vin"), car.get("source", ""))
            found = [e for e in entries if e["status"] == "ok"]
            stuck = [e for e in entries if e["status"] in ("bootloader", "responding")]
            current = (self.state.family["name"] if self.state.family
                       else self.state.settings.get("module"))
            pick = next((e for e in found if e["family"] == current), None)
            if pick is None and len(found) == 1:
                pick = found[0]
            if pick:
                self.choose(self.state.family_by_name(pick["family"]), pick)
            elif current and current not in {e["family"] for e in found}:
                generic = self.state.extra_families.get(current)
                entry = self.scan_results.get(current) or {}
                if generic is not None and entry.get("status") == "ok":
                    self.choose(generic, entry)  # still there after this scan
                elif self.state.family and self.state.family.get("generic"):
                    self.state.select_module(None)  # it didn't answer this time
                else:
                    self.state.set_identity(None)
            if not entries or all(e["status"] == "absent" for e in entries):
                self.scan_banner.show_message(
                    "Nothing answered. Check the ignition is on and the adapter is plugged into "
                    "the car (or the bench harness is powered).", "warning")
            elif stuck:
                self.scan_banner.show_message(
                    "A module is waiting in its loader. Use Recover on its card to flash it back.",
                    "warning")
            elif len(found) > 1 and not pick:
                self.scan_banner.show_message("Found several modules. Choose one.", "info")
            else:
                self.scan_banner.hide()
            check = result.get("selftest")
            if check:  # demo's simulated car: did the real scan see what the car really has?
                if check["problems"]:
                    self.scan_banner.show_message(
                        f"Simulated car test: scan FAILED, {check['passed']}/{check['total']} checks "
                        f"as expected ({check['seconds']} s).\n• " + "\n• ".join(check["problems"]),
                        "error")
                else:
                    self.scan_banner.show_message(
                        f"Simulated car test: scan passed, all {check['total']} checks as expected "
                        f"({check['seconds']} s). The real scan code read the pretend car correctly.",
                        "ok")
            self._refresh_cards()
            self.state.last_scan = result
            self.state.modules_scanned.emit(result)

        def failed(error):
            self.scan_banner.show_message(self.error_text(error), "error")

        started = time.time()
        inner_done = done

        def done(result):  # noqa: F811  (wraps the handler above with when/how long)
            result["started"], result["elapsed"] = started, time.time() - started
            inner_done(result)

        self.state.run("scan", {"freeze_frames": bool(self.state.settings.get("scan_freeze_frames", True))},
                       done, failed)

    def refresh(self):
        adapter = self.state.adapter
        backends = self.state.hello.get("backends", {})
        self._refresh_cards()
        self.bring_up.hide()
        if not adapter:
            self.adapter_text.setText("No CAN interface found.")
            self.adapter_help.show_message(
                "Plug in your adapter; it shows up on its own within a few seconds.\n"
                "• J2534 (Windows): install the adapter's own driver.\n"
                "• CANable / candleLight (gs_usb): no driver needed on Windows 10/11. "
                "If it isn't listed, use Zadig to bind WinUSB to it once.\n"
                "• Linux SocketCAN: plug it in and it shows up as can0.", "info")
        else:
            self.adapter_text.setText(f"via {adapter.get('label', '')}")
            problem = adapter.get("problem")
            if adapter["adapter"] == "gs_usb" and not backends.get("gs_usb"):
                problem = "gs_usb support isn't installed. Re-run the installer."
            elif adapter["adapter"] == "socketcan" and adapter.get("up") is False:
                problem = (f"{adapter['channel']} is down. Bring it up at 500 kbit/s first "
                           "(you'll be asked for your password).")
                self.bring_up.setText(f"Bring up {adapter['channel']}")
                self.bring_up.show()
            elif adapter["adapter"] == "socketcan" and adapter.get("bitrate") not in (None, 500000):
                problem = (f"{adapter['channel']} is set to {adapter['bitrate'] // 1000} kbit/s. "
                           "The PQ35 powertrain/diagnostic CAN runs at 500 kbit/s.")
                self.bring_up.setText(f"Set {adapter['channel']} to 500 kbit/s")
                self.bring_up.show()
            elif adapter["adapter"] == "j2534" and not adapter.get("present", True):
                problem = f"The registered driver DLL is missing: {adapter.get('dll')}"
            self.adapter_help.show_message(problem or "", "warning")
        idle = not self.state.busy
        self.scan_button.setEnabled(bool(adapter) and idle)
        self.pick_button.setEnabled(idle)
        for card in {**self.cards, **self.generic_cards}.values():
            card.setEnabled(idle)
        self._next_hint()

    def bring_up_socketcan(self):
        channel = str((self.state.adapter or {}).get("channel", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", channel):
            return
        pkexec, ip = shutil.which("pkexec"), shutil.which("ip")
        if not (pkexec and ip):
            self.adapter_help.show_message(
                "Couldn't find pkexec to ask for your password. Run this in a terminal:\n"
                f"sudo ip link set {channel} down; "
                f"sudo ip link set {channel} up type can bitrate 500000", "warning")
            return
        script = (f"{ip} link set {channel} down && "
                  f"{ip} link set {channel} up type can bitrate 500000 restart-ms 100")
        self.bring_up.setEnabled(False)
        self.bring_up_process = QProcess(self)

        def finished(code, _status):
            self.bring_up.setEnabled(True)
            if code == 0:
                self.state.refresh_interfaces.emit()
            else:
                error = bytes(self.bring_up_process.readAllStandardError()).decode(errors="replace")
                self.adapter_help.show_message(
                    f"Couldn't bring up {channel}" + (f": {error.strip()}" if error.strip() else
                                                      " (cancelled?)."), "error")

        self.bring_up_process.finished.connect(finished)
        self.bring_up_process.start(pkexec, ["/bin/sh", "-c", script])

    def _next_hint(self):
        if not self.state.identity:
            self.next_hint.hide()
            return
        family = self.state.family
        if family["name"] == EPS:
            self.next_hint.show_message(
                "Ready. EPS racks can't be read back over OBD, so there's no backup step. "
                "Keep a copy of the firmware file the rack is on now.", "ok")
        elif family["name"] not in BACKUP_FAMILIES and not family.get("can_backup"):
            self.next_hint.hide()  # nothing to flash here, so no backup step to suggest
        elif self.state.record and self.state.record.has_backup():
            self.next_hint.show_message("Ready. You already have a backup of this module.", "ok")
        else:
            self.next_hint.show_message("Next: make a backup before flashing anything.", "info")

    def identify(self):
        self.banner.show_message("Talking to the module…", "info")

        def done(result):
            if result.get("error") or result.get("exit_code"):
                self.state.set_identity(None)
                self.banner.show_message(
                    "The module didn't answer. Check the ignition is on and the adapter is "
                    "plugged in, or press Scan.\n\n" + str(result.get("error") or ""),
                    "error")
                return
            self.banner.hide()
            self.state.set_identity(result.get("result") or {})

        def failed(error):
            self.state.set_identity(None)
            message = self.error_text(error)
            if "not " in message and "identified as" in message:
                message = ("That isn't the " + self.state.family["label"] + " module this app supports. "
                           "Press Scan to see what's there.\n\n" + message)
            self.banner.show_message(message, "error")

        self.state.run("identify", {}, done, failed)


class BackupPage(Page):
    title = "Back up"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        # Shown instead of everything else for modules that can't be read back.
        self.not_possible = self.add(Card("EPS racks can't be backed up"))
        self.not_possible.add(muted(
            "The rack's firmware can't be read back over OBD, so there's no backup step for "
            "EPS. You don't need one to flash it: the Flash page only allows writes that suit "
            "the firmware the rack reports."))
        self.not_possible.add(muted(
            "Instead, keep copies of what the rack runs now: the factory SGO or .bin for its "
            "part number and version, and its steer dataset. A factory full firmware carries "
            "the placeholder 000 dataset, so going back to stock means both."))
        self.not_possible.add(Banner()).show_message(
            "Nothing to do here for EPS. Go to Flash when you're ready.", "info")
        # BCM: no firmware backup here; its read-only page FF dump is on Diagnostics.
        self.bcm_note = self.add(Card("No firmware backup for the BCM here"))
        self.bcm_note.add(muted(
            "This app can't read the BCM's full firmware back. The read-only boot page FF "
            "dump is on the Diagnostics page."))
        card = self.add(Card("Read the module"))
        self.read_card = card
        card.hint("Copies the firmware off the module into your library. It's your way "
                  "back if a tune misbehaves, and new images are compared against it. "
                  "Takes about 7 minutes over OBD. Nothing is written.")
        row = QHBoxLayout()
        self.read_button = QPushButton("Read module")
        self.read_button.setObjectName("primary")
        self.read_button.clicked.connect(self.read)
        row.addWidget(self.read_button)
        self.open_button = QPushButton("Open module folder")
        self.open_button.clicked.connect(self.open_folder)
        row.addWidget(self.open_button)
        row.addStretch(1)
        card.add(row)
        self.progress = card.add(ProgressPanel())
        self.banner = card.add(Banner())
        history = self.add(Card("Backups of this module"))
        self.history_card = history
        self.list = history.add(QListWidget())
        self.list.setMinimumHeight(140)
        self.finish()
        state.progress.connect(self.on_progress)

    def refresh(self):
        record = self.state.record
        family = self.state.family["name"] if self.state.family else None
        possible = family is None or family in BACKUP_CAPABLE
        self.not_possible.setVisible(not possible)
        self.bcm_note.setVisible(False)
        self.read_card.setVisible(possible)
        self.history_card.setVisible(possible)
        if not possible:
            return
        self.read_button.setText("Back up BCM (boot page + coding)" if family == BCM
                                 else "Read module")
        self.read_button.setToolTip(
            "Saves the resident boot page and the BCM's identity/coding. The application "
            "firmware can't be read back, so this is not a full-image backup."
            if family == BCM else "")
        self.read_button.setEnabled(bool(record) and not self.state.busy)
        self.open_button.setEnabled(bool(record))
        self.list.clear()
        if not record:
            self.list.addItem("Select a module on Connect and read its info first.")
            return
        backups = record.backups()
        for entry in reversed(backups):
            self.list.addItem(f"{entry['time']}   {entry.get('status', '')}   "
                              f"{entry.get('sha256', '')[:16]}…")
        if not backups:
            self.list.addItem("No backups yet.")

    def on_progress(self, message):
        if self.progress.isVisibleTo(self) and self.state.busy:
            self.progress.update_from(message)

    def open_folder(self):
        if self.state.record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.state.record.root)))

    def read(self):
        record = self.state.record
        out = record.new_backup_dir()
        family = self.state.family["name"] if self.state.family else None
        if family == BCM:
            return self._backup_bcm(record, out)
        self.banner.show_message("Reading… keep the ignition on and don't unplug anything.", "info")
        self.progress.start("Connecting")

        def done(result):
            self.progress.stop()
            report = result.get("result") or {}
            status = report.get("status", "unknown")
            image = result.get("image")
            if image and Path(image).is_file():
                import hashlib
                sha = hashlib.sha256(Path(image).read_bytes()).hexdigest().upper()
                record.add({"kind": "backup", "image": image, "dir": str(out), "status": status,
                            "sha256": sha, "identity": self.state.identity})
                record.adopt_readout(image)
            level = {"captured_checksums_valid": "ok", "captured_complete": "ok"}.get(status, "warning")
            messages = {
                "captured_checksums_valid": "Backup saved. Checksums inside it are valid.",
                "captured_complete": "Backup saved.",
                "captured_partial": "Backup saved, but some areas couldn't be read. Flashing "
                                    "will play it safe and write the full range.",
                "checksum_mismatch": "Backup saved, but its checksums don't add up. Read again "
                                     "before relying on it.",
            }
            text = messages.get(status, f"Read finished with status {status}. "
                                        + str(report.get("error") or result.get("error") or ""))
            if not image:
                level, text = "error", "The read failed. " + str(report.get("error") or result.get("error") or "")
            self.banner.show_message(text, level)
            self.refresh()

        def failed(error):
            self.progress.stop()
            self.banner.show_message(self.error_text(error), "error")

        self.state.run("readout", {"out": str(out)}, done, failed)

    def _backup_bcm(self, record, out):
        """BCM snapshot: boot page FF + identity/coding bundle (read-only)."""
        self.banner.show_message("Backing up the BCM boot page and coding… keep the ignition on.", "info")
        self.progress.start("Connecting")

        def done(result):
            self.progress.stop()
            report = result.get("result") or {}
            status = report.get("status", "unknown")
            pageff = (report.get("components") or {}).get("pageff") or {}
            image = pageff.get("path")
            if image and Path(image).is_file():
                record.add({"kind": "backup", "image": image, "dir": result.get("backup_dir") or str(out),
                            "status": status, "sha256": (pageff.get("sha256") or "").upper(),
                            "scope": "boot_page+coding", "identity": self.state.identity})
            if image:
                self.banner.show_message(
                    "BCM backup saved: boot page and coding. (The application firmware can't be "
                    "read back, so this isn't a full-image backup.)",
                    "ok" if status == "ok" else "warning")
            else:
                self.banner.show_message(
                    "Backup failed. " + str(report.get("error") or result.get("error") or ""), "error")
            self.refresh()

        def failed(error):
            self.progress.stop()
            self.banner.show_message(self.error_text(error), "error")

        self.state.run("backup", {"out": str(out)}, done, failed)


class FlashPage(Page):
    title = "Flash"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.path = None
        self.loaded_for = None
        self.inspection = None
        self.plan = None
        self.gate = self.add(Banner())

        # Undo / redo for the image, its patches and the advanced options.
        # The "Before you flash" checklist is deliberately not part of it.
        from .undo import SnapshotHistory
        self.history = SnapshotHistory()
        self._restoring = False
        self._pending_option = None
        undo_bar = QWidget()
        undo_row = QHBoxLayout(undo_bar)
        undo_row.setContentsMargins(0, 0, 0, 0)
        undo_row.addStretch(1)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Undo the last change to the image, patches or options (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        undo_row.addWidget(self.undo_button)
        self.redo_button = QPushButton("Redo")
        self.redo_button.setToolTip("Redo (Ctrl+Shift+Z)")
        self.redo_button.clicked.connect(self.redo)
        undo_row.addWidget(self.redo_button)
        self.add(undo_bar)
        for keys, slot in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        # BCM only: a read-only check of the four gates a BCM flash's 10 85 needs
        # (supply voltage, attempt counter, two flags). Independent of the flash
        # gate below — run it any time a BCM is connected; nothing is written.
        self.precond = QWidget()
        precond_box = QVBoxLayout(self.precond)
        precond_box.setContentsMargins(0, 0, 0, 0)
        precond_row = QHBoxLayout()
        self.precond_button = QPushButton("Check programming conditions")
        self.precond_button.clicked.connect(self.check_preconditions)
        precond_row.addWidget(self.precond_button)
        precond_row.addStretch(1)
        precond_box.addLayout(precond_row)
        self.precond_banner = Banner()
        precond_box.addWidget(self.precond_banner)
        self.precond_note = muted("")
        precond_box.addWidget(self.precond_note)
        self.precond_note.hide()
        self.add(self.precond)
        self.precond.hide()

        file_card = self.add(Card("1. Choose the image"))
        self.drop = file_card.add(DropZone())
        self.drop.picked.connect(self.load)
        self.badges = file_card.add(FlowRow())
        self.file_banner = file_card.add(Banner())
        # EPS only: patch the chosen image in place. The patched copy is saved to
        # the Patched folder and flashed; the chosen file itself never changes.
        self.patch_source = None
        self.patch_info = None
        self.patch_row = QWidget()
        patch_box = QHBoxLayout(self.patch_row)
        patch_box.setContentsMargins(0, 0, 0, 0)
        self.patch_button = QPushButton("Patch…")
        self.patch_button.clicked.connect(self.open_patches)
        patch_box.addWidget(self.patch_button)
        self.patch_label = muted("")
        self.patch_label.setWordWrap(True)
        patch_box.addWidget(self.patch_label, 1)
        file_card.add(self.patch_row)
        self.patch_row.hide()

        # BCM only: the stock full-span image the module currently runs. The plan
        # diffs against it and writes just the 16 KiB sectors that differ.
        self.bcm_reference = None
        self.reference_card = self.add(Card("Stock reference image"))
        ref_row = QHBoxLayout()
        self.reference_button = QPushButton("Choose stock reference…")
        self.reference_button.clicked.connect(self._choose_reference)
        ref_row.addWidget(self.reference_button)
        self.reference_label = muted("No stock reference chosen")
        ref_row.addWidget(self.reference_label, 1)
        self.reference_card.add(ref_row)
        self.reference_card.add(muted(
            "The BCM writes only the 16 KiB sectors that differ from this stock image. "
            "Point it at the full-span .bin the BCM currently runs."))

        # BCM only: the separate RAM-driver .bin the loader stages as download #1
        # before the first application page. A verified driver is required; the
        # worker auto-discovers one beside the image, or it can be chosen here.
        self.bcm_driver = None
        self.driver_card = self.add(Card("RAM driver"))
        driver_row = QHBoxLayout()
        self.driver_button = QPushButton("Choose driver .bin…")
        self.driver_button.clicked.connect(self._choose_driver)
        driver_row.addWidget(self.driver_button)
        self.driver_label = muted("Looking for the driver beside the image…")
        driver_row.addWidget(self.driver_label, 1)
        self.driver_card.add(driver_row)
        self.driver_card.add(muted(
            "The BCM loader needs a separate 918-byte RAM driver (driver_ram_003800.bin) "
            "staged before any application page. Put it next to the image, or choose it here. "
            "Flashing is blocked until a verified driver is available."))

        self.range_card = self.add(Card("2. What to write"))
        self.reason = self.range_card.add(muted(""))
        self.plan_notes = self.range_card.add(Banner())
        self.options_box = QVBoxLayout()
        self.range_card.add(self.options_box)
        self.option_group = QButtonGroup(self)
        self.option_group.buttonToggled.connect(lambda *_: self.update_ready())
        self.option_group.buttonClicked.connect(lambda *_: self._schedule_commit())

        advanced = self.add(Card())
        self.advanced_toggle = QCheckBox("Show advanced options")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.advanced_toggle.toggled.connect(lambda *_: self._schedule_commit())
        advanced.add(self.advanced_toggle)
        self.advanced = QWidget()
        adv = QVBoxLayout(self.advanced)
        adv.setContentsMargins(22, 0, 0, 0)
        self.correct = QCheckBox("Fix wrong internal checksums (only for images you edited yourself)")
        self.simulator = QCheckBox("Bench simulator patches. BENCH ONLY: never put this in a car")
        self.recovery = QCheckBox("Recovery: the module is stuck in its bootloader and won't identify")
        self.no_backup = QCheckBox("Flash without a backup (not recommended)")
        self.crossflash = QCheckBox("Crossflash: allow an image for a different part number")
        for box in (self.correct, self.simulator, self.recovery, self.no_backup, self.crossflash):
            adv.addWidget(box)
            box.toggled.connect(self._options_changed)
            box.toggled.connect(lambda *_: self._schedule_commit())
        advanced.add(self.advanced)
        self.advanced.hide()

        checklist = self.add(Card("3. Before you flash"))
        self.checks = [QCheckBox(text) for text in (
            "Battery charger or a stable 13-14 V supply is connected",
            "Ignition on, engine off",
            "This computer is plugged in and won't be closed or unplugged")]
        for box in self.checks:
            checklist.add(box)
            box.toggled.connect(lambda *_: self.update_ready())

        go = self.add(Card("4. Flash"))
        self.summary = go.add(muted(""))
        self.flash_button = go.add(HoldButton("Hold to flash", seconds=3))
        self.flash_button.confirmed.connect(self.flash)
        self.progress = go.add(ProgressPanel())
        self.result_banner = go.add(Banner())
        self.cycle_button = QPushButton("I've cycled the ignition: check again")
        self.cycle_button.clicked.connect(self.check_after_cycle)
        self.cycle_button.hide()
        go.add(self.cycle_button)
        self.expected_version = None
        self.finish()
        self.history.reset(self._snapshot())
        self._update_undo_buttons()
        state.progress.connect(self.on_progress)
        state.open_in_flash.connect(self.load)
        state.open_patched_in_flash.connect(lambda path, source: self.load(path, patched_from=source))
        state.start_recovery.connect(self.begin_recovery)

    def ai_context(self):
        """What the Assistant sees of this page (None: no image chosen)."""
        if not self.inspection:
            return None
        ident = self.state.identity or {}
        lines = [f"Module: {(self.state.family or {}).get('label', '?')}, part on the car "
                 f"{self.state.part() or 'unknown'}, software {ident.get('sw_version', '?')}",
                 f"Image: {self.inspection.get('name')} ({self.inspection.get('size')} bytes)"]
        for check in self.inspection.get("checks") or []:
            lines.append(f"- check {check.get('key')}: {check.get('status', '')} {check.get('detail', '')}".strip())
        if self.patch_info:
            lines.append(f"Patched from {Path(self.patch_source).name}: {self.patch_info.get('summary', '')}")
        if self.plan:
            lines.append(f"Plan: {self.plan.get('reason', '')}")
            for note in self.plan.get("notes") or []:
                lines.append(f"- note: {note}")
            for option in self.plan.get("options") or []:
                lines.append(f"- option {option.get('label')}: {option.get('size')} bytes, "
                             f"{'valid' if option.get('valid') else 'not possible: ' + str(option.get('reason'))}")
        chosen = self.selected_option()
        if chosen:
            lines.append(f"Chosen: {chosen.get('label')}")
        flags = [name for name in self.FLAGS if getattr(self, name).isChecked()]
        lines.append("Advanced options on: " + (", ".join(flags) if flags else "none"))
        lines.append("Has a backup of this module: " + ("yes" if self.state.record and self.state.record.has_backup() else "no"))
        return {"title": "flash review", "context": "\n".join(lines), "question":
               "Review this flash before I do it. Does the image match the module, is the write plan "
               "sensible, and is there anything risky I should fix first?",
               "extra": "You are giving a second opinion only. The app runs its own safety checks; "
                        "never tell the user to bypass them."}

    # --- undo / redo ---

    FLAGS = ("correct", "simulator", "recovery", "no_backup", "crossflash")

    def _snapshot(self):
        checked = self.option_group.checkedButton()
        return {
            "path": str(self.path) if self.path else None,
            "patch_source": str(self.patch_source) if self.patch_source else None,
            "patch_info": self.patch_info,
            "advanced": self.advanced_toggle.isChecked(),
            "flags": {name: getattr(self, name).isChecked() for name in self.FLAGS},
            "option": (checked.property("option") or {}).get("key") if checked else None,
        }

    def _schedule_commit(self):
        if not self._restoring:
            QTimer.singleShot(0, self._commit)

    def _commit(self):
        if self._restoring:
            return
        self.history.commit(self._snapshot())
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        idle = not self.state.busy
        self.undo_button.setEnabled(idle and self.history.can_undo())
        self.redo_button.setEnabled(idle and self.history.can_redo())

    def undo(self):
        if not self.state.busy and self.history.can_undo():
            self._restore(self.history.undo())

    def redo(self):
        if not self.state.busy and self.history.can_redo():
            self._restore(self.history.redo())

    def _restore(self, snap):
        self._restoring = True
        try:
            self.advanced_toggle.blockSignals(True)
            self.advanced_toggle.setChecked(snap["advanced"])
            self.advanced.setVisible(snap["advanced"])
            self.advanced_toggle.blockSignals(False)
            for name in self.FLAGS:
                box = getattr(self, name)
                box.blockSignals(True)
                box.setChecked(snap["flags"][name])
                box.blockSignals(False)
            self._pending_option = snap["option"]
            same_file = snap["path"] == (str(self.path) if self.path else None) \
                and snap["patch_source"] == (str(self.patch_source) if self.patch_source else None)
            if snap["path"] is None:
                self._reset_file()
                self.refresh()
            elif same_file and self.plan:
                self._planned(self.plan)  # re-applies the option choice
            else:
                self.load(snap["path"], patched_from=snap["patch_source"], patch_info=snap["patch_info"])
        finally:
            self._restoring = False
        self._update_undo_buttons()

    # --- state ---

    def begin_recovery(self):
        """From a Recover button on the Connect page: preset recovery mode."""
        from . import security
        if not security.require(self, self.state.settings, "recovery"):
            return
        self._reset_file()
        self.advanced_toggle.setChecked(True)
        self.recovery.blockSignals(True)
        self.recovery.setChecked(True)
        self.recovery.blockSignals(False)
        self.result_banner.show_message(
            f"Recovery for the {self.state.family['label']}: choose the exact image made for this "
            "module. Recovery skips the checks that it's the module you think it is, so the "
            "wrong image bricks it.", "warning")
        self.refresh()

    def _toggle_advanced(self, on):
        self.advanced.setVisible(on)
        if not on:
            for box in (self.correct, self.simulator, self.recovery, self.no_backup,
                        self.crossflash):
                box.setChecked(False)
        if self.plan:
            self._planned(self.plan)

    def _options_changed(self, *_):
        if self.crossflash.isChecked() and self.sender() is self.crossflash:
            MessageBox.warning(self, "Crossflash",
                                "A crossflash puts firmware for a different part number on "
                                "this module, and it won't work as before without further "
                                "steps.\n\nOnly do this if you know exactly what your car needs.")
        if self.recovery.isChecked() and self.sender() is self.recovery:
            from . import security
            if not security.require(self, self.state.settings, "recovery"):
                self.recovery.blockSignals(True)
                self.recovery.setChecked(False)
                self.recovery.blockSignals(False)
                return
            MessageBox.warning(self, "Recovery mode",
                                "Recovery skips every check that the module is what you think it "
                                "is. Only use it when the module is stuck in its bootloader, and "
                                "only with the exact image made for it.\n\n"
                                "Flashing the wrong image in recovery mode bricks the module.")
        if self.path:
            self.load(self.path, patched_from=self.patch_source, patch_info=self.patch_info)
        else:
            self.refresh()

    def family_name(self):
        return self.state.family["name"] if self.state.family else None

    def _choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose stock reference image", "", "Firmware images (*.bin)")
        if not path:
            return
        self.bcm_reference = path
        self.reference_label.setText(Path(path).name)
        if self.path:
            # Re-plan against the new reference.
            self.load(self.path)

    def _choose_driver(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose BCM RAM driver", "", "Driver images (*.bin)")
        if not path:
            return
        self.bcm_driver = path
        if self.path:
            # Re-inspect so the chosen driver is verified and the badge updates.
            self.load(self.path)
        else:
            self.driver_label.setText(Path(path).name)

    def _set_driver_status(self):
        """Reflect the inspection's driver check on the Flash page."""
        info = (self.inspection or {}).get("driver") or {}
        if info.get("found"):
            self.driver_label.setText(f"Verified: {Path(info['path']).name}")
        elif info.get("explicit"):
            self.driver_label.setText(info.get("error") or "Driver file failed verification.")
        elif self.inspection is not None:
            self.driver_label.setText("No verified driver found — choose the driver .bin.")
        else:
            self.driver_label.setText("Looking for the driver beside the image…")

    def refresh(self):
        family = self.state.family
        if self.path and self.loaded_for != (self.family_name(), self.state.part()):
            self._reset_file()
        if hasattr(self, "history"):
            self._update_undo_buttons()
        self.simulator.setVisible(self.family_name() == HALDEX)
        self.no_backup.setVisible(self.family_name() in BACKUP_FAMILIES)
        self.reference_card.setVisible(self.family_name() == BCM)
        self.driver_card.setVisible(self.family_name() == BCM)
        patchable = (self.family_name() == EPS and bool(self.inspection)
                     and self.inspection.get("guessed_family") == EPS)
        self.patch_row.setVisible(patchable)
        self.patch_button.setEnabled(patchable and not self.state.busy)
        can_precond = (self.family_name() == BCM and self.state.family
                       and self.state.family.get("can_check_preconditions"))
        self.precond.setVisible(bool(can_precond))
        self.precond_button.setEnabled(bool(can_precond) and not self.state.busy)
        problem = None
        if not family:
            problem = "Choose a module at the top."
        elif not family.get("can_flash"):
            problem = f"Flashing isn't supported for the {family['label']}."
        elif not self.state.identity and not self.recovery.isChecked():
            problem = "Read the module's info on the Connect page first (Read info)."
        elif (self.family_name() in BACKUP_FAMILIES and self.state.record
              and not self.state.record.has_backup()
              and not self.no_backup.isChecked() and not self.recovery.isChecked()):
            problem = "Make a backup on the Back up page first."
        self.gate.show_message(problem or "", "warning")
        self.update_ready()

    def selected_option(self):
        button = self.option_group.checkedButton()
        return button.property("option") if button else None

    def blocking_reason(self):
        if self.state.busy:
            return "Working…"
        if self.gate.active:
            return self.gate.text()
        if not self.inspection:
            return "Choose an image."
        allowed = {"checksums": self.correct.isChecked(), "crossflash": self.crossflash.isChecked()}
        errors = [c for c in self.inspection["checks"]
                  if c["level"] == "error" and not allowed.get(c["key"], False)]
        if errors:
            driver_error = next((c for c in errors if c["key"] == "driver"), None)
            if driver_error:
                return (driver_error.get("detail")
                        or "The BCM RAM driver is missing or failed verification. Choose a "
                           "verified driver .bin before flashing.")
            if any(c["key"] == "crossflash" for c in self.inspection["checks"]):
                return ("This image is for a different part number (a crossflash). Allow it under "
                        "advanced options if that's what you want.")
            return "This image can't be flashed. See the red badges."
        option = self.selected_option()
        if not option:
            return self.plan["reason"] if self.plan and not self.plan.get("recommended") else "Choose what to write."
        if not all(box.isChecked() for box in self.checks):
            return "Tick every item in the checklist."
        return None

    def update_ready(self):
        reason = self.blocking_reason()
        self.flash_button.setEnabled(reason is None)
        option = self.selected_option()
        if reason:
            self.summary.setText(reason)
        else:
            self.summary.setText(
                f"Writes {option['label']} (0x{option['start']:05X}-0x{option['end']:05X}, "
                f"{kib(option['size'])}) from {Path(self.path).name}.")

    # --- programming conditions (BCM, read-only) ---

    def check_preconditions(self):
        self.precond_button.setEnabled(False)
        self.precond_note.hide()
        self.precond_banner.show_message(
            "Checking programming conditions… nothing is written to the module.", "info")

        def done(result):
            self.precond_button.setEnabled(not self.state.busy)
            report = result.get("result") or {}
            if result.get("error") or not report:
                self.precond_banner.show_message(
                    "Couldn't read the programming conditions. "
                    + str(result.get("error") or ""), "error")
                self.precond_note.hide()
                return
            if report.get("all_ok"):
                self.precond_banner.show_message(
                    f"Programming conditions OK — supply {report.get('supply_volts')} V.", "ok")
                self.precond_note.hide()
            else:
                blocking = report.get("blocking") or ["a programming condition isn't met"]
                self.precond_banner.show_message("Not ready: " + "; ".join(blocking), "warning")
                note = report.get("scaling_note")
                self.precond_note.setText(note or "")
                self.precond_note.setVisible(bool(note))

        def failed(error):
            self.precond_button.setEnabled(not self.state.busy)
            self.precond_note.hide()
            self.precond_banner.show_message(self.error_text(error), "error")

        self.state.run("preconditions", {}, done, failed)

    # --- image ---

    def _reset_file(self):
        self.path = self.inspection = self.plan = None
        self.patch_source = self.patch_info = None
        self.loaded_for = None
        self.drop.set_file("Drop a .bin file here, or click to choose", "")
        self.badges.clear()
        self.file_banner.hide()
        self.patch_label.setText("")
        self._clear_options()

    def load(self, path, converted=False, patched_from=None, patch_info=None):
        """Check ``path`` and plan its flash. ``patched_from`` is the image it
        was patched from (the plan diffs against it to pick the blocks)."""
        if self.state.busy:
            return
        if str(path).lower().endswith(".sgo"):
            self.file_banner.show_message("")
            convert_sgo_then(self, path, self.family_name(), self.file_banner,
                             lambda out: self.load(out, converted=True))
            return
        self.converted = converted
        if not converted:
            self.file_banner.show_message("")
        self.patch_source = patched_from
        self.patch_info = patch_info
        if patched_from:
            text = f"Patched copy of {Path(patched_from).name}"
            if patch_info and patch_info.get("summary"):
                text += f": {patch_info['summary']}"
            self.patch_label.setText(text + f". Saved as {Path(path).name}.")
        else:
            self.patch_label.setText("Apply patches to this image before flashing it.")
        self.path = path
        self.loaded_for = (self.family_name(), self.state.part())
        self.inspection = self.plan = None
        self.result_banner.hide()
        self.cycle_button.hide()
        self.drop.set_file(Path(path).name, "Checking…")
        self.badges.clear()
        self._clear_options()
        family = self.family_name()
        if not family:
            self.file_banner.show_message("Choose a module at the top first.", "warning")
            return
        self._schedule_commit()
        self.state.offline("inspect_image", {"path": path, "family": family,
                                             "ecu_part": self.state.part() or None,
                                             "ecu_version": (self.state.identity or {}).get("sw_version"),
                                             "driver": self.bcm_driver if family == BCM else None},
                           self._inspected, lambda e: self._file_error(e))

    def _file_error(self, error):
        self.drop.set_file(Path(self.path).name if self.path else "", "")
        self.file_banner.show_message(self.error_text(error), "error")
        self.refresh()

    def _inspected(self, result):
        self.inspection = result
        self._set_driver_status()
        self.drop.set_file(result["name"], f"{kib(result['size'])} · SHA-256 {result['sha256']}")
        badges = [(c["label"], c["level"], c.get("detail")) for c in result["checks"]]
        self.badges.set_badges(badges)
        errors = [c for c in result["checks"] if c["level"] == "error"]
        warnings = [c for c in result["checks"] if c["level"] == "warning"]
        if errors:
            text = "\n".join(c["label"] + (": " + c["detail"] if c["detail"] else "") for c in errors)
            if result.get("correctable") and not self.correct.isChecked():
                text += ("\n\nIf you edited this image yourself, turn on \"Fix wrong internal "
                         "checksums\" under advanced options. Otherwise the file is damaged or "
                         "incomplete: get a fresh copy.")
            self.file_banner.show_message(text, "error")
            allowable = {"crossflash"} | ({"checksums"} if result.get("correctable") else set())
            if not all(c["key"] in allowable for c in errors):
                self.refresh()
                return
        elif warnings:
            self.file_banner.show_message("\n".join(c["detail"] for c in warnings), "warning")
        elif not getattr(self, "converted", False):
            self.file_banner.show_message("")
        reference = None
        if self.family_name() == BCM:
            reference = self.bcm_reference
        elif self.state.record and self.family_name() in BACKUP_FAMILIES:
            reference = self.state.record.state()
        self.state.offline("plan_flash", {
            "path": self.path, "family": self.family_name(),
            "reference": str(reference) if reference else None,
            "ecu_version": (self.state.identity or {}).get("sw_version"),
            "correct_checksum": self.correct.isChecked(),
            "simulator_mode": self.simulator.isChecked(),
            "patched_from": self.patch_source if self.family_name() == EPS else None,
            "recovery": self.recovery.isChecked(),
        }, self._planned, self._file_error)

    # --- patching (EPS) ---

    def open_patches(self):
        """Patch the image on this page. Patches always go onto the original
        file, so choosing again replaces the earlier choice instead of stacking."""
        if self.state.busy or not self.path:
            return
        from . import security
        if not security.require(self, self.state.settings, "patch"):
            return
        from .disclaimer import confirm_patching
        if not confirm_patching(self):
            return
        source = self.patch_source or self.path
        self.state.offline("eps_patch_options", {"path": source, "current": self.path},
                           lambda options: self._choose_patches(source, options),
                           lambda e: self.file_banner.show_message(self.error_text(e), "error"))

    def _choose_patches(self, source, options):
        from .patch_dialog import PatchDialog
        dialog = PatchDialog(options, self)
        if dialog.exec() != PatchDialog.DialogCode.Accepted:
            return
        selection = dialog.selection()
        if PatchDialog.empty(selection):
            if self.patch_source:
                self.file_banner.show_message(f"No patches chosen: back to {Path(source).name}.", "info")
                self.load(source)
            return
        summary = PatchDialog.describe(selection)
        stem = f"{Path(source).stem}_patched_{timestamp()}"
        out = patched_dir() / f"{stem}{Path(source).suffix}"
        count = 1
        while out.exists():  # never overwrite an earlier patched copy
            count += 1
            out = patched_dir() / f"{stem}_{count}{Path(source).suffix}"

        def done(result):
            result["summary"] = summary
            self.load(result["out"], patched_from=source, patch_info=result)

        self.state.offline("eps_apply_patches", {"path": source, "out": str(out), **selection}, done,
                           lambda e: self.file_banner.show_message(
                               "Not patched: " + self.error_text(e), "error"))

    def _clear_options(self):
        for button in self.option_group.buttons():
            self.option_group.removeButton(button)
            button.deleteLater()
        while self.options_box.count():
            item = self.options_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.reason.setText("")
        self.plan_notes.show_message("")

    def _planned(self, plan):
        self.plan = plan
        self._clear_options()
        self.reason.setText(plan["reason"])
        self.plan_notes.show_message("\n\n".join(plan.get("notes") or []), "warning")
        hidden = 0
        for option in plan["options"]:
            if (plan["family"] == HALDEX and option["key"] != "full"
                    and not self.advanced_toggle.isChecked()):
                hidden += 1
                continue
            text = f"{option['label']}   ·   {kib(option['size'])}"
            if option["key"] == plan.get("recommended"):
                text += "   ·   recommended"
            button = QRadioButton(text)
            button.setObjectName("option")
            button.setProperty("option", option)
            button.setToolTip(option.get("hint", ""))
            if not option["valid"]:
                button.setEnabled(False)
                button.setToolTip(option.get("reason", ""))
            self.option_group.addButton(button)
            self.options_box.addWidget(button)
            hint = muted(option.get("hint", "") if option["valid"] else "Not possible: " + option.get("reason", ""))
            hint.setContentsMargins(28, 0, 0, 4)
            self.options_box.addWidget(hint)
            if option["key"] == plan.get("recommended"):
                button.setChecked(True)
        if hidden:
            self.options_box.addWidget(muted(f"{hidden} smaller range(s) under advanced options."))
        if self._pending_option:
            for button in self.option_group.buttons():
                if button.isEnabled() and (button.property("option") or {}).get("key") == self._pending_option:
                    button.setChecked(True)
            self._pending_option = None
        self.refresh()

    # --- flashing ---

    def flash(self):
        if self.blocking_reason():
            return
        from . import security
        if not security.require(self, self.state.settings, "flash"):
            return
        option = self.selected_option()
        crossing = next((c for c in self.inspection["checks"] if c["key"] == "crossflash"), None)
        if crossing:
            text, ok = InputDialog.getText(
                self, "Confirm crossflash",
                f"{crossing['label']}.\nType CROSSFLASH to write it anyway:")
            if not ok or text.strip() != "CROSSFLASH":
                return
        if self.recovery.isChecked():
            text, ok = InputDialog.getText(
                self, "Confirm recovery flash",
                f"Recovery mode can't check the module.\nType RECOVER to flash "
                f"{Path(self.path).name} to the {self.state.family['label']}:")
            if not ok or text.strip() != "RECOVER":
                return
        params = {"input": self.path, "start": option["start"], "end": option["end"],
                  "correct_checksum": self.correct.isChecked(),
                  "simulator_mode": self.simulator.isChecked(),
                  "recovery": self.recovery.isChecked()}
        if option.get("sectors"):
            # BCM flashes by 16 KiB sector; the worker builds --bcm-sector args.
            params["sectors"] = list(option["sectors"])
        if self.family_name() == BCM:
            # Thread the verified RAM driver through to --bcm-driver. The flash
            # is gated on a found driver (blocking_reason), so this is present.
            driver = (self.inspection.get("driver") or {}).get("path")
            if driver:
                params["driver"] = driver
        self.result_banner.show_message("Flashing. Don't touch the car, the cable or this computer.", "warning")
        self.cycle_button.hide()
        self.progress.start("Connecting")
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))
        self.flashed_plan = dict(self.plan, flashed=option["key"])
        self.state.run("flash", params, lambda r: self._flashed(r, params, option), self._flash_failed)

    def on_progress(self, message):
        if self.progress.isVisibleTo(self) and self.state.busy:
            self.progress.update_from(message)

    def _flash_failed(self, error):
        # The worker couldn't run the flash at all; still look at the module.
        self._reidentify("failed", "The flash didn't run: " + self.error_text(error))

    def _flashed(self, result, params, option):
        report = result.get("result") or {}
        record = self.state.record
        entry = {"kind": "flash", "image": self.path, "sha256": self.inspection["sha256"],
                 "start": option["start"], "end": option["end"], "status": report.get("status"),
                 "boot_verified": report.get("boot_verified"), "recovery": params["recovery"],
                 "error": result.get("error")}
        if self.patch_source:
            entry["patched_from"] = self.patch_source
            entry["patches"] = {k: (self.patch_info or {}).get(k)
                                for k in ("features", "gains", "torque_trip", "summary")}
        if record and report.get("checksum_verified"):
            try:
                entry["kept_copy"] = record.keep_flashed(self.path)
            except OSError:
                pass
        if record:
            record.add(entry)
        if report.get("boot_verified") and report.get("commit_outcome") == "ambiguous":
            # Written, checksum-verified and seen running, but the ECU didn't
            # acknowledge every commit service (often it restarts before
            # answering StopCommunication). Not a bootloader case.
            if record:
                record.forget_state()
            unanswered = [f"0x{k.upper()}" for k, v in (report.get("commit_responses") or {}).items()
                          if v != "acknowledged"]
            self._reidentify("ambiguous", "Flashed and checksum-verified. The module didn't "
                             f"acknowledge the last commit step ({', '.join(unanswered) or 'commit'}), "
                             "which the ignition cycle below settles.")
            return
        if result.get("error") or report.get("status") != "ok":
            if record:
                record.forget_state()
            self.failed_needs_recovery = report.get("recovery_required", True)
            self._reidentify("failed", "Flash failed: " + str(
                result.get("error") or report.get("error") or report.get("status")))
            return
        if record and self.family_name() in BACKUP_FAMILIES:
            reference = record.state()
            self.state.offline("compose_state", {
                **{k: params[k] for k in ("correct_checksum", "simulator_mode")},
                "path": self.path, "family": self.family_name(), "start": option["start"],
                "end": option["end"], "reference": str(reference) if reference else None,
                "out": str(record.state_path) + ".new"},
                lambda r: self._state_written(r, record), lambda e: record.forget_state())
        self._reidentify("ok", "Flashed and verified.")

    def _state_written(self, result, record):
        new = Path(str(record.state_path) + ".new")
        if result.get("written") and new.is_file():
            new.replace(record.state_path)
        else:
            record.forget_state()

    # --- after a flash: identify, then confirm across an ignition cycle ---

    def _reidentify(self, outcome, headline):
        """The worker has released the adapter; ask the module what it is now.
        This also updates the Connect page and the status pill."""
        for box in self.checks:  # another flash needs a fresh confirmation
            box.setChecked(False)
        self.progress.start("Checking the module")
        self.state.run("identify", {},
                       lambda r: self._after_identify(outcome, headline, r),
                       lambda e: self._after_identify(outcome, headline,
                                                      {"error": self.error_text(e)}))

    @staticmethod
    def _identity_from(result):
        identity = result.get("result") or {}
        if result.get("error") or result.get("exit_code") or not identity:
            return None
        return identity

    def _after_identify(self, outcome, headline, result):
        self.progress.stop()
        identity = self._identity_from(result)
        self.state.set_identity(identity)
        running = identity and not identity.get("in_bootloader")
        if outcome in ("ok", "ambiguous") and running:
            self.expected_version = identity.get("sw_version", "")
            self.result_banner.show_message(
                f"{headline} It's running {self.state.part()} {self.expected_version} "
                f"({identity.get('system_desc', '')}).\n\n"
                "Last step: turn the ignition off, wait about 10 seconds, turn it back on (or "
                "start the engine), then press Check again.",
                "ok" if outcome == "ok" else "warning")
            self.cycle_button.show()
            self._dataset_prompt()
        elif outcome in ("ok", "ambiguous"):
            self.expected_version = None
            self.result_banner.show_message(
                f"{headline} But the module didn't identify straight afterwards.\n\n"
                "Turn the ignition off, wait about 10 seconds, turn it back on, then press "
                "Check again.", "warning")
            self.cycle_button.show()
        elif running:
            self.result_banner.show_message(
                f"{headline}\n\nThe module still runs its application "
                f"({self.state.part()} {identity.get('sw_version', '')}), so nothing was lost.",
                "error")
        else:
            advice = ("\n\nThe module is probably waiting in its bootloader. That's recoverable: "
                      "A scan on the Connect page shows it with a Recover button, or turn "
                      "on Recovery under advanced options and flash the same image again."
                      if getattr(self, "failed_needs_recovery", True) else "\n\nNothing was erased.")
            self.result_banner.show_message(headline + advice, "error")

    def check_after_cycle(self):
        self.cycle_button.setEnabled(False)
        self.progress.start("Checking after the ignition cycle")
        self.state.run("identify", {}, self._after_cycle,
                       lambda e: self._after_cycle({"error": self.error_text(e)}))

    def _after_cycle(self, result):
        self.progress.stop()
        self.cycle_button.setEnabled(True)
        identity = self._identity_from(result)
        if identity is None:
            self.result_banner.show_message(
                "The module didn't answer after the ignition cycle. Check the ignition is on, "
                "then press Check again. If it still doesn't answer, a scan on the Connect "
                "page shows whether it's waiting in its bootloader.", "warning")
            return
        self.state.set_identity(identity)
        version = identity.get("sw_version", "")
        if identity.get("in_bootloader"):
            self.result_banner.show_message(
                "After the ignition cycle the module came up in its bootloader. Flash it again "
                "with Recovery (Connect → Scan → Recover).", "error")
        elif self.expected_version and version != self.expected_version:
            self.result_banner.show_message(
                f"After the ignition cycle it reports {version}, not {self.expected_version}. "
                "Check what's on it before driving.", "warning")
        else:
            self.cycle_button.hide()
            if self.state.record:
                self.state.record.add({"kind": "verify", "sw_version": version,
                                       "identity": identity})
            self.result_banner.show_message(
                f"Confirmed after an ignition cycle: {self.state.part()} {version} "
                f"({identity.get('system_desc', '')}). You're done: go ahead and drive. Flashing "
                "doesn't set fault codes, and any faults left from the module being offline "
                "clear themselves once it's running normally.", "ok")

    def _dataset_prompt(self):
        plan = getattr(self, "flashed_plan", None) or {}
        if not (plan.get("needs_dataset") and plan.get("flashed") == "full"):
            return
        self.result_banner.show_message(
            self.result_banner.text() + "\n\nThis firmware came with the placeholder 000 "
            "dataset. Flash a real steer dataset next.", "warning")
        answer = MessageBox.question(
            self, "Flash a steer dataset",
            "The firmware you flashed carries the factory placeholder dataset (000), so the "
            "rack has no real steer dataset yet.\n\nChoose a dataset to flash now? Use a "
            "4 KiB dataset file or SGO, or a full image (its 0x5E block will be used).")
        if answer == MessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getOpenFileName(self, "Choose steer dataset",
                                                  self.drop.start_dir,
                                                  "Firmware (*.bin *.sgo);;All files (*)")
            if path:
                self.load(path)


class PatchesPage(Page):
    title = "Patches"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.path = None
        self.intro = self.add(muted(""))
        card = self.add(Card("Image"))
        self.drop = card.add(DropZone())
        self.drop.picked.connect(self.load)
        self.banner = card.add(Banner())
        self.results = self.add(Card("Result"))
        self.tree = self.results.add(QTreeWidget())
        self.tree.setHeaderLabels(["Item", "Value"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setMinimumHeight(160)
        self.actions = QHBoxLayout()
        self.results.add(self.actions)
        self.finish()

    def refresh(self):
        family = self.state.family["name"] if self.state.family else None
        self.intro.setText({
            EPS: "Check which known patches and HCA gains an EPS image has, and apply them. "
                 "Patching writes a new file; your original is never changed. The Flash page "
                 "can also patch the image you're about to flash.",
            HALDEX: "Check the Layer-1 checksums of a Haldex image. The anti-brick patches are "
                    "added automatically when you flash, so there's nothing to apply here.",
        }.get(family, "No offline tools for this module yet."))
        self.setEnabled(family in (EPS, HALDEX) and not self.state.busy)

    def _clear_actions(self):
        while self.actions.count():
            item = self.actions.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def load(self, path):
        if str(path).lower().endswith(".sgo"):
            family = self.state.family["name"] if self.state.family else None
            convert_sgo_then(self, path, family, self.banner, self.load)
            return
        self.path = path
        self.tree.clear()
        self._clear_actions()
        self.banner.hide()
        self.drop.set_file(Path(path).name, str(Path(path).parent))
        family = self.state.family["name"]
        if family == EPS:
            self.state.offline("eps_patch_options", {"path": path}, self._eps_status,
                               lambda e: self.banner.show_message(self.error_text(e), "error"))
        else:
            self.state.offline("haldex_checksums", {"path": path}, self._haldex,
                               lambda e: self.banner.show_message(self.error_text(e), "error"))

    def _haldex(self, rows):
        bad = 0
        for row in rows:
            ok = row.get("valid")
            bad += ok is False
            name = f"Block 0x{row['start']:05X}-0x{row['end_exclusive'] - 1:05X}"
            if ok is None:
                value = "not checked"
            elif ok:
                value = f"valid (0x{row['stored']:04X})"
            else:
                value = f"WRONG: stored 0x{row['stored']:04X}, should be 0x{row['computed']:04X}"
            QTreeWidgetItem(self.tree, [name, value])
        self.banner.show_message("All block checksums are valid." if not bad else
                                 f"{bad} block checksum(s) are wrong. If you edited the image, the "
                                 "flasher can fix them (advanced options on the Flash page).",
                                 "ok" if not bad else "warning")

    def _eps_status(self, result):
        from .patch_dialog import GAIN_PRESETS
        self.options = result
        known = {f["manifest"]: f["status"] for f in result["features"]
                 if f["manifest"] and f["feature"] not in GAIN_PRESETS}
        if not known and not result.get("gains") and result.get("torque_trip") is None:
            self.banner.show_message("No known patch fits this image.", "info")
            return
        for name, status in known.items():
            QTreeWidgetItem(self.tree, [name, status])
        speeds = result.get("gain_speeds") or []
        for mode, row in enumerate(result.get("gains") or []):
            QTreeWidgetItem(self.tree, [f"HCA gain mode {mode} ({'/'.join(map(str, speeds))} km/h)",
                                        " / ".join(map(str, row))])
        if result.get("torque_trip") is not None:
            QTreeWidgetItem(self.tree, ["HCA torque trip (0x5D09C)", f"{result['torque_trip']:.2f} Nm"])
        button = QPushButton("Choose patches…")
        button.setObjectName("primary")
        button.clicked.connect(self.choose)
        self.actions.addWidget(button)
        self.actions.addStretch(1)

    def choose(self):
        from .disclaimer import confirm_patching
        from .patch_dialog import PatchDialog
        if not confirm_patching(self):
            return
        dialog = PatchDialog(self.options, self)
        if dialog.exec() != PatchDialog.DialogCode.Accepted:
            return
        selection = dialog.selection()
        if PatchDialog.empty(selection):
            self.banner.show_message("No patches chosen.", "info")
            return
        source = Path(self.path)
        default = str(patched_dir() / f"{source.stem}_patched{source.suffix}")
        out, _ = QFileDialog.getSaveFileName(self, "Save patched image", default, "Firmware (*.bin)")
        if not out:
            return
        if Path(out).resolve() == source.resolve():
            self.banner.show_message("Save the patched image as a new file.", "error")
            return
        summary = PatchDialog.describe(selection)

        def done(result):
            self.banner.show_message(f"Saved {Path(result['out']).name}: {summary}.", "ok")
            self._clear_actions()
            button = QPushButton("Flash this file")
            button.setObjectName("primary")
            button.clicked.connect(
                lambda: self.state.open_patched_in_flash.emit(result["out"], str(source)))
            self.actions.addWidget(button)
            self.actions.addStretch(1)

        self.state.offline("eps_apply_patches", {"path": self.path, "out": out, **selection}, done,
                           lambda e: self.banner.show_message(self.error_text(e), "error"))


DIAGNOSTICS = (
    ("faults", "Stored faults", "Fault slots with their freeze-frame snapshots."),
    ("supply", "Supply voltage", "Battery and motor supply measuring blocks."),
    ("assist", "Assist", "Assist and torque measuring blocks."),
    ("motion", "Motion", "Steering angle and motion blocks."),
    ("can_config", "CAN configuration", "What the rack thinks is on the bus."),
    ("eeprom", "Serial EEPROM", "Saves the 1 KiB EEPROM to a file."),
)


class DiagnosticsPage(Page):
    title = "Diagnostics"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        # Every module's faults (sidebar) and the scan log beside it (diagnostics_view); below,
        # the chosen module's own tests, only for the modules that have some (EPS, BCM).
        from .health import controller
        from .diagnostics_view import AutoScanView
        self.health_card = controller(state)
        self.view = AutoScanView(state)
        self.layout_.addWidget(self.view, 1)
        card = self.add(Card("Module tests"))
        self.read_card = card
        self.intro = card.add(muted(""))
        grid = QGridLayout()
        self.buttons = {}
        for index, (op, label, hint) in enumerate(DIAGNOSTICS):
            button = QPushButton(label)
            button.setToolTip(hint)
            button.clicked.connect(lambda _=False, o=op: self.run(o))
            grid.addWidget(button, index // 3, index % 3)
            self.buttons[op] = button
        card.add(grid)
        row = QHBoxLayout()
        row.addWidget(muted("Samples"))
        self.samples = QSpinBox()
        self.samples.setRange(1, 200)
        row.addWidget(self.samples)
        row.addStretch(1)
        card.add(row)
        self.banner = card.add(Banner())
        self._build_pageff()
        out = self.add(Card("Result"))
        self.result_card = out
        self.tree = out.add(QTreeWidget())
        self.tree.setHeaderLabels(["Field", "Value"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setMinimumHeight(150)
        state.progress.connect(self.on_progress)

    def _build_pageff(self):
        # BCM only. A memory read, nothing like the Flash page: no erase, no write.
        card = self.add(Card("BCM boot page FF (read-only)"))
        self.pageff_card = card
        card.add(muted("Reads the 16 KiB boot/vector page (0x7FC000–0x7FFFFF) off the BCM over "
                       "TP2.0 and saves it with a JSON sidecar in the module's folder. Nothing is "
                       "written to the module. About 256 small reads; keep the ignition on."))
        row = QHBoxLayout()
        self.pageff_button = QPushButton("Dump BCM page FF (read-only, safe)")
        self.pageff_button.clicked.connect(self.dump_pageff)
        row.addWidget(self.pageff_button)
        self.pageff_open = QPushButton("Open dump")
        self.pageff_open.clicked.connect(lambda: self._open(self.pageff_paths.get("image")))
        row.addWidget(self.pageff_open)
        self.pageff_sidecar = QPushButton("Open sidecar")
        self.pageff_sidecar.clicked.connect(lambda: self._open(self.pageff_paths.get("sidecar")))
        row.addWidget(self.pageff_sidecar)
        self.pageff_folder = QPushButton("Open folder")
        self.pageff_folder.clicked.connect(lambda: self._open(self.pageff_paths.get("out")))
        row.addWidget(self.pageff_folder)
        row.addStretch(1)
        card.add(row)
        self.pageff_progress = card.add(ProgressPanel())
        self.pageff_banner = card.add(Banner())
        self.pageff_result = card.add(muted(""))
        self.pageff_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pageff_result.setWordWrap(True)
        self.pageff_paths = {}
        self.pageff_running = False

    def refresh(self):
        ops = set(self.state.family["operations"]) if self.state.family else set()
        for op, button in self.buttons.items():
            button.setVisible(op in ops)
            button.setEnabled(bool(self.state.identity) and not self.state.busy)
        bcm = bool(self.state.family) and self.state.family["name"] == BCM
        reads = bool(ops & set(self.buttons))
        # Fault codes for every module live in the card above; this part is the extra
        # read-only tests for the module selected on Connect (EPS so far).
        per_module = bool(self.state.identity) and reads and not bcm
        self.read_card.setVisible(per_module)
        self.result_card.setVisible(per_module)
        if per_module:
            self.intro.setText(f"Extra read-only tests for the {self.state.family['label']} selected on "
                               "Connect. None of these clear faults or write anything. Run them with "
                               "the car stationary.")
            self.banner.hide()
        self.pageff_card.setVisible(bcm)
        if bcm:
            self._refresh_pageff()

    def run(self, op):
        self.tree.clear()
        params = {"samples": self.samples.value()} if op in ("motion", "assist", "supply", "can_config") else {}
        if op == "eeprom" and self.state.record:
            params["out"] = str(self.state.record.root / f"eeprom_{timestamp()}.bin")
        self.banner.show_message("Reading…", "info")

        def done(result):
            report = result.get("result") or {}
            if result.get("error"):
                self.banner.show_message(str(result["error"]), "error")
            elif op == "eeprom":
                self.banner.show_message(f"Saved to {result.get('out')}", "ok")
            else:
                self.banner.hide()
            self._fill(self.tree.invisibleRootItem(), report)
            self.tree.expandToDepth(1)

        self.state.run(op, params, done, lambda e: self.banner.show_message(self.error_text(e), "error"))

    def _pageff_available(self):
        demo = bool(self.state.adapter) and self.state.adapter.get("adapter") == "demo"
        return demo or bool(self.state.family.get("read_pageff"))

    def _refresh_pageff(self):
        available = self._pageff_available()
        self.pageff_button.setEnabled(available and bool(self.state.identity) and not self.state.busy)
        for button, key in ((self.pageff_open, "image"), (self.pageff_sidecar, "sidecar"),
                            (self.pageff_folder, "out")):
            button.setEnabled(bool(self.pageff_paths.get(key)))
        if self.pageff_running:
            return
        if not available:
            self.pageff_banner.show_message(
                "This flasher build can't read page FF yet (no --read-bcm-pageff). Update the "
                "flasher submodule, then restart the app.", "warning")
        elif not self.state.identity:
            self.pageff_banner.show_message("Read the module's info on the Connect page first (Read info).", "info")
        elif not self.pageff_paths and self.pageff_banner.property("level") != "error":
            self.pageff_banner.hide()

    def on_progress(self, message):
        if self.pageff_running:
            self.pageff_progress.update_from(message)

    def _open(self, path):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def dump_pageff(self):
        params = {}
        if self.state.record:
            params["out_dir"] = str(self.state.record.root / "PageFF")
        self.pageff_paths = {}
        self.pageff_result.setText("")
        self.pageff_banner.show_message("Reading page FF… nothing is written to the module.", "info")
        self.pageff_progress.start("Connecting")

        def finished():
            self.pageff_running = False
            self.pageff_progress.stop()

        def done(result):
            finished()
            image = result.get("image")
            if image and Path(image).is_file():
                self.pageff_paths = {key: result.get(key) for key in ("image", "sidecar", "out")}
                if self.state.record:
                    import hashlib
                    sha = hashlib.sha256(Path(image).read_bytes()).hexdigest().upper()
                    self.state.record.add({"kind": "pageff", "image": image,
                                           "sidecar": result.get("sidecar"), "sha256": sha,
                                           "identity": self.state.identity})
                self.pageff_result.setText(
                    f"Dump: {image}  ({kib(Path(image).stat().st_size)})\n"
                    f"Sidecar: {result.get('sidecar') or 'none found'}")
            if result.get("error") or not self.pageff_paths:
                text = str(result.get("error") or "The read failed.")
                if result.get("exit_code"):
                    text += f" (exit code {result['exit_code']})"
                self.pageff_banner.show_message(text + " See Help → Troubleshooting & logs for details.", "error")
            else:
                self.pageff_banner.show_message("Page FF saved. Nothing was written to the module.", "ok")
            self.refresh()

        def failed(error):
            finished()
            self.pageff_banner.show_message(self.error_text(error), "error")
            self.refresh()

        self.pageff_running = self.state.run("pageff", params, done, failed)
        if not self.pageff_running:
            self.pageff_progress.stop()

    def _fill(self, parent, value):
        if isinstance(value, dict):
            for key, child in value.items():
                item = QTreeWidgetItem(parent, [str(key), "" if isinstance(child, (dict, list)) else str(child)])
                self._fill(item, child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                item = QTreeWidgetItem(parent, [f"[{index}]", "" if isinstance(child, (dict, list)) else str(child)])
                self._fill(item, child)
