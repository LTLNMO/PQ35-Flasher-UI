"""Open logs from other tools: turn any of them into the app's own (Standard) log.

The Standard log is Drew's Datazap CSV: a "Time" column in seconds, then one "Name (unit)"
column per channel. The Logger writes it; Analyze, Compare and Export all work on it. A log
from somewhere else is converted once into a Standard copy (imported_logs_dir, a cache) and
Analyze opens that.

One reader covers the CSV-style logs Datazap takes (COBB Accessport, MHD, bootmod3, JB4,
EcuTek, VCDS, ME7Logger, EvoScan, Torque, DashCommand, RomRaider, Simos Tools, Tactrix,
Hondata, MegaLogViewer .msl, Haltech CSV, Integrated Engineering …). They differ in a few
ways only, and each is handled:
  - text lines above the column names (tool, car, date…)
  - a units row under the names, or units in the names as (unit) or [unit]
  - comma, semicolon (with decimal commas), tab or | between cells
  - what the time column is called, where it is, and how it's written: seconds,
    milliseconds, MM:SS.mmm, HH:MM:SS, a clock/date time or a Unix time
  - VCDS's stacked headers (group row, names row, units row) and per-field TIME columns
  - text columns (notes, markers, gear letters) are dropped
Integrated Engineering logs also get the ECU's own names back where their values match
the ECU's (see _IE_BACK); the two rail-pressure columns they scale differently keep
Integrated Engineering's names, so they never pass for Standard values."""
import csv
import datetime as _dt
import io
import re
from pathlib import Path

from . import log_export

# --- helpers ---------------------------------------------------------------------------------

_BRACKET = re.compile(r"^(.*?)\s*\[([^\[\]]*)\]\s*$")
_PAREN_TIGHT = re.compile(r"^(.*?\S)\(([^()]*)\)\s*$")
_CLOCK = re.compile(r"^\d{1,3}(:\d{1,2}){1,2}([.,]\d+)?$")
_NUM = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%d-%b-%Y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S",
    "%a %b %d %H:%M:%S %Z %Y", "%a %b %d %H:%M:%S %Y", "%H:%M:%S.%f", "%H:%M:%S",
)
# How common time columns are named (lower case). Exact names win over "contains".
_TIME_EXACT = ("time", "time (s)", "time(s)", "time (sec)", "time(sec)", "time [s]", "time (seconds)",
               "seconds", "secs", "sec", "elapsed", "elapsed time", "stamp", "timestamp",
               "time stamp", "logentryseconds", "section time", "offset", "t", "zeit",
               "time (min:sec.ms)", "time (ms)", "time(ms)", "time [ms]", "time (msec)")
_TIME_WORDS = ("time", "stamp", "second", "zeit", "elapsed")
_NOT_TIME = ("date", "gps", "lap", "ignition", "injection", "timing", "dwell", "shut-off",
             "opening", "duration", "runtime", "run time", "boost", "spool")
_DROP = ("marker", "logid", "log id")


def _is_num(text):
    return bool(_NUM.match(text.strip())) if text else False


def _to_float(text, comma_decimal=False):
    text = text.strip()
    if comma_decimal:
        text = text.replace(",", ".")
    return float(text)


