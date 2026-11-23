"""Top bar (interface menu + module chip), horizontal tab nav, page stack."""
import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QPropertyAnimation, QEasingCurve, Qt, QTimer
from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QProgressBar, QPushButton, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

def _material_icon(codepoint, size=20, color="#8a8880", rotate=0, fill=False):
    """Render a Material Symbols Rounded glyph to a QIcon via QPainter (stylesheet-proof).
    rotate: degrees clockwise (e.g. 90 turns bar_chart into horizontal bars).
    fill: the filled style (the font's FILL axis), e.g. a solid dot for fiber_manual_record."""
    if theme.is_mac():
        size = theme.mac_icon_size(size)  # one scale for every icon (theme.icon_px)
    if not rotate and not fill and theme.is_mac():  # Mac interface: Apple's SF Symbols instead
        from . import sf_symbols
        if codepoint == "arrow_drop_down":  # a menu's ▾: AppKit's small semibold chevron
            sym = sf_symbols.icon("chevron.down", size, color, "semibold", point=max(7.0, size * 0.42))
            if sym is not None:
                return sym
        name = sf_symbols.MATERIAL_TO_SF.get(codepoint)
        if name:
            sym = sf_symbols.icon(name, size, color)
            if sym is not None:
                return sym
    from PySide6.QtWidgets import QApplication
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    real = int(size * dpr)
    px = QPixmap(real, real)
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont("Material Symbols Rounded")
    f.setPixelSize(size)
    if fill:
        try:
            f.setVariableAxis(QFont.Tag("FILL"), 1.0)  # Qt 6.7+
        except AttributeError:
            pass
    p.setFont(f)
    p.setPen(QColor(color))
    from PySide6.QtCore import QRect
    if rotate:
        p.translate(size / 2, size / 2)
        p.rotate(rotate)
        p.translate(-size / 2, -size / 2)
    # (the raw-font path below can't set the FILL axis, so filled glyphs are drawn as text)
    raw = _bundled_symbols(size) if len(codepoint) == 1 and not fill else None
    if raw is not None and raw.supportsCharacter(ord(codepoint)):
        # Straight from the bundled font file: an older "Material Symbols Rounded" installed on
        # the computer has the same family name, may win, and lacks newer glyphs (speed_4_progress).
        from PySide6.QtCore import QPointF
        glyph = raw.glyphIndexesForString(codepoint)[0]
        path = raw.pathForGlyph(glyph)
        advance = raw.advancesForGlyphIndexes([glyph])[0].x()
        path.translate(QPointF((size - advance) / 2, (size + raw.ascent() - raw.descent()) / 2))
        p.fillPath(path, QColor(color))
    else:
        p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, codepoint)
    p.end()
    return QIcon(px)


_RAW_SYMBOLS = {}


def _bundled_symbols(size):
    """The bundled Material Symbols Rounded font at a pixel size, or None if it isn't there."""
    if size not in _RAW_SYMBOLS:
        from PySide6.QtGui import QRawFont
        path = Path(__file__).resolve().parent / "fonts" / "MaterialSymbolsRounded.ttf"
        raw = QRawFont(str(path), size) if path.exists() else None
        _RAW_SYMBOLS[size] = raw if raw is not None and raw.isValid() else None
    return _RAW_SYMBOLS[size]

class AnimatedTabButton(QPushButton):
    """Tab button that smoothly expands to reveal its label on hover (Icons tab style)."""

    COLLAPSED_W = 50
    DURATION = 150
    HOVER_DELAY = 600  # ms before expanding

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self._label = label
        self._expanded_w = 0
        self._icons_mode = True
        self._pinned = False  # the current tab stays expanded so you can see which page you're on
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._expand)
        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(self.DURATION)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._collapse_text)
        self.setMaximumWidth(self.COLLAPSED_W)

    def _calc_expanded_w(self):
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self._label)
        return self.iconSize().width() + text_w + 36

    def enterEvent(self, event):
        super().enterEvent(event)
        if not self._icons_mode:
            return
        self._hover_timer.start(self.HOVER_DELAY)

    def set_pinned(self, on):
        """Keep this tab expanded (icon + name) while it's the current one."""
        self._pinned = on
        if not self._icons_mode:
            return
        if on:
            if not self._expanded_w:
                self._expanded_w = self._calc_expanded_w()
            self.setText(self._label)
            self._anim.stop()
            self._anim.setStartValue(self.width())
            self._anim.setEndValue(self._expanded_w)
            self._anim.start()
        elif not self.underMouse():
            self._collapse()

    def _expand(self):
        if not self.underMouse() and not self._pinned:
            return
        if not self._expanded_w:
            self._expanded_w = self._calc_expanded_w()
        self.setText(self._label)
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(self._expanded_w)
        self._anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if not self._icons_mode:
            return
        self._hover_timer.stop()
        if not self._pinned:
            self._collapse()

    def _collapse(self):
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(self.COLLAPSED_W)
        self._anim.start()

    def _collapse_text(self):
        if self._anim.endValue() == self.COLLAPSED_W and not self.underMouse() and not self._pinned:
            self.setText("")


from . import pages, theme, updates
from .home_page import HomePage
from .flash_page import FlashPage


def _lazy(module, name):
    """A page class imported only when the page is built: the graph pages pull in pyqtgraph
    and numpy, which alone cost about a second at startup."""
    def build(state):
        import importlib
        return getattr(importlib.import_module(module, __package__), name)(state)
    return build


LiveViewPage = _lazy(".liveview_page", "LiveViewPage")
LoggerPage = _lazy(".logger_page", "LoggerPage")
DynoPage = _lazy(".dyno_page", "DynoPage")
# Tabs that stay usable while a hardware job runs (by job): the rest are locked until it ends.
BUSY_OPEN_TABS = {"log": ("logger", "liveview", "playback", "dyno", "help")}
PlaybackPage = _lazy(".playback_page", "PlaybackPage")
MapsPage = _lazy(".maps_page", "MapsPage")
HelpPage = _lazy(".help_page", "HelpPage")
ProjectsPage = _lazy(".projects", "ProjectsPage")
SettingsPage = _lazy(".settings_page", "SettingsPage")
from .client import WorkerPool
from .state import AppState
from .storage import REPO_ROOT, Settings
from .widgets import Badge, ElidedButton, muted
from .popups import MessageBox

