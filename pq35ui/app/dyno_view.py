"""Analyze's Dyno view: horsepower and torque against engine speed from full-throttle pulls in
one gear (see dyno.py for the maths), plus the car profile (weight, drag, tyres) it needs,
saved per VIN and optionally filled in by the user's AI."""
import json
import re

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import dyno, graphstyle, log_export, theme
from . import units as unitconv
from .popups import MessageBox, OverlayDialog
from .widgets import muted

SETTING = "dyno"


def _opts(settings):
    o = dict(settings.get(SETTING) or {})
    o.setdefault("cars", {})
    o.setdefault("mode", "wheel")
    o.setdefault("correction", "sae")
    o.setdefault("smoothing", "normal")
    o.setdefault("overlay", True)
    return o


def car_key(vin):
    return vin or "default"


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


def scan_facts(state):
    """What Connect's scan says about the drivetrain: {"awd": bool|None, "dsg": bool|None}."""
    entries = (getattr(state, "last_scan", None) or {}).get("result") or [] if state else []
    if not entries:
        return {"awd": None, "dsg": None}
    ok = {e.get("family") for e in entries if e.get("status") == "ok"}
    return {"awd": any("haldex" in (f or "") for f in ok), "dsg": "transmission" in ok}


def guess_car(vin, facts=None):
    """A car profile from the VIN (and the scan) alone, or None for a car the table doesn't know."""
    from .vehicle import decode
    d = decode(vin) if vin else {}
    spec = MODEL_SPECS.get((vin or "")[6:8])
    if not spec:
        return None
    name, curb, cd, area, tyre = spec
    facts = facts or {}
    awd = bool(facts.get("awd"))
    car = dict(dyno.DEFAULT_CAR, curb_kg=curb + (AWD_EXTRA_KG if awd else 0.0), cd=cd, area_m2=area, tyre=tyre,
               loss_pct=19.0 if awd else 15.0 if facts.get("dsg") is not False else 13.0)
    year = d.get("year")
    car["label"] = " ".join(str(x) for x in (year, name, "AWD" if awd else "") if x)
    car["notes"] = ("Filled in automatically: typical figures for this model"
                    + (" with all-wheel drive (Haldex found)" if awd else "") + ".")
    car["source"] = "built-in"
    return car


def load_car(settings, vin, state=None):
    """This car's profile: saved for the VIN, else typical figures from the VIN, else the no-VIN
    one, else the defaults."""
    cars = _opts(settings)["cars"]
    saved = cars.get(car_key(vin))
    if saved:
        return dict(dyno.DEFAULT_CAR, **saved)
    guess = guess_car(vin, scan_facts(state)) if vin else None
    if guess:
        return guess
    return dict(dyno.DEFAULT_CAR, **(cars.get("default") or {}))


def apply_ai(car, data):
    """car updated with the AI's answer (checked, out-of-range numbers ignored): (car, filled keys)."""
    out, filled = dict(car), []
    for key, lo, hi in (("curb_kg", 500, 6000), ("cd", 0.15, 0.8), ("area_m2", 1.2, 4.0), ("loss_pct", 0, 40)):
        try:
            v = float(data[key])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= v <= hi:
            out[key] = v
            filled.append(key)
    if isinstance(data.get("tyre"), str) and dyno.tyre_radius(data["tyre"]) != 0.31:
        out["tyre"] = data["tyre"].strip()
        filled.append("tyre")
    if isinstance(data.get("label"), str) and data["label"].strip():
        out["label"] = data["label"].strip()[:80]
    note = str(data.get("notes") or "").strip()
    out["notes"] = ("AI: " + note) if note else "Filled in by AI."
    return out, filled


def save_car(settings, vin, car):
    o = _opts(settings)
    o["cars"] = dict(o["cars"], **{car_key(vin): car})
    settings.set(SETTING, o)


def imperial(settings):
    return unitconv.system(settings) == "imperial"


def _num(v, nd=0):
    return f"{v:,.{nd}f}".replace("-", "−")


