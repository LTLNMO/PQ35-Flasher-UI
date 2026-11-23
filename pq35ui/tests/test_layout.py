"""The layout rules, checked on every tab in both interfaces (offscreen, isolated, 72 dpi, 1300 wide).

Mac rules come from mac_shell's LAYOUT RULES block; Classic gets the rules that apply to both.
A failure names the tab and the control, e.g. "mac/maps: row 1 control 'Show map' centre 101, not 103".

Run:  .venv/bin/python -m unittest pq35ui.tests.test_layout
(slow-ish: builds the real window twice, ~15 s)"""
import os
import sys
import unittest

from pq35ui.tests import layout_tools as L

if __name__ != "__main__" and "PQ35_LAYOUT_ISOLATED" not in os.environ:
    L.isolate()  # before anything imports the app (which reads the data folders)
    os.environ["PQ35_LAYOUT_ISOLATED"] = "1"


def _measure():
    """{interface: {tab: (window, [ToolIcon info], [controls])}} measured once for every test."""
    from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QComboBox, QLineEdit,
                                   QScrollBar, QTabBar)
    from pq35ui.app.toolbar import ToolIcon
    L.app()
    out = {}
    for interface in ("classic", "mac"):
        w = L.build_window(interface)
        tabs = {}
        for key in L.tab_keys(w):
            page = L.show_tab(w, key)
            icons, ctrls, hbars = [], [], []
            for c in w.findChildren(QAbstractButton) + w.findChildren(QComboBox) + \
                    w.findChildren(QLineEdit) + w.findChildren(QTabBar):
                if not c.isVisibleTo(w) or c.width() <= 0 or not page.isAncestorOf(c) and c.window() is w and \
                        not _in_header(w, c):
                    continue
                p = c.mapTo(w, c.rect().topLeft())
                info = dict(name=_name(c), x=p.x(), y=p.y(), w=c.width(), h=c.height(),
                            icon=isinstance(c, ToolIcon), cls=type(c).__name__,
                            in_slot=isinstance(c, ToolIcon) and c.parentWidget() is not None
                            and c.parentWidget().layout() is not None
                            and c.parentWidget().layout().count() == 1)
                info["owner"] = id(c.parentWidget().parentWidget() if info["in_slot"] else c.parentWidget())
                (icons if info["icon"] else ctrls).append(info)
            for sb in w.findChildren(QScrollBar):
                if sb.isVisible() and sb.orientation().name == "Horizontal" and sb.maximum() > 0 \
                        and isinstance(sb.parent().parent(), QAbstractScrollArea) and page.isAncestorOf(sb):
                    hbars.append(_name(sb.parent().parent()) or type(sb.parent().parent()).__name__)
            # Mac: overlay bars stay hidden until you scroll, so ask the area's own range instead
            for area in w.findChildren(QAbstractScrollArea):
                overlay = getattr(area, "_pq35_overlay", None)
                if overlay is not None and area.isVisible() and page.isAncestorOf(area) and any(
                        not vertical and real.maximum() > 0 for vertical, _b, real, _e, _f in overlay.bars):
                    hbars.append(_name(area) or type(area).__name__)
            views = []  # graphs, lists, tables, logs: the content views
            for v in w.findChildren(QAbstractScrollArea):
                if v.isVisible() and page.isAncestorOf(v) and v.width() > 200 and v.height() > 100 \
                        and not _in_column(v):
                    p = v.mapTo(w, v.rect().topLeft())
                    views.append(dict(name=_name(v), cls=type(v).__name__, x=p.x(), y=p.y()))
            bottoms = {}
            if key in BOTTOM_TABS:
                bottoms["default"] = _painted_bottom(w)
                if key == "playback":  # the strip under the graph, with both its parts off
                    pb = page
                    was = (pb.legend_button.isChecked(), pb.timeline_button.isChecked())
                    pb.legend_button.setChecked(False)
                    pb.timeline_button.setChecked(False)
                    L.settle()
                    bottoms["legend and timeline off"] = _painted_bottom(w)
                    pb.legend_button.setChecked(was[0])
                    pb.timeline_button.setChecked(was[1])
                    L.settle()
            tabs[key] = dict(icons=icons, ctrls=ctrls, hbars=hbars, views=views, bottoms=bottoms)
        out[interface] = tabs
        L.close_window(w)
    return out


