"""log_info: each recording's companion file (row groups + raw bytes) and Analyze lining channels
up by their real read times. Runs in throwaway folders (PQ35_DATA_DIR / PQ35_USER_DIR)."""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("PQ35_DATA_DIR", "PQ35_USER_DIR")}
        os.environ["PQ35_DATA_DIR"] = os.path.join(self.tmp.name, "data")
        os.environ["PQ35_USER_DIR"] = os.path.join(self.tmp.name, "user")
        from pq35ui.app import log_info
        self.log_info = log_info

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def record(self, mode="continuous", seconds=10.0, trigger=None):
        """A two-group recording: group 2 = rpm (a ramp, rpm = 100*t), group 122 = a status code.
        Reads alternate every 0.1 s, as the logger does."""
        from pq35ui.app.log_writer import DatazapWriter
        path = Path(self.tmp.name) / f"engine_{mode}.csv"
        columns = [("2.1", "Engine speed"), ("122.4", "Transmission torque intervention")]
        info = {"2.1": {"group": 2, "field": 1, "kind": "number", "formula": 1, "unit": "rpm"},
                "122.4": {"group": 122, "field": 4, "kind": "status", "formula": 37, "unit": ""}}
        w = DatazapWriter(path, columns, [2, 122], mode=mode, trigger=trigger, pre_s=1.0,
                          post_s=0.5, keep_s=4.0, info_columns=info)
        t, n = 0.0, 0
        while t <= seconds:
            if n % 2 == 0:
                w.update(2, {"2.1": 100.0 * t}, {"2.1": "rpm"}, t, raw=f"01 c8 {n % 256:02x}")
            else:
                w.update(122, {"122.4": 120 if t < 5 else 12}, {"122.4": ""}, t, raw="25 00 78")
            t, n = round(t + 0.1, 3), n + 1
        w.close()
        return path, w


class CompanionFile(_Env):
    def test_one_group_and_raw_per_row(self):
        path, w = self.record()
        info = self.log_info.load(path)
        self.assertIsNotNone(info)
        self.assertEqual(len(info["groups"]), w.rows)
        self.assertEqual(len(info["raw"]), w.rows)
        self.assertEqual(set(info["groups"]), {2, 122})
        self.assertEqual(info["columns"]["Engine speed (rpm)"]["formula"], 1)
        self.assertIn("Transmission torque intervention", info["columns"])

    def test_rolling_and_triggered_stay_in_step(self):
        path, w = self.record(mode="loop")
        self.assertEqual(len(self.log_info.load(path)["groups"]), w.rows)
        path, w = self.record(mode="trigger", trigger=lambda latest: 3.0 * 100 <= latest.get("2.1", 0) <= 600)
        info = self.log_info.load(path)
        self.assertGreater(w.rows, 0)
        self.assertEqual(len(info["groups"]), w.rows)

    def test_no_raw_no_file(self):
        from pq35ui.app.log_writer import DatazapWriter
        path = Path(self.tmp.name) / "plain.csv"
        w = DatazapWriter(path, [("2.1", "Engine speed")], [2])  # e.g. the dyno recorder
        w.update(2, {"2.1": 800.0}, {"2.1": "rpm"}, 0.0)
        w.close()
        self.assertIsNone(self.log_info.load(path))

    def test_discard_and_rename_take_the_companion(self):
        from pq35ui.app import recent_logs
        path, _w = self.record()
        new = path.with_name("renamed.csv")
        path.rename(new)
        recent_logs.rename(path, new)
        self.assertIsNotNone(self.log_info.load(new))
        recent_logs.discard(new)
        self.assertFalse(self.log_info.path_for(new).exists())


class Align(_Env):
    def test_channels_land_on_their_real_read_times(self):
        from pq35ui.app.playback_common import _parse_csv
        path, _w = self.record()
        headers, times, cols = _parse_csv(path)
        rpm = cols["Engine speed (rpm)"]
        inside = (times >= 0.2) & (times <= 9.8)  # from rpm's first read in the file
        # Forward-filled, rpm lags 0.1 s on every other row; lined up, it's the ramp itself.
        np.testing.assert_allclose(rpm[inside], 100.0 * times[inside], atol=1e-6)

    def test_status_codes_are_not_interpolated(self):
        from pq35ui.app.playback_common import _parse_csv
        path, _w = self.record()
        _h, _t, cols = _parse_csv(path)
        self.assertEqual(set(np.unique(cols["Transmission torque intervention"])), {12.0, 120.0})

    def test_without_companion_nothing_changes(self):
        from pq35ui.app.playback_common import _parse_csv
        path, _w = self.record()
        self.log_info.discard(path)
        _h, times, cols = _parse_csv(path)
        rpm = cols["Engine speed (rpm)"]
        self.assertFalse(np.allclose(rpm[times <= 9.8], 100.0 * times[times <= 9.8]))  # the old lag

    def test_row_count_mismatch_is_left_alone(self):
        info = {"groups": [2, 122], "columns": {"A": {"group": 2, "formula": 1}}}
        cols = {"A": np.array([1.0, 1.0, 3.0])}
        out, n = self.log_info.align(["A"], np.array([0.0, 0.1, 0.2]), cols, info)
        self.assertEqual(n, 0)
        self.assertIs(out["A"], cols["A"])

    def test_older_column_names_still_match(self):
        from pq35ui.app import log_export
        info = {"groups": [31, 2, 31, 2], "columns": {
            "Lambda control bank 1 (actual) (°C)": {"group": 31, "formula": 31}}}
        name = "Lambda control bank 1 (actual)"
        cols = {name: np.array([1.0, 1.0, 0.8, 0.8])}
        out, n = self.log_info.align([name], np.array([0.0, 0.1, 0.2, 0.3]), cols, info,
                                     log_export.current_header)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(out[name][1], 0.9)


if __name__ == "__main__":
    unittest.main()
