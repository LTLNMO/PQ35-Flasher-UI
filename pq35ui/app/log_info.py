"""Companion data for each recording: when each channel was really read, and what the car sent.

The CSV stays Drew's Datazap format: one row per measuring-group read, every other column
forward-filled with its last value. That hides two things this file keeps:

  * groups: which group each row came from. A channel's value is only fresh on rows from its
    own group; on the others it's up to a whole read cycle old (~0.7 s with ten groups). VCDS
    keeps a time stamp per group for the same reason. Analyze uses this (align) to place each
    channel at its real read times, interpolating between two real reads instead of holding
    a stale value. Status codes and bit fields are never interpolated.
  * raw: the bytes of every group read (formula, A, B per field), one entry per row, and per
    column its group, field, formula and unit. Any log can be decoded again from these if a
    formula is ever corrected.

Kept out of Documents, in <data folder>/Log info/<csv file name>.json, written when the
recording stops. A log without one (older, imported, or moved) opens as before.
"""
import json
from pathlib import Path

import numpy as np

DIR = "Log info"
VERSION = 1

# Formulas whose values are codes, text or bit fields: never interpolated between reads.
DISCRETE_FORMULAS = {10, 16, 17, 29, 37, 44, 63}
DISCRETE_KINDS = {"status", "bitfield"}


def _folder() -> Path:
    from .storage import data_dir
    return data_dir() / DIR


def path_for(csv_path) -> Path:
    return _folder() / f"{Path(csv_path).name}.json"


def raw_hex(fields) -> str | None:
    """'01 c8 32 05 0a 78 …' for a sample's fields (formula, A, B each), or None if any is
    missing (e.g. a pretend module)."""
    out = []
    for f in fields or ():
        try:
            out += [int(f["formula"]), int(f["a"]), int(f["b"])]
        except (KeyError, TypeError, ValueError):
            return None
    return bytes(out).hex(" ") if out else None


def save(csv_path, columns: dict, groups: list, raw: list):
    """columns: {CSV header: {"group", "field", "formula", "unit", "kind", "id"}}; groups and
    raw: one entry per CSV data row, in order."""
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    data = {"version": VERSION, "csv": Path(csv_path).name, "columns": columns,
            "groups": groups, "raw": raw}
    target = path_for(csv_path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    return target


def load(csv_path):
    try:
        data = json.loads(path_for(csv_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == VERSION else None


def discard(csv_path):
    path_for(csv_path).unlink(missing_ok=True)


def rename(old, new):
    try:
        path_for(old).replace(path_for(new))
    except OSError:
        pass


def _interpolatable(meta) -> bool:
    if (meta.get("kind") or "number") in DISCRETE_KINDS:
        return False
    return meta.get("formula") not in DISCRETE_FORMULAS


def align(headers, times, columns, info, rename_header=lambda h: h):
    """Each channel at its real read times: (new columns dict, how many were aligned).

    For a channel from group g, only rows from group g hold a fresh value. Between two such
    rows the value is interpolated in time; after the last it's held, and rows before the first
    stay as recorded.
    Needs one group per data row (a log still recording, or cut in an editor, has a mismatch
    and is left as it was)."""
    if not info or len(info.get("groups") or ()) != len(times):
        return columns, 0
    groups = np.asarray(info["groups"])
    metas = {rename_header(h): m for h, m in (info.get("columns") or {}).items()}
    out, aligned = dict(columns), 0
    t = np.asarray(times, dtype=float)
    for name in headers:
        meta = metas.get(name)
        if not meta or not _interpolatable(meta) or name not in columns:
            continue
        values = np.asarray(columns[name], dtype=float)
        fresh = (groups == meta.get("group")) & np.isfinite(values)
        if fresh.sum() < 2:
            continue
        lined = np.interp(t, t[fresh], values[fresh])
        first = int(np.argmax(fresh))
        lined[:first] = values[:first]  # before its first read in the file: as recorded
        out[name] = lined
        aligned += 1
    return out, aligned
