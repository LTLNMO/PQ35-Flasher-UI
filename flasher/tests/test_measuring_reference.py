"""Measuring-block formulas checked against real data, so a change can't quietly break them.

Three independent references, collected 2026-11-22:

* REAL_CAR: bytes a 2011 Audi A3 2.0 TFSI engine ECU (8P0907115AS, Bosch MED17) sent for
  one read of every group, key on and engine off. Expected values from kw1281test's
  decoder (github.com/gmenounos/kw1281test, Blocks/SensorValue.cs), not from ours. The
  identification groups (80-82, 188, 250: VIN, part numbers) are left out on purpose.
* VCDS_MED17_5: values VCDS 15.7 displayed in a 2009 Tiguan's logs (06J906026CG, MED17.5,
  Ross-Tech forum thread 4888), 1 decimal. Each must be what some byte gives.
* PHYSICAL: values that must hold in the real world (the A3's odometer read 215,870 km,
  about 134k miles, matching its dash; throttle can't pass 100 %).
"""
import unittest

from flasher.vag_protocols.measuring import decode_value

# (group, field, formula, A, B, expected value, expected unit)
REAL_CAR = (
    (1, 1, 1, 200, 0, 0.0, "rpm"),
    (1, 2, 5, 10, 153, 53.0, "°C"),          # coolant
    (1, 3, 20, 50, 128, 0.0, "%"),           # lambda control
    (2, 2, 33, 133, 101, 75.9398, "%"),      # load: 100*B/A (not 0.01*A*B = 134.33)
    (2, 3, 22, 255, 0, 0.0, "ms"),           # injection time
    (2, 4, 25, 190, 0, 1.044, "g/s"),        # MAF
    (3, 3, 33, 255, 22, 8.6275, "%"),        # throttle
    (3, 4, 27, 75, 128, 0.0, "°"),           # ignition timing (crank degrees)
    (4, 2, 21, 94, 127, 11.938, "V"),        # battery
    (4, 4, 5, 10, 148, 48.0, "°C"),          # intake air
    (9, 2, 102, 10, 17, 17.0, "mm"),
    (10, 7, 14, 40, 38, 7.6, "bar"),         # 0.005*A*B (the old 0.01*A*(B-100) gave -24.8)
    (12, 6, 90, 2, 0, 0.0, "kg"),
    (13, 5, 91, 56, 144, 89.6, "°"),
    (14, 6, 99, 127, 255, 32767, ""),
    (20, 1, 34, 75, 128, 0.0, "°"),          # timing retard: crank degrees, not kW
    (26, 1, 21, 156, 107, 16.692, "V"),      # knock sensor
    (31, 1, 31, 20, 255, 1.9922, ""),        # lambda actual: a ratio, no unit (not °C)
    (31, 2, 31, 20, 128, 1.0, ""),           # lambda specified
    (33, 2, 66, 10, 76, 1.4869, "V"),        # wideband sensor voltage
    (37, 3, 61, 255, 128, 0.0, ""),
    (57, 4, 52, 12, 52, 0.48, "Nm"),
    (74, 5, 36, 84, 83, 215870, "km"),       # odometer: A*2560+B*10 km
    (85, 1, 36, 84, 83, 215870, "km"),       # odometer again (group 85)
    (89, 1, 92, 1, 0, 0, "km"),              # distance with MIL on
    (91, 8, 97, 10, 10, 0, "°C"),
    (93, 3, 81, 0, 0, 0.0, "°"),             # camshaft phase position: an angle
    (101, 5, 96, 255, 197, 5023.5, "mbar"),
    (102, 5, 99, 0, 100, 100, ""),
    (106, 1, 83, 15, 160, 40.0, "bar"),      # rail specified
    (106, 2, 83, 2, 138, 6.5, "bar"),        # rail actual (engine off)
    (106, 3, 33, 255, 15, 5.8824, "%"),      # fuel pump duty
    (106, 4, 26, 48, 0, -48, "°C"),          # fuel temperature (placeholder, engine off)
    (106, 7, 90, 2, 99, 19.8, "kg"),
    (114, 1, 33, 133, 126, 94.7368, "%"),    # specified load
    (114, 4, 33, 255, 5, 1.9608, "%"),       # N75 duty
    (115, 3, 18, 250, 26, 260.0, "mbar"),    # boost specified
    (115, 4, 18, 250, 86, 860.0, "mbar"),    # boost actual (= barometric, engine off)
    (118, 2, 5, 10, 148, 48.0, "°C"),
    (120, 2, 52, 110, 50, 0.0, "Nm"),
    (122, 2, 52, 110, 254, 448.8, "Nm"),     # torque limit from the transmission (no-limit)
    (122, 3, 52, 110, 50, 0.0, "Nm"),
    (122, 4, 37, 0, 120, 120, ""),           # transmission torque intervention: a code
)

# (what VCDS showed, formula, A the car uses for it, unit) - VCDS rounds to 1 decimal.
VCDS_MED17_5 = (
    (10.5, 27, 75, "°"),     # 003.4 ignition timing angle, "°BTDC": advance is positive
    (25.5, 27, 75, "°"),
    (5.3, 27, 75, "°"),
    (-2.3, 34, 75, "°"),     # 020 timing retardation, "°KW"
    (-3.0, 34, 75, "°"),
    (127.1, 33, 133, "%"),   # 115.2 engine load
    (98.8, 33, 255, "%"),    # 003.3 throttle valve angle
    (96.5, 33, 255, "%"),    # 118.3 N75 duty
    (1900.0, 18, 250, "mbar"),  # 115 boost
)


class RealCar(unittest.TestCase):
    def test_every_field_matches_kw1281test(self):
        for group, field, formula, a, b, want, unit in REAL_CAR:
            with self.subTest(group=group, field=field, formula=formula):
                value, got_unit = decode_value(formula, a, b)
                self.assertIsInstance(value, (int, float))
                self.assertAlmostEqual(value, want, places=3)
                self.assertEqual(got_unit, unit)


class VcdsMed175(unittest.TestCase):
    def test_vcds_values_are_reachable(self):
        for shown, formula, a, unit in VCDS_MED17_5:
            with self.subTest(shown=shown, formula=formula):
                values = [decode_value(formula, a, b) for b in range(256)]
                self.assertTrue(any(abs(v - shown) <= 0.051 for v, _u in values),
                                f"no byte gives VCDS's {shown}")
                self.assertEqual(values[0][1], unit)

    def test_timing_advance_positive_retard_negative(self):
        # VCDS shows ignition timing as "°BTDC" with positive values: before TDC is positive.
        self.assertGreater(decode_value(27, 75, 142)[0], 0)
        self.assertLess(decode_value(27, 75, 120)[0], 0)  # after TDC keeps its sign
        self.assertLess(decode_value(34, 75, 125)[0], 0)  # retard is negative
        self.assertLess(decode_value(4, 75, 140)[0], 0)   # formula 4: b > 127 is ATDC


class Physical(unittest.TestCase):
    def test_odometer_matches_the_dash(self):
        km = decode_value(36, 84, 83)[0]
        self.assertAlmostEqual(km / 1.609344, 134135, delta=1500)  # dash: ~133-134k miles

    def test_throttle_and_duty_never_pass_100(self):
        self.assertAlmostEqual(decode_value(33, 255, 255)[0], 100.0)

    def test_load_caps_where_the_ecu_field_ends(self):
        self.assertAlmostEqual(decode_value(33, 133, 255)[0], 191.73, places=2)


if __name__ == "__main__":
    unittest.main()
