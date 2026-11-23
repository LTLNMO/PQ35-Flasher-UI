"""Offscreen layout tools: build the real window in either interface and measure every tab.

Used by test_layout.py (the rules) and by hand around UI refactors, to prove the layout didn't move:

    PQ35_SNAPSHOT_DIR=<dir> .venv/bin/python -m pq35ui.tests.layout_tools snapshot before
    ... edit ...
    PQ35_SNAPSHOT_DIR=<dir> .venv/bin/python -m pq35ui.tests.layout_tools snapshot after
    PQ35_SNAPSHOT_DIR=<dir> .venv/bin/python -m pq35ui.tests.layout_tools diff before after

Always isolated: HOME, PQ35_USER_DIR and PQ35_DATA_DIR point at a temporary folder, demo mode, and
the app's entry point (main.main) is never called, so no lock file or real user file is touched.
Fonts render at 72 dpi (QT_FONT_DPI) so sizes match a Mac."""
import json
import os
import sys
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 1300, 820


def isolate():
    """Point every user / data folder at a fresh temp folder. Call before importing the app."""
    root = Path(tempfile.mkdtemp(prefix="pq35-layout-"))
    os.environ.update(HOME=str(root), PQ35_USER_DIR=str(root / "user"), PQ35_DATA_DIR=str(root / "data"),
                      PQ35_DEMO="1", QT_QPA_PLATFORM="offscreen", QT_FONT_DPI="72")
    os.environ.pop("QT_SCALE_FACTOR", None)
    return root


def app():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    a = QApplication.instance()
    if a is None:
        a = QApplication([sys.argv[0]])
        from pq35ui.app import main as entry  # module only: main() is never called
        font = entry.FONTS_DIR / "MaterialSymbolsRounded.ttf"
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        from pq35ui.app.widgets import AppEventFilter
        a._pq35_filter = AppEventFilter(a)
        a.installEventFilter(a._pq35_filter)
    return a


def build_window(interface):
    """The main window in `interface` ('classic' or 'mac'), WIDTH x HEIGHT, shown offscreen."""
    os.environ["PQ35_INTERFACE"] = interface
    a = app()
    from pq35ui.app import theme
    from pq35ui.app.storage import Settings
    theme.set_settings(Settings())
    theme.apply(a)
    from pq35ui.app.main_window import MainWindow
    w = MainWindow()
    w.setWindowState(w.windowState() & ~(w.windowState().WindowFullScreen | w.windowState().WindowMaximized))
    w.resize(WIDTH, HEIGHT)
    w.show()
    settle()
    return w


def close_window(w):
    try:
        w.pool.stop()
    except Exception:
        pass
    w.close()
    w.deleteLater()
    settle()


def settle(rounds=6):
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtTest import QTest
    for _ in range(rounds):
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        QTest.qWait(10)


def tab_keys(window):
    from pq35ui.app.main_window import NAV
    skip = {"help", "settings"} if getattr(window, "mac", False) and sys.platform == "darwin" else set()
    return [k for k, *_ in NAV if k not in skip]


def show_tab(window, key):
    window._switch_tab(key)
    from PySide6.QtTest import QTest
    QTest.qWait(700)  # Classic's tab bar animates its width
    settle()
    return window.stack.currentWidget()


def _label(w):
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit
    for get in ("toolTip", "accessibleName"):
        t = getattr(w, get)()
        if t:
            return t[:60]
    if isinstance(w, (QAbstractButton, QLabel)):
        return w.text()[:40]
    if isinstance(w, QLineEdit):
        return w.placeholderText()[:40]
    return ""