class DynoView(QWidget):
    """page = the Analyze page (times, columns, headers, csv_path, source_path, state)."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.settings = page.state.settings
        self.pulls = []
        self.results = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        o = _opts(self.settings)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.pull_combo = QComboBox()
        self.pull_combo.setMinimumWidth(90)
        self.pull_combo.setToolTip("Which pull to show")
        self.pull_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pull_combo.setMinimumContentsLength(10)
        row.addWidget(self.pull_combo, 3)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Wheel", "wheel")
        self.mode_combo.addItem("Engine (estimate)", "engine")
        self.mode_combo.setItemData(0, "What a chassis dyno shows: power at the wheels", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(1, "Wheel power plus what the rotating parts and drivetrain "
                                       "take (set the loss in the car profile)", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setCurrentIndex(max(self.mode_combo.findData(o["mode"]), 0))
        row.addWidget(self.mode_combo, 2)
        self.corr_combo = QComboBox()
        for i, (key, label, tip) in enumerate(dyno.CORRECTIONS):
            self.corr_combo.addItem(label, key)
            self.corr_combo.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        self.corr_combo.setCurrentIndex(max(self.corr_combo.findData(o["correction"]), 0))
        self.corr_combo.setToolTip("Correct to standard air, like a dyno sheet")
        row.addWidget(self.corr_combo, 1)
        self.smooth_combo = QComboBox()
        for key, label, _h in dyno.SMOOTHING:
            self.smooth_combo.addItem(label, key)
        self.smooth_combo.setCurrentIndex(max(self.smooth_combo.findData(o["smoothing"]), 0))
        self.smooth_combo.setToolTip("Smoothing: how much the curve is smoothed. Normal suits most "
                                     "logs; Strong for few readings a second or a bumpy road")
        row.addWidget(self.smooth_combo, 1)
        self.overlay_check = QCheckBox("Other pulls")
        self.overlay_check.setChecked(bool(o["overlay"]))
        self.overlay_check.setToolTip("Draw the log's other pulls faintly behind the one shown")
        row.addWidget(self.overlay_check)
        lay.addLayout(row)
        for combo in (self.mode_combo, self.corr_combo, self.smooth_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(9 if combo is self.corr_combo else 5)
            combo.currentIndexChanged.connect(self._option_changed)
        self.pull_combo.currentIndexChanged.connect(lambda _: self.draw())
        self.overlay_check.toggled.connect(self._option_changed)

        self.headline = QLabel("")
        self.headline.setObjectName("dynoHeadline")
        self.headline.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.headline)
        self.details = muted("")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.details)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.hide()
        lay.addWidget(self.warn)

        self.plot = pg.PlotWidget()
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        item = self.plot.getPlotItem()
        item.setLabel("bottom", "Engine speed (rpm)")
        item.showAxis("right")
        self.tq_box = pg.ViewBox()
        self.tq_box.setMouseEnabled(x=False, y=False)
        item.scene().addItem(self.tq_box)
        item.getAxis("right").linkToView(self.tq_box)
        self.tq_box.setXLink(item.vb)
        item.vb.sigResized.connect(lambda: self.tq_box.setGeometry(item.vb.sceneBoundingRect()))
        item.vb.setMouseEnabled(x=False, y=False)
        self.hover_line = pg.InfiniteLine(angle=90, movable=False)
        self.hover_line.hide()
        self.plot.addItem(self.hover_line, ignoreBounds=True)
        self.hover = QLabel("")
        self.hover.setObjectName("muted")
        self.hover.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        graphstyle.apply(self.plot, self.settings)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.hover)
        self.empty = muted("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        lay.addWidget(self.empty, 1)
        self.shown = None
        self._retheme()
        theme.on_apply(self._retheme)

    # --- data ---

    def _vin(self):
        path = getattr(self.page, "source_path", None) or getattr(self.page, "csv_path", None)
        vin = log_export.log_vin(path) if path else None
        return vin or self.page.state.vin() or ""

    def car(self):
        return load_car(self.settings, self._vin(), self.page.state)

    def log_changed(self):
        """A log was opened (or reloaded): find its pulls again."""
        self.results = {}
        times, columns = self.page.times, self.page.columns
        self.chans = dyno.find_channels(list(columns or {})) if columns else {}
        self.pulls = dyno.find_pulls(times, columns, self.chans) if times is not None else []
        self._fill_pulls()

    def _fill_pulls(self):
        current = self.pull_combo.currentData()
        self.pull_combo.blockSignals(True)
        self.pull_combo.clear()
        speed_h = self.chans.get("speed") if hasattr(self, "chans") else None
        imp = imperial(self.settings)
        for i, (a, b) in enumerate(self.pulls):
            text = f"Pull {i + 1}"
            if speed_h:
                t = self.page.times
                v = dyno.to_si(self.page.columns[speed_h], speed_h, "speed")
                m = (t >= a) & (t <= b) & np.isfinite(v)
                if m.any():
                    f = 2.23694 if imp else 3.6
                    text += f" · {v[m].min() * f:.0f}–{v[m].max() * f:.0f} {'mph' if imp else 'km/h'}"
            text += f" · {b - a:.1f} s"
            if self.live() and i == len(self.pulls) - 1 and b >= float(self.page.times[-1]) - 0.5:
                text += " · recording"
            self.pull_combo.addItem(text, f"pull:{i}")
        if len(self.pulls) > 1:
            self.pull_combo.addItem(f"Average of all {len(self.pulls)} pulls", "avg")
            self.pull_combo.setItemData(self.pull_combo.count() - 1,
                                        "Pulls both ways on the same road, averaged, cancel out slope and wind",
                                        Qt.ItemDataRole.ToolTipRole)
        rng = self._selection()
        if rng is not None:
            self.pull_combo.addItem(f"Selection · {rng[0]:.1f}–{rng[1]:.1f} s", "sel")
        i = self.pull_combo.findData(current) if current is not None else -1
        if i < 0 and current == "sel" and rng is not None:
            i = self.pull_combo.count() - 1
        self.pull_combo.setCurrentIndex(max(i, 0))
        self.pull_combo.blockSignals(False)

    def _selection(self):
        """The blue Selection band (Stats → Selection), if on: offered as a pull. Zooming doesn't count."""
        page = self.page
        if page.times is None or page.stats_mode.currentData() != "sel":
            return None
        return page._stats_range()

    def range_changed(self):
        """The Selection band moved (or the view was shown): offer it as a pull."""
        if self.page.times is None:
            return
        sel = self._selection()
        if sel != getattr(self, "_last_sel", None) or self.pull_combo.count() == 0:
            self._last_sel = sel
            self._fill_pulls()
        self.draw()

    def live(self):
        return bool(getattr(self.page, "_live", None))

    def live_update(self):
        """Rows were added to a recording being followed: find the pulls again (finished pulls keep
        their cached results; the one in progress is redrawn), and keep showing the newest pull
        while it was the one shown."""
        if self.page.times is None:
            return
        was = self.pull_combo.currentData()
        newest = was is None or (was.startswith("pull:") and int(was[5:]) >= len(self.pulls) - 1)
        self.chans = dyno.find_channels(list(self.page.columns))
        self.pulls = dyno.find_pulls(self.page.times, self.page.columns, self.chans)
        if len(self.results) > 200:
            self.results = {}
        self._fill_pulls()
        if newest and self.pulls:
            self.pull_combo.blockSignals(True)
            self.pull_combo.setCurrentIndex(max(self.pull_combo.findData(f"pull:{len(self.pulls) - 1}"), 0))
            self.pull_combo.blockSignals(False)
        if self.isVisible():
            self.draw()

    def _option_changed(self, *_):
        o = _opts(self.settings)
        o.update(mode=self.mode_combo.currentData(), correction=self.corr_combo.currentData(),
                 smoothing=self.smooth_combo.currentData(), overlay=self.overlay_check.isChecked())
        self.settings.set(SETTING, o)
        self.results = {}
        self.draw()

    def _result(self, pull, car):
        key = (tuple(pull), json.dumps(car, sort_keys=True), self.corr_combo.currentData(),
               self.smooth_combo.currentData())
        if key not in self.results:
            self.results[key] = dyno.analyse(self.page.times, self.page.columns, pull, car,
                                             self.corr_combo.currentData(), self.smooth_combo.currentData(),
                                             self.chans)
        return self.results[key]

    # --- drawing ---

    def _show_empty(self, text):
        self.plot.hide()
        self.hover.hide()
        self.headline.setText("")
        self.details.setText("")
        self.warn.hide()
        self.empty.setText(text)
        self.empty.show()
        self.shown = None

    def draw(self):
        live = getattr(self.page, "_live", None)
        if live and not live.get("started"):  # following a recording that has no rows yet
            self._show_empty("● Recording. Waiting for the first full-throttle pull: floor it in one gear "
                             "(3rd is ideal) and horsepower and torque appear here.")
            return
        if self.page.times is None:
            self._show_empty("● Recording. Waiting for the first full-throttle pull…" if self.live() else
                             "Open a log to see its dyno curve, or record one with the Logger's Dyno mode.")
            return
        if not hasattr(self, "chans"):
            self.log_changed()
        missing = [n for n, k in (("engine speed", "rpm"), ("vehicle speed", "speed")) if not self.chans.get(k)]
        if missing:
            self._show_empty(f"The dyno needs {' and '.join(missing)} in the log.\n\n"
                             "In the Logger, tick engine speed, vehicle speed and the accelerator pedal, then "
                             "record a full-throttle pull in one gear (3rd is ideal) from low rpm to near "
                             "redline, on a flat road.")
            return
        choice = self.pull_combo.currentData()
        if choice is None and self.live():
            self._show_empty("● Recording. Floor it in one gear: horsepower and torque appear here a few "
                             "seconds into the pull.")
            return
        if choice is None:
            self._show_empty("No full-throttle pull found in this log.\n\nA pull is floored in one gear with "
                             "engine speed climbing for at least a few seconds. Or select a stretch on the "
                             "Single graph (Shift-drag) and pick “Selection” here.")
            return
        car = self.car()
        kind, _, num = choice.partition(":")
        arg = int(num) if num else None
        sel = self._selection()
        if kind == "avg" and self.pulls:
            res = dyno.average([self._result(p, car) for p in self.pulls])
        elif kind == "sel" and sel is not None:
            res = self._result(sel, car)
        elif self.pulls:
            kind, arg = "pull", min(arg or 0, len(self.pulls) - 1)
            res = self._result(self.pulls[arg], car)
        else:
            self._show_empty("No full-throttle pull found in this log.")
            return
        if "error" in res:
            self._show_empty(res["error"])
            return
        self.empty.hide()
        self.plot.show()
        self.hover.show()
        self.shown = res
        others = []
        if self.overlay_check.isChecked() and kind != "sel":
            for i, p in enumerate(self.pulls):
                if kind == "pull" and i == arg:
                    continue
                r = self._result(p, car)
                if "error" not in r:
                    others.append(r)
        self._plot(res, others)
        self._texts(res, car, kind)

    def _series(self, res):
        engine = self.mode_combo.currentData() == "engine"
        hp = res["ehp"] if engine else res["whp"]
        tq = res["etq_nm"] if engine else res["wtq_nm"]
        if imperial(self.settings):
            tq = tq * dyno.NM_LBFT
        return hp, tq

    def _plot(self, res, others):
        item = self.plot.getPlotItem()
        for it in list(item.items):
            if it is not self.hover_line:
                item.removeItem(it)
        self.tq_box.clear()
        c = theme.colors()
        hp_col, tq_col = graphstyle.color(0, self.settings), graphstyle.color(1, self.settings)
        w = graphstyle.width(self.settings)
        imp = imperial(self.settings)
        engine = self.mode_combo.currentData() == "engine"
        for r in others:
            hp, tq = self._series(r)
            item.addItem(pg.PlotDataItem(r["rpm"], hp, pen=pg.mkPen(_alpha(hp_col, 70), width=max(w - 0.5, 1))))
            self.tq_box.addItem(pg.PlotDataItem(r["rpm"], tq, pen=pg.mkPen(_alpha(tq_col, 70), width=max(w - 0.5, 1))))
        hp, tq = self._series(res)
        item.addItem(pg.PlotDataItem(res["rpm"], hp, pen=pg.mkPen(hp_col, width=w + 1)))
        self.tq_box.addItem(pg.PlotDataItem(res["rpm"], tq, pen=pg.mkPen(tq_col, width=w + 1)))
        if engine and res.get("ecu_tq_nm") is not None:
            ecu = res["ecu_tq_nm"] * (dyno.NM_LBFT if imp else 1)
            self.tq_box.addItem(pg.PlotDataItem(res["rpm"], ecu, pen=pg.mkPen(tq_col, width=w,
                                                                               style=Qt.PenStyle.DashLine)))
        item.setLabel("left", "Power (whp)" if not engine else "Power (hp, estimate)", color=hp_col)
        item.setLabel("right", f"Torque ({'lb-ft' if imp else 'Nm'})", color=tq_col)
        # Both axes from zero with headroom, ticks lining up roughly like a dyno sheet.
        top = max([float(np.nanmax(self._series(r)[0])) for r in [res] + others] + [1])
        ttop = max([float(np.nanmax(self._series(r)[1])) for r in [res] + others] + [1])
        item.vb.setYRange(0, top * 1.12, padding=0)
        self.tq_box.setYRange(0, ttop * 1.12, padding=0)
        lo = min(r["rpm"][0] for r in [res] + others)
        hi = max(r["rpm"][-1] for r in [res] + others)
        item.vb.setXRange(lo, hi, padding=0.02)
        self.tq_box.setGeometry(item.vb.sceneBoundingRect())
        self.hover_line.setPen(pg.mkPen(c["muted"], width=1))

    def _texts(self, res, car, kind):
        pk = res["peaks"]
        imp = imperial(self.settings)
        engine = self.mode_combo.currentData() == "engine"
        hp, hp_rpm = (pk["ehp"], pk["ehp_rpm"]) if engine else (pk["whp"], pk["whp_rpm"])
        tq, tq_rpm = (pk["etq"], pk["etq_rpm"]) if engine else (pk["wtq"], pk["wtq_rpm"])
        tq_unit = "lb-ft" if imp else "Nm"
        tq = tq * dyno.NM_LBFT if imp else tq
        hp_col, tq_col = graphstyle.color(0, self.settings), graphstyle.color(1, self.settings)
        hp_word = "hp (est.)" if engine else "whp"
        self.headline.setText(
            f"<span style='color:{hp_col}; font-size:17pt; font-weight:800'>{_num(hp)}</span> "
            f"<b>{hp_word}</b> <span style='color:{theme.colors()['muted']}'>@ {_num(hp_rpm)} rpm</span>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:{tq_col}; font-size:17pt; font-weight:800'>{_num(tq)}</span> "
            f"<b>{tq_unit}</b> <span style='color:{theme.colors()['muted']}'>@ {_num(tq_rpm)} rpm</span>")
        ratio = res["kmh_per_1000"]
        gearing = f"{ratio * 0.621371:.1f} mph" if imp else f"{ratio:.1f} km/h"
        air = res["air"]
        temp = f"{air['temp_c'] * 9 / 5 + 32:.0f} °F" if imp else f"{air['temp_c']:.0f} °C"
        press = f"{air['mbar'] * 0.0295300:.2f} inHg" if imp else f"{air['mbar']:.0f} mbar"
        weight = f"{res['mass_kg'] * dyno.KG_LB:,.0f} lb" if imp else f"{res['mass_kg']:,.0f} kg"
        corr = self.corr_combo.currentData()
        corr_txt = "uncorrected" if corr == "none" else f"{self.corr_combo.currentText()} ×{res['cf']:.3f}"
        what = f"average of {res.get('count', 1)} pulls" if kind == "avg" else \
            f"{res['pull'][0]:.1f}–{res['pull'][1]:.1f} s"
        label = car.get("label") or "car profile not set"
        self.details.setText(
            f"{what} · {gearing} per 1,000 rpm (measured) · engine speed read {res['rate_hz']:.1f}×/s · "
            f"air {temp}, {press} ({'from the log' if air['source'] == 'log' else 'partly from the profile' if air['source'] != 'profile' else 'from the car profile'}) · "
            f"{corr_txt} · {weight} · {label}")
        warnings = list(res["warnings"])
        if kind == "avg" and res.get("skipped"):
            n = res["skipped"]
            warnings.append(f"{n} pull{'s' if n > 1 else ''} in another gear left out of the average.")
        if not car.get("label") and self._vin() not in _opts(self.settings)["cars"] and \
                "default" not in _opts(self.settings)["cars"]:
            warnings.insert(0, "Using a typical Golf GTI's weight and drag. Set this car's in the Logger: "
                               "Dyno ▾ → Car (weight matters most: 5% off is 5% off).")
        if engine and res.get("ecu_tq_nm") is not None:
            warnings.append("Dashed line: the engine ECU's own torque estimate, for comparison (on a tuned car "
                            "it often reads wrong).")
        self.warn.setText("\n".join(warnings))
        self.warn.setVisible(bool(warnings))

    def _mouse_moved(self, pos):
        res = self.shown
        vb = self.plot.getPlotItem().vb
        if res is None or not self.plot.sceneBoundingRect().contains(pos):
            return
        x = vb.mapSceneToView(pos).x()
        if not res["rpm"][0] <= x <= res["rpm"][-1]:
            self.hover_line.hide()
            self.hover.setText("")
            return
        hp, tq = self._series(res)
        k = int(np.argmin(np.abs(res["rpm"] - x)))
        self.hover_line.setPos(res["rpm"][k])
        self.hover_line.show()
        unit = "lb-ft" if imperial(self.settings) else "Nm"
        word = "hp" if self.mode_combo.currentData() == "engine" else "whp"
        self.hover.setText(f"{_num(res['rpm'][k])} rpm:  {_num(hp[k])} {word}  ·  {_num(tq[k])} {unit}")

    def _retheme(self):
        c = theme.colors()
        self.warn.setStyleSheet(f"color: {c['warn']};")

    def showEvent(self, event):
        """Coming into view (the Analyze tab opened, or this view picked): catch up on anything
        that happened while hidden, e.g. pulls recorded while you were on the Logger."""
        super().showEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._catch_up)

    def _catch_up(self):
        if not self.isVisible():
            return
        if self.page.times is not None:
            self.live_update() if self.live() else self.draw()
        else:
            self.draw()

    def car_changed(self):
        """The car profile was edited (in the Logger): work everything out again."""
        self.results = {}
        if self.page.times is not None:
            self.draw()


