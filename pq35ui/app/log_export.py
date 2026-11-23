"""Convert a PQ35 Flasher datalog (Datazap CSV) into other log formats.

The logger always writes the Datazap CSV ("Time", then "Name (unit)" columns);
these exporters read that file back and write a copy in another format."""
import csv
import math
import re
import time
from pathlib import Path

_UNIT = re.compile(r"^(.*?) \(([^()]*)\)$")

# (key, label, extension, file dialog filter). "datazap" is the Standard log itself (Drew's
# Datazap CSV, what the Logger records); the key stays "datazap" for saved settings.
FORMATS = (
    ("datazap", "Standard CSV (Datazap)", ".csv", "Standard CSV, Datazap (*.csv)"),
    ("ie", "Integrated Engineering CSV", ".csv", "Integrated Engineering log (*.csv)"),
    ("megalog", "MegaLogViewer (.msl)", ".msl", "MegaLogViewer log (*.msl)"),
    ("generic", "Generic CSV (name + unit rows)", ".csv", "Generic CSV (*.csv)"),
)


# Integrated Engineering CSV: our channel name (as the ECU reports it, from the
# Standard log) -> (Integrated Engineering's header, exactly as POWERlink writes it; how Integrated Engineering shows the number).
# In Integrated Engineering's column order. Integrated Engineering's own quirks are copied on purpose so an export looks like
# a real POWERlink log of this car:
#   - group 106 labels are shifted: Integrated Engineering calls the rail setpoint "Fuel Rail Actual", the
#     rail actual "LPFP Duty", HPFP duty "Fuel Rail Spec" and fuel temp "Fuel Pump
#     Shut-Off Time";
#   - Integrated Engineering decodes both rail pressures as 0.04*A*B "mbar" instead of (A*256+B)*0.01 bar;
#   - group 1 field 4 (status bits) is "Lambda Control Value Bank 2", group 122 field 4
#     (transmission status) is "Intervention (Bar)".
# Columns with no Integrated Engineering name keep ours. Only the Integrated Engineering export uses this; the Standard log
# never changes.
IE_COLUMNS = (
    ("Engine speed", "Engine Speed (rpm)", "int"),
    ("Engine load (actual)", "Actual Load (%)", "int"),
    ("Injection duration", "Injection Time (ms)", "num"),
    ("Mass air flow (G70)", "Mass Air Flow (g/s)", "fixed1"),
    ("Throttle valve position", "Throttle Valve Angle (%)", "int"),
    ("Ignition timing", "Ignition Angle (°)", "num"),
    ("Terminal 30 voltage (engine ECU)", "Voltage (V)", "num"),
    ("Coolant temperature (G62)", "Coolant Temperature (°C)", "int"),
    ("Intake air temperature (G42)", "Intake Air Temperature (°C)", "int"),
    ("Timing retard cylinder 1", "Ignition Correction Cyl 1 (Deg)", "num"),
    ("Timing retard cylinder 2", "Ignition Correction Cyl 2 (Deg)", "num"),
    ("Timing retard cylinder 3", "Ignition Correction Cyl 3 (Deg)", "num"),
    ("Timing retard cylinder 4", "Ignition Correction Cyl 4 (Deg)", "num"),
    ("Fuel rail pressure (specified)", "Fuel Rail Actual (mbar)", "rail"),
    ("Fuel rail pressure (actual)", "LPFP Duty (mbar)", "rail"),
    ("Fuel pump duty", "Fuel Rail Spec (%)", "int"),
    ("Fuel temperature (calculated)", "Fuel Pump Shut-Off Time (C)", "int"),
    ("Engine load (specified)", "Specified Load (%)", "int"),
    ("Engine load (specified corrected)", "Request Load (%)", "int"),
    ("Boost control valve duty (N75)", "N75 Duty (%)", "int"),
    ("Boost pressure (specified absolute)", "Requested Boost (mbar)", "int"),
    ("Boost pressure (actual absolute)", "Actual Boost (mbar)", "int"),
    ("Engine torque specified by transmission", "Specified Moment Trans (Nm)", "num"),
    ("Engine torque (actual, group 122)", "Engine Moment (Nm)", "num"),
    ("Engine torque (actual)", "Engine Moment (Nm)", "num"),  # if logged from group 120 instead
    ("Transmission torque intervention", "Intervention (Bar)", "num"),
    ("Transmission status (engine ECU)", "Intervention (Bar)", "num"),  # its name before 2026-11-22
    ("Lambda control bank 1", "Lambda Control Value Bank 1 (%)", "int"),
    ("Basic setting requirements", "Lambda Control Value Bank 2 ()", "bits"),
)


def _num(x):
    """Shortest plain text for a number: 880, 1.275, 13.536 (no exponent, no .0)."""
    x = round(x, 6)
    return str(int(x)) if x == int(x) else repr(x)