def controls(window):
    """Every visible control in the window: [{"key", "cls", "rect": [x, y, w, h]}], keys stable
    across runs (class, object name, label, and a count for repeats)."""
    from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSlider, QComboBox,
                                   QLabel, QLineEdit, QSplitter, QTabBar, QWidget)
    kinds = (QAbstractButton, QComboBox, QLineEdit, QLabel, QAbstractScrollArea, QSplitter, QTabBar,
             QAbstractSlider)
    out, seen = [], {}
    for w in window.findChildren(QWidget):
        if not w.isVisibleTo(window) or not isinstance(w, kinds) or w.width() <= 0:
            continue
        if isinstance(w, QAbstractSlider) and w.parent() is not None and isinstance(
                w.parent().parent(), QAbstractScrollArea) and not w.isVisible():
            continue
        cls = type(w).__name__
        kind = "Button" if isinstance(w, QAbstractButton) else cls  # a button's class may change, not its place
        base = f"{kind}|{w.objectName()}|{_label(w)}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        p = w.mapTo(window, w.rect().topLeft())
        out.append({"key": f"{base}|{n}", "cls": cls, "rect": [p.x(), p.y(), w.width(), w.height()]})
    out.sort(key=lambda c: c["key"])
    return out


def snapshot(name, out_dir):
    """Geometry (JSON) + screenshots (PNG) of every tab in both interfaces, into out_dir/name."""
    dest = Path(out_dir) / name
    dest.mkdir(parents=True, exist_ok=True)
    data = {}
    for interface in ("classic", "mac"):
        w = build_window(interface)
        for key in tab_keys(w):
            show_tab(w, key)
            data[f"{interface}/{key}"] = controls(w)
            w.grab().save(str(dest / f"{interface}-{key}.png"))
        close_window(w)
    (dest / "layout.json").write_text(json.dumps(data, indent=1))
    return data


def diff(a_dir, b_dir, tolerance=0):
    """Controls that moved, appeared or disappeared between two snapshots: list of strings."""
    a = json.loads((Path(a_dir) / "layout.json").read_text())
    b = json.loads((Path(b_dir) / "layout.json").read_text())
    problems = []
    for page in sorted(set(a) | set(b)):
        ca = {c["key"]: c["rect"] for c in a.get(page, [])}
        cb = {c["key"]: c["rect"] for c in b.get(page, [])}
        for k in sorted(set(ca) | set(cb)):
            ra, rb = ca.get(k), cb.get(k)
            if ra is None:
                problems.append(f"{page}: NEW {k} {rb}")
            elif rb is None:
                problems.append(f"{page}: GONE {k} {ra}")
            elif any(abs(x - y) > tolerance for x, y in zip(ra, rb)):
                problems.append(f"{page}: MOVED {k} {ra} -> {rb}")
    return problems


def image_diff(a_dir, b_dir):
    """Screenshots that differ (name, changed pixel count)."""
    from PySide6.QtGui import QImage
    out = []
    for pa in sorted(Path(a_dir).glob("*.png")):
        pb = Path(b_dir) / pa.name
        if not pb.exists():
            out.append((pa.name, -1))
            continue
        ia, ib = QImage(str(pa)), QImage(str(pb))
        if ia.size() != ib.size():
            out.append((pa.name, -2))
            continue
        if ia == ib:
            continue
        changed = sum(1 for y in range(ia.height()) for x in range(0, ia.width(), 1)
                      if ia.pixel(x, y) != ib.pixel(x, y))
        out.append((pa.name, changed))
    return out


if __name__ == "__main__":
    isolate()
    cmd, *args = sys.argv[1:]
    base = os.environ.get("PQ35_SNAPSHOT_DIR") or tempfile.gettempdir()
    app()
    if cmd == "snapshot":
        snapshot(args[0], base)
        print("saved", Path(base) / args[0])
    elif cmd == "diff":
        moved = diff(Path(base) / args[0], Path(base) / args[1])
        print("\n".join(moved) or "geometry: identical")
        for name, n in image_diff(Path(base) / args[0], Path(base) / args[1]):
            print(f"image {name}: {n} px differ")
    sys.stdout.flush()
    os._exit(0)
