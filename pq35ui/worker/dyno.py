"""Demo mode's pretend car on a dyno: 3rd-gear pulls from ~2,900 to 6,700 rpm, over and over.

An Audi S3 (8P, 2011): 2.0 TFSI, 6-speed S tronic (DSG) and quattro (Haldex), 225/40 R18 tyres,
about 1,575 kg with the driver. The full-throttle maps (boost asked for and made, load, timing,
knock retard per cylinder, airflow, injection, wastegate duty, ECU torque) and how fast the revs
climb are taken from a real logged 3rd-gear pull of a 2011 Audi 8P 2.0 TFSI (24 Oct 2026,
3,040 → 6,720 rpm in 6.5 s, ~530 rpm/s). Each cycle: cruise at ~2,900 rpm for a few seconds,
floor it, pull in 3rd to 6,700 rpm,
lift (overrun fuel cut), brake back down and cruise again. The car is stepped in small
time steps, so everything follows from everything else: boost builds with rpm (turbo spool,
a small overshoot, taper to redline), load follows boost, torque follows load, speed comes from
torque against drag, timing and lambda follow rpm and load, temperatures heat up through the
pull and cool afterwards, the DSG's clutch K1 carries the torque in 3rd with 4th preselected,
and the Haldex couples harder at lower speed.

DynoPull.advance(t) moves the car to t seconds; value(name, unit, seed) gives the reading of a
measuring value (by its catalog name) right now, with a little sensor noise."""
import math
import random

MPH = 1.609344                 # km/h per mph
RATIO = 1.300 * 4.118          # engine turns per wheel turn in 3rd: S tronic 3rd × final drive
WHEEL_R = 0.309                # m, 225/40 R18 rolling radius
KMH_PER_RPM = 2 * math.pi * WHEEL_R / RATIO * 0.06   # ≈ 0.02177 km/h per rpm in 3rd
MASS = 1575.0                  # kg, car + driver
INERTIA = 1.10                 # rotating parts in 3rd (quattro: a bit more than front drive)
DRIVE_EFF = 0.83               # S tronic + Haldex: about 17 % lost on the way to the wheels
CDA = 0.33 * 2.15              # drag coefficient × frontal area, m²
CRUISE_RPM = 2900.0           # part throttle before the pull (the real log rolled 2,560 → 3,040)
PULL_TO_RPM = 6700.0          # lift here (the real pull shifted at 6,720)
CRUISE_V = CRUISE_RPM * KMH_PER_RPM / 3.6   # m/s
GEAR4_OVER_3 = 0.902 / 1.300   # DSG: the preselected 4th gear shaft turns at this share

# Full-throttle torque: a stock S3's factory curve (265 PS at 6,000, 350 Nm flat from 2,500 to
# 5,000 rpm), which real stock 8P S3s match on a chassis dyno (~210-220 whp). The other maps below
# (boost, timing, lambda ...) keep the shape of the real logged pull.
TORQUE = ((2000, 290), (2500, 350), (5000, 350), (5500, 337), (6000, 311), (6250, 297), (6500, 282),
          (6800, 262))   # Nm at the crank
BOOST_SPEC = ((2500, 2000), (3000, 2240), (3500, 2260), (3750, 2370), (4000, 2450), (5000, 2470),
              (5500, 2440), (5750, 2350), (6000, 2260), (6250, 2200), (6500, 2130), (6800, 2080))  # mbar abs
SPOOL = ((2500, 1500), (3000, 2000), (3500, 2300), (3800, 2400), (4100, 2480), (4600, 2500),
         (5000, 2500), (5500, 2470), (5750, 2380), (6000, 2290), (6250, 2230), (6500, 2160),
         (6800, 2110))  # what the turbo can make (a little over the request once spooled: load holds)
LOAD_SPEC = ((2000, 191), (6800, 191))  # % (specified load sits on its 191 % cap)
TIMING_WOT = ((3000, 9.5), (3500, 8.0), (4000, 8.0), (4250, 12.0), (4500, 10.5), (4750, 7.5),
              (5000, 7.5), (5250, 10.0), (5500, 9.5), (5750, 12.0), (6000, 14.0), (6250, 15.0),
              (6500, 10.5))  # ° BTDC before knock retard (real ignition angle + the retard it had)
LAMBDA_WOT = ((2000, 0.88), (3500, 0.85), (5000, 0.80), (6500, 0.80))
CAM_WOT = ((2000, 30.0), (4000, 22.0), (5500, 8.0), (6500, 5.0))  # intake cam advance °
N75_WOT = ((3000, 80.0), (3750, 81.0), (4000, 87.0), (4500, 88.0), (4750, 91.0), (5000, 95.0),
           (5250, 96.0), (6800, 96.0))  # wastegate duty at full throttle, %
