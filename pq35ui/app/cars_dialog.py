"""The Cars window: your saved car profiles (cars.py) on the left, the one picked on the right.
Opened from the car in the top bar and from the Dyno tab. Changes save as you leave a car, add
one, or close; anything you save is yours and never replaced automatically."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import cars, dyno
from . import units as unitconv
from .popups import MessageBox, OverlayDialog
from .widgets import muted, scrollable

ID_ROLE = Qt.ItemDataRole.UserRole
WIDTHS = tuple(range(155, 336, 10))     # mm: 155 … 335
PROFILES = tuple(range(25, 81, 5))      # %: 25 … 80
RIMS = tuple(range(13, 23))             # inches: 13 … 22


class TyrePicker(QWidget):
    """Tyre size as three dropdowns, width / profile R rim (e.g. 225 / 45 R 17), read off the
    tyre's sidewall. text() / set_text() use the usual "225/45R17"."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.width, self.profile, self.rim = QComboBox(), QComboBox(), QComboBox()
        for combo, values, tip in ((self.width, WIDTHS, "Width (mm)"), (self.profile, PROFILES, "Profile (%)"),
                                   (self.rim, RIMS, "Rim (inches)")):
            for v in values:
                combo.addItem(str(v), v)
            combo.setToolTip(tip)
            combo.currentIndexChanged.connect(lambda _i: on_change())
        row.addWidget(self.width)
        row.addWidget(QLabel("/"))
        row.addWidget(self.profile)
        row.addWidget(QLabel("R"))
        row.addWidget(self.rim)
        row.addStretch(1)

    def set_text(self, text):
        import re
        m = re.match(r"\s*(\d{3})\s*/\s*(\d{2})\s*[A-Za-z]*\s*(\d{2})", text or "")
        w, p, r = (int(x) for x in m.groups()) if m else (225, 45, 17)
        for combo, v in ((self.width, w), (self.profile, p), (self.rim, r)):
            combo.blockSignals(True)
            i = combo.findData(v)
            if i < 0:  # an unusual size: add it
                combo.addItem(str(v), v)
                i = combo.count() - 1
            combo.setCurrentIndex(i)
            combo.blockSignals(False)

    def text(self):
        return f"{self.width.currentData()}/{self.profile.currentData()}R{self.rim.currentData()}"


GAL_PER_L = 0.264172


def imperial(settings):
    return unitconv.system(settings) == "imperial"


