"""Data logger: pick named values, poll their measuring groups, show them live
and write a Datazap-friendly CSV.

Layout: log controls, then the channels list (Analyze's channel list, full width: colour, name,
Now / Min / Max / Avg, gauge bar; amber "no data" when a channel stops updating)."""
import collections
import csv
from html import escape
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox,
    QComboBox, QHBoxLayout, QHeaderView, QMenu, QPushButton,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import alarms, channel_info, log_export, log_info, recent_logs
from . import units as unitconv
from .storage import datalogs_dir
from .presets import PresetButton
from .channel_list import (ALARM_ROLE, CATEGORY_ROLE, FIXED_ROLE, FRAC_ROLE, RAW_GROUP_ROLE, ROLE, STALE_ROLE, VALUE_ROLE,
                           ChannelDropdown,
                           ask_raw_group, groups_summary)
from .channel_sidebar import ChannelSidebar
from . import analyze_tools as T
from .widgets import Banner, muted, scrollable
from .popups import OverlayDialog
from .gauges import ScaleTracker
from .toolbar import ToolIcon

# Per-group read interval: 0 is as fast as the module answers.
RATES = (("As fast as possible", 0), ("10 Hz", 100), ("5 Hz", 200), ("2 Hz", 500), ("1 Hz", 1000))
GROUP_RATES = (("Same as Rate", None),) + RATES + (("Every 5 s", 5000),)
# Channels sidebar value columns (Analyze's list): (key, header, tooltip). Column 0 is the channel.
SIDEBAR_COLUMNS = (
    ("now", "Now", "Latest reading"),
    ("group", "Group", "Where the value is read from: measuring group.field"),
    ("min", "Min", "Lowest value since Start"),
    ("max", "Max", "Highest value since Start"),
    ("avg", "Avg", "Average value since Start"),
)


