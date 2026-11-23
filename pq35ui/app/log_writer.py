"""Writing a datalog CSV (Datazap / Standard format) as readings arrive: Continuous, Rolling (keep
the last few minutes) or Triggered (rows around moments a condition is true). Used by the Logger
and the dyno recorder. No Qt: tested directly (tests/test_log_writer.py)."""
import collections
import csv


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
        except Exception:  # the user's trigger condition on live values (None, divide by 0 ...): not met
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
        except Exception:  # the recording itself must never fail over its companion file (logged)
            import logging
            logging.getLogger("Logger").exception("Couldn't save the log's companion file")
