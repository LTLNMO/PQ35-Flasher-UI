"""Cars: the app's saved car profiles, one per car (by VIN; cars added by hand get their own id).

A profile holds what the car IS (VIN details, the modules a scan found, the engine's part number,
mileage) and its specs (kerb weight, tyres, gearbox, drivetrain loss, drag, tank) plus your own
notes and mods. A scan that reads a VIN creates the car's profile with typical figures for the
model (MODEL_SPECS + what the scan found: Haldex = AWD, a DSG), then the AI (Settings → AI) looks up
that exact car in the background (Filler). What you save yourself is never replaced.

Used by the Dyno (dyno_car() turns a profile plus the session's people / cargo / fuel into the
numbers dyno.analyse takes). Stored in settings["cars"]; older dyno-only profiles
(settings["dyno"]["cars"]) are moved here once (migrate)."""
import json
import re
import time

from PySide6.QtCore import QObject, Signal

from . import dyno

SETTING = "cars"
SPEC_KEYS = ("curb_kg", "tank_l", "cd", "area_m2", "crr", "tyre", "gearbox", "speed_from", "loss_pct")
DEFAULT_SPECS = {k: dyno.DEFAULT_CAR[k] for k in SPEC_KEYS}

# Typical figures by VIN model code (characters 7-8), for the usual 2.0 TFSI / 1.8 TFSI front-drive
# car with a DSG: (name, kerb kg, Cd, frontal area m², stock tyre). Starting points the AI (or
# you) then refine; all-wheel drive (a Haldex module found) adds weight and drivetrain loss.
MODEL_SPECS = {
    "1K": ("VW Golf / Jetta Mk5-6", 1450, 0.32, 2.22, "225/45R17"),
    "5K": ("VW Golf Mk6", 1400, 0.32, 2.22, "225/45R17"),
    "AJ": ("VW Golf Mk6", 1400, 0.32, 2.22, "225/45R17"),
    "5M": ("VW Golf Plus", 1400, 0.32, 2.35, "205/55R16"),
    "1T": ("VW Touran", 1520, 0.32, 2.50, "205/55R16"),
    "1F": ("VW Eos", 1550, 0.32, 2.10, "235/45R17"),
    "1Q": ("VW Eos", 1550, 0.32, 2.10, "235/45R17"),
    "13": ("VW Scirocco", 1370, 0.32, 2.12, "235/45R17"),
    "5N": ("VW Tiguan", 1600, 0.37, 2.60, "235/55R17"),
    "5C": ("VW Beetle", 1380, 0.37, 2.15, "235/45R18"),
    "3C": ("VW Passat B6/B7", 1500, 0.28, 2.30, "235/45R17"),
    "35": ("VW Passat CC", 1510, 0.29, 2.25, "235/40R18"),
    "8P": ("Audi A3 (8P)", 1420, 0.32, 2.15, "225/45R17"),
    "FM": ("Audi A3 (8P)", 1420, 0.32, 2.15, "225/45R17"),
    "FH": ("Audi A3 Cabriolet", 1530, 0.32, 2.10, "225/45R17"),
    "8J": ("Audi TT (8J)", 1300, 0.30, 2.00, "245/45R17"),
    "1P": ("SEAT Leon Mk2", 1340, 0.32, 2.20, "225/45R17"),
    "5P": ("SEAT Altea", 1360, 0.32, 2.30, "205/55R16"),
    "1Z": ("Škoda Octavia Mk2", 1400, 0.30, 2.20, "225/45R17"),
    "5L": ("Škoda Yeti", 1500, 0.36, 2.50, "225/50R17"),
    "3T": ("Škoda Superb Mk2", 1500, 0.30, 2.30, "225/45R17"),
}
AWD_EXTRA_KG = 80.0
# Common sizes on these cars (Golf, Jetta, A3, TT, Leon, Octavia, Scirocco, Passat, Tiguan ...).
TYRE_SIZES = ("195/65R15", "205/55R16", "215/55R16", "225/45R17", "225/50R17", "235/45R17", "205/50R17",
              "235/55R17", "215/45R18", "225/40R18", "235/40R18", "245/40R18", "235/50R18", "225/35R19",
              "235/35R19", "245/35R19")


# --- the store ---

def all_cars(settings):
    """{car id: profile}, newest first."""
    migrate(settings)
    cars = dict(settings.get(SETTING) or {})
    return dict(sorted(cars.items(), key=lambda kv: -(kv[1].get("updated") or 0)))


