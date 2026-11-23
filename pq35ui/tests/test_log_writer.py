"""The datalog writer's three modes, read back from the CSV it writes (no Qt).

A ramp is logged as the Logger does it: group 2 (rpm = 100 * t) and group 122 alternate every
0.1 s. Then: Continuous keeps everything, Rolling keeps only the last keep_s seconds, Triggered
keeps rows around the moments the trigger is true (pre_s before, post_s after) and re-arms.

Run:  .venv/bin/python -m unittest pq35ui.tests.test_log_writer"""
import csv
import tempfile
import unittest
from pathlib import Path

from pq35ui.app.log_writer import DatazapWriter, csv_number

COLUMNS = [("2.1", "Engine speed"), ("122.4", "Torque intervention")]


def record(folder, mode, seconds=10.0, rpm=lambda t: 100.0 * t, **kw):
    path = Path(folder) / f"engine_{mode}.csv"
    w = DatazapWriter(path, COLUMNS, [2, 122], mode=mode, **kw)
    t, n = 0.0, 0
    while t <= seconds:
        if n % 2 == 0:
            w.update(2, {"2.1": rpm(t)}, {"2.1": "rpm"}, t)
        else:
            w.update(122, {"122.4": 12}, {"122.4": ""}, t)
        t, n = round(t + 0.1, 3), n + 1
    w.close()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[0], [[float(x) for x in r] for r in rows[1:]], w


class Writer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_continuous_keeps_everything_in_order(self):
        header, rows, w = record(self.tmp.name, "continuous")
        self.assertEqual(header[1:], ["Engine speed (rpm)", "Torque intervention"])
        times = [r[0] for r in rows]
        self.assertEqual(times, sorted(times))
        self.assertGreater(len(rows), 90)
        self.assertEqual(len(rows), w.rows)

    def test_rolling_keeps_only_the_last_seconds(self):
        _h, rows, _w = record(self.tmp.name, "loop", keep_s=4.0)
        span = rows[-1][0] - rows[0][0]
        self.assertLessEqual(span, 4.0 + 0.2)
        self.assertGreater(span, 3.0)
        self.assertGreaterEqual(rows[-1][0], 9.8)  # the end of the drive is what's kept

    def test_triggered_keeps_rows_around_each_event(self):
        # rpm above 500 between t = 3 and 4 s, and again between 7 and 7.5 s
        def rpm(t):
            return 900.0 if 3.0 <= t <= 4.0 or 7.0 <= t <= 7.5 else 200.0
        _h, rows, _w = record(self.tmp.name, "trigger", rpm=rpm, pre_s=1.0, post_s=0.5,
                              trigger=lambda latest: (latest.get("2.1") or 0) > 500)
        times = [r[0] for r in rows]
        self.assertTrue(times, "no rows written")
        self.assertGreaterEqual(min(times), 3.0 - 1.0 - 0.2)      # pre_s before the first event
        self.assertTrue(any(6.0 - 0.2 <= t < 7.0 for t in times))  # re-armed: pre_s before the second
        self.assertFalse(any(5.0 < t < 5.8 for t in times))        # nothing in the quiet gap
        self.assertLessEqual(max(times), 7.5 + 0.5 + 0.2)          # post_s after the last

    def test_csv_number(self):
        self.assertEqual(csv_number({"value": 12.5, "numeric": True}), 12.5)
        self.assertEqual(csv_number({"text": "0101 (bits)"}), 5)


if __name__ == "__main__":
    unittest.main()
