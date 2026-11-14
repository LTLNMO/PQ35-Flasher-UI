"""Virtual dyno: wheel (and estimated engine) horsepower and torque from a full-throttle pull
in one gear, worked out like a chassis inertia dyno but with the car's own mass as the drum.

    wheel power = (mass x acceleration + air drag + rolling resistance) x road speed

How it stays accurate:
  * Road speed comes from ENGINE SPEED x the gearing measured in the log itself (road speed /
    rpm over the pull). Engine speed is far finer than the speed reading (whole km/h steps),
    and measuring the ratio means no gear ratios or tyre sizes have to be typed in. A ratio
    that wanders during the pull means clutch slip or a gear change: that's reported.
  * Logs repeat the last reading on every row until a value's group is read again, so only
    the rows where engine speed actually changed are used (the real readings, at the times
    they arrived).
  * Acceleration is the slope of a smooth local curve through those readings (a weighted
    quadratic fitted around each moment), not the difference between neighbouring rows,
    which would multiply every bit of noise.
  * Air density for the drag comes from the logged air pressure and temperature when the log
    has them; the result can be corrected to standard air (SAE J1349, STD or DIN 70020) like
    a real dyno sheet.
  * The engine estimate adds what the rotating parts (engine, gearbox, wheels) soak up while
    they speed up, then the drivetrain loss. Wheel figures match what a chassis dyno shows.

Pure numpy, no Qt: analyse() takes the log's columns and returns plain arrays and numbers."""
import math
import re

import numpy as np

G = 9.80665
HP_W = 745.69987        # watts per mechanical horsepower
NM_LBFT = 0.737562
KG_LB = 2.20462

# A 2008-ish Golf GTI / A3 2.0T: a sensible start until the car profile is filled in.
DEFAULT_CAR = {
    "label": "",
    "curb_kg": 1420.0,       # kerb weight (full fluids and a full tank, no driver)
    "people": 1,             # driver + passengers (PERSON_KG each)
    "cargo_kg": 0.0,
    "fuel_frac": 1.0,        # how full the tank is (kerb weight assumes full)
    "tank_l": 55.0,
    "cd": 0.32,
    "area_m2": 2.22,
    "crr": 0.012,
    "tyre": "225/45R17",
    "loss_pct": 15.0,        # drivetrain loss for the engine estimate (FWD DSG ~13-15, Haldex AWD ~16-19)
    "gearbox": "unknown",    # a key of GEARBOXES, "manual" or "unknown": for the speed-signal check
    "speed_from": "auto",    # "auto": both blended (gearbox known); "log": measured; "gearbox": ratio + tyre
    "air_c": 20.0,           # used when the log has no air temperature
    "air_mbar": 1013.0,      # used when the log has no air pressure
    "notes": "",
}

CORRECTIONS = (
    ("sae", "SAE J1349", "Corrected to 25 °C, 990 mbar dry air (most dyno sheets)"),
    ("std", "STD", "Corrected to 15.6 °C, 1013 mbar (older SAE J607; reads ~4% higher than SAE)"),
    ("din", "DIN 70020", "Corrected to 20 °C, 1013 mbar (European)"),
    ("none", "Uncorrected", "As measured, in the day's air"),
)
SMOOTHING = (("low", "Light", 0.8), ("normal", "Normal", 1.2), ("high", "Strong", 1.6))  # window half-width, s

PERSON_KG = 80.0
FUEL_KG_L = 0.745        # petrol
# Full-throttle crank power per gram a second of air for a turbo petrol running rich (lambda ~0.8):
# the usual rule of thumb (10 hp per lb/min of air is 1.32). Good to about ±10%: a sanity check.
HP_PER_GS = 1.30

# Overall ratios (engine turns per wheel turn) of gearboxes whose ratios are known, to check the
# speed signal against: 6-speed S tronic / DSG 02E as fitted to the 2.0 TFSI (gears 1-4 and 5-6 run
# on final drives 4.118 and 3.043).
GEARBOXES = {
    "dsg6": ("6-speed DSG (02E)", tuple(round(g * f, 3) for g, f in (
        (3.462, 4.118), (2.050, 4.118), (1.300, 4.118), (0.902, 4.118), (0.914, 3.043), (0.756, 3.043)))),
}
# How far each road-speed scale can typically be off (%, one sigma): the ABS speed (its stored tyre
# circumference) and gear ratio + tyre size (wear, pressure, growth at speed). "auto" weights them by
# these; further apart than BLEND_MAX_PCT one of them is plainly wrong, so it isn't blended.
SIGMA_ABS_PCT = 2.0
SIGMA_TYRE_PCT = 1.2
BLEND_MAX_PCT = 4.0
SPEED_FROM = (("auto", "Automatic (best estimate)"), ("log", "Speed signal (measured from the log)"),
              ("gearbox", "Gear ratio + tyre size"))
