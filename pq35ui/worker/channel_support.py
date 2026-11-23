"""Which catalog channels this car's module really has, so the channel lists only offer those.

The catalog (flasher.measuring_catalog) was transcribed from one car's reference sheets; other
ECUs and software versions lay their groups out differently (a 2011 A3's MED17 refuses every
engine group above 127, for example). So a scan reads, once per module identity (part number +
software version), every group the catalog uses and remembers per group whether it answered
and what each field holds. The catalog request then:

  * drops a channel whose every documented location is missing on this module: the group is
    refused as out of range (NRC 0x31) twice, or the field isn't in the group's reply;
  * moves a channel to its first documented location that does exist, when its usual one
    doesn't.

Only "out of range" counts as missing: a timeout, "busy" or any other refusal leaves the group
unknown, and unknown never hides anything. Refused groups are asked again at the end of the
same read, and every later scan asks the missing ones once more (refusals are quick), so a
group that answers after all brings its channels back.

Read-only (KWP 21 gg, the logger's request). Kept in <data folder>/channel_support.json:
{"<module>": {"key": "<part>/<sw>", "read_at": ..., "groups": {"<g>": {"ok": bool,
"fields": [[formula, a, b], ...]}}}}. A module never read keeps the full catalog.
"""
import json
import time
from pathlib import Path

FILE = "channel_support.json"


def identity_key(identity) -> str:
    identity = identity or {}
    part = str(identity.get("software_part_number") or identity.get("part_number") or "").strip()
    version = str(identity.get("sw_version") or "").strip()
    return f"{part}/{version}" if part else ""


def _load(data_dir) -> dict:
    try:
        return json.loads((Path(data_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def for_module(data_dir, module, key=None):
    """The saved support for a module, or None. With key, only if it's for that identity."""
    entry = _load(data_dir).get(str(int(module)))
    if not entry or (key and entry.get("key") != key):
        return None
    return entry


def needs_read(data_dir, module, key) -> bool:
    return bool(key) and for_module(data_dir, module, key) is None


def catalog_groups(module) -> list:
    from flasher.measuring_catalog import VALUES
    return sorted({group for row in VALUES if row[4] == module for group, _f in row[5]})


OUT_OF_RANGE = "0x31"  # requestOutOfRange: the module has no such group


def _out_of_range(entry) -> bool:
    return entry.get("status") == "refused" and entry.get("nrc") == OUT_OF_RANGE


def from_reads(reads, again=()) -> dict:
    """{"<g>": {"ok", "fields"}} from group_dump.read_groups output (plus a second read of some
    groups, ``again``). A group is ok if either read answered, missing only if every read said
    out of range, and otherwise left out (unknown: timeouts, busy, other refusals)."""
    second = {entry["group"]: entry for entry in again}
    out = {}
    for entry in reads:
        tries = [entry] + ([second[entry["group"]]] if entry["group"] in second else [])
        good = next((t for t in tries if t.get("status") == "ok"), None)
        if good is not None:
            out[str(entry["group"])] = {"ok": True, "fields": [[f["formula"], f["a"], f["b"]]
                                                               for f in good.get("fields") or ()]}
        elif all(_out_of_range(t) for t in tries):
            out[str(entry["group"])] = {"ok": False, "fields": []}
    return out


def save(data_dir, module, key, groups):
    """Remember one module's groups. Not saved when nothing answered (a wrong session or a
    module that doesn't do measuring blocks would otherwise hide every channel)."""
    if not key or not any(g["ok"] for g in groups.values()):
        return False
    data = _load(data_dir)
    data[str(int(module))] = {"key": key, "read_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "groups": groups}
    path = Path(data_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
    return True


def read_and_save(kwp, tp, log, progress, data_dir, module, key, label="engine", reads=None):
    """Read the catalog's groups (or reuse reads already made) and save them. Never raises."""
    from .group_dump import read_groups
    try:
        wanted = set(catalog_groups(module))
        if reads is None:
            reads = read_groups(kwp, tp, log, progress, groups=sorted(wanted), label=label)
        reads = [r for r in reads if r["group"] in wanted]
        # Ask every group that didn't answer once more, after a moment: one refusal or timeout
        # is never enough to hide a channel.
        retry = [r["group"] for r in reads if r.get("status") != "ok"]
        again = []
        if retry:
            time.sleep(0.3)
            again = read_groups(kwp, tp, log, progress, groups=retry, label=f"{label} (again)")
        groups = from_reads(reads, again)
        if save(data_dir, module, key, groups):
            missing = sum(1 for g in groups.values() if not g["ok"])
            log(f"Channel check ({label}, {key}): {len(groups) - missing} groups answer, "
                f"{missing} refused")
    except Exception as exc:
        log(f"Channel check ({label}) failed: {type(exc).__name__}: {exc}")


def _slot_ok(groups, group, field):
    entry = groups.get(str(group))
    if entry is None:
        return None  # never asked or errored: unknown
    # (An all-zero slot isn't treated as empty: a real reading can be 0/0, e.g. engine off.)
    return entry["ok"] and field <= len(entry["fields"])


def filter_values(values, support):
    """(values the module has, each at a location it has; number hidden)."""
    if not support:
        return values, 0
    groups = support.get("groups") or {}
    kept, hidden = [], 0
    for value in values:
        locations = value.get("locations") or [[value["group"], value["field"]]]
        verdicts = [(_slot_ok(groups, g, f), g, f) for g, f in locations]
        usable = next(((g, f) for ok, g, f in verdicts if ok), None)
        if usable is None and all(ok is False for ok, _g, _f in verdicts):
            hidden += 1
            continue
        if usable is None:  # nothing known to work, something unknown: keep it as it was
            usable = (value["group"], value["field"])
        value = dict(value, group=usable[0], field=usable[1])
        kept.append(value)
    return kept, hidden


def recheck(kwp, tp, log, progress, data_dir, module, key, label="engine"):
    """A later scan of a module already checked: ask its missing groups again. Any that answer
    now are marked ok (their channels come back). Quick: a refusal takes a few ms. Never raises."""
    from .group_dump import read_groups
    try:
        entry = for_module(data_dir, module, key)
        missing = sorted(int(g) for g, v in (entry or {}).get("groups", {}).items() if not v["ok"])
        if not missing:
            return
        found = from_reads(read_groups(kwp, tp, log, None, groups=missing, label=label))
        back = {g: v for g, v in found.items() if v["ok"]}
        if back:
            groups = dict(entry["groups"], **back)
            save(data_dir, module, key, groups)
            log(f"Channel check ({label}): groups {', '.join(sorted(back, key=int))} answer now; "
                "their channels are back")
    except Exception as exc:
        log(f"Channel recheck ({label}) failed: {type(exc).__name__}: {exc}")