# knock retard per cylinder (°, 0.75° steps like the ECU): it jumps on tip-in, eases off, and the
# hot cylinder 3 picks up more around 5,250 rpm, as in the real log
TIP_RETARD = (6.75, 7.5, 6.0, 3.0)
KNOCK = ((4100, 1, 0.75), (4100, 2, 0.75), (4100, 3, 0.75), (5200, 3, 3.0), (5400, 4, 2.25))
CRUISE = {"boost": 1080.0, "load": 72.0, "timing": 15.0, "throttle": 29.0, "torque": 154.0}
OVERRUN = {"boost": 970.0, "load": 7.0, "timing": 13.5, "throttle": 8.0}


def interp(x, table):
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


class DynoPull:
    CRUISE_S = 5.0   # seconds at 30 mph between pulls
    TIP_S = 0.3      # pedal to the floor
    LIFT_S = 0.4     # pedal off at 80 mph
    BRAKE = 2.6      # m/s² back down to 30
    STEP = 0.01      # s

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.t = 0.0
        self.phase, self.phase_t = "cruise", 0.0
        self.v = CRUISE_V
        self.pedal = 14.0
        self.boost, self.rail, self.cam = CRUISE["boost"], 50.0, 20.0
        self.lam = 1.0
        self.iat, self.oil, self.cool = 28.0, 101.0, 97.0
        self.egt, self.cat, self.clutch_heat, self.plate = 520.0, 620.0, 0.0, 58.0
        self.retard = [0.0] * 4
        self.knocked = set()
        self.reached = False  # boost reached its target this pull (for the overshoot)
        self.bump = 0.0
        self.q = {}
        self._step(0.0)

    def advance(self, t):
        while self.t + self.STEP <= t:
            self._step(self.STEP)
        return self.q

    # --- the car ---

    def _step(self, dt):
        self.t += dt
        self.phase_t += dt
        kmh = self.v * 3.6
        rpm = kmh / KMH_PER_RPM
        # phases: cruise → tip-in → pull → lift → brake → cruise …
        if self.phase == "cruise" and self.phase_t >= self.CRUISE_S:
            self.phase, self.phase_t, self.knocked, self.reached = "tip", 0.0, set(), False
        elif self.phase == "tip" and self.phase_t >= self.TIP_S:
            self.phase, self.phase_t = "pull", 0.0
        elif self.phase == "pull" and rpm >= PULL_TO_RPM:
            self.phase, self.phase_t = "lift", 0.0
        elif self.phase == "lift" and self.phase_t >= self.LIFT_S:
            self.phase, self.phase_t = "brake", 0.0
        elif self.phase == "brake" and self.v <= CRUISE_V:
            self.phase, self.phase_t = "cruise", 0.0
            self.v = CRUISE_V
        phase = self.phase
        pedal = {"cruise": 14.0, "pull": 100.0, "brake": 0.0}.get(phase)
        if phase == "tip":
            pedal = 14.0 + 86.0 * clip(self.phase_t / self.TIP_S)
        elif phase == "lift":
            pedal = 100.0 * (1 - clip(self.phase_t / 0.2))
        self.pedal = pedal
        blend = clip((pedal - 14.0) / 86.0)      # 0 cruising … 1 flat out
        overrun = pedal < 1.0

        # boost: target from the map, limited by spool; lags, with a small overshoot when it arrives
        spec_wot = interp(rpm, BOOST_SPEC)
        boost_spec = 190.0 if overrun else CRUISE["boost"] + 45.0 + (spec_wot - CRUISE["boost"] - 45.0) * blend
        target = OVERRUN["boost"] if overrun else min(boost_spec, max(interp(rpm, SPOOL), CRUISE["boost"]))
        tau = 0.28 if target > self.boost else 0.15
        if dt:
            self.boost += (target - self.boost) * dt / tau
        if blend > 0.9 and not self.reached and self.boost >= boost_spec - 40:
            self.reached, self.bump = True, 25.0
        self.bump *= math.exp(-dt / 0.45) if dt else 1.0
        boost = self.boost + self.bump

        # load and torque
        load_wot = interp(rpm, LOAD_SPEC)
        load_spec = 155.0 + (load_wot - 155.0) * blend   # the real ECU asks ~155 % even cruising
        cruise_load = CRUISE["load"] + (load_wot - CRUISE["load"]) * blend
        load = OVERRUN["load"] if overrun else cruise_load * clip(boost / max(boost_spec, 1.0), 0.0, 1.03)
        # the map's torque once load reaches its full-throttle target (less while boost builds)
        torque = 0.0 if overrun else max(CRUISE["torque"] * (1 - blend),
                                         interp(rpm, TORQUE) * load / load_wot)

        # speed: cruising holds 30 mph; the pull is torque against drag; then brakes
        drag = 0.5 * 1.2 * CDA * self.v ** 2 + 0.012 * MASS * 9.81
        if phase == "pull" or phase == "tip" or phase == "lift":
            force = torque * RATIO * DRIVE_EFF / WHEEL_R
            accel = (force - drag) / (MASS * INERTIA)
        elif phase == "brake":
            accel = -self.BRAKE
        else:
            accel = 0.0
        self.v = max(self.v + accel * dt, CRUISE_V * 0.98)

        # knock: a few small events each pull; retard decays at 0.8 °/s
        for i in range(4):
            self.retard[i] = max(0.0, self.retard[i] - 0.25 * dt)
        if phase == "tip" and "tip" not in self.knocked:
            self.knocked.add("tip")
            self.retard = [max(r, d) for r, d in zip(self.retard, TIP_RETARD)]
        if phase == "pull":
            for k_rpm, cyl, deg in KNOCK:
                if rpm >= k_rpm and (k_rpm, cyl) not in self.knocked:
                    self.knocked.add((k_rpm, cyl))
                    self.retard[cyl - 1] += deg
        elif phase in ("brake", "cruise"):
            self.retard = [max(0.0, r - 6.0 * dt) for r in self.retard]   # forgiven off throttle
        timing = OVERRUN["timing"] if overrun else \
            CRUISE["timing"] + (interp(rpm, TIMING_WOT) - CRUISE["timing"]) * blend - sum(self.retard) / 4

        # fuelling
        lam_spec = 1.0 + (interp(rpm, LAMBDA_WOT) - 1.0) * blend
        lam_target = 1.6 if overrun else lam_spec   # overrun fuel cut reads lean
        if dt:
            self.lam += (lam_target - self.lam) * dt / 0.12
        rail_spec = 40.0 if overrun else 50.0 + 60.0 * blend
        if dt:
            self.rail += (rail_spec - self.rail) * dt / 0.15
        rail = self.rail - max(0.0, rpm - 4800.0) * 0.012 * blend   # the pump falls a little behind
        inj = 0.0 if overrun else load * 0.039 * (1 - 0.18 * clip((rpm - 4500.0) / 2000.0))
        # airflow follows power (a turbo petrol at full throttle makes ~1.3 hp per g/s), plus idle air
        maf = 3.5 + max(torque, 0.0) * rpm / 7121.0 / 1.33

        # wastegate (N75), cam, throttle
        if blend > 0.5:
            n75 = interp(rpm, N75_WOT)
        else:
            n75 = 1.0
        cam_spec = 20.0 + (interp(rpm, CAM_WOT) - 20.0) * blend
        if dt:
            self.cam += (cam_spec - self.cam) * dt / 0.2
        throttle = OVERRUN["throttle"] if overrun else CRUISE["throttle"] + (100.0 - CRUISE["throttle"]) * blend

        # temperatures (slow)
        if dt:
            iat_t = 28.0 - 3.0 * blend - (4.0 if overrun else 0.0)   # cool morning: more air, cooler intake
            self.iat += (iat_t - self.iat) * dt / (4.0 if iat_t < self.iat else 25.0)
            self.egt += ((520.0 + 380.0 * blend * clip(rpm / 5500.0) - (120.0 if overrun else 0.0)) - self.egt) * dt / 2.5
            self.cat += ((620.0 + 200.0 * blend) - self.cat) * dt / 6.0
            self.cool += ((97.0 + 1.0 * blend) - self.cool) * dt / 20.0
            self.oil += ((101.0 + 5.0 * blend) - self.oil) * dt / 25.0
            self.clutch_heat += ((10.0 * blend) - self.clutch_heat) * dt / 8.0
            self.plate += ((58.0 + 25.0 * blend * clip(1 - (kmh - 48) / 90)) - self.plate) * dt / 5.0

        haldex = 30.0 if phase == "cruise" else 0.0 if overrun else \
            50.0 + 450.0 * blend * clip(1 - (kmh - 48.0) / 90.0)
        self.q = {
            "rpm": rpm, "kmh": kmh, "pedal": pedal, "throttle": throttle, "blend": blend,
            "boost": boost, "boost_spec": boost_spec, "load": load, "load_spec": load_spec,
            "torque": torque, "maf": maf, "inj": inj, "timing": timing, "retard": list(self.retard),
            "lam": self.lam, "lam_spec": lam_spec, "rail": rail, "rail_spec": rail_spec,
            "pump": 30.0 + 60.0 * blend * clip(rpm / 6000.0), "n75": n75,
            "cam": self.cam, "cam_spec": cam_spec, "iat": self.iat, "egt": self.egt, "cat": self.cat,
            "cool": self.cool, "oil": self.oil, "volts": 13.63 - 0.15 * blend, "gen": 35.0 + 10.0 * blend,
            "overrun": overrun, "haldex": haldex, "clutch_heat": self.clutch_heat, "plate": self.plate,
        }

    # --- readings ---

    def _noise(self, size):
        return self.rng.gauss(0.0, size)

    def value(self, name, unit="", seed=0):
        """The reading of a measuring value (catalog name) now. Unknown values stay steady."""
        q, n = self.q, (name or "").lower()
        rule = _rule(n)
        if rule is not None:
            return rule(self, q, n)
        # anything else: a steady reading (adaptations, statuses, counters don't move on a pull)
        unit = (unit or "").lower()
        if unit in ("rpm", "/min"):
            return q["rpm"]
        if unit == "km/h":
            return q["kmh"]
        if unit == "v":
            return q["volts"]
        return round(((seed * 37) % 50) / 10.0, 1)


