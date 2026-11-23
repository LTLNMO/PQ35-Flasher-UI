"""Where things live.

Your files (module backups, copies of flashed images, patched images, logs)
go in "PQ35 Flasher" in your Documents folder on every OS, so they're easy to
find. Settings and lock files stay in the per-user app-data folder.
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("PQ35_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PQ35-Flasher"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "PQ35-Flasher"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pq35-flasher"
    if os.environ.get("PQ35_DEMO"):
        path = path / "demo"  # keep the pretend module's backups out of the real library
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_dir() -> Path:
    override = os.environ.get("PQ35_USER_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("PQ35_DATA_DIR"):
        base = Path(os.environ["PQ35_DATA_DIR"]) / "user"  # tests and portable setups
    else:
        documents = ""
        try:
            from PySide6.QtCore import QStandardPaths
            documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        except Exception:
            pass
        base = Path(documents or Path.home() / "Documents") / "PQ35 Flasher"
    if os.environ.get("PQ35_DEMO"):
        base = base / "Demo"
    for name in ("Modules", "Images", "Patched", "Logs", "Datalogs"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


def modules_dir() -> Path:
    return user_dir() / "Modules"


def images_dir() -> Path:
    return user_dir() / "Images"


def patched_dir() -> Path:
    return user_dir() / "Patched"


def logs_dir() -> Path:
    return user_dir() / "Logs"


_ACTIVE_SETTINGS = None  # the app's Settings, so folders the user picked are honoured


def default_datalogs_dir() -> Path:
    return user_dir() / "Datalogs"


def datalogs_dir() -> Path:
    """Where datalogs are saved: Settings → Datalogs, or the default folder."""
    custom = _ACTIVE_SETTINGS.get("datalogs_dir") if _ACTIVE_SETTINGS is not None else None
    if custom:
        path = Path(custom).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass  # e.g. an unplugged drive: fall back rather than lose the log
    return default_datalogs_dir()


def check_writable() -> list:
    """Folders the app must be able to write, each with why it can't. Empty if fine."""
    problems = []
    for describe, folder in (("settings", data_dir), ("your files", user_dir)):
        try:
            path = folder()
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{describe}: {getattr(exc, 'filename', None) or ''} "
                            f"({exc.strerror or exc})".replace(" ()", ""))
    return problems


def migrate_old_library() -> list:
    """Move module records from the old app-data location to Documents.
    Returns the ones that couldn't be moved, with why."""
    failed = []
    old = data_dir() / "library"
    if not old.is_dir():
        return failed
    for child in old.iterdir():
        target = modules_dir() / child.name
        if not target.exists():
            try:
                shutil.move(str(child), str(target))
            except OSError as exc:
                failed.append(f"{child} ({exc.strerror or exc})")
    try:
        old.rmdir()
    except OSError:
        pass  # still holds a record that already exists in Documents, or failed above
    return failed


def prune_logs(keep: int = 20):
    logs = sorted(logs_dir().glob("session_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def log_stamp(when=None) -> str:
    """Datalog names: 2026-11-09_073012 (sorts by name, readable, seconds so names never clash)."""
    return time.strftime("%Y-%m-%d_%H%M%S", time.localtime(when) if when else time.localtime())


def recent_logs_dir() -> Path:
    """The Recent logs index (recent.json, out of Documents). Before Nov 2026 recordings lived
    here too; recent_logs.migrate moves them into Datalogs."""
    path = data_dir() / "Recent logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def imported_logs_dir() -> Path:
    """Analyze's converted copies of logs from other tools (a cache; safe to delete)."""
    path = data_dir() / "Imported logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Channel ids that were dropped or merged -> the id that replaces them. Saved channel
# selections, presets and layouts are moved over once, when the settings load.
RENAMED_IDS = {
    # Same field (group 3 field 3) as engine.throttle_position: one channel now (2026-11-22)
    "engine.throttle_angle": "engine.throttle_position",
}


def _migrate_ids(value):
    """Replace RENAMED_IDS in place anywhere in the settings (list items, dict keys and values),
    without leaving a duplicate in a list. True if anything changed."""
    changed = False
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item in RENAMED_IDS:
                item, changed = RENAMED_IDS[item], True
            elif _migrate_ids(item):
                changed = True
            if isinstance(item, str) and item in out:
                changed = True
                continue
            out.append(item)
        value[:] = out
    elif isinstance(value, dict):
        for key in list(value):
            if _migrate_ids(value[key]):
                changed = True
            if isinstance(value[key], str) and value[key] in RENAMED_IDS:
                value[key], changed = RENAMED_IDS[value[key]], True
            if key in RENAMED_IDS:
                new = RENAMED_IDS[key]
                if new not in value:
                    value[new] = value[key]
                del value[key]
                changed = True
    return changed


class Settings:
    def __init__(self):
        self.path = data_dir() / "settings.json"
        try:
            self.values = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.values = {}
        if _migrate_ids(self.values):
            self._save()
        global _ACTIVE_SETTINGS
        _ACTIVE_SETTINGS = self

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self._save()

    def remove(self, *keys):
        """Forget these keys, so get() gives its default again (set(key, None) stores null)."""
        for key in keys:
            self.values.pop(key, None)
        self._save()

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
        except OSError:
            pass


def ecu_key(family: str, identity: dict) -> str:
    """Folder name for one physical module. Not the software version: that
    changes every time you flash, and the backups must stay with the module."""
    part = identity.get("software_part_number") or identity.get("part_number") or "unknown"
    raw = f"{family}_{part}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")


class EcuRecord:
    """Backups, flash history and the best-known current image for one ECU.

    `state.bin` is what we believe the ECU holds right now: a fresh readout,
    or the previous state with the last flashed range written over it. It is
    what new images are diffed against to pick the smallest flash range.
    """

    def __init__(self, key: str):
        self.key = key
        self.root = modules_dir() / key
        self.root.mkdir(parents=True, exist_ok=True)
        self.history_path = self.root / "history.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.bin"

    def state(self):
        return self.state_path if self.state_path.is_file() else None

    def new_backup_dir(self) -> Path:
        path = self.root / "Backups" / timestamp()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def history(self) -> list:
        try:
            return json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def add(self, entry: dict):
        entries = self.history()
        entries.append(dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S")))
        self.history_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def backups(self) -> list:
        return [e for e in self.history() if e.get("kind") == "backup" and e.get("image")]

    def has_backup(self) -> bool:
        return any(Path(e["image"]).is_file() for e in self.backups())

    def keep_flashed(self, image: str) -> str:
        """Keep a copy of every image flashed to this module."""
        target = self.root / "Flashed" / f"{timestamp()}_{Path(image).name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, target)
        return str(target)

    def adopt_readout(self, image: str):
        shutil.copyfile(image, self.state_path)

    def forget_state(self):
        if self.state_path.exists():
            self.state_path.unlink()
