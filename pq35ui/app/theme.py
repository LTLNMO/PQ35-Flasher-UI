"""App theme. Mode (dark / light / black / system), one accent colour, and a tint strength
that pulls the backgrounds toward the accent's hue. Plus font size."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


def _mix(a, b, t):
    """Blend colour *a* toward *b* by fraction t (0..1)."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t)).name()


def _accent_hover(accent):
    """Lighter version of accent for hover states."""
    c = QColor(accent)
    return QColor.fromHslF(c.hslHueF(), c.hslSaturationF(), min(c.lightnessF() + 0.12, 1.0)).name()


def on_accent(accent):
    """Black or white, whichever reads better on top of *accent*."""
    c = QColor(accent)
    lum = 0.2126 * c.redF() ** 2.2 + 0.7152 * c.greenF() ** 2.2 + 0.0722 * c.blueF() ** 2.2
    return "#000000" if lum > 0.18 else "#ffffff"


DEFAULT_ACCENT = "#e8820c"

# Status colours never follow the accent, so OK / warning / error always mean the same thing.
_DARK_STATUS = {
    "ok": "#4ade80", "ok_bg": "#0f2a1a", "warn": "#f59e0b", "warn_bg": "#2e1f0a",
    "err": "#f87171", "err_bg": "#2d1212", "info": "#60a5fa", "info_bg": "#0f1d2e",
}
_LIGHT_STATUS = {
    "ok": "#16a34a", "ok_bg": "#dcfce7", "warn": "#d97706", "warn_bg": "#fef3c7",
    "err": "#dc2626", "err_bg": "#fee2e2", "info": "#2563eb", "info_bg": "#dbeafe",
}

# Chrome recipe per mode: role -> (lightness, max saturation at full tint).
# At tint 0 every role is a neutral grey; raising tint pulls it toward the accent's hue.
_CHROME = {
    "dark": {"bg": (0.051, 0.45), "surface": (0.086, 0.38), "surface2": (0.118, 0.32),
             "border": (0.165, 0.28), "muted": (0.46, 0.16), "text": (0.90, 0.22)},
    "black": {"bg": (0.0, 0.0), "surface": (0.045, 0.45), "surface2": (0.08, 0.38),
              "border": (0.13, 0.30), "muted": (0.46, 0.16), "text": (0.90, 0.22)},
    "light": {"bg": (0.958, 0.55), "surface": (0.995, 0.60), "surface2": (0.905, 0.42),
              "border": (0.835, 0.32), "muted": (0.45, 0.20), "text": (0.10, 0.30)},
}

# Old named colour schemes, kept only to carry an existing choice over: palette -> (accent, tint).
_LEGACY_PALETTES = {"ember": ("#e8820c", 0), "slate": ("#38bdf8", 45), "charcoal": ("#f5a623", 35),
                    "graphite": ("#34d399", 25), "indigo": ("#818cf8", 70)}

# Settings are read from the app's Settings store at apply() time.
# Kept across importlib.reload (the F5 / file-watch hot-reload in main.py): without this a reload
# reset them, so the app fell back to the default amber / neutral look and icons stopped following.
try:
    _settings  # noqa: B018
except NameError:
    _settings = None


def set_settings(settings):
    global _settings
    _settings = settings


def _system_dark() -> bool:
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "colorScheme"):
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def mode() -> str:
    """Resolved mode: 'dark', 'light' or 'black'."""
    pref = _settings.get("theme", "dark") if _settings else "dark"
    if pref == "system":
        return "dark" if _system_dark() else "light"
    return pref if pref in _CHROME else "dark"


def accent() -> str:
    if not _settings:
        return DEFAULT_ACCENT
    saved = _settings.get("accent_color")
    if saved:
        return saved
    return _LEGACY_PALETTES.get(_settings.get("palette"), (DEFAULT_ACCENT, 0))[0]


def tint() -> int:
    """Background tint strength, 0 (neutral grey) .. 100 (strongly tinted)."""
    if not _settings:
        return 0
    saved = _settings.get("tint")
    if saved is None:
        return _LEGACY_PALETTES.get(_settings.get("palette"), (None, 0))[1]
    return max(0, min(100, int(saved)))


# ── Interface: Classic (the app's own look) or Native (the look of the system it runs on) ──
# Native is the Mac look on macOS (Mockups/Swift native); the Windows look comes later, so until
# then Native is only offered on a Mac. Changing it restarts the app (main_window builds a
# different frame around the same pages).
INTERFACES = (("classic", "Classic"), ("native", "Mac"))


def native_available() -> bool:
    import sys
    return sys.platform == "darwin"


INTERFACE_KEY = "ui_interface"  # NOT "interface": that one is the saved CAN adapter


def _migrate_interface_key():
    """An early build saved the choice as "interface", overwriting the saved CAN adapter with a
    string (the adapter list then crashed). Move it to its own key and clear the bad value."""
    old = _settings.get("interface")
    if isinstance(old, str):
        if _settings.get(INTERFACE_KEY) is None:
            _settings.set(INTERFACE_KEY, old)
        _settings.set("interface", None)


