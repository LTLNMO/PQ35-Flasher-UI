"""Maps: a read-only table viewer for ECU files and a byte-level file compare.

Map viewer - you describe where a map lives (address, size, data type,
scaling, optional axes) and it is shown as a heat-mapped table and as a graph.
Definitions are saved so you can come back to them.

Compare files - lists every byte range that differs between two files (stock
vs tuned), with a side-by-side hex view and a jump into the map viewer.

Nothing here writes to a file or a module."""
import math
import struct
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QStackedWidget,
    QTabBar, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from . import theme
from .storage import user_dir
from .widgets import collapsible_sidebar, Banner, ElidedLabel, FlowLayout, muted, pair, scrollable
from .popups import InputDialog

# label -> (struct format char, size, big endian)
DTYPES = {
    "u8": ("B", 1, False), "s8": ("b", 1, False),
    "u16 LE": ("H", 2, False), "u16 BE": ("H", 2, True),
    "s16 LE": ("h", 2, False), "s16 BE": ("h", 2, True),
    "u32 LE": ("I", 4, False), "u32 BE": ("I", 4, True),
    "float32 LE": ("f", 4, False), "float32 BE": ("f", 4, True),
}
DEFS_KEY = "map_definitions"


def _size(n):
    return f"{n / 1024:,.0f} KB" if n >= 1024 else f"{n} bytes"


def _row(*widgets):
    row = QHBoxLayout()
    row.setSpacing(6)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def read_values(data: bytes, address: int, count: int, dtype: str, factor=1.0, offset=0.0):
    fmt, size, big = DTYPES[dtype]
    end = address + count * size
    if address < 0 or end > len(data):
        raise ValueError(f"0x{address:X}–0x{end:X} is past the end of the file (0x{len(data):X} bytes).")
    values = struct.unpack((">" if big else "<") + fmt * count, data[address:end])
    return np.array(values, dtype=float) * factor + offset


def diff_ranges(a: bytes, b: bytes, merge_gap=8):
    """[(start, end_exclusive)] where a and b differ, merging ranges closer than merge_gap."""
    n = min(len(a), len(b))
    av = np.frombuffer(a[:n], dtype=np.uint8)
    bv = np.frombuffer(b[:n], dtype=np.uint8)
    idx = np.nonzero(av != bv)[0]
    ranges = []
    if len(idx):
        start = prev = int(idx[0])
        for i in idx[1:]:
            i = int(i)
            if i - prev > merge_gap:
                ranges.append((start, prev + 1))
                start = i
            prev = i
        ranges.append((start, prev + 1))
    if len(a) != len(b):
        ranges.append((n, max(len(a), len(b))))
    return ranges


def _heat(fraction):
    """Blue -> green -> yellow -> red, like a tuning-software map."""
    stops = [(0.0, (59, 130, 246)), (0.35, (34, 197, 94)), (0.65, (234, 179, 8)), (1.0, (239, 68, 68))]
    for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
        if fraction <= f1:
            t = 0 if f1 == f0 else (fraction - f0) / (f1 - f0)
            return QColor(*[int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3)])
    return QColor(*stops[-1][1])


def _hex(text):
    text = text.strip().lower().replace("_", "")
    return int(text, 16) if text.startswith("0x") else int(text, 16 if any(c in "abcdef" for c in text) else 10)