def get(settings, car_id):
    if not car_id:
        return None
    car = all_cars(settings).get(car_id)
    return dict(DEFAULT_SPECS, **car) if car else None


def save(settings, car_id, car, touch=True):
    cars = dict(settings.get(SETTING) or {})
    car = dict(car)
    if touch:
        car["updated"] = time.time()
    cars[car_id] = car
    settings.set(SETTING, cars)


def delete(settings, car_id):
    cars = dict(settings.get(SETTING) or {})
    cars.pop(car_id, None)
    settings.set(SETTING, cars)


def new_id(settings):
    cars = settings.get(SETTING) or {}
    n = 1
    while f"car-{n}" in cars:
        n += 1
    return f"car-{n}"


def for_vin(settings, vin):
    """The id of the car with this VIN, or None."""
    if not vin:
        return None
    for cid, car in all_cars(settings).items():
        if cid == vin or car.get("vin") == vin:
            return cid
    return None


def title(car):
    from .vehicle import decode
    return car.get("label") or (decode(car["vin"]).get("label") if car.get("vin") else "") or "Unnamed car"


# --- filling in ---

def scan_facts(scan):
    """What a scan says about the drivetrain: {"awd": bool|None, "dsg": bool|None}."""
    entries = (scan or {}).get("result") or []
    if not entries:
        return {"awd": None, "dsg": None}
    ok = {e.get("family") for e in entries if e.get("status") == "ok"}
    return {"awd": any("haldex" in (f or "") for f in ok), "dsg": "transmission" in ok}


def identity_from_scan(state):
    """What the last scan says about the car: modules found, engine part / software, mileage."""
    scan = getattr(state, "last_scan", None) or {}
    entries = [e for e in scan.get("result") or [] if e.get("status") == "ok"]
    if not entries:
        return None
    modules = []
    out = {"scanned": time.time()}
    for e in entries:
        fam = state.family_by_name(e.get("family")) if hasattr(state, "family_by_name") else None
        ident = e.get("identity") or {}
        part = (ident.get("software_part_number") or ident.get("part_number") or "").strip()
        desc = str(ident.get("system_desc") or "").replace("(demo)", "").strip()
        label = (fam or {}).get("label") or e.get("family", "")
        modules.append(" · ".join(x for x in (label, part, desc) if x))
        if e.get("family") == "engine":
            out["engine"] = " ".join(x for x in (desc, part, str(ident.get("sw_version") or "").strip()) if x)
        if ident.get("mileage_km") and not out.get("mileage_km"):
            out["mileage_km"] = ident["mileage_km"]
    out["modules"] = modules
    out["facts"] = scan_facts(scan)
    return out


def guess(vin, facts=None):
    """A profile from the VIN (and what a scan found) alone: typical figures for the model, or just
    the VIN's name with default figures for a model the table doesn't know."""
    from .vehicle import decode
    d = decode(vin) if vin else {}
    facts = facts or {}
    awd = bool(facts.get("awd"))
    spec = MODEL_SPECS.get((vin or "")[6:8])
    car = dict(DEFAULT_SPECS, vin=vin or "", source="built-in",
               loss_pct=17.0 if awd else 15.0 if facts.get("dsg") is not False else 13.0,
               gearbox="dsg6" if facts.get("dsg") else "unknown")
    if spec:
        name, curb, cd, area, tyre = spec
        car.update(curb_kg=curb + (AWD_EXTRA_KG if awd else 0.0), cd=cd, area_m2=area, tyre=tyre)
        car["notes"] = ("Filled in automatically: typical figures for this model"
                        + (" with all-wheel drive (Haldex found)" if awd else "") + ".")
    else:
        name = d.get("label") or "Car"
        car["notes"] = "A model the app doesn't know: default figures. Set the real weight; it matters most."
    car["label"] = " ".join(str(x) for x in (d.get("year"), name, "AWD" if awd else "") if x)
    return car