def interface() -> str:
    """'classic' or 'mac' (the resolved native look)."""
    import os
    if _settings:
        _migrate_interface_key()
    pref = os.environ.get("PQ35_INTERFACE") or (_settings.get(INTERFACE_KEY, "classic") if _settings else "classic")
    if pref in ("native", "mac") and (native_available() or os.environ.get("PQ35_INTERFACE")):
        return "mac"
    return "classic"


def is_mac() -> bool:
    return interface() == "mac"


MAC_ACCENT = {"dark": "#0a84ff", "light": "#007aff"}  # macOS system blue
# macOS-like greys (window, content, controls, separators, secondary label, label).
# Dark values are the Swift mockup's CSS (Mockups/Swift native, gen.py of 2026-11-14) flattened:
# its rgba(255,255,255,a) layers composited over the window's #1e1e1e (or the sidebar's #2a2a30).
#   text .88, muted .5, sub .55, cards .05 (+ .08 border), table groups .04, zebra +.03,
#   buttons .12, segments .08 / on .22, sidebar selection .12, header line black .65.
_MAC_CHROME = {
    "dark": {"bg": "#1e1e1e", "surface": "#292929", "surface2": "#393939", "border": "#363636",
             "muted": "#8e8e8e", "sub": "#9a9a9a", "text": "#e4e4e4", "sidebar": "#2a2a30",
             "sidebar_sel": "#444449", "sidebar_group": "#838386", "sidebar_line": "#111113",
             "card_border": "#303030", "group": "#272727", "zebra": "#2e2e2e", "th_line": "#3d3d3d",
             "btn": "#393939", "btn_border": "#393939", "btn_hover": "#444444", "btn_press": "#4f4f4f",
             "header_line": "#0e0e0e", "menu_bg": "#2b2b2d", "menu_border": "#4a4a4c", "conn_bg": "#35353a", "list_sel": "#0a64d6",
             "icon": "#bfbfbf", "panel": "#252528", "seg_on": "#5e5e5e", "segments": "#303030"},
    "light": {"bg": "#f5f5f7", "surface": "#ffffff", "surface2": "#e8e8ea", "border": "#dcdce0",
              "muted": "#6e6e73", "sub": "#6e6e73", "text": "#1d1d1f", "sidebar": "#e8e8ea",
              "sidebar_sel": "#d4d4d8", "sidebar_group": "#8a8a8e", "sidebar_line": "#d0d0d4",
              "card_border": "#e0e0e4", "group": "#ffffff", "zebra": "#f5f5f7", "th_line": "#e0e0e4",
              "btn": "#ffffff", "btn_border": "#d0d0d4", "btn_hover": "#f0f0f2", "btn_press": "#e4e4e6",
              "header_line": "#d8d8dc", "menu_bg": "#f2f2f4", "menu_border": "#c8c8cc", "conn_bg": "#dcdce0", "list_sel": "#0a64d6",
              "icon": "#4a4a4f", "panel": "#f0f0f2", "seg_on": "#ffffff", "segments": "#e3e3e6"},
}


def _build_mac_colors(m):
    m = "light" if m == "light" else "dark"
    c = dict(_MAC_CHROME[m])
    c.update(_LIGHT_STATUS if m == "light" else _DARK_STATUS)
    # Apple's system colours (the mockup's .ok/.warn/.err/.badge), tints at its 16 % / 15 %.
    sys_colors = ({"ok": "#30d158", "warn": "#ffd60a", "err": "#ff453a", "info": "#0a84ff"} if m == "dark"
                  else {"ok": "#28a745", "warn": "#b38600", "err": "#e5352b", "info": "#007aff"})
    for key, col in sys_colors.items():
        c[key] = col
        c[key + "_bg"] = _mix(c["bg"], col, 0.16 if m == "dark" else 0.14)
    a = MAC_ACCENT[m]
    c["accent"] = a
    c["accent_bg"] = _mix(c["surface"], a, 0.16 if m == "light" else 0.24)
    c["on_accent"] = "#ffffff"
    c["tab_text"] = c["icon"]  # toolbar icons: light grey, as in the mockup
    c["tab_disabled"] = _mix(c["sidebar"], c["muted"], 0.45)
    return c


def colors(mode_name=None, accent_color=None, tint_amount=None) -> dict:
    """The full colour set. Arguments override the saved settings (used by the Settings preview)."""
    m = mode_name or mode()
    if is_mac() and accent_color is None and tint_amount is None:
        key = ("mac", m)
        if key not in _COLORS_CACHE:
            _COLORS_CACHE[key] = _build_mac_colors(m)
        return dict(_COLORS_CACHE[key])
    a = accent_color or accent()
    t = (tint() if tint_amount is None else tint_amount) / 100.0
    key = (m, a, t)
    if key not in _COLORS_CACHE:  # painting asks for these many times a second
        if len(_COLORS_CACHE) > 64:  # e.g. dragging the tint slider in Settings
            _COLORS_CACHE.clear()
        _COLORS_CACHE[key] = _build_colors(m, a, t)
    return dict(_COLORS_CACHE[key])  # a copy: callers may change theirs


_COLORS_CACHE = {}


