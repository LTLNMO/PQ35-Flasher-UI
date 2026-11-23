"""Smart presets for Analyze, done the way Datazap does them: ready-made analysis layouts
(a name, a one-line description, and chart rows of channels) generated for each log and shown
as a row of chips above the graph. Click one: its channels go on the graph, one Split row per
chart (or the Single graph when it's one row).

Where they come from:
  * The AI the user chose in Settings → AI, automatically the first time a set of channels is
    seen (Settings → Data → Smart presets turns that off). Only the channel names are sent,
    never the logged values. Its answer is checked against the log: names it made up are dropped.
  * Built-in rules otherwise (AI off, not approved yet, offline, or it failed), so there are
    always some.
Results are cached by the log's channel names (without units), so another log with the same
channels, from the same car in °C or °F, gets them instantly without asking the AI again."""
import hashlib
import json
import re

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QSizePolicy, QWidget

from . import log_export, theme

AI_KEY = "smart_presets:ai"          # generate with AI automatically (default on)
BAR_KEY = "playback:smart_bar"       # show the chip row (default off: see bar_on)
BAR_RESET_KEY = "playback:smart_bar_off_once"
CACHE_FILE = "smart_presets.json"
CACHE_VERSION = 2  # 2: stricter prompt + unit check (older AI answers are asked again)

def bar_on(settings):
    """Whether Analyze shows the chip row. Off unless switched on (✨ or Settings → Data): the row
    pushed the graph below where every other page's content starts (user 2026-11-23). Switched off
    once for everyone who had it on from the old default."""
    if not settings.get(BAR_RESET_KEY):
        settings.set(BAR_RESET_KEY, True)
        settings.set(BAR_KEY, False)
    return bool(settings.get(BAR_KEY, False))


# ── built-in layouts: (name, description, rows); a row = (include patterns, exclude patterns) ──
RULES = (
    ("Boost control", "Boost vs target, N75 duty, load",
     ((r"boost pressure|boost \(|actual boost|requested boost|specified boost", r"control"),
      (r"n75|wastegate|boost control valve", ()), (r"\bload\b", r"corrected"))),
    ("Timing and knock", "Ignition timing and retard per cylinder",
     ((r"ignition timing|ignition angle", r"retard|correct"), (r"retard|ignition correction", ()),
      (r"knock sensor", ()))),
    ("Fuel pressure", "Rail pressure vs target, pump, injection",
     ((r"rail", ()), (r"fuel pump|lpfp", ()), (r"inject", ()))),
    ("Mixture", "Lambda vs target, lambda control and trims",
     ((r"lambda|\bafr\b", r"control|adapt|temp"), (r"lambda control|trim|adapt", r"temp"))),
    ("Airflow and torque", "Mass air flow, torque and load",
     ((r"\bmaf\b|mass air|air mass|airflow", ()), (r"torque|engine moment", r"transmission|trans\b"),
      (r"\bload\b", r"corrected"))),
    ("Temperatures", "Coolant, intake air, oil and fuel temperatures",
     ((r"temp", r"lambda|exhaust"),)),
    ("Driving", "Vehicle speed, gear, accelerator and throttle",
     ((r"vehicle speed|road speed", ()), (r"\bgear\b", ()), (r"accelerat|pedal|throttle", r"specified"))),
)
_RPM = re.compile(r"engine speed|\brpm\b", re.I)


def _matches(name, include, exclude):
    return re.search(include, name, re.I) and not (exclude and re.search(exclude, name, re.I))


def rule_layouts(headers):
    """Built-in layouts for these channels: [{name, description, charts:[{title, channels}]}]."""
    rpm = [h for h in headers if _RPM.search(h)][:1]
    out = []
    for name, desc, rows in RULES:
        used, charts = set(rpm), []
        for include, exclude in rows:
            found = [h for h in headers if h not in used and _matches(h, include, exclude)][:8]
            used.update(found)
            charts += [part[:8] for part in _split_units(found)]
        if sum(len(c) for c in charts) >= 2:
            charts = ([rpm] if rpm else []) + charts
            out.append({"name": name, "description": desc,
                        "charts": [{"title": log_export.split_header(c[0])[0], "channels": c} for c in charts]})
    return out


