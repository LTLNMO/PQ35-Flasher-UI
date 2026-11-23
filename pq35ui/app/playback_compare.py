"""Analyze (PlaybackPage), comparing with a second log. A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
import sys
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMenu, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)
from . import log_export, log_import, recent_logs
from . import theme
from .storage import datalogs_dir
from .widgets import muted
from . import menus
from .playback_common import (_parse_csv)


class CompareMixin:
    # --- compare log ---

    def _update_title(self):
        """Tab-row heading: just the log's file name (details on hover)."""
        if not hasattr(self, "file_label"):  # called once while the page is still being built
            return
        path = getattr(self, "csv_path", None)
        if path is None or self.times is None:
            self.file_label.setText("")
            self.file_label.setToolTip("")
            return
        shown = self.source_path or path
        self.file_label.setText(shown.name)
        if theme.is_mac():  # the file button, with long names shortened in the middle
            fm = self.open_button.fontMetrics()
            # Mac: the mockup shows the whole name (engine_2026-11-14_121055 ≈ 190 px); only very long ones shorten
            self.open_button.setText(fm.elidedText(shown.stem, Qt.TextElideMode.ElideMiddle,
                                                   260 if theme.is_mac() else 170))
            self.open_button.setToolTip(f"{shown.name}\nClick: open another log")
        self.file_label.setToolTip("")  # details on click, so nothing pops over the graph by accident
        self._fit_title()

    def _show_file_details(self, anchor=None):
        """Click on the log's name: a small panel with where it is, how long, which car."""
        from PySide6.QtWidgets import QFormLayout
        from .vehicle import decode
        from .widgets import PopupCard, reveal_file
        path = getattr(self, "csv_path", None)
        if path is None or self.times is None:
            return
        secs = float(self.times[-1] - self.times[0]) if len(self.times) else 0.0
        vin = log_export.log_vin(self.source_path or path) or log_export.log_vin(path)
        path = self.source_path or path
        car = decode(vin) if vin else None
        card = PopupCard(self, min_width=320)
        box = QVBoxLayout(card.body)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        name = QLabel(path.name)
        name.setStyleSheet("font-weight: 700;")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(name)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        where = str(path.parent)
        rows = [("Folder", where), ("Length", f"{int(secs // 60)}:{int(secs % 60):02d}"
                                              f"  ({len(self.times):,} rows)")]
        if self.source_format:
            rows.append(("Format", f"{self.source_format}, converted to Standard"))
        elif recent_logs.is_recent(path):
            entry = recent_logs.info(path)
            if entry.get("exported"):
                rows.append(("Exported", ", ".join(Path(x).name for x in entry["exported"])))
        if car:
            rows += [("Car", car["label"]), ("VIN", vin)]
        if self.compare is not None:
            rows.append(("Compared with", self.compare["path"].name))
        for label, value in rows:
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(muted(label), v)
        box.addLayout(form)
        reveal = QPushButton("Show in Finder" if sys.platform == "darwin" else "Show in folder")
        reveal.clicked.connect(lambda: (card.close(), reveal_file(path)))
        row = QHBoxLayout()
        row.addWidget(reveal)
        row.addStretch(1)
        box.addLayout(row)
        card.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        card.open_at(anchor or self.file_label, align="right")

    def _fit_title(self):
        """The name sits in the view-button row with stretch, so the layout elides it."""
        self.file_label.updateGeometry()


    def _set_compare_enabled(self):
        has = self.compare is not None
        for w in (self.offset, self.align_button, self.clear_compare_button):
            w.setEnabled(has)
        self._update_title()
        self.compare_row.setVisible(has)

    def _compare_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        loaded = self.times is not None
        menus.menu_action(menu, "Overlay a log on the graph…", "stacked_line_chart", self._open_compare,
                      enabled=loaded, tip="Show a second log as dashed lines over this one" if loaded
                      else "Open a log first")
        menus.menu_action(menu, "Compare logs with AI…", "auto_awesome", self._compare_with_ai,
                      tip="Pick any number of logs; your AI compares them side by side, by rpm")
        if self.compare is not None:
            menus.menu_header(menu, "Overlay")
            menus.menu_action(menu, "Stop overlaying", "close", self._clear_compare)
        return menu

    def _load_for_ai(self, path):
        headers, times, columns = _parse_csv(log_import.native_path(path)[0])
        headers, columns = self._to_display_units(headers, columns)
        return headers, times, columns

    def _compare_with_ai(self):
        from . import log_compare_ai
        paths = [p for p in (self.csv_path if self.times is not None else None,
                             self.compare["path"] if self.compare else None) if p]
        log_compare_ai.open_compare(self, self.state.settings, self._load_for_ai, paths)

    def _open_compare(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with datalog", str(datalogs_dir()), log_import.OPEN_FILTER)
        if path:
            self.load_compare(Path(path))

    def load_compare(self, path: Path):
        try:
            headers, times, columns = _parse_csv(log_import.native_path(path)[0])
        except Exception as e:
            self.banner.show_message(f"Couldn't open the log to compare: {e}", "error")
            return
        headers, columns = self._to_display_units(headers, columns)
        self.compare = {"path": path, "headers": headers, "times": times, "columns": columns}
        shared = [h for h in self.headers if h in columns]
        if not shared:
            self.banner.show_message("The logs have no channels in common, so there's nothing to overlay.",
                                     "warning")
        else:
            self.banner.show_message(f"Comparing with {path.name}: dashed lines. {len(shared)} channel(s) "
                                     "in common. Use Shift or Auto-align to line the runs up.", "info")
        self.offset.blockSignals(True)
        self.offset.setValue(0)
        self.offset.blockSignals(False)
        self._set_compare_enabled()
        self._rebuild_curves()

    def _clear_compare(self):
        self.compare = None
        self._set_compare_enabled()
        self.banner.hide()
        self._rebuild_curves()

    def _compare_column(self, name):
        if self.compare is None:
            return None
        return self.compare["columns"].get(name)

    def _auto_align(self):
        checked = [n for n in self._checked_channels() if self._compare_column(n) is not None]
        if not checked:
            self.banner.show_message("Tick a channel both logs have (throttle or rpm works best).", "warning")
            return
        name = checked[0]

        def first_cross(times, col):
            valid = col[~np.isnan(col)]
            if not len(valid):
                return None
            level = valid.min() + 0.5 * (valid.max() - valid.min())
            hits = np.nonzero(col > level)[0]
            return float(times[hits[0]]) if len(hits) else None

        a = first_cross(self.times, self.columns[name])
        b = first_cross(self.compare["times"], self._compare_column(name))
        if a is None or b is None:
            self.banner.show_message(f"Couldn't find a clear rise in {name} in both logs.", "warning")
            return
        self.offset.setValue(a - b)
        self.banner.show_message(f"Aligned on {name}: compared log shifted {a - b:+.2f} s.", "ok")