def _build_colors(m, a, t):
    hue = max(QColor(a).hslHueF(), 0.0)  # greys report -1
    c = {role: QColor.fromHslF(hue, sat * t, light).name() for role, (light, sat) in _CHROME[m].items()}
    c.update(_LIGHT_STATUS if m == "light" else _DARK_STATUS)
    c["accent"] = a
    c["accent_bg"] = _mix(c["surface"], a, 0.14 if m == "light" else 0.2)
    c["on_accent"] = on_accent(a)
    # Nav tab labels/icons: brighter than "muted" and carrying more of the hue, so they read clearly
    # and visibly follow the tint. Greyed-out tabs get a dimmer version of the same colour.
    tab_light = 0.36 if m == "light" else 0.66
    c["tab_text"] = QColor.fromHslF(hue, 0.40 * t, tab_light).name()
    # Greyed-out = the same colour faded toward the bar, so it reads as "dimmed", not "different".
    c["tab_disabled"] = _mix(c["surface"], c["tab_text"], 0.42)
    return c


# Called after every apply(), for widgets that paint theme colours into pixmaps (nav icons, graphs).
try:
    _listeners  # noqa: B018  (survives hot-reload, see _settings)
except NameError:
    _listeners = []
_last_css = None  # skip the restyle when nothing changed
try:
    _root  # noqa: B018  (survives hot-reload)
except NameError:
    _root = None


def set_root(widget):
    """The main window. Once it exists, theme changes restyle just its widget tree (popups and
    dialogs parented to it included), which is ~4x faster than replacing the app-wide stylesheet."""
    global _root
    _root = widget


def on_apply(callback):
    """Call `callback` after every apply(). A widget's own method is dropped as soon as the widget
    is destroyed (else every dialog or rebuilt control left one behind for good)."""
    _listeners.append(callback)
    owner = getattr(callback, "__self__", None)
    destroyed = getattr(owner, "destroyed", None)
    if destroyed is not None:
        try:
            destroyed.connect(lambda *_a, cb=callback: _forget(cb))
        except (RuntimeError, TypeError):
            pass


def _forget(callback):
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def font_size() -> int:
    return _settings.get("font_size", 13) if _settings else 13


# ── Mac interface: one icon scale, tied to Settings → Text size ──
# Apple sizes an SF Symbol to the text beside it (a symbol's point size = the font's). Our symbols
# are drawn at 0.8 x their box (sf_symbols.icon), so a box of text_pt / 0.8 gives a symbol as tall
# as that text. Pages never pick their own number: _material_icon maps every size through here.

class MacPx(int):
    """An icon size already on the Mac scale (icon_px); mac_icon_size leaves it alone."""


def icon_px(role="body"):
    """The Mac icon box for a role: body (buttons, menus, lists: next to body text), control
    (segments and small controls: next to 12 pt text), toolbar (stand-alone toolbar icons: a step
    up, as in Finder's and Mail's toolbars), sidebar (as big, as in Finder's sidebar), small (a
    caption-sized glyph)."""
    fs = font_size()
    text = {"body": fs, "control": fs - 1, "toolbar": fs + 2, "sidebar": fs + 2, "small": fs - 2}[role]
    return MacPx(round(text / 0.8))


def mac_icon_size(px):
    """Mac: a size asked for in Classic's px (18 menus, 20/22 toolbars…) becomes the scale's body
    size; small glyphs (chevrons, dots, < 17 px) and sizes already on the scale stay as they are."""
    if isinstance(px, MacPx) or px < 17:
        return px
    return icon_px("body")