# ── cache, by channel names without units ──

def _base(name):
    return log_export.split_header(name)[0].strip().lower()


def signature(headers):
    names = sorted({_base(h) for h in headers if h})
    return hashlib.sha1("\n".join(names).encode()).hexdigest()[:16]


def _cache_path():
    from .storage import data_dir
    return data_dir() / CACHE_FILE


def _read_cache():
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if data.get("version") == CACHE_VERSION else {"version": CACHE_VERSION, "logs": {}}
    except Exception:
        return {"version": CACHE_VERSION, "logs": {}}


def cached(headers):
    """AI layouts saved for this set of channels (by base name), or None."""
    entry = _read_cache()["logs"].get(signature(headers))
    return resolve(entry["layouts"], headers) if entry else None


def store(headers, layouts):
    data = _read_cache()
    data["logs"][signature(headers)] = {
        "layouts": [{"name": l["name"], "description": l.get("description", ""),
                     "charts": [{"title": c.get("title", ""), "channels": [_base(n) for n in c["channels"]]}
                                for c in l["charts"]]} for l in layouts]}
    try:
        _cache_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def forget(headers):
    data = _read_cache()
    if data["logs"].pop(signature(headers), None) is not None:
        try:
            _cache_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


# ── checking layouts against the log ──

def _lookup(headers):
    exact = {h: h for h in headers}
    lower = {h.lower(): h for h in headers}
    base = {}
    for h in headers:
        base.setdefault(_base(h), h)

    def find(name):
        # Exact, any case, the name without its unit ("engine load (actual)" for "Engine load
        # (actual) (%)": a bracket in a name can't be told from a unit), then unit stripped too.
        name = str(name).strip()
        return (exact.get(name) or lower.get(name.lower()) or base.get(name.lower())
                or base.get(_base(name)))
    return find


def resolve(layouts, headers):
    """Layouts with every channel matched to this log's headers; unknown ones dropped, and
    layouts or charts left empty removed."""
    find = _lookup(headers)
    out, layouts_seen = [], set()
    for l in layouts or []:
        if not isinstance(l, dict) or not l.get("name"):
            continue
        charts, seen = [], set()
        for c in l.get("charts") or []:
            chans = []
            for n in (c.get("channels") or []) if isinstance(c, dict) else []:
                h = find(n)
                if h and h not in seen:
                    seen.add(h)
                    chans.append(h)
            parts = _split_units(chans[:8])
            for part in parts:  # split by unit: each new chart is named after its first channel
                title = str(c.get("title") or "") if len(parts) == 1 else log_export.split_header(part[0])[0]
                charts.append({"title": title, "channels": part})
        sig = frozenset(seen)
        if sum(len(c["channels"]) for c in charts) >= 2 and sig not in layouts_seen:
            layouts_seen.add(sig)
            out.append({"name": str(l["name"]).replace("_", " ").strip()[:40],
                        "description": str(l.get("description") or "").strip()[:90],
                        "charts": charts[:6]})
    return out[:8]


def _stem(name):
    """First two words of a channel's name: 'fuel rail' for 'Fuel Rail Actual (mbar)'."""
    return " ".join(re.findall(r"[a-z0-9]+", _base(name))[:2])


def _split_units(chans):
    """A chart's channels, split into one chart per unit when they mix units, except channels
    of the same thing ('Fuel Rail Actual (mbar)' + 'Fuel Rail Spec (%)': IE's labels can be wrong)."""
    groups = {}
    for h in chans:
        unit = log_export.split_header(h)[1].strip().lower()
        key = next((k for k, g in groups.items() if k == unit or any(_stem(h) == _stem(x) for x in g)), unit)
        groups.setdefault(key, []).append(h)
    return list(groups.values())


# ── asking the AI ──

SYSTEM = (
    "You design analysis views for engine datalogs in a VW/Audi (PQ35 platform: 2.0 TFSI, 1.8T, "
    "1.4 TSI) ECU tuning app, the way an experienced tuner lays out Datazap. Reply with JSON only: "
    "no prose, no code fences.")