def apply_ai(car, data):
    """car updated with the AI's answer (checked, out-of-range numbers ignored): (car, filled keys)."""
    out, filled = dict(car), []
    for key, lo, hi in (("curb_kg", 500, 6000), ("cd", 0.15, 0.8), ("area_m2", 1.2, 4.0), ("loss_pct", 0, 40),
                        ("tank_l", 30, 120)):
        try:
            v = float(data[key])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= v <= hi:
            out[key] = v
            filled.append(key)
    if str(data.get("gearbox", "")).lower() in ("dsg6", "manual"):
        out["gearbox"] = str(data["gearbox"]).lower()
        filled.append("gearbox")
    if isinstance(data.get("tyre"), str) and dyno.tyre_ok(data["tyre"]):
        out["tyre"] = data["tyre"].strip()
        filled.append("tyre")
    if isinstance(data.get("label"), str) and data["label"].strip():
        out["label"] = data["label"].strip()[:80]
    note = str(data.get("notes") or "").strip()
    out["notes"] = ("AI: " + note) if note else "Filled in by AI."
    return out, filled


AI_SYSTEM = (
    "You give vehicle specifications for a virtual dyno. Answer with ONE JSON object and nothing "
    "else, with these keys: label (short name: year, model, engine, gearbox), curb_kg (kerb weight "
    "in kg with fluids and a full tank, no driver, for this exact model, engine, gearbox and drivetrain), cd (drag "
    "coefficient), area_m2 (frontal area in square metres), tyre (stock tyre size like 225/45R17), "
    "drivetrain (FWD or AWD), tank_l (fuel tank size in litres), gearbox (\"dsg6\" for the 6-speed DSG / "
    "S tronic, else \"manual\"), loss_pct (typical drivetrain loss in percent for this gearbox and "
    "drivetrain on a chassis dyno), notes (one or two short sentences: where the figures come from and "
    "anything the owner should check, e.g. options that change the weight). Use published "
    "manufacturer figures where you know them; otherwise give your best estimate and say so in notes."
)


def ai_prompt(car):
    """What the AI is told about a car: its VIN, what that decodes to, the modules a scan found,
    and how it's described so far (from the profile, so it works without the car connected)."""
    from .vehicle import decode
    lines = []
    vin = car.get("vin")
    if vin:
        d = decode(vin)
        lines.append(f"VIN: {vin}")
        if d.get("label"):
            lines.append(f"Decoded from the VIN: {d['label']}" + (f", built in {d['plant']}" if d.get("plant") else ""))
    ident = car.get("identity") or {}
    if ident.get("modules"):
        lines.append("Modules found on the car: " + "; ".join(ident["modules"]))
    if car.get("label"):
        lines.append(f"Described so far as: {car['label']}")
    if car.get("mods"):
        lines.append(f"Owner's notes: {car['mods'][:400]}")
    if not lines:
        lines.append("Unknown car: assume a 2006-2009 VW Golf GTI (Mk5) 2.0 TFSI with a DSG gearbox.")
    return "\n".join(lines) + "\n\nGive the JSON object."