# (key, material_icon_codepoint, label, page class)
# Codepoints or icon names from Material Symbols Rounded (https://fonts.google.com/icons);
# names are drawn as ligatures by _material_icon.
# Tab order, in groups: start → write to a module → read / record / view → (right side) app utilities.
NAV = (
    ("home",        "dashboard_2", "Dashboard", HomePage),             # Material name (ligature)
    ("projects",    "folder_open", "Projects", ProjectsPage),
    ("connect",     "\ue8be", "Connect",     pages.ConnectPage),      # settings_ethernet
    ("backup",      "\ue864", "Back up",     pages.BackupPage),       # backup
    ("flash",       "\uea0b", "Flash",       FlashPage),              # bolt
    ("patches",     "\ue87b", "Patches",     pages.PatchesPage),      # extension
    ("diagnostics", "\ue002", "Diagnostics", pages.DiagnosticsPage),  # warning
    ("liveview",    "\ue9e4", "Live View",   LiveViewPage),           # speed
    ("logger",      "timeline", "Logger",     LoggerPage),
    ("playback",    "troubleshoot", "Analyze", PlaybackPage),
    ("dyno",        "ssid_chart",    "Dyno",    DynoPage),
    ("maps",        "\ue55b", "Maps",        MapsPage),               # map
    ("help",        "\ue887", "Help",        HelpPage),               # help
    ("settings",    "\ue8b8", "Settings",    SettingsPage),           # settings
)
# Built before the window shows: all of them. Building pages after it showed froze the window
# for up to ~0.7 s each in its first seconds; the user prefers a slower start with no pauses.
EAGER_PAGES = tuple(key for key, *_ in NAV)
NAV_GROUP_STARTS = {"backup", "diagnostics"}  # a thin divider goes before these
NAV_RIGHT_START = "help"                              # this and the rest sit at the right edge
TAB_ICON = 28
DIVIDER = "|"  # a divider's place in a saved tab order


def default_tab_order():
    """The left-side tabs in NAV order, with a divider before each group."""
    order = []
    for key, *_ in NAV:
        if key == NAV_RIGHT_START:
            break
        if key in NAV_GROUP_STARTS:
            order.append(DIVIDER)
        order.append(key)
    return order


def tab_order(settings):
    """The saved order (Settings → Appearance), checked against the tabs that exist:
    unknown keys are dropped, tabs added in a newer version go on the end."""
    default = default_tab_order()
    saved = settings.get("tab_order")
    if not isinstance(saved, list) or not saved:
        return default
    keys = {k for k in default if k != DIVIDER}
    order = [k for k in saved if k == DIVIDER or k in keys]
    seen = set()
    order = [k for k in order if k == DIVIDER or not (k in seen or seen.add(k))]
    order += [k for k in default if k != DIVIDER and k not in order]
    return order
TAB_DOT = 8  # text tabs: just the red fault dot as an icon
BADGE_TAB = "diagnostics"  # the tab that gets the red dot while fault codes are stored