RULES_TEXT = """Rules:
1. Each layout answers one question a tuner asks of a log. Good ones, when the channels exist:
   - Boost control: boost actual + specified (one chart), N75/wastegate duty, load actual + specified.
   - Timing and knock: ignition timing; timing retard / ignition correction for every cylinder (one chart).
   - Fuel pressure: rail pressure actual + specified (one chart); fuel pump duty / LPFP; injection time.
   - Mixture: lambda actual + specified (one chart); lambda control / fuel trims (another chart).
   - Airflow and torque: mass air flow; engine torque actual (+ requested); load.
   - Temperatures: coolant, intake air, oil, fuel temperatures (one chart, all the same unit).
   - Driveability: engine speed, vehicle speed, gear, throttle / accelerator pedal.
2. Every chart holds ONE kind of quantity in ONE unit. Always pair actual with specified/requested of
   the same thing on one chart. Never put different quantities on one chart (no lambda with pump duty,
   no throttle with N75 duty, no knock sensor volts with retard degrees).
3. A chart's title names exactly what is on it, in 1 to 3 words.
4. Start each pull-analysis layout (boost, timing, fuel, mixture, airflow) with a chart holding only
   engine speed, so the rpm is always in view. That chart counts toward the 4.
5. 2 to 4 charts per layout, 1 to 4 channels per chart (up to 8 for per-cylinder channels).
6. Copy channel names exactly from the list. Skip a layout if the log lacks its main channels;
   never pad a layout with unrelated channels.
7. Integrated Engineering logs can carry wrong units in brackets (e.g. "LPFP Duty (mbar)"): judge a
   channel by its name, not its unit."""


def prompt(headers):
    names = "\n".join(f"- {h}" for h in headers)
    return (
        "These are the channels in a datalog:\n" + names + "\n\n"
        "Make 4 to 7 analysis layouts for this log. Each layout is a stack of charts on a shared time "
        "axis.\n\n" + RULES_TEXT + "\n\n"
        'Answer as: {"layouts": [{"name": "Boost control", "description": "Boost vs target, N75 duty, load", '
        '"charts": [{"title": "Engine speed", "channels": ["Engine speed (rpm)"]}, '
        '{"title": "Boost", "channels": ["exact actual name", "exact specified name"]}]}]}\n'
        "name: at most 3 words. description: at most 60 characters, listing what's shown.")