class GroupRatesDialog(OverlayDialog):
    """Per measuring-group read rate. Fast groups (rpm, boost) can go flat out
    while slow ones (temperatures) are read once a second, so the fast ones
    get more of the bus."""

    CARD_ICON = "speed"  # header icon when shown as a card

    def __init__(self, groups, names_by_group, rates, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Group rates")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(muted("Each group is one request to the module. Slow down groups that "
                               "change slowly so the ones you care about are read more often."))
        self.table = QTableWidget(len(groups), 3)
        self.table.setHorizontalHeaderLabels(["Group", "Values", "Rate"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.combos = {}
        for row, group in enumerate(groups):
            self.table.setItem(row, 0, QTableWidgetItem(str(group)))
            self.table.setItem(row, 1, QTableWidgetItem(", ".join(names_by_group.get(group, [])) or "raw"))
            combo = QComboBox()
            for label, ms in GROUP_RATES:
                combo.addItem(label, ms)
            index = combo.findData(rates.get(str(group)))
            combo.setCurrentIndex(index if index >= 0 else 0)
            self.table.setCellWidget(row, 2, combo)
            self.combos[group] = combo
        self.table.resizeColumnToContents(2)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def rates(self):
        return {str(g): c.currentData() for g, c in self.combos.items() if c.currentData() is not None}


def csv_number(field):
    """Datazap wants every cell numeric. Bit fields ("0101") become integers."""
    value = field.get("value")
    if field.get("numeric") and isinstance(value, (int, float)):
        return value
    text = str(field.get("text") or value or "").split(" ")[0]
    if text and set(text) <= {"0", "1"}:
        return int(text, 2)
    try:
        return float(text)
    except ValueError:
        return None


from .units import fmt as _fmt  # one number style on every tab


class DatazapWriter:
    """CSV with a "Time" column in seconds and one "Name (unit)" column per
    value. A row is made per group read; values forward-fill between reads.
    Rows wait until every group has been read and every column has a value,
    so the columns are final and no cell is empty, but never more than WAIT_S:
    a group the module doesn't answer must not lose the whole recording. Its
    columns stay blank (filled in if it answers later) and are listed in .missing.

    mode:
      "continuous" - every row goes to the file.
      "loop"       - only the last keep_s seconds are kept, written at close.
      "trigger"    - rows are written while trigger(latest) is true, plus
                     pre_s seconds before it and post_s seconds after it ends.
                     Re-arms after each event, so several pulls land in one file."""

    WAIT_S = 5.0  # seconds to wait for every value before writing rows anyway

    def __init__(self, path, columns, groups, mode="continuous", keep_s=300.0,
                 trigger=None, pre_s=5.0, post_s=3.0, info_columns=None):
        self.path = path
        # log_info companion: {key: {group, field, formula, unit, kind, id}} (filled as reads
        # arrive), and per written row its group and raw bytes. None: no companion file.
        self.info_columns = info_columns
        self.row_groups, self.row_raw = [], []
        self.wait_since = None
        self.missing = []  # column titles with no value when rows started
        self.columns = columns  # [(key, title)]; raw groups add theirs on first read
        self.groups = set(groups)
        self.seen = set()
        self.latest = {}
        self.units = {}
        self.file = None
        self.writer = None
        self.ready = False
        self.rows = 0
        self.first_t = self.last_t = None
        self.mode = mode
        self.keep_s = keep_s
        self.trigger = trigger
        self.pre_s = pre_s
        self.post_s = post_s
        self.buffer = collections.deque()  # (seconds, row) for loop / pre-trigger
        self.triggered = False
        self.last_true = None
        self.events = 0

    def update(self, group: int, values: dict, units: dict, seconds: float, raw=None):
        """raw: the read's bytes as hex (log_info.raw_hex), kept with the row it makes."""
        self.seen.add(group)
        self.latest.update({k: v for k, v in values.items() if v is not None})
        self.units.update({k: u for k, u in units.items() if u})
        if not self.ready:
            if self.wait_since is None:
                self.wait_since = seconds
            complete = self.seen >= self.groups and all(key in self.latest for key, _ in self.columns)
            if not complete and seconds - self.wait_since < self.WAIT_S:
                return
            self.ready = True
            self.missing = [title for key, title in self.columns if key not in self.latest]
        row = [f"{seconds:.3f}"] + [self.latest.get(key, "") for key, _ in self.columns]
        meta = (group, raw)
        if self.mode == "loop":
            self._buffer(seconds, row, self.keep_s, meta)
        elif self.mode == "trigger":
            self._triggered_row(seconds, row, meta)
        else:
            self._write(row, meta)

    @property
    def status(self):
        if self.mode == "loop":
            span = self.buffer[-1][0] - self.buffer[0][0] if len(self.buffer) > 1 else 0
            return f"rolling buffer {span:.0f} s of {self.keep_s:.0f} s"
        if self.mode == "trigger":
            state = "TRIGGERED" if self.triggered else "armed"
            return f"{state} · {self.events} event(s)"
        return ""

    def _buffer(self, seconds, row, keep, meta=None):
        self.buffer.append((seconds, row, meta))
        while self.buffer and seconds - self.buffer[0][0] > keep:
            self.buffer.popleft()

    def _triggered_row(self, seconds, row, meta=None):
        try:
            active = bool(self.trigger(self.latest))
        except Exception:
            active = False
        if active:
            self.last_true = seconds
            if not self.triggered:
                self.triggered = True
                self.events += 1
                while self.buffer:
                    self._write(*self.buffer.popleft()[1:])
            self._write(row, meta)
        elif self.triggered and seconds - self.last_true <= self.post_s:
            self._write(row, meta)
        else:
            self.triggered = False
            self._buffer(seconds, row, self.pre_s, meta)

    def _write(self, row, meta=None):
        if self.writer is None:
            # Line-buffered: every row reaches the file whole, at once (Analyze can follow it live)
            self.file = open(self.path, "w", newline="", encoding="utf-8", buffering=1)
            self.writer = csv.writer(self.file)
            self.writer.writerow(["Time"] + [self._title(key, title) for key, title in self.columns])
        self.writer.writerow(row)
        self.rows += 1
        group, raw = meta or (None, None)
        self.row_groups.append(group)
        self.row_raw.append(raw)
        self.last_t = float(row[0])
        if self.first_t is None:
            self.first_t = self.last_t
        if self.rows % 50 == 0:
            self.file.flush()

    def latest_titles(self):
        """Titles of the columns that have had a value by now."""
        return {title for key, title in self.columns if key in self.latest}

    def _title(self, key, title):
        unit = self.units.get(key, "")
        return f"{title} ({unit})" if unit else title

    def close(self):
        if self.mode == "loop":
            while self.buffer:
                self._write(*self.buffer.popleft()[1:])
        if self.file:
            self.file.close()
        self._save_info()

    def _save_info(self):
        """The log_info companion, when every written row knows its group. Never raises: the
        recording itself is already safe."""
        if self.info_columns is None or not self.rows or None in self.row_groups:
            return
        try:
            from . import log_info
            columns = {self._title(key, title): dict(self.info_columns.get(key) or {})
                       for key, title in self.columns}
            log_info.save(self.path, columns, self.row_groups, self.row_raw)
        except Exception:
            import logging
            logging.getLogger("Logger").exception("Couldn't save the log's companion file")


def _yes(answer):
    from PySide6.QtWidgets import QMessageBox
    return answer == QMessageBox.StandardButton.Yes


class _ModeButton(QPushButton):
    """A recording-mode button; with a dropdown, a small ▾ after the name (its settings)."""

    def __init__(self, label, dropdown=False):
        super().__init__(label)
        from .widgets import style_segment
        style_segment(self)
        self.dropdown = dropdown

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.dropdown:
            return
        from PySide6.QtGui import QPainter
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        on = self.property("active") == "true"
        p = QPainter(self)
        icon = _material_icon("arrow_drop_down", 18, c["on_accent"] if on else c["tab_text"])
        p.drawPixmap(self.width() - 22, (self.height() - 18) // 2, icon.pixmap(18, 18))
        p.end()


class RecordingModePicker(QWidget):
    """Continuous | Triggered | Rolling (key "loop"), styled like Analyze's view buttons (Single, Split…): icon +
    name, the current one filled with the accent colour. Triggered has a ▾: picking it opens its
    settings right under it. Keeps the bit of QComboBox's API the logger uses (currentData,
    findData, setCurrentIndex, currentIndexChanged)."""
    currentIndexChanged = Signal(int)
    MODES = (
        ("continuous", "Continuous", "all_inclusive", "Record everything from Start to Stop"),
        ("trigger", "Triggered", "bolt_boost", "Record only around moments when a value crosses a limit "
                                        "(e.g. throttle > 90 %), with a few seconds before and after. "
                                        "Click to set it up."),
        ("loop", "Rolling", "autorenew", "Keep only the last few minutes, saved when you press Stop "
                                         "(how long: ⚙ → Keep last)"),
    )

    def __init__(self, open_trigger_settings, parent=None):
        super().__init__(parent)
        self._open_trigger = open_trigger_settings
        from . import theme
        from .widgets import segment_track
        self.buttons = []
        self._index = 0
        for i, (_key, label, _glyph, tip) in enumerate(self.MODES):
            b = _ModeButton(label, dropdown=_key == "trigger")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, i=i: self._clicked(i))
            self.buttons.append(b)
        segment_track(self, QHBoxLayout(self), self.buttons)  # as Analyze's Single / Split / …
        self._restyle()
        theme.on_apply(self._restyle)

    def _clicked(self, index):
        self.setCurrentIndex(index)
        if self.MODES[index][0] == "trigger":
            self._open_trigger(self.buttons[index])

    def _restyle(self):
        """Look: QPushButton#viewTab in theme.py, like Analyze's view buttons."""
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        for i, (b, (_k, _l, glyph, _t)) in enumerate(zip(self.buttons, self.MODES)):
            active = i == self._index
            b.setProperty("active", "true" if active else "false")
            b.setStyleSheet("QPushButton#viewTab { padding-right: 24px; }" if b.dropdown else "")  # room for ▾
            if theme.is_mac():  # grey "on" segment: the icon stays the text colour
                b.setIcon(_material_icon(glyph, theme.icon_px("control"), c["text"]))
            else:
                b.setIcon(_material_icon(glyph, 20, c["on_accent"] if active else c["tab_text"]))

    def currentIndex(self):
        return self._index

    def currentData(self):
        return self.MODES[self._index][0]

    def label(self):
        return self.MODES[self._index][1]

    def findData(self, key):
        return next((i for i, m in enumerate(self.MODES) if m[0] == key), -1)

    def setCurrentIndex(self, index):
        if 0 <= index < len(self.buttons) and index != self._index:
            self._index = index
            self._restyle()
            self.currentIndexChanged.emit(index)


class LoggerPage(QWidget):
    """Data logger: controls row (channels dropdown first) and the live table.

    Inherits from QWidget directly instead of Page (QScrollArea) so the table
    can fill the height. The state.changed / busy_changed signals are wired up manually."""

    title = "Data logger"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        self.catalog = []
        self.catalog_module = None
        self.catalog_ecu = None  # whose channel list is loaded (state.ecu_key)
        self.raw_groups = set()
        self.running = False
        self.writer = None
        self.last_log = None  # the last recording (in Datalogs): what Export… copies
        self.logged = set()   # column keys being logged (shown in the list)
        self.seen_at = {}     # column key -> time.time() of its last reading ("no data" check)
        self.periods = {}     # group -> read interval (s) while logging
        self.latest = {}      # column key -> (text, unit)
        self.started = 0.0
        self.samples = 0
        self.errors = 0
        self.stats = {}       # column key -> [min, max, sum, count] (module units)
        self.stat_conv = {}   # column key -> display conversion
        self.latest_num = {}  # column key -> latest number (shown units), for the sidebar bars
        self.scales = {}      # column key -> ScaleTracker: the bar's gauge scale (as Live View's)
        self.latest_raw = {}  # column key -> latest number (module units): what alarm limits compare
        self.alarm_active = set()  # column keys past their alarm limit right now
        self.alarm_popped = {}     # column key -> time.time() its popup last showed
        self._alarm_popup_open = False
        self._flash = None         # alarms.ScreenFlash over the window while any alarm is tripped

        state.changed.connect(self.refresh)
        state.busy_changed.connect(lambda _busy: self.refresh())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The channels list: Analyze's channel list (channel_sidebar) as the main view, full
        # width: the ticked channels with their colour, the reading now and a gauge bar. Its
        # tree is also the model behind Select channels (search, categories, on/off switches;
        # raw groups at the end).
        self.sidebar = ChannelSidebar(
            state, "logger", SIDEBAR_COLUMNS,
            empty_text="No channels yet.\nUse Select channels to add some.",
            columns_tip="Choose which values the list shows: Now, Group, Min, Max, Avg",
            default=("now", "group"),
            bars_tip="Show or hide the bars under each channel: the reading on a gauge scale "
                     "(the unit's usual range, as Live View's bar gauges)")
        self.sidebar.setProperty("table", True)  # Mac: drawn as a rounded card, not a sidebar
        self.tree = self.sidebar.tree
        self.tree.reordered.connect(self._channels_changed)  # the log's column order follows
        self.tree.customContextMenuRequested.connect(self._channel_menu)
        # Under the list (the same on every page): what the value under the mouse shows, then
        # the groups summary. Raw groups are a category at the end of the list (＋ Add group…, ✕).
        self.summary = muted("")
        self.summary.setWordWrap(True)
        self.channels = ChannelDropdown(self.tree, self._channels_changed, self, footer=self.summary,
                                        describe=self._describe)
        self.selector = self.channels.selector
        self.selector.raw_groups_enabled = True
        self.selector.add_raw_group.connect(self._add_raw)
        self.selector.menu_requested.connect(self._selector_menu)
        self.selector.remove_raw_group.connect(self._remove_raw)
        self.sidebar.as_main()

        # ── Content: controls + channels list ──
        content = QWidget()
        content.setObjectName("page")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(10)
        outer.addWidget(scrollable(content), 1)

        # Row 1 (page_frame places it): Record + readout, Select channels, recording modes, the
        # log's name, then the right-hand icons.
        self.rate = QComboBox()
        for label, interval in RATES:
            self.rate.addItem(label, interval)
        self.start_button = T.RunButton("fiber_manual_record", "err")  # ● record / ■ stop (filled)
        self.start_button.setToolTip("Record: start reading the ticked values and saving them to a datalog.")
        self.start_button.clicked.connect(self.toggle)
        # The readout while logging (time · reads · rows …), right beside the record button
        from .widgets import ElidedLabel
        self.status = ElidedLabel("")  # one line; shortens with … (full text on hover)
        self.status.setObjectName("muted")
        self.status.setMinimumWidth(0)
        self.status.hide()  # shown once logging starts

        # Recording mode (continuous, triggered, rolling) as segments; Triggered's ▾ opens its
        # settings. Everything else is in the ⚙ menu.
        self.mode = RecordingModePicker(self._open_trigger_settings)
        # Watch in Analyze: the recording in progress, graphed live there
        self.watch_button = ToolIcon("troubleshoot", "Watch in Analyze: graph this recording "
                                          "live on the Analyze page while it records")
        self.watch_button.clicked.connect(self._watch)
        self.watch_button.setEnabled(False)
        # Smart presets: sets of this module's channels suggested by your AI (or built-in rules),
        # in Presets ▾ and as an optional row of chips under row 1, as on Analyze
        from . import smart_presets
        self.smart_profile = smart_presets.LoggerProfile()
        self.smart = smart_presets.Generator(state.settings, self, profile=self.smart_profile)
        self._smart_ids = {}  # channel name sent to the AI -> value id
        self.smart_bar = smart_presets.SmartBar(self, self.smart, verb="tick these channels",
                                                detail=self._smart_detail)
        self.smart_bar.apply.connect(self._apply_smart)
        self.smart.asked.connect(self._apply_smart)  # an Ask AI answer: ticked at once
        self.smart_bar.on_ask = lambda: smart_presets.ask_dialog(self, self.smart)
        state.faults_changed.connect(lambda _n: self.catalog and self._load_smart())  # new scan: fault sets
        self.smart_bar.hide()
        # Presets: the same button as Analyze's (bookmark ▾), beside ⚙
        self.presets = PresetButton(state, lambda: {v["id"] for v in self._checked_values()},
                                    lambda ids: self._build_tree(ids), on_loaded=self._preset_loaded,
                                    extra_menu=self._smart_menu)
        self.options = ToolIcon("instant_mix", "Logger settings: rate, group rates, how long Rolling keeps",
                                  menu=self._fill_options)
        # The settings' values live in these; the ⚙ menu and the Triggered popup set them.
        for w in (self.rate,):
            w.setParent(self)
            w.hide()
        self.trigger_channel = QComboBox()
        self.trigger_channel.setMinimumWidth(150)
        self.trigger_op = QComboBox()
        self.trigger_op.addItems([">", "<"])
        self.trigger_op.setFixedWidth(56)
        self.trigger_value = QDoubleSpinBox()
        self.trigger_value.setRange(-1e6, 1e6)
        self.trigger_value.setDecimals(1)
        self.trigger_value.setValue(90)
        self.trigger_value.setFixedWidth(84)
        self.pre_s = QDoubleSpinBox()
        self.pre_s.setRange(0, 120)
        self.pre_s.setValue(5)
        self.pre_s.setSuffix(" s before")
        self.post_s = QDoubleSpinBox()
        self.post_s.setRange(0, 120)
        self.post_s.setValue(3)
        self.post_s.setSuffix(" s after")
        self.trigger_panel = self._build_trigger_panel()
        self.keep_min = QDoubleSpinBox(self)  # Rolling: Keep last (⚙ menu); never shown
        self.keep_min.setRange(0.5, 120)
        self.keep_min.setValue(5)
        self.keep_min.hide()
        # Right of row 1: every recording saves itself into Datalogs; Recent logs ▾ lists
        # them, ⋮ exports (another format or place) and opens Datalogs.
        from .widgets import ElidedLabel
        self.name_preview = ElidedLabel("")  # the log being recorded, or the last one
        self.name_preview.setObjectName("muted")
        self.name_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.recent_button = ToolIcon(
            "history", "Recent logs: your recordings, newest first (each one is saved in Datalogs as "
            "it records). Open, export or discard them.", menu=self._fill_recent, dropdown=True)
        self._export_ok = False

        def fill_more(menu):
            T.menu_action(menu, "Export last log…", "ios_share", lambda: self._export(self.last_log), self._export_ok,
                          tip="Save the last log to Datalogs (or anywhere) as Standard CSV, Integrated "
                              "Engineering, MegaLogViewer or generic CSV")
            T.menu_action(menu, "Open Datalogs folder", "folder_open", self._open_datalogs,
                          tip="Where exports go")
        self.more_button = ToolIcon("more_vert", "More: export the last log, open the Datalogs folder",
                                    menu=fill_more)
        from .page_frame import page_frame
        page_frame(self, cl, action=(self.start_button, self.status), stretch_action=self.status,
                   left=(self.channels, self.mode), middle=self.name_preview, segments=self.mode.buttons,
                   tools=(self.sidebar.columns_button, self.sidebar.bars_button, self.watch_button),
                   recent=self.recent_button, presets=self.presets, settings=self.options, more=self.more_button)
        cl.addWidget(self.smart_bar)
        self._load_recording()
        for w in (self.mode, self.trigger_op):
            w.currentIndexChanged.connect(self._recording_changed)
        self.trigger_channel.currentIndexChanged.connect(self._recording_changed)
        for w in (self.trigger_value, self.pre_s, self.post_s, self.keep_min):
            w.valueChanged.connect(self._recording_changed)

        self.banner = Banner()
        cl.addWidget(self.banner)

        # The channels list (the ticked values, live while logging)
        cl.addWidget(self.sidebar, 1)

        self.ticker = QTimer(self)
        self.ticker.setInterval(150)
        self.ticker.timeout.connect(self._paint)
        state.sample.connect(self.on_sample)
        state.stop_logging.connect(lambda: self.running and self.stop())  # Analyze's live banner
        try:
            recent_logs.prune()  # recordings past the keep limits, on start
        except OSError:
            pass
        self._show_name()

    # --- module and catalog ---

    def refresh(self):
        self._show_name()
        family = self.state.setup_family
        module = family["module"] if family and family.get("can_log") else None
        ecu = self.state.catalog_params(module).get("ecu") if module is not None else None
        if (module, ecu) != (self.catalog_module, self.catalog_ecu) and not self.running:
            self.catalog_module, self.catalog_ecu = module, ecu
            self.catalog = []
            self.raw_groups = set()
            self._build_tree()
            self._load_smart()  # none until the new module's channels arrive
            if module is not None:
                self.state.offline("catalog", self.state.catalog_params(module), self._catalog_loaded,
                                   lambda e: self.banner.show_message(self.error_text(e), "error"))
        idle = not self.state.busy or self.running
        from .needs_car import gate
        # recording carries on if the adapter goes mid-log: Stop must always work
        if self.running:
            self.start_button.setEnabled(True)
        else:
            picked = self.state.family is not None and self.state.family.get("can_log")
            gate(self.start_button, self.state, bool(picked) and idle)  # plugged in: needs a real pick
        # channels can't change while logging, but the list stays live: it shows the readings
        self.channels.setEnabled(not self.running)
        self.tree.setDragEnabled(not self.running)  # the log's columns are fixed once it starts
        rolling = self.mode.currentData() == "loop"
        self.watch_button.setEnabled(self.running and not rolling)
        self.watch_button.setToolTip(
            "Watch in Analyze isn't available for Rolling: its file is written when you stop"
            if rolling else
            "Watch in Analyze: graph this recording live on the Analyze page while it records")
        if self.running:
            self.channels.close_picker()
        self.presets.setEnabled(not self.running and self.state.setup_family is not None)
        for w in (self.mode, self.trigger_panel):
            w.setEnabled(not self.running)
        if module is None and not self.running:
            self.banner.show_message("Choose a module that supports logging on the Connect page "
                                     "(engine, transmission or Haldex).", "info")
        elif not self.running and self.banner.property("level") == "info":
            self.banner.hide()

    def error_text(self, error):
        return error.get("message", str(error)) if isinstance(error, dict) else str(error)

    def _settings_key(self):
        return f"logger:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _catalog_loaded(self, values):
        self.catalog = values or []
        saved = self.state.settings.get(self._settings_key()) or {}
        self.raw_groups = set(saved.get("raw_groups", []))
        self._build_tree(set(saved.get("values", [])))
        self._load_smart()
        if not self.catalog:
            self.summary.setText("No named values for this module in this flasher build. "
                                 "Add whole groups below.")

    def _build_tree(self, checked=()):
        """Every named value as a row (known by its group.field key), plus each raw group as an
        always-on row; the ticked ones show with a colour, the reading now and a range bar."""
        self._units_built = unitconv.system(self.state.settings)
        self.tree.blockSignals(True)
        self.tree.clear()
        order = []
        with self.tree.bulk():
            for value in self.catalog:
                key = f"{value['group']}.{value['field']}"
                # Unit beside the name, as Analyze's list ("Engine speed (rpm)"); Now is just the number
                shown = unitconv.conversion(value.get("unit") or "", self.state.settings)[0]
                item = self.tree.new_item(f"{value['name']} ({shown})" if shown else value["name"], key)
                item.setData(0, VALUE_ROLE, value)
                item.setData(0, CATEGORY_ROLE, value.get("category") or "Other")
                item.setToolTip(0, self._tip(value, key))
                item.setText(self.sidebar.col["group"], key)
                if value["id"] in checked:
                    item.setCheckState(0, Qt.CheckState.Checked)
                order.append(key)
            for group in sorted(self.raw_groups):
                item = self.tree.new_item(f"Group {group} · all fields", f"raw:{group}")
                item.setData(0, VALUE_ROLE, {"raw_group": group})
                item.setData(0, FIXED_ROLE, True)  # always logged while it's in the list; ✕ removes it
                item.setData(0, RAW_GROUP_ROLE, group)
                item.setToolTip(0, f"Every field of group {group}, unnamed. ✕ removes it.")
                item.setText(self.sidebar.col["group"], str(group))
                item.setCheckState(0, Qt.CheckState.Checked)
        self.tree.log_order = order  # the selector lists them in catalog order, by category
        self.tree.blockSignals(False)
        self._channels_changed()
        self.channels.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        # Units may have been switched on the Settings page: the names carry the unit
        if getattr(self, "_units_built", None) not in (None, unitconv.system(self.state.settings)) \
                and not self.running and self.catalog:
            self._build_tree(self._checked_ids())

    def _channels_changed(self):
        """After switching values on or off, or a preset: colours, summary, preview."""
        self.tree.sync_rows(self.state.settings)
        self._selection_changed()
        self.sidebar.fit_width()  # Select channels (n/N) may have grown, as in Analyze

    def _describe(self, key):
        """The Select channels description box: the value under the mouse."""
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key), None)
        value = item.data(0, VALUE_ROLE) if item else None
        if isinstance(value, dict) and value.get("id"):
            return channel_info.tooltip(value)
        if isinstance(value, dict) and "raw_group" in value:
            return (f"<b>Group {value['raw_group']}</b> · every field, unnamed<br>Logged as "
                    f"<i>Group {value['raw_group']} field 1</i>, <i>field 2</i>… once the first read "
                    "arrives. ✕ removes the group.")
        return ""

    def _add_raw(self):
        if self.running:
            return
        group = ask_raw_group(self, self.raw_groups)
        if group is not None:
            self.raw_groups.add(group)
            self._build_tree(self._checked_ids())

    def _remove_raw(self, group):
        if self.running:
            return
        self.raw_groups.discard(group)
        self._build_tree(self._checked_ids())

    def _checked_values(self):
        """The ticked named values, in list order (raw groups are self.raw_groups)."""
        return [it.data(0, VALUE_ROLE) for it in self.tree.checked_items()
                if not it.data(0, FIXED_ROLE)]


    def _checked_ids(self):
        return {v["id"] for v in self._checked_values()}

    def _selection_changed(self):
        values = self._checked_values()
        groups = sorted({v["group"] for v in values} | self.raw_groups)
        if values or self.raw_groups:
            self.summary.setText(groups_summary(len(values), groups, len(self.raw_groups))
                                 + (" · custom group rates" if any(str(g) in self._group_rates()
                                                                   for g in groups) else ""))
        elif self.catalog:
            self.summary.setText("Tick the values you want to log.")
        key = self._settings_key()
        if key and self.catalog:
            self.state.settings.set(key, {"values": sorted(v["id"] for v in values),
                                          "raw_groups": sorted(self.raw_groups)})
        if not self.running:
            self._preview(values)
        self._fill_trigger_channels(values)

    # --- per-group rates ---

    # --- smart presets ---

    SMART_BAR_KEY = "logger:smart_bar"

    def _load_smart(self):
        """The module's channels (names with their module units, as sent to the AI) -> presets,
        led by sets for the module's fault codes from the last Diagnostics check."""
        from . import smart_presets
        self._smart_ids, groups = {}, {}
        for v in self.catalog:
            unit = v.get("unit") or ""
            name = f"{v['name']} ({unit})" if unit else v["name"]
            if name not in self._smart_ids:
                self._smart_ids[name] = v["id"]
                groups[name] = v["group"]
        self.smart_profile.groups = groups
        family = self.state.setup_family
        self.smart_profile.faults = smart_presets.faults_for(self.state.settings, family["name"]) if family else []
        self.smart.load(list(self._smart_ids))
        self._sync_smart_bar()

    def _smart_groups(self, layout):
        from .smart_presets import channels_of
        return sorted({self.smart_profile.groups[n] for n in channels_of(layout) if n in self.smart_profile.groups})

    def _smart_detail(self, layout):
        from .smart_presets import channels_of
        groups = self._smart_groups(layout)
        n = len(channels_of(layout))
        return (f"{n} channel{'s' if n != 1 else ''} · {len(groups)} measuring group{'s' if len(groups) != 1 else ''}"
                f" ({', '.join(map(str, groups))}): fewer groups read faster")

    def _apply_smart(self, layout):
        """A smart preset: tick exactly its channels (as loading a preset does)."""
        if self.running:
            return
        from .smart_presets import channels_of
        ids = {self._smart_ids[n] for n in channels_of(layout) if n in self._smart_ids}
        if ids:
            self._build_tree(ids)

    def _sync_smart_bar(self):
        on = bool(self.state.settings.get(self.SMART_BAR_KEY, False))
        self.smart_bar.setVisible(on and bool(self.catalog))

    def _smart_bar_toggled(self, on):
        self.state.settings.set(self.SMART_BAR_KEY, bool(on))
        self._sync_smart_bar()

    def _smart_menu(self, menu):
        """Presets ▾, top section: the smart presets for this module, then their options."""
        from . import ai, smart_presets
        T_ = T
        gen = self.smart
        T_.menu_header(menu, "Smart presets")
        T_.menu_action(menu, "Ask AI for a preset…", "auto_awesome", lambda: smart_presets.ask_dialog(self, self.smart),
                       enabled=bool(self.catalog) and not self.running and not gen.busy,
                       tip="Say what you want to look at; your AI picks the channels connected to it")
        for layout in gen.layouts:
            n, g = len(smart_presets.channels_of(layout)), len(self._smart_groups(layout))
            glyph = "warning" if layout.get("fault") else "auto_awesome" if layout.get("asked") \
                else smart_presets.icon_for(layout["name"])
            T_.menu_action(menu, f"{layout['name']}  ({n} · {g} group{'s' if g != 1 else ''})", glyph, lambda _=False, l=layout: self._apply_smart(l),
                           enabled=not self.running, tip=layout.get("description", ""))
        if gen.busy:
            menu.addAction("Generating with your AI…").setEnabled(False)
        elif not gen.layouts:
            menu.addAction("Choose a module first" if not self.catalog else "None for this module").setEnabled(False)
        sm = T_.menu_sub(menu, "Smart preset options", "auto_awesome")
        on = ai.enabled(self.state.settings)
        T_.menu_action(sm, "Generating…" if gen.busy else "Regenerate with AI", "refresh",
                       lambda: gen.regenerate(self), enabled=bool(gen.headers) and on and not gen.busy,
                       tip="Ask your AI again for this module's channels" if on
                       else "Choose an AI in Settings → AI first")
        T_.menu_toggle(sm, "Show smart preset chips", "view_carousel",
                       bool(self.state.settings.get(self.SMART_BAR_KEY, False)), self._smart_bar_toggled)
        T_.menu_toggle(sm, "Generate with AI automatically", "auto_awesome",
                       bool(self.state.settings.get(smart_presets.AI_KEY, True)),
                       lambda v: self.state.settings.set(smart_presets.AI_KEY, bool(v)),
                       tip="When a module with new channels is chosen, ask your AI for sets (only channel names are sent)")
        if any(l.get("asked") for l in gen.layouts):
            T_.menu_action(sm, "Remove my Ask AI presets", "delete", gen.forget_asked)
        if gen.error:
            sm.addSeparator()
            sm.addAction(f"Last AI attempt: {gen.error[:70]}").setEnabled(False)

    def _preset_loaded(self, name, builtin):
        """The Integrated Engineering Logger preset also makes Export… start on Integrated
        Engineering CSV (it remembers the format you pick there)."""
        if builtin and name == "Integrated Engineering Logger" and \
                self.state.settings.get("export_format") != "ie":
            self.state.settings.set("export_format", "ie")
            self.banner.show_message("Export… will now start on Integrated Engineering CSV.", "info")

    def _rates_key(self):
        return f"logger_rates:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _group_rates(self):
        key = self._rates_key()
        return dict(self.state.settings.get(key) or {}) if key else {}

    def _edit_rates(self):
        values = self._checked_values()
        groups = sorted({v["group"] for v in values} | self.raw_groups)
        if not groups:
            self.banner.show_message("Tick some values first; rates are set per group.", "warning")
            return
        names = {}
        for v in values:
            names.setdefault(v["group"], []).append(v["name"])
        dialog = GroupRatesDialog(groups, names, self._group_rates(), self)
        if dialog.exec():
            rates = self._group_rates()
            for g in groups:
                rates.pop(str(g), None)
            rates.update(dialog.rates())
            self.state.settings.set(self._rates_key(), rates)
            self._selection_changed()

    # --- recording mode ---

    def _load_recording(self):
        saved = self.state.settings.get("logger:recording") or {}
        self.mode.setCurrentIndex(max(self.mode.findData(saved.get("mode", "continuous")), 0))
        self.trigger_op.setCurrentText(saved.get("op", ">"))
        self.trigger_value.setValue(saved.get("value", 90))
        self.pre_s.setValue(saved.get("pre", 5))
        self.post_s.setValue(saved.get("post", 3))
        self.keep_min.setValue(saved.get("keep_min", 5))
        self._saved_trigger_key = saved.get("channel")
        self._show_recording()

    def _show_recording(self):
        if not self.running:
            self._start_text()

    def _build_trigger_panel(self):
        """Triggered's settings, dropping down from its button."""
        from PySide6.QtWidgets import QFormLayout, QLabel
        from .widgets import PopupCard, pair
        panel = PopupCard(self, min_width=380)
        box = QVBoxLayout(panel.body)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        heading = QLabel("TRIGGERED RECORDING")
        heading.setObjectName("optionsHeading")
        box.addWidget(heading)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addRow(muted("Start when"), pair(self.trigger_channel, self.trigger_op, self.trigger_value))
        form.addRow(muted("Keep"), pair(self.pre_s, self.post_s))
        box.addLayout(form)
        note = muted("Only the moments around each trigger are saved.")
        note.setWordWrap(True)
        box.addWidget(note)
        return panel

    def _open_trigger_settings(self, anchor):
        if not self.running:
            self.trigger_panel.open_at(anchor, align="left")

    def _fill_options(self, menu):
        """⚙: Rate ▸, Group rates…, Keep last ▸ (✓ on the current choice)."""
        idle = not self.running

        def choices(title, glyph, items, current, pick, tip="", enabled=None):
            sub = T.menu_sub(menu, title, glyph)
            sub.setEnabled(idle if enabled is None else enabled)
            if tip:
                sub.menuAction().setToolTip(tip)
            for label, value in items:
                act = sub.addAction(label)
                act.setCheckable(True)
                act.setChecked(value == current)
                act.triggered.connect(lambda _=False, v=value: pick(v))
            return sub

        T.menu_header(menu, "Reading")
        choices("Rate", "pace", RATES,  # read rate: same icon as Group rates (speed = dyno) self.rate.currentData(),
                lambda v: self.rate.setCurrentIndex(max(self.rate.findData(v), 0)),
                "How often each group is read")
        T.menu_action(menu, "Group rates…", "pace", self._edit_rates, idle,
                      tip="Read some groups faster than others")
        T.menu_header(menu, "Rolling")
        lengths = [(f"{m:g} min", float(m)) for m in (1, 2, 5, 10, 15, 30, 60)]
        if all(v != self.keep_min.value() for _l, v in lengths):  # an older custom length
            lengths.append((f"{self.keep_min.value():g} min", self.keep_min.value()))
        choices("Keep last", "autorenew", lengths, self.keep_min.value(), self.keep_min.setValue,
                "Rolling recording keeps this much, saved when you press Stop")
        T.menu_header(menu, "Saving")
        # Every log is saved in Datalogs (Standard format) as it records. Auto-export
        # also puts a copy in your format into Datalogs at every Stop, without the dialog.
        choices("Auto-export", "ios_share",
                [("Off", None)] + [(label, key) for key, label, _ext, _f in log_export.FORMATS
                                   if key != "datazap"],  # the log itself is already Standard, in Datalogs
                self._auto_export_format(),
                lambda v: self.state.settings.set("logger:auto_export", v),
                "When you stop, also save a copy in this format to Datalogs (no dialog). "
                "The log itself is always saved in Datalogs (Standard CSV) too.", enabled=True)

    # --- recent logs and export ---

    def _auto_export_format(self):
        fmt = self.state.settings.get("logger:auto_export")
        # Standard isn't offered any more: every recording is already a Standard CSV in Datalogs
        return fmt if fmt != "datazap" and any(f[0] == fmt for f in log_export.FORMATS) else None

    def _do_auto_export(self, path):
        """Auto-export at Stop: (message, written path or None); ("", None) when it's off."""
        fmt = self._auto_export_format()
        if not fmt:
            return "", None
        label = next(f[1] for f in log_export.FORMATS if f[0] == fmt)
        try:
            written = log_export.auto_export(Path(path), fmt, vin=self.state.vin())
        except Exception as e:
            return f" Auto-export ({label}) failed: {e}", None
        return f" Exported to Datalogs as {written.name} ({label}).", written

    def _show_name(self):
        """Right of the mode row: the log being recorded, or the last one."""
        if self.running and self.writer is not None:
            name, prefix = self.writer.path.name, "Recording: "
        elif self.last_log is not None and self.last_log.exists():
            name, prefix = self.last_log.name, "Last log: "
        else:
            self.last_log = None
            self.name_preview.setText("")
            self.name_preview.setToolTip("")
            self._export_ok = False
            return
        self.name_preview.setText(prefix + name)
        self.name_preview.setToolTip(f"{prefix}{name}\nSaved in Datalogs. Export… for another format or place.")
        self._export_ok = not self.running

    def _fill_recent(self, menu):
        """Recent logs ▾: each recording (newest first) with Open in Analyze / Export… / Discard."""
        items = recent_logs.entries(limit=recent_logs.KEEP)
        if not items:
            menu.addAction("No recordings yet").setEnabled(False)
            return
        idle = not self.running
        for path, entry in items:
            current = self.running and self.writer is not None and path == self.writer.path
            sub = T.menu_sub(menu, f"{path.stem}   {recent_logs.describe(entry)}",
                             "check_circle" if entry.get("exported") else "description")
            sub.setEnabled(not current)
            T.menu_action(sub, "Open in Analyze", "query_stats", lambda _=False, p=path: self._analyze(p))
            T.menu_action(sub, "Export…", "ios_share", lambda _=False, p=path: self._export(p), idle)
            T.menu_action(sub, "Discard…", "delete", lambda _=False, p=path: self._discard(p))
        menu.addSeparator()
        note = menu.addAction("Saved in Datalogs; nothing is deleted unless you discard it")
        note.setEnabled(False)

    def _analyze(self, path):
        self.state.open_in_playback.emit(str(path))

    def _export(self, path):
        if path is None or not Path(path).exists():
            self.banner.show_message("Record a log first: Export… saves a copy of it.", "warning")
            return
        log_export.export_dialog(self, self.state, Path(path), self.banner)

    def _discard(self, path):
        from .popups import MessageBox
        entry = recent_logs.info(path)
        warn = "" if entry.get("exported") else " It hasn't been exported."
        if not _yes(MessageBox.question(self, "Discard log", f"Delete {Path(path).name}?{warn} "
                                        "This can't be undone.")):
            return
        try:
            recent_logs.discard(path)
        except OSError as e:
            self.banner.show_message(f"Couldn't delete {Path(path).name}: {e}", "error")
            return
        if self.last_log is not None and Path(path) == self.last_log:
            self.last_log = None
        self.banner.show_message(f"Discarded {Path(path).name}.", "info")
        self._show_name()

    def _open_datalogs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(datalogs_dir())))

    def _start_text(self):
        """Record (●; the mode buttons under it show which mode); its tooltip says what this
        mode will save."""
        mode = self.mode.currentData()
        self.start_button.set_recording(False)
        self.start_button.setToolTip(
            {"trigger": "Record: only the moments around each trigger are saved (set it up with "
                        "the ▾ on Triggered).",
             "loop": "Record: the last few minutes are kept and saved when you press Stop "
                     "(⚙ → Keep last)."}.get(
                mode, "Record: read the ticked values and save them to a datalog. "
                      "Pick Continuous, Triggered or Rolling below."))

    def _recording_changed(self, *_):
        self._show_recording()
        key = self.trigger_channel.currentData() or self._saved_trigger_key
        self.state.settings.set("logger:recording", {
            "mode": self.mode.currentData(), "op": self.trigger_op.currentText(),
            "value": self.trigger_value.value(), "pre": self.pre_s.value(),
            "post": self.post_s.value(), "keep_min": self.keep_min.value(), "channel": key})

    def _fill_trigger_channels(self, values):
        current = self.trigger_channel.currentData() or self._saved_trigger_key
        self.trigger_channel.blockSignals(True)
        self.trigger_channel.clear()
        for v in values:
            label = f"{v['name']} ({v['unit']})" if v.get("unit") else v["name"]
            self.trigger_channel.addItem(label, f"{v['group']}.{v['field']}")
        index = self.trigger_channel.findData(current)
        if index < 0:
            index = next((i for i, v in enumerate(values)
                          if any(w in v["name"].lower() for w in ("throttle", "pedal"))), 0)
        self.trigger_channel.setCurrentIndex(index)
        self.trigger_channel.blockSignals(False)

    def _preview(self, values):
        """What will be logged, before Start: the list shows the ticked values with "–"."""
        self.logged = {f"{v['group']}.{v['field']}" for v in values} | {f"raw:{g}" for g in self.raw_groups}
        self.latest = {}
        self.latest_num = {}
        self.scales = {}
        self.seen_at = {}
        self._clear_sidebar()

    # --- running ---

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.state.need_adapter("log"):
            return  # nothing created: no empty log file
        if self.state.family is None:
            self.banner.show_message("No module picked yet. Press Scan on the Connect page (or "
                                     "Select manually there) first.", "warning",
                                     actions={"Go to Connect": lambda: self.state.open_tab.emit("connect")})
            return
        values = self._checked_values()
        groups = sorted({v["group"] for v in values} | self.raw_groups)
        if not groups:
            self.banner.show_message("Tick at least one value, or add a group.", "warning")
            return
        self.columns = [(f"{v['group']}.{v['field']}", v["name"]) for v in values]
        self._preview(values)
        module = self.state.setup_family or {}
        mode = self.mode.currentData()
        # Recorded into Datalogs; the car's VIN goes in its index entry (exports and Analyze
        # find it there), so the name stays short: engine_2026-11-09_073012.csv
        path = recent_logs.new_log(module.get("name", ""), vin=self.state.vin(), mode=mode)
        trigger = None
        if mode == "trigger":
            key = self.trigger_channel.currentData()
            if not key:
                self.banner.show_message("Pick the channel that starts a triggered log.", "warning")
                return
            limit, above = self.trigger_value.value(), self.trigger_op.currentText() == ">"

            def crossed(latest, key=key, limit=limit, above=above):
                value = latest.get(key)
                return isinstance(value, (int, float)) and (value > limit if above else value < limit)
            trigger = crossed
        # Companion data (log_info): each column's group/field/kind now, formula and unit
        # from its first read (on_sample).
        self.info_columns = {f"{v['group']}.{v['field']}": {
            "group": v["group"], "field": v["field"], "kind": v.get("kind", "number"),
            "id": v.get("id")} for v in values}
        self.writer = DatazapWriter(path, self.columns, groups, mode=mode,
                                    keep_s=self.keep_min.value() * 60, trigger=trigger,
                                    pre_s=self.pre_s.value(), post_s=self.post_s.value(),
                                    info_columns=self.info_columns)
        self.samples = self.errors = 0
        self.stats = {}
        self.latest_raw = {}
        self._reset_alarms()
        rates = self._group_rates()
        self.periods = {g: (rates.get(str(g)) or self.rate.currentData() or 0) / 1000 for g in groups}
        self.started = time.time()
        self.banner.show_message(f"Recording {path.name}. Press Stop when you're done.", "info")
        rates = {g: ms for g, ms in self._group_rates().items() if int(g) in groups}
        self.running = self.state.run(
            "log", {"groups": groups, "interval_ms": self.rate.currentData(), "group_periods": rates},
            self._finished, self._failed)
        if self.running:
            self.state.logger_recording.emit(True)  # Live View shows the readings too
            self.start_button.set_recording(True)
            self.start_button.setToolTip("Stop: stop logging and save the datalog.")
            self.ticker.start()
        self.refresh()

    def stop(self):
        self.start_button.setEnabled(False)  # greys out until the log is saved
        self.start_button.setToolTip("Stopping…")
        self.state.stop()

    def _add_raw_field(self, key, name, group):
        """A raw group's field, learned from its first read: its own row in the list. The rows
        stay after Stop (with the last readings); the next recording reuses them."""
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key
                     and it.data(0, RAW_GROUP_ROLE) is not None), None)  # not a named value's row
        if item is not None:
            self.logged.add(key)
            return
        item = self.tree.new_item(name, key)
        item.setData(0, VALUE_ROLE, {"raw_group": group})
        item.setData(0, FIXED_ROLE, True)
        item.setData(0, RAW_GROUP_ROLE, group)
        item.setCheckState(0, Qt.CheckState.Checked)
        for key_, col in self.sidebar.col.items():
            item.setText(col, key if key_ == "group" else "–")
        self.tree.sync_rows(self.state.settings)
        self.logged.add(key)

    def _drop_raw_placeholder(self, group):
        """The "Group N · all fields" row gives way to the group's fields (it comes back when
        the channels change: adding or removing a group, a new module)."""
        key = f"raw:{group}"
        self.logged.discard(key)
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).data(0, ROLE) == key:
                self.tree.takeTopLevelItem(i)
                break

    # --- channels sidebar ---

    def _channel_menu(self, pos):
        """Right-click on the sidebar: Analyze's menu, minus the graph-only items."""
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        idle = not self.running
        if item is not None:
            T.menu_header(menu, "This channel")
            if item.data(0, RAW_GROUP_ROLE) is not None:
                group = item.data(0, RAW_GROUP_ROLE)
                T.menu_action(menu, "Remove from log", "close", lambda: self._remove_raw(group), enabled=idle)
            else:
                T.menu_action(menu, "Remove from log", "close",
                              lambda: (item.setCheckState(0, Qt.CheckState.Unchecked),
                                       self._channels_changed(), self.channels.refresh()), enabled=idle)
                key = item.data(0, ROLE)
                value = item.data(0, VALUE_ROLE) or {}
                # alarms can be set or changed mid-recording
                T.menu_action(menu, f"Set alarm for {value.get('name', 'this channel')}…", "notifications",
                              lambda: self._edit_alarm(key))
        T.menu_header(menu, "All channels")
        T.menu_action(menu, "Select channels…", "checklist", self.channels.click, enabled=idle)
        T.menu_action(menu, "Remove all", "clear_all", self._remove_all, enabled=idle)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _remove_all(self):
        self.raw_groups = set()
        self._build_tree(set())

    def columns_name(self, key):
        return next((name for k, name in getattr(self, "columns", ()) if k == key), "")

    def _stale(self, key, now_t):
        """True when a logged channel hasn't had a reading for a while: 3 s, or 3 of its
        group's read intervals when that's longer (a group read every 5 s isn't stale at 4 s)."""
        try:
            group = int(str(key).split(":")[-1].split(".")[0])
        except ValueError:
            return False
        limit = max(3.0, 3 * self.periods.get(group, 0))
        return now_t - self.seen_at.get(key, self.started) > limit

    def _clear_sidebar(self):
        for item in self.tree.items():
            fixed = bool(item.data(0, FIXED_ROLE))
            for key_, col in self.sidebar.col.items():
                if key_ != "group":  # where it's read from doesn't change
                    item.setText(col, "" if fixed else "–")
            item.setData(0, FRAC_ROLE, None)
            item.setData(0, STALE_ROLE, None)

    def _update_sidebar(self):
        """The ticked channels' Now / Min / Max / Avg and where Now sits in its range."""
        now_t = time.time()
        for item in self.tree.items():
            key = item.data(0, ROLE)
            if item.isHidden():
                continue
            if self.running and key in self.logged and self._stale(key, now_t):
                age = now_t - self.seen_at.get(key, self.started)
                item.setText(self.sidebar.col["now"], f"no data {age:.0f} s")
                item.setData(0, STALE_ROLE, True)
                continue
            if item.data(0, STALE_ROLE):
                item.setData(0, STALE_ROLE, None)
            latest = self.latest.get(key)
            if latest is None:
                continue
            text, unit = latest  # the unit is in the row's name
            reading = text[:-len(unit)].strip() if unit and text.endswith(unit) else text
            item.setText(self.sidebar.col["now"], reading)
            st = self.stats.get(key)
            if not st:
                continue
            conv = self.stat_conv.get(key, unitconv.IDENTITY)
            for col, number in (("min", st[0]), ("max", st[1]), ("avg", st[2] / st[3])):
                item.setText(self.sidebar.col[col], _fmt(unitconv.to_display(number, conv)))
            now, scale = self.latest_num.get(key), self.scales.get(key)
            if now is not None and scale is not None:
                lo, hi = scale.range()  # the unit's usual range, widened to what's been seen
                item.setData(0, FRAC_ROLE, (now - lo) / (hi - lo))

    def ai_context(self):
        """What the Assistant sees of this page (None: nothing logged yet)."""
        col = self.sidebar.col
        rows = []
        for item in self.tree.items():
            reading = item.text(col["now"])
            if item.isHidden() or reading in ("", "–") or item.data(0, STALE_ROLE):
                continue
            rows.append(f"- {item.text(0)}: {reading} (min {item.text(col['min'])}, "
                        f"max {item.text(col['max'])}, avg {item.text(col['avg'])})")
        if not rows:
            return None
        family = self.state.setup_family or {}
        context = (f"Module: {family.get('label', '?')}, part {self.state.part() or '?'}\n"
                   f"Logger {'recording' if self.running else 'stopped'}: {self.status.fullText()}\n"
                   "Values (reading now, then min/max/avg since Start; units in brackets):\n"
                   + "\n".join(rows))
        return {"title": "Logger", "context": context,
                "question": "Explain what these logged values say about the car: what's normal, what looks "
                            "off, and what to check or log next."}

    # --- alarms (the same limits as Live View's: set in either, they work in both) ---

    POPUP_AGAIN_S = 30  # a channel hovering at its limit pops up at most this often

    def _alarms_key(self):
        return f"alarms:{self.state.setup_family['name']}" if self.state.setup_family else None

    def _limits(self):
        key = self._alarms_key()
        return (self.state.settings.get(key) or {}) if key else {}

    def _tip(self, value, key, limits=None):
        limits = self._limits() if limits is None else limits
        conv = unitconv.conversion(value.get("unit") or "", self.state.settings)[1]
        alarm = alarms.describe(unitconv.limit_to_display(limits.get(key), conv))
        return channel_info.tooltip(value) + (f"<br>Alarm: {alarm}" if alarm
                                              else "<br>No alarm (right-click to set one)")

    def _selector_menu(self, key, global_pos):
        """Right-click a channel in Select channels: its alarm."""
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key), None)
        value = item.data(0, VALUE_ROLE) if item is not None else None
        if not isinstance(value, dict) or not value.get("id"):
            return  # raw groups have no named channel to set a limit on
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        T.menu_action(menu, f"Set alarm for {value['name']}…", "notifications",
                      lambda: (self.channels.close_picker(), self._edit_alarm(key)))
        menu.exec(global_pos)

    def _edit_alarm(self, key):
        settings_key = self._alarms_key()
        item = next((it for it in self.tree.items() if it.data(0, ROLE) == key), None)
        value = item.data(0, VALUE_ROLE) if item is not None else None
        if not settings_key or not isinstance(value, dict) or not value.get("id"):
            return
        limits = dict(self._limits())
        shown, conv = unitconv.conversion(value.get("unit") or "", self.state.settings)
        dialog = alarms.AlarmDialog(value["name"], shown, unitconv.limit_to_display(limits.get(key), conv), self)
        if not dialog.exec():
            return
        limit = unitconv.limit_to_raw(dialog.limit(), conv)
        if limit is None:
            limits.pop(key, None)
        else:
            limits[key] = limit
        self.state.settings.set(settings_key, limits)
        self.alarm_popped.pop(key, None)  # a new limit may pop up straight away
        for it in self.tree.items():
            v = it.data(0, VALUE_ROLE)
            if isinstance(v, dict) and v.get("id"):
                it.setToolTip(0, self._tip(v, it.data(0, ROLE), limits))

    def _check_alarms(self):
        """Mark channels past their limits red, beep, and pop up a warning for newly tripped ones."""
        if not self.running:
            return
        limits = self._limits()
        active = {key for key, number in self.latest_raw.items()
                  if key in self.logged and alarms.tripped(limits.get(key), number)}
        new = active - self.alarm_active
        if active and not self.alarm_active:
            if self._flash is None:
                self._flash = alarms.ScreenFlash(self.window())
            self._flash.start()
        elif self.alarm_active and not active and self._flash is not None:
            self._flash.stop()
        if active != self.alarm_active:
            for item in self.tree.items():
                key = item.data(0, ROLE)
                item.setData(0, ALARM_ROLE, True if key in active else None)
        self.alarm_active = active
        if not new:
            return
        if any(limits[key].get("beep", True) for key in new):
            QApplication.beep()
        now = time.time()
        due = [k for k in sorted(new) if now - self.alarm_popped.get(k, 0) > self.POPUP_AGAIN_S]
        if due and not self._alarm_popup_open:
            for k in due:
                self.alarm_popped[k] = now
            # after this paint returns: the popup waits for an answer while recording carries on
            QTimer.singleShot(0, lambda keys=due: self._alarm_popup(keys))

    def _alarm_text(self, key, limits):
        name = self.columns_name(key) or key
        text, unit = self.latest.get(key, ("–", ""))
        reading = text[:-len(unit)].strip() if unit and text.endswith(unit) else text
        unit = f" {unit}" if unit else ""
        conv = self.stat_conv.get(key, unitconv.IDENTITY)
        limit = alarms.describe(unitconv.limit_to_display(limits.get(key), conv))
        return f"<b>{escape(name)}</b> is <b>{escape(reading + unit)}</b> (alarm: {escape(limit + unit)})"

    def _alarm_popup(self, keys):
        from .disclaimer import run_card
        from .popups import _host
        window = _host(self)
        if window is None or not self.running or self._alarm_popup_open:
            return
        limits = self._limits()
        lines = "<br>".join(self._alarm_text(k, limits) for k in keys if k in limits)
        if not lines:
            return
        from .disclaimer import Disclaimer
        self._alarm_popup_open = True
        try:
            keep = run_card(window, lambda overlay: Disclaimer(
                overlay, title="Alarm", icon="warning", level="err", size="compact",
                text=f"<p>{lines}</p><p>Recording carries on.</p>",
                decline="Stop recording", accept="Keep recording"))
        finally:
            self._alarm_popup_open = False
        if not keep and self.running:
            self.stop()

    def _reset_alarms(self):
        self.alarm_active = set()
        self.alarm_popped = {}
        if self._flash is not None:
            self._flash.stop()
        for item in self.tree.items():
            if item.data(0, ALARM_ROLE):
                item.setData(0, ALARM_ROLE, None)

    def _watch(self):
        """Watch in Analyze: Analyze opens the recording and follows it as rows are written."""
        if self.running and self.writer:
            self.state.follow_log.emit(str(self.writer.path))

    def on_sample(self, sample):
        if not self.running:
            return
        self.samples += 1
        if sample.get("error"):
            self.errors += 1
            return
        group = sample["group"]
        values, units = {}, {}
        for field in sample.get("fields") or ():
            key = f"{group}.{field['index']}"
            if group in self.raw_groups and key not in self.logged and not self.writer.ready:
                self._drop_raw_placeholder(group)
                self.columns.append((key, f"Group {group} field {field['index']}"))
                self._add_raw_field(key, self.columns[-1][1], group)
            if key in self.logged:
                self.seen_at[key] = time.time()
                meta = self.info_columns.setdefault(key, {"group": group, "field": field["index"],
                                                          "kind": "number"})
                if "formula" not in meta:
                    meta.update(formula=field.get("formula"), unit=field.get("unit", ""))
                raw_unit = field.get("unit", "")
                values[key] = csv_number(field)
                shown, conv = unitconv.conversion(raw_unit, self.state.settings)
                if conv != unitconv.IDENTITY and isinstance(values[key], (int, float)):
                    self.latest[key] = (_fmt(unitconv.to_display(values[key], conv)), shown)
                else:
                    self.latest[key] = (field.get("text") or str(field.get("value")), raw_unit)
                self.stat_conv[key] = conv
                number = values[key]
                if isinstance(number, (int, float)):
                    shown_number = unitconv.to_display(number, conv) if conv != unitconv.IDENTITY else number
                    scale = self.scales.get(key)
                    if scale is None:
                        scale = self.scales[key] = ScaleTracker(self.latest[key][1], self.columns_name(key))
                    scale.update(shown_number)
                    self.latest_num[key] = shown_number
                    self.latest_raw[key] = number
                    st = self.stats.get(key)
                    if st is None:
                        self.stats[key] = [number, number, number, 1]
                    else:
                        st[0] = min(st[0], number)
                        st[1] = max(st[1], number)
                        st[2] += number
                        st[3] += 1
                units[key] = field.get("unit", "")
        self.writer.update(group, values, units, sample.get("wall_time", time.time()) - self.started,
                           raw=log_info.raw_hex(sample.get("fields")))

    def _paint(self):
        self._check_alarms()
        self._update_sidebar()
        elapsed = time.time() - self.started
        rate = self.samples / elapsed if elapsed > 0 else 0
        self.status.show()
        self.status.setText(f"{int(elapsed // 60)}:{int(elapsed % 60):02d} · {self.samples} reads "
                            f"({rate:.1f}/s) · {self.writer.rows if self.writer else 0} CSV rows"
                            + (f" · {self.writer.status}" if self.writer and self.writer.status else "")
                            + (f" · {self.errors} errors" if self.errors else ""))

    def _end(self):
        self.running = False
        self.state.logger_recording.emit(False)
        self.ticker.stop()
        self._paint()
        self._reset_alarms()
        for item in self.tree.items():  # no "no data" once stopped: the last readings stay
            if item.data(0, STALE_ROLE):
                item.setData(0, STALE_ROLE, None)
                item.setText(self.sidebar.col["now"], "–")
        if self.writer:
            self.writer.close()
            self.state.follow_log.emit("")  # Analyze, if following it, reads the last rows
        self._start_text()
        self.refresh()

    def _finished(self, result):
        self._end()
        writer = self.writer
        rows = writer.rows if writer else 0
        if writer:
            span = (writer.last_t - writer.first_t) if writer.first_t is not None else 0.0
            recent_logs.finish(writer.path, rows, span)
        if result.get("error"):
            note = ""
            if rows:
                self.last_log = writer.path
                note, _written = self._do_auto_export(writer.path)
            self.banner.show_message("Logging stopped: " + str(result["error"])
                                     + (f" The {rows} rows read before that are saved in Datalogs." if rows else "")
                                     + note, "error")
        elif rows:
            self.last_log = path = writer.path
            secs = writer.last_t - writer.first_t
            note, written = self._do_auto_export(path)
            links = {"Open in Analyze": lambda: self._analyze(path)}
            if written is not None:
                from .widgets import reveal_file
                links["Show in Finder" if sys.platform == "darwin" else "Show in folder"] = \
                    lambda: reveal_file(written)
            else:
                links["Export…"] = lambda: self._export(path)
            links["Discard…"] = lambda: self._discard(path)
            silent = [t for t in writer.missing if t not in writer.latest_titles()]
            silent_note = (" No reply from the module for: " + ", ".join(silent[:6]) + ("…" if len(silent) > 6 else "")
                           + " (blank in the log; untick them next time).") if silent else ""
            self.banner.show_message(
                f"Logged {rows:,} rows ({int(secs // 60)}:{int(secs % 60):02d}), saved in Datalogs as {path.name}."
                + (note or ("" if silent else " Min / max / avg are in the list.")) + silent_note,
                "error" if note and written is None else ("warning" if silent else "ok"), links)
        elif writer and writer.mode == "trigger" and writer.ready:
            self.banner.show_message("Stopped. The trigger never fired, so nothing was recorded.", "warning")
        else:
            self.banner.show_message("Stopped. No complete rows yet, so nothing was recorded: every "
                                     "value needs at least one reading.", "warning")
        recent_logs.prune(protect=[self.last_log])
        self._show_name()

    def _failed(self, error):
        self._end()
        writer = self.writer
        rows = writer.rows if writer else 0
        if writer:
            span = (writer.last_t - writer.first_t) if writer.first_t is not None else 0.0
            recent_logs.finish(writer.path, rows, span)
            if rows:
                self.last_log = writer.path
        self.banner.show_message(self.error_text(error)
                                 + (f" The {rows} rows read before that are saved in Datalogs." if rows else ""),
                                 "error")
        self._show_name()
