"""The module screens: Connect, Back up, Patches, Diagnostics (Flash is in flash_page.py)."""
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton,
    QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .needs_car import gate
from .storage import images_dir, patched_dir, timestamp
from .widgets import Badge, Banner, Card, DropZone, ProgressPanel, discard, kib, muted, restyle
from .toolbar import ToolIcon

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
                     ("logger", "Logger"), ("playback", "Analyze"), ("dyno", "Dyno"), ("maps", "Maps"))
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
        from . import theme
        if theme.is_mac():
            from .mac_shell import CONTENT_MARGINS
            self.layout_.setContentsMargins(*CONTENT_MARGINS)
        else:
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
            discard(self.grid.takeAt(0).widget())
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


MAC_STATUS = {  # Mac card pills: short, like the render's Ready / Unexpected part / Can't be reached
    "ok": ("Ready", "ok"),
    "bootloader": ("In its bootloader", "warning"),
    "responding": ("Won't identify", "warning"),
    "unexpected": ("Different module", "error"),
    "absent": ("Can't be reached", "error"),
    None: ("Not checked", "idle"),
}


class MacModuleCard(ModuleCard):
    """Mac interface: the render's compact card. [01  Engine] / part · system / [Ready] … [Select].
    Same attributes as ModuleCard, so show_entry and the page work unchanged; the selected
    card's full identity is in its tooltip instead of a grid that makes it taller."""

    def __init__(self, family, page):
        Card.__init__(self)
        self.family = family
        self.page = page
        self.entry = None
        self._selected = False
        self.setProperty("module", True)
        self.body.setContentsMargins(18, 14, 18, 14)
        self.body.setSpacing(6)
        self.art = QLabel()  # no pictures on these cards (paint_art keeps it hidden)
        self.art.hide()
        head = QHBoxLayout()
        head.setSpacing(8)
        self.number = QLabel(f"{family['module']:02X}")
        self.number.setObjectName("macModNum")
        head.addWidget(self.number, 0, Qt.AlignmentFlag.AlignBaseline)
        title = QLabel(family["label"])
        title.setObjectName("macModName")
        head.addWidget(title, 1, Qt.AlignmentFlag.AlignBaseline)
        self.info_button = ToolIcon("info", "Show this module's details", checkable=True)
        self.info_button.toggled.connect(self._toggle_info)
        head.addWidget(self.info_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.add(head)
        self.address = QLabel()  # kept for show_entry; shown as a row of the details
        self.address.hide()
        self.detail = self.add(muted(""))
        # ⓘ opens these: part, software, description, flash counts, address (show_entry fills them)
        self.details = QWidget()
        self.grid = QGridLayout(self.details)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(3)
        self.details.hide()
        self.add(self.details)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.badge = Badge("", "idle")
        foot.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.faults = Badge("", "error")
        self.faults.setCursor(Qt.CursorShape.PointingHandCursor)
        self.faults.mousePressEvent = lambda _e: self._open_faults()
        self.faults.hide()
        foot.addWidget(self.faults, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addStretch(1)
        self.identify = QPushButton("Re-read info")
        self.identify.clicked.connect(page.identify)
        self.recover = QPushButton("Recover…")
        self.recover.setObjectName("danger")
        self.recover.clicked.connect(lambda: page.recover(self.family))
        self.use = QPushButton("Select")
        self.use.clicked.connect(lambda: None if self._selected else page.choose(self.family, self.entry))
        for button in (self.identify, self.recover, self.use):
            foot.addWidget(button)
        self.body.addStretch(1)  # cards are evened in height: the buttons stay at the bottom
        self.add(foot)

    def paint_art(self):
        self.art.hide()

    @property
    def expanded(self):
        return self.info_button.isChecked()

    def _toggle_info(self, on):
        self.details.setVisible(on)
        self.info_button.setToolTip("Hide the details" if on else "Show this module's details")
        self.page._even_heights()  # this card grows (only its column moves down); the rest stay one size

    def set_faults(self, counts):
        codes, active = counts or (0, 0)
        self.faults.set(f"{codes} fault{'s' if codes != 1 else ''}", "error")
        self.faults.setToolTip(f"{codes} fault code{'s' if codes != 1 else ''}"
                               + (f", {active} still active" if active else "")
                               + ". Click: work on this module and see them on Diagnostics.")
        self.faults.setVisible(bool(codes))

    def show_entry(self, entry, selected, identity):
        super().show_entry(entry, selected, identity)
        self._selected = bool(selected and identity)
        status = self.status()
        workshop = (entry or {}).get("workshop")
        self.number.setText(workshop["address_text"] if workshop else f"{self.family['module']:02X}")
        tip = [self.family["label"], self.address.text()]
        found = identity if self._selected else ((entry or {}).get("identity") or {})
        rows = identity_rows(found) if found else []
        if not self._selected:  # the scan doesn't read flash counts: leave out its placeholders
            rows = [(k, v) for k, v in rows if k not in ("Flash attempts / successes", "Last flashed")]
        rows.append(("Address", self.address.text()))
        self.setToolTip("\n".join(t for t in tip if t))
        while self.grid.count():
            discard(self.grid.takeAt(0).widget())
        for row, (key, value) in enumerate(rows):
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(muted(key), row, 0)
            self.grid.addWidget(value_label, row, 1)
        self.grid.setColumnStretch(1, 1)
        generic_quiet = self.family.get("generic") and not self.family.get("has_dtc")
        text, level = MAC_STATUS.get(status, MAC_STATUS[None])
        if status == "ok" and generic_quiet:
            text, level = "No fault codes", "idle"
        self.badge.set(text, level)
        part = (found.get("software_part_number") or found.get("part_number") or "").strip()
        desc = str(found.get("system_desc", "")).strip()
        if status == "ok" or self._selected:
            line = " · ".join(t for t in (part, desc) if t)
            if not line:
                line = "Answers, but didn't say what it is"
            if (entry or {}).get("in_gateway") is False:
                line += " · not in the gateway's list"
            self.detail.setText(line)
            self.detail.setWordWrap(False)
        else:
            self.detail.setWordWrap(True)  # the explanation from ModuleCard (loader, wrong part, …)
        self.detail.setVisible(bool(self.detail.text()))
        # Selected: a blue "Selected" (does nothing); others: a plain "Select".
        can_pick = status == "ok" and not generic_quiet
        self.use.setText("Selected" if self._selected else "Select")
        self.use.setObjectName("primary" if self._selected else "")
        self.use.setVisible(self._selected or (can_pick and not selected))
        restyle(self.use)


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

        from . import theme
        self.mac = theme.is_mac()
        self.intro = ("Ignition on, engine off. Scan reads the main modules (choose which in "
                      "Settings → General); Full Scan reads every module on the gateway's list. "
                      "Both only read: what each module is and its fault codes.")
        self.scan_button = QPushButton("Scan")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(lambda: self.scan())
        self.full_scan_button = QPushButton("Full Scan")
        self.full_scan_button.setToolTip("Like a VCDS Auto-Scan: opens every module the gateway lists and "
                                         "reads who it is and its fault codes. Read-only; about 1–2 s per "
                                         "module.")
        self.full_scan_button.clicked.connect(lambda: self.scan(full=True))
        from .widgets import pulldown
        from .menus import popup_menu
        self.pick_button = pulldown(QPushButton("Choose a module" if self.mac else "Select manually"))
        self.pick_button.setToolTip("Choose a module type yourself without scanning, e.g. on a bench harness.")
        self.pick_menu = QMenu(self)
        self.pick_button.clicked.connect(lambda: popup_menu(self.pick_menu, self.pick_button))
        self.adapter_text = muted("")
        self.adapter_help = Banner()
        self.bring_up = QPushButton("")
        self.bring_up.setToolTip("Bring the SocketCAN adapter up at 500 kbit/s (asks for your password).")
        self.bring_up.clicked.connect(self.bring_up_socketcan)
        self.bring_up.hide()
        self.bring_up_process = None
        self.scan_banner = Banner()
        self.faults_banner = Banner()
        state.faults_changed.connect(lambda _n: self._refresh_cards())
        theme.on_apply(lambda: [c.paint_art() for c in {**self.cards, **self.generic_cards}.values()])

        # Module cards in equal columns (Classic: two, the user's choice 2026-11-09; Mac: three,
        # like the render). Independent columns (not a grid): when the selected Classic card grows,
        # only its own column moves down.
        self.cards_box = QHBoxLayout()
        self.cards_box.setContentsMargins(0, 0, 0, 0)
        self.cards_box.setSpacing(14 if self.mac else 10)
        self.card_columns = []
        for _ in range(3 if self.mac else CARD_COLUMNS):
            column = QVBoxLayout()
            column.setSpacing(14 if self.mac else 10)
            self.cards_box.addLayout(column, 1)
            self.card_columns.append(column)
        self.cards_holder = QWidget()
        self.cards_holder.setLayout(self.cards_box)
        self.absent = muted("")
        self.absent.setWordWrap(True)
        self.others = muted("")
        self.others.setWordWrap(True)
        self.gateway = None
        self.banner = Banner()
        self.next_hint = Banner()
        if self.mac:
            self._build_mac()
        else:
            self._build_classic()
        self.finish()

    def _build_classic(self):
        find = self.add(Card())
        find.add(muted(self.intro))
        row = QHBoxLayout()
        for w in (self.scan_button, self.full_scan_button, self.pick_button):
            row.addWidget(w)
        row.addWidget(self.adapter_text, 1)
        find.add(row)
        find.add(self.adapter_help)
        row = QHBoxLayout()
        row.addWidget(self.bring_up)
        row.addStretch(1)
        find.add(row)
        find.add(self.scan_banner)
        find.add(self.faults_banner)
        for w in (self.cards_holder, self.absent, self.others, self.banner, self.next_hint):
            self.add(w)

    def _build_mac(self):
        """The render's Connect: title bar [Scan] [Full Scan] via adapter; the car on top; MODULES
        FOUND as a three-across grid; Select manually under it."""
        from .mac_shell import page_bar
        self.mac_bar, bar = page_bar()
        bar.addWidget(self.scan_button)
        bar.addWidget(self.full_scan_button)
        bar.addSpacing(4)
        bar.addWidget(self.adapter_text)
        bar.addStretch(1)
        self.scan_button.setMinimumWidth(72)
        # Problems first (adapter, scan), then the car, then the modules.
        self.add(self.adapter_help)
        row = QHBoxLayout()
        row.addWidget(self.bring_up)
        row.addStretch(1)
        self.layout_.addLayout(row)
        self.add(self.scan_banner)
        self.car_card = self.add(self._mac_car_card())
        self.modules_title = self.add(QLabel("MODULES FOUND"))
        self.modules_title.setObjectName("cardTitle")
        self.modules_title.setContentsMargins(0, 6, 0, 0)
        for w in (self.cards_holder, self.absent, self.others):
            self.add(w)
        manual = QHBoxLayout()
        manual.setSpacing(6)
        for w in (muted("Select manually:"), self.pick_button, muted("if a module doesn't answer the scan.")):
            if isinstance(w, QLabel):
                w.setWordWrap(False)
            manual.addWidget(w)
        manual.addStretch(1)
        self.layout_.addLayout(manual)
        self.add(self.banner)
        self.add(self.next_hint)
        # the car card and each module's pill show the faults: this banner lives in a hidden holder
        holder = QWidget(self)
        holder.hide()
        QVBoxLayout(holder).addWidget(self.faults_banner)
        self.adapter_text.setWordWrap(False)

    def _mac_car_card(self):
        """The car on top: ✓ name / VIN · how it was read · last scan, and Car details…"""
        card = Card()
        card.setProperty("module", True)
        card.body.setContentsMargins(18, 12, 14, 12)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.car_icon = QLabel()
        self.car_icon.setFixedSize(30, 30)
        row.addWidget(self.car_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.car_title = QLabel("")
        self.car_title.setObjectName("macCarTitle")
        self.car_line = muted("")
        self.car_line.setWordWrap(True)
        col.addWidget(self.car_title)
        col.addWidget(self.car_line)
        row.addLayout(col, 1)
        self.car_button = QPushButton("Car details…")
        self.car_button.setToolTip("Your cars: details, weight for the dyno, notes")
        self.car_button.clicked.connect(self._open_cars)
        row.addWidget(self.car_button, 0, Qt.AlignmentFlag.AlignVCenter)
        card.add(row)
        return card

    def _open_cars(self):
        window = self.window()
        if hasattr(window, "_open_cars"):
            window._open_cars()

    def _refresh_car(self):
        """Mac car card: the car connected now (VIN read this run), else what to do."""
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        car = (self.state.vehicle or {}) if self.state.vin() else {}
        scan = self.state.last_scan or {}
        if car.get("vin"):
            self.car_title.setText(car.get("label") or "Car")
            source = str(car.get("source") or "").replace("_", " ")
            parts = [f"VIN {car['vin']}"] + ([f"read from the {source}"] if source else [])
        else:
            self.car_title.setText("No car read yet")
            parts = ["Ignition on, engine off, then press Scan"]
        if scan.get("started"):
            found = sum(1 for e in scan.get("result") or () if e.get("status") == "ok")
            found += sum(1 for m in ((scan.get("gateway") or {}).get("modules") or ())
                         if not m.get("family") and m.get("status") == "ok")
            codes = sum(n for n, _a in self.state.fault_summary.values())
            when = time.strftime("%H:%M", time.localtime(scan["started"]))
            day = "today" if time.strftime("%x", time.localtime(scan["started"])) == time.strftime("%x") \
                else time.strftime("%b %d", time.localtime(scan["started"]))
            parts.append(f"last scan {day} {when} ({found} module{'s' if found != 1 else ''}, "
                         f"{codes} fault{'s' if codes != 1 else ''})")
        self.car_line.setText(" · ".join(parts))
        ok = bool(car.get("vin"))
        icon = _material_icon("check_circle" if ok else "directions_car", theme.MacPx(30),
                              c["ok"] if ok else c["muted"])  # SF Symbols on a Mac
        self.car_icon.setPixmap(icon.pixmap(30, 30))
        self.car_card.setToolTip(self.intro)
        self.car_button.setVisible(ok)

    # --- module cards ---

    def _build_cards(self):
        if self.cards or not self.state.families:
            return
        self.pick_menu.clear()
        for family in self.state.families:
            self.cards[family["name"]] = (MacModuleCard if self.mac else ModuleCard)(family, self)
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
            discard(card)
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
            card = self.generic_cards[name] = (MacModuleCard if self.mac else ModuleCard)(family, self)
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
            self.card_columns[index % len(self.card_columns)].addWidget(card)
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
        if self.mac:
            self.modules_title.setVisible(bool(shown))
            self._refresh_car()
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
        if not getattr(self, "scan_full", True):
            listed = len({e["tp2"] for e in gateway.get("entries") or () if e.get("coded", True)})
            self.others.setText(f"Quick scan: {len(modules)} of the {listed} modules on the gateway "
                                f"list, {answered} answered. Full Scan reads them all (its scan log "
                                "and every module's fault codes are on Diagnostics).")
        else:
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
        selected = self.state.family["name"] if self.state.family and not self.mac else None
        for card in self.cards.get(selected), self.generic_cards.get(selected):
            if card is not None:  # never squeezed: always room for its details
                card.setMinimumHeight(0)
                card.setMinimumHeight(card.natural_height(card.width()))
        grown = [c for c in {**self.cards, **self.generic_cards}.values()
                 if getattr(c, "expanded", False)]  # Mac: ⓘ open
        for card in grown:
            card.setMinimumHeight(0)
            card.setMinimumHeight(card.natural_height(card.width()))
        cards = [c for c in {**self.cards, **self.generic_cards}.values()
                 if c.isVisible() and c.family["name"] != selected and c not in grown]
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

    def scan(self, full=False):
        """The scan: the quick one reads the modules picked in Settings (quick_scan); the full one
        every module on the gateway's list. Each module's identity and fault codes."""
        from . import quick_scan
        only = None if full else quick_scan.addresses(self.state.settings)
        self.scan_banner.show_message("Reading every module on the gateway list…" if full else
                                      f"Quick scan: {quick_scan.describe(self.state.settings)}…", "info")
        self.banner.hide()

        def done(result):
            entries = result.get("result") or []
            if result.get("error"):
                self.scan_banner.show_message("The scan stopped: " + str(result["error"]), "error")
                return
            self.scan_results = {e["family"]: e for e in entries}
            self.gateway = result.get("gateway")
            self.scan_full = result.get("full", True)
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
            if result.get("full"):
                self.state.last_full_scan = result
            self.state.modules_scanned.emit(result)

        def failed(error):
            self.scan_banner.show_message(self.error_text(error), "error")

        started = time.time()
        inner_done = done

        def done(result):  # noqa: F811  (wraps the handler above with when/how long)
            result["started"], result["elapsed"] = started, time.time() - started
            inner_done(result)

        params = {"freeze_frames": bool(self.state.settings.get("scan_freeze_frames", True))}
        if only is not None:
            params["only"] = only
        self.state.run("scan", params, done, failed)

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
        gate(self.scan_button, self.state, idle)
        gate(self.full_scan_button, self.state, idle)
        from . import quick_scan
        self.scan_button.setToolTip("Quick scan: " + quick_scan.describe(self.state.settings)
                                    + ". Change which in Settings → General → Scan."
                                    + ("\n\n" + self.intro if self.mac else ""))
        self.pick_button.setEnabled(idle)
        for card in {**self.cards, **self.generic_cards}.values():
            card.setEnabled(idle)
            gate(card.identify, self.state, idle)  # Read info
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
        gate(self.read_button, self.state, bool(record) and not self.state.busy)
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
                discard(item.widget())

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
        self.mac_bar = getattr(self.view, "mac_bar", None)  # Mac: None (the log's buttons are in its pane)
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
        from . import theme
        if theme.is_mac():  # Modules column and log run to the window's edges; the rest keeps a gutter
            self.layout_.setContentsMargins(0, 0, 0, 0)
            self.layout_.setSpacing(0)
            rest = QWidget()
            rest_lay = QVBoxLayout(rest)
            rest_lay.setContentsMargins(24, 0, 24, 16)
            rest_lay.setSpacing(14)
            moving = [self.layout_.itemAt(i).widget() for i in range(self.layout_.count())]
            for w in moving:
                if w is not None and w is not self.view:
                    self.layout_.removeWidget(w)
                    rest_lay.addWidget(w)
            self.layout_.addWidget(rest)
            self._mac_rest, self._mac_rest_lay = rest, rest_lay
            # Only takes room (and its bottom margin) when a module test card shows; otherwise the
            # Modules column and the log run to the window's bottom edge.
            self._sync_mac_rest()  # and after refresh() shows / hides the cards

    def _sync_mac_rest(self):
        lay = getattr(self, "_mac_rest_lay", None)
        if lay is None:
            return
        shown = any(lay.itemAt(i).widget() is not None and not lay.itemAt(i).widget().isHidden()
                    for i in range(lay.count()))
        self._mac_rest.setVisible(shown)

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
            gate(button, self.state, bool(self.state.identity) and not self.state.busy)
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
        self._sync_mac_rest()
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
        gate(self.pageff_button, self.state, available and bool(self.state.identity) and not self.state.busy)
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
