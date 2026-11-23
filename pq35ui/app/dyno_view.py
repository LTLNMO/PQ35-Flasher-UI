"""The dyno sheet: horsepower and torque against engine speed from a log's full-throttle pulls in
one gear (maths in dyno.py), for the Dyno tab. It's handed the log with set_log() and asks
car_for(vin) for the car to work it out for (cars.py profiles)."""
import json

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import dyno, graphstyle, theme
from . import units as unitconv
from .widgets import muted

SETTING = "dyno"


def _opts(settings):
    o = dict(settings.get(SETTING) or {})
    o.setdefault("mode", "wheel")
    o.setdefault("correction", "sae")
    o.setdefault("smoothing", "normal")
    o.setdefault("overlay", True)
    return o


def imperial(settings):
    return unitconv.system(settings) == "imperial"


def _num(v, nd=0):
    return f"{v:,.{nd}f}".replace("-", "−")


class DynoView(QWidget):
    """settings: the app's; car_for(vin) -> (the car for dyno.analyse, whether it's a saved car)."""

    def __init__(self, settings, car_for, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.car_for = car_for
        self.times = None
        self.columns = None
        self.vin = ""
        self.live_state = None   # None, "waiting" (recording, no rows yet) or "recording"
        self.chans = {}
        self.pulls = []
        self.results = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        o = _opts(self.settings)

        row = self.control_row = QHBoxLayout()  # the page may put its sessions toggle first (Mac)
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
        self.check = QLabel("")
        self.check.setWordWrap(True)
        lay.addWidget(self.check)
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

    def set_log(self, times, columns, vin="", live=None, new=True):
        """Show a log: its times and columns (None: nothing to show), the car's VIN, and whether it's a
        recording in progress (live "waiting" / "recording"). new=False: rows were added to the same
        recording (the finished pulls keep their results)."""
        self.times, self.columns, self.vin, self.live_state = times, columns, vin or "", live
        if new:
            self.log_changed()
            self.draw()
        else:
            self.live_update()

    def car(self):
        return self.car_for(self.vin)[0]

    def log_changed(self):
        """A log was opened (or reloaded): find its pulls again."""
        self.results = {}
        times, columns = self.times, self.columns
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
                t = self.times
                v = dyno.to_si(self.columns[speed_h], speed_h, "speed")
                m = (t >= a) & (t <= b) & np.isfinite(v)
                if m.any():
                    f = 2.23694 if imp else 3.6
                    text += f" · {v[m].min() * f:.0f}–{v[m].max() * f:.0f} {'mph' if imp else 'km/h'}"
            text += f" · {b - a:.1f} s"
            if self.live() and i == len(self.pulls) - 1 and b >= float(self.times[-1]) - 0.5:
                text += " · recording"
            else:  # finished pulls: how trustworthy (cached, so cheap after the first time)
                r = self._result((a, b), self.car())
                if "quality" in r:
                    text += f" · {r['quality'][0].lower()}"
            self.pull_combo.addItem(text, f"pull:{i}")
        if len(self.pulls) > 1:
            self.pull_combo.addItem(f"Average of all {len(self.pulls)} pulls", "avg")
            self.pull_combo.setItemData(self.pull_combo.count() - 1,
                                        "Pulls both ways on the same road, averaged, cancel out slope and wind",
                                        Qt.ItemDataRole.ToolTipRole)
        if 2 < len(self.pulls) <= 12:  # back-to-back pulls are usually there and back
            for i in range(len(self.pulls) - 1):
                self.pull_combo.addItem(f"Pulls {i + 1} + {i + 2} averaged", f"pair:{i}")
                self.pull_combo.setItemData(self.pull_combo.count() - 1,
                                            "Two back-to-back pulls, e.g. one each way on the same road: "
                                            "averaging cancels slope and wind", Qt.ItemDataRole.ToolTipRole)
        rng = self._selection()
        if rng is not None:
            self.pull_combo.addItem(f"Selection · {rng[0]:.1f}–{rng[1]:.1f} s", "sel")
        i = self.pull_combo.findData(current) if current is not None else -1
        if i < 0 and current == "sel" and rng is not None:
            i = self.pull_combo.count() - 1
        self.pull_combo.setCurrentIndex(max(i, 0))
        self.pull_combo.blockSignals(False)

    def _selection(self):
        """A hand-picked stretch offered as a pull (none on the Dyno tab)."""
        return None

    def range_changed(self):
        """The Selection band moved (or the view was shown): offer it as a pull."""
        if self.times is None:
            return
        sel = self._selection()
        if sel != getattr(self, "_last_sel", None) or self.pull_combo.count() == 0:
            self._last_sel = sel
            self._fill_pulls()
        self.draw()

    def live(self):
        return bool(self.live_state)

    def live_update(self):
        """Rows were added to a recording being followed: find the pulls again (finished pulls keep
        their cached results; the one in progress is redrawn), and keep showing the newest pull
        while it was the one shown."""
        if self.times is None:
            return
        was = self.pull_combo.currentData()
        newest = was is None or (was.startswith("pull:") and int(was[5:]) >= len(self.pulls) - 1)
        self.chans = dyno.find_channels(list(self.columns))
        self.pulls = dyno.find_pulls(self.times, self.columns, self.chans)
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
            self.results[key] = dyno.analyse(self.times, self.columns, pull, car,
                                             self.corr_combo.currentData(), self.smooth_combo.currentData(),
                                             self.chans)
        return self.results[key]

    # --- drawing ---

    def _show_empty(self, text):
        self.plot.hide()
        self.hover.hide()
        self.headline.setText("")
        self.details.setText("")
        self.check.setText("")
        self.warn.hide()
        self.empty.setText(text)
        self.empty.show()
        self.shown = None

    def draw(self):
        if self.live_state == "waiting":  # recording, no rows yet
            self._show_empty("● Recording. Waiting for the first full-throttle pull: floor it in one gear "
                             "(3rd is ideal) and horsepower and torque appear here.")
            return
        if self.times is None:
            self._show_empty("● Recording. Waiting for the first full-throttle pull…" if self.live() else
                             "Press Record and floor it in one gear, or pick a session on the left (Open… for any log).")
            return
        missing = [n for n, k in (("engine speed", "rpm"), ("vehicle speed", "speed")) if not self.chans.get(k)]
        if missing:
            self._show_empty(f"The dyno needs {' and '.join(missing)} in the log.\n\n"
                             "Record with this tab's Record button (it reads what the dyno needs), flooring it "
                             "in one gear (3rd is ideal) from low rpm to near redline, on a flat road.")
            return
        choice = self.pull_combo.currentData()
        if choice is None and self.live():
            self._show_empty("● Recording. Floor it in one gear: horsepower and torque appear here a few "
                             "seconds into the pull.")
            return
        if choice is None:
            self._show_empty("No full-throttle pull found in this log.\n\nA pull is floored in one gear with "
                             "engine speed climbing for at least a few seconds.")
            return
        car = self.car()
        kind, _, num = choice.partition(":")
        arg = int(num) if num else None
        sel = self._selection()
        if kind == "avg" and self.pulls:
            res = dyno.average([self._result(p, car) for p in self.pulls])
        elif kind == "pair" and arg is not None and arg + 1 < len(self.pulls):
            res = dyno.average([self._result(p, car) for p in self.pulls[arg:arg + 2]])
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
        self._consistency(res, kind)

    def _consistency(self, res, kind):
        """Pulls in the same gear that disagree point at slope, wind or heat soak: say so (and how to
        cancel it out) under the other warnings."""
        if kind not in ("pull", "avg", "pair") or len(self.pulls) < 2:
            return
        car = self.car()
        rs = [self._result(p, car) for p in self.pulls]
        group = dyno.same_gear(rs, res)
        if len(group) < 2:
            return
        spread = dyno.spread_pct(group)
        if spread < 4.0:
            return
        text = (f"This log's pulls in the same gear differ by {spread:.0f}% in peak power. That's usually road "
                "slope or wind (or heat soak after several pulls). Pull once each way on the same stretch and "
                "pick an average.")
        if kind in ("avg", "pair"):
            text = (f"The pulls averaged here differ by {res.get('spread_pct', spread):.0f}% in peak power: "
                    "averaging one each way cancels slope and wind, but not heat soak.")
        lines = [t for t in self.warn.text().split("\n") if t]
        self.warn.setText("\n".join(lines + [text]))
        self.warn.show()

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
        if engine and res.get("maf_hp") is not None:
            item.addItem(pg.PlotDataItem(res["rpm"], res["maf_hp"], pen=pg.mkPen(hp_col, width=w,
                                                                                 style=Qt.PenStyle.DotLine)))
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
            f"<b>{tq_unit}</b> <span style='color:{theme.colors()['muted']}'>@ {_num(tq_rpm)} rpm</span>"
            + self._quality_html(res))
        ratio = res["kmh_per_1000"]
        gearing = f"{ratio * 0.621371:.1f} mph" if imp else f"{ratio:.1f} km/h"
        air = res["air"]
        temp = f"{air['temp_c'] * 9 / 5 + 32:.0f} °F" if imp else f"{air['temp_c']:.0f} °C"
        press = f"{air['mbar'] * 0.0295300:.2f} inHg" if imp else f"{air['mbar']:.0f} mbar"
        weight = f"{res['mass_kg'] * dyno.KG_LB:,.0f} lb" if imp else f"{res['mass_kg']:,.0f} kg"
        corr = self.corr_combo.currentData()
        corr_txt = "uncorrected" if corr == "none" else f"{self.corr_combo.currentText()} ×{res['cf']:.3f}"
        what = f"average of {res['count']} pulls" if kind in ("avg", "pair") and res.get("count", 1) > 1 else \
            f"{res['pull'][0]:.1f}–{res['pull'][1]:.1f} s"
        label = car.get("label") or "car profile not set"
        g = res.get("gear")
        if g and g["used"] == "gearbox":
            gearing = f"{gearing} per 1,000 rpm ({g['box']} gear {g['gear']} + tyre size; speed signal " \
                      f"{g['speed_err_pct']:+.1f}%)"
        elif g and g["used"] == "blend":
            gearing = (f"{gearing} per 1,000 rpm (best estimate: speed signal {g['speed_err_pct']:+.1f}% vs gear "
                       f"{g['gear']} + tyre size, weighted {1 - g['weight']:.0%} / {g['weight']:.0%})")
        elif g:
            gearing = f"{gearing} per 1,000 rpm (measured: gear {g['gear']}, speed signal {g['speed_err_pct']:+.1f}%)"
        else:
            gearing = f"{gearing} per 1,000 rpm (measured, overall {res['overall_ratio']:.2f}:1)"
        self.details.setText(
            f"{what} · {gearing} · engine speed read {res['rate_hz']:.1f}×/s · "
            f"air {temp}, {press} ({'from the ECU' if air['source'] == 'log' else 'partly standard air: not all of it was logged' if air['source'] != 'profile' else 'standard air: not in this log'}) · "
            f"{corr_txt} · {weight} · {label}")
        warnings = list(res["warnings"])
        if kind == "avg" and res.get("skipped"):
            n = res["skipped"]
            warnings.append(f"{n} pull{'s' if n > 1 else ''} left out of the average (another gear, or too short).")
        if not self.car_for(self.vin)[1]:
            warnings.insert(0, "No saved car for this log: using a typical Golf GTI's weight and drag. Pick the "
                               "car above, or add it in Cars… (weight matters most: 5% off is 5% off).")
        self._check_text(res, engine)
        self.warn.setText("\n".join(warnings))
        self.warn.setVisible(bool(warnings))

    def _quality_html(self, res):
        """' · Good pull' after the headline, coloured, with the reasons in the headline's tooltip."""
        q = res.get("quality")
        if not q:
            self.headline.setToolTip("")
            return ""
        label, score, why = q
        c = theme.colors()
        col = {"Good": c["ok"], "Fair": c["warn"]}.get(label, c["err"])
        self.headline.setToolTip(f"Pull quality {score}/100" + (": " + "; ".join(why) if why else
                                                                  ": nothing to fault"))
        return f"&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:{col}; font-weight:700'>● {label} pull</span>"

    def _check_text(self, res, engine):
        """The cross-check line: peak engine power from this dyno, the ECU's torque and the airflow."""
        chk = res.get("check")
        c = theme.colors()
        if not chk:
            self.check.setText("Cross-check: add engine torque and mass air flow to the log (the Dyno preset "
                               "has them) to check every pull against the ECU and the airflow.")
            self.check.setStyleSheet(f"color: {c['muted']};")
            return
        parts = [f"this dyno {_num(chk['ours'])} hp"]
        if chk["ecu"]:
            parts.append(f"ECU torque {_num(chk['ecu'])} hp")
        if chk["maf"]:
            parts.append(f"airflow ~{_num(chk['maf'])} hp")
        worst = chk["worst_pct"]
        if worst <= 8:
            verdict, col = f"✓ agree within {max(worst, 1):.0f}%", c["ok"]
        elif worst <= 15:
            verdict, col = (f"⚠ {worst:.0f}% apart: check the weight, tyre size and speed signal (on a tuned car "
                            "the ECU's torque can read wrong)"), c["warn"]
        else:
            verdict, col = (f"✗ {worst:.0f}% apart: something's off (weight, gear, speed signal, or a pull "
                            "that isn't flat out)"), c["err"]
        legend = "  (dashed: ECU torque · dotted: airflow power)" if engine else ""
        self.check.setText(f"Cross-check, engine peak uncorrected: {' · '.join(parts)}  {verdict}{legend}")
        self.check.setToolTip("Three independent estimates of peak engine power. This dyno's comes from the "
                              "car's acceleration, weight and drag; the ECU's from its own torque model; airflow "
                              f"from mass air flow × {dyno.HP_PER_GS} hp per g/s (good to about ±10%). Close "
                              "agreement means the car profile and the pull are sound.")
        self.check.setStyleSheet(f"color: {col};")

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
        that happened while hidden, e.g. pulls recorded while you were on another tab."""
        super().showEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._catch_up)

    def _catch_up(self):
        if not self.isVisible():
            return
        if self.times is not None:
            self.live_update() if self.live() else self.draw()
        else:
            self.draw()

    def units_changed(self):
        """Metric / imperial was switched in Settings: relabel the pulls and redraw."""
        if self.times is not None:
            self._fill_pulls()
        self.draw()

    # --- export ---

    def can_export(self):
        return self.shown is not None

    def sheet_image(self):
        """The dyno sheet as shown (headline, details, warnings and the graph; not the controls)."""
        top = self.headline.geometry().top()
        bottom = self.plot.geometry().bottom()
        from PySide6.QtCore import QRect
        return self.grab(QRect(0, top, self.width(), bottom - top + 1))

    def export_csv(self, path):
        """The curves shown, one row per rpm step: wheel and engine power and torque, in the units
        chosen in Settings."""
        import csv
        res = self.shown
        imp = imperial(self.settings)
        tq_f, tq_u = (dyno.NM_LBFT, "lb-ft") if imp else (1.0, "Nm")
        cols = [("Engine speed (rpm)", res["rpm"], 1.0), ("Wheel power (whp)", res["whp"], 1.0),
                (f"Wheel torque ({tq_u})", res["wtq_nm"], tq_f), ("Engine power (hp, estimate)", res["ehp"], 1.0),
                (f"Engine torque ({tq_u}, estimate)", res["etq_nm"], tq_f)]
        if res.get("ecu_tq_nm") is not None:
            cols.append((f"ECU torque ({tq_u})", res["ecu_tq_nm"], tq_f))
        if res.get("maf_hp") is not None:
            cols.append(("Airflow power (hp, estimate)", res["maf_hp"], 1.0))
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([name for name, _v, _f in cols])
            for i in range(len(res["rpm"])):
                w.writerow([f"{float(v[i]) * k:.1f}" if np.isfinite(v[i]) else "" for _n, v, k in cols])

    def car_changed(self):
        """The car profile was edited: work everything out again."""
        self.results = {}
        if self.times is not None:
            self.draw()


def _alpha(colour, a):
    from PySide6.QtGui import QColor
    q = QColor(colour)
    q.setAlpha(a)
    return q
