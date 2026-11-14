import ctypes
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QLockFile, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon,
                            QImage, QKeySequence, QLinearGradient, QPainter,
                            QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import QApplication, QMessageBox

from . import theme

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
from .storage import check_writable, data_dir, logs_dir, migrate_old_library


FOLDER_HELP = {
    "darwin": "On macOS, allow PQ35 Flasher (or Python) under System Settings > Privacy & "
              "Security > Files and Folders > Documents, then press Try again.",
    "win32": "On Windows, Controlled folder access (Windows Security > Virus & threat "
             "protection > Ransomware protection) can block apps from Documents; allow the "
             "app there, then press Try again.",
}


def _generate_icon_pixmap(size: int, accent: str = "#e8820c") -> QPixmap:
    """Generate the app icon at *size* px using the given accent color."""
    c = QColor(accent)
    # Derive top/bottom gradient from accent — saturated, iOS-flat style
    top = QColor.fromHslF(c.hslHueF(), min(c.hslSaturationF() + 0.1, 1.0),
                          min(c.lightnessF() + 0.08, 0.62))
    bot = QColor.fromHslF(c.hslHueF(), min(c.hslSaturationF() + 0.15, 1.0),
                          max(c.lightnessF() - 0.08, 0.35))
    bolt_color = QColor.fromHslF(c.hslHueF(), min(c.hslSaturationF() + 0.1, 1.0),
                                 max(c.lightnessF() - 0.05, 0.30))

    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    # macOS-style rounded "squircle" with a transparent margin, so the icon has
    # the expected rounded corners (and Dock spacing) instead of a full square.
    pad = round(size * 0.096)
    s = size - 2 * pad
    radius = s * 0.2237  # Apple's corner-radius ratio
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(pad, pad, s, s), radius, radius)
    p.setClipPath(clip)
    p.translate(pad, pad)

    # Background gradient
    grad = QLinearGradient(0, 0, 0, s)
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bot)
    p.fillRect(0, 0, s, s, grad)

    # Subtle top sheen
    sheen = QLinearGradient(0, 0, 0, s * 0.4)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 25))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, s, int(s * 0.4), sheen)

    # Chip
    cs = int(s * 0.68)
    cx = (s - cs) // 2
    cy = (s - cs) // 2
    cr = int(s * 0.045)

    # Pins (4 per side) — extend to the rounded icon edge
    ph = int(s * 0.05)
    p.setPen(QPen(QColor(0, 0, 0, 0)))
    p.setBrush(QBrush(QColor(255, 255, 255, 100)))
    gap = cs / 5
    for i in range(4):
        off = int(gap * (i + 1) - ph / 2)
        # Top pins: from top edge (0) to chip top
        p.drawRoundedRect(cx + off, 0, ph, cy, 4, 4)
        # Bottom pins: from chip bottom to bottom edge
        p.drawRoundedRect(cx + off, cy + cs, ph, s - (cy + cs), 4, 4)
        # Left pins: from left edge (0) to chip left
        p.drawRoundedRect(0, cy + off, cx, ph, 4, 4)
        # Right pins: from chip right to right edge
        p.drawRoundedRect(cx + cs, cy + off, s - (cx + cs), ph, 4, 4)

    # Chip body
    p.setBrush(QBrush(QColor(255, 255, 255, 210)))
    p.setPen(QPen(QColor(0, 0, 0, 0)))
    p.drawRoundedRect(cx, cy, cs, cs, cr, cr)

    # Inner highlight
    ig = QLinearGradient(cx, cy, cx, cy + cs * 0.15)
    ig.setColorAt(0.0, QColor(255, 255, 255, 50))
    ig.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(cx, cy, cs, int(cs * 0.15), ig)

    # Bolt glyph (Material Symbols Rounded)
    bf = QFont("Material Symbols Rounded", int(s * 0.34))
    bf.setHintingPreference(QFont.PreferNoHinting)
    p.setFont(bf)
    p.setPen(QPen(bolt_color))
    p.drawText(QRectF(cx, cy, cs, cs), Qt.AlignCenter, '\uea0b')

    p.end()
    return QPixmap.fromImage(img)


def app_icon(accent: str | None = None) -> QIcon:
    """Build the app icon using the current accent color."""
    if accent is None:
        accent = theme.colors().get("accent", "#e8820c")
    cached = _ICON_CACHE.get(accent)
    if cached is not None:
        return cached
    icon = QIcon()
    for sz in (1024, 512, 256, 128, 64, 32, 16):
        icon.addPixmap(_generate_icon_pixmap(sz, accent))
    _ICON_CACHE[accent] = icon
    return icon


_ICON_CACHE = {}  # accent -> QIcon; drawing all sizes takes ~0.1 s and it's asked for often