def ie_value(kind, text):
    """One cell as POWERlink shows it, from a Standard-log cell."""
    try:
        x = float(text)
    except (TypeError, ValueError):
        return text
    if kind == "int":
        return str(math.floor(x + 0.5))
    if kind == "fixed1":
        return f"{x:.1f}"
    if kind == "bits":
        return format(int(x) & 0xFF, "08b")
    if kind == "rail":
        # Undo our (A*256+B)*0.01 bar to the raw bytes, then apply Integrated Engineering's 0.04*A*B.
        raw = max(0, min(0xFFFF, int(round(x * 100))))
        return _num(round(0.04 * (raw >> 8) * (raw & 0xFF), 2))
    return _num(x)


def ie_columns(names, units=None):
    """[(source index, Integrated Engineering header, kind)] in Integrated Engineering's order, then unmapped columns as-is.
    names/units exclude Time. A unitless header like "Transmission status (engine ECU)"
    splits into a false name/unit, so the whole header is matched too."""
    units = units or [""] * len(names)
    index = {}
    for i, (name, unit) in enumerate(zip(names, units)):
        index.setdefault(name, i)
        if unit:
            index.setdefault(f"{name} ({unit})", i)
    out, used, headers = [], set(), set()
    for ours, header, kind in IE_COLUMNS:
        i = index.get(ours)
        if i is None or i in used or header in headers:
            continue
        out.append((i, header, kind))
        used.add(i)
        headers.add(header)
    return out, [i for i in range(len(names)) if i not in used]


def parse_time(text):
    """Seconds from '12.345' (Datazap) or 'MM:SS.mmm' (Integrated Engineering)."""
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
        return seconds
    return float(text)


def ie_time(seconds):
    """Integrated Engineering's 'MM:SS.mmm' time stamp."""
    millis = int(round(seconds * 1000))
    minutes, millis = divmod(millis, 60_000)
    return f"{minutes:02d}:{millis // 1000:02d}.{millis % 1000:03d}"


def is_log_header(first_cell):
    return first_cell.strip().lower().startswith("time")


# Column names our logs used before a label was corrected -> today's name, applied whenever a
# log is opened (Analyze, Dashboard, Dyno, AI tools) or exported. The files stay as recorded.
LEGACY_HEADERS = {
    # formula 31 is lambda (a ratio), not a temperature: "°C" made Imperial show 1.0 as 33.8 °F
    "Lambda control bank 1 (actual) (°C)": "Lambda control bank 1 (actual)",
    "Lambda control bank 1 (specified) (°C)": "Lambda control bank 1 (specified)",
    # group 122 field 4 is an intervention status code (formula 37), not seconds
    "Transmission status (engine ECU) (s)": "Transmission torque intervention",
    "Transmission status (engine ECU)": "Transmission torque intervention",
    # formula 81 is an angle (crank degrees); it used to be logged with no unit
    "Camshaft phase position intake bank 1": "Camshaft phase position intake bank 1 (°)",
}


def current_header(header):
    """Today's name for a column of an older log of ours (LEGACY_HEADERS), else unchanged."""
    return LEGACY_HEADERS.get(header, header)


def split_header(header):
    """'Boost pressure (mbar)' -> ('Boost pressure', 'mbar')."""
    match = _UNIT.match(header)
    return (match.group(1), match.group(2)) if match else (header, "")


def _read(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or not rows[0] or not is_log_header(rows[0][0]):
        raise ValueError("Not a datalog this app can read (the first column must be the time).")
    headers, body = [current_header(h) for h in rows[0]], []
    headers[0] = "Time"
    for row in rows[1:]:
        try:  # Integrated Engineering logs use MM:SS.mmm; store seconds so every format converts the same way
            body.append([f"{parse_time(row[0]):.3f}"] + row[1:])
        except (ValueError, IndexError):
            continue  # e.g. the units row of a generic CSV
    return headers, body


_OUR_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{6})")
_OLD_STAMP = re.compile(r"(\d{8}-\d{6})")
_IE_STAMP = re.compile(r"Datalog-(\d{2}-\d{2}-\d{4}_\d{2}-\d{2})")


def log_start(source: Path) -> float:
    """When the log was recorded: from its name (ours or Integrated Engineering's), else the file's time."""
    for pattern, fmt in ((_OUR_STAMP, "%Y-%m-%d_%H%M%S"), (_OLD_STAMP, "%Y%m%d-%H%M%S"),
                         (_IE_STAMP, "%m-%d-%Y_%H-%M")):
        match = pattern.search(source.name)
        if match:
            try:
                return time.mktime(time.strptime(match.group(1), fmt))
            except ValueError:
                pass
    try:
        return source.stat().st_mtime
    except OSError:
        return time.time()


def log_vin(source: Path, fallback=None):
    """The car's VIN for a log: in its file name (Integrated Engineering's and older logs of ours
    carry it), else the Recent logs index (new logs keep it there), else fallback."""
    from .recent_logs import vin_for
    from .vehicle import find_vin
    return find_vin(source.stem) or vin_for(source) or fallback or None