class MapViewer(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.data = None
        self.path = None
        self.data_b = None
        self.path_b = None
        # Right side (this widget): what the map looks like.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 24 if theme.is_mac() else 16, 20 if theme.is_mac() else 16)
        layout.setSpacing(10)
        # Left side (self.sidebar, placed by MapsPage): which file and which map.
        self.sidebar = QWidget()
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)

        # Files: one row of buttons, one line saying what's loaded.
        open_button = QPushButton("Open file…")
        open_button.setObjectName("primary")
        open_button.clicked.connect(self._open)
        compare = QPushButton("Compare…")
        compare.setToolTip("Load a second file (e.g. the tuned one) to see this map side by side and as a difference")
        compare.clicked.connect(self._open_b)
        self.clear_b = QPushButton("Clear")
        self.clear_b.setToolTip("Stop comparing with the second file")
        self.clear_b.setEnabled(False)
        self.clear_b.clicked.connect(self._clear_compare)
        sb.addLayout(_row(open_button, compare, self.clear_b))
        self.file_label = ElidedLabel("No file loaded")
        self.file_label.setObjectName("muted")
        sb.addWidget(self.file_label)

        # Saved maps: the list and its buttons on one row.
        self.saved = QComboBox()
        self.saved.setMinimumWidth(100)
        self.saved.setToolTip("Saved map definitions")
        self.saved.activated.connect(self._load_saved)
        save = QPushButton("Save")
        save.setToolTip("Save this map definition")
        save.clicked.connect(self._save_def)
        delete = QPushButton("Delete")
        delete.setToolTip("Delete the selected saved map")
        delete.clicked.connect(self._delete_def)
        saved_row = QHBoxLayout()
        saved_row.setSpacing(6)
        saved_row.addWidget(self.saved, 1)
        saved_row.addWidget(save)
        saved_row.addWidget(delete)
        sb.addSpacing(4)
        sb.addLayout(saved_row)
        sb.addSpacing(4)

        top = FlowLayout()
        show = QPushButton("Show map")
        show.setObjectName("primary")
        show.setToolTip("Read the map from the file (it also updates as you change the fields)")
        show.clicked.connect(self.show_map)
        top.addWidget(show)
        self.view_mode = QComboBox()
        self.view_mode.addItem("This file", "a")
        self.view_mode.addItem("Other file", "b")
        self.view_mode.addItem("Difference (other − this)", "diff")
        self.view_mode.setEnabled(False)
        self.view_mode.currentIndexChanged.connect(lambda _: self.show_map(quiet=True))
        self.view_mode.setToolTip("With a second file loaded: show this file, the other file, or the difference")
        top.addWidget(pair(muted("View"), self.view_mode))
        self.compare_label = muted("")
        top.addWidget(self.compare_label)
        layout.addLayout(top)
        self.banner = Banner()
        layout.addWidget(self.banner)

        form_outer = sb
        self.def_tabs = QTabWidget()
        map_tab = QWidget()
        form = QFormLayout(map_tab)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ignition timing (main)")
        self.address = QLineEdit("0x0")
        self.address.setToolTip("Where the map starts in the file, in hex. Addresses depend on the exact "
                                "software version; use Compare files to find what a tune changed.")
        self.rows = QSpinBox()
        self.rows.setRange(1, 256)
        self.rows.setValue(8)
        self.cols = QSpinBox()
        self.cols.setRange(1, 256)
        self.cols.setValue(8)
        self.dtype = QComboBox()
        self.dtype.addItems(list(DTYPES))
        self.factor = self._dspin(1.0)
        self.offset = self._dspin(0.0)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        self.decimals.setValue(1)
        form.addRow("Name", self.name)
        form.addRow("Address", self.address)
        form.addRow("Rows × cols", pair(self.rows, QLabel("×"), self.cols))
        self.decimals.setToolTip("Decimals shown")
        form.addRow("Data type", pair(self.dtype, QLabel("dp"), self.decimals))
        form.addRow("Value =", pair(QLabel("raw ×"), self.factor, QLabel("+"), self.offset))
        self.def_tabs.addTab(map_tab, "Map")
        self.x_axis = self._axis_box("X axis (columns)")
        self.y_axis = self._axis_box("Y axis (rows)")
        form_outer.addWidget(self.def_tabs)
        form_outer.addStretch(1)
        for w in (self.rows, self.cols, self.decimals):
            w.valueChanged.connect(lambda _: self.show_map(quiet=True))
        for w in (self.factor, self.offset):
            w.valueChanged.connect(lambda _: self.show_map(quiet=True))
        self.dtype.currentIndexChanged.connect(lambda _: self.show_map(quiet=True))
        self.address.editingFinished.connect(lambda: self.show_map(quiet=True))

        self.views = QTabWidget()
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.views.addTab(self.table, "Table")
        c = theme.colors()
        self.graph = pg.PlotWidget()
        self.graph.setBackground(c["surface"])
        self.graph.showGrid(x=True, y=True, alpha=0.15)
        self.graph.addLegend()
        from . import graphstyle
        graphstyle.theme_plot(self.graph)
        self.views.addTab(self.graph, "Graph (one line per row)")
        self.surface = SurfaceView()
        self.views.addTab(self.surface, "3D")
        layout.addWidget(self.views, 1)
        self._refresh_saved()

    @staticmethod
    def _dspin(value):
        spin = QDoubleSpinBox()
        spin.setRange(-1e6, 1e6)
        spin.setDecimals(4)
        spin.setMinimumWidth(60)
        spin.setValue(value)
        return spin

    def _axis_box(self, title):
        box = QWidget()
        f = QFormLayout(box)
        address = QLineEdit("")
        address.setPlaceholderText("none (shows 0, 1, 2…)")
        dtype = QComboBox()
        dtype.addItems(list(DTYPES))
        factor = self._dspin(1.0)
        offset = self._dspin(0.0)
        f.addRow("Address", address)
        f.addRow("Data type", dtype)
        f.addRow("Value =", pair(QLabel("raw ×"), factor, QLabel("+"), offset))
        self.def_tabs.addTab(box, title.split(" (")[0])
        address.editingFinished.connect(lambda: self.show_map(quiet=True))
        dtype.currentIndexChanged.connect(lambda _: self.show_map(quiet=True))
        factor.valueChanged.connect(lambda _: self.show_map(quiet=True))
        offset.valueChanged.connect(lambda _: self.show_map(quiet=True))
        return {"address": address, "dtype": dtype, "factor": factor, "offset": offset}

    # --- file ---

    def _open(self):
        start = str(user_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Open ECU file", start,
                                              "ECU images (*.bin *.ori *.mod *.sgo);;All files (*)")
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path):
        try:
            self.data = Path(path).read_bytes()
        except OSError as e:
            self.banner.show_message(f"Couldn't read the file: {e}", "error")
            return
        self.path = Path(path)
        self._show_files()
        self.banner.hide()
        self.show_map(quiet=True)

    def _show_files(self):
        if self.path is None:
            text, tip = "No file loaded", ""
        else:
            text = f"{self.path.name} · {_size(len(self.data))}"
            tip = f"{self.path}\n{len(self.data):,} bytes (0x{len(self.data):X})"
            if self.path_b is not None:
                text += f"   vs   {self.path_b.name}"
                tip += f"\n\nCompared with {self.path_b}"
        self.file_label.setText(text)
        self.file_label.setToolTip(tip)

    # --- definitions ---

    def definition(self):
        def axis(a):
            return {"address": a["address"].text().strip(), "dtype": a["dtype"].currentText(),
                    "factor": a["factor"].value(), "offset": a["offset"].value()}
        return {"name": self.name.text().strip(), "address": self.address.text().strip(),
                "rows": self.rows.value(), "cols": self.cols.value(), "dtype": self.dtype.currentText(),
                "factor": self.factor.value(), "offset": self.offset.value(),
                "decimals": self.decimals.value(), "x": axis(self.x_axis), "y": axis(self.y_axis)}

    def set_definition(self, d):
        widgets = (self.rows, self.cols, self.decimals, self.factor, self.offset, self.dtype)
        for w in widgets:
            w.blockSignals(True)
        self.name.setText(d.get("name", ""))
        self.address.setText(d.get("address", "0x0"))
        self.rows.setValue(d.get("rows", 1))
        self.cols.setValue(d.get("cols", 1))
        self.dtype.setCurrentText(d.get("dtype", "u8"))
        self.factor.setValue(d.get("factor", 1.0))
        self.offset.setValue(d.get("offset", 0.0))
        self.decimals.setValue(d.get("decimals", 1))
        for key, axis in (("x", self.x_axis), ("y", self.y_axis)):
            a = d.get(key) or {}
            axis["address"].setText(a.get("address", ""))
            axis["dtype"].setCurrentText(a.get("dtype", "u8"))
            axis["factor"].setValue(a.get("factor", 1.0))
            axis["offset"].setValue(a.get("offset", 0.0))
        for w in widgets:
            w.blockSignals(False)
        self.show_map(quiet=True)

    def _defs(self):
        return list(self.state.settings.get(DEFS_KEY) or [])

    def _refresh_saved(self):
        self.saved.clear()
        self.saved.addItem("Saved maps…", None)
        for i, d in enumerate(self._defs()):
            self.saved.addItem(f"{d.get('name') or 'Unnamed'} @ {d.get('address')}", i)

    def _load_saved(self, index):
        i = self.saved.itemData(index)
        if i is not None:
            self.set_definition(self._defs()[i])
        self.saved.setCurrentIndex(0)

    def _save_def(self):
        d = self.definition()
        if not d["name"]:
            name, ok = InputDialog.getText(self, "Save map", "Map name:")
            if not ok or not name.strip():
                return
            self.name.setText(name.strip())
            d["name"] = name.strip()
        defs = [x for x in self._defs() if x.get("name") != d["name"]]
        defs.append(d)
        self.state.settings.set(DEFS_KEY, defs)
        self._refresh_saved()

    def _delete_def(self):
        defs = self._defs()
        if not defs:
            return
        names = [f"{d.get('name') or 'Unnamed'} @ {d.get('address')}" for d in defs]
        name, ok = InputDialog.getItem(self, "Delete map", "Map:", names, 0, False)
        if ok:
            defs.pop(names.index(name))
            self.state.settings.set(DEFS_KEY, defs)
            self._refresh_saved()

    # --- display ---

    def _axis_values(self, axis, count):
        text = axis["address"].text().strip()
        if not text:
            return None
        return read_values(self.data, _hex(text), count, axis["dtype"].currentText(),
                           axis["factor"].value(), axis["offset"].value())

    def show_map(self, quiet=False):
        if self.data is None:
            if not quiet:
                self.banner.show_message("Open an ECU file first.", "warning")
            return
        rows, cols = self.rows.value(), self.cols.value()
        try:
            address = _hex(self.address.text())
            values = read_values(self.data, address, rows * cols, self.dtype.currentText(),
                                 self.factor.value(), self.offset.value()).reshape(rows, cols)
            xs = self._axis_values(self.x_axis, cols)
            ys = self._axis_values(self.y_axis, rows)
            other = None
            if self.data_b is not None:
                other = read_values(self.data_b, address, rows * cols, self.dtype.currentText(),
                                    self.factor.value(), self.offset.value()).reshape(rows, cols)
        except (ValueError, struct.error) as e:
            self.banner.show_message(str(e), "error")
            return
        self.banner.hide()
        self.values, self.other, self.xs, self.ys = values, other, xs, ys
        mode = self.view_mode.currentData() if other is not None else "a"
        shown = {"a": values, "b": other, "diff": (other - values) if other is not None else None}[mode]
        dec = self.decimals.value()
        fmt = lambda v: f"{v:+.{dec}f}" if mode == "diff" and v else f"{v:.{dec}f}"
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)
        axis_fmt = lambda v: f"{v:.6g}"
        self.table.setHorizontalHeaderLabels([axis_fmt(v) for v in xs] if xs is not None else [str(i) for i in range(cols)])
        self.table.setVerticalHeaderLabels([axis_fmt(v) for v in ys] if ys is not None else [str(i) for i in range(rows)])
        lo, hi = float(np.nanmin(shown)), float(np.nanmax(shown))
        span = max(abs(lo), abs(hi)) or 1.0
        for r in range(rows):
            for c in range(cols):
                v = shown[r, c]
                item = QTableWidgetItem(fmt(v))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if mode == "diff":
                    item.setBackground(_diverging(v / span))
                    if other is not None:
                        item.setToolTip(f"This file {values[r, c]:.{dec}f} → other {other[r, c]:.{dec}f}")
                else:
                    frac = 0.5 if hi == lo or math.isnan(v) else (v - lo) / (hi - lo)
                    item.setBackground(_heat(frac))
                item.setForeground(QColor("#111111"))
                self.table.setItem(r, c, item)
        if other is not None:
            diff = other - values
            changed = int(np.count_nonzero(np.abs(diff) > 1e-9))
            self.compare_label.setText(
                f"vs {self.path_b.name}: {changed} of {diff.size} cells differ"
                + (f" · biggest change {diff.flat[np.argmax(np.abs(diff))]:+.{dec}f}" if changed else ""))
        else:
            self.compare_label.setText("")
        self.graph.clear()
        x = xs if xs is not None else np.arange(cols)
        colors = ["#e8820c", "#3b82f6", "#22c55e", "#ef4444", "#a855f7", "#06b6d4", "#f59e0b", "#ec4899"]
        for r in range(rows):
            label = f"{ys[r]:.6g}" if ys is not None else f"row {r}"
            color = colors[r % len(colors)]
            self.graph.plot(x, shown[r], pen=pg.mkPen(color, width=2), name=label if rows <= 16 else None)
            if other is not None and mode != "diff":
                base = other if mode == "a" else values
                self.graph.plot(x, base[r], pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine))
        self.surface.set_data(shown, xs, ys, diverging=(mode == "diff"))

    # --- AI ---

    def _grid_text(self, grid):
        dec = self.decimals.value()
        head = ["Y\\X"] + ([f"{v:.6g}" for v in self.xs] if self.xs is not None else
                            [str(i) for i in range(grid.shape[1])])
        rows = [",".join(head)]
        for r in range(grid.shape[0]):
            label = f"{self.ys[r]:.6g}" if self.ys is not None else str(r)
            rows.append(",".join([label] + [f"{v:.{dec}f}" for v in grid[r]]))
        return "\n".join(rows)

    def ai_context(self):
        if getattr(self, "values", None) is None:
            return None
        d = self.definition()
        lines = [f"File: {self.path.name} ({len(self.data):,} bytes)",
                 f"Map: {d['name'] or 'unnamed'} at {d['address']}, {d['rows']}×{d['cols']} {d['dtype']}, "
                 f"value = raw × {d['factor']:g} + {d['offset']:g}",
                 f"X axis (columns): {d['x']['address'] or 'none'}; Y axis (rows): {d['y']['address'] or 'none'}",
                 "\nTable (CSV, rows = Y axis, columns = X axis):", self._grid_text(self.values)]
        if self.other is not None:
            lines += [f"\nSame map in {self.path_b.name}:", self._grid_text(self.other),
                      "\nDifference (other − this):", self._grid_text(self.other - self.values)]
            question = ("What changed in this map between the two files, where (which rpm/load area), and "
                        "what would that likely do to how the car drives? Anything risky?")
        else:
            question = ("What kind of map does this look like (shape, axes, units), and does anything look "
                        "unusual or risky?")
        return {"title": d["name"] or "map", "context": "\n".join(lines), "question": question,
                "extra": "The table was read from an ECU flash image using a map definition the user "
                            "typed in; the definition itself may be wrong, so say if the data looks like "
                            "it isn't a real map (random-looking values, wrong data type or address)."}

    # --- compare with a second file ---

    def _open_b(self):
        start = str(self.path.parent if self.path else user_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Compare with file", start,
                                              "ECU images (*.bin *.ori *.mod *.sgo);;All files (*)")
        if path:
            self.load_compare(Path(path))

    def load_compare(self, path: Path):
        try:
            self.data_b = Path(path).read_bytes()
        except OSError as e:
            self.banner.show_message(f"Couldn't read the file: {e}", "error")
            return
        self.path_b = Path(path)
        self._show_files()
        self.view_mode.setEnabled(True)
        self.clear_b.setEnabled(True)
        if self.view_mode.currentData() == "a":
            self.view_mode.setCurrentIndex(2)  # difference is what you usually want
        self.show_map(quiet=True)

    def _clear_compare(self):
        self.data_b = self.path_b = None
        self._show_files()
        self.view_mode.setCurrentIndex(0)
        self.view_mode.setEnabled(False)
        self.clear_b.setEnabled(False)
        self.show_map(quiet=True)


