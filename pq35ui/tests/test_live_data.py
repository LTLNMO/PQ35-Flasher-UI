"""Live View's channel history (live_data): bounded, oldest out first, and the graph's window.

Run:  .venv/bin/python -m unittest pq35ui.tests.test_live_data   (no Qt needed)"""
import unittest

from pq35ui.app import live_data as D


class History(unittest.TestCase):
    def test_bounded_and_keeps_the_newest(self):
        ch = {"x": [], "y": []}
        total = D.MAX_POINTS * 3
        for i in range(total):
            D.push(ch, float(i), float(i) * 2)
        self.assertLessEqual(len(ch["x"]), D.MAX_POINTS + D.TRIM_SLACK)
        self.assertGreaterEqual(len(ch["x"]), D.MAX_POINTS)
        self.assertEqual(ch["x"][-1], total - 1)            # newest kept
        self.assertEqual(ch["y"][-1], (total - 1) * 2)      # x and y stay paired
        self.assertEqual(len(ch["x"]), len(ch["y"]))
        self.assertEqual(ch["x"], sorted(ch["x"]))

    def test_window_with_one_point_before(self):
        ch = {"x": [float(t) for t in range(100)], "y": [float(t) for t in range(100)]}
        xs, ys = D.window(ch, now=99.0, seconds=10.0)
        self.assertEqual(xs[0], 88.0)   # one before 89, so the line starts at the left edge
        self.assertEqual(xs[-1], 99.0)
        self.assertEqual(len(xs), len(ys))
        xs, _ys = D.window(ch, now=5.0, seconds=60.0)
        self.assertEqual(xs[0], 0.0)    # a window longer than the history: all of it


if __name__ == "__main__":
    unittest.main()