BOTTOM_TABS = ("logger", "liveview", "playback")  # their content is a card down to the bottom margin


def _painted_bottom(w):
    """The lowest painted row of the content, probed at a fixed x right of any column."""
    img = w.grab().toImage()
    x = img.width() - 300
    bg = img.pixel(x, img.height() - 3)
    for y in range(img.height() - 3, img.height() - 200, -1):
        if img.pixel(x, y) != bg:
            return y
    return None


def _in_column(c):
    """In a Mac side column (#macPanel): its own first row and heading come before its list."""
    p = c.parentWidget()
    while p is not None:
        if p.objectName() == "macPanel" and p.property("table") is not True:
            return True
        p = p.parentWidget()
    return False


def _in_header(w, c):
    header = getattr(w, "mac_header", None)
    return header is not None and header.isAncestorOf(c)


def _name(c):
    for t in (getattr(c, "toolTip", lambda: "")(), getattr(c, "text", lambda: "")(), c.objectName()):
        if t:
            return t.split("\n")[0][:50]
    return type(c).__name__


class LayoutRules(unittest.TestCase):
    data = None

    @classmethod
    def setUpClass(cls):
        if LayoutRules.data is None:
            LayoutRules.data = _measure()

    def _tabs(self, interface):
        return self.data[interface].items()

    # --- both interfaces ---

    def test_no_sideways_scrolling(self):
        """No tab scrolls sideways at the default window size."""
        bad = [f"{i}/{k}: {bars}" for i in ("classic", "mac") for k, t in self._tabs(i) if (bars := t["hbars"])]
        self.assertEqual(bad, [], "horizontal scroll bars")

    def test_toolbar_icons_one_size(self):
        """Every ToolIcon is the size rule's: plain or ▾ width, the interface's height."""
        from pq35ui.app import toolbar as T
        from pq35ui.app import theme
        bad = []
        for interface in ("classic", "mac"):
            os.environ["PQ35_INTERFACE"] = interface
            plain, drop = T.tool_metrics(False)[:2], T.tool_metrics(True)[:2]
            for k, t in self._tabs(interface):
                for b in t["icons"]:
                    if (b["w"], b["h"]) not in (plain, drop):
                        bad.append(f"{interface}/{k}: {b['name']!r} {b['w']}x{b['h']}")
        self.assertFalse(theme is None)
        self.assertEqual(bad, [], "toolbar icons off the size rule")

    def test_icons_in_a_row_line_up(self):
        """Toolbar icons sharing a row share its top and height (no icon a pixel above its neighbours)."""
        bad = []
        for interface in ("classic", "mac"):
            for k, t in self._tabs(interface):
                icons = sorted(t["icons"], key=lambda b: b["y"])
                for a in icons:
                    for b in icons:
                        same_row = a["owner"] == b["owner"] and \
                            abs((a["y"] + a["h"] / 2) - (b["y"] + b["h"] / 2)) < a["h"] / 2
                        if same_row and (a["y"], a["h"]) != (b["y"], b["h"]) and a["name"] < b["name"]:
                            bad.append(f"{interface}/{k}: {a['name']!r} y{a['y']} vs {b['name']!r} y{b['y']}")
        self.assertEqual(bad, [], "icons in one row at different heights")

    # --- Mac: mac_shell's LAYOUT RULES ---

    def _row1(self, t):
        from pq35ui.app.mac_shell import CONTENT_MARGINS, ROW_H
        top = self.ROW1_Y
        return [c for c in t["icons"] + t["ctrls"]
                if c["x"] >= self.MAIN_X and top - 4 <= c["y"] <= top + 6 and c["h"] <= ROW_H + 4], CONTENT_MARGINS

    ROW1_Y = 89      # rule 2/3: row 1's top at 72 dpi (title bar + CONTENT_MARGINS top)
    MAIN_X = 210     # right of the sidebar

    def test_mac_row1_centre_line(self):
        """Rule 2: every control in row 1 centres on the same line, on every tab."""
        from pq35ui.app.mac_shell import ROW_H
        centre = self.ROW1_Y + ROW_H / 2
        bad = []
        for k, t in self._tabs("mac"):
            row, _m = self._row1(t)
            for c in row:
                mid = c["y"] + c["h"] / 2
                if abs(mid - centre) > 0.5:
                    bad.append(f"mac/{k}: row 1 {c['cls']} {c['name']!r} centre {mid:g}, not {centre:g}")
        self.assertEqual(bad, [], "row 1 off its centre line")

    def test_mac_row1_icons_full_height(self):
        """Rule 2: row 1's toolbar icons are ROW_H tall at row 1's top."""
        from pq35ui.app.mac_shell import ROW_H
        bad = [f"mac/{k}: {b['name']!r} y{b['y']} h{b['h']}"
               for k, t in self._tabs("mac") for b in self._row1(t)[0]
               if b["icon"] and (b["y"], b["h"]) != (self.ROW1_Y, ROW_H)]
        self.assertEqual(bad, [], "row 1 icons not full row height")

    def test_mac_right_icons_on_the_grid(self):
        """Rule 7: right-hand icons sit on fixed columns from the right margin: ⋯ / ⚙ ends on the
        margin, each slot ICON_SLOT + ROW_SPACING to its left."""
        from pq35ui.app import toolbar as T
        from pq35ui.app.mac_shell import CONTENT_MARGINS, ICON_SLOT, ROW_SPACING
        os.environ["PQ35_INTERFACE"] = "mac"
        last = L.WIDTH - CONTENT_MARGINS[2] - T.tool_metrics(False)[0]
        columns = {last - k * (ICON_SLOT + ROW_SPACING) for k in range(10)}
        bad = []
        for k, t in self._tabs("mac"):
            for b in self._row1(t)[0]:
                if b["icon"] and b["in_slot"] and b["x"] not in columns:
                    bad.append(f"mac/{k}: {b['name']!r} x{b['x']} not on the icon grid")
        self.assertEqual(bad, [], "right-hand icons off the grid")

    def test_mac_content_under_row1(self):
        """Rule 3: content views right under row 1 (graph, list, table, log) start ROW_GAP below it,
        at the same y on every tab (Analyze's graph once sat 10 px lower than Logger's list)."""
        from pq35ui.app.mac_shell import ROW_GAP, ROW_H
        top = self.ROW1_Y + ROW_H + ROW_GAP
        bad = [f"mac/{k}: {v['cls']} {v['name']!r} starts at y{v['y']}, not y{top}"
               for k, t in self._tabs("mac") if any(b["icon"] for b in self._row1(t)[0])
               for v in t["views"] if v["x"] >= self.MAIN_X and top - 10 < v["y"] < top + 24 and v["y"] != top]
        self.assertEqual(bad, [], "content not at row 1's gap")

    def test_mac_content_ends_on_the_bottom_margin(self):
        """Rule 4: the content's card ends CONTENT_MARGINS' bottom above the window's edge on every
        tab that fills down (Analyze's graph once ended 8 px short with legend and timeline off)."""
        from pq35ui.app.mac_shell import CONTENT_MARGINS
        line = L.HEIGHT - CONTENT_MARGINS[3] - 1
        bad = [f"mac/{k} ({how}): ends at y{y}, not y{line}"
               for k, t in self._tabs("mac") for how, y in t["bottoms"].items() if y != line]
        self.assertEqual(bad, [], "content not on the bottom margin")

    def test_mac_nothing_past_the_margins(self):
        """Rule 4: no row 1 control runs past the right content margin."""
        from pq35ui.app.mac_shell import CONTENT_MARGINS
        edge = L.WIDTH - CONTENT_MARGINS[2]
        bad = [f"mac/{k}: {c['name']!r} ends at {c['x'] + c['w']}"
               for k, t in self._tabs("mac") for c in self._row1(t)[0] if c["x"] + c["w"] > edge]
        self.assertEqual(bad, [], "controls past the right margin")


if __name__ == "__main__":
    L.isolate()
    os.environ["PQ35_LAYOUT_ISOLATED"] = "1"
    try:
        unittest.main(exit=False)
    finally:
        sys.stdout.flush()
        os._exit(0)  # skip Qt's teardown of the offscreen window (and its worker) at exit
