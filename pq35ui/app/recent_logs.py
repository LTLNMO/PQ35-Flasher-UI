"""Recent logs: every recording lands here first (crash-safe, automatic), and stays a while.

Only Export… puts a log in Datalogs (or anywhere you choose). An index (recent.json beside the
logs) remembers each log's module, car (VIN), length, and where it was exported to, so the
names can stay short: engine_2026-11-09_073012.csv.

Kept: the newest KEEP logs, plus anything younger than KEEP_DAYS. Older ones are deleted on
the next recording or app start (exported copies elsewhere are never touched)."""
import json
import time
from pathlib import Path

from .storage import log_stamp, recent_logs_dir

KEEP = 20
KEEP_DAYS = 14
INDEX = "recent.json"


def _index_path() -> Path:
    return recent_logs_dir() / INDEX


def _load() -> dict:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict):
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def is_recent(path) -> bool:
    try:
        return Path(path).resolve().parent == recent_logs_dir().resolve()
    except OSError:
        return False


def new_log(module: str, vin=None, label="", mode="continuous") -> Path:
    """A fresh path for a recording, registered in the index: <module>_<date>_<time>.csv."""
    folder = recent_logs_dir()
    now = time.time()
    base = f"{module or 'log'}_{log_stamp(now)}"
    path, n = folder / f"{base}.csv", 2
    while path.exists():
        path, n = folder / f"{base}_{n}.csv", n + 1
    data = _load()
    data[path.name] = {"module": module, "label": label, "vin": vin or "", "mode": mode,
                       "started": now, "rows": 0, "seconds": 0.0, "exported": []}
    _save(data)
    return path


def finish(path: Path, rows: int, seconds: float):
    """Recording stopped. No rows: the (empty) log is dropped."""
    data = _load()
    if rows <= 0:
        data.pop(path.name, None)
        _save(data)
        path.unlink(missing_ok=True)
        return
    entry = data.setdefault(path.name, {"started": time.time(), "exported": []})
    entry.update(rows=int(rows), seconds=float(seconds))
    _save(data)


def info(path) -> dict:
    """The index entry for a recent log ({} if it isn't one)."""
    path = Path(path)
    return _load().get(path.name, {}) if is_recent(path) else {}


def vin_for(path):
    return info(path).get("vin") or None


def mark_exported(path, out):
    if not is_recent(path):
        return
    data = _load()
    entry = data.get(Path(path).name)
    if entry is None:
        return
    out = str(out)
    entry["exported"] = [p for p in entry.get("exported", []) if p != out] + [out]
    _save(data)


def discard(path):
    path = Path(path)
    data = _load()
    data.pop(path.name, None)
    _save(data)
    path.unlink(missing_ok=True)


def add_existing(path: Path, like=None):
    """Register a log written straight into Recent (e.g. Analyze's 'save part as new log')."""
    data = _load()
    entry = dict(info(like)) if like else {}
    entry.update(started=time.time(), exported=[])
    entry.pop("rows", None)
    data[path.name] = entry
    _save(data)


def rename(path: Path, new: Path):
    data = _load()
    if path.name in data:
        data[new.name] = data.pop(path.name)
        _save(data)


def entries(limit=None):
    """[(path, entry)] newest first; logs missing from the index are listed too."""
    folder = recent_logs_dir()
    data = _load()
    out = []
    for p in folder.glob("*.csv"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        entry = data.get(p.name) or {"started": mtime, "exported": []}
        out.append((p, entry, entry.get("started") or mtime))
    out.sort(key=lambda t: t[2], reverse=True)
    out = [(p, e) for p, e, _ in out]
    return out[:limit] if limit else out


def prune(keep=KEEP, days=KEEP_DAYS, protect=()):
    """Delete logs that are both outside the newest `keep` and older than `days`."""
    cutoff = time.time() - days * 86400
    protect = {Path(p).name for p in protect if p}
    data = _load()
    changed = False
    for i, (p, entry) in enumerate(entries()):
        if i < keep or p.name in protect or (entry.get("started") or 0) >= cutoff:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        data.pop(p.name, None)
        changed = True
    # forget index entries whose file is gone
    folder = recent_logs_dir()
    for name in [n for n in data if not (folder / n).exists()]:
        data.pop(name)
        changed = True
    if changed:
        _save(data)


def describe(entry: dict) -> str:
    """'0:42 · 4,812 rows · exported' for menus and tooltips."""
    secs = entry.get("seconds") or 0
    parts = [f"{int(secs // 60)}:{int(secs % 60):02d}"] if secs else []
    if entry.get("rows"):
        parts.append(f"{entry['rows']:,} rows")
    parts.append("exported" if entry.get("exported") else "not exported")
    return " · ".join(parts)
