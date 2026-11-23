"""One-off check of the engine's measuring groups, straight from the car.

When the request file ``group_dump.request`` is in the app's data folder, the next real scan
reads every engine measuring group (1-255) once and saves, per field, the raw bytes the ECU
sent (formula number, A, B), our decoded value and unit, and the name the catalog gives that
slot. Afterwards the request file is removed, so later scans are as quick as before.

Written to <data folder>/Group dumps/engine_groups_<time>.json (everything) and .csv (one row
per field, for reading). Read-only: KWP 21 gg only, the same request the logger sends.
"""
import csv
import json
import time
from pathlib import Path

REQUEST_FILE = "group_dump.request"
OUT_DIR = "Group dumps"


def requested(data_dir) -> bool:
    return data_dir is not None and (Path(data_dir) / REQUEST_FILE).exists()


def _catalog(module):
    """{(group, field): [(id, name, unit), ...]} for one module, every documented location."""
    try:
        from flasher.measuring_catalog import VALUES
    except Exception:
        return {}
    out = {}
    for vid, name, unit, _kind, mod, locations in VALUES:
        if mod != module:
            continue
        for group, field in locations:
            out.setdefault((group, field), []).append((vid, name, unit))
    return out


def read_groups(kwp, tp, log, progress=None, first=1, last=255, groups=None, label="engine"):
    """Every group once. [{group, status, raw, fields?, nrc?, error?}]. Never raises.
    Starts in the session the scan left the ECU in; if the first groups are all refused, it
    switches to the logger's session (measuring.DEFAULT_SESSION) and starts again."""
    wanted = list(groups) if groups is not None else list(range(first, last + 1))
    groups = _read_range(kwp, tp, log, progress, wanted, label)
    if groups and not any(g.get("status") == "ok" for g in groups[:10]):
        from flasher.vag_protocols.measuring import DEFAULT_SESSION
        try:
            kwp.session(DEFAULT_SESSION)
            log(f"Group read: groups refused; switched to session 0x{DEFAULT_SESSION:02X}, reading again")
            groups = _read_range(kwp, tp, log, progress, wanted, label)
        except Exception as exc:
            log(f"Group read: session 0x{DEFAULT_SESSION:02X} not accepted ({exc})")
    return groups


def _read_range(kwp, tp, log, progress, wanted, label):
    from flasher.vag_protocols.measuring import decode_group
    groups = []
    failures = 0
    for n, group in enumerate(wanted, 1):
        if progress and n % 8 == 1:
            progress("SCAN", 50, f"Reading {label} measuring groups ({n}/{len(wanted)})")
        entry = {"group": group}
        try:
            reply = bytes(kwp.request(bytes([0x21, group]), raise_negative=False,
                                      validate_positive=False))
            entry["raw"] = reply.hex(" ")
            if reply[:1] == b"\x7f":
                entry["status"] = "refused"
                entry["nrc"] = f"0x{reply[2]:02X}" if len(reply) > 2 else None
            elif reply[:2] == bytes([0x61, group]):
                entry["status"] = "ok"
                entry["fields"] = decode_group(reply)
            else:
                entry["status"] = "unexpected reply"
            failures = 0
        except Exception as exc:
            entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
            failures += 1
        groups.append(entry)
        keepalive = getattr(tp, "maybe_send_keep_alive", None)
        if keepalive is not None:
            try:
                keepalive()
            except Exception:
                pass
        if failures >= 5:
            log(f"Group read: 5 errors in a row at group {group}; stopping the group read")
            break
    return groups


def write_report(data_dir, groups, identity, module=0x01):
    """Save the JSON + CSV. Removes the request file when the read got through every group."""
    catalog = _catalog(module)
    out_dir = Path(data_dir) / OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base = out_dir / f"engine_groups_{stamp}"
    rows = []
    for entry in groups:
        for field in entry.get("fields") or ():
            names = catalog.get((entry["group"], field["index"]), [])
            value = field["value"]
            rows.append({
                "group": entry["group"], "field": field["index"], "formula": field["formula"],
                "a": field["a"], "b": field["b"],
                "value": round(value, 4) if isinstance(value, float) else value,
                "unit": field["unit"], "known_formula": field["numeric"] or isinstance(value, str),
                "catalog_names": " | ".join(n for _i, n, _u in names),
                "catalog_units": " | ".join(sorted({u for _i, _n, u in names if u})),
            })
    # Done only when every group was asked and real data came back (else the next scan tries again).
    complete = bool(groups) and groups[-1]["group"] == 255 and any(g.get("status") == "ok" for g in groups)
    summary = {
        "read_at": stamp, "module": module, "complete": complete,
        "identity": {k: v for k, v in (identity or {}).items() if isinstance(v, (str, int, float, bool))},
        "groups_ok": [g["group"] for g in groups if g.get("status") == "ok"],
        "groups_refused": [g["group"] for g in groups if g.get("status") == "refused"],
        "groups_error": [g["group"] for g in groups if g.get("status") not in ("ok", "refused")],
        "formulas_seen": sorted({r["formula"] for r in rows}),
    }
    with open(f"{base}.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "groups": groups, "fields": rows}, fh, indent=1, default=str)
    with open(f"{base}.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["group"])
        writer.writeheader()
        writer.writerows(rows)
    if complete:
        try:
            (Path(data_dir) / REQUEST_FILE).unlink()
        except OSError:
            pass
    return f"{base}.json", summary