def _alpha(colour, a):
    from PySide6.QtGui import QColor
    q = QColor(colour)
    q.setAlpha(a)
    return q


# --- the car profile dialog --------------------------------------------------------

AI_SYSTEM = (
    "You give vehicle specifications for a virtual dyno. Answer with ONE JSON object and nothing "
    "else, with these keys: label (short name: year, model, engine, gearbox), curb_kg (kerb weight "
    "in kg with fluids, no driver, for this exact model, engine, gearbox and drivetrain), cd (drag "
    "coefficient), area_m2 (frontal area in square metres), tyre (stock tyre size like 225/45R17), "
    "drivetrain (FWD or AWD), loss_pct (typical drivetrain loss in percent for this gearbox and "
    "drivetrain on a chassis dyno), notes (one or two short sentences: where the figures come from and "
    "anything the owner should check, e.g. options that change the weight). Use published "
    "manufacturer figures where you know them; otherwise give your best estimate and say so in notes."
)


def ai_prompt(state, vin, car):
    from .vehicle import decode
    lines = []
    if vin:
        d = decode(vin)
        lines.append(f"VIN: {vin}")
        if d.get("label"):
            lines.append(f"Decoded from the VIN: {d['label']}" + (f", built in {d['plant']}" if d.get("plant") else ""))
    entries = (getattr(state, "last_scan", None) or {}).get("result") or []
    found = []
    for e in entries:
        if e.get("status") != "ok":
            continue
        fam = state.family_by_name(e.get("family")) if hasattr(state, "family_by_name") else None
        ident = e.get("identity") or {}
        part = ident.get("software_part_number") or ident.get("part_number") or ""
        desc = (ident.get("system_desc") or "").replace(" (demo)", "")
        found.append(" ".join(x for x in ((fam or {}).get("label", e.get("family", "")), part, desc) if x))
    if found:
        lines.append("Modules found on the car: " + "; ".join(found))
    if car.get("label"):
        lines.append(f"Described so far as: {car['label']}")
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