GEARBOX_CHOICES = (("dsg6", "6-speed DSG (S tronic)"), ("manual", "Manual / other"), ("unknown", "Don't know"))


def car_mass(car):
    """Car + people + cargo, less the fuel missing from a full tank (kg). Profiles saved before
    people / fuel existed have one extra_kg (driver, load and fuel together)."""
    if "people" not in car and "extra_kg" in car:
        return float(car["curb_kg"]) + float(car["extra_kg"])
    full = float(car.get("tank_l", 55.0)) * FUEL_KG_L
    fuel = min(max(float(car.get("fuel_frac", 1.0)), 0.0), 1.0)
    return (float(car["curb_kg"]) + int(car.get("people", 1)) * PERSON_KG + float(car.get("cargo_kg", 0.0))
            - (1 - fuel) * full)


def match_gear(overall, gearbox):
    """(gear number, its overall ratio, speed signal error %) for the gear of a known gearbox nearest
    the measured overall ratio, or None. Error > 0: the speed signal reads high."""
    box = GEARBOXES.get(gearbox)
    if not box or not overall or not math.isfinite(overall):
        return None
    k = int(np.argmin([abs(math.log(overall / g)) for g in box[1]]))
    return k + 1, box[1][k], (box[1][k] / overall - 1) * 100


# --- reading the log ---------------------------------------------------------

def split_header(header):
    """('Vehicle speed', 'km/h') from 'Vehicle speed (km/h)'."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", header or "")
    return (m.group(1), m.group(2)) if m else (header or "", "")


def _find(headers, patterns, avoid=()):
    for pat in patterns:
        for h in headers:
            low = h.lower()
            if re.search(pat, low) and not any(re.search(a, low) for a in avoid):
                return h
    return None


def find_channels(headers):
    """Which columns the dyno uses: {rpm, speed, pedal, throttle, pressure, air_temp, ecu_torque, maf}."""
    hs = [h for h in headers if not h.startswith("ƒ ")]  # skip math channels
    return {
        "rpm": _find(hs, [r"^engine speed\b", r"engine speed", r"^rpm\b", r"\brpm\b"],
                     avoid=[r"specified", r"target", r"idle", r"limit", r"\(spec"]),
        "speed": _find(hs, [r"^vehicle speed\b", r"vehicle speed", r"^speed\b", r"road speed"],
                       avoid=[r"cruise", r"median", r"limit", r"specified"]),
        "pedal": _find(hs, [r"accelerator pedal position(?!.*sensor 2)", r"pedal"],
                       avoid=[r"sensor 2", r"kick", r"alignment"]),
        "throttle": _find(hs, [r"throttle valve angle", r"throttle valve position", r"throttle"],
                          avoid=[r"specified", r"adapt"]),
        "pressure": _find(hs, [r"atmospheric pressure", r"ambient pressure", r"baro"]),
        "air_temp": _find(hs, [r"ambient temperature", r"ambient air temp", r"outside temp"]),
        "ecu_torque": _find(hs, [r"engine torque \(actual", r"^engine torque\b", r"actual torque"],
                            avoid=[r"specified", r"request", r"loss", r"limit"]),
        "maf": _find(hs, [r"mass air ?flow", r"air mass", r"\bmaf\b"],
                     avoid=[r"specified", r"sensor voltage", r"adapt", r"\(v\)$"]),
    }


def to_si(values, header, kind):
    """Convert a column to SI-ish units from its header's unit: speed m/s, pressure Pa, temp °C."""
    unit = split_header(header)[1].strip().lower()
    v = np.asarray(values, dtype=float)
    if kind == "speed":
        return v * 0.44704 if unit == "mph" else v if unit == "m/s" else v / 3.6
    if kind == "pressure":
        factor = {"mbar": 100.0, "hpa": 100.0, "kpa": 1000.0, "psi": 6894.757, "bar": 1e5}.get(unit, 100.0)
        return v * factor
    if kind == "temp":
        return (v - 32.0) * 5 / 9 if unit in ("°f", "f", "degf") else v
    if kind == "airflow":  # g/s
        return v / 3.6 if unit == "kg/h" else v * 7.5599 if unit in ("lb/min", "lbs/min") else v
    return v


