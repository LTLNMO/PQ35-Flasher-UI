"""Speed guards: things that once made the app slow, measured so they can't creep back.

- Live View's per-reading cost once its graphs are full (was 58 us per channel, now ~1.5 us).
- Nothing installs its own app-wide event filter: each one made every event in the app (repaints,
  mouse moves, timers) cross into Python again; the Mac window build went 9.2 s -> 4.2 s when they
  were folded into widgets.AppEventFilter. Everything goes through AppEventFilter.add instead.
- Building the window stays within a generous time (catches a big regression, not noise).

Run:  .venv/bin/python -m unittest pq35ui.tests.test_performance"""
import os
import time
import types
import unittest

from pq35ui.tests import layout_tools as L

if __name__ != "__main__" and "PQ35_LAYOUT_ISOLATED" not in os.environ:
    L.isolate()
    os.environ["PQ35_LAYOUT_ISOLATED"] = "1"

PUSH_US = 15.0       # per reading per channel, steady state (measured ~1.5 us; slow CI machines allowed)
BUILD_S = 25.0       # building + showing the window (measured ~4-5 s offscreen)


class LiveViewCost(unittest.TestCase):
    def test_full_graphs_stay_cheap(self):
        from pq35ui.app import liveview_page as LV
        page = types.SimpleNamespace(channel_data={f"c{i}": {"x": [], "y": [], "conv": None} for i in range(25)})
        push = LV.LiveViewPage._push.__get__(page)
        real = LV.units.to_display
        LV.units.to_display = lambda v, c: v
        try:
            for k in page.channel_data:  # full: the steady state of a long session
                for t in range(LV.MAX_POINTS + LV.TRIM_SLACK):
                    push(k, float(t), 1.0)
            n = 2000
            t0 = time.perf_counter()
            for i in range(n):
                for k in page.channel_data:
                    push(k, 1e6 + i, 1.0)
            per = (time.perf_counter() - t0) / n / len(page.channel_data) * 1e6
        finally:
            LV.units.to_display = real
        self.assertLess(per, PUSH_US, f"Live View: {per:.1f} us per reading per channel")
        self.assertTrue(all(len(d["x"]) <= LV.MAX_POINTS + LV.TRIM_SLACK for d in page.channel_data.values()))


class WindowBuild(unittest.TestCase):
    def test_one_app_wide_filter_and_build_time(self):
        from PySide6.QtCore import QObject
        app = L.app()
        from pq35ui.app.widgets import AppEventFilter
        installed = []
        original = QObject.installEventFilter

        def spy(obj, f):
            if obj is app:
                installed.append(type(f).__name__)
            return original(obj, f)
        QObject.installEventFilter = spy
        try:
            t0 = time.perf_counter()
            w = L.build_window("mac")
            built = time.perf_counter() - t0
            for key in L.tab_keys(w):
                L.show_tab(w, key)
        finally:
            QObject.installEventFilter = original
        L.close_window(w)
        others = [n for n in installed if n != AppEventFilter.__name__]
        self.assertEqual(others, [], "app-wide event filters besides AppEventFilter (use app_filter().add)")
        self.assertLess(built, BUILD_S, f"window build took {built:.1f} s")


if __name__ == "__main__":
    import sys
    L.isolate()
    os.environ["PQ35_LAYOUT_ISOLATED"] = "1"
    try:
        unittest.main(exit=False)
    finally:
        sys.stdout.flush()
        os._exit(0)
