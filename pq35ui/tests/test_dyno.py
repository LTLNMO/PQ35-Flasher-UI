"""The virtual dyno against ground truth, the dyno recorder's read plan, and car profiles.

The simulated S3 (worker/dyno.py: factory 350 Nm / 265 PS curve, known mass, drag, gearing) is
logged the way the recorder does it (groups read in turn, forward-filled rows, whole-km/h speed,
engine speed from every group), then worked out by app/dyno.py and compared with the truth.

Run:  .venv/bin/python -m unittest discover -s pq35ui/tests -t .   (no Qt window needed)"""
import unittest

import numpy as np

from pq35ui.app import cars, dyno
from pq35ui.app.dyno_recorder import pick_values, plan
from pq35ui.worker import dyno as sim

GROUPS = ("spd", "ped", "maf", "tq")   # groups 5, 54, 2, 120: each also carries engine speed
CAR = dict(dyno.DEFAULT_CAR, label="S3 sim", curb_kg=1495, people=1, cargo_kg=0, fuel_frac=1.0, cd=0.33,
           area_m2=2.15, tyre="225/40R18", loss_pct=17.0, gearbox="dsg6", speed_from="auto")


def simulate(seconds=60.0, read_s=0.05, speedo=1.0, seed=1):
    """(times, columns, true peak engine hp) for the demo car's pulls, logged like the recorder does."""
    rng = np.random.default_rng(seed)
    car = sim.DynoPull(seed=seed)
    names = {"rpm": "Engine speed (rpm)", "spd": "Vehicle speed (km/h)", "ped": "Accelerator pedal position (%)",
             "maf": "Mass air flow (G70) (g/s)", "tq": "Engine torque (actual) (Nm)"}
    rows = {k: [] for k in names}
    last = dict.fromkeys(names, np.nan)
    times, peak = [], 0.0
    t = 0.0
    for k in range(int(seconds / read_s)):
        t += read_s
        q = car.advance(t)
        peak = max(peak, q["torque"] * q["rpm"] / 7121.0)
        last["rpm"] = round(q["rpm"] + rng.normal(0, 3), 1)
        g = GROUPS[k % len(GROUPS)]
        last[g] = {"spd": float(round(q["kmh"] * speedo)), "ped": q["pedal"], "maf": q["maf"],
                   "tq": q["torque"]}[g]
        times.append(t)
        for key in names:
            rows[key].append(last[key])
    cols = {names[k]: np.array(v, float) for k, v in rows.items()}
    ok = np.all([np.isfinite(c) for c in cols.values()], axis=0)
    return np.array(times)[ok], {k: v[ok] for k, v in cols.items()}, peak


class DynoAccuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.times, cls.cols, cls.truth = simulate()
        cls.pulls = dyno.find_pulls(cls.times, cls.cols)

    def results(self, car=CAR, times=None, cols=None, pulls=None):
        times = self.times if times is None else times
        cols = self.cols if cols is None else cols
        pulls = self.pulls if pulls is None else pulls
        return [dyno.analyse(times, cols, p, car, correction="none") for p in pulls]

    def test_finds_every_full_pull(self):
        self.assertGreaterEqual(len(self.pulls), 2)
        for a, b in self.pulls[:2]:
            self.assertGreater(b - a, 8.0)  # a full 3rd-gear pull is ~9.5 s

    def test_engine_estimate_matches_truth(self):
        complete = [r for r in self.results() if "error" not in r and not r["cut_off"]]
        self.assertGreaterEqual(len(complete), 2)
        for r in complete:
            self.assertAlmostEqual(r["peaks"]["ehp"] / self.truth, 1.0, delta=0.03)
            self.assertEqual(r["quality"][0], "Good")

    def test_wheel_power_is_a_real_s3s(self):
        r = dyno.average(self.results())
        self.assertTrue(190 <= r["peaks"]["whp"] <= 215, r["peaks"]["whp"])  # stock 8P S3s: ~200-220 whp

    def test_cross_check_agrees(self):
        r = dyno.average(self.results())
        self.assertLess(r["check"]["worst_pct"], 5.0)

    def test_gear_and_speed_signal_found(self):
        g = self.results()[0]["gear"]
        self.assertEqual(g["gear"], 3)
        self.assertLess(abs(g["speed_err_pct"]), 1.0)
        self.assertEqual(g["used"], "blend")

    def test_speedo_error(self):
        """A speedo 3% high: measured speed reads ~6% high, Automatic far less, gear ratio ~right."""
        times, cols, truth = simulate(speedo=1.03)
        pulls = dyno.find_pulls(times, cols)[:2]
        err = {}
        for mode in ("log", "auto", "gearbox"):
            rs = self.results(dict(CAR, speed_from=mode), times, cols, pulls)
            err[mode] = np.mean([r["peaks"]["ehp"] for r in rs]) / truth - 1
        self.assertGreater(err["log"], 0.04)
        self.assertLess(err["auto"], err["log"] / 2)
        self.assertLess(abs(err["gearbox"]), 0.03)
        self.assertIn("speed signal", " ".join(self.results(dict(CAR, speed_from="log"), times, cols,
                                                            pulls)[0]["warnings"]))

    def test_slow_reads_still_close_but_rated_lower(self):
        times, cols, truth = simulate(read_s=0.18)
        rs = [r for r in self.results(CAR, times, cols, dyno.find_pulls(times, cols)) if "error" not in r]
        r = dyno.full_pulls(rs)[0]
        self.assertAlmostEqual(r["peaks"]["ehp"] / truth, 1.0, delta=0.04)
        self.assertNotEqual(r["quality"][0], "Good")

    def test_average_leaves_out_short_pulls(self):
        rs = self.results()
        short = dict(rs[0], rpm=rs[0]["rpm"][:20], whp=rs[0]["whp"][:20])
        self.assertEqual(len(dyno.full_pulls(rs[:2] + [short])), 2)


class DynoHelpers(unittest.TestCase):
    def test_tyres(self):
        self.assertEqual(round(dyno.tyre_diameter_mm("225/40R18")), 637)
        self.assertFalse(dyno.tyre_ok("junk"))
        self.assertAlmostEqual(dyno.tyre_radius("225/40R18"), 0.309, places=3)

    def test_weight(self):
        car = dict(dyno.DEFAULT_CAR, curb_kg=1400, people=2, cargo_kg=10, fuel_frac=0.5, tank_l=55)
        self.assertAlmostEqual(dyno.car_mass(car), 1400 + 160 + 10 - 0.5 * 55 * dyno.FUEL_KG_L)

    def test_gear_match(self):
        n, expect, err = dyno.match_gear(5.353 / 1.02, "dsg6")  # speedo 2% high
        self.assertEqual(n, 3)
        self.assertAlmostEqual(err, 2.0, delta=0.05)
        self.assertIsNone(dyno.match_gear(5.3, "manual"))

    def test_standard_air_is_unity(self):
        self.assertAlmostEqual(dyno.correction_factor("sae", 25.0, 99000.0), 1.0, places=3)
        self.assertAlmostEqual(dyno.correction_factor("din", 20.0, 101300.0), 1.0, places=3)