def parse_ai(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in the reply")
    data = json.JSONDecoder().raw_decode(text[start:])[0]
    if not isinstance(data, dict):
        raise ValueError("unexpected reply")
    return data


# --- the dyno ---

SESSION_DEFAULTS = {"people": 1, "cargo_kg": 0.0, "fuel_frac": 1.0}
FUEL_LEVELS = ((1.0, "Full"), (0.75, "¾"), (0.5, "Half"), (0.25, "¼"), (0.08, "Nearly empty"))


def dyno_car(car, session=None):
    """The numbers dyno.analyse takes: a profile's specs plus this session's people, cargo and fuel."""
    out = dict(dyno.DEFAULT_CAR)
    if car:
        out.update({k: car[k] for k in SPEC_KEYS if k in car})
        out["label"] = title(car)
        out["notes"] = car.get("notes", "")
    out.update(dict(SESSION_DEFAULTS, **(session or {})))
    return out


# --- moving the old dyno-only profiles ---

def migrate(settings):
    """Once: profiles saved by the dyno before Cars existed (settings["dyno"]["cars"], keyed by VIN or
    "default") become Cars; their people / fuel become the dyno session's."""
    opts = settings.get("dyno") or {}
    old = opts.get("cars")
    if not old:
        return
    cars = dict(settings.get(SETTING) or {})
    session = None
    for key, prof in old.items():
        prof = dict(prof)
        if "extra_kg" in prof and "people" not in prof:
            extra = float(prof.pop("extra_kg"))
            prof.update(people=1, cargo_kg=max(extra - dyno.PERSON_KG, 0.0), fuel_frac=1.0)
        if session is None:
            session = {k: prof[k] for k in SESSION_DEFAULTS if k in prof}
        car = {k: prof[k] for k in SPEC_KEYS if k in prof}
        car.update(label=prof.get("label", ""), notes=prof.get("notes", ""), source=prof.get("source", "you"),
                   vin="" if key == "default" else key, updated=time.time())
        if "gearbox" not in car and re.search(r"\bDSG\b|S tronic|02E|DQ250", car["label"], re.I):
            car["gearbox"] = "dsg6"  # saved before the gearbox was asked; its name says
        cid = "car-default" if key == "default" else key
        cars.setdefault(cid, car)
    settings.set(SETTING, cars)
    opts = dict(opts)
    opts.pop("cars", None)
    if session:
        opts["session"] = dict(SESSION_DEFAULTS, **session)
    settings.set("dyno", opts)


class Filler(QObject):
    """Keeps the connected car's profile filled in: after a scan that read a VIN it saves the scan's
    facts (modules, mileage), creates the profile from typical figures if it's new, then asks the
    AI for this exact car in the background. Emits changed(car id) whenever a profile changed."""
    changed = Signal(str)

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self.asking = None   # car id the AI is being asked about
        self._req = None
        self._asked = set()  # ids asked this run (once per car per run)

    def vin_read(self, vin):
        """A car was plugged in (its VIN read without a scan): make sure it has a profile, then
        fill it in. AWD and the modules come with the next scan."""
        if not vin:
            return
        settings = self.state.settings
        cid = for_vin(settings, vin)
        if cid is None:
            cid = vin
            save(settings, cid, guess(vin), touch=False)
            self.changed.emit(cid)
        self.ask_ai(cid)

    def scanned(self):
        """A scan finished: record what it found about the car."""
        vin = self.state.vin()
        if not vin:
            return
        settings = self.state.settings
        cid = for_vin(settings, vin)
        ident = identity_from_scan(self.state)
        if cid is None:
            cid = vin
            car = guess(vin, (ident or {}).get("facts"))
        else:
            car = get(settings, cid)
            facts = (ident or {}).get("facts") or {}
            if car.get("source") == "built-in" and facts.get("awd") and "AWD" not in car.get("label", ""):
                car = dict(guess(vin, facts), identity=car.get("identity"))  # a better built-in guess
        if ident:
            car["identity"] = ident
        save(settings, cid, car, touch=False)
        self.changed.emit(cid)
        self.ask_ai(cid)

    def ask_ai(self, cid, force=False, on_done=None):
        """Fill in a car with the AI. Without force, only a car still on typical figures, once per run."""
        from . import ai
        settings = self.state.settings
        car = get(settings, cid)
        if car is None or self._req is not None:
            return False
        if not force and (car.get("source") != "built-in" or cid in self._asked):
            return False
        if not ai.enabled(settings) or ai.resolved(settings)[1] == "chat":
            return False
        prompt = ai_prompt(car)
        from PySide6.QtWidgets import QApplication
        if not ai.confirm_send(QApplication.activeWindow(), settings, AI_SYSTEM + "\n\n" + prompt):
            return False
        self._asked.add(cid)
        self.asking = cid
        reply = []
        req = self._req = ai.Ask(settings, AI_SYSTEM, [{"role": "user", "content": prompt}], "low", self)

        def finish(result):
            self._req = None
            self.asking = None
            self.changed.emit(cid)
            if on_done:
                on_done(result)

        def done():
            try:
                data = parse_ai("".join(reply))
            except ValueError as e:
                finish(f"Couldn't read the answer ({e}).")
                return
            current = get(settings, cid)
            if current is None or (current.get("source") == "you" and not force):
                finish("")  # deleted, or you saved your own while it was asking
                return
            new, filled = apply_ai(current, data)
            if filled:
                save(settings, cid, dict(new, source="ai"))
            finish("" if filled else "The answer had no usable numbers.")

        req.text.connect(reply.append)
        req.done.connect(done)
        req.failed.connect(lambda error: finish(f"Couldn't fill in: {error}"))
        req.start()
        self.changed.emit(cid)
        return True


def filler(state):
    """The app's one Filler (made on first use; it follows every scan from then on)."""
    f = getattr(state, "_car_filler", None)
    if f is None:
        f = state._car_filler = Filler(state)
        state.modules_scanned.connect(lambda _result: f.scanned())
        state.car_changed.connect(f.vin_read)
    return f