def crash_file() -> Path:
    """Somewhere a crash report can always be written."""
    for folder in (logs_dir, data_dir):
        try:
            return folder() / f"crash_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / f"pq35-flasher-crash_{time.strftime('%Y%m%d-%H%M%S')}.txt"


def install_crash_handler():
    """No console behind the app window: show unexpected errors instead of losing them."""
    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        path = crash_file()
        try:
            path.write_text(text, encoding="utf-8")
            saved = f"Details were saved to:\n{path}"
        except OSError:
            saved = "The details couldn't be saved:\n\n" + text[-1500:]
        sys.__excepthook__(kind, value, tb)
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "PQ35 Flasher",
                                 f"Something went wrong: {value}\n\n{saved}\n\n"
                                 "Include that file if you report this (Help → Troubleshooting & logs → Open logs folder).")
    sys.excepthook = hook


def folders_ok() -> bool:
    """Refuse to start half-broken: say which folder can't be written, and how to fix it."""
    while True:
        problems = check_writable()
        if not problems:
            return True
        box = QMessageBox(QMessageBox.Icon.Critical, "PQ35 Flasher",
                          "PQ35 Flasher can't write to the folders it needs, so it can't keep "
                          "backups, logs or settings:\n\n" + "\n".join(problems)
                          + ("\n\n" + FOLDER_HELP[sys.platform] if sys.platform in FOLDER_HELP else ""))
        retry = box.addButton("Try again", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not retry:
            return False


def main():
    if "--demo" in sys.argv:
        # Set before anything reads the data dir; workers inherit it.
        os.environ["PQ35_DEMO"] = "1"
    install_crash_handler()
    if sys.platform == "win32":
        # Group under our own taskbar entry and icon instead of python.exe's.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DSparks156x.PQ35Flasher")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    # Load bundled Material Symbols Rounded font before anything draws with it (the icon
    # below does; a missing family makes Qt search every installed font first).
    mat_font = str(FONTS_DIR / "MaterialSymbolsRounded.ttf")
    if Path(mat_font).exists():
        QFontDatabase.addApplicationFont(mat_font)
    app.setWindowIcon(app_icon())
    from .widgets import AppEventFilter
    app_filter = AppEventFilter(app)  # wheel guard + rounded popups, one filter
    app.installEventFilter(app_filter)
    app.setApplicationName("PQ35 Flasher")
    app.setOrganizationName("PQ35")
    theme.apply(app)  # initial apply before settings are available
    app.styleHints().colorSchemeChanged.connect(lambda *_: theme.apply(app))

    if not folders_ok():
        return 1
    lock = QLockFile(str(data_dir() / "ui.lock"))
    if not lock.tryLock(100):
        QMessageBox.warning(None, "PQ35 Flasher", "PQ35 Flasher is already open.")
        return 1

    not_moved = migrate_old_library()
    if not_moved:
        QMessageBox.warning(None, "PQ35 Flasher",
                            "Some module records couldn't be moved to Documents/PQ35 Flasher/"
                            "Modules. They're still in the old folder:\n\n" + "\n".join(not_moved))
    from .main_window import MainWindow
    from .storage import Settings
    early_settings = Settings()
    theme.set_settings(early_settings)
    theme.apply(app)  # re-apply with user preferences
    window = MainWindow()
    # Settings → Window: full screen or windowed. This also shows the window; showing it
    # windowed first and then going full screen would run two macOS transitions.
    window.apply_window_mode()

    # Hot-reload: watch theme.py and re-apply the stylesheet on save.
    theme_file = str(Path(__file__).resolve().parent / "theme.py")
    watcher = QFileSystemWatcher([theme_file], app)

    def _reload_theme():
        import importlib
        try:
            importlib.reload(theme)
            theme.apply(app)
            app.setWindowIcon(app_icon())
            window.setWindowIcon(app_icon())
            print("[hot-reload] Theme reloaded")
        except Exception as exc:
            print(f"[hot-reload] Failed: {exc}")
        # macOS drops the watch after a write; re-add it.
        if theme_file not in watcher.files():
            watcher.addPath(theme_file)

    watcher.fileChanged.connect(lambda *_: _reload_theme())
    QShortcut(QKeySequence("F5"), window).activated.connect(_reload_theme)

    def acknowledge():
        from .disclaimer import should_show_caution, startup_caution
        if should_show_caution(window.settings) and not startup_caution(window, window.settings):
            window.close()

    # The caution over the dimmed window on every start, unless "Don't show this again" was ticked
    # (Settings → General brings it back). It's drawn inside the main window (no separate
    # dialog window), so it can't knock macOS out of full screen and needn't wait for the
    # full-screen animation.
    QTimer.singleShot(0, acknowledge)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