class RecorderPlan(unittest.TestCase):
    CATALOG = [
        {"id": "engine.rpm", "group": 1, "field": 1, "unit": "rpm", "locations": [[1, 1], [2, 1], [5, 1], [54, 1], [120, 1]]},
        {"id": "vehicle.speed", "group": 5, "field": 3, "unit": "km/h", "locations": [[5, 3]]},
        {"id": "engine.pedal_position", "group": 54, "field": 3, "unit": "%", "locations": [[54, 3]]},
        {"id": "engine.throttle_position", "group": 3, "field": 3, "unit": "%", "locations": [[3, 3]]},
        {"id": "engine.maf", "group": 2, "field": 4, "unit": "g/s", "locations": [[2, 4]]},
        {"id": "engine.torque.actual", "group": 120, "field": 3, "unit": "Nm", "locations": [[120, 3]]},
        {"id": "engine.atmospheric_pressure", "group": 113, "field": 4, "unit": "mbar", "locations": [[113, 4]]},
        {"id": "ambient.temperature", "group": 134, "field": 2, "unit": "°C", "locations": [[134, 2]]},
    ]

    def test_values_and_groups(self):
        values, why = pick_values(self.CATALOG)
        ids = [v["id"] for v in values]
        self.assertNotIn("engine.throttle_position", ids)  # the pedal is there
        groups, rates, aliases, _units = plan(values)
        self.assertNotIn(1, groups)                  # engine speed comes with the other groups
        self.assertEqual(rates, {"113": 3000, "134": 3000})
        self.assertEqual({k for k, v in aliases.items() if v == "1.1"}, {"2.1", "5.1", "54.1", "120.1"})

    def test_module_without_the_basics(self):
        values, why = pick_values([v for v in self.CATALOG if v["id"] != "vehicle.speed"])
        self.assertIsNone(values)
        self.assertIn("vehicle speed", why)


class FakeSettings(dict):
    def set(self, key, value):
        self[key] = value

    def remove(self, *keys):
        for k in keys:
            self.pop(k, None)


class CarProfiles(unittest.TestCase):
    def test_guess_from_vin_and_scan(self):
        car = cars.guess("WAUMFAFM7BA056804", {"awd": True, "dsg": True})
        self.assertIn("A3", car["label"])
        self.assertIn("AWD", car["label"])
        self.assertEqual(car["curb_kg"], 1420 + cars.AWD_EXTRA_KG)
        self.assertEqual(car["gearbox"], "dsg6")
        self.assertEqual(car["source"], "built-in")

    def test_unknown_model_still_gets_a_profile(self):
        car = cars.guess("WVWZZZ99ZBW000123")
        self.assertEqual(car["curb_kg"], dyno.DEFAULT_CAR["curb_kg"])

    def test_store_and_dyno_car(self):
        s = FakeSettings()
        cars.save(s, "VIN1", dict(cars.DEFAULT_SPECS, vin="VIN1", label="Mine", curb_kg=1500))
        self.assertEqual(cars.for_vin(s, "VIN1"), "VIN1")
        d = cars.dyno_car(cars.get(s, "VIN1"), {"people": 2, "fuel_frac": 0.5})
        self.assertEqual((d["label"], d["curb_kg"], d["people"], d["fuel_frac"]), ("Mine", 1500, 2, 0.5))
        cars.delete(s, "VIN1")
        self.assertIsNone(cars.get(s, "VIN1"))

    def test_old_dyno_profiles_move_over(self):
        s = FakeSettings(dyno={"cars": {"WAUMFAFM7BA056804": {"label": "A3", "curb_kg": 1450.0, "extra_kg": 90.0,
                                                               "source": "ai", "air_c": 20.0}},
                               "mode": "wheel"})
        car = cars.get(s, "WAUMFAFM7BA056804")
        self.assertEqual((car["label"], car["curb_kg"], car["source"]), ("A3", 1450.0, "ai"))
        self.assertNotIn("cars", s["dyno"])
        self.assertEqual(s["dyno"]["session"]["people"], 1)
        self.assertNotIn("air_c", car)

    def test_ai_answer_checked(self):
        car, filled = cars.apply_ai(dict(cars.DEFAULT_SPECS), {"curb_kg": 99999, "cd": 0.31, "tyre": "nonsense",
                                                              "tank_l": 55, "gearbox": "dsg6"})
        self.assertEqual(car["curb_kg"], cars.DEFAULT_SPECS["curb_kg"])  # out of range: ignored
        self.assertEqual(sorted(filled), ["cd", "gearbox", "tank_l"])


if __name__ == "__main__":
    unittest.main()
