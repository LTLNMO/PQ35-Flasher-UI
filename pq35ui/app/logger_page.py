"""Data logger: pick named values, poll their measuring groups, show them live
and write a Datazap-friendly CSV.

Layout: log controls, then the channels list (Analyze's channel list, full width: colour, name,
Now / Min / Max / Avg, gauge bar; amber "no data" when a channel stops updating)."""
import collections
import csv
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QComboBox, QHBoxLayout, QHeaderView, QMenu, QPushButton,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import channel_info, log_export, recent_logs
from . import units as unitconv
from .storage import datalogs_dir
from .presets import PresetButton
from .channel_list import (CATEGORY_ROLE, FIXED_ROLE, FRAC_ROLE, RAW_GROUP_ROLE, ROLE, STALE_ROLE, VALUE_ROLE,
                           ChannelDropdown,
                           ask_raw_group, groups_summary)
from .channel_sidebar import ChannelSidebar
from . import analyze_tools as T
from .widgets import Banner, muted, scrollable
from .popups import OverlayDialog
from .gauges import ScaleTracker

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


def _fmt(number):
    if abs(number) >= 1000:
        return f"{number:.0f}"
    if abs(number) >= 10:
        return f"{number:.1f}"
    return f"{number:.2f}"


class DatazapWriter:
    """CSV with a "Time" column in seconds and one "Name (unit)" column per
    value. A row is made per group read; values forward-fill between reads.
    Rows wait until every group has been read and every column has a value,
    so the columns are final and no cell is empty.

    mode:
      "continuous" - every row goes to the file.
      "loop"       - only the last keep_s seconds are kept, written at close.
      "trigger"    - rows are written while trigger(latest) is true, plus
                     pre_s seconds before it and post_s seconds after it ends.
                     Re-arms after each event, so several pulls land in one file."""

    def __init__(self, path, columns, groups, mode="continuous", keep_s=300.0,
                 trigger=None, pre_s=5.0, post_s=3.0):
        self.path = path
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

    def update(self, group: int, values: dict, units: dict, seconds: float):
        self.seen.add(group)
        self.latest.update({k: v for k, v in values.items() if v is not None})
        self.units.update({k: u for k, u in units.items() if u})
        if not self.ready:
            if self.seen < self.groups or not all(key in self.latest for key, _ in self.columns):
                return
            self.ready = True
        row = [f"{seconds:.3f}"] + [self.latest[key] for key, _ in self.columns]
        if self.mode == "loop":
            self._buffer(seconds, row, self.keep_s)
        elif self.mode == "trigger":
            self._triggered_row(seconds, row)
        else:
            self._write(row)

    @property
    def status(self):
        if self.mode == "loop":
            span = self.buffer[-1][0] - self.buffer[0][0] if len(self.buffer) > 1 else 0
            return f"rolling buffer {span:.0f} s of {self.keep_s:.0f} s"
        if self.mode == "trigger":
            state = "TRIGGERED" if self.triggered else "armed"
            return f"{state} · {self.events} event(s)"
        return ""

    def _buffer(self, seconds, row, keep):
        self.buffer.append((seconds, row))
        while self.buffer and seconds - self.buffer[0][0] > keep:
            self.buffer.popleft()

    def _triggered_row(self, seconds, row):
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
                    self._write(self.buffer.popleft()[1])
            self._write(row)
        elif self.triggered and seconds - self.last_true <= self.post_s:
            self._write(row)
        else:
            self.triggered = False
            self._buffer(seconds, row, self.pre_s)

    def _write(self, row):
        if self.writer is None:
            # Line-buffered: every row reaches the file whole, at once (Analyze can follow it live)
            self.file = open(self.path, "w", newline="", encoding="utf-8", buffering=1)
            self.writer = csv.writer(self.file)
            self.writer.writerow(["Time"] + [self._title(key, title) for key, title in self.columns])
        self.writer.writerow(row)
        self.rows += 1
        self.last_t = float(row[0])
        if self.first_t is None:
            self.first_t = self.last_t
        if self.rows % 50 == 0:
            self.file.flush()

    def _title(self, key, title):
        unit = self.units.get(key, "")
        return f"{title} ({unit})" if unit else title

    def close(self):
        if self.mode == "loop":
            while self.buffer:
                self._write(self.buffer.popleft()[1])
        if self.file:
            self.file.close()


def _yes(answer):
    from PySide6.QtWidgets import QMessageBox
    return answer == QMessageBox.StandardButton.Yes


