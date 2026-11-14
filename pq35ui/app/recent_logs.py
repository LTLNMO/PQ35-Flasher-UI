"""Recordings: every log the app records is saved straight into Datalogs as it records
(crash-safe, automatic); dyno sessions go in Datalogs/Dyno. Nothing is ever deleted on its own:
only Discard… removes a recording. Export… makes a copy in another format or place.

Names stay short: engine_2026-11-09_073012.csv (dyno sessions the same, in Datalogs/Dyno). An index (recent.json in the app's settings folder, "Recent logs") remembers each recording
by its full path: module, car (VIN), length, mode, the dyno's car and load, and where it was exported.
"Recent logs" in the app is that list, newest first.

on_finished(fn): fn(path, entry) is called for every recording that kept rows (Projects files
them into the car's active project)."""
import json
import shutil
import time
from pathlib import Path

from .storage import datalogs_dir, log_stamp, recent_logs_dir

KEEP = 20          # how many the Recent logs menus list (all are kept on disk)
INDEX = "recent.json"
VERSION = 2        # 2: keyed by full path, files in Datalogs (1: names, in the hidden Recent logs folder)

_finished_listeners = []   # fn(path, entry) after a recording with rows
_keepers = []              # kept for callers (Projects); nothing is deleted on its own any more


def on_finished(fn):
    _finished_listeners.append(fn)


def keep_also(fn):
    _keepers.append(fn)


def _index_path() -> Path:
    return recent_logs_dir() / INDEX


def _key(path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path))


def _read() -> dict:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _load() -> dict:
    data = _read()
    if data.get("_version") != VERSION:
        data = migrate(data)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _save(data: dict):
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(dict(data, _version=VERSION), indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def folder_for(mode="continuous") -> Path:
    folder = datalogs_dir() / "Dyno" if mode == "dyno" else datalogs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _free(path: Path) -> Path:
    n = 2
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return out


def migrate(data: dict) -> dict:
    """Once: move the recordings out of the hidden Recent logs folder into Datalogs (dyno sessions
    into Datalogs/Dyno, names unchanged), key the index by full path, and point projects at the
    moved files."""
    old_dir = recent_logs_dir()
    out = {}
    for name, entry in data.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        p = Path(name)
        if p.is_absolute():  # already a full-path key
            out[_key(p)] = entry
            continue
        src = old_dir / name
        if not src.exists():
            continue
        dest = _free(folder_for(entry.get("mode")) / src.name)
        try:
            shutil.move(str(src), str(dest))
        except OSError:
            out[_key(src)] = entry  # couldn't move: keep tracking it where it is
            continue
        out[_key(dest)] = entry
        from .analyze_tools import relink_projects
        relink_projects(src, dest)
    # recordings that were never in the index
    for src in old_dir.glob("*.csv"):
        if _key(src) in out:
            continue
        dest = _free(folder_for() / src.name)
        try:
            shutil.move(str(src), str(dest))
            out[_key(dest)] = {"started": dest.stat().st_mtime, "exported": []}
        except OSError:
            pass
    _save(out)
    return out


def is_recent(path) -> bool:
    """Whether this app recorded it (it's in the index)."""
    return _key(path) in _load()


def new_log(module: str, vin=None, label="", mode="continuous") -> Path:
    """A fresh path for a recording, registered in the index (the VIN goes there, not in the
    name): Datalogs/<module>_<date>_<time>.csv, in Datalogs/Dyno for a dyno session."""
    folder = folder_for(mode)
    now = time.time()
    base = f"{module or 'log'}_{log_stamp(now)}"
    path = _free(folder / f"{base}.csv")
    data = _load()
    data[_key(path)] = {"module": module, "label": label, "vin": vin or "", "mode": mode,
                        "started": now, "rows": 0, "seconds": 0.0, "exported": []}
    _save(data)
    return path


def finish(path: Path, rows: int, seconds: float):
    """Recording stopped. No rows: the (empty) log is dropped."""
    data = _load()
    key = _key(path)
    if rows <= 0:
        data.pop(key, None)
        _save(data)
        Path(path).unlink(missing_ok=True)
        return
    entry = data.setdefault(key, {"started": time.time(), "exported": []})
    entry.update(rows=int(rows), seconds=float(seconds))
    _save(data)
    for fn in list(_finished_listeners):
        try:
            fn(path, dict(entry))
        except Exception:  # a listener's problem never loses the recording
            import logging
            logging.getLogger("recent_logs").exception("on_finished listener failed")


def info(path) -> dict:
    """The index entry for a recording ({} if the app didn't record it)."""
    return _load().get(_key(path), {})


def update(path, **fields):
    """Store extra facts about a recording in its index entry (e.g. the dyno's car and load).
    Returns False for a log the app didn't record."""
    data = _load()
    key = _key(path)
    if key not in data:
        return False
    data[key].update(fields)
    _save(data)
    return True


def vin_for(path):
    return info(path).get("vin") or None


def mark_exported(path, out):
    data = _load()
    entry = data.get(_key(path))
    if entry is None:
        return
    out = str(out)
    entry["exported"] = [p for p in entry.get("exported", []) if p != out] + [out]
    _save(data)


def discard(path):
    path = Path(path)
    data = _load()
    data.pop(_key(path), None)
    _save(data)
    path.unlink(missing_ok=True)


def add_existing(path: Path, like=None):
    """Register a log the app wrote (e.g. Analyze's 'save part as new log')."""
    data = _load()
    entry = dict(info(like)) if like else {}
    entry.update(started=time.time(), exported=[])
    entry.pop("rows", None)
    data[_key(path)] = entry
    _save(data)


def rename(path: Path, new: Path):
    data = _load()
    if _key(path) in data:
        data[_key(new)] = data.pop(_key(path))
        _save(data)


def entries(limit=None):
    """[(path, entry)] of the recordings still on disk, newest first."""
    out = []
    for key, entry in _load().items():
        p = Path(key)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        out.append((p, entry, entry.get("started") or mtime))
    out.sort(key=lambda t: t[2], reverse=True)
    out = [(p, e) for p, e, _ in out]
    return out[:limit] if limit else out


def prune(*_args, **_kwargs):
    """Forget recordings whose file is gone (deleted or moved in Finder). Never deletes a file."""
    data = _load()
    gone = [k for k in data if not Path(k).exists()]
    for k in gone:
        data.pop(k)
    if gone:
        _save(data)


def describe(entry: dict) -> str:
    """'0:42 · 4,812 rows' (+ ' · exported') for menus and tooltips."""
    secs = entry.get("seconds") or 0
    parts = [f"{int(secs // 60)}:{int(secs % 60):02d}"] if secs else []
    if entry.get("rows"):
        parts.append(f"{entry['rows']:,} rows")
    if entry.get("exported"):
        parts.append("exported")
    return " · ".join(parts)