def _free(path: Path) -> Path:
    """path, or 'name (2).ext', 'name (3).ext'… if it's taken."""
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def default_path(source: Path, fmt: str, vin=None, folder: Path | None = None, stem=None) -> Path:
    """Suggested export path, in Datalogs (or `folder`), never over an existing file:
      Standard:   engine_2026-11-09_073012.csv (the log's own name)
      Integrated Engineering: their own pattern, Datalog-MM-DD-YYYY_HH-MM_<VIN>.csv, so it
                  looks like a POWERlink log (rename it to anything you like)
      MegaLog:    engine_2026-11-09_073012.msl
      Generic:    engine_2026-11-09_073012 (generic).csv"""
    from .storage import datalogs_dir
    folder = Path(folder) if folder else datalogs_dir()
    stem = stem or source.stem
    ext = next(f[2] for f in FORMATS if f[0] == fmt)
    if fmt == "ie":
        from .vehicle import ie_log_name
        if _IE_STAMP.match(stem):
            return _free(folder / (stem + ext))
        return _free(folder / ie_log_name(log_vin(source, vin), log_start(source)))
    suffix = {"generic": " (generic)"}.get(fmt, "")
    return _free(folder / (stem + suffix + ext))


def export(source: Path, fmt: str, out: Path | None = None, vin=None) -> Path:
    """Write source in the given format; returns the written path."""
    headers, rows = _read(source)
    out = out or default_path(source, fmt, vin)
    if out.resolve() == source.resolve():
        raise ValueError("Choose a different file name than the original log.")
    names, units = zip(*(split_header(h) for h in headers))
    names, units = list(names), list(units)
    units[0] = "s"
    if fmt == "megalog":
        with open(out, "w", newline="", encoding="utf-8") as f:
            f.write('"PQ35 Flasher datalog"\n')
            f.write(f'"Capture Date: {time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(source.stat().st_mtime))}"\n')
            f.write("\t".join(names) + "\n")
            f.write("\t".join(units) + "\n")
            for row in rows:
                f.write("\t".join(row) + "\n")
    elif fmt == "generic":
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(names)
            writer.writerow(units)
            writer.writerows(rows)
    elif fmt == "ie":
        # Integrated Engineering: "Time (min:sec.ms)" with MM:SS.mmm stamps; known channels
        # get Integrated Engineering's names, order and number format (IE_COLUMNS); any others keep ours as
        # "Name (unit)", with "()" when there's no unit.
        mapped, rest = ie_columns(names[1:], units[1:])
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Time (min:sec.ms)"] + [h for _, h, _ in mapped]
                            + [f"{names[i + 1]} ({units[i + 1]})" for i in rest])
            for row in rows:
                cells = row[1:]
                writer.writerow([ie_time(float(row[0]))]
                                + [ie_value(kind, cells[i]) if i < len(cells) else "" for i, _, kind in mapped]
                                + [cells[i] if i < len(cells) else "" for i in rest])
    elif fmt == "datazap":
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    return out


def write_copy(source: Path, fmt: str, out: Path, vin=None) -> Path:
    """Write the export file: Standard is the log copied as it is, the rest converted."""
    if fmt == "datazap" and out.resolve() != source.resolve():
        import shutil
        shutil.copyfile(source, out)
        return out
    return export(source, fmt, out, vin=vin)


def auto_export(source: Path, fmt: str, vin=None) -> Path:
    """The Logger's Auto-export (⚙): a copy in `fmt` straight into Datalogs, no dialog, under
    the name Export… would suggest. Marked exported in Recent logs. Raises on failure."""
    from .recent_logs import mark_exported
    vin = log_vin(source, vin)
    written = write_copy(source, fmt, default_path(source, fmt, vin), vin=vin)
    mark_exported(source, written)
    return written


def export_dialog(parent, state, source: Path, banner=None, stem=None):
    """Export… (Logger and Analyze): a save dialog that starts in Datalogs on the format used
    last (Standard the first time), then writes the copy and says where it went (with Show in
    Finder). source: a Standard log; stem: the name to suggest (an imported log's own name).
    Returns the written path, or None."""
    import sys
    from PySide6.QtWidgets import QFileDialog
    from .recent_logs import mark_exported
    from .widgets import reveal_file
    filters = ";;".join(f[3] for f in FORMATS)
    last = state.settings.get("export_format") or "datazap"
    last = last if any(f[0] == last for f in FORMATS) else "datazap"
    vin = log_vin(source, state.vin())
    default = default_path(source, last, vin, stem=stem)
    selected = next(f[3] for f in FORMATS if f[0] == last)
    path, chosen = QFileDialog.getSaveFileName(parent, "Export datalog", str(default), filters, selected)
    if not path:
        return None
    fmt = next((f[0] for f in FORMATS if f[3] == chosen), last)
    state.settings.set("export_format", fmt)
    out = Path(path)
    ext = next(f[2] for f in FORMATS if f[0] == fmt)
    if out.suffix.lower() != ext:
        out = out.with_suffix(ext)
    try:
        written = write_copy(source, fmt, out, vin=vin)
    except Exception as e:
        if banner is not None:
            banner.show_message(f"Export failed: {e}", "error")
        return None
    mark_exported(source, written)
    if banner is not None:
        reveal = "Show in Finder" if sys.platform == "darwin" else "Show in folder"
        banner.show_message(f"Exported to {written}", "ok", {reveal: lambda: reveal_file(written)})
    return written