def _cyl(n):
    for i in "1234":
        if f"cylinder {i}" in n:
            return int(i) - 1
    return 0


# (words in the name, reading). First match wins, so the specific ones come first.
RULES = (
    (("misfire rpm barrier (lower)",), lambda s, q, n: 3000.0),
    (("misfire rpm barrier (upper)",), lambda s, q, n: 6200.0),
    (("misfire counter",), lambda s, q, n: 0.0),
    (("engine speed (specified)",), lambda s, q, n: 760.0),
    (("engine speed",), lambda s, q, n: q["rpm"] + s._noise(6)),
    (("intake air temperature correction factor",), lambda s, q, n: 1.0 - (q["iat"] - 30.0) / 200.0),
    (("correction factor", "adaptation factor", "altitude correction"), lambda s, q, n: 1.0),
    (("enrichment factor",), lambda s, q, n: 1.0 + (1.0 - q["lam_spec"])),
    (("dynamic factor",), lambda s, q, n: 0.92),
    (("engine start temperature",), lambda s, q, n: 24.0),
    (("engine outlet temperature",), lambda s, q, n: q["cool"] + 3.0),
    (("radiator outlet temperature",), lambda s, q, n: q["cool"] - 9.0),
    (("coolant temperature",), lambda s, q, n: q["cool"]),
    (("haldex oil temperature",), lambda s, q, n: 52.0 + q["clutch_heat"] * 0.3),
    (("haldex plate temperature",), lambda s, q, n: q["plate"]),
    (("oil temperature",), lambda s, q, n: q["oil"]),
    (("intake air temperature",), lambda s, q, n: q["iat"] + s._noise(0.1)),
    (("ambient temperature",), lambda s, q, n: 22.0),
    (("fuel temperature",), lambda s, q, n: 38.0),
    (("exhaust temperature",), lambda s, q, n: q["egt"]),
    (("catalytic converter temperature",), lambda s, q, n: q["cat"]),
    (("mass air flow (g70)",), lambda s, q, n: q["maf"] * (1 + s._noise(0.01))),
    (("ignition timing",), lambda s, q, n: q["timing"]),
    (("timing retard cylinder",), lambda s, q, n: q["retard"][_cyl(n)]),
    (("injection duration",), lambda s, q, n: q["inj"]),
    (("engine load (actual)",), lambda s, q, n: q["load"] + s._noise(0.4)),
    (("engine load (specified",), lambda s, q, n: q["load_spec"]),
    (("boost pressure (actual absolute)",), lambda s, q, n: q["boost"] + s._noise(8)),
    (("boost pressure (specified absolute)",), lambda s, q, n: q["boost_spec"]),
    (("atmospheric pressure",), lambda s, q, n: 1010.0),
    (("boost control valve duty",), lambda s, q, n: q["n75"]),
    (("fuel rail pressure (actual)",), lambda s, q, n: q["rail"] + s._noise(0.6)),
    (("fuel rail pressure (specified)",), lambda s, q, n: q["rail_spec"]),
    (("fuel pump duty",), lambda s, q, n: q["pump"]),
    (("throttle valve angle", "throttle valve position", "throttle valve sensor 1"),
     lambda s, q, n: q["throttle"]),
    (("accelerator pedal position sensor 2",), lambda s, q, n: q["pedal"] / 2.0),
    (("accelerator pedal position",), lambda s, q, n: q["pedal"]),
    (("cruise vehicle speed",), lambda s, q, n: 0.0),
    (("median vehicle speed",), lambda s, q, n: 62.0),
    (("vehicle speed",), lambda s, q, n: q["kmh"]),
    (("terminal 30 voltage",), lambda s, q, n: q["volts"] + s._noise(0.01)),
    (("generator duty",), lambda s, q, n: q["gen"]),
    (("lambda control bank 1 (actual)",), lambda s, q, n: q["lam"] + s._noise(0.004)),
    (("lambda control bank 1 (specified)",), lambda s, q, n: q["lam_spec"]),
    (("lambda control bank 1",), lambda s, q, n: 0.0 if q["blend"] > 0.5 else 1.5 * math.sin(s.t * 1.7)),
    (("lambda sensor voltage bank 1 sensor 2",), lambda s, q, n: 0.05 if q["overrun"] else 0.68 + 0.12 * q["blend"]),
    (("lambda sensor voltage bank 1 sensor 1", "lambda voltage bank 1 sensor 1"),
     lambda s, q, n: 1.5 + (q["lam"] - 1.0) * 2.0),
    (("knock sensor voltage",), lambda s, q, n: 0.35 + 0.9 * q["rpm"] / 6500 + q["load"] / 1000 + 0.03 * _cyl(n)
     + abs(s._noise(0.02))),
    (("engine torque specified",), lambda s, q, n: 400.0),
    (("engine torque (actual",), lambda s, q, n: q["torque"]),
    (("camshaft intake bank 1 adjustment (actual)",), lambda s, q, n: q["cam"]),
    (("camshaft intake bank 1 adjustment (specified)",), lambda s, q, n: q["cam_spec"]),
    (("selected gear",), lambda s, q, n: 3.0),
    (("time since engine start",), lambda s, q, n: 420.0 + s.t),
    (("vehicle mileage",), lambda s, q, n: 123456.0),
    (("distance driven with mil",), lambda s, q, n: 0.0),
    (("cooling fan",), lambda s, q, n: 0.0),
    (("a/c refrigerant pressure",), lambda s, q, n: 7.5),
    (("quantity valve closing angle",), lambda s, q, n: 60.0 + 40.0 * q["blend"]),
    # DSG (02E): 3rd on shaft 1 through clutch K1, 4th preselected on shaft 2
    (("driveshaft 1 speed",), lambda s, q, n: q["rpm"]),
    (("driveshaft 2 speed",), lambda s, q, n: q["rpm"] * GEAR4_OVER_3),
    (("specified clutch torque k1",), lambda s, q, n: 20.0 if q["overrun"] else q["torque"] * 1.15 + 15.0),
    (("specified clutch torque k2",), lambda s, q, n: 0.0),
    (("clutch valve 1 current",), lambda s, q, n: 0.35 + max(q["torque"], 0.0) / 600.0),
    (("clutch valve 2 current",), lambda s, q, n: 0.05),
    (("actual clutch 1 pressure",), lambda s, q, n: 1.0 + max(q["torque"], 0.0) / 30.0),
    (("actual clutch 2 pressure",), lambda s, q, n: 0.4),
    (("gear selector 1-3 travel",), lambda s, q, n: 8.6),
    (("gear selector 2-4 travel",), lambda s, q, n: 8.4),
    (("gear selector 5-n travel", "gear selector 6-r travel"), lambda s, q, n: 0.1),
    (("transmission fluid temperature",), lambda s, q, n: 82.0 + q["clutch_heat"] * 0.4),
    (("transmission control module temperature",), lambda s, q, n: 68.0),
    (("clutch oil temperature",), lambda s, q, n: 95.0 + q["clutch_heat"]),
    # Haldex: couples harder at lower speed under load
    (("haldex estimated torque",), lambda s, q, n: q["haldex"]),
    (("haldex oil pressure",), lambda s, q, n: q["haldex"] / 45.0),
    (("haldex clutch valve n273 opening",), lambda s, q, n: clip(q["haldex"] / 6.0, 0.0, 100.0)),
    (("haldex clutch valve n273 current",), lambda s, q, n: 150.0 + q["haldex"] * 0.9),
)

_CACHE = {}


def _rule(name):
    if name not in _CACHE:
        _CACHE[name] = next((fn for words, fn in RULES if any(w in name for w in words)), None)
    return _CACHE[name]
