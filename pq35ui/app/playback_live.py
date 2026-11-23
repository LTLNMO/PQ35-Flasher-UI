"""Analyze (PlaybackPage), following a recording live (Logger → Watch in Analyze). A mixin: PlaybackPage combines these; methods use
the page's attributes as before (moved out of playback_page.py unchanged)."""
from pathlib import Path
import numpy as np
from . import log_export, mathchannels, units
from . import smart_presets
from .playback_common import (_parse_csv, _aligned)


class LiveFollowMixin:
    # --- following a recording live (Logger → Watch in Analyze) ---

    LIVE_MS = 1000  # how often a followed recording is read for new rows

    def follow(self, path):
        """Open the Logger's recording in progress and keep adding the rows it writes: the graph
        grows, and while the view is at the end it moves along with the newest data."""
        from PySide6.QtCore import QTimer
        path = Path(path)
        if self._live and self._live["path"] == path:
            return
        self._stop_follow()
        if not hasattr(self, "_live_timer"):
            self._live_timer = QTimer(self)
            self._live_timer.setInterval(self.LIVE_MS)
            self._live_timer.timeout.connect(self._live_tick)
        self._live = {"path": path, "started": False, "ended": False}
        if not self._live_open():
            self.banner.show_message("Live: waiting for the recording's first rows…", "info",
                                     actions={"Stop recording": self.state.stop_logging.emit,
                                              "Back to Logger": lambda: self.state.open_tab.emit("logger")})
        self._live_timer.start()

    def end_follow(self):
        """The Logger stopped: read the last rows, then the log is an ordinary one."""
        if not self._live:
            return
        self._live["ended"] = True
        self._live_tick()

    def _stop_follow(self):
        if getattr(self, "_live_timer", None):
            self._live_timer.stop()
        self._live = None

    def _live_open(self):
        """First read of a followed recording (False while it has no rows yet)."""
        live = self._live
        try:
            headers, times, cols = _parse_csv(live["path"])
            with open(live["path"], "rb") as f:  # where the next unread row starts
                f.readline()
                for _ in range(len(times)):
                    f.readline()
                live["pos"] = f.tell()
        except (OSError, ValueError):
            return False
        live.update(started=True, headers=headers, times=times, cols=dict(cols))
        self.load_csv(live["path"])
        if self.times is None:
            return False
        self.cursor_idx = len(self.times) - 1  # the cursor rides the newest row
        self.cursor_line.setValue(float(self.times[-1]))
        self._live_banner()
        return True

    def _live_banner(self):
        self.banner.show_message("● Live: following the recording as it's written. Scroll back to "
                                 "look around; go back to the end to follow again.", "info",
                                 actions={"Stop recording": self.state.stop_logging.emit,
                                          "Back to Logger": lambda: self.state.open_tab.emit("logger")})

    def _live_tick(self):
        live = self._live
        if not live:
            return
        if not live["started"]:
            if not self._live_open() and live["ended"]:
                self._stop_follow()
                self.banner.show_message("The recording ended before any rows were written.", "warning")
            return
        rows = self._live_read()
        if rows is not None:
            self._live_append(rows)
        if live["ended"]:
            self._live_align()
            self._stop_follow()
            self.banner.show_message("Recording finished: this is the whole log.", "info")

    def _live_align(self):
        """The recording just stopped and its log_info companion exists: put each channel at its
        real read times, as opening the log would, without moving the view."""
        live = self._live
        cols = _aligned(live["path"], live["headers"], live["times"], live["cols"])
        if all(cols[n] is live["cols"][n] for n in live["headers"]):
            return
        self.headers, self.times, self.columns = list(live["headers"]), live["times"], dict(cols)
        self._add_math_columns()
        self.headers, self.columns = self._to_display_units(self.headers, self.columns)
        for name in self.headers:
            col = self.columns[name]
            valid = col[~np.isnan(col)]
            self._ranges[name] = (float(valid.min()), float(valid.max())) if len(valid) else None
        if not self._refresh_curves_in_place():
            self._rebuild_curves()

    def _live_read(self):
        """The whole rows added since the last read, as an array (None: nothing new)."""
        live = self._live
        try:
            with open(live["path"], "rb") as f:
                f.seek(live["pos"])
                chunk = f.read()
        except OSError:
            return None
        end = chunk.rfind(b"\n")
        if end < 0:
            return None
        live["pos"] += end + 1
        width = len(live["headers"]) + 1
        rows = []
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            cells = line.split(",")
            if len(cells) != width:
                continue
            try:
                rows.append([float(c) for c in cells])
            except ValueError:
                continue
        return np.array(rows) if rows else None

    def _live_append(self, rows):
        live = self._live
        vb = self.plot.getPlotItem().vb
        a, b = vb.viewRange()[0]
        t0, old_end = float(self.times[0]), float(self.times[-1])
        slack = max(old_end - t0, 1.0) * 0.02
        at_end = b >= old_end - slack           # the view shows the newest data: keep following
        showing_all = a <= t0 + slack
        cursor_at_end = self.cursor_idx >= len(self.times) - 1
        live["times"] = np.concatenate([live["times"], rows[:, 0]])
        for i, name in enumerate(live["headers"], 1):
            live["cols"][name] = np.concatenate([live["cols"][name], rows[:, i]])
        # The same steps as load_csv after reading the file: math channels, display units
        self.headers, self.times, self.columns = list(live["headers"]), live["times"], dict(live["cols"])
        self._add_math_columns()
        self.headers, self.columns = self._to_display_units(self.headers, self.columns)
        for name in self.headers:
            col = self.columns[name]
            valid = col[~np.isnan(col)]
            self._ranges[name] = (float(valid.min()), float(valid.max())) if len(valid) else None
        new_end = float(self.times[-1])
        self.sel_band.setBounds((t0, new_end))
        if not self._refresh_curves_in_place():
            self._rebuild_curves()  # before moving the cursor: its readout looks up the new lines
        if cursor_at_end:
            self.cursor_idx = len(self.times) - 1
            self.cursor_line.setValue(new_end)
        self._update_stats()
        if at_end:
            if showing_all:
                self._show_all()
            else:  # zoomed in: slide along, same width
                self.plot.setXRange(new_end - (b - a), new_end, padding=0)

    def _smart_bar_toggled(self, on):
        self.state.settings.set(smart_presets.BAR_KEY, bool(on))
        self._sync_smart_bar()

    def _load_smart(self):
        """This log's smart presets, led by layouts for the engine's fault codes (last check)."""
        self.smart.profile.faults = smart_presets.faults_for(self.state.settings, "engine")
        self.smart.load(list(self.headers))
        self._sync_smart_bar()

    def _sync_smart_bar(self):
        """The chip row shows on Single / Split with a log open, unless switched off."""
        on = smart_presets.bar_on(self.state.settings)  # switched in Presets ▾ (or Settings)
        self.smart_bar.setVisible(on and self.times is not None and self._view() in ("single", "split"))

    def _apply_smart(self, layout):
        """A smart preset: its channels on the graph, one Split row per chart (Single for one)."""
        charts = [c["channels"] for c in layout["charts"] if c["channels"]]
        if not charts:
            return
        self.hidden.clear()
        self._mark_hidden()
        if len(charts) > 1:
            self.split.groups = [list(c) for c in charts]
            self.split._heights = {}
        self._apply_channels([n for c in charts for n in c])
        self.tabs.setCurrentWidget(self.split_page if len(charts) > 1 else self.time_page)

    def _add_math_columns(self):
        """Append a computed column per math channel whose inputs are in this log."""
        by_name = {}
        for header in self.headers:
            by_name.setdefault(header, header)
            by_name.setdefault(mathchannels.strip_unit(header), header)
        for definition, formula in mathchannels.compiled(self.state.settings):
            cols = [by_name.get(r) for r in formula.refs]
            if None in cols:
                continue
            unit = definition.get("unit", "")
            title = "\u0192 " + definition["name"] + (f" ({unit})" if unit else "")
            result = formula.evaluate([self.columns[c] for c in cols])
            result = np.broadcast_to(np.asarray(result, dtype=float), self.times.shape).copy()
            self.columns[title] = result
            self.headers.append(title)

    def _to_display_units(self, headers, columns):
        """Rename 'X (°C)' -> 'X (°F)' etc. and convert, when imperial is chosen."""
        new_headers, new_columns = [], {}
        for header in headers:
            name, unit = log_export.split_header(header)
            shown, conv = units.conversion(unit, self.state.settings)
            title = f"{name} ({shown})" if conv != units.IDENTITY else header
            new_headers.append(title)
            new_columns[title] = units.to_display(columns[header], conv) if conv != units.IDENTITY else columns[header]
        return new_headers, new_columns

    def _edit_math(self):
        names = [mathchannels.strip_unit(h) for h in self.headers if not h.startswith("\u0192 ")]
        dialog = mathchannels.MathChannelsDialog(self.state.settings, names, self)
        if dialog.exec() and self.csv_path is not None:
            checked = set(self._checked_channels())
            self.load_csv(self.csv_path)
            self._restore_checked(lambda name: name in checked)

    def _export(self):
        if self.csv_path is None:
            return
        log_export.export_dialog(self, self.state, self.csv_path, self.banner,
                                 stem=(self.source_path or self.csv_path).stem)