class CarDialog(OverlayDialog):
    CARD_ICON = "directions_car"

    def __init__(self, state, vin, car, parent=None):
        super().__init__(parent)
        self.state = state
        self.settings = state.settings
        self.vin = vin
        self.car = dict(car)
        self.req = None
        self.setWindowTitle("Car for the dyno")
        self.setMinimumWidth(520)
        imp = imperial(self.settings)
        self.kg = dyno.KG_LB if imp else 1.0
        wunit = "lb" if imp else "kg"
        lay = QVBoxLayout(self)
        from .vehicle import decode
        who = decode(vin).get("label") if vin else ""
        lay.addWidget(muted((f"{who or 'VIN'} · {vin}" if vin else "No VIN for this log: this profile is used "
                             "for logs without one.") + "  Saved for this car; every pull uses it."))
        form = QFormLayout()
        form.setVerticalSpacing(8)

        def spin(value, lo, hi, decimals, step, suffix=""):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(decimals)
            s.setSingleStep(step)
            s.setValue(value)
            if suffix:
                s.setSuffix(" " + suffix)
            return s

        self.label = QLineEdit(self.car.get("label", ""))
        self.label.setPlaceholderText("e.g. 2008 Golf GTI 2.0 TFSI DSG")
        form.addRow("Car", self.label)
        self.curb = spin(self.car["curb_kg"] * self.kg, 500, 6000, 0, 10, wunit)
        self.curb.setToolTip("Kerb weight: full fluids, no driver. A weighbridge beats any spec sheet.")
        form.addRow("Kerb weight", self.curb)
        self.extra = spin(self.car["extra_kg"] * self.kg, 0, 1000, 0, 5, wunit)
        self.extra.setToolTip("Driver, passengers, cargo, and fuel above what kerb weight includes")
        form.addRow("Driver + load", self.extra)
        self.cd = spin(self.car["cd"], 0.15, 0.8, 3, 0.005)
        self.cd.setToolTip("Drag coefficient (Cd), e.g. 0.32 for a Mk5 Golf GTI")
        form.addRow("Drag coefficient", self.cd)
        self.area = spin(self.car["area_m2"], 1.2, 4.0, 2, 0.01, "m²")
        self.area.setToolTip("Frontal area, about 0.84 × width × height")
        form.addRow("Frontal area", self.area)
        self.crr = spin(self.car["crr"], 0.005, 0.03, 4, 0.001)
        self.crr.setToolTip("Rolling resistance coefficient: 0.010 to 0.015 for road tyres; small effect")
        form.addRow("Rolling resistance", self.crr)
        self.tyre = QLineEdit(self.car.get("tyre", ""))
        self.tyre.setPlaceholderText("225/45R17")
        self.tyre.setToolTip("Only used for the engine estimate (how much the wheels and drivetrain "
                             "soak up); the gearing itself is measured from the log")
        form.addRow("Tyre size", self.tyre)
        self.loss = spin(self.car["loss_pct"], 0, 40, 1, 0.5, "%")
        self.loss.setToolTip("For the engine estimate only. FWD DSG about 13–15%, Haldex AWD about 18–20%")
        form.addRow("Drivetrain loss", self.loss)
        tunit = "°F" if imp else "°C"
        air_c = self.car["air_c"] * 9 / 5 + 32 if imp else self.car["air_c"]
        self.air_t = spin(air_c, -40, 140, 0, 1, tunit)
        self.air_t.setToolTip("Used when the log has no ambient temperature")
        form.addRow("Air temperature", self.air_t)
        self.air_p = spin(self.car["air_mbar"], 600, 1100, 0, 1, "mbar")
        self.air_p.setToolTip("Used when the log has no atmospheric pressure (sea level ≈ 1013)")
        form.addRow("Air pressure", self.air_p)
        lay.addLayout(form)
        self.notes = muted(self.car.get("notes", ""))
        lay.addWidget(self.notes)

        ai_row = QHBoxLayout()
        self.ai_button = QPushButton("Fill in with AI")
        self.ai_button.setToolTip("Ask your AI (Settings → AI) for this car's weight, drag and tyres "
                                  "from its VIN and modules. You can still change every value.")
        self.ai_button.clicked.connect(self._ask_ai)
        ai_row.addWidget(self.ai_button)
        self.ai_status = muted("")
        ai_row.addWidget(self.ai_status, 1)
        lay.addLayout(ai_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.finished.connect(lambda _: self.req.stop() if self.req is not None else None)

    def values(self):
        imp = imperial(self.settings)
        air_c = (self.air_t.value() - 32) * 5 / 9 if imp else self.air_t.value()
        return {
            "label": self.label.text().strip(), "curb_kg": self.curb.value() / self.kg,
            "extra_kg": self.extra.value() / self.kg, "cd": self.cd.value(), "area_m2": self.area.value(),
            "crr": self.crr.value(), "tyre": self.tyre.text().strip() or dyno.DEFAULT_CAR["tyre"],
            "loss_pct": self.loss.value(), "air_c": air_c, "air_mbar": self.air_p.value(),
            "notes": self.notes.text(),
        }

    def _ask_ai(self):
        from . import ai
        if not ai.enabled(self.settings):
            MessageBox.information(self, "AI is off", "Choose an AI in Settings → AI first.")
            return
        provider, kind, *_ = ai.resolved(self.settings)
        if kind == "chat":
            MessageBox.information(self, "Needs an AI that answers in the app",
                                   "“My AI chat in the browser” can't send the answer back. Pick an API key, "
                                   "Claude Code, Gemini CLI, Codex CLI or a local model in Settings → AI.")
            return
        prompt = ai_prompt(self.state, self.vin, self.values())
        if not ai.confirm_send(self, self.settings, AI_SYSTEM + "\n\n" + prompt):
            return
        self.ai_button.setEnabled(False)
        self.ai_status.setText("Asking…")
        self._reply = ""
        self.req = ai.Ask(self.settings, AI_SYSTEM, [{"role": "user", "content": prompt}], "low", self)
        self.req.text.connect(lambda t: setattr(self, "_reply", self._reply + t))
        self.req.done.connect(self._ai_done)
        self.req.failed.connect(self._ai_failed)
        self.req.start()

    def _ai_failed(self, error):
        self.ai_button.setEnabled(True)
        self.ai_status.setText(f"Couldn't fill in: {error}")

    def _ai_done(self):
        self.ai_button.setEnabled(True)
        try:
            data = parse_ai(self._reply)
        except ValueError as e:
            self.ai_status.setText(f"Couldn't read the answer ({e}).")
            return
        car, filled = apply_ai(self.values(), data)
        self.curb.setValue(car["curb_kg"] * self.kg)
        self.cd.setValue(car["cd"])
        self.area.setValue(car["area_m2"])
        self.loss.setValue(car["loss_pct"])
        self.tyre.setText(car["tyre"])
        self.label.setText(car.get("label", ""))
        self.notes.setText(car["notes"])
        self.ai_status.setText("Filled in. Check the values, add your own weight, then Save."
                               if filled else "The answer had no usable numbers.")
