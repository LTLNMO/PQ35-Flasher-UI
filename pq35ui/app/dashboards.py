"""Saved Live View dashboards: layout, panel types, channels and graph picks,
stored per module family and exportable as JSON to share."""
import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from .storage import user_dir
from .popups import InputDialog, MessageBox
from .toolbar import ToolIcon
from . import menus

FORMAT_TAG = "pq35-dashboard"


def dashboards_dir() -> Path:
    return user_dir() / "Dashboards"


class DashboardButton(ToolIcon):
    """Saved dashboards, shown like the Presets button beside it (icon ▾); its menu: load /
    save as / delete / export / import."""

    GLYPH = "dashboard_customize"  # Material Symbols: a grid of panels with a +

    def __init__(self, page, parent=None):
        self.page = page
        self.current = None  # name of the dashboard last loaded or saved (ticked in the menu)
        super().__init__(self.GLYPH, "Saved dashboards: load, save, delete, export or import Live View layouts",
                         dropdown=True, menu=self._fill, parent=parent)

    # --- storage ---

    def _key(self):
        family = self.page.state.family
        return f"dashboards:{family['name']}" if family else None

    def saved(self):
        key = self._key()
        return dict(self.page.state.settings.get(key) or {}) if key else {}

    def _store(self, dashboards):
        key = self._key()
        if key:
            self.page.state.settings.set(key, dashboards)

    # --- menu ---

    def _fill(self, menu):
        """Like Analyze's menus: saved dashboards (✓ on the one loaded), then actions with icons."""
        saved = self.saved()
        if not self._key():
            menu.addAction("Choose a module first").setEnabled(False)
            return
        for name in sorted(saved, key=str.lower):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.current)
            act.triggered.connect(lambda _=False, n=name: self.load(n))
        if not saved:
            menu.addAction("No saved dashboards").setEnabled(False)
        menus.menu_header(menu, "Manage")
        menus.menu_action(menu, "Save current as…", "bookmark_add", self.save_as)
        menus.menu_action(menu, "Delete…", "delete", self.delete, enabled=bool(saved))
        menus.menu_header(menu, "Share")
        menus.menu_action(menu, "Export…", "upload", self.export, enabled=bool(saved),
                      tip="Save a dashboard as a file to share")
        menus.menu_action(menu, "Import…", "download", self.import_, tip="Add a dashboard from a file")

    def load(self, name):
        state = self.saved().get(name)
        if state:
            self.page.apply_dashboard(state)
            self.current = name

    def save_as(self):
        name, ok = InputDialog.getText(self, "Save dashboard", "Dashboard name (e.g. Track day):")
        name = name.strip()
        if not ok or not name:
            return
        saved = self.saved()
        if name in saved and MessageBox.question(
                self, "Save dashboard", f"Replace “{name}”?") != MessageBox.StandardButton.Yes:
            return
        saved[name] = self.page.dashboard_state()
        self._store(saved)
        self.current = name

    def delete(self):
        saved = self.saved()
        name, ok = InputDialog.getItem(self, "Delete dashboard", "Dashboard:",
                                        sorted(saved, key=str.lower), 0, False)
        if ok and name:
            saved.pop(name, None)
            self._store(saved)

    def export(self):
        saved = self.saved()
        name, ok = InputDialog.getItem(self, "Export dashboard", "Dashboard:",
                                        sorted(saved, key=str.lower), 0, False)
        if not ok or not name:
            return
        dashboards_dir().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Export dashboard",
                                              str(dashboards_dir() / f"{name}.json"), "Dashboard (*.json)")
        if not path:
            return
        payload = {"format": FORMAT_TAG, "version": 1, "name": name,
                   "module": self.page.state.family["name"], "dashboard": saved[name]}
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            MessageBox.warning(self, "Export dashboard", f"Couldn't write the file: {e}")

    def import_(self):
        dashboards_dir().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Import dashboard", str(dashboards_dir()),
                                              "Dashboard (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("format") != FORMAT_TAG or not isinstance(payload.get("dashboard"), dict):
                raise ValueError("not a PQ35 Flasher dashboard file")
        except (OSError, ValueError) as e:
            MessageBox.warning(self, "Import dashboard", f"Couldn't import: {e}")
            return
        module = payload.get("module")
        current = self.page.state.family["name"]
        if module and module != current and MessageBox.question(
                self, "Import dashboard",
                f"This dashboard was made for {module}, not {current}. Channels that don't exist "
                "on this module are skipped. Import anyway?") != MessageBox.StandardButton.Yes:
            return
        name = payload.get("name") or Path(path).stem
        saved = self.saved()
        saved[name] = payload["dashboard"]
        self._store(saved)
        self.page.apply_dashboard(payload["dashboard"])
