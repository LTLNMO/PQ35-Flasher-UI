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


def colors(mode_name=None, accent_color=None, tint_amount=None) -> dict:
    """The full colour set. Arguments override the saved settings (used by the Settings preview)."""
    m = mode_name or mode()
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
    _listeners.append(callback)


def font_size() -> int:
    return _settings.get("font_size", 13) if _settings else 13


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