def parse(text):
    """The layouts in the AI's reply (tolerates code fences or words around the JSON)."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in the reply")
    data = json.JSONDecoder().raw_decode(text[start:])[0]
    if isinstance(data, dict):
        data = data.get("layouts") or []
    if not isinstance(data, list):
        raise ValueError("no layouts in the reply")
    return data


class Generator(QObject):
    """Smart presets for one log: built-in ones at once, then the AI's (cached, or asked for
    in the background). Signals arrive on the UI thread."""
    changed = Signal()      # layouts / busy / source changed

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.layouts, self.source, self.busy, self.error = [], "", False, ""
        self.headers = []
        self._req = None
        self._reply = ""

    def ai_wanted(self):
        return bool(self.settings.get(AI_KEY, True))

    def ai_ready(self):
        """AI on, and allowed to send without asking (approved provider, or a local one)."""
        from . import ai
        if not ai.enabled(self.settings):
            return False
        c = ai.config(self.settings)
        return c["provider"] in c["approved"] or c["provider"] in ("ollama", "lmstudio")

    def needs_approval(self):
        from . import ai
        return self.ai_wanted() and ai.enabled(self.settings) and not self.ai_ready()

    def load(self, headers):
        """A log was opened: show its presets (cached AI ones, else built-in, then ask the AI)."""
        self.cancel()
        self.headers = [h for h in headers if h]
        self.error = ""
        found = cached(self.headers)
        if found:
            self.layouts, self.source = found, "ai"
        else:
            self.layouts, self.source = rule_layouts(self.headers), "rules"
            if self.ai_wanted() and self.ai_ready():
                self.generate()
                return
        self.changed.emit()

    def generate(self, parent=None):
        """Ask the AI now (Regenerate, or the first time for these channels)."""
        from . import ai
        if not self.headers or not ai.enabled(self.settings):
            return
        if parent is not None and not ai.confirm_send(parent, self.settings, SYSTEM + "\n\n" + prompt(self.headers)):
            return
        self.cancel()
        self.busy, self.error, self._reply = True, "", ""
        self.changed.emit()
        self._req = ai.Ask(self.settings, SYSTEM, [{"role": "user", "content": prompt(self.headers)}], "medium", self)
        self._req.text.connect(self._chunk)
        self._req.done.connect(self._done)
        self._req.failed.connect(self._failed)
        self._req.start()

    def cancel(self):
        if self._req is not None:
            for sig in (self._req.text, self._req.done, self._req.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._req.stop()
            self._req = None
        self.busy = False

    def _chunk(self, t):
        self._reply += t

    def _done(self):
        self._req, self.busy = None, False
        try:
            layouts = resolve(parse(self._reply), self.headers)
        except Exception as e:
            layouts, self.error = [], f"Couldn't read the AI's answer ({e})."
        if layouts:
            store(self.headers, layouts)
            self.layouts, self.source = layouts, "ai"
        elif not self.error:
            self.error = "The AI didn't suggest any layouts for these channels."
        self.changed.emit()

    def _failed(self, msg):
        self._req, self.busy, self.error = None, False, msg
        self.changed.emit()


# ── the chip row ──

def icon_for(name):
    """A Material icon for a preset, from words in its name (like Datazap's)."""
    t = name.lower()
    for words, glyph in ((("thermal", "temp", "coolant", "oil", "heat"), "thermostat"),
                         (("boost", "turbo", "wastegate", "n75"), "speed"),
                         (("health", "overview", "summary"), "monitor_heart"),
                         (("fuel", "inject", "lambda", "afr", "trim"), "local_gas_station"),
                         (("power", "torque", "delivery", "pull"), "bolt"),
                         (("knock", "detonation"), "warning"),
                         (("timing", "ignition", "spark"), "electric_bolt"),
                         (("traction", "wheel", "slip", "vehicle", "driv", "speed"), "directions_car"),
                         (("transmission", "clutch", "gear", "shift"), "settings"),
                         (("cam", "valve", "vanos"), "sync"),
                         (("air", "intake", "maf", "map"), "air")):
        if any(w in t for w in words):
            return glyph
    return "auto_awesome"


CHIP_H = 40


class _Chip(QWidget):
    """Icon + name over a one-line description, in a rounded card; hover brightens it."""
    clicked = Signal()

    def __init__(self, glyph, name, description, parent=None):
        super().__init__(parent)
        self.glyph, self.name, self.description = glyph, name, description
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _badge_font(self):
        f = self.font()
        f.setPointSizeF(max(theme.font_size() - 2, 9))
        f.setWeight(f.Weight.DemiBold)
        return f

    def _small(self):
        f = self.font()
        f.setPointSizeF(max(f.pointSizeF() - 2.5, 8))
        return f

    def sizeHint(self):
        from PySide6.QtGui import QFontMetrics
        if theme.is_mac():  # mockup .badge: 11 pt semibold, 2 px 7 px, radius 5, white .1
            return QSize(QFontMetrics(self._badge_font()).horizontalAdvance(self.name) + 14, 20)
        bold = self.font()
        bold.setBold(True)
        w = max(QFontMetrics(bold).horizontalAdvance(self.name),
                min(QFontMetrics(self._small()).horizontalAdvance(self.description), 200))
        h = QFontMetrics(bold).height() + QFontMetrics(self._small()).height() + 8
        return QSize(12 + 18 + 8 + w + 14, max(h, 30))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        from .main_window import _material_icon
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if theme.is_mac():
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.get("btn_hover", c["seg_on"]) if self._hover else c.get("conn_bg", c["surface2"])))
            p.drawRoundedRect(r, 5, 5)
            p.setFont(self._badge_font())
            p.setPen(QColor(c["text"] if self._hover else c.get("sub", c["muted"])))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.name)
            p.end()
            return
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(c["muted"] if self._hover else c["border"]))
        p.setBrush(QColor(c["surface2"] if self._hover else c["surface"]))
        p.drawRoundedRect(r, 9, 9)
        icon = _material_icon(self.glyph, 18, c["accent"] if self._hover else c["tab_text"])
        p.drawPixmap(12, (self.height() - 18) // 2, icon.pixmap(QSize(18, 18)))
        x = 12 + 18 + 8
        w = self.width() - x - 10
        bold = self.font()
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(QColor(c["text"]))
        top = QRectF(x, 3, w, self.height() / 2 - 1)
        p.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, int(w)))
        small = self._small()
        p.setFont(small)
        p.setPen(QColor(c["muted"]))
        bottom = QRectF(x, self.height() / 2 + 1, w, self.height() / 2 - 4)
        p.drawText(bottom, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   p.fontMetrics().elidedText(self.description, Qt.TextElideMode.ElideRight, int(w)))
        p.end()