class SleepBlocker:
    """Keep the computer awake while a flash or read is running."""

    def __init__(self):
        self.process = None

    def hold(self):
        if sys.platform == "win32":
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            return
        tool = shutil.which("systemd-inhibit") or shutil.which("caffeinate")
        if tool and self.process is None:
            self.process = QProcess()
            if tool.endswith("caffeinate"):
                self.process.start(tool, ["-i"])
            else:
                self.process.start(tool, ["--what=sleep:idle:handle-lid-switch",
                                          "--who=PQ35 Flasher", "--why=Writing ECU firmware",
                                          "sleep", "infinity"])

    def release(self):
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            return
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(1000)
            self.process = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.demo = bool(os.environ.get("PQ35_DEMO"))
        self.setWindowTitle("PQ35 Flasher (demo mode)" if self.demo else "PQ35 Flasher")
        self.setMinimumSize(900, 560)  # below this the layouts can't fit their controls
        from . import theme
        self.settings = Settings()
        self._restore_geometry()
        theme.set_settings(self.settings)
        theme.set_root(self)
        from PySide6.QtWidgets import QApplication
        theme.apply(QApplication.instance())  # before any children exist, so it costs nothing
        self.pool = WorkerPool(self.settings, self)
        self.state = AppState(self.pool, self.settings, self)
        from .tooltips import TooltipGate
        TooltipGate.install(self.settings)  # Settings → General → Tooltips
        from .session_log import SessionLog
        self.session_log = SessionLog(self.pool, self)  # writes the session log from the start
        last_check = self.settings.get("health_last") or {}
        self.fault_count = sum(len(m.get("list") or []) for m in last_check.get("modules") or [])
        self.sleep = SleepBlocker()
        self.interfaces = []
        self.listing = False

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.tab_buttons = {}
        self._tab_seps = []  # divider widgets (reused when the order changes)
        topbar, tabbar = self._topbar(), self._tabbar()
        outer.addWidget(topbar)
        outer.addWidget(tabbar)
        pages_layout = outer
        # Mac interface (Settings → Appearance → Interface): a sidebar and page header instead of
        # the top bar and tab bar. Those stay, hidden: they keep the rules (locked, hidden, current
        # page) that the sidebar mirrors (mac_shell).
        self.mac = theme.is_mac()
        self.sidebar = self.mac_header = None
        if self.mac and sys.platform == "darwin":
            # Content runs up under the traffic lights, title bar see-through (Qt 6.9+), like Finder.
            # Qt keeps layouts out of the title bar area (safe area margins) unless told not to.
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
            for widget in (self, root):
                widget.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        if self.mac:
            from . import mac_shell
            topbar.hide()
            tabbar.hide()
            body = QHBoxLayout()
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(0)
            self.sidebar = mac_shell.Sidebar(self)
            body.addWidget(self.sidebar)
            pages_layout = QVBoxLayout()
            pages_layout.setContentsMargins(0, 0, 0, 0)
            pages_layout.setSpacing(0)
            self.mac_header = mac_shell.Header(self)
            pages_layout.addWidget(self.mac_header)
            body.addLayout(pages_layout, 1)
            outer.addLayout(body, 1)
            from PySide6.QtWidgets import QApplication
            mac_shell.install_app_tweaks(QApplication.instance(), self)
            if sys.platform == "darwin":
                mac_shell.build_menu_bar(self)

        # Page stack — full width, no sidebar
        # Every page is built here, before the window shows (EAGER_PAGES). The session log itself
        # starts with the window, see SessionLog. _build_next_page would still build any page
        # left out of EAGER_PAGES after the window is up.
        self.stack = QStackedWidget()
        self.pages = {}
        self._page_classes = {key: cls for key, _icon, _label, cls in NAV}
        pages_layout.addWidget(self.stack, 1)
        self.busy_bar = QProgressBar()
        self.busy_bar.setFixedHeight(4)
        self.busy_bar.hide()
        pages_layout.addWidget(self.busy_bar)
        # The window is assembled first and the pages go into it after: Qt restyles a whole
        # widget tree each time it moves to a new parent, so attaching it last styled every page twice.
        self.setCentralWidget(root)
        if self.sidebar is not None:
            self.mac_header.make_room(self.sidebar.width())
        for key in EAGER_PAGES:
            self._ensure_page(key)

        # Version / update info: shown in Help → Troubleshooting & logs
        self.local_version = updates.local_version()
        self.runtime_label = muted("")
        self.update_status = muted("")
        self.update_button = QPushButton("Check for updates")
        self.update_button.clicked.connect(self._update_clicked)
        self.update_info = None
        self.checker = updates.UpdateChecker(self)
        self.checker.finished.connect(self._update_checked)

        self.state.changed.connect(self._state_changed)
        self.state.busy_changed.connect(self._busy_changed)
        self.state.connect_needed.connect(self._connect_needed)
        self.state.progress.connect(lambda m: self.busy_bar.setValue(int(m.get("percent", 0))))
        # A page built during one of these signals connected too late to receive it, so it's
        # handed the call directly (_open_built). Pages built earlier get it via their own slot.
        self.state.open_in_flash.connect(
            lambda path: self._open_built("flash", lambda page: page.load(path), self.navigate))
        self.state.open_patched_in_flash.connect(
            lambda path, source: self._open_built("flash", lambda page: page.load(path, patched_from=source)))
        self.state.refresh_interfaces.connect(self.refresh_interfaces)
        self.state.start_recovery.connect(
            lambda: self._open_built("flash", lambda page: page.begin_recovery()))
        self.state.open_in_playback.connect(
            lambda p: self._open_built("playback", lambda page: page.load_csv(Path(p))))
        self.state.follow_log.connect(
            lambda p: p and self._open_built("playback", lambda page: page.follow(Path(p))))
        self.state.open_in_maps.connect(
            lambda *args: self._open_built("maps", lambda page: page._open_pair(*args)))
        self.state.open_tab.connect(self.navigate)
        self.state.restart_app.connect(self._restart)
        self.state.modules_scanned.connect(self._scan_finished)
        self.state.request_scan.connect(lambda: self._ensure_page("connect")[0].scan())
        self.state.request_full_scan.connect(lambda: self._ensure_page("connect")[0].scan(full=True))
        self.state.faults_changed.connect(self._set_fault_count)
        self.state.faults_changed.connect(self._refresh_car)
        from . import cars
        cars.filler(self.state)  # every scan that reads a VIN keeps that car's profile up to date
        from . import projects
        projects.attach(self.state)  # new recordings go into their car's active project
        from . import car_watch
        car_watch.watch(self.state, self)  # reads the plugged-in car's VIN by itself
        self.state.car_changed.connect(self._refresh_car)

        # Select first tab
        self._switch_tab("home")

        # F11 (and ⌃⌘F on macOS) switches between full screen and a window.
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(  # ⌘K on a Mac
            lambda: self._ai_on() and self._open_assistant())
        for keys in (QKeySequence("F11"), QKeySequence(QKeySequence.StandardKey.FullScreen)):
            if not keys.isEmpty():
                QShortcut(keys, self).activated.connect(self.toggle_fullscreen)

        native = self.pool.native()
        native.call("hello", {}, self._hello, self._worker_broken)
        native.call("families", {}, self._families, self._worker_broken)
        self.listing = False
        self.refresh_interfaces()
        self.rescan = QTimer(self)
        self.rescan.setInterval(4000)
        self.rescan.timeout.connect(self._auto_refresh)
        self.rescan.start()
        QTimer.singleShot(300, self._build_next_page)

    # --- pages, built lazily ---

    def _ensure_page(self, key):
        """The page for *key*, building it now if it doesn't exist yet. Returns (page, new)."""
        page = self.pages.get(key)
        if page is not None:
            return page, False
        page = self._page_classes[key](self.state)
        self.pages[key] = page
        self.stack.addWidget(page)
        if key != "home" and hasattr(page, "refresh"):
            page.refresh()  # catch up on state changes that happened before it existed
        return page, True

    def _build_next_page(self):
        """Background build: one page per event-loop turn so the window stays responsive."""
        for key, *_ in NAV:
            if key not in self.pages:
                self._ensure_page(key)
                QTimer.singleShot(0, self._build_next_page)
                return
        from . import tooltips
        tooltips.apply(self)

    def _open_built(self, key, call, go=None):
        """Show *key*'s page; if it had to be built just now, make the call its own slot missed.
        A feature turned off in Settings → Features stays closed."""
        if key in pages.hidden_features(self.settings):
            return
        _page, new = self._ensure_page(key)
        (go or self._switch_tab)(key)
        if new:
            call(self.pages[key])

    def _restart(self, demo):
        if self.state.busy:
            MessageBox.warning(self, "PQ35 Flasher", "Wait for the current operation to finish.")
            return
        env = dict(os.environ)
        env.pop("PQ35_DEMO", None)
        args = [sys.executable, "-m", "pq35ui"] + (["--demo"] if demo else [])
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            subprocess.Popen(args, cwd=str(REPO_ROOT), env=env, creationflags=flags)
        except OSError as exc:
            MessageBox.critical(self, "PQ35 Flasher", f"Couldn't restart: {exc}")
            return
        self.close()

    # --- tab navigation ---

    def navigate(self, key):
        """Go to a tab (from its button or a link elsewhere). A locked tab can't be used yet, so
        explain why instead of showing a dead page. "Go to Connect" goes there; Escape or a
        click outside the notice just closes it and leaves you where you were."""
        if key in pages.hidden_features(self.settings):
            return  # turned off in Settings → Features
        button = self.tab_buttons.get(key)
        if button is not None and button.property("locked"):
            label = next((lbl for k, _i, lbl, _c in NAV if k == key), key)
            from html import escape
            from .disclaimer import confirm
            title, text = "Module needed", (
                f"<p><b>{escape(label)}</b> needs a module that supports it.</p>"
                "<p>Choose one on the <b>Connect</b> page first: <b>Scan</b>, then "
                "<b>Select</b>.</p>")
            go = confirm(self, title=title, icon="memory", level="info", decline=None,
                         accept="Go to Connect", closable=True, size="compact", text=text)
            if go:
                self._switch_tab("connect")
            return
        self._switch_tab(key)

    def _switch_tab(self, key):
        if self.mac and sys.platform == "darwin" and key in ("help", "settings"):
            from .mac_shell import open_side_window
            open_side_window(self, key, next((lbl for k, _i, lbl, _c in NAV if k == key), key))
            return  # their own windows; the main window stays on its page
        for k, btn in self.tab_buttons.items():
            active = k == key
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn._expanded_w = 0  # the active style can change the font: measure again
            btn.set_pinned(active)
        self._paint_tab_icons()
        self.stack.setCurrentWidget(self._ensure_page(key)[0])
        if self.mac_header is not None:
            from .mac_shell import COLUMN_PAGES
            self.mac_header.set_page(next((lbl for k, _i, lbl, _c in NAV if k == key), ""), self.pages.get(key))
            self.mac_header.setVisible(key not in COLUMN_PAGES)
            self.sidebar.window_resized()  # this page may need the room the names take
        if getattr(self, "_assistant", None) is not None and self._assistant.isVisible():
            from .assistant import refresh
            refresh(self)  # its hint and suggestions follow the page
        from . import tooltips
        tooltips.apply(self)  # fills any control still missing a tooltip (incl. ones built later)

    # --- top bar ---

    def _topbar(self):
        bar = QFrame()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        from .main import app_icon
        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(QSize(36, 36)))
        layout.addWidget(icon_label)
        from . import theme
        theme.on_apply(lambda: icon_label.setPixmap(app_icon().pixmap(QSize(36, 36))))  # follows the accent
        title = QLabel("PQ35 Flasher")
        title.setObjectName("title")
        layout.addWidget(title)
        if os.environ.get("PQ35_DEMO"):
            layout.addWidget(Badge("DEMO", "info"))
        # The car the last scan identified by its VIN (a scan on Connect).
        self.car_label = QLabel("")
        self.car_label.setObjectName("muted")
        self.car_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.car_label.installEventFilter(self)  # a click opens Cars (_open_cars)
        layout.addSpacing(8)
        layout.addWidget(self.car_label)
        layout.addStretch(1)
        self.interface_button = QToolButton()
        self.interface_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.interface_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.interface_button.setToolTip("CAN interface")
        self.interface_menu = QMenu(self)
        self.interface_button.setMenu(self.interface_menu)
        from .analyze_tools import anchor_menu
        anchor_menu(self.interface_menu, self.interface_button)
        self.interface_group = QActionGroup(self)
        layout.addWidget(self.interface_button)
        self.status = ElidedButton("No module", suffix="  \u25BE", tip="Switch module")
        self.status.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status.setObjectName("chip")
        self.status.clicked.connect(self._module_menu)
        layout.addWidget(self.status)
        return bar

    # --- window size / position ---

    def _restore_geometry(self):
        """Reopen where it was closed (size, position, maximized), like most apps.
        First run: a comfortable size that fits the screen, centred."""
        import base64
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QGuiApplication
        saved = self.settings.get("window_geometry")
        if saved:
            try:
                if self.restoreGeometry(QByteArray(base64.b64decode(saved))):
                    return
            except (ValueError, TypeError):
                pass
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        width, height = 1280, 820
        if area is not None:
            width, height = min(width, int(area.width() * 0.9)), min(height, int(area.height() * 0.9))
            self.resize(width, height)
            self.move(area.center().x() - width // 2, area.center().y() - height // 2)
        else:
            self.resize(width, height)

    def _save_geometry(self):
        import base64
        self.settings.set("window_geometry", base64.b64encode(bytes(self.saveGeometry())).decode("ascii"))

    # --- window mode ---

    def apply_window_mode(self, mode=None):
        """'fullscreen' or 'windowed' (the Settings choice; F11 toggles it)."""
        mode = mode or self.state.settings.get("window_mode", "windowed")
        if mode == "fullscreen":
            self.showFullScreen()
        else:
            self.showNormal()

    def toggle_fullscreen(self):
        mode = "windowed" if self.isFullScreen() else "fullscreen"
        self.state.settings.set("window_mode", mode)
        self.apply_window_mode(mode)
        settings_page = self.pages.get("settings")
        if settings_page is not None and hasattr(settings_page, "sync_window_mode"):
            settings_page.sync_window_mode()

    def _ai_on(self):
        """AI switched on in Settings. (Not "the ✦ tab is visible": the Mac interface hides the
        Classic tab bar, which left ⌘K doing nothing there.)"""
        from . import ai
        return ai.enabled(self.settings)

    def _open_assistant(self):
        """✦ in the tab bar (or ⌘K / Ctrl+K): the Assistant, which also sees what's on screen."""
        from .assistant import open_assistant
        open_assistant(self)

    def _tabbar(self):
        bar = QFrame()
        bar.setObjectName("tabbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(0)
        self._tab_layout = layout
        self._assistant_button()
        for key, icon_char, label, cls in NAV:
            btn = AnimatedTabButton(label)
            btn.setObjectName("tab")
            btn.setProperty("active", "false")
            btn.setProperty("icon_char", icon_char)
            btn.setProperty("tab_label", label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setIconSize(QSize(TAB_ICON, TAB_ICON))
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.tab_buttons[key] = btn
        self.layout_tabs()
        self._apply_tab_style()
        from . import theme
        theme.on_apply(self._paint_tab_icons)
        return bar

    def _new_divider(self):
        holder = QWidget()  # divider + its spacing, hidden together
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(6, 0, 6, 0)
        sep = QFrame()
        sep.setObjectName("tabsep")
        sep.setFixedSize(1, 26)
        hl.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
        return holder

    def layout_tabs(self):
        """Lay the tab bar out in the saved order (Settings → Appearance): left-side tabs and
        dividers as arranged, then the Assistant, Help and Settings at the right edge."""
        layout = self._tab_layout
        while layout.count():
            layout.takeAt(0)  # widgets stay; only their places are cleared
        dividers = iter(self._tab_seps)
        used = []
        for key in tab_order(self.settings):
            if key == DIVIDER:
                holder = next(dividers, None) or self._new_divider()
                if holder not in self._tab_seps:
                    self._tab_seps.append(holder)
                used.append(holder)
                layout.addWidget(holder)
            else:
                layout.addWidget(self.tab_buttons[key])
        for holder in self._tab_seps:
            if holder not in used:
                holder.hide()
        layout.addStretch(1)
        layout.addWidget(self.assistant_tab)
        keys = [k for k, *_ in NAV]
        for key in keys[keys.index(NAV_RIGHT_START):]:
            layout.addWidget(self.tab_buttons[key])
        self._dividers_in_use = used

    def _assistant_button(self):
        """Icon-only ✦ before Log/Help/Settings; hidden while AI is off in Settings."""
        from . import ai
        btn = AnimatedTabButton("Assistant")
        btn.setObjectName("tab")
        btn.setProperty("active", "false")
        btn.setProperty("icon_char", "\ue65f")  # auto_awesome
        btn.setProperty("labels", "false")
        btn._icons_mode = True
        btn.setMaximumWidth(AnimatedTabButton.COLLAPSED_W)
        btn.setIconSize(QSize(TAB_ICON, TAB_ICON))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        shortcut = "⌘K" if sys.platform == "darwin" else "Ctrl+K"
        btn.setToolTip(f"Assistant ({shortcut}): ask how to do something in PQ35 Flasher, or about your car. "
                       "It sees what's on screen, including the log, values or map on the page you're on.")
        btn.clicked.connect(self._open_assistant)
        btn.setVisible(ai.enabled(self.settings))
        ai.hub().enabled_changed.connect(btn.setVisible)
        self.assistant_tab = btn
        return btn

    def _apply_tab_style(self):
        """Tabs show an icon (name slides out on hover; the current tab stays open) or just their
        name (Settings → Tab Style)."""
        text = self.settings.get("tab_style", "icons") != "icons"  # old "both" counts as text
        # (Text tabs keep the icon tabs' height via min-height in theme.py, so the bar doesn't jump.)
        for key, btn in self.tab_buttons.items():
            btn._icons_mode = not text
            btn.setProperty("labels", "true" if text else "false")
            btn.setText(btn.property("tab_label") if text else "")
            btn.setMaximumWidth(16777215 if text else AnimatedTabButton.COLLAPSED_W)
            btn.setIconSize(QSize(TAB_ICON, TAB_ICON) if not text else QSize(TAB_DOT, TAB_DOT))
            btn._expanded_w = 0  # recalculated on next hover
            btn._anim.stop()
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            if not text and btn.property("active") == "true":
                btn.set_pinned(True)
        self._paint_tab_icons()

    def _paint_tab_icons(self):
        """Draw each tab icon in the current theme: normal, active, and a tinted greyed-out version
        (otherwise Qt makes its own plain grey one). Re-run on tab switch and every theme change."""
        from .theme import colors
        c = colors()
        for btn in list(self.tab_buttons.values()) + [getattr(self, "assistant_tab", None)]:
            if btn is None:
                continue
            glyph = btn.property("icon_char")
            if not glyph:
                continue
            if not btn._icons_mode:  # text tabs: no icon, only the fault dot on Dashboard
                dot = btn is self.tab_buttons.get(BADGE_TAB) and self.fault_count
                btn.setIcon(self._dot_icon() if dot else QIcon())
                continue
            size = btn.iconSize().width()
            active = btn.property("active") == "true"
            normal = c["tab_disabled"] if btn.property("locked") else c["tab_text"]
            icon = _material_icon(glyph, size, c["on_accent"] if active else normal)
            off = _material_icon(glyph, size, c["tab_disabled"]).pixmap(QSize(size, size))
            icon.addPixmap(off, QIcon.Mode.Disabled)
            if btn is self.tab_buttons.get(BADGE_TAB) and self.fault_count:
                icon = self._with_badge(icon, size)
            btn.setIcon(icon)
        if getattr(self, "sidebar", None) is not None:
            self.sidebar.sync()

    def _dot_icon(self):
        from PySide6.QtGui import QColor, QPainter, QPixmap
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(TAB_DOT * dpr), int(TAB_DOT * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.colors()["err"]))
        painter.drawEllipse(0, 0, TAB_DOT, TAB_DOT)
        painter.end()
        return QIcon(pix)

    def _with_badge(self, icon, size):
        """The icon with a small red dot in its top-right corner (stored fault codes)."""
        from PySide6.QtGui import QColor, QPainter
        out = QIcon()
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
            pix = icon.pixmap(QSize(size, size), self.devicePixelRatioF(), mode)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.colors()["err"]))
            dot = max(5.0, size * 0.36)
            painter.drawEllipse(size - dot, 0.0, dot, dot)
            painter.end()
            out.addPixmap(pix, mode)
        return out

    def _tab_badge_tip(self, key):
        """The fault dot's explanation: how many codes, on which modules, where to see them all."""
        if key != BADGE_TAB or not self.fault_count:
            return ""
        last = self.settings.get("health_last") or {}
        per = [f"{m.get('module', '?').split(' (')[0]} {len(m.get('list') or [])}"
               for m in last.get("modules") or [] if m.get("list")]
        return (f"{self.fault_count} fault code{'s' if self.fault_count != 1 else ''} at the last check"
                + (f": {', '.join(per)}" if per else "") + ".\nEvery module's codes are on the Dashboard.")

    def _set_fault_count(self, count):
        self.fault_count = count
        self._state_changed()  # tooltips (the fault dot's tab may be locked)

    def _scan_finished(self, result):
        """Every scan reads every module's codes: hand them to the fault controller."""
        from .health import controller
        QTimer.singleShot(0, lambda: controller(self.state).load_full_scan(result))

    def refresh_interfaces(self):
        if self.listing:
            return
        self.listing = True
        self.pool.native().call("list_interfaces", {}, self._interfaces, self._listing_failed)

    def _auto_refresh(self):
        if not self.state.busy and self.isActiveWindow():
            self.refresh_interfaces()

    def _listing_failed(self, error):
        self.listing = False
        if (error or {}).get("type") == "WorkerDied":
            return  # restarted (or crashed) mid-listing: the next refresh starts a fresh one
        self._worker_broken(error)

    def _interfaces(self, interfaces):
        self.listing = False
        if interfaces == self.interfaces and self.interface_menu.actions():
            return
        self.interfaces = interfaces
        current = self.state.adapter or self.settings.get("interface")
        if not isinstance(current, dict):
            current = None  # a damaged setting: just pick the first interface found
        selected = next((i for i, a in enumerate(interfaces) if self.state.same_adapter(a, current)),
                        0 if interfaces else -1)
        self.interface_menu.clear()
        for action in self.interface_group.actions():
            self.interface_group.removeAction(action)
        for index, interface in enumerate(interfaces):
            action = QAction(interface["label"], self, checkable=True)
            action.setChecked(index == selected)
            action.triggered.connect(lambda _=False, i=index: self._interface_picked(i))
            self.interface_group.addAction(action)
            self.interface_menu.addAction(action)
        if not interfaces:
            none = self.interface_menu.addAction("No interface found")
            none.setEnabled(False)
        self.interface_menu.addSeparator()
        self.interface_menu.addAction("Look for interfaces again", self.refresh_interfaces)
        self._interface_picked(selected)

    def _interface_picked(self, index):
        adapter = self.interfaces[index] if 0 <= index < len(self.interfaces) else None
        if adapter:
            self.settings.set("interface", adapter)
        self.state.set_adapter(adapter)
        self.interface_button.setText((adapter["label"] if adapter else "No interface") + "  \u25BE")
        self._state_changed()

    def _families(self, families):
        self.state.families = families
        self.state.changed.emit()

    def _connect_needed(self, _operation):
        """Something that talks to the car was pressed with no interface plugged in."""
        if getattr(self, "_connect_prompt_open", False):
            return
        from .disclaimer import confirm
        self._connect_prompt_open = True
        try:
            go = confirm(self, title="Connect first", icon="settings_ethernet", level="info",
                         decline=None, accept="Go to Connect", closable=True, size="compact",
                         text="<p>That needs the car. Plug in your adapter and turn the ignition on; "
                              "it's found on its own.</p><p>Everything else (channels, presets, "
                              "alarms, logs) works without it.</p>")
        finally:
            self._connect_prompt_open = False
        if go:
            self.navigate("connect")

    def _hello(self, info):
        self.state.hello = info
        python = ".".join(str(info.get("python", "?")).split(".")[:2])
        self.runtime_label.setText(f"flasher {(info.get('flasher_commit') or 'built in')[:8]} \u00B7 Py {python}")
        self.state.changed.emit()

    def _worker_broken(self, error):
        if getattr(self, "worker_error_shown", False):
            return
        self.worker_error_shown = True
        MessageBox.critical(self, "PQ35 Flasher",
                             "The background worker couldn't start:\n\n" + error.get("message", "")
                             + "\n\nTry re-running the installer.")

    # --- module switcher (top-bar chip) ---

    def _module_menu(self):
        """The modules the last scan found, to switch between from any page. Choosing works
        exactly like Select on Connect (which stays the place to find and recover them)."""
        menu = QMenu(self)
        self.fill_module_menu(menu)
        from .analyze_tools import popup_menu
        popup_menu(menu, self.status)

    def fill_module_menu(self, menu):
        """The module menu's items (Classic: the module button; Mac: the connection card)."""
        state = self.state
        connect = self.pages.get("connect")
        entries = (state.last_scan or {}).get("result") or []
        found = [e for e in entries if e.get("status") == "ok"]
        stuck = [e for e in entries if e.get("status") in ("bootloader", "responding")]
        current = state.family["name"] if state.family else None
        if found:
            title = menu.addAction("Switch module")
            title.setEnabled(False)
            for entry in found:
                family = state.family_by_name(entry["family"])
                if family is None:
                    continue
                ident = entry.get("identity") or {}
                part = ident.get("software_part_number") or ident.get("part_number") or ""
                codes, active = state.fault_summary.get(entry["family"], (0, 0))
                text = family["label"] + (f"  ·  {part}" if part else "")
                if codes:
                    text += f"  ·  ⚠ {codes} fault code{'s' if codes != 1 else ''}"
                action = menu.addAction(text)
                action.setCheckable(True)
                action.setChecked(entry["family"] == current)
                action.triggered.connect(lambda _=False, f=family, e=entry: connect.choose(f, e))
            for entry in stuck:
                family = state.family_by_name(entry["family"])
                label = family["label"] if family else entry["family"]
                menu.addAction(f"{label}  ·  in its loader: recover on Connect…").triggered.connect(
                    lambda: self.navigate("connect"))
            menu.addSeparator()
            again = menu.addAction("Scan again")
            again.triggered.connect(lambda: (self.navigate("connect"), connect.scan()))
            again.setEnabled(bool(state.adapter))
        else:
            find = menu.addAction("Scan…")
            find.triggered.connect(lambda: (self.navigate("connect"), connect.scan()))
            find.setEnabled(bool(state.adapter))
        menu.addAction("Open Connect").triggered.connect(lambda: self.navigate("connect"))

    # --- state ---

    # Gearbox from the transmission ECU's part number prefix.
    GEARBOXES = {"02E": "DSG 6-speed (DQ250)", "0AM": "DSG 7-speed (DQ200)", "0D9": "DSG 6-speed (DQ250)",
                 "09G": "6-speed automatic (09G)", "09M": "6-speed automatic (09M)"}

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "car_label", None) and event.type() == QEvent.Type.MouseButtonRelease \
                and event.button() == Qt.MouseButton.LeftButton:
            self._open_cars()
            return True
        return super().eventFilter(obj, event)

    def _open_cars(self):
        from . import cars
        from .cars_dialog import CarsDialog
        CarsDialog(self.state, cars.filler(self.state), parent=self).exec()

    def _refresh_car(self, *_):
        """Top-bar car label + tooltip: only while connected and read from the car this session
        (the VIN is remembered for naming logs)."""
        state = self.state
        car = (state.vehicle or {}) if state.vin() else {}  # the car connected now (VIN read this run)
        if not car.get("vin"):
            self.car_label.setText("")
            self.car_label.setToolTip("")
            return
        self.car_label.setText(f"{car.get('label', '')}  \u00B7  {car['vin']}")
        lines = [f"VIN {car['vin']}"] + [f"{k.title()}: {car[k]}" for k in ("make", "model", "year", "plant")
                                         if car.get(k)]
        entries = {e["family"]: e for e in (state.last_scan or {}).get("result") or []}
        found = [e for e in entries.values() if e.get("status") == "ok"]

        def ident(name):
            e = entries.get(name) or {}
            return (e.get("identity") or {}) if e.get("status") == "ok" else {}

        engine = ident("engine")
        if engine:
            part = (engine.get("software_part_number") or engine.get("part_number") or "").strip()
            desc = str(engine.get("system_desc", "")).replace("(demo)", "").strip()
            sw = str(engine.get("sw_version", "")).strip()
            lines.append("Engine: " + " \u00B7 ".join(x for x in (desc, part + (f" sw {sw}" if sw else "")) if x.strip()))
            km = engine.get("mileage_km")
            if km:
                from . import units
                miles = f" ({km * 0.621371:,.0f} mi)" if units.system(self.settings) == "imperial" else ""
                lines.append(f"Mileage: {km:,} km{miles} (engine ECU)")
        gearbox = ident("transmission")
        if gearbox:
            part = (gearbox.get("software_part_number") or gearbox.get("part_number") or "").strip()
            name = self.GEARBOXES.get(part[:3].upper()) or str(gearbox.get("system_desc", "")).replace("(demo)", "").strip()
            lines.append(f"Gearbox: {name}" + (f" \u00B7 {part}" if part else ""))
        elif "transmission" in entries:
            lines.append("Gearbox: manual (no transmission module answered)")
        if found:
            faulty = sum(1 for codes, _a in state.fault_summary.values() if codes)
            text = f"Modules: {len(found)} found"
            if state.fault_summary:
                text += f" \u00B7 {faulty} with fault codes" if faulty else " \u00B7 no fault codes"
            lines.append(text)
        self.car_label.setToolTip("\n".join(lines + ["", "Click to open Cars: your saved car profiles"]))

    def _state_changed(self):
        state = self.state
        if state.family and state.identity:
            text, level = (f"{state.family['label']} \u00B7 {state.part()} "
                           f"{state.identity.get('sw_version', '')}"), "ok"
        elif state.family:
            text, level = f"{state.family['label']} \u00B7 not identified", "idle"
        else:
            text, level = "No module", "idle"
        self.status.setText(text)
        self._refresh_car()
        self.status.setProperty("level", level)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        has_family = state.family is not None
        enabled = {
            "connect": True,
            "backup": has_family and (bool(state.identity)
                                      or state.family["name"] not in pages.BACKUP_FAMILIES),
            "flash": has_family and state.family.get("can_flash", False),
            "patches": has_family and state.family["name"] in (pages.EPS, pages.HALDEX),
            # The module you're working on (every module's faults are on the Dashboard).
            # Every module's scan and faults, with or without a chosen module.
            "diagnostics": True,
            # set up unplugged on the last-used module (nothing shows as picked on Connect)
            "logger": bool(state.setup_family and state.setup_family.get("can_log", False)),
            "liveview": bool(state.setup_family and state.setup_family.get("can_log", False)),
        }
        # Hide/show tabs: turned off in Settings → Features, or (Back up) not for this module
        hidden = pages.hidden_features(self.settings)
        backup_visible = has_family and state.family["name"] in pages.BACKUP_CAPABLE
        for key, btn in self.tab_buttons.items():
            btn.setVisible(key not in hidden and (key != "backup" or backup_visible))
        # A divider shows only between two visible tabs (never doubled, never at an end).
        pending, seen_tab = None, False
        dividers = iter(getattr(self, "_dividers_in_use", []))
        for key in tab_order(self.settings):
            if key == DIVIDER:
                holder = next(dividers, None)
                if holder is None:
                    continue
                holder.hide()
                if seen_tab and pending is None:
                    pending = holder
            elif not self.tab_buttons[key].isHidden():
                if pending is not None:
                    pending.show()
                    pending = None
                seen_tab = True
        for page in list(self.pages.values()):
            if hasattr(page, "features_changed"):
                page.features_changed(hidden)
        # Tabs that need a module are "locked" rather than disabled: dimmed (in the theme's tinted
        # grey, icon and text alike) but still clickable, and clicking one explains what's needed.
        for key, btn in self.tab_buttons.items():
            locked = not enabled.get(key, True)
            if btn.property("locked") != locked:
                btn.setProperty("locked", locked)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            need = "choose a module on Connect"
            badge = self._tab_badge_tip(key)
            btn.setToolTip("\n".join(t for t in (f"{btn.property('tab_label')}: {need} first" if locked
                                                   else btn.property("tab_label") if badge else "", badge) if t))
        self._paint_tab_icons()
        # If current tab is hidden or disabled, go back to Connect
        current_key = next((k for k, b in self.tab_buttons.items()
                            if b.property("active") == "true"), "connect")
        if self.tab_buttons[current_key].isHidden() or self.tab_buttons[current_key].property("locked"):
            self._switch_tab("connect")

    # --- updates ---

    def _update_clicked(self):
        info = self.update_info or {}
        if info.get("status") in ("behind", "unknown_local"):
            self._run_update(info)
            return
        self.update_button.setEnabled(False)
        self.update_button.setText("Checking\u2026")
        self.update_status.setText("")
        self.checker.start(self.local_version)

    def _update_checked(self, info):
        self.update_info = info
        self.update_button.setEnabled(not self.state.busy)
        self.update_button.setObjectName("")
        status = info.get("status")
        if status == "behind":
            count = info.get("behind_by") or len(info.get("changes", []))
            self.update_status.setText(f"{count} update{'s' if count != 1 else ''} available")
            self.update_button.setText("Update now")
            self.update_button.setObjectName("primary")
        elif status == "unknown_local":
            self.update_status.setText("Can't tell which version this is.")
            self.update_button.setText("Update now")
        elif status == "identical":
            self.update_status.setText("Up to date")
            self.update_button.setText("Check again")
        elif status in ("ahead", "local_only"):
            self.update_status.setText("Development version (newer than GitHub)")
            self.update_button.setText("Check again")
        elif status == "diverged":
            self.update_status.setText("Local changes. Update from a terminal.")
            self.update_button.setText("Check again")
        else:
            self.update_status.setText("Couldn't reach GitHub.")
            self.update_button.setToolTip(info.get("error", ""))
            self.update_button.setText("Try again")
        self.update_button.style().unpolish(self.update_button)
        self.update_button.style().polish(self.update_button)

    def _run_update(self, info):
        if self.state.busy:
            MessageBox.warning(self, "Update", "Wait for the current operation to finish first.")
            return
        changes = info.get("changes") or []
        listing = "\n".join(f"\u2022 {c}" for c in changes[:12])
        if len(changes) > 12:
            listing += f"\n\u2022 \u2026and {len(changes) - 12} more"
        answer = MessageBox.question(
            self, "Update PQ35 Flasher",
            "PQ35 Flasher will close, update and reopen."
            + (f"\n\nWhat's new:\n{listing}" if listing else ""))
        if answer != MessageBox.StandardButton.Yes:
            return
        try:
            note = updates.launch_update()
        except OSError as exc:
            MessageBox.critical(self, "Update", f"Couldn't start the updater: {exc}")
            return
        self.update_status.setText(note)
        self.close()

    def _busy_changed(self, busy):
        self.update_button.setEnabled(not busy)
        for widget in (self.interface_button, self.status):
            widget.setEnabled(not busy)
        # While a job runs the tabs are locked, so nothing else can start talking to the module.
        # A recording (Logger or Live View) only reads, and you need to get back to its Stop
        # (e.g. from Analyze's live graph): those pages, Analyze and Help stay open.
        open_tabs = BUSY_OPEN_TABS.get(self.state.operation, ()) if busy else ()
        for key, btn in self.tab_buttons.items():
            btn.setEnabled(not busy or key in open_tabs)
        self.busy_bar.setVisible(busy)
        self.busy_bar.setValue(0)
        if busy:
            self.sleep.hold()
            self.status.setText("Working\u2026")
        else:
            self.sleep.release()
            self._state_changed()
            if getattr(self, "close_when_idle", False):
                QTimer.singleShot(0, self.close)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "sidebar", None) is not None:
            self.sidebar.window_resized(self.width())

    def showEvent(self, event):
        super().showEvent(event)
        if self.mac and not getattr(self, "_full_size", False):
            from .mac_shell import full_size_content
            self._full_size = True
            full_size_content(self)  # title hidden (the content itself goes under it: see __init__)

    def closeEvent(self, event):
        logger = self.pages.get("logger")
        liveview = self.pages.get("liveview")
        dyno = self.pages.get("dyno")
        logger_running = logger is not None and logger.running
        liveview_running = liveview is not None and liveview.running
        dyno_running = dyno is not None and dyno.recorder.running
        if logger_running or liveview_running or dyno_running:
            self.close_when_idle = True
            if logger_running:
                logger.stop()
            if liveview_running:
                liveview._stop()
            if dyno_running:
                dyno.recorder.stop()  # saves the pulls recorded so far
            event.ignore()
            return
        if self.state.busy:
            MessageBox.warning(self, "PQ35 Flasher",
                                "Something is talking to the module right now. Closing would "
                                "interrupt it. Wait for it to finish.")
            event.ignore()
            return
        self._save_geometry()
        self.pool.stop()
        event.accept()