def _diverging(t):
    """-1 blue … 0 grey … +1 red, for difference tables."""
    t = max(-1.0, min(1.0, float(t)))
    grey = (200, 200, 200)
    end = (239, 68, 68) if t > 0 else (59, 130, 246)
    a = abs(t) ** 0.6
    return QColor(*[int(grey[k] + (end[k] - grey[k]) * a) for k in range(3)])


class SurfaceView(QWidget):
    """3D surface of a map, drawn with QPainter. Drag to rotate, wheel to zoom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.z = None
        self.xs = self.ys = None
        self.diverging = False
        self.azimuth = -50.0
        self.elevation = 32.0
        self.zoom = 1.0
        self._drag = None
        self.setMinimumHeight(160)
        self.setMouseTracking(False)

    def set_data(self, z, xs=None, ys=None, diverging=False):
        self.z = np.array(z, dtype=float)
        self.xs, self.ys, self.diverging = xs, ys, diverging
        self.update()

    def mousePressEvent(self, e):
        self._drag = (e.position(), self.azimuth, self.elevation)

    def mouseMoveEvent(self, e):
        if self._drag:
            start, az, el = self._drag
            d = e.position() - start
            self.azimuth = az + d.x() * 0.5
            self.elevation = max(5.0, min(85.0, el + d.y() * 0.4))
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        self.zoom = max(0.4, min(3.0, self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)))
        self.update()

    def _project(self, x, y, z):
        a, el = math.radians(self.azimuth), math.radians(self.elevation)
        # rotate around vertical axis, then tilt
        xr = x * math.cos(a) - y * math.sin(a)
        yr = x * math.sin(a) + y * math.cos(a)
        sy = yr * math.sin(el) - z * math.cos(el)
        depth = yr * math.cos(el) + z * math.sin(el)
        return xr, sy, depth

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPen, QPolygonF
        from PySide6.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.colors()
        p.fillRect(self.rect(), QColor(c["surface"]))
        if self.z is None or self.z.size == 0:
            p.setPen(QColor(c["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Show a map to see it in 3D")
            p.end()
            return
        z = self.z
        rows, cols = z.shape
        lo, hi = float(np.nanmin(z)), float(np.nanmax(z))
        span = (hi - lo) or 1.0
        absmax = max(abs(lo), abs(hi)) or 1.0
        # normalised grid: x,y in [-1, 1], height in [0, 0.9]
        def gx(ci): return -1 + 2 * ci / max(cols - 1, 1)
        def gy(ri): return -1 + 2 * ri / max(rows - 1, 1)
        def gz(v): return ((v - lo) / span) * 0.9 if not self.diverging else (v / absmax) * 0.6 + 0.3
        pts = [[self._project(gx(ci), gy(ri), gz(z[ri, ci])) for ci in range(cols)] for ri in range(rows)]
        w, h = self.width(), self.height()
        scale = min(w, h) * 0.33 * self.zoom
        cx, cy = w / 2, h / 2 + h * 0.08
        to_screen = lambda q: QPointF(cx + q[0] * scale, cy + q[1] * scale)
        quads = []
        for ri in range(rows - 1):
            for ci in range(cols - 1):
                corners = (pts[ri][ci], pts[ri][ci + 1], pts[ri + 1][ci + 1], pts[ri + 1][ci])
                depth = sum(q[2] for q in corners) / 4
                v = (z[ri, ci] + z[ri, ci + 1] + z[ri + 1, ci + 1] + z[ri + 1, ci]) / 4
                quads.append((depth, corners, v))
        quads.sort(key=lambda q: q[0])
        edge = QPen(QColor(0, 0, 0, 90), 1)
        for _depth, corners, v in quads:
            color = _diverging(v / absmax) if self.diverging else _heat((v - lo) / span)
            p.setBrush(color)
            p.setPen(edge)
            p.drawPolygon(QPolygonF([to_screen(q) for q in corners]))
        if rows == 1 or cols == 1:  # a 1-D map: draw it as a line
            p.setPen(QPen(QColor(c["accent"]), 3))
            flat = [to_screen(q) for row in pts for q in row]
            for a, b in zip(flat, flat[1:]):
                p.drawLine(a, b)
        # axis labels at the base corners
        p.setPen(QColor(c["muted"]))
        fmt = lambda v: f"{v:.6g}"
        labels = [
            ((gx(0), gy(rows - 1)), (fmt(self.xs[0]) if self.xs is not None else "0")),
            ((gx(cols - 1), gy(rows - 1)), (fmt(self.xs[-1]) if self.xs is not None else str(cols - 1))),
            ((gx(0), gy(0)), (fmt(self.ys[0]) if self.ys is not None else "0")),
        ]
        for (x, y), text in labels:
            q = to_screen(self._project(x * 1.12, y * 1.12, 0))
            p.drawText(QRectF(q.x() - 40, q.y() - 8, 80, 16), Qt.AlignmentFlag.AlignCenter, text)
        p.drawText(QRectF(8, 8, w - 16, 18), Qt.AlignmentFlag.AlignLeft,
                   ("Difference (other − this) " if self.diverging else "") +
                   f"min {lo:.4g} · max {hi:.4g}   ·   drag to rotate, scroll to zoom")
        p.end()


class FileCompare(QWidget):
    def __init__(self, viewer: MapViewer, tabs: QTabWidget, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.tabs = tabs
        self.a = self.b = None
        self.paths = [None, None]
        self.ranges = []
        # Left side (self.sidebar): the two files and the list of changed ranges.
        self.sidebar = QWidget()
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)
        self.labels = []
        for i, (title, hint) in enumerate((("File A", "stock"), ("File B", "tuned"))):
            button = QPushButton(f"{title}…")
            button.setToolTip(f"Choose {title} (e.g. the {hint} file)")
            button.setMinimumWidth(80)
            button.clicked.connect(lambda _=False, i=i: self._choose(i))
            label = ElidedLabel(f"e.g. {hint}")
            label.setObjectName("muted")
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(button)
            row.addWidget(label, 1)
            sb.addLayout(row)
            self.labels.append(label)
        sb.addSpacing(4)
        self.summary = muted("Choose two files to see where they differ.")
        self.summary.setWordWrap(True)
        sb.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Bytes", "A", "B"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in ((0, 80), (1, 80), (2, 60)):
            header.resizeSection(col, width)
        for col in (3, 4):  # the hex view on the right shows the bytes
            self.table.setColumnHidden(col, True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_hex(r))
        sb.addWidget(self.table, 1)
        self.open_map = QPushButton("Open in map viewer")
        self.open_map.setToolTip("Show the selected change as a table in the Map viewer")
        self.open_map.setEnabled(False)
        self.open_map.clicked.connect(self._to_viewer)

        # Right side (this widget): the bytes around the selected change.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 24 if theme.is_mac() else 16, 20 if theme.is_mac() else 16)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(muted("Bytes around the selected change (A above B; changed bytes highlighted)."), 1)
        top.addWidget(self.open_map)
        layout.addLayout(top)
        self.hex = QTextEdit()
        self.hex.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.hex.setFont(mono)
        layout.addWidget(self.hex, 1)

    def _choose(self, i):
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", str(user_dir()),
                                              "ECU images (*.bin *.ori *.mod *.sgo);;All files (*)")
        if path:
            self.load(i, Path(path))

    def load(self, i, path: Path):
        try:
            data = path.read_bytes()
        except OSError as e:
            self.summary.setText(f"Couldn't read {path.name}: {e}")
            return
        self.paths[i] = path
        if i == 0:
            self.a = data
        else:
            self.b = data
        self.labels[i].setText(f"{path.name} · {_size(len(data))}")
        self.labels[i].setToolTip(f"{path}\n{len(data):,} bytes")
        self._compare()

    def _compare(self):
        self.table.setRowCount(0)
        self.hex.clear()
        if self.a is None or self.b is None:
            self.ranges = []
            self.open_map.setEnabled(False)
            return
        self.ranges = diff_ranges(self.a, self.b)
        changed = sum(e - s for s, e in self.ranges)
        note = "" if len(self.a) == len(self.b) else f" Sizes differ ({len(self.a):,} vs {len(self.b):,} bytes)."
        self.summary.setText(f"{changed:,} byte(s) differ in {len(self.ranges)} range(s)." + note
                             if self.ranges else "The files are identical." + note)
        self.table.setRowCount(len(self.ranges))
        for r, (s, e) in enumerate(self.ranges):
            cells = (f"0x{s:06X}", f"0x{e - 1:06X}", f"{e - s:,}",
                     self.a[s:min(e, s + 16)].hex(" ").upper() + (" …" if e - s > 16 else ""),
                     self.b[s:min(e, s + 16)].hex(" ").upper() + (" …" if e - s > 16 else ""))
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        if self.ranges:
            self.table.selectRow(0)

    def _show_hex(self, r):
        self.open_map.setEnabled(0 <= r < len(self.ranges))
        if not 0 <= r < len(self.ranges):
            return
        s, e = self.ranges[r]
        lo = max(0, (s // 16) * 16 - 16)
        hi = min(max(len(self.a), len(self.b)), ((e + 15) // 16) * 16 + 16, lo + 16 * 24)
        c = theme.colors()
        hl = f"background:{c['err']};color:{c['on_accent']}"
        lines = []
        for off in range(lo, hi, 16):
            for tag, data, other in (("A", self.a, self.b), ("B", self.b, self.a)):
                cells = []
                for i in range(off, off + 16):
                    if i >= len(data):
                        cells.append("  ")
                        continue
                    text = f"{data[i]:02X}"
                    differs = i >= len(other) or data[i] != other[i]
                    cells.append(f"<span style='{hl}'>{text}</span>" if differs else text)
                lines.append(f"{tag} {off:06X}  " + " ".join(cells))
            lines.append("")
        self.hex.setHtml("<pre style='margin:0'>" + "\n".join(lines) + "</pre>")

    def ai_context(self):
        if not self.ranges:
            return None
        lines = [f"File A: {self.paths[0].name} ({len(self.a):,} bytes)",
                 f"File B: {self.paths[1].name} ({len(self.b):,} bytes)",
                 f"{sum(e - s for s, e in self.ranges):,} bytes differ in {len(self.ranges)} range(s). "
                 "First 40 ranges (start-end, length, first bytes A → B):"]
        for s, e in self.ranges[:40]:
            lines.append(f"- 0x{s:06X}-0x{e - 1:06X} ({e - s} bytes): "
                         f"{self.a[s:min(e, s + 24)].hex(' ')} → {self.b[s:min(e, s + 24)].hex(' ')}")
        return {"title": "file changes", "context": "\n".join(lines), "question":
               "Looking at where these two ECU files differ, what kind of changes are these likely to be "
               "(map edits, checksum, code, identification strings)? Which ranges look like maps worth "
               "opening in the map viewer?"}

    def _to_viewer(self):
        r = self.table.currentRow()
        if not 0 <= r < len(self.ranges) or self.paths[1] is None:
            return
        s, e = self.ranges[r]
        n = e - s
        cols = min(16, n)
        self.viewer.load_file(self.paths[0])
        self.viewer.load_compare(self.paths[1])
        self.viewer.set_definition({"name": f"Change at 0x{s:X}", "address": f"0x{s:X}",
                                    "rows": max(1, math.ceil(n / cols)), "cols": cols,
                                    "dtype": "u8", "factor": 1.0, "offset": 0.0, "decimals": 0})
        self.tabs.setCurrentIndex(0)


class MapsPage(QWidget):
    title = "Maps"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Top row: the tool switch, then the setup's toggle (a picker first, its column's toggle beside it)
        top = QHBoxLayout()
        top.setContentsMargins(16, 16, 16, 0)
        top.setSpacing(8)
        from .analyze_tools import icon_button
        self.sidebar_button = icon_button("tune", "Show or hide the map setup", checkable=True)
        outer.addLayout(top)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(6)
        outer.addWidget(self.splitter, 1)

        # ── Left sidebar: tool switch + that tool's controls ──
        sidebar = QWidget()
        sidebar.setObjectName("page")
        sidebar.setMinimumWidth(300)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(16, 10, 8, 16)
        sb.setSpacing(10)
        self.tabs = QTabBar()  # Map viewer | Compare files
        self.tabs.setToolTip("Look inside ECU files. Read-only: nothing is written to a file or a module.")
        self.tabs.setExpanding(False)  # compact pills like every other section row
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Map viewer")
        self.tabs.addTab("Compare files")
        top.addWidget(self.tabs)
        top.addWidget(self.sidebar_button)
        top.addStretch(1)
        if theme.is_mac():  # mac_shell's rule 5: the setup is a full-height column; its first row
            from .mac_shell import column, first_row  # is the tool switch + the setup's toggle
            outer.removeItem(top)
            top.setContentsMargins(0, 0, 0, 0)
            sb.insertLayout(0, first_row(top))
            column(sidebar)
            self._top = top
        self.side_stack = QStackedWidget()
        sb.addWidget(self.side_stack, 1)
        self.splitter.addWidget(scrollable(sidebar))

        # ── Right content: the selected tool's view ──
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("page")
        self.viewer = MapViewer(state)
        self.compare = FileCompare(self.viewer, self.tabs)
        for tool in (self.viewer, self.compare):
            tool.setObjectName("page")
            self.side_stack.addWidget(tool.sidebar)
            self.content_stack.addWidget(tool)
        if theme.is_mac():  # the main area: rules 2–4; row 1 gets the switch while the setup is hidden
            from .mac_shell import first_row, main_layout
            main = QWidget()
            main.setObjectName("page")
            ml = main_layout(QVBoxLayout(main))
            self._main_row = first_row(QHBoxLayout())
            self._main_row.setContentsMargins(0, 0, 0, 0)
            self._main_row.addStretch(1)  # the switch + toggle go in front of it, at their own width
            self._main_row_box = QWidget()
            self._main_row_box.setLayout(self._main_row)
            self._main_row_box.hide()
            ml.addWidget(self._main_row_box)
            ml.addWidget(self.content_stack, 1)
            self.splitter.addWidget(scrollable(main))
        else:
            self.splitter.addWidget(scrollable(self.content_stack))
        self.tabs.currentChanged.connect(self._tool_changed)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([420, 880])
        self.splitter.setCollapsible(1, False)
        if theme.is_mac():  # setup column | the tool
            from .mac_shell import column_split
            column_split(self.splitter, 420)  # its controls need more than a list's width
            for tool in (self.viewer, self.compare):  # the main area has the margins
                if tool.layout() is not None:
                    tool.layout().setContentsMargins(0, 0, 0, 0)
        toggle = collapsible_sidebar(self.splitter, state.settings, "maps", "Map setup", button=self.sidebar_button)
        # Setup hidden: the tool keeps the page's 16 px edge
        edge = self._mac_shown if theme.is_mac() else (
            lambda shown: self.content_stack.setContentsMargins(0 if shown else 8, 0, 0, 0))
        toggle.shown_changed.connect(edge)
        edge(self.sidebar_button.isChecked())
        state.open_in_maps.connect(self._open_pair)

    def _mac_shown(self, shown):
        """Rule 5: the tool switch + toggle are the setup column's first row; while it's hidden
        they start the main area instead, in the same spot."""
        for w in (self.tabs, self.sidebar_button):
            self._top.removeWidget(w)
            self._main_row.removeWidget(w)
        target = self._top if shown else self._main_row
        target.insertWidget(0, self.tabs)
        target.insertWidget(1, self.sidebar_button)
        self._main_row_box.setVisible(not shown)
        self.tabs.show()
        self.sidebar_button.show()

    def ai_context(self):
        """The open tool's data for the Assistant."""
        tool = self.content_stack.currentWidget()
        return tool.ai_context() if hasattr(tool, "ai_context") else None

    def _tool_changed(self, index):
        self.side_stack.setCurrentIndex(index)
        self.content_stack.setCurrentIndex(index)

    def _open_pair(self, a, b):
        if a and b:
            self.compare.load(0, Path(a))
            self.compare.load(1, Path(b))
            self.tabs.setCurrentIndex(1)
        elif a:
            self.viewer.load_file(Path(a))
            self.tabs.setCurrentIndex(0)

    def refresh(self):
        pass
