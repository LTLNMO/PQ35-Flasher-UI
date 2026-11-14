"""Analyze's Heatmap view: a table laid out like an ECU map. Pick X (e.g. engine speed), Y
(e.g. load) and a value (e.g. ignition timing); every sample is put on the nearest X / Y
breakpoint and each cell shows the value's average (or max, min, count) there, coloured from
low (blue) to high (red)."""

import math
import re

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import log_export, theme

AGGREGATES = (("mean", "Average"), ("max", "Max"), ("min", "Min"), ("count", "Samples"))
SETTING = "playback:heatmap"
# low -> high colour stops
STOPS = ((0.0, (59, 130, 246)), (0.35, (34, 197, 94)), (0.65, (234, 179, 8)), (1.0, (239, 68, 68)))

_X_GUESS = [r"engine speed", r"\brpm\b"]
_Y_GUESS = [r"load", r"boost pressure \(actual", r"\bmaf\b", r"air mass", r"throttle"]
_V_GUESS = [r"timing retard", r"retard", r"ignition", r"lambda", r"boost"]


def nice_step(span, count):
    """A round step (1, 2, 2.5, 5 x 10^k) giving about `count` breakpoints over `span`."""
    if span <= 0 or count < 1:
        return 1.0
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def breakpoints(values, count):
    """Round-number breakpoints covering the values (ECU-map style axis)."""
    lo, hi = float(np.min(values)), float(np.max(values))
    if hi == lo:
        return np.array([lo])
    step = nice_step(hi - lo, count - 1)
    start = math.floor(lo / step + 0.5) * step
    end = math.floor(hi / step + 0.5) * step
    n = int(round((end - start) / step)) + 1
    return start + step * np.arange(n)


def grid(x, y, v, nx, ny, how="mean"):
    """(x breakpoints, y breakpoints, table[ny, nx] of values (nan = no samples), counts)."""
    ok = np.isfinite(x) & np.isfinite(y) & (np.isfinite(v) if how != "count" else True)
    x, y, v = x[ok], y[ok], v[ok]
    if not len(x):
        return np.array([]), np.array([]), np.zeros((0, 0)), np.zeros((0, 0), dtype=int)
    bx, by = breakpoints(x, nx), breakpoints(y, ny)
    ix = np.clip(np.rint((x - bx[0]) / ((bx[1] - bx[0]) if len(bx) > 1 else 1)), 0, len(bx) - 1).astype(int)
    iy = np.clip(np.rint((y - by[0]) / ((by[1] - by[0]) if len(by) > 1 else 1)), 0, len(by) - 1).astype(int)
    cell = iy * len(bx) + ix
    size = len(bx) * len(by)
    counts = np.bincount(cell, minlength=size)
    if how == "count":
        table = counts.astype(float)
        table[counts == 0] = np.nan
    elif how == "mean":
        sums = np.bincount(cell, weights=v, minlength=size)
        with np.errstate(invalid="ignore", divide="ignore"):
            table = sums / counts
    else:
        table = np.full(size, np.inf if how == "min" else -np.inf)
        (np.minimum if how == "min" else np.maximum).at(table, cell, v)
        table[counts == 0] = np.nan
    return bx, by, table.reshape(len(by), len(bx)), counts.reshape(len(by), len(bx))


def colour(t):
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return QColor(*[round(a + (b - a) * f) for a, b in zip(c0, c1)])
    return QColor(*STOPS[-1][1])


from .units import fmt as _fmt  # one number style on every tab


def _guess(names, patterns, avoid=()):
    for pat in patterns:
        for n in names:
            if n not in avoid and re.search(pat, n, re.I):
                return n
    rest = [n for n in names if n not in avoid]
    return rest[0] if rest else None