class _ModeButton(QPushButton):
    """A recording-mode button; with a dropdown, a small ▾ after the name (its settings)."""

    def __init__(self, label, dropdown=False):
        super().__init__(label)
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


# Dyno mode: what it ticks, which of those are read only every few seconds, and its trigger.
DYNO_VALUES = ("engine.rpm", "vehicle.speed", "engine.pedal_position", "engine.throttle_position",
               "engine.atmospheric_pressure", "ambient.temperature")
DYNO_SLOW = ("engine.atmospheric_pressure", "ambient.temperature")
DYNO_SLOW_MS = 3000
DYNO_PEDAL = 90.0   # % pedal: a pull
DYNO_PRE_S, DYNO_POST_S = 2.0, 1.0


class RecordingModePicker(QWidget):
    """Continuous | Triggered | Rolling (key "loop"), styled like Analyze's view buttons (Single, Split…): icon +
    name, the current one filled with the accent colour. Triggered has a ▾: picking it opens its
    settings right under it. Keeps the bit of QComboBox's API the logger uses (currentData,
    findData, setCurrentIndex, currentIndexChanged)."""
    currentIndexChanged = Signal(int)
    dyno_clicked = Signal()
    MODES = (
        ("continuous", "Continuous", "all_inclusive", "Record everything from Start to Stop"),
        ("trigger", "Triggered", "bolt_boost", "Record only around moments when a value crosses a limit "
                                        "(e.g. throttle > 90 %), with a few seconds before and after. "
                                        "Click to set it up."),
        ("loop", "Rolling", "autorenew", "Keep only the last few minutes, saved when you press Stop "
                                         "(how long: ⚙ → Keep last)"),
        ("dyno", "Dyno", "rocket_launch", "Dyno pulls: ticks what the dyno needs and records each "
                                          "full-throttle pull (pedal over 90 %, 2 s before, 1 s after). "
                                          "Then Open Dyno shows horsepower and torque."),
    )

    def __init__(self, open_trigger_settings, open_dyno_settings=None, parent=None):
        super().__init__(parent)
        self._open_trigger = open_trigger_settings
        self._open_dyno = open_dyno_settings or (lambda anchor: None)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.buttons = []
        self._index = 0
        for i, (_key, label, _glyph, tip) in enumerate(self.MODES):
            b = _ModeButton(label, dropdown=_key in ("trigger", "dyno"))
            b.setObjectName("viewTab")
            b.setFixedHeight(34)  # same height as every toolbar button
            b.setToolTip(tip)
            b.setProperty("active", "false")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setIconSize(QSize(20, 20))
            b.clicked.connect(lambda _=False, i=i: self._clicked(i))
            row.addWidget(b)
            self.buttons.append(b)
        self._restyle()
        from . import theme
        theme.on_apply(self._restyle)

    def _clicked(self, index):
        self.setCurrentIndex(index)
        if self.MODES[index][0] == "trigger":
            self._open_trigger(self.buttons[index])
        elif self.MODES[index][0] == "dyno":
            self.dyno_clicked.emit()
            self._open_dyno(self.buttons[index])

    def _restyle(self):
        """Look: QPushButton#viewTab in theme.py, like Analyze's view buttons."""
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        for i, (b, (_k, _l, glyph, _t)) in enumerate(zip(self.buttons, self.MODES)):
            active = i == self._index
            b.setProperty("active", "true" if active else "false")
            b.setStyleSheet("QPushButton#viewTab { padding-right: 24px; }" if b.dropdown else "")  # room for ▾
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
        self.raw_groups = set()
        self.running = False
        self.writer = None
        self.last_log = None  # the last recording (in Recent logs): what Export… saves
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
        self.selector.remove_raw_group.connect(self._remove_raw)
        self.sidebar.as_main()

        # ── Content: controls + channels list ──
        content = QWidget()
        content.setObjectName("page")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(10)
        outer.addWidget(scrollable(content), 1)

        # Controls row: actions, recording mode, ⚙ (settings) at the right
        ctrl = self.ctrl_row = QHBoxLayout()
        ctrl.setSpacing(8)
        self.rate = QComboBox()
        for label, interval in RATES:
            self.rate.addItem(label, interval)
        self.start_button = T.RunButton("fiber_manual_record", "err")  # ● record / ■ stop (filled)
        self.start_button.setToolTip("Record: start reading the ticked values and saving them to a datalog.")
        self.start_button.clicked.connect(self.toggle)
        # Actions first (left, always in reach), then the options; wraps when narrow.
        ctrl.addWidget(self.channels)
        ctrl.addWidget(self.start_button)
        # The readout while logging (time · reads · rows …), right beside the record button
        from .widgets import ElidedLabel
        self.status = ElidedLabel("")  # one line; shortens with … (full text on hover)
        self.status.setObjectName("muted")
        self.status.setMinimumWidth(0)
        self.status.hide()  # shown once logging starts
        ctrl.addSpacing(4)
        ctrl.addWidget(self.status, 100)  # takes the free room (the stretch after it only when hidden)

        # Recording mode (continuous, triggered, rolling) as view buttons on their own row under
        # the toolbar, like Analyze's Single / Split / … row; Triggered's ▾ opens its settings.
        # Everything else is in the ⚙ menu.
        self.mode = RecordingModePicker(self._open_trigger_settings, self._open_dyno_settings)
        self.mode.dyno_clicked.connect(self._dyno_setup)
        ctrl.addStretch(1)
        # The list's buttons (as on Analyze's sidebar): which values show, gauge bars on/off
        ctrl.addWidget(self.sidebar.columns_button)
        ctrl.addWidget(self.sidebar.bars_button)
        # Watch in Analyze: the recording in progress, graphed live there
        self.watch_button = T.icon_button("troubleshoot", "Watch in Analyze: graph this recording "
                                          "live on the Analyze page while it records")
        self.watch_button.clicked.connect(self._watch)
        self.watch_button.setEnabled(False)
        ctrl.addWidget(self.watch_button)
        # Presets: the same button as Analyze's (bookmark ▾), beside ⚙
        self.presets = PresetButton(state, lambda: {v["id"] for v in self._checked_values()},
                                    lambda ids: self._build_tree(ids), on_loaded=self._preset_loaded)
        ctrl.addWidget(self.presets)
        self.options = T.MenuIconButton("settings", "Logger settings: rate, group rates, how long Rolling keeps",
                                        self._fill_options)
        ctrl.addWidget(self.options)
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
        cl.addLayout(ctrl)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)  # the toolbar's gap
        mode_row.addWidget(self.mode)
        mode_row.addSpacing(12)
        # Right of the mode row: every recording is kept in Recent logs automatically; Export…
        # is how one gets out (to Datalogs or anywhere), 📁 opens Datalogs.
        from .widgets import ElidedLabel
        self.name_preview = ElidedLabel("")  # the log being recorded, or the last one
        self.name_preview.setObjectName("muted")
        self.name_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.recent_button = T.MenuIconButton(
            "history", f"Recent logs: every recording is kept here automatically (the last "
            f"{recent_logs.KEEP}, and all from the last {recent_logs.KEEP_DAYS} days). Open, export "
            "or discard them.", self._fill_recent)
        self.export_button = T.icon_button("ios_share", "Export…: save the last log to Datalogs "
                                           "(or anywhere) as Standard CSV, Integrated Engineering, "
                                           "MegaLogViewer or generic CSV")
        self.export_button.clicked.connect(lambda: self._export(self.last_log))
        self.folder_button = T.icon_button("folder_open", "Open the Datalogs folder (where exports go)")
        self.folder_button.clicked.connect(self._open_datalogs)
        mode_row.addWidget(self.name_preview, 1)
        for b in (self.recent_button, self.export_button, self.folder_button):
            mode_row.addWidget(b)
        cl.addLayout(mode_row)
        self._load_recording()
        for w in (self.mode, self.trigger_op):
            w.currentIndexChanged.connect(self._recording_changed)
        self.mode.currentIndexChanged.connect(self._mode_switched)
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
        family = self.state.family
        module = family["module"] if family and family.get("can_log") else None
        if module != self.catalog_module and not self.running:
            self.catalog_module = module
            self.catalog = []
            self.raw_groups = set()
            self._build_tree()
            if module is not None:
                self.state.offline("catalog", {"module": module}, self._catalog_loaded,
                                   lambda e: self.banner.show_message(self.error_text(e), "error"))
        idle = not self.state.busy or self.running
        self.start_button.setEnabled(bool(module is not None and self.state.adapter) and idle)
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
        self.presets.setEnabled(not self.running and self.state.family is not None)
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
        return f"logger:{self.state.family['name']}" if self.state.family else None

    def _catalog_loaded(self, values):
        self.catalog = values or []
        saved = self.state.settings.get(self._settings_key()) or {}
        self.raw_groups = set(saved.get("raw_groups", []))
        self._build_tree(set(saved.get("values", [])))
        if not self.catalog:
            self.summary.setText("No named values for this module in this flasher build. "
                                 "Add whole groups below.")

    def _build_tree(self, checked=()):
        """Every named value as a row (known by its group.field key), plus each raw group as an
        always-on row; the ticked ones show with a colour, the reading now and a range bar."""
        self.tree.blockSignals(True)
        self.tree.clear()
        order = []
        for value in self.catalog:
            key = f"{value['group']}.{value['field']}"
            # Unit beside the name, as Analyze's list ("Engine speed (rpm)"); Now is just the number
            shown = unitconv.conversion(value.get("unit") or "", self.state.settings)[0]
            item = self.tree.new_item(f"{value['name']} ({shown})" if shown else value["name"], key)
            item.setData(0, VALUE_ROLE, value)
            item.setData(0, CATEGORY_ROLE, value.get("category") or "Other")
            item.setToolTip(0, channel_info.tooltip(value))
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

    def _preset_loaded(self, name, builtin):
        """The Integrated Engineering Logger preset also makes Export… start on Integrated
        Engineering CSV (it remembers the format you pick there). The Dyno preset picks Dyno mode."""
        if builtin and name == "Dyno" and self.mode.currentData() != "dyno":
            self.mode.setCurrentIndex(self.mode.findData("dyno"))
            self._dyno_setup()
            return
        if builtin and name == "Integrated Engineering Logger" and \
                self.state.settings.get("export_format") != "ie":
            self.state.settings.set("export_format", "ie")
            self.banner.show_message("Export… will now start on Integrated Engineering CSV.", "info")

    def _rates_key(self):
        return f"logger_rates:{self.state.family['name']}" if self.state.family else None

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
        choices("Rate", "speed", RATES, self.rate.currentData(),
                lambda v: self.rate.setCurrentIndex(max(self.rate.findData(v), 0)),
                "How often each group is read")
        T.menu_action(menu, "Group rates…", "tune", self._edit_rates, idle,
                      tip="Read some groups faster than others")
        T.menu_header(menu, "Rolling")
        lengths = [(f"{m:g} min", float(m)) for m in (1, 2, 5, 10, 15, 30, 60)]
        if all(v != self.keep_min.value() for _l, v in lengths):  # an older custom length
            lengths.append((f"{self.keep_min.value():g} min", self.keep_min.value()))
        choices("Keep last", "autorenew", lengths, self.keep_min.value(), self.keep_min.setValue,
                "Rolling recording keeps this much, saved when you press Stop")
        T.menu_header(menu, "Saving")
        # Every log is kept in Recent logs (Standard format, the app's own copy). Auto-export
        # also puts a copy in your format into Datalogs at every Stop, without the dialog.
        choices("Auto-export", "ios_share",
                [("Off", None)] + [(label, key) for key, label, _ext, _f in log_export.FORMATS],
                self._auto_export_format(),
                lambda v: self.state.settings.set("logger:auto_export", v),
                "When you stop, also save a copy in this format to Datalogs (no dialog). "
                "The log is always kept in Recent logs too.", enabled=True)

    # --- recent logs and export ---

    def _auto_export_format(self):
        fmt = self.state.settings.get("logger:auto_export")
        return fmt if any(f[0] == fmt for f in log_export.FORMATS) else None

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
            self.export_button.setEnabled(False)
            return
        self.name_preview.setText(prefix + name)
        self.name_preview.setToolTip(f"{prefix}{name}\nKept in Recent logs; Export… to keep a copy.")
        self.export_button.setEnabled(not self.running)

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
        note = menu.addAction(f"Kept: the last {recent_logs.KEEP} and all from the last "
                              f"{recent_logs.KEEP_DAYS} days")
        note.setEnabled(False)

    def _analyze(self, path, view=None):
        self.state.open_in_playback.emit(str(path))
        if view:
            page = getattr(self.window(), "pages", {}).get("playback")
            if page is not None and hasattr(page, "show_view"):
                page.show_view(view)

    # --- Dyno mode ---

    def _before_dyno_key(self):
        return f"logger:before_dyno:{self.state.family['name']}" if self.state.family else None

    def _mode_switched(self, _index=None):
        """Leaving Dyno mode: put back what was ticked before it."""
        key = self._before_dyno_key()
        if self.running or not key or self.mode.currentData() == "dyno":
            return
        # The groups Dyno slowed down (air pressure, outside temperature) go back to their rates
        # (unless you changed them since).
        rkey = key.replace("before_dyno", "dyno_rates")
        changed = self.state.settings.get(rkey)
        if changed is None and self.state.settings.get(key) is not None:  # set up before this was kept
            changed = {str(v["group"]): 0 for v in self.catalog or () if v["id"] in DYNO_SLOW}
        if changed and self._rates_key():
            rates = self._group_rates()
            for g, ms in changed.items():
                if rates.get(g) == DYNO_SLOW_MS:
                    if ms:
                        rates[g] = ms
                    else:
                        rates.pop(g, None)
            self.state.settings.set(self._rates_key(), rates)
            self.state.settings.set(rkey, None)
            self._selection_changed()
        before = self.state.settings.get(key)
        if before is None:
            return
        self.state.settings.set(key, None)
        self.raw_groups = set(before.get("raw_groups", []))
        self._build_tree(set(before.get("values", [])))
        self._selection_changed()

    def _dyno_setup(self):
        """Dyno picked: switch to the Dyno preset (only what the dyno needs; what was ticked comes
        back when you leave Dyno mode) and read the slow values (air pressure, outside
        temperature) only now and then, so engine speed is read as often as possible."""
        if self.running or not self.catalog:
            return
        have = {v["id"] for v in self.catalog}
        wanted = [i for i in DYNO_VALUES if i in have]
        if not any(i in have for i in ("engine.pedal_position", "engine.throttle_position")) or \
                "engine.rpm" not in have or "vehicle.speed" not in have:
            self.banner.show_message("This module doesn't report what the dyno needs (engine speed, vehicle "
                                     "speed and the pedal). Pick the engine on the Connect page.", "warning")
            return
        if "engine.pedal_position" in have:
            wanted = [i for i in wanted if i != "engine.throttle_position"]
        before = {v["id"] for v in self._checked_values()}
        key = self._before_dyno_key()
        if key and (before != set(wanted) or self.raw_groups) and self.state.settings.get(key) is None:
            self.state.settings.set(key, {"values": sorted(before), "raw_groups": sorted(self.raw_groups)})
        self.raw_groups = set()
        self._build_tree(set(wanted))
        self._selection_changed()
        slow = {v["group"] for v in self.catalog if v["id"] in DYNO_SLOW}
        fast = {v["group"] for v in self.catalog if v["id"] in wanted and v["id"] not in DYNO_SLOW}
        rates = self._group_rates()
        rkey = key.replace("before_dyno", "dyno_rates") if key else None
        changed = dict(self.state.settings.get(rkey) or {}) if rkey else {}
        for g in slow - fast:
            changed.setdefault(str(g), rates.get(str(g), 0))  # what it was, put back on leaving Dyno
            rates[str(g)] = DYNO_SLOW_MS
        if rkey:
            self.state.settings.set(rkey, changed)
        if self._rates_key():
            self.state.settings.set(self._rates_key(), rates)
        self._selection_changed()
        tip = " What you had ticked comes back when you pick another mode." \
            if key and self.state.settings.get(key) is not None else ""
        self.banner.show_message(
            "Dyno: switched to the Dyno preset: engine speed, vehicle speed and the pedal (air pressure and "
            "temperature read every few seconds). Press Record, then floor it in one gear (3rd is ideal) from low rpm "
            "to near redline on a flat, empty road. Each pull is saved; a run each way cancels slope and "
            "wind." + tip, "info")
        self._auto_car()

    def _follow_in_analyze(self, path):
        """A Dyno recording: Analyze follows it on its Dyno view straight away (without leaving the
        Logger), so opening Analyze shows horsepower and torque as each pull is recorded."""
        window = self.window()
        if not hasattr(window, "_ensure_page"):
            return
        try:
            page, _new = window._ensure_page("playback")
        except KeyError:  # Analyze turned off in Settings → Features
            return
        page.follow(Path(path))
        if hasattr(page, "show_view"):
            page.show_view("dyno")
            page.dyno.draw()

    def _open_dyno_settings(self, anchor):
        if self.running:
            return
        self._fill_dyno_panel()
        self.dyno_panel.open_at(anchor, align="left")

    def _build_dyno_panel(self):
        """Dyno's setup, dropping down from its button: the car the numbers are worked out for."""
        from PySide6.QtWidgets import QLabel
        from .widgets import PopupCard
        panel = PopupCard(self, min_width=400)
        box = QVBoxLayout(panel.body)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        heading = QLabel("DYNO")
        heading.setObjectName("optionsHeading")
        box.addWidget(heading)
        self.dyno_car_label = QLabel("")
        self.dyno_car_label.setWordWrap(True)
        self.dyno_car_label.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.dyno_car_label)
        row = QHBoxLayout()
        edit = QPushButton("Car and weight…")
        edit.setToolTip("Weight, drag, tyres and drivetrain loss for this car, saved per VIN. "
                        "Fill in with AI is in there too.")
        edit.clicked.connect(lambda: (panel.close(), self._edit_dyno_car()))
        row.addWidget(edit)
        row.addStretch(1)
        box.addLayout(row)
        note = muted(f"Records each pull: pedal over {DYNO_PEDAL:.0f} %, {DYNO_PRE_S:.0f} s before, "
                     f"{DYNO_POST_S:.0f} s after. Analyze's Dyno view shows the result live while recording "
                     "and afterwards.")
        note.setWordWrap(True)
        box.addWidget(note)
        return panel

    def _fill_dyno_panel(self):
        from . import dyno as D
        from . import theme
        from .dyno_view import imperial, load_car
        from .vehicle import decode
        if not hasattr(self, "dyno_panel"):
            self.dyno_panel = self._build_dyno_panel()
        vin = self.state.vin()
        car = load_car(self.state.settings, vin, self.state)
        saved = (self.state.settings.get("dyno") or {}).get("cars") or {}
        imp = imperial(self.state.settings)
        mass = car["curb_kg"] + car["extra_kg"]
        weight = f"{mass * D.KG_LB:,.0f} lb" if imp else f"{mass:,.0f} kg"
        name = car.get("label") or (decode(vin).get("label") if vin else "") or "This car"
        source = {"you": "set by you", "ai": "filled in by AI", "built-in": "typical for this model"}.get(
            car.get("source"), "set by you")
        if getattr(self, "_car_req", None) is not None:
            source += " · asking AI for this exact car…"
        muted = theme.colors()["muted"]
        if (vin or "default") in saved or "default" in saved or car.get("source") == "built-in":
            self.dyno_car_label.setText(f"<b>{name}</b> <span style='color:{muted}'>({source})</span><br>{weight} with "
                                        f"driver · Cd {car['cd']:.2f} × {car['area_m2']:.2f} m² · {car['tyre']} · "
                                        f"loss {car['loss_pct']:.0f} %")
        else:
            self.dyno_car_label.setText(f"<b>{name}</b><br>Not set yet: using a typical Golf GTI ({weight}). "
                                        "Set the real weight; it matters most.")

    def _edit_dyno_car(self):
        from .dyno_view import CarDialog, load_car, save_car
        vin = self.state.vin()
        dlg = CarDialog(self.state, vin, load_car(self.state.settings, vin, self.state), self)
        if dlg.exec():
            save_car(self.state.settings, vin, dict(dlg.values(), source="you"))  # yours: never auto-replaced
            page = getattr(self.window(), "pages", {}).get("playback")
            if page is not None and hasattr(page, "dyno"):
                page.dyno.car_changed()
            self.banner.show_message("Car saved. The Dyno view uses it for every pull of this car.", "ok")

    def _auto_car(self):
        """Fill in the car by itself: typical figures from the VIN and the scan at once, then the AI
        for this exact car in the background. What you save yourself is never replaced."""
        from . import ai
        from .dyno_view import AI_SYSTEM, ai_prompt, apply_ai, guess_car, load_car, parse_ai, save_car, scan_facts
        settings = self.state.settings
        vin = self.state.vin()
        if not vin:
            return
        saved = ((settings.get("dyno") or {}).get("cars") or {}).get(vin) or {}
        if saved.get("source") in ("you", "ai") or (saved and "source" not in saved):
            return  # yours (or already the AI's)
        guess = guess_car(vin, scan_facts(self.state))
        if guess and not saved:
            save_car(settings, vin, guess)
            self._car_changed()
        if not ai.enabled(settings) or ai.resolved(settings)[1] == "chat" or getattr(self, "_car_req", None):
            return
        prompt = ai_prompt(self.state, vin, load_car(settings, vin, self.state))
        if not ai.confirm_send(self, settings, AI_SYSTEM + "\n\n" + prompt):
            return
        reply = []
        req = self._car_req = ai.Ask(settings, AI_SYSTEM, [{"role": "user", "content": prompt}], "low", self)

        def done():
            self._car_req = None
            try:
                data = parse_ai("".join(reply))
            except ValueError:
                self._car_changed()
                return
            current = ((settings.get("dyno") or {}).get("cars") or {}).get(vin) or {}
            if current.get("source") == "you":  # you saved your own while it was asking
                self._car_changed()
                return
            car, filled = apply_ai(load_car(settings, vin, self.state), data)
            if filled:
                save_car(settings, vin, dict(car, source="ai"))
                self.banner.show_message(f"Dyno car filled in by AI: {car.get('label') or 'this car'}. "
                                         "Change anything in Dyno ▾ → Car and weight…", "ok")
            self._car_changed()

        def failed(error):
            self._car_req = None
            self._car_changed()

        req.text.connect(reply.append)
        req.done.connect(done)
        req.failed.connect(failed)
        req.start()
        self._car_changed()

    def _car_changed(self):
        """The car profile changed: the Dyno ▾ panel and Analyze's Dyno view follow."""
        if hasattr(self, "dyno_panel"):
            self._fill_dyno_panel()
        page = getattr(self.window(), "pages", {}).get("playback")
        if page is not None and hasattr(page, "dyno"):
            page.dyno.car_changed()

    def _fewest_groups(self, values, groups):
        """Groups to read, leaving out any whose values all sit in other groups read at full rate
        (Dyno: engine speed comes with vehicle speed and the pedal, so group 1 isn't needed).
        Those values are then taken from the other groups (self.aliases)."""
        rates = self._group_rates()
        groups = list(groups)
        for g in sorted(groups):
            if g in self.raw_groups:
                continue
            others = [x for x in groups if x != g and not rates.get(str(x))]
            mine = [v for v in values if v["group"] == g]
            if mine and all(any(lg in others for lg, _f in (v.get("locations") or [])) for v in mine):
                groups.remove(g)
        return groups

    def _dyno_trigger_key(self, values):
        for wanted in ("engine.pedal_position", "engine.throttle_position"):
            v = next((v for v in values if v["id"] == wanted), None)
            if v is not None:
                return f"{v['group']}.{v['field']}"
        return None

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
                      "Pick Continuous, Triggered, Rolling or Dyno below.")
            if mode != "dyno" else "Record dyno pulls: each full-throttle stretch is saved (pedal over "
                                   f"{DYNO_PEDAL:.0f} %, {DYNO_PRE_S:.0f} s before, {DYNO_POST_S:.0f} s after).")

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
        values = self._checked_values()
        groups = sorted({v["group"] for v in values} | self.raw_groups)
        if not groups:
            self.banner.show_message("Tick at least one value, or add a group.", "warning")
            return
        if self.mode.currentData() == "dyno":
            groups = self._fewest_groups(values, groups)
        self.columns = [(f"{v['group']}.{v['field']}", v["name"]) for v in values]
        self._preview(values)
        # Many values (engine speed above all) sit in several groups. When another group being
        # read anyway carries one, its reading is used too: more readings at no extra cost
        # (engine speed several times per round, which the dyno's curve needs).
        self.aliases = {}
        self.alias_units = {}
        slow = {int(g) for g, ms in self._group_rates().items() if ms}
        for v in values:
            for g, f in (v.get("locations") or [])[1:]:
                key = f"{g}.{f}"
                if g in groups and g not in slow and g not in self.raw_groups and key not in self.logged:
                    self.aliases.setdefault(key, f"{v['group']}.{v['field']}")
                    self.alias_units.setdefault(key, (v.get("unit") or "").strip())
        module = self.state.family or {}
        mode = self.mode.currentData()
        # Recorded into Recent logs; the car's VIN goes in its index entry (exports and Analyze
        # find it there), so the name stays short: engine_2026-11-09_073012.csv
        path = recent_logs.new_log(module.get("name", ""), vin=self.state.vin(), mode=mode)
        trigger = None
        pre_s, post_s = self.pre_s.value(), self.post_s.value()
        if mode == "dyno":
            key = self._dyno_trigger_key(values)
            if key is None:
                self.banner.show_message("Dyno needs the accelerator pedal (or throttle) ticked: press "
                                         "Dyno again to tick what it needs.", "warning")
                return

            def floored(latest, key=key):
                value = latest.get(key)
                return isinstance(value, (int, float)) and value > DYNO_PEDAL
            trigger, pre_s, post_s = floored, DYNO_PRE_S, DYNO_POST_S
        elif mode == "trigger":
            key = self.trigger_channel.currentData()
            if not key:
                self.banner.show_message("Pick the channel that starts a triggered log.", "warning")
                return
            limit, above = self.trigger_value.value(), self.trigger_op.currentText() == ">"

            def crossed(latest, key=key, limit=limit, above=above):
                value = latest.get(key)
                return isinstance(value, (int, float)) and (value > limit if above else value < limit)
            trigger = crossed
        self.writer = DatazapWriter(path, self.columns, groups, mode="trigger" if mode == "dyno" else mode,
                                    keep_s=self.keep_min.value() * 60, trigger=trigger,
                                    pre_s=pre_s, post_s=post_s)
        self.writer.dyno = mode == "dyno"
        self.samples = self.errors = 0
        self.stats = {}
        rates = self._group_rates()
        self.periods = {g: (rates.get(str(g)) or self.rate.currentData() or 0) / 1000 for g in groups}
        self.started = time.time()
        self.banner.show_message(f"Recording {path.name}. Press Stop when you're done.", "info")
        rates = {g: ms for g, ms in self._group_rates().items() if int(g) in groups}
        self.running = self.state.run(
            "log", {"groups": groups, "interval_ms": self.rate.currentData(), "group_periods": rates},
            self._finished, self._failed)
        if self.running and mode == "dyno":
            self._follow_in_analyze(path)
            self._auto_car()
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
        family = self.state.family or {}
        context = (f"Module: {family.get('label', '?')}, part {self.state.part() or '?'}\n"
                   f"Logger {'recording' if self.running else 'stopped'}: {self.status.fullText()}\n"
                   "Values (reading now, then min/max/avg since Start; units in brackets):\n"
                   + "\n".join(rows))
        return {"title": "Logger", "context": context,
                "question": "Explain what these logged values say about the car: what's normal, what looks "
                            "off, and what to check or log next."}

    def _watch(self):
        """Watch in Analyze: Analyze opens the recording and follows it as rows are written."""
        if self.running and self.writer:
            self.state.follow_log.emit(str(self.writer.path))
            if getattr(self.writer, "dyno", False):  # a Dyno recording: straight to the Dyno view
                page = getattr(self.window(), "pages", {}).get("playback")
                if page is not None and hasattr(page, "show_view"):
                    page.show_view("dyno")

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
            alias = getattr(self, "aliases", {}).get(key)
            if alias is not None:
                unit = (field.get("unit") or "").strip()
                main_unit = self.writer.units.get(alias)
                expected = self.alias_units.get(key, "")
                if (main_unit is not None and unit != main_unit) or (expected and unit != expected):
                    continue  # not the same reading there (other unit): don't mix the two
                key = alias
            if group in self.raw_groups and key not in self.logged and not self.writer.ready:
                self._drop_raw_placeholder(group)
                self.columns.append((key, f"Group {group} field {field['index']}"))
                self._add_raw_field(key, self.columns[-1][1], group)
            if key in self.logged:
                self.seen_at[key] = time.time()
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
                    st = self.stats.get(key)
                    if st is None:
                        self.stats[key] = [number, number, number, 1]
                    else:
                        st[0] = min(st[0], number)
                        st[1] = max(st[1], number)
                        st[2] += number
                        st[3] += 1
                units[key] = field.get("unit", "")
        self.writer.update(group, values, units, sample.get("wall_time", time.time()) - self.started)

    def _paint(self):
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
                                     + (f" The {rows} rows read before that are in Recent logs." if rows else "")
                                     + note, "error")
        elif rows:
            self.last_log = path = writer.path
            secs = writer.last_t - writer.first_t
            note, written = self._do_auto_export(path)
            links = {"Open in Analyze": lambda: self._analyze(path)}
            if getattr(writer, "dyno", False):
                links = {"Open Dyno": lambda: self._analyze(path, view="dyno"), **links}
            if written is not None:
                from .widgets import reveal_file
                links["Show in Finder" if sys.platform == "darwin" else "Show in folder"] = \
                    lambda: reveal_file(written)
            else:
                links["Export…"] = lambda: self._export(path)
            links["Discard…"] = lambda: self._discard(path)
            self.banner.show_message(
                f"Logged {rows:,} rows ({int(secs // 60)}:{int(secs % 60):02d}), kept in Recent logs."
                + (note or " Min / max / avg are in the list."),
                "error" if note and written is None else "ok", links)
        elif writer and getattr(writer, "dyno", False) and writer.ready:
            self.banner.show_message("Stopped. No full-throttle pull happened (pedal over "
                                     f"{DYNO_PEDAL:.0f} %), so nothing was recorded.", "warning")
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
                                 + (f" The {rows} rows read before that are in Recent logs." if rows else ""),
                                 "error")
        self._show_name()
