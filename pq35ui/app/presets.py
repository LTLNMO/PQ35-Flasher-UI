"""Channel presets: named sets of ticked channels, shared by Logger and Live View.

Stored per module family under settings "presets:<family>" as {name: [value ids]}."""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QMenu, QPushButton

from .popups import InputDialog, MessageBox

# Built-in presets offered for a module on top of the user's own. Keyed by
# family name. "Integrated Engineering Logger" reads exactly the 27 measuring-block fields Integrated
# Engineering's POWERlink logger reads on the 2.0T engine ECU (groups 1, 2, 3, 4, 20,
# 106, 114, 115, 122). The Standard log keeps the ECU's names; exporting it as an
# Integrated Engineering CSV gives Integrated Engineering's names, quirks included (see log_export.IE_COLUMNS).
BUILTIN_PRESETS = {
    "engine": {
        "Integrated Engineering Logger": [
            "engine.rpm", "engine.load.actual", "engine.injection_time", "engine.maf",
            "engine.throttle_position", "engine.ignition_timing", "engine.terminal30_voltage",
            "engine.coolant_temperature", "engine.intake_temperature",
            "engine.timing_retard.cylinder1", "engine.timing_retard.cylinder2",
            "engine.timing_retard.cylinder3", "engine.timing_retard.cylinder4",
            "engine.fuel_rail.spec", "engine.fuel_rail.actual", "engine.fuel_pump_duty",
            "engine.fuel_temperature.calculated",
            "engine.load.spec", "engine.load.corrected", "engine.n75_duty",
            "engine.boost.spec_absolute", "engine.boost.actual_absolute",
            "engine.torque.transmission_specified", "engine.torque.actual.group122",
            "engine.transmission.status",
            "engine.lambda.control.bank1", "engine.basic_setting.requirements",
        ],
        # Diagnosing timing retard / knock (e.g. EA888 2.0T on a K04 + tune): the retard
        # itself per cylinder, plus every common cause — heat (IAT, coolant), fuelling
        # shortfall (HPFP rail actual vs spec, LPFP duty), overboost (actual vs requested,
        # N75), lean mixture (lambda), and knock-sensor activity. Covers 9 groups.
        "Timing retard / knock": [
            "engine.rpm", "engine.load.actual", "engine.throttle_position", "engine.maf",
            "engine.ignition_timing", "engine.injection_time", "engine.terminal30_voltage",
            "engine.timing_retard.cylinder1", "engine.timing_retard.cylinder2",
            "engine.timing_retard.cylinder3", "engine.timing_retard.cylinder4",
            "engine.knock.cylinder1.sensor_voltage", "engine.knock.cylinder2.sensor_voltage",
            "engine.knock.cylinder3.sensor_voltage", "engine.knock.cylinder4.sensor_voltage",
            "engine.boost.spec_absolute", "engine.boost.actual_absolute", "engine.n75_duty",
            "engine.intake_temperature", "engine.coolant_temperature",
            "engine.fuel_rail.actual", "engine.fuel_rail.spec", "engine.fuel_pump_duty",
            "engine.lambda.control.bank1",
        ],
        # Dyno pulls (the Logger's Dyno mode loads this): only what the virtual dyno needs, so engine
        # speed is read as often as possible. Air pressure and outside temperature are for the
        # standard-air correction; Dyno mode reads their groups only every few seconds.
        "Dyno": [
            "engine.rpm", "vehicle.speed", "engine.pedal_position",
            "engine.atmospheric_pressure", "ambient.temperature",
        ],
        # Diagnosing poor fuel economy: mixture and fuel trims (the main driver of
        # MPG), the operating point (rpm, load, speed, gear, throttle), fuelling
        # (injection, MAF, rail), warm-up state (coolant, intake) and timing.
        "Fuel economy / MPG": [
            "engine.rpm", "engine.load.actual", "vehicle.speed", "engine.selected_gear",
            "engine.throttle_position", "engine.maf", "engine.injection_time",
            "engine.coolant_temperature", "engine.intake_temperature",
            "engine.lambda.control.bank1", "engine.lambda.bank1.actual",
            "engine.lambda.bank1.specified", "engine.lambda.bank1.sensor1.adaptation.idle",
            "engine.lambda.bank1.sensor1.adaptation.partial",
            "engine.fuel_rail.actual", "engine.fuel_rail.spec", "engine.ignition_timing",
        ],
    },
}


