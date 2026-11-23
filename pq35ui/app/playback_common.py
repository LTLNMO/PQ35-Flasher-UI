"""Analyze: constants and log reading shared by playback_page.py and its section mixins."""
import csv
import warnings
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt
from . import log_export


GRAPH_SLOTS = 6


STATS_MODES = (("view", "What's shown"), ("all", "Whole log"), ("sel", "Selection"))


# Sidebar value columns you can show or hide: (key, header, tooltip). Column 0 is the channel.
SIDEBAR_COLUMNS = (
    ("now", "Now", "Value at the cursor"),
    ("cmp", "Compared", "Compared log's value at the cursor"),
    ("delta", "Δ", "Now minus compared"),
    ("min", "Min", "Lowest value (in the selected range, if one is set)"),
    ("max", "Max", "Highest value (in the selected range, if one is set)"),
    ("avg", "Avg", "Average value (in the selected range, if one is set)"),
)


SIDEBAR_DEFAULT = ("now",)


COL = {key: i + 1 for i, (key, _h, _t) in enumerate(SIDEBAR_COLUMNS)}


ROLE = Qt.ItemDataRole.UserRole


# Axis choices -> graphscale mode: (key, menu label, graphscale mode)
AXIS_MODES = (
    ("shared", "Shared: one y axis, real units (click a line to give it its own axis)", "real"),
    ("independent", "Independent: each channel fits its own range", "each"),
)


AXIS_SCALE = {key: mode for key, _label, mode in AXIS_MODES}


RAW_COL_MIN = 150  # px: Raw columns at least this wide, so a useful part of the name shows


def _parse_csv(path: Path):
    """Read a Datazap-style CSV. Returns (headers, time_array, data_dict)."""
    with open(path, "r", encoding="utf-8") as f:
        headers = next(csv.reader([f.readline()]), None)  # readline, not iteration: keeps f.tell() usable
        if not headers or not log_export.is_log_header(headers[0]):
            raise ValueError("Not a datalog this app can read (the first column must be the time).")
        headers = headers[:1] + [log_export.current_header(h) for h in headers[1:]]  # older names
        width = len(headers)
        # Fast path (numpy's C reader, ~3.5x quicker on long logs): every cell a plain number,
        # every row as wide as the header. Anything else (blank cells, MM:SS times, a units
        # row, quoting, ragged rows) falls through to the row-by-row reader below.
        start = f.tell()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # "input contained no data" on a header-only file
                data = np.loadtxt(f, delimiter=",", dtype=float, ndmin=2, comments=None)
        except ValueError:
            data = None
        if data is not None and data.shape[0] and data.shape[1] == width:
            columns = {name: data[:, i] for i, name in enumerate(headers[1:], 1)}
            return headers[1:], data[:, 0], _aligned(path, headers[1:], data[:, 0], columns)
        f.seek(start)
        reader = csv.reader(f)
        rows = []
        for row in reader:
            try:  # time as seconds (Datazap/MegaLog/generic) or MM:SS.mmm (Integrated Engineering)
                values = [log_export.parse_time(row[0])] + [
                    float(v) if v.strip() else float("nan") for v in row[1:width]]
            except (ValueError, IndexError):
                continue  # header-like rows, e.g. a units row
            values += [float("nan")] * (width - len(values))
            rows.append(values)
    if not rows:
        raise ValueError("CSV has no data rows.")
    data = np.array(rows)
    times = data[:, 0]
    columns = {}
    for i, name in enumerate(headers[1:], 1):
        columns[name] = data[:, i] if i < data.shape[1] else np.zeros(len(times))
    return headers[1:], times, _aligned(path, headers[1:], times, columns)


def _aligned(path, names, times, columns):
    """Each channel at its real read times when the recording has a log_info companion."""
    from . import log_info
    columns, _n = log_info.align(names, times, columns, log_info.load(path), log_export.current_header)
    return columns
