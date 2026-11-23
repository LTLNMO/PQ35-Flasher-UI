"""The Dyno tab's recorder: no screen of its own, independent of the Logger page.

A dyno session reads only what the dyno needs from the chosen engine module (engine speed, vehicle
speed, the pedal, mass air flow and the ECU's torque for the cross-check, plus air pressure and
outside temperature every few seconds) and saves every full-throttle pull (pedal over 90 %,
2 s before, 1 s after) to a session in Datalogs/Dyno (engine_<date>_<time>.csv, Recent logs mode "dyno").

Engine speed sits in many measuring groups: a group whose values all come with other groups being
read is left out, and engine speed is taken from every group read (aliases), so it's read about 20
times a second. The CSV itself is written by the Logger's DatazapWriter class (shared format)."""
import time

from PySide6.QtCore import QObject, Signal

from . import recent_logs
from .log_writer import DatazapWriter, csv_number

VALUES = ("engine.rpm", "vehicle.speed", "engine.pedal_position", "engine.throttle_position",
          "engine.maf", "engine.torque.actual", "engine.torque.actual.group122",
          "engine.atmospheric_pressure", "ambient.temperature")
SLOW = ("engine.atmospheric_pressure", "ambient.temperature")  # read only every SLOW_MS
SLOW_MS = 3000
PEDAL = 90.0             # % pedal (or throttle): a pull
PRE_S, POST_S = 2.0, 1.0


def pick_values(catalog):
    """(the catalog values a session reads, "") or (None, why not)."""
    have = {v["id"]: v for v in catalog or ()}
    if "engine.rpm" not in have or "vehicle.speed" not in have or \
            not ("engine.pedal_position" in have or "engine.throttle_position" in have):
        return None, ("This module doesn't report what the dyno needs (engine speed, vehicle speed and the "
                      "pedal). Choose the engine on the Connect page.")
    wanted = [i for i in VALUES if i in have]
    if "engine.pedal_position" in have:
        wanted = [i for i in wanted if i != "engine.throttle_position"]
    if "engine.torque.actual" in wanted:
        wanted = [i for i in wanted if i != "engine.torque.actual.group122"]
    return [have[i] for i in wanted], ""


def plan(values):
    """(groups to read, {group: period ms} for the slow ones, {alias key: main key}, {alias key: unit}).
    Leaves out groups whose values all come with other full-rate groups (engine speed's group 1), and
    takes a value from every other full-rate group read that carries it too."""
    fast = {v["group"] for v in values if v["id"] not in SLOW}
    rates = {str(v["group"]): SLOW_MS for v in values if v["id"] in SLOW and v["group"] not in fast}
    groups = sorted({v["group"] for v in values})
    for g in list(groups):
        others = [x for x in groups if x != g and str(x) not in rates]
        mine = [v for v in values if v["group"] == g]
        if mine and all(any(lg in others for lg, _f in (v.get("locations") or [])) for v in mine):
            groups.remove(g)
    main = {f"{v['group']}.{v['field']}" for v in values}
    aliases, units = {}, {}
    for v in values:
        for g, f in (v.get("locations") or [])[1:]:
            key = f"{g}.{f}"
            if g in groups and str(g) not in rates and key not in main:
                aliases.setdefault(key, f"{v['group']}.{v['field']}")
                units.setdefault(key, (v.get("unit") or "").strip())
    return groups, rates, aliases, units