class PresetButton(QPushButton):
    """Presets, as on Analyze: a bookmark ▾ button whose menu loads a ready-made (built-in) or
    saved preset, saves the ticked channels as a new one, overwrites one, or manages them.

    get_ids() -> iterable of the currently ticked value ids
    apply_ids(ids) -> tick exactly these ids
    on_loaded(name, builtin) -> optional, called after a preset is loaded"""

    def __init__(self, state, get_ids, apply_ids, parent=None, on_loaded=None):
        super().__init__(parent)
        self.state, self.get_ids, self.apply_ids, self.on_loaded = state, get_ids, apply_ids, on_loaded
        self.setObjectName("iconTool")
        self.setToolTip("Presets: load a ready-made or saved set of channels, or save the ticked ones")
        self.setFixedSize(50, 34)
        self.setIconSize(QSize(36, 22))
        self.clicked.connect(lambda: self.menu().exec(self.mapToGlobal(self.rect().bottomLeft())))
        self._tint()
        from . import theme
        theme.on_apply(self._tint)

    def _tint(self):
        from . import analyze_tools as T, theme
        self.setIcon(T.toolbar_icon("bookmark", theme.colors()["tab_text"], dropdown=True))

    def _key(self):
        family = self.state.family
        return f"presets:{family['name']}" if family else None

    def presets(self):
        key = self._key()
        return dict(self.state.settings.get(key) or {}) if key else {}

    def _store(self, presets):
        key = self._key()
        if key:
            self.state.settings.set(key, presets)

    def _builtins(self):
        family = self.state.family
        return BUILTIN_PRESETS.get(family["name"], {}) if family else {}

    def menu(self):
        from . import analyze_tools as T
        from .smart_presets import icon_for
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        builtins = self._builtins()
        if builtins:
            T.menu_header(menu, "Ready-made")
            for name in sorted(builtins, key=str.lower):
                T.menu_action(menu, f"{name}  ({len(builtins[name])})", icon_for(name),
                              lambda _=False, n=name: self._load(n, True), tip="Built in")
        saved = self.presets()
        T.menu_header(menu, "Your presets")
        for name in sorted(saved, key=str.lower):
            T.menu_action(menu, f"{name}  ({len(saved[name])})", "bookmark",
                          lambda _=False, n=name: self._load(n, False))
        if not saved:
            menu.addAction("None saved yet").setEnabled(False)
        menu.addSeparator()
        have = bool(list(self.get_ids()))
        T.menu_action(menu, "Save preset…", "bookmark_add", self._save, have,
                      tip="Save the ticked channels as a new preset" if have else "Tick some channels first")
        over = T.menu_sub(menu, "Overwrite preset", "save_as")
        # Always opens, saying why when there's nothing to pick (a greyed item explains nothing).
        if not saved:
            over.addAction("No saved presets yet: use Save preset… first").setEnabled(False)
        elif not have:
            over.addAction("Tick some channels first").setEnabled(False)
        for name in sorted(saved, key=str.lower):
            T.menu_action(over, name, "bookmark", lambda _=False, n=name: self._overwrite(n), have)
        T.menu_action(menu, "Manage presets…", "tune", self._manage, tip="Rename or delete your presets")
        return menu

    def _load(self, name, builtin):
        ids = self._builtins().get(name, []) if builtin else self.presets().get(name, [])
        self.apply_ids(set(ids))
        if self.on_loaded:
            self.on_loaded(name, builtin)

    def _save(self):
        ids = sorted(self.get_ids())
        if not self._key() or not ids:
            return
        name, ok = InputDialog.getText(self, "Save preset", "Preset name (e.g. Boost monitoring):")
        name = (name or "").strip()
        if not ok or not name:
            return
        presets = self.presets()
        if name in presets and MessageBox.question(
                self, "Save preset", f"Replace the existing preset “{name}”?") != MessageBox.StandardButton.Yes:
            return
        presets[name] = ids
        self._store(presets)

    def _overwrite(self, name):
        ids = sorted(self.get_ids())
        n = len(ids)
        if MessageBox.question(self, "Overwrite preset",
                               f"Replace “{name}” with the {n} ticked channel{'s' if n != 1 else ''}?"
                               ) != MessageBox.StandardButton.Yes:
            return
        presets = self.presets()
        presets[name] = ids
        self._store(presets)

    def _manage(self):
        from . import analyze_tools as T

        def rename(old, new):
            self._store({(new if k == old else k): v for k, v in self.presets().items()})

        def delete(name):
            presets = self.presets()
            presets.pop(name, None)
            self._store(presets)

        T.manage_presets(self, self.presets, rename, delete,
                         "No presets saved yet.\nTick channels, then Presets → Save preset….",
                         tip=lambda _name, ids: f"{len(ids)} channels")