class CarsDialog(OverlayDialog):
    CARD_ICON = "directions_car"

    def __init__(self, state, filler, select=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = state.settings
        self.filler = filler
        self.current = None      # car id shown
        self.dirty = False
        self._filling = False    # filling the form in: not your edits
        self.setWindowTitle("Cars")
        self.setMinimumSize(900, 640)
        imp = imperial(self.settings)
        self.kg = dyno.KG_LB if imp else 1.0
        wunit = "lb" if imp else "kg"
        self.litre = GAL_PER_L if imp else 1.0  # tank shown in US gallons when imperial

        lay = QVBoxLayout(self)
        lay.addWidget(muted("Your cars: what they are and their specs. Plugging into a car adds it by its VIN and "
                            "fills it in (typical figures, then your AI for that exact car); a scan adds its "
                            "modules and mileage. What you save is never replaced. The Dyno works out every pull "
                            "from these."))
        body = QHBoxLayout()
        body.setSpacing(12)

        # left: the cars
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._picked)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setToolTip("Add a car by hand (with or without its VIN)")
        add.clicked.connect(self._add)
        row.addWidget(add)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        left.addLayout(row)
        body.addLayout(left)

        # right: the car
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setVerticalSpacing(8)

        def spin(lo, hi, decimals, step, suffix=""):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(decimals)
            s.setSingleStep(step)
            if suffix:
                s.setSuffix(" " + suffix)
            s.valueChanged.connect(self._edited)
            return s

        self.label = QLineEdit()
        self.label.setPlaceholderText("e.g. 2011 Audi A3 2.0 TFSI quattro DSG")
        self.label.textEdited.connect(self._edited)
        form.addRow("Name", self.label)
        self.vin = QLineEdit()
        self.vin.setPlaceholderText("17 characters (optional for a car added by hand)")
        self.vin.textEdited.connect(self._edited)
        form.addRow("VIN", self.vin)
        self.curb = spin(500 * self.kg, 6000 * self.kg, 0, 10, wunit)
        self.curb.setToolTip("Kerb weight: full fluids and a full tank, no driver. A weighbridge beats any spec "
                             "sheet. For the dyno, weight matters most: 5% off is 5% off.")
        form.addRow("Kerb weight", self.curb)
        self.tank = spin(30 * self.litre, 120 * self.litre, 1 if imp else 0, 1, "gal" if imp else "l")
        self.tank.setToolTip("Fuel tank size (the dyno takes off the fuel that isn't in it)")
        form.addRow("Fuel tank", self.tank)
        self.tyre = TyrePicker(self._edited)
        self.tyre.setToolTip("As on the tyre's sidewall: width / profile R rim. Only the size counts (not the "
                             "brand). It matters most with "
                             "Speed from → Gear ratio + tyre size and for the speed-signal check: 1% off in "
                             "diameter is about 2% in power.")
        form.addRow("Tyre size", self.tyre)
        self.gearbox = QComboBox()
        for key, text in dyno.GEARBOX_CHOICES:
            self.gearbox.addItem(text, key)
        self.gearbox.setToolTip("With a known gearbox the dyno checks the speed signal against its gear ratios "
                                "and the tyre size")
        self.gearbox.currentIndexChanged.connect(self._gearbox_changed)
        form.addRow("Gearbox", self.gearbox)
        self.speed_from = QComboBox()
        for key, text in dyno.SPEED_FROM:
            self.speed_from.addItem(text, key)
        self.speed_from.setToolTip(
            "Where the dyno's road speed scale comes from (engine speed gives its shape).\n"
            "Automatic: the speed signal and gear ratio + tyre size, weighted by how far each is typically off "
            f"(about {dyno.SIGMA_ABS_PCT:.0f}% and {dyno.SIGMA_TYRE_PCT:.1f}%); more than "
            f"{dyno.BLEND_MAX_PCT:.0f}% apart it uses the speed signal and warns you.\n"
            "Speed signal: measured, but a speedo 3% high makes power 6% high.\n"
            "Gear ratio + tyre size: right if the tyre size is.\n"
            "Only for a gearbox whose ratios are known; otherwise the speed signal is used.")
        self.speed_from.currentIndexChanged.connect(self._edited)
        form.addRow("Speed from", self.speed_from)
        self.loss = spin(0, 40, 1, 0.5, "%")
        self.loss.setToolTip("Drivetrain loss, for the dyno's engine estimate. FWD DSG about 13–15%, Haldex AWD "
                             "about 16–19%")
        form.addRow("Drivetrain loss", self.loss)
        self.cd = spin(0.15, 0.8, 3, 0.005)
        self.cd.setToolTip("Drag coefficient (Cd), e.g. 0.32 for a Mk5 Golf GTI")
        form.addRow("Drag coefficient", self.cd)
        self.area = spin(1.2, 4.0, 2, 0.01, "m²")
        self.area.setToolTip("Frontal area, about 0.84 × width × height")
        form.addRow("Frontal area", self.area)
        self.crr = spin(0.005, 0.03, 4, 0.001)
        self.crr.setToolTip("Rolling resistance coefficient: 0.010 to 0.015 for road tyres; small effect")
        form.addRow("Rolling resistance", self.crr)
        self.mods = QPlainTextEdit()
        self.mods.setPlaceholderText("Mods, tune, anything worth remembering (the AI sees this too)")
        self.mods.setFixedHeight(70)
        self.mods.textChanged.connect(self._edited)
        form.addRow("Notes and mods", self.mods)
        rl.addLayout(form)
        self.source = muted("")
        self.source.setWordWrap(True)
        rl.addWidget(self.source)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        rl.addWidget(line)
        self.identity = muted("")
        self.identity.setWordWrap(True)
        self.identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rl.addWidget(self.identity)
        rl.addStretch(1)
        body.addWidget(scrollable(right), 1)
        lay.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.ai_button = QPushButton("Fill in with AI")
        self.ai_button.setToolTip("Ask your AI (Settings → AI) for this car's weight, drag, tyres and gearbox from "
                                  "its VIN, modules and notes. You can still change every value.")
        self.ai_button.clicked.connect(self._ask_ai)
        bottom.addWidget(self.ai_button)
        self.ai_status = muted("")
        bottom.addWidget(self.ai_status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)

        self.filler.changed.connect(self._filler_changed)
        self.finished.connect(lambda _r: (self._save_current(), self._disconnect()))
        self._fill_list(select or cars.for_vin(self.settings, state.vin()))

    def _disconnect(self):
        try:
            self.filler.changed.disconnect(self._filler_changed)
        except (RuntimeError, TypeError):
            pass

    # --- the list ---

    def _fill_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        connected = cars.for_vin(self.settings, self.state.vin())
        for cid, car in cars.all_cars(self.settings).items():
            vin = car.get("vin") or "no VIN"
            mark = "\n● connected now" if cid == connected else ""
            item = QListWidgetItem(f"{cars.title(car)}\n{vin}{mark}")
            item.setData(ID_ROLE, cid)
            self.list.addItem(item)
        self.list.blockSignals(False)
        index = next((i for i in range(self.list.count()) if self.list.item(i).data(ID_ROLE) == select), 0)
        if self.list.count():
            self.list.setCurrentRow(index)
        else:
            self.current = None
            self._show(None)

    def _picked(self, item, _previous):
        self._save_current()
        self.current = item.data(ID_ROLE) if item else None
        self._show(cars.get(self.settings, self.current))

    def _add(self):
        self._save_current()
        cid = cars.new_id(self.settings)
        cars.save(self.settings, cid, dict(cars.DEFAULT_SPECS, label="New car", vin="", source="you",
                                           notes="Added by hand."))
        self._fill_list(cid)
        self.label.setFocus()
        self.label.selectAll()
        self.filler.changed.emit(cid)

    def _delete(self):
        if not self.current:
            return
        car = cars.get(self.settings, self.current) or {}
        from PySide6.QtWidgets import QMessageBox
        answer = MessageBox.question(self, "Delete car", f"Delete {cars.title(car)}? Its logs and dyno sessions "
                                     "are kept; scanning the car again adds it back with typical figures.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        cid, self.current, self.dirty = self.current, None, False
        cars.delete(self.settings, cid)
        self._fill_list()
        self.filler.changed.emit(cid)

    # --- the form ---

    def _show(self, car):
        self._filling = True
        enabled = car is not None
        for w in (self.label, self.vin, self.curb, self.tank, self.tyre, self.gearbox, self.speed_from, self.loss,
                  self.cd, self.area, self.crr, self.mods, self.ai_button, self.delete_button):
            w.setEnabled(enabled)
        car = car or dict(cars.DEFAULT_SPECS)
        self.label.setText(car.get("label", ""))
        self.vin.setText(car.get("vin", ""))
        self.vin.setReadOnly(bool(car.get("identity")))  # read from the car: fixed
        self.curb.setValue(float(car["curb_kg"]) * self.kg)
        self.tank.setValue(float(car["tank_l"]) * self.litre)
        self.tyre.set_text(car.get("tyre") or dyno.DEFAULT_CAR["tyre"])
        self.gearbox.setCurrentIndex(max(self.gearbox.findData(car.get("gearbox", "unknown")), 0))
        self.speed_from.setCurrentIndex(max(self.speed_from.findData(car.get("speed_from", "auto")), 0))
        self.loss.setValue(float(car["loss_pct"]))
        self.cd.setValue(float(car["cd"]))
        self.area.setValue(float(car["area_m2"]))
        self.crr.setValue(float(car["crr"]))
        self.mods.setPlainText(car.get("mods", ""))
        self._gearbox_changed()
        self._show_source(car)
        self._show_identity(car)
        self.ai_status.setText("Asking…" if self.filler.asking == self.current and self.current else "")
        self.dirty = False
        self._filling = False

    def _show_source(self, car):
        notes = car.get("notes", "")
        if car.get("source") == "built-in":  # its notes say so
            text = notes
        else:
            source = {"you": "Set by you.", "ai": "Filled in by AI."}.get(car.get("source"), "")
            text = " ".join(x for x in (source, notes if notes not in ("Added by hand.", "") else "") if x)
        self.source.setText(text)

    def _show_identity(self, car):
        from .vehicle import decode
        lines = []
        vin = car.get("vin")
        if vin:
            d = decode(vin)
            lines.append("From the VIN: " + ", ".join(str(d[k]) for k in ("make", "model", "year", "plant") if d.get(k)))
        ident = car.get("identity") or {}
        if ident.get("engine"):
            lines.append(f"Engine: {ident['engine']}")
        if ident.get("mileage_km"):
            km = ident["mileage_km"]
            lines.append(f"Mileage: {km * 0.621371:,.0f} mi ({km:,} km)" if imperial(self.settings)
                         else f"Mileage: {km:,} km ({km * 0.621371:,.0f} mi)")
        if ident.get("modules"):
            lines.append(f"Modules found ({len(ident['modules'])}): " + "; ".join(ident["modules"]))
        if self.current:
            from .projects import Project
            mine = [p for p in Project.all() if p.data.get("car_id") == self.current]
            if mine:
                lines.append("Projects: " + ", ".join(p.name + (" (active)" if car.get("active_project") == p.root.name
                                                                 else "") for p in mine))
        if ident.get("scanned"):
            lines.append("Last scanned " + time.strftime("%d %b %Y, %H:%M", time.localtime(ident["scanned"])))
        self.identity.setText("\n".join(lines) or "Not scanned yet: connect and Scan on the Connect page to read "
                                                  "its modules and mileage.")

    def _edited(self, *_):
        if not self._filling:
            self.dirty = True

    def _gearbox_changed(self, *_):
        known = self.gearbox.currentData() in dyno.GEARBOXES
        self.speed_from.setEnabled(known and self.current is not None)
        self._edited()

    def values(self):
        vin = self.vin.text().strip().upper()
        return {
            "label": self.label.text().strip(), "vin": vin,
            "curb_kg": self.curb.value() / self.kg, "tank_l": self.tank.value() / self.litre,
            "tyre": self.tyre.text(),
            "gearbox": self.gearbox.currentData(), "speed_from": self.speed_from.currentData(),
            "loss_pct": self.loss.value(), "cd": self.cd.value(), "area_m2": self.area.value(), "crr": self.crr.value(),
            "mods": self.mods.toPlainText().strip(),
        }

    def _save_current(self):
        if not self.current or not self.dirty:
            return
        car = cars.get(self.settings, self.current) or {}
        vals = self.values()
        if vals["vin"] and len(vals["vin"]) != 17:
            vals["vin"] = car.get("vin", "")  # not a VIN: keep the old one
        car.update(vals, source="you")
        cars.save(self.settings, self.current, car)
        self.dirty = False
        cid = self.current
        self.filler.changed.emit(cid)
        # the name in the list
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(ID_ROLE) == cid:
                item.setText(f"{cars.title(car)}\n{car.get('vin') or 'no VIN'}"
                             + ("\n● connected now" if cid == cars.for_vin(self.settings, self.state.vin()) else ""))

    # --- AI ---

    def _ask_ai(self):
        from . import ai
        if not self.current:
            return
        if not ai.enabled(self.settings):
            MessageBox.information(self, "AI is off", "Choose an AI in Settings → AI first.")
            return
        if ai.resolved(self.settings)[1] == "chat":
            MessageBox.information(self, "Needs an AI that answers in the app",
                                   "“My AI chat in the browser” can't send the answer back. Pick an API key, "
                                   "Claude Code, Gemini CLI, Codex CLI or a local model in Settings → AI.")
            return
        self._save_current()
        cid = self.current
        started = self.filler.ask_ai(cid, force=True, on_done=lambda msg: self._ai_done(cid, msg))
        if started:
            self.ai_button.setEnabled(False)
            self.ai_status.setText("Asking…")
        elif self.filler.asking:
            self.ai_status.setText("Already asking about another car: try again in a moment.")

    def _ai_done(self, cid, message):
        if not self.isVisible():
            return
        self.ai_button.setEnabled(self.current is not None)
        if self.current == cid:
            self._show(cars.get(self.settings, cid))
            self.ai_status.setText(message or "Filled in. Check the values, then add your own weight if you know it.")

    def _filler_changed(self, cid):
        """A scan or the AI changed a car: show it (unless you're editing it)."""
        if not self.isVisible():
            return
        if cid == self.current and not self.dirty:
            self._show(cars.get(self.settings, cid))
        if cid not in [self.list.item(i).data(ID_ROLE) for i in range(self.list.count())] and \
                cars.get(self.settings, cid) is not None:
            self._fill_list(self.current)