def apply(app):
    """Restyle the whole app. The stylesheet step re-polishes every widget (~1–2 s with all pages
    built), so it's skipped when the result is unchanged; callers should batch rapid changes."""
    global _last_css
    c = colors()
    fs = font_size()
    mono_size = max(fs - 2, 9)
    title_size = fs + 10
    h1_size = fs + 7
    accent_hover = _accent_hover(c["accent"])
    if app.style().objectName().lower() != "fusion" and not app.styleSheet():
        app.setStyle("Fusion")  # once; re-setting it re-polishes every widget again (~0.3 s)
    palette = QPalette()
    for role, key in (
            (QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"),
            (QPalette.ColorRole.AlternateBase, "surface2"), (QPalette.ColorRole.Button, "surface"),
            (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
            (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
            (QPalette.ColorRole.ToolTipBase, "surface"), (QPalette.ColorRole.ToolTipText, "text"),
            (QPalette.ColorRole.PlaceholderText, "muted"), (QPalette.ColorRole.Mid, "border")):
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["on_accent"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    css = STYLE.format(**c, fs=fs, mono_size=mono_size, title_size=title_size, h1_size=h1_size,
                       accent_hover=accent_hover)
    if is_mac():
        c.setdefault("sidebar", c["surface"])
        c.setdefault("sidebar_sel", c["surface2"])
        c.setdefault("sidebar_line", c["border"])
        c.setdefault("card_border", c["border"])
        c.setdefault("panel", c["surface"])
        c.setdefault("seg_on", c["surface2"])
        c.setdefault("segments", c["surface2"])
        css += MAC_STYLE.format(**c, fs=fs, small=max(fs - 2, 9), small_plus=max(fs - 1, 9),
                                title_size=fs + 4, big=fs + 13, header_size=fs + 2,
                                scan_title=fs + 4, accent_hover=accent_hover)
        css += _mac_arrows(c)  # after MAC_STYLE: its image rules win
    root = _root
    try:
        use_root = root is not None and bool(app.styleSheet())
        if use_root:
            root.objectName()  # raises RuntimeError if the window is gone
    except RuntimeError:
        use_root = False
    target = root if use_root else app
    if css == _last_css and target.styleSheet() == css:
        return
    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        app.setPalette(palette)  # cheap; tooltips and other top-levels read their colours from it
        # The app-wide sheet is only set before the window exists (startup dialogs). After that the
        # window's own sheet overrides it, and replacing the app sheet would re-polish everything.
        target.setStyleSheet(css)
        _last_css = css
    finally:
        QGuiApplication.restoreOverrideCursor()
    for callback in list(_listeners):
        try:
            callback()
        except RuntimeError:  # widget already deleted
            _listeners.remove(callback)


STYLE = """QWidget {{ font-size: {fs}pt; color: {text}; }}
QMainWindow, QDialog, #page {{ background: {bg}; }}

/* Top bar + headings */
#topbar {{ background: {surface}; border-bottom: 1px solid {border}; }}
#title {{ font-size: {title_size}pt; font-weight: 800; color: {accent}; letter-spacing: 0.3px; }}
#nav {{ background: {surface}; border-right: 1px solid {border}; }}
#h1 {{ font-size: {h1_size}pt; font-weight: 700; letter-spacing: -0.2px; }}
#muted, QLabel[muted="true"] {{ color: {muted}; }}

/* Cards */
#card {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; }}
#card[selected="true"] {{ border: 1px solid {accent}; background: {surface}; }}
#cardName {{ font-size: 13pt; font-weight: 700; color: {accent}; }}
#cardTitle {{ font-weight: 600; }}

/* Drop zone */
#drop {{ background: {surface}; border: 2px dashed {border}; border-radius: 12px; }}
#drop[hover="true"] {{ border-color: {accent}; background: {accent_bg}; }}

/* Inputs */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {surface2}; border: 1px solid {border}; border-radius: 8px;
    padding: 6px 11px; min-height: 20px; color: {text};
    selection-background-color: {accent}; selection-color: {on_accent}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {muted}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox {{ combobox-popup: 0; }}  /* plain list popup: the macOS menu-style one paints a square panel behind the rounded list */
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; border-radius: 9px;
    selection-background-color: {accent_bg}; selection-color: {accent}; outline: none; padding: 5px; }}
QComboBoxPrivateContainer {{ background: transparent; border: none; }}  /* window behind the rounded list */
QComboBox QAbstractItemView > QWidget {{ background: transparent; }}  /* viewport: keep the list's corners */
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 17px; border: none; background: transparent; }}

/* Buttons */
QPushButton {{ background: {surface2}; border: 1px solid {border}; border-radius: 8px;
    padding: 7px 15px; color: {text}; font-weight: 600; min-height: 18px; }}
QPushButton:hover {{ background: {border}; }}
QPushButton:pressed {{ background: {accent_bg}; border-color: {accent}; }}
QPushButton:focus {{ border-color: {accent}; }}
QPushButton#iconTool:focus {{ border-color: {border}; }}  /* icon buttons: no outline left after a click */
QPushButton:disabled {{ color: {muted}; background: {surface}; border-color: {border}; }}
QToolButton {{ background: {surface2}; border: 1px solid {border}; border-radius: 8px;
    padding: 7px 12px; color: {text}; font-weight: 600; }}
QToolButton:hover {{ background: {border}; }}
QToolButton:pressed {{ background: {accent_bg}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* Primary / danger */
QPushButton#primary {{ background: {accent}; color: {on_accent}; border: 1px solid {accent}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#primary:pressed {{ background: {accent}; }}
QPushButton#primary:disabled {{ background: {surface2}; color: {muted}; border-color: {border}; }}
QPushButton[hero="true"] {{ font-size: 17pt; font-weight: 800; padding: 18px 28px; border-radius: 12px; }}
QPushButton#danger {{ color: {err}; border: 1px solid {err}; background: {surface2}; }}
QPushButton#danger:hover {{ background: {err_bg}; }}
QPushButton#rowAction {{ padding: 2px 10px; border-radius: 6px; min-height: 16px; }}

/* Module chip */
QPushButton#chip {{ border-radius: 8px; padding: 7px 13px; font-weight: 700; }}
QPushButton#chip[level="ok"] {{ background: {ok_bg}; color: {ok}; border: 1px solid {ok}; }}
QPushButton#chip[level="idle"] {{ background: {surface2}; color: {muted}; border: 1px solid {border}; }}
QPushButton:disabled {{ color: {muted}; }}

/* Nav tabs (pills) */
#tabbar {{ background: {surface}; border-bottom: 1px solid {border}; }}
#tabsep {{ background: {border}; }}
QPushButton#tab {{ background: transparent; border: none; border-radius: 10px;
    padding: 7px 10px; margin: 6px 2px; color: {tab_text}; font-weight: 600; }}
QPushButton#tab[labels="true"] {{ padding: 7px 10px; margin: 6px 1px; min-height: 28px; }}  /* = icon size: same bar height */
/* Icon tabs: icon pinned on the left where it sits when collapsed ((50 - 2*2 margin - 28 icon) / 2 = 9),
   so it doesn't jump while the name slides out on hover. */
QPushButton#tab[labels="false"] {{ text-align: left; padding-left: 9px; padding-right: 9px; }}
QPushButton#tab:hover {{ color: {text}; background: {accent_bg}; }}
QPushButton#tab[active="true"] {{ background: {accent}; color: {on_accent}; font-weight: 700; }}
QPushButton#tab:disabled {{ color: {tab_disabled}; }}
QPushButton#tab[locked="true"] {{ color: {tab_disabled}; }}
QPushButton#tab[locked="true"]:hover {{ color: {tab_disabled}; background: {accent_bg}; }}

/* In-page section buttons, one look everywhere: Analyze's views, Logger's modes, Settings'
   sections (QPushButton#viewTab) and every in-page tab bar (Projects, Maps). Like the top bar's
   tabs, a size smaller: the one showing is filled with the accent. */
QPushButton#viewTab {{ background: transparent; border: none; border-radius: 8px; padding: 5px 9px;
    margin: 0; min-height: 22px; color: {tab_text}; font-weight: 600; text-align: left; }}
QPushButton#viewTab:hover {{ color: {text}; background: {accent_bg}; }}
QPushButton#viewTab[active="true"] {{ background: {accent}; color: {on_accent}; font-weight: 700; }}
QPushButton#viewTab:disabled {{ color: {muted}; }}
/* Settings: the heading of each group card, and the search results list */
QLabel#settingsGroup {{ color: {text}; font-weight: 700; padding-bottom: 2px; }}
QListWidget#settingsResults::item {{ padding: 0; }}
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 10px; background: {surface}; top: 4px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {tab_text}; padding: 5px 11px; margin-right: 2px;
    border: none; border-radius: 8px; font-weight: 600; min-height: 22px; }}
QTabBar::tab:hover {{ color: {text}; background: {accent_bg}; }}
QTabBar::tab:selected {{ background: {accent}; color: {on_accent}; font-weight: 700; }}

/* Badges */
QLabel[level="ok"] {{ background: {ok_bg}; color: {ok}; border-radius: 7px; padding: 3px 10px; font-weight: 700; }}
QLabel[level="warning"] {{ background: {warn_bg}; color: {warn}; border-radius: 7px; padding: 3px 10px; font-weight: 700; }}
QLabel[level="error"] {{ background: {err_bg}; color: {err}; border-radius: 7px; padding: 3px 10px; font-weight: 700; }}
QLabel[level="info"] {{ background: {info_bg}; color: {info}; border-radius: 7px; padding: 3px 10px; font-weight: 700; }}
QLabel[level="idle"] {{ background: {surface2}; color: {muted}; border-radius: 7px; padding: 3px 10px; }}

/* Banners */
#banner[level="error"] {{ background: {err_bg}; color: {err}; border-radius: 9px; border-left: 3px solid {err}; }}
#banner[level="warning"] {{ background: {warn_bg}; color: {warn}; border-radius: 9px; border-left: 3px solid {warn}; }}
#banner[level="ok"] {{ background: {ok_bg}; color: {ok}; border-radius: 9px; border-left: 3px solid {ok}; }}
#banner[level="info"] {{ background: {info_bg}; color: {info}; border-radius: 9px; border-left: 3px solid {info}; }}

/* Radios / checks */
QRadioButton#option {{ background: {surface}; border: 1px solid {border}; border-radius: 9px; padding: 11px; }}
QRadioButton#option:checked {{ border: 1px solid {accent}; background: {accent_bg}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
    border: 1px solid {muted}; background: {surface2}; border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {border}; }}

/* Progress */
QProgressBar {{ background: {surface2}; border: none; border-radius: 6px; height: 12px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}

/* Text / trees / tables / lists */
QPlainTextEdit, QTreeWidget, QTableWidget, QListWidget {{ background: {surface}; border: 1px solid {border};
    border-radius: 10px; color: {text}; outline: none; }}
QPlainTextEdit {{ font-family: "SF Mono", "Cascadia Code", "JetBrains Mono", "Consolas",
    "DejaVu Sans Mono", monospace; font-size: {mono_size}pt; padding: 4px; }}
QTreeWidget::item, QListWidget::item {{ padding: 5px 4px; border-radius: 5px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {surface2}; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
    background: {accent_bg}; color: {accent}; }}
QHeaderView::section {{ background: {surface}; border: none; border-bottom: 1px solid {border};
    padding: 8px 10px; color: {muted}; font-weight: 700; }}
QTableWidget {{ gridline-color: {border}; }}
QTableWidget::item {{ padding: 5px 7px; }}

/* Scrollbars — thin floating, inset by the bar margin */
QScrollArea {{ border: none; background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 3px; min-width: 40px; }}
QScrollBar::handle:horizontal:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Group boxes */
QGroupBox {{ border: 1px solid {border}; border-radius: 10px; margin-top: 16px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {muted}; font-weight: 700; }}

/* Menus */
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 9px; padding: 5px; color: {text}; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_bg}; color: {accent}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}

/* Splitter */
QSplitter {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QSplitter#panelSplit::handle:horizontal {{ width: 12px; }}
QSplitter#panelSplit::handle:vertical {{ height: 12px; }}

/* Tooltips */
QToolTip {{ background: palette(tooltip-base); border: 1px solid palette(mid); color: palette(tooltip-text);
    border-radius: 8px; padding: 5px 11px; font-size: {fs}pt; }}
"""


def _mac_arrows(c):
    """Combo boxes and spin boxes with AppKit's chevrons (SF Symbols saved as PNGs), as a pop-up
    button and a stepper draw them. Empty when the symbols can't be drawn (not macOS)."""
    try:
        from . import sf_symbols
        updown = sf_symbols.symbol_file("chevron.up.chevron.down", 12, c["sub"], "semibold", point=8.5)
        up = sf_symbols.symbol_file("chevron.up", 8, c["sub"], "bold", point=6)
        down = sf_symbols.symbol_file("chevron.down", 8, c["sub"], "bold", point=6)
    except Exception:  # cosmetic: without the symbols, Qt's own arrows
        return ""
    css = ""
    on, off = _mac_checkbox_files(c)
    if on and off:  # AppKit's check box: blue rounded square + white tick; off: a dark well
        for sel in ("QCheckBox", "QAbstractItemView", "QTreeWidget", "QListWidget", "QTableWidget"):
            css += (f"{sel}::indicator {{{{ width: 14px; height: 14px; border: none; background: transparent; "
                    f"image: url('{off}'); }}}}\n{sel}::indicator:checked {{{{ image: url('{on}'); }}}}\n")
    if updown:
        css += ("QComboBox::drop-down {{ border: none; width: 22px; }}\n"
                "QComboBox::down-arrow {{ image: url('{0}'); width: 12px; height: 12px; }}\n").format(updown)
    if up and down:
        css += ("QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url('{0}'); width: 8px; height: 8px; }}\n"
                "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url('{1}'); width: 8px; height: 8px; }}\n"
                ).format(up, down)
    return css.replace("{{", "{").replace("}}", "}")


def _mac_checkbox_files(c):
    """Check box images (14 pt, saved at 2x): on = accent square with a white tick, off = a
    field-coloured square with a thin outline. (None, None) if they can't be saved."""
    import os
    import tempfile
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen
    folder = os.path.join(tempfile.gettempdir(), "pq35-sf-symbols")
    paths = []
    for state in ("on", "off"):
        fill = c["accent"] if state == "on" else c["btn"]
        edge = c["accent"] if state == "on" else c["menu_border"]
        path = os.path.join(folder, f"check-{state}-{fill.lstrip('#')}-{edge.lstrip('#')}@2x.png")
        if not os.path.exists(path):
            img = QImage(28, 28, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(edge), 1.2))
            p.setBrush(QColor(fill))
            p.drawRoundedRect(QRectF(1.5, 1.5, 25, 25), 6, 6)
            if state == "on":
                tick = QPainterPath(QPointF(7.5, 14.5))
                tick.lineTo(12, 19)
                tick.lineTo(20.5, 9)
                pen = QPen(QColor("#ffffff"), 3.2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(tick)
            p.end()
            try:
                os.makedirs(folder, exist_ok=True)
                if not img.save(path, "PNG"):
                    return None, None
            except OSError:
                return None, None
        paths.append(path)
    return paths[0], paths[1]


# Added after STYLE in the Mac interface: San Francisco, Mac-sized radii and controls, the sidebar
# frame (mac_shell) and segmented view switches in the system blue.
MAC_STYLE = """
QWidget {{ font-family: ".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue"; }}
/* Buttons (mockup .btn: 24 px tall, 0 11 px, radius 6, white .12, no border) */
QPushButton {{ background: {btn}; border: 1px solid {btn_border}; border-radius: 6px; padding: 2px 11px;
    min-height: 18px; font-weight: 500; color: {text}; }}
QPushButton:hover {{ background: {btn_hover}; border-color: {btn_hover}; }}
QPushButton:pressed {{ background: {btn_press}; border-color: {btn_press}; }}
QPushButton:focus {{ border-color: {btn_border}; }}
QPushButton:disabled {{ background: {btn}; border-color: {btn_border}; color: {muted}; }}
QPushButton#primary {{ background: {accent}; border: 1px solid {accent}; color: #ffffff; font-weight: 500; }}
QPushButton#primary:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton#primary:disabled {{ background: {btn}; border-color: {btn_border}; color: {muted}; }}
QPushButton#danger {{ background: {btn}; border: 1px solid {btn_border}; color: {err}; }}
QPushButton[hero="true"] {{ font-size: 14pt; font-weight: 500; padding: 4px 16px; border-radius: 6px; }}
QToolButton {{ border-radius: 6px; padding: 4px 8px; font-weight: 500; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {btn}; border: 1px solid {btn_border};
    border-radius: 6px; padding: 2px 8px; min-height: 18px; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {btn_hover}; }}
/* Cards (mockup .card: white .05, .08 border, radius 10) and table groups (.grp: white .04) */
#card {{ background: {group}; border: 1px solid {card_border}; border-radius: 10px; }}
#cardName {{ color: {text}; font-size: {fs}pt; }}
#cardTitle {{ color: {muted}; font-size: {small}pt; font-weight: 600; letter-spacing: 0.2px; }}
/* One type scale (Settings → Text size = body): caption fs-2, control fs-1, body fs, title fs+2.
   Classic's big headings (h1 fs+7, title fs+10) and accent card names become Mac titles/captions. */
#h1 {{ font-size: {header_size}pt; font-weight: 700; letter-spacing: 0; }}
#title {{ font-size: {header_size}pt; font-weight: 700; color: {text}; letter-spacing: 0; }}
#cardName {{ font-size: {small}pt; font-weight: 600; color: {muted}; letter-spacing: 0.2px; }}
/* Banners: a quiet tinted box, no accent bar, body weight (AppKit's inline notices) */
#banner[level="error"], #banner[level="warning"], #banner[level="ok"], #banner[level="info"] {{
    border-left: none; border-radius: 8px; font-weight: 400; }}
#banner[level="info"] {{ background: {segments}; color: {text}; }}
#card QTableWidget, #card QTreeWidget, #card QListWidget {{ border: none; background: transparent;
    alternate-background-color: {zebra}; }}
#card QHeaderView::section {{ background: transparent; }}
/* Tables: small grey headers, no row lines, zebra rows (mockup th/td) */
QTableWidget {{ gridline-color: transparent; alternate-background-color: {zebra}; }}
QTableWidget::item {{ border: none; padding: 0 12px; }}
QTreeWidget::item {{ border: none; }}
QHeaderView::section {{ color: {muted}; font-size: {small}pt; font-weight: 600; text-align: left;
    padding: 5px 12px; border: none; border-bottom: 1px solid {th_line}; }}
#sideWindow {{ background: {bg}; }}
#macPanel {{ background: {panel}; border-right: 1px solid {sidebar_line}; }}
#macPaneTitle {{ font-size: {fs}pt; font-weight: 600; }}  /* a column's heading: one step under the page title */
#macPanel #muted {{ font-size: {small}pt; }}
#macFootLine {{ background: {sidebar_line}; }}
#macPanel QLineEdit {{ background: {conn_bg}; border: 1px solid {conn_bg}; border-radius: 7px; padding: 3px 8px; }}
/* Segmented control (mockup .seg: white .08, radius 7, 2 px inset; on: white .22) */
#macSegments {{ background: {segments}; border-radius: 7px; }}
#macSegments QPushButton#viewTab {{ border-radius: 5px; margin: 0; padding: 0 12px; min-height: 22px; max-height: 22px;
    font-size: {small_plus}pt; }}
QPushButton#macFilePill {{ padding: 2px 11px; font-weight: 500; }}
QTextBrowser#macLog {{ background: {surface}; border: 1px solid {card_border}; border-radius: 10px; padding: 12px;
    font-family: "Menlo", monospace; font-size: {small_plus}pt; }}
#macPanel QTreeWidget, #macPanel QListWidget {{ border: none; background: transparent; }}
/* The Logger's channel list: the sidebar widget used as the page's main table, so a card */
#macPanel[table="true"] {{ background: {group}; border: 1px solid {card_border}; border-radius: 10px; }}
/* List headers in a column or card: just the names over a thin line, no lighter (square) band */
#macPanel QHeaderView, #macPanel QHeaderView::section {{ background: transparent; }}
QPushButton#viewTab {{ background: transparent; border: none; padding: 3px 12px; color: {text}; font-weight: 500; }}
QPushButton#viewTab:hover {{ background: {btn}; color: {text}; }}
QPushButton#viewTab[active="true"] {{ background: {seg_on}; color: {text}; font-weight: 500; border: none; }}
#settingsPage #card {{ background: transparent; border: none; border-top: 1px solid {border}; border-radius: 0; }}
QLabel#settingsGroup {{ color: {muted}; font-size: {small}pt; font-weight: 600; }}
QRadioButton#option, QRadioButton#option:checked {{ background: transparent; border: none; padding: 3px 8px 3px 2px; }}
QRadioButton::indicator {{ width: 15px; height: 15px; border-radius: 8px; }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 #ffffff, stop:0.32 #ffffff, stop:0.38 {accent}, stop:1 {accent}); border: 1px solid {accent}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: none; background: {btn}; }}
QCheckBox::indicator:checked {{ background: {accent}; }}
/* Dashboard tiles (mockup .card .k/.v/.s) and the scan card */
#macTile {{ background: {surface}; border: 1px solid {card_border}; border-radius: 10px; }}
#macTile #muted {{ font-size: {small}pt; }}
#macBig {{ font-size: {big}pt; font-weight: 700; }}
#macScanIcon {{ border-radius: 16px; background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 #0a84ff, stop:1 #5e5ce6); }}
#macScanTitle {{ font-size: {scan_title}pt; font-weight: 700; }}
#card[tile="true"] {{ background: {surface}; }}
#card[tile="true"] #muted {{ font-size: {small_plus}pt; }}
/* Connect: module cards (big address number + name), the selected one outlined in blue */
#macModNum {{ font-size: {title_size}pt; font-weight: 700; }}
#macModName {{ font-size: {header_size}pt; font-weight: 600; }}
#card[module="true"] {{ background: {surface}; }}
#card[module="true"][selected="true"] {{ border: 2px solid {accent}; }}
#macCarTitle {{ font-size: {header_size}pt; font-weight: 700; }}
QPlainTextEdit, QTreeWidget, QTableWidget, QListWidget {{ border-radius: 8px; }}
QPlainTextEdit {{ font-family: "Menlo", monospace; }}
/* Menus and pop-up lists: AppKit's dark menu (tight rows, blue highlight, thin outline) */
QMenu {{ background: {menu_bg}; border: 1px solid {menu_border}; border-radius: 7px; padding: 5px; }}
QMenu::item {{ padding: 3px 22px 3px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {accent}; color: #ffffff; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {menu_border}; margin: 5px 10px; }}
QMenu::icon {{ padding-left: 8px; }}
QComboBox QAbstractItemView {{ background: {menu_bg}; border: 1px solid {menu_border}; border-radius: 7px;
    padding: 5px; selection-background-color: {accent}; selection-color: #ffffff; }}
QComboBox QAbstractItemView::item {{ padding: 3px 10px; border-radius: 4px; min-height: 18px; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
    background: {list_sel}; color: #ffffff; }}
/* Every in-page tab bar is the mockup's segmented control (.seg): a 2 px inset track, 12 pt labels;
   the page under it has no box of its own (the tables/cards inside have theirs). */
QTabBar {{ background: {segments}; border-radius: 7px; font-size: {small_plus}pt; }}
QTabBar::tab {{ background: transparent; border-radius: 5px; font-weight: 500;
    padding: 0 12px; margin: 2px 0; color: {text}; }}  /* 26 tall: the segmented control's metric */
QTabBar::tab:first {{ margin-left: 2px; }}
QTabBar::tab:last {{ margin-right: 2px; }}
QTabBar::tab:hover {{ background: {btn}; color: {text}; }}
QTabBar::tab:selected {{ background: {seg_on}; color: {text}; font-weight: 500; }}
QTabWidget::pane {{ border: none; background: transparent; top: 10px; }}
QTabWidget#viewTabs::pane {{ top: 0; }}  /* Analyze's views: tabs hidden, so no gap for them (rule 3: content at y127) */
QPushButton#chip {{ border-radius: 6px; padding: 2px 11px; font-weight: 600; }}
/* Badges (mockup .badge: radius 5, 2 px 7 px, 11 px semibold) */
QLabel[level="ok"], QLabel[level="warning"], QLabel[level="error"], QLabel[level="info"], QLabel[level="idle"] {{
    border-radius: 5px; padding: 2px 7px; font-size: {small}pt; font-weight: 600; }}

/* Mac frame: sidebar, page header, connection card (mockup .sidebar/.si/.sh/.conn/.tb) */
#macSidebar {{ background: {sidebar}; border-right: 1px solid {sidebar_line}; }}
#macGroup {{ color: {sidebar_group}; font-size: {small}pt; font-weight: 600; padding: 10px 0 3px 10px; }}
QPushButton#macItem {{ background: transparent; border: none; border-radius: 6px; text-align: left;
    padding: 4px 10px; margin: 0; color: {text}; font-weight: 400; min-height: 16px; }}
QPushButton#macItem:hover {{ background: {btn}; }}
QPushButton#macItem[active="true"] {{ background: {sidebar_sel}; font-weight: 400; }}
QPushButton#macItem[locked="true"] {{ color: {tab_disabled}; }}
#macHeader {{ background: {bg}; border-bottom: 1px solid {header_line}; }}
#macHeader #muted {{ font-size: {small}pt; color: {muted}; }}
#macTitle {{ font-size: {header_size}pt; font-weight: 700; }}
/* Toolbar icons: plain glyphs, a soft square only under the pointer (Mac toolbars) */
QPushButton#iconTool {{ background: transparent; border: none; border-radius: 6px; }}
QPushButton#iconTool:hover {{ background: {btn}; }}
QPushButton#iconTool:checked {{ background: {btn}; }}  /* an on toggle: AppKit's grey square */
QPushButton#iconTool[plain="true"]:checked {{ background: transparent; }}  /* sidebar toggles: just the icon */
QPushButton#iconTool[plain="true"]:checked:hover {{ background: {btn}; }}
QPushButton#iconTool:checked:hover {{ background: {btn_hover}; }}
QPushButton#iconTool:disabled {{ background: transparent; }}  /* greyed out: never looks switched on */
QToolButton {{ background: transparent; border: none; border-radius: 6px; }}
QToolButton:hover {{ background: {btn}; }}
#macCard {{ background: {conn_bg}; border: none; border-radius: 8px; }}
#macCard QLabel {{ font-size: {small}pt; color: {sub}; }}
QPushButton#macCollapse {{ background: transparent; border: none; padding: 3px; border-radius: 6px; min-height: 0; }}
QPushButton#macCollapse:hover {{ background: {btn}; }}
"""