def _norm_header(name, unit=""):
    """'Boost [psi]' / 'Boost(psi)' / 'Boost' + 'psi' -> 'Boost (psi)'."""
    name = " ".join((name or "").replace("﻿", "").split())
    unit = " ".join((unit or "").split())
    m = _BRACKET.match(name)
    if m:
        name, unit = m.group(1).strip(), unit or m.group(2).strip()
    else:
        n, u = log_export.split_header(name)
        if u:
            name, unit = n, unit or u
        else:
            m = _PAREN_TIGHT.match(name)
            if m:
                name, unit = m.group(1).strip(), unit or m.group(2).strip()
    if unit and unit not in ("-", "--", "N/A", "n/a", "none"):
        return f"{name} ({unit})"
    return name


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw[:2000].count(b"\x00") > 100:  # UTF-16 without a BOM
        return raw.decode("utf-16-le", errors="replace")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _sniff_delimiter(lines):
    best, best_score = ",", -1
    sample = [ln for ln in lines[:200] if ln.strip()]
    for d in (",", ";", "\t", "|"):
        counts = [len(next(csv.reader([ln], delimiter=d))) for ln in sample]
        wide = [c for c in counts if c > 1]
        if not wide:
            continue
        # the most common width among the last half of the sample (the data rows)
        tail = wide[len(wide) // 2:] or wide
        mode = max(set(tail), key=tail.count)
        score = tail.count(mode) * mode
        if score > best_score:
            best, best_score = d, score
    return best


def _parse_time_cell(text, comma_decimal=False):
    """Seconds (float) from a time cell, or None. Clock/date strings give seconds since
    midnight / the Unix epoch (made relative later)."""
    text = text.strip().strip('"')
    if not text:
        return None
    if _is_num(text):
        try:
            return _to_float(text, comma_decimal or ("," in text and "." not in text))
        except ValueError:
            return None
    if _CLOCK.match(text):
        try:
            return log_export.parse_time(text.replace(",", "."))
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            d = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if d.year == 1900:  # time of day only
            return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
        return d.timestamp()
    return None


# --- Integrated Engineering: their headers back to the ECU's names ---------------------------

_IE_UNIT = {"Deg": "°", "C": "°C"}
_UNITLESS = {"Transmission torque intervention", "Transmission status (engine ECU)",
             "Basic setting requirements"}


def _ie_back():
    """{IE header: (Standard header, kind)}, skipping the rail pressures (scaled 0.04*A*B by
    Integrated Engineering, which can't be undone)."""
    out = {}
    for ours, header, kind in log_export.IE_COLUMNS:
        if kind == "rail" or header in out:
            continue
        _name, unit = log_export.split_header(header)
        unit = "" if ours in _UNITLESS else _IE_UNIT.get(unit, unit)
        out[header] = (f"{ours} ({unit})" if unit else ours, kind)
    return out


_IE_BACK = _ie_back()


def _is_ie(headers):
    return bool(headers) and headers[0].strip().lower() == "time (min:sec.ms)"


# --- the reader ------------------------------------------------------------------------------

def is_native(path: Path) -> bool:
    """Our own log (Drew's Datazap CSV): first cell exactly 'Time', the next line a number."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            first, second = f.readline(), f.readline()
    except (OSError, UnicodeDecodeError):
        return False
    head = next(csv.reader([first]), [])
    if not head or head[0].strip() != "Time" or len(head) < 2 or "[" in first:
        return False
    cell = second.split(",", 1)[0].strip()
    return _is_num(cell)


def read(path: Path):
    """Any supported log -> (headers, rows, format label). headers = ["Time", "Name (unit)", …];
    rows = [[seconds, cell, …]] as text; empty cells stay empty."""
    path = Path(path)
    raw = path.read_bytes()
    if raw[:5] == b"MLVLG":
        raise ValueError("This is a MegaSquirt binary log (.mlg). Open it in MegaLogViewer and save it "
                         "as .msl or CSV, then open that here.")
    if raw[:4] == b"PK\x03\x04":
        raise ValueError("This log is compressed (zip). Unzip it first, then open the file inside.")
    text = _decode(raw)
    if "\x00" in text[:4000]:
        raise ValueError("This is a binary log, not a text/CSV log, so it can't be opened here. "
                         "Export it as CSV from the tool that recorded it.")
    lines = text.splitlines()
    delim = _sniff_delimiter(lines)
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    comma_decimal = delim in (";", "\t") and any(
        re.match(r"^-?\d+,\d+$", c) for r in rows[:300] for c in r)

    def numeric_share(r):
        cells = [c for c in r if c]
        if len(cells) < 2:
            return 0.0
        good = sum(1 for c in cells if _is_num(c) or _CLOCK.match(c) or c.lower() in ("nan", "-"))
        return good / len(cells)

    # First data row: mostly numbers, and the next two rows too.
    start = None
    for i in range(min(len(rows), 400)):
        if numeric_share(rows[i]) >= 0.6 and all(
                numeric_share(rows[j]) >= 0.6 for j in range(i + 1, min(i + 3, len(rows)))):
            start = i
            break
    if start is None or start == 0:
        raise ValueError("Couldn't find the column names and data in this file. Is it a datalog?")
    width = max(len(r) for r in rows[start:start + 50])

    # Header rows: the up-to-4 text rows just above the data. Names = the wordiest; units =
    # a later row of short tokens.
    above = []
    for j in range(start - 1, max(-1, start - 5), -1):
        r = rows[j]
        if not any(r) or numeric_share(r) >= 0.6:
            break
        above.insert(0, r)

    def wordy(r):
        return sum(1 for c in r if re.search(r"[A-Za-z]{2,}", c) and len(c) > 2)

    names_row = max(above, key=lambda r: (wordy(r), above.index(r))) if above else []
    units_row = []
    after = above[above.index(names_row) + 1:] if names_row in above else []
    if after:
        cand = after[-1]
        tokens = [c for c in cand if c]
        if tokens and all(len(c) <= 12 for c in tokens):
            units_row = cand

    def cell(r, i):
        return r[i] if i < len(r) else ""

    headers = []
    for i in range(width):
        name, unit = cell(names_row, i), cell(units_row, i)
        if not name and unit and unit.lower() in ("stamp", "time", "marker"):  # VCDS: STAMP sits in the units row
            name, unit = unit, ""
        headers.append(_norm_header(name, unit) if name else "")

    ie = _is_ie(headers)
    data = rows[start:]

    # Time column
    def time_score(i):
        h = headers[i].lower()
        if not h or any(w in h for w in _NOT_TIME) and h not in _TIME_EXACT:
            return 0
        if h in _TIME_EXACT:
            return 3
        return 2 if any(w in h for w in _TIME_WORDS) else 0

    def parsed_times(i):
        values = [_parse_time_cell(cell(r, i), comma_decimal) for r in data[:2000]]
        return [v for v in values if v is not None]

    candidates = sorted((i for i in range(width) if time_score(i)), key=lambda i: (-time_score(i), i))
    time_col = None
    for i in candidates:
        vals = parsed_times(i)
        if len(vals) >= max(2, len(data[:2000]) * 0.5) and \
                sum(b >= a for a, b in zip(vals, vals[1:])) >= 0.95 * (len(vals) - 1):
            time_col = i
            break
    if time_col is None:  # no named time column: the first column that only goes up
        for i in range(width):
            vals = parsed_times(i)
            if len(vals) >= len(data[:2000]) * 0.9 and len(set(vals)) > 1 and \
                    all(b >= a for a, b in zip(vals, vals[1:])):
                time_col = i
                break
    if time_col is None:
        raise ValueError("Couldn't find a time column in this log.")
    th = headers[time_col].lower()
    scale = 1.0
    if re.search(r"\b(ms|msec|millis|milliseconds)\b", th) and "min:sec" not in th:
        scale = 0.001
    elif re.search(r"\b(us|µs|micros)\b", th):
        scale = 1e-6

    # Channel columns: named, not time-like, not a marker, with at least one number
    keep = []
    seen = {}
    for i in range(width):
        h = headers[i]
        if i == time_col or not h or h.lower() in _DROP:
            continue
        if time_score(i) == 3 or h.lower().startswith("time"):
            continue  # other time stamps (VCDS gives every field its own)
        sample = [cell(r, i) for r in data[:3000]]
        if not any(_is_num(c) for c in sample) and not (ie and h in _IE_BACK
                                                        and _IE_BACK[h][1] == "bits"):
            continue
        out = h
        kind = None
        if ie and h in _IE_BACK:
            out, kind = _IE_BACK[h]
        n = seen.get(out, 0) + 1
        seen[out] = n
        if n > 1:
            out = f"{out} {n}"
        keep.append((i, out, kind))

    if not keep:
        raise ValueError("No numeric channels found in this log.")

    out_rows = []
    first = None
    for r in data:
        t = _parse_time_cell(cell(r, time_col), comma_decimal)
        if t is None:
            continue
        t *= scale
        if first is None:
            first = t
        # Unix / clock times start at their first stamp; plain run times keep theirs
        # (Integrated Engineering logs start part-way, e.g. 1:31.430)
        rel = t - first if first > 86400 or ":" in cell(r, time_col) and not ie else t
        row = [f"{rel:.3f}"]
        for i, _h, kind in keep:
            c = cell(r, i)
            if kind == "bits" and c and set(c) <= {"0", "1"}:
                row.append(str(int(c, 2)))
            elif _is_num(c):
                try:
                    row.append(log_export._num(_to_float(c, comma_decimal)))
                except ValueError:
                    row.append("")
            else:
                row.append("")
        out_rows.append(row)
    if not out_rows:
        raise ValueError("No data rows found in this log.")
    return ["Time"] + [h for _i, h, _k in keep], out_rows, _label(path, headers, ie, units_row, delim)


def _label(path, headers, ie, units_row, delim):
    if ie:
        return "Integrated Engineering CSV"
    joined = " ".join(headers).lower()
    if path.suffix.lower() == ".msl":
        return "MegaLogViewer"
    if "stamp" in joined and any(h.lower() == "marker" for h in headers):
        return "VCDS"
    if "ap info" in joined:
        return "COBB Accessport"
    if "logentryseconds" in joined.replace(" ", ""):
        return "EvoScan"
    if "device time" in joined or "gps time" in joined:
        return "Torque / DashCommand"
    if units_row:
        return "CSV with a units row"
    return {",": "CSV", ";": "CSV (semicolons)", "\t": "Tab-separated log", "|": "CSV (|)"}[delim]


# --- open in Analyze -------------------------------------------------------------------------

def native_path(path: Path):
    """(path of a Standard log to open, format label or None). Our own logs open as they are;
    others are converted once into imported_logs_dir (redone if the source changes)."""
    from .storage import imported_logs_dir
    path = Path(path)
    if is_native(path):
        return path, None
    folder = imported_logs_dir()
    source = str(path.resolve())
    for label_file in folder.glob("*.format"):  # converted before, and unchanged since?
        try:
            label, src = label_file.read_text(encoding="utf-8").split("\n", 1)
            cached = label_file.with_suffix(".csv")
            if src == source and cached.exists() and cached.stat().st_mtime >= path.stat().st_mtime:
                return cached, label
        except (OSError, ValueError):
            continue
    headers, rows, label = read(path)
    out = folder / (path.stem + ".csv")
    label_file = out.with_suffix(".format")
    if label_file.exists() and label_file.read_text(encoding="utf-8").split("\n", 1)[-1] != source:
        # same name from another folder: keep both
        n = 2
        while out.exists():
            out = folder / f"{path.stem} {n}.csv"
            n += 1
        label_file = out.with_suffix(".format")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    label_file.write_text(f"{label}\n{source}", encoding="utf-8")
    _prune(folder)
    return out, label


def _prune(folder, keep=15):
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".format").unlink(missing_ok=True)


OPEN_FILTER = ("Datalogs (*.csv *.msl *.txt *.log *.tsv *.dat);;CSV (*.csv);;"
               "MegaLogViewer (*.msl);;All files (*)")