class DynoRecorder(QObject):
    """start() / stop(); signals: started(path), finished(path or None, rows, text, level),
    message(text, level). running, samples, errors, started_at, writer for the status line."""
    started = Signal(object)
    finished = Signal(object, int, str, str)
    message = Signal(str, str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.running = False
        self.writer = None
        self.samples = self.errors = 0
        self.started_at = 0.0
        self._catalogs = {}   # module -> catalog values
        self._loading = False
        state.sample.connect(self._on_sample)

    # --- starting ---

    def start(self):
        if self.running or self._loading:
            return
        family = self.state.family
        if not self.state.adapter:
            self.message.emit("Connect an interface first (Connect page).", "warning")
            return
        if not (family and family.get("can_log")):
            self.message.emit("Choose the engine on the Connect page first: the dyno reads its engine speed, "
                              "vehicle speed and pedal.", "warning")
            return
        if self.state.busy:
            self.message.emit("Something else is using the module (a Logger recording, Live View or another "
                              "job). Stop it first.", "warning")
            return
        module = family["module"]
        if module in self._catalogs:
            self._begin(self._catalogs[module])
            return
        self._loading = True

        def loaded(values):
            self._loading = False
            self._catalogs[module] = values or []
            self._begin(self._catalogs[module])

        def failed(error):
            self._loading = False
            self.message.emit("Couldn't load the engine's values: "
                              + (error.get("message", str(error)) if isinstance(error, dict) else str(error)), "error")
        self.state.offline("catalog", self.state.catalog_params(module), loaded, failed)

    def _begin(self, catalog):
        values, why = pick_values(catalog)
        if values is None:
            self.message.emit(why, "warning")
            return
        groups, rates, self.aliases, self.alias_units = plan(values)
        self.columns = [(f"{v['group']}.{v['field']}", v["name"]) for v in values]
        self.logged = {key for key, _ in self.columns}
        gate = next(f"{v['group']}.{v['field']}" for v in values
                    if v["id"] in ("engine.pedal_position", "engine.throttle_position"))

        def floored(latest, key=gate):
            value = latest.get(key)
            return isinstance(value, (int, float)) and value > PEDAL
        family = self.state.family or {}
        path = recent_logs.new_log(family.get("name", ""), vin=self.state.vin(), mode="dyno")
        self.writer = DatazapWriter(path, self.columns, groups, mode="trigger", trigger=floored,
                                    pre_s=PRE_S, post_s=POST_S)
        self.samples = self.errors = 0
        self.started_at = time.time()
        self.running = True  # before run(): a job that can't start reports through _failed at once
        ok = self.state.run("log", {"groups": groups, "interval_ms": 0, "group_periods": rates},
                            self._done, self._failed)
        if ok:
            self.started.emit(path)
        else:
            self.running = False

    def stop(self):
        if self.running:
            self.state.stop()

    # --- readings ---

    def _on_sample(self, sample):
        if not self.running or self.writer is None:
            return
        self.samples += 1
        if sample.get("error"):
            self.errors += 1
            return
        group = sample["group"]
        values, units = {}, {}
        for field in sample.get("fields") or ():
            key = f"{group}.{field['index']}"
            unit = (field.get("unit") or "").strip()
            alias = self.aliases.get(key)
            if alias is not None:
                main_unit = self.writer.units.get(alias)
                expected = self.alias_units.get(key, "")
                if (main_unit is not None and unit != main_unit) or (expected and unit != expected):
                    continue  # not the same reading there (another unit): don't mix the two
                key = alias
            if key in self.logged:
                values[key] = csv_number(field)
                units[key] = field.get("unit", "")
        self.writer.update(group, values, units, sample.get("wall_time", time.time()) - self.started_at)

    # --- ending ---

    def _end(self):
        writer = self.writer
        rows = writer.rows if writer else 0
        if writer:
            writer.close()
            span = (writer.last_t - writer.first_t) if writer.first_t is not None else 0.0
            recent_logs.finish(writer.path, rows, span)
            if rows:
                recent_logs.prune(protect=[writer.path])
        self.running = False
        return writer, rows

    def _done(self, result):
        writer, rows = self._end()
        error = result.get("error") if isinstance(result, dict) else None
        if error:
            text, level = ("Recording stopped: " + str(error)
                           + (" The pulls recorded before that are kept." if rows else "")), "error"
        elif rows:
            text, level = "", "ok"
        elif writer is not None and writer.ready:
            text, level = (f"Stopped. No full-throttle pull happened (pedal over {PEDAL:.0f} %), so nothing "
                           "was recorded."), "warning"
        else:
            text, level = "Stopped before every value had been read once, so nothing was recorded.", "warning"
        self.finished.emit(writer.path if writer is not None and rows else None, rows, text, level)

    def _failed(self, error):
        writer, rows = self._end()
        text = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        self.finished.emit(writer.path if writer is not None and rows else None, rows,
                           text + (" The pulls recorded before that are kept." if rows else ""), "error")