class SmartBar(QScrollArea):
    """The row of smart preset chips above the graph (scrolls sideways when they don't fit)."""
    apply = Signal(dict)

    def __init__(self, page, generator, parent=None):
        super().__init__(parent)
        self.page, self.gen = page, generator
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No scrollbar (it ate the chips' bottom edge); the wheel / trackpad scrolls sideways.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.row = QHBoxLayout(self.body)
        self.row.setContentsMargins(0, 2, 0, 6)
        self.row.setSpacing(8)
        self.setWidget(self.body)
        generator.changed.connect(self.rebuild)
        theme.on_apply(self._paint)
        theme.on_apply(self.fit_to_font)  # the text size lives in the theme's stylesheet
        self._paint()
        self.fit_to_font()

    def fit_to_font(self):
        """As tall as a chip at the current text size (+ room for the row's margins)."""
        probe = _Chip("auto_awesome", "Ag", "Ag", self.body)
        probe.ensurePolished()  # picks up the app's text size from the stylesheet
        self.setFixedHeight(probe.sizeHint().height() + 10)
        probe.hide()
        probe.deleteLater()

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit_to_font()
        super().changeEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta()
        step = d.x() if abs(d.x()) > abs(d.y()) else d.y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - step)
        e.accept()

    def _paint(self):
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        for i in range(self.row.count()):
            w = self.row.itemAt(i).widget()
            if w is not None:
                w.update()

    def _clear(self):
        while self.row.count():
            item = self.row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _tip(self, layout):
        page = self.page
        rows = []
        for i, chart in enumerate(layout["charts"], 1):
            dots = " ".join(
                f"<span style='color:{page.channel_color(n) if hasattr(page, 'channel_color') else '#888'}'>"
                f"&#9679;</span>&nbsp;{n}" for n in chart["channels"])
            rows.append(f"<b>{i}.</b> {dots}")
        src = "Suggested by your AI" if self.gen.source == "ai" else "Built in"
        muted = theme.colors()["muted"]
        return (f"<b>{layout['name']}</b><br>{layout.get('description', '')}<br><br>"
                + "<br>".join(rows) + f"<br><br><span style='color:{muted}'>{src} · click to show</span>")

    def rebuild(self):
        self._clear()
        gen = self.gen
        for layout in gen.layouts:
            chip = _Chip(icon_for(layout["name"]), layout["name"], layout.get("description", ""), self.body)
            chip.setToolTip(self._tip(layout))
            chip.clicked.connect(lambda l=layout: self.apply.emit(l))
            self.row.addWidget(chip)
        if gen.busy:
            chip = _Chip("auto_awesome", "Generating…", "Asking your AI for smart presets", self.body)
            chip.setCursor(Qt.CursorShape.BusyCursor)
            chip.setToolTip("Your AI is suggesting layouts for this log's channels (only the names are sent).")
            self.row.addWidget(chip)
        elif gen.needs_approval() and gen.source != "ai":
            chip = _Chip("auto_awesome", "Generate with AI", "Smart presets for these channels", self.body)
            chip.setToolTip("Ask your AI for layouts made for this log. You'll see exactly what's sent first "
                            "(only the channel names).")
            chip.clicked.connect(lambda: gen.generate(self.page))
            self.row.addWidget(chip)
        self.row.addStretch(1)
        self.body.adjustSize()
        self.fit_to_font()