def readings(times, values):
    """(t, v) of the rows where the value actually changed (the real readings): logs repeat the
    last reading on every row until its group is read again."""
    t, v = np.asarray(times, float), np.asarray(values, float)
    ok = np.isfinite(t) & np.isfinite(v)
    t, v = t[ok], v[ok]
    if len(v) < 2:
        return t, v
    keep = np.r_[True, np.diff(v) != 0]
    return t[keep], v[keep]


# --- finding the pulls ---------------------------------------------------------

def find_pulls(times, columns, chans=None, min_s=2.5, min_rpm_span=1200.0):
    """[(t0, t1)] of full-throttle stretches where engine speed climbs steadily (one gear).
    Uses the pedal (or throttle) when logged; a shift (engine speed falling) ends a pull."""
    chans = chans or find_channels(list(columns))
    rpm_h = chans.get("rpm")
    if rpm_h is None:
        return []
    t = np.asarray(times, float)
    rpm = np.asarray(columns[rpm_h], float)
    gate_h = chans.get("pedal") or chans.get("throttle")
    if gate_h is not None:
        gate = np.asarray(columns[gate_h], float)
        top = np.nanmax(gate) if np.isfinite(gate).any() else 0
        if split_header(gate_h)[1].strip() == "%" and top < 70:
            return []  # never floored: part-throttle runs aren't dyno pulls
        wide_open = gate >= max(top * 0.85, 1.0) if top > 0 else np.zeros(len(t), bool)
    else:  # no pedal: anywhere engine speed is rising fast enough
        rt, rv = readings(t, rpm)
        if len(rt) < 4:
            return []
        slope = np.gradient(rv, rt)
        wide_open = np.interp(t, rt, slope) > 150.0  # rpm/s
    pulls = []
    i, n = 0, len(t)
    while i < n:
        if not wide_open[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and wide_open[j + 1]:
            j += 1
        pulls.extend(_split_at_shifts(t[i:j + 1], rpm[i:j + 1]))
        i = j + 1
    return [(a, b) for a, b in pulls if b - a >= min_s and _span(t, rpm, a, b) >= min_rpm_span]


def _span(t, rpm, a, b):
    m = (t >= a) & (t <= b) & np.isfinite(rpm)
    return float(np.ptp(rpm[m])) if m.sum() > 1 else 0.0


def _split_at_shifts(t, rpm):
    """Pieces of a wide-open stretch between gear changes, each trimmed to where engine speed
    starts climbing and to its highest point."""
    rt, rv = readings(t, rpm)
    if len(rt) < 4:
        return []
    out, start = [], 0
    peak = 0
    for k in range(1, len(rv)):
        if rv[k] > rv[peak]:
            peak = k
        if rv[peak] - rv[k] > 250.0:  # fell well below its peak: a shift (or lift)
            out.append((start, peak))
            start = peak = k
    out.append((start, peak))
    pieces = []
    for s, e in out:
        if e - s < 3:
            continue
        # start from the lowest point before the peak (after a shift the revs are still falling
        # when the piece begins), then where it really starts to climb (after tip-in / spool) ...
        s += int(np.argmin(rv[s:e + 1]))
        if e - s < 3:
            continue
        base = rv[s]
        s2 = s
        while s2 < e and rv[s2] < base + 40.0:
            s2 += 1
        pieces.append((float(rt[max(s2 - 1, s)]), float(rt[e])))
    return pieces


# --- the maths ------------------------------------------------------------------

MIN_POINTS = 10  # a fit window holds at least this many readings (tested on the demo car) ...
MAX_HALF = 2.0   # ... unless that would stretch it past this many seconds each side
FEW_POINTS = 5   # (it always gets at least this many)


def local_fit(t, y, grid, half):
    """Value and slope at each grid time from a tricube-weighted quadratic through the readings
    within `half` seconds, widened where needed to hold MIN_POINTS readings (but no wider than
    MAX_HALF unless it has fewer than FEW_POINTS)."""
    val = np.full(len(grid), np.nan)
    der = np.full(len(grid), np.nan)
    if len(t) < 4:
        return val, der
    for k, g in enumerate(grid):
        d = np.abs(t - g)
        nth = lambda n: float(np.partition(d, min(n, len(d) - 1))[min(n, len(d) - 1)])
        h = max(half, min(nth(MIN_POINTS), MAX_HALF), nth(FEW_POINTS))
        m = d <= h
        if m.sum() < 4:
            continue
        x = t[m] - g
        w = (1 - (np.abs(x) / (h * 1.0001)) ** 3) ** 3
        A = np.stack([np.ones_like(x), x, x * x], axis=1)
        sw = np.sqrt(w)
        try:
            coef, *_ = np.linalg.lstsq(A * sw[:, None], y[m] * sw, rcond=None)
        except np.linalg.LinAlgError:
            continue
        val[k], der[k] = coef[0], coef[1]
    return val, der


def gearing(st, sv, rt, rv, half):
    """(ratio m/s per rpm, speed lag s, fitted speeds) from speed readings (st, sv) against engine
    speed readings (rt, rv). The speed signal usually trails engine speed a little (it comes from
    the ABS over CAN, filtered), which a plain speed/rpm fit reads as too low a ratio, and power
    goes with the ratio squared (0.1 s is about 2% low). So it fits
        speed = ratio x (rpm - lag x rpm climb rate)
    and falls back to the plain fit if the lag comes out implausible."""
    rpm_at = np.interp(st, rt, rv)
    plain = float(np.sum(sv * rpm_at) / np.sum(rpm_at * rpm_at))
    _v, climb = local_fit(rt, rv, st, half)
    ok = np.isfinite(climb)
    if ok.sum() >= 6 and np.ptp(rpm_at[ok]) > 500:
        coef, *_ = np.linalg.lstsq(np.c_[rpm_at[ok], climb[ok]], sv[ok], rcond=None)
        ratio, lag = float(coef[0]), float(-coef[1] / coef[0]) if coef[0] else 0.0
        if ratio > 0 and -0.05 <= lag <= 0.5 and abs(ratio / plain - 1) < 0.05:
            return ratio, lag, ratio * (rpm_at - lag * np.where(ok, climb, 0.0))
    return plain, 0.0, plain * rpm_at


TYRE_RE = r"\s*(\d{3})\s*/\s*(\d{2})\s*[A-Za-z]*\s*(\d{2})"


def tyre_ok(tyre):
    return bool(re.match(TYRE_RE, tyre or ""))


def tyre_diameter_mm(tyre):
    """Unloaded diameter from '225/45R17': rim + two sidewalls (width x profile %)."""
    width, aspect, rim = (int(x) for x in re.match(TYRE_RE, tyre).groups())
    return rim * 25.4 + 2 * width * aspect / 100.0


def tyre_radius(tyre):
    """Rolling radius in metres from '225/45R17' (about 3% under the unloaded radius)."""
    if not tyre_ok(tyre):
        return 0.31
    return tyre_diameter_mm(tyre) / 2 / 1000.0 * 0.97


def correction_factor(kind, temp_c, pressure_pa):
    """Standard-air correction multiplier (humidity ignored: dry air assumed)."""
    p = pressure_pa / 100.0  # mbar
    tk = temp_c + 273.15
    if kind == "sae":
        return 1.180 * (990.0 / p) * math.sqrt(tk / 298.15) - 0.18
    if kind == "std":
        return (1013.25 / p) * math.sqrt(tk / 288.71)
    if kind == "din":
        return (1013.0 / p) * math.sqrt(tk / 293.15)
    return 1.0


def _mean_in(times, columns, header, kind, a, b):
    if not header:
        return None
    t = np.asarray(times, float)
    v = to_si(columns[header], header, kind)
    m = (t >= a) & (t <= b) & np.isfinite(v)
    if not m.any():
        m = np.isfinite(v)
    return float(np.mean(v[m])) if m.any() else None


def analyse(times, columns, pull, car=None, correction="sae", smoothing="normal", chans=None, step_rpm=50.0):
    """Dyno result for one pull (t0, t1). Returns a dict:
      rpm[]                      engine speed grid
      whp[], wtq_nm[]            wheel power / torque at engine rpm (chassis-dyno style)
      ehp[], etq_nm[]            engine estimate (rotating parts + drivetrain loss)
      ecu_tq_nm[] or None        the ECU's own torque reading on the same grid
      peaks {whp, whp_rpm, wtq, wtq_rpm, ehp, ehp_rpm, etq, etq_rpm}
      kmh_per_1000, slip_pct, rate_hz, mass_kg, rot_factor, air {temp_c, mbar, source}, cf
      warnings [str]
    or {"error": "..."}."""
    car = dict(DEFAULT_CAR, **(car or {}))
    chans = chans or find_channels(list(columns))
    t_all = np.asarray(times, float)
    a, b = pull
    rpm_h, spd_h = chans.get("rpm"), chans.get("speed")
    if rpm_h is None:
        return {"error": "This log has no engine speed, which the dyno needs."}
    if spd_h is None:
        return {"error": "This log has no vehicle speed, which the dyno needs to measure the gearing."}
    warnings = []
    pad = 0.0  # only readings inside the pull: just after it the driver lifts or shifts
    m = (t_all >= a - pad) & (t_all <= b + pad)
    rt, rv = readings(t_all[m], np.asarray(columns[rpm_h], float)[m])
    st, sv = readings(t_all[m], to_si(np.asarray(columns[spd_h], float)[m], spd_h, "speed"))
    inside = (rt >= a) & (rt <= b)
    if inside.sum() < 6:
        return {"error": "Too few engine speed readings in this pull (log fewer groups to read faster)."}
    rate = (inside.sum() - 1) / max(rt[inside][-1] - rt[inside][0], 1e-6)
    if rate < 4:
        warnings.append(f"Engine speed was read only {rate:.1f} times a second. For a dyno run, log just "
                        "engine speed, vehicle speed and the accelerator pedal (plus atmospheric pressure "
                        "and ambient temperature at a slow group rate) so it's read much faster.")

    # Gearing measured from the log: road speed per rpm, using the speed readings against engine
    # speed at the same moments.
    sm = (st >= a) & (st <= b) & (sv > 2.0)
    if sm.sum() < 3:
        return {"error": "Too few vehicle speed readings in this pull to measure the gearing."}
    half = dict((k, h) for k, _l, h in SMOOTHING).get(smoothing, 1.2)
    ratio, lag, fitted = gearing(st[sm], sv[sm], rt, rv, half)
    # Speed readings are whole km/h: allow for that before calling it slip.
    resid = sv[sm] - fitted
    quant = 1.0 / 3.6 / math.sqrt(12)
    slip = float(np.sqrt(max(np.mean(resid ** 2) - quant ** 2, 0.0)) / max(np.mean(sv[sm]), 1e-6) * 100)
    if slip > 3.0:
        warnings.append(f"Road speed and engine speed disagree by about {slip:.0f}% through the pull: "
                        "clutch slip, wheelspin or a gear change. Treat this pull's numbers with care.")

    radius = tyre_radius(car.get("tyre"))
    measured_overall = (2 * math.pi / 60.0) / (ratio / radius)  # engine turns per wheel turn
    gear = None
    match = match_gear(measured_overall, car.get("gearbox"))
    if match and abs(match[2]) < 10:  # further off than that it's a different gearbox (or tyre)
        n, expect, err = match
        box = GEARBOXES[car["gearbox"]][0]
        mode = car.get("speed_from", "auto")
        geared = (2 * math.pi / 60.0) * radius / expect  # m/s per rpm from the gear ratio + tyre
        gear = {"gear": n, "expected": expect, "measured": measured_overall, "speed_err_pct": err,
                "box": box, "used": "log", "weight": 0.0}
        if mode == "gearbox":
            ratio, lag = geared, 0.0
            gear.update(used="gearbox", weight=1.0)
        elif mode == "auto" and abs(err) <= BLEND_MAX_PCT:
            w = SIGMA_ABS_PCT ** 2 / (SIGMA_ABS_PCT ** 2 + SIGMA_TYRE_PCT ** 2)  # inverse-variance weight
            ratio = math.exp(w * math.log(geared) + (1 - w) * math.log(ratio))
            gear.update(used="blend", weight=w)
        elif mode == "auto":
            warnings.append(
                f"The speed signal and {box} gear {n} on {car.get('tyre')} tyres disagree by {abs(err):.1f}%, "
                "too much to blend, so the measured speed is used. Check the tyre size in the car profile "
                "(Cars…).")
        elif abs(err) >= 2.5:
            warnings.append(
                f"The speed signal reads {abs(err):.1f}% {'high' if err > 0 else 'low'} against {box} gear {n} "
                f"on {car.get('tyre')} tyres, which makes power about {2 * abs(err):.0f}% "
                f"{'high' if err > 0 else 'low'}. If the tyre size is right, set Speed from → Gear ratio in the "
                "car profile (Cars…).")

    grid = np.arange(a, b, 0.025)
    if len(grid) < 8:
        return {"error": "This pull is too short."}
    rpm_s, drpm = local_fit(rt, rv, grid, half)
    ok = np.isfinite(rpm_s) & np.isfinite(drpm)
    # Trim the ends where the fit leans on one side only.
    trim = max(half * 0.4, 1.0 / rate)  # at least one reading's gap
    edge = (grid >= a + trim) & (grid <= b - trim)
    ok &= edge
    if ok.sum() < 8:
        return {"error": "Not enough readings to draw a curve for this pull."}
    t_g, rpm_g, drpm_g = grid[ok], rpm_s[ok], drpm[ok]
    v = ratio * rpm_g                 # m/s
    acc = ratio * drpm_g              # m/s²

    # Air: logged if possible, else the profile's numbers.
    press = _mean_in(t_all, columns, chans.get("pressure"), "pressure", a, b)
    temp = _mean_in(t_all, columns, chans.get("air_temp"), "temp", a, b)
    if press is not None and not 60000 < press < 110000:
        press = None
    if temp is not None and not -40 < temp < 60:
        temp = None
    source = "log" if press is not None and temp is not None else \
        "log + profile" if press is not None or temp is not None else "profile"
    # Not logged: standard air (the profile no longer asks; older saved profiles' values are ignored)
    press = press if press is not None else float(DEFAULT_CAR["air_mbar"]) * 100.0
    temp = temp if temp is not None else float(DEFAULT_CAR["air_c"])
    rho = press / (287.05 * (temp + 273.15))

    mass = car_mass(car)
    overall = (2 * math.pi / 60.0) / (ratio / radius)  # engine turns per wheel turn
    rot = 1.04 + 0.0025 * overall ** 2                # Gillespie: rotating parts as extra mass
    drag = 0.5 * rho * float(car["cd"]) * float(car["area_m2"]) * v * v
    roll = float(car["crr"]) * mass * G
    p_wheel = (mass * acc + drag + roll) * v
    p_engine = (mass * rot * acc + drag + roll) * v / max(1 - float(car["loss_pct"]) / 100.0, 0.3)
    cf = correction_factor(correction, temp, press)
    p_wheel, p_engine = p_wheel * cf, p_engine * cf

    # Onto an rpm grid (keeping only where engine speed rises, so rpm is one-to-one with time).
    rising = drpm_g > 0
    rpm_r, pw, pe, tr = rpm_g[rising], p_wheel[rising], p_engine[rising], t_g[rising]
    if len(rpm_r) < 8:
        return {"error": "Engine speed didn't climb steadily in this pull."}
    order = np.argsort(rpm_r)
    rpm_r, pw, pe, tr = rpm_r[order], pw[order], pe[order], tr[order]
    lo = math.ceil(rpm_r[0] / step_rpm) * step_rpm
    hi = math.floor(rpm_r[-1] / step_rpm) * step_rpm
    if hi - lo < step_rpm * 4:
        return {"error": "The pull covers too few rpm."}
    rpm_axis = np.arange(lo, hi + step_rpm / 2, step_rpm)
    whp = np.interp(rpm_axis, rpm_r, pw) / HP_W
    ehp = np.interp(rpm_axis, rpm_r, pe) / HP_W
    omega = rpm_axis * 2 * math.pi / 60.0
    wtq = whp * HP_W / omega
    etq = ehp * HP_W / omega

    def on_grid(header, kind=None):
        """Another logged value at each moment of the pull, then onto the rpm grid."""
        if not header:
            return None
        vals = np.asarray(columns[header], float)[m]
        et, ev = readings(t_all[m], to_si(vals, header, kind) if kind else vals)
        if len(et) < 4:
            return None
        return np.interp(rpm_axis, rpm_r, np.interp(tr, et, ev))

    ecu = on_grid(chans.get("ecu_torque"))
    maf = on_grid(chans.get("maf"), "airflow")
    maf_hp = maf * HP_PER_GS * cf if maf is not None else None  # corrected like ours, to draw alongside

    def peak(y):
        k = int(np.nanargmax(y))
        return float(y[k]), float(rpm_axis[k])

    pk = {}
    for key, arr in (("whp", whp), ("wtq", wtq), ("ehp", ehp), ("etq", etq)):
        pk[key], pk[key + "_rpm"] = peak(arr)
    cut_off = pk["whp_rpm"] >= rpm_axis[-1] - 2 * step_rpm
    if cut_off:
        warnings.append(f"Power was still climbing when this pull ended ({rpm_axis[-1]:,.0f} rpm), so the real "
                        "peak is probably higher: hold it flat closer to redline.")
    out = {
        "rpm": rpm_axis, "whp": whp, "wtq_nm": wtq, "ehp": ehp, "etq_nm": etq, "ecu_tq_nm": ecu,
        "maf_hp": maf_hp, "check": cross_check(rpm_axis, ehp / cf, ecu, maf_hp / cf if maf_hp is not None else None),
        "gear": gear,
        "peaks": pk, "kmh_per_1000": ratio * 3.6 * 1000, "slip_pct": slip, "rate_hz": float(rate),
        "speed_lag_s": lag,
        "mass_kg": mass, "speed_from": gear["used"] if gear else "log", "rot_factor": rot, "overall_ratio": overall, "cf": cf,
        "air": {"temp_c": temp, "mbar": press / 100.0, "source": source, "density": rho},
        "pull": (float(a), float(b)), "rpm_range": (float(rpm_axis[0]), float(rpm_axis[-1])),
        "warnings": warnings, "cut_off": cut_off,
    }
    out["quality"] = quality(out)
    return out


def quality(res):
    """(label Good / Fair / Poor, score 0-100, [reasons]) for how far a result can be trusted."""
    score, why = 100, []

    def ding(points, text):
        nonlocal score
        score -= points
        why.append(text)
    rate = res.get("rate_hz", 0)
    if rate < 6:
        ding(30, f"engine speed read only {rate:.1f}×/s")
    elif rate < 12:
        ding(10, f"engine speed read {rate:.0f}×/s (more is better)")
    slip = res.get("slip_pct", 0)
    if slip > 3:
        ding(40, f"{slip:.0f}% slip or wheelspin")
    elif slip > 1.5:
        ding(15, f"{slip:.1f}% slip")
    span = res["rpm"][-1] - res["rpm"][0]
    if span < 2000:
        ding(25, f"only {span:,.0f} rpm covered")
    elif span < 3000:
        ding(10, f"{span:,.0f} rpm covered (3,000+ is better)")
    if res.get("cut_off"):
        ding(15, "cut off before peak power")
    chk = res.get("check")
    if chk and chk["worst_pct"] > 15:
        ding(40, f"cross-check {chk['worst_pct']:.0f}% apart")
    elif chk and chk["worst_pct"] > 8:
        ding(20, f"cross-check {chk['worst_pct']:.0f}% apart")
    g = res.get("gear")
    if g and abs(g["speed_err_pct"]) > BLEND_MAX_PCT:
        ding(20, f"speed signal and gear ratio {abs(g['speed_err_pct']):.0f}% apart")
    elif g and g["used"] == "log" and abs(g["speed_err_pct"]) >= 2.5:
        ding(10, f"speed signal {abs(g['speed_err_pct']):.1f}% off the gear ratio (Speed from: Automatic would help)")
    score = max(score, 0)
    label = "Good" if score >= 80 else "Fair" if score >= 55 else "Poor"
    return label, score, why


def cross_check(rpm, ehp, ecu_tq, maf_hp):
    """Peak engine power three ways, all uncorrected: this dyno's estimate, the ECU's own torque
    figure, and airflow x HP_PER_GS. {ours, ecu, maf, worst_pct} or None if the log has neither."""
    if ecu_tq is None and maf_hp is None:
        return None
    out = {"ours": float(np.nanmax(ehp)), "ecu": None, "maf": None}
    if ecu_tq is not None:
        out["ecu"] = float(np.nanmax(ecu_tq * rpm * 2 * math.pi / 60.0 / HP_W))
    if maf_hp is not None:
        out["maf"] = float(np.nanmax(maf_hp))
    others = [v for v in (out["ecu"], out["maf"]) if v]
    out["worst_pct"] = max(abs(out["ours"] / v - 1) * 100 for v in others) if others else 0.0
    return out


def same_gear(results, ref):
    """The results in the same gear as ref (gearing within 3%)."""
    return [r for r in results if "error" not in r and abs(r["kmh_per_1000"] / ref["kmh_per_1000"] - 1) < 0.03]


def full_pulls(results):
    """Leave out pulls covering under 60% of the longest one's rpm (a recording stopped mid-pull, a
    short burst): compared or averaged with the rest they'd mislead or cut the shared range."""
    span = lambda r: r["rpm"][-1] - r["rpm"][0]
    longest = max((span(r) for r in results), default=0)
    return [r for r in results if span(r) >= 0.6 * longest]


def spread_pct(results):
    """How far apart same-gear pulls are: mean wheel power over the rpm range they share (max - min,
    % of the mean). Short pulls are left out."""
    good = full_pulls(results)
    if len(good) < 2:
        return 0.0
    lo, hi = max(r["rpm"][0] for r in good), min(r["rpm"][-1] for r in good)
    if hi - lo < 400:
        return 0.0
    rpm = np.linspace(lo, hi, 50)
    means = [float(np.mean(np.interp(rpm, r["rpm"], r["whp"]))) for r in good]
    return (max(means) - min(means)) / (sum(means) / len(means)) * 100


def average(results):
    """One curve from several pulls on their shared rpm range (e.g. both directions on the same
    road, which cancels slope and wind)."""
    good = [r for r in results if "error" not in r]
    if not good:
        return {"error": "No usable pulls to average."}
    # Only pulls in the same gear (gearing within 3%): in a taller gear less power goes into spinning
    # up the engine, so its wheel figures run a little higher. The gear with the most pulls wins.
    groups = [[r for r in good if abs(r["kmh_per_1000"] / g["kmh_per_1000"] - 1) < 0.03] for g in good]
    same = full_pulls(max(groups, key=len))
    skipped = len(good) - len(same)
    good = same
    if len(good) == 1:
        return dict(good[0], skipped=skipped, count=1)
    lo = max(r["rpm"][0] for r in good)
    hi = min(r["rpm"][-1] for r in good)
    if hi - lo < 400:
        return {"error": "These pulls don't share enough rpm to average."}
    step = good[0]["rpm"][1] - good[0]["rpm"][0]
    rpm = np.arange(lo, hi + step / 2, step)
    out = dict(good[0])
    for key in ("whp", "wtq_nm", "ehp", "etq_nm"):
        out[key] = np.mean([np.interp(rpm, r["rpm"], r[key]) for r in good], axis=0)
    for key in ("ecu_tq_nm", "maf_hp"):
        arrs = [np.interp(rpm, r["rpm"], r[key]) for r in good if r.get(key) is not None]
        out[key] = np.mean(arrs, axis=0) if arrs else None
    out["rpm"] = rpm
    pk = {}
    for key, arr in (("whp", out["whp"]), ("wtq", out["wtq_nm"]), ("ehp", out["ehp"]), ("etq", out["etq_nm"])):
        k = int(np.argmax(arr))
        pk[key], pk[key + "_rpm"] = float(arr[k]), float(rpm[k])
    out["peaks"] = pk
    out["kmh_per_1000"] = float(np.mean([r["kmh_per_1000"] for r in good]))
    out["slip_pct"] = max(r["slip_pct"] for r in good)
    out["rate_hz"] = float(np.mean([r["rate_hz"] for r in good]))
    out["warnings"] = sorted({w for r in good for w in r["warnings"]})
    out["count"] = len(good)
    out["skipped"] = skipped
    out["spread_pct"] = spread_pct(good)
    out["cut_off"] = any(r.get("cut_off") for r in good)
    cf = out["cf"]
    out["check"] = cross_check(rpm, out["ehp"] / cf, out["ecu_tq_nm"],
                               out["maf_hp"] / cf if out["maf_hp"] is not None else None)
    out["quality"] = quality(out)
    return out