class HeatmapView(QWidget):
    def __init__(self, settings, on_change, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.x_combo, self.y_combo, self.v_combo = QComboBox(), QComboBox(), QComboBox()
        self.agg = QComboBox()
        for key, label in AGGREGATES:
            self.agg.addItem(label, key)
        self.nx, self.ny = QSpinBox(), QSpinBox()
        for spin, default in ((self.nx, 16), (self.ny, 12)):
            spin.setRange(3, 40)
            spin.setValue(default)
            spin.setToolTip("About how many breakpoints along this axis (rounded to even steps)")
            spin.setFixedWidth(64)
        for label, w, stretch in (("X", self.x_combo, 2), ("Y", self.y_combo, 2), ("Value", self.v_combo, 2),
                                  ("Show", self.agg, 0), ("Columns", self.nx, 0), ("Rows", self.ny, 0)):
            lab = QLabel(label)
            lab.setObjectName("muted")
            row.addWidget(lab)
            row.addWidget(w, stretch)
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(lambda _: self._changed())
                w.setMinimumWidth(90 if w is self.agg else 150)
                # Don't grow to the longest channel name: that made the whole Analyze page wider
                # than the window. They still stretch into the room there is; long names get …
                w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                w.setMinimumContentsLength(8)
                if w is self.agg:
                    w.setFixedWidth(110)
            else:
                w.valueChanged.connect(lambda _: self._changed())
        lay.addLayout(row)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(30)
        self.table.verticalHeader().setMinimumSectionSize(18)
        self.table.setShowGrid(True)
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        lay.addWidget(self.table, 1)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)  # a long summary wraps instead of widening the page
        lay.addWidget(self.note)
        self._filling = False
        self._restore()

    # --- choices ---

    def _restore(self):
        saved = self.settings.get(SETTING) or {}
        self._saved = saved if isinstance(saved, dict) else {}
        self.agg.setCurrentIndex(max(self.agg.findData(self._saved.get("agg", "mean")), 0))
        self.nx.setValue(int(self._saved.get("nx", 16)))
        self.ny.setValue(int(self._saved.get("ny", 12)))

    def _save(self):
        def base(combo):
            n = combo.currentData()
            return log_export.split_header(n)[0] if n else None
        self._saved = {"x": base(self.x_combo), "y": base(self.y_combo), "v": base(self.v_combo),
                       "agg": self.agg.currentData(), "nx": self.nx.value(), "ny": self.ny.value()}
        self.settings.set(SETTING, self._saved)

    def _changed(self):
        if self._filling:
            return
        self._save()
        self.on_change()

    def set_names(self, names):
        """Fill the channel pickers for a new log: your last choice by name, else a good guess."""
        by_base = {log_export.split_header(n)[0]: n for n in names}
        x = by_base.get(self._saved.get("x")) or _guess(names, _X_GUESS)
        y = by_base.get(self._saved.get("y")) or _guess(names, _Y_GUESS, avoid=(x,))
        v = by_base.get(self._saved.get("v")) or _guess(names, _V_GUESS, avoid=(x, y))
        self._filling = True
        for combo, pick in ((self.x_combo, x), (self.y_combo, y), (self.v_combo, v)):
            combo.clear()
            for n in names:
                combo.addItem(n, n)
            combo.setCurrentIndex(max(combo.findData(pick), 0))
        self._filling = False

    # --- drawing ---

    def draw(self, columns, mask):
        xn, yn, vn = self.x_combo.currentData(), self.y_combo.currentData(), self.v_combo.currentData()
        self.table.clear()
        if not (xn and yn and vn):
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        how = self.agg.currentData()
        bx, by, table, counts = grid(columns[xn][mask], columns[yn][mask], columns[vn][mask],
                                     self.nx.value(), self.ny.value(), how)
        self.table.setColumnCount(len(bx))
        self.table.setRowCount(len(by))
        if not len(bx):
            self.note.setText("No samples in this part of the log.")
            return
        self.table.setHorizontalHeaderLabels([_fmt(b) for b in bx])
        self.table.setVerticalHeaderLabels([_fmt(b) for b in by])
        x_name, x_unit = log_export.split_header(xn)
        y_name, y_unit = log_export.split_header(yn)
        self.table.horizontalHeader().setToolTip(f"X: {xn}")
        self.table.verticalHeader().setToolTip(f"Y: {yn}")
        finite = table[np.isfinite(table)]
        lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
        c = theme.colors()
        for r in range(len(by)):
            for col in range(len(bx)):
                val = table[r, col]
                item = QTableWidgetItem("")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFont(self.mono)
                n = int(counts[r, col])
                if np.isfinite(val):
                    t = 0.5 if hi == lo else (val - lo) / (hi - lo)
                    bg = colour(t)
                    bg.setAlpha(215)
                    item.setBackground(bg)
                    item.setForeground(QColor("#111111") if 0.25 < t < 0.8 else QColor("#ffffff"))
                    item.setText(str(n) if how == "count" else _fmt(val))
                else:
                    item.setBackground(QColor(c["surface"]))
                item.setToolTip(f"{x_name} ≈ {_fmt(bx[col])} {x_unit}\n{y_name} ≈ {_fmt(by[r])} {y_unit}\n"
                                + (f"{dict(AGGREGATES)[how]}: {_fmt(val)}\n" if np.isfinite(val) and how != "count" else "")
                                + f"{n:,} samples")
                self.table.setItem(r, col, item)
        label = dict(AGGREGATES)[how]
        self.note.setText(f"{label} of {vn} by {x_name} (across) and {y_name} (down) · "
                          f"{int(counts.sum()):,} samples · range {_fmt(lo)} to {_fmt(hi)}")
