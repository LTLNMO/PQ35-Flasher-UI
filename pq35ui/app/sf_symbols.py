"""Apple's SF Symbols as Qt icons (macOS 11+), for the Mac interface.

macOS draws the symbol (NSImage imageWithSystemSymbolName), it comes back as PNG bytes and Qt
tints it to the colour asked for. Returns None when that isn't possible (another system, an
older macOS, a name this macOS doesn't have), so callers fall back to the Material icon."""
import ctypes
import ctypes.util
import sys

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

_RT = None
_CACHE = {}

# NSFontWeight values
WEIGHTS = {"light": -0.4, "regular": 0.0, "medium": 0.23, "semibold": 0.3}


def _runtime():
    global _RT
    if _RT is not None:
        return _RT or None
    _RT = False
    if sys.platform != "darwin":
        return None
    try:
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        addr = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
        vp = ctypes.c_void_p
        _RT = {
            "cls": lib.objc_getClass, "sel": lib.sel_registerName,
            "id0": ctypes.CFUNCTYPE(vp, vp, vp)(addr),
            "id1": ctypes.CFUNCTYPE(vp, vp, vp, vp)(addr),
            "id2": ctypes.CFUNCTYPE(vp, vp, vp, vp, vp)(addr),
            "str": ctypes.CFUNCTYPE(vp, vp, vp, ctypes.c_char_p)(addr),
            "conf": ctypes.CFUNCTYPE(vp, vp, vp, ctypes.c_double, ctypes.c_double)(addr),
            "rep": ctypes.CFUNCTYPE(vp, vp, vp, ctypes.c_ulong, vp)(addr),
            "ulong": ctypes.CFUNCTYPE(ctypes.c_ulong, vp, vp)(addr),
        }
    except Exception:
        _RT = False
    return _RT or None


def _png(name, point_size, weight):
    """PNG bytes of the symbol drawn in black at point_size, or None."""
    rt = _runtime()
    if rt is None:
        return None
    cls, sel = rt["cls"], rt["sel"]
    pool = rt["id0"](rt["id0"](cls(b"NSAutoreleasePool"), sel(b"alloc")), sel(b"init"))
    try:
        ns_name = rt["str"](cls(b"NSString"), sel(b"stringWithUTF8String:"), name.encode())
        image = rt["id2"](cls(b"NSImage"), sel(b"imageWithSystemSymbolName:accessibilityDescription:"),
                          ns_name, None)
        if not image:
            return None
        conf = rt["conf"](cls(b"NSImageSymbolConfiguration"), sel(b"configurationWithPointSize:weight:"),
                          float(point_size), WEIGHTS.get(weight, 0.0))
        if conf:
            image = rt["id1"](image, sel(b"imageWithSymbolConfiguration:"), conf) or image
        tiff = rt["id0"](image, sel(b"TIFFRepresentation"))
        if not tiff:
            return None
        bitmap = rt["id1"](cls(b"NSBitmapImageRep"), sel(b"imageRepWithData:"), tiff)
        if not bitmap:
            return None
        png = rt["rep"](bitmap, sel(b"representationUsingType:properties:"), 4, None)  # 4 = PNG
        if not png:
            return None
        length = rt["ulong"](png, sel(b"length"))
        data = rt["id0"](png, sel(b"bytes"))
        return ctypes.string_at(data, length) if data and length else None
    except Exception:
        return None
    finally:
        rt["id0"](pool, sel(b"drain"))


# Symbols shaped like lower-case text (f(x)) look ~20 % smaller than boxy ones at the same point
# size (measured ink: function 28 px tall vs 35 for square.grid.2x2 / sidebar.left at 38 pt).
# Toolbars draw these larger, in a box this much wider: name -> (point scale, box width / height).
OPTICAL_SCALE = {"function": (1.25, 1.45)}


def icon(name, size, color, weight="regular", point=None, width=None):
    """A QIcon of SF Symbol *name* in a *size* px square, in *color*; None if unavailable.
    Drawn at one point size (default 0.8 x size) and centred, never stretched to the box: like
    AppKit, so a wide symbol (f(x)) and a tall one (bookmark) read as the same size of text.
    point: the symbol's point size, for a small glyph in a bigger box (dropdown chevrons).
    width: a wider box than *size* (a wide symbol drawn larger, see OPTICAL_SCALE)."""
    key = (name, size, color, weight, point, width)
    if key in _CACHE:
        return _CACHE[key]
    from PySide6.QtWidgets import QApplication
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    # Symbols are drawn larger than their point size suggests; ~0.8 keeps them the size of the
    # Material icons they replace.
    data = _png(name, (point or size * 0.8) * dpr, weight)
    result = None
    if data:
        img = QImage()
        if img.loadFromData(QByteArray(data), "PNG") and not img.isNull():
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            p = QPainter(img)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(img.rect(), QColor(color))
            p.end()
            real = int(size * dpr)
            real_w = int((width or size) * dpr)
            canvas = QPixmap(real_w, real)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scaled = img
            if img.width() > real_w or img.height() > real:  # only ever shrink, to fit
                scaled = img.scaled(QSize(real_w, real), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            p.drawImage((real_w - scaled.width()) // 2, (real - scaled.height()) // 2, scaled)
            p.end()
            canvas.setDevicePixelRatio(dpr)
            result = QIcon(canvas)
    _CACHE[key] = result
    return result


def symbol_file(name, size, color, weight="semibold", point=None):
    """The symbol saved as a PNG (for stylesheet url()s such as a combo box's arrow); path or None.
    Saved at 2x with an @2x name, so Qt draws it sharp on Retina screens."""
    import os
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "pq35-sf-symbols")
    path = os.path.join(folder, f"{name}-{size}-{color.lstrip('#')}-{weight}-{point}@2x.png")
    if os.path.exists(path):
        return path
    data = _png(name, (point or size * 0.8) * 2, weight)
    if not data:
        return None
    img = QImage()
    if not img.loadFromData(QByteArray(data), "PNG") or img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(img.rect(), QColor(color))
    p.end()
    real = size * 2
    canvas = QImage(real, real, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.drawImage((real - img.width()) // 2, (real - img.height()) // 2, img)
    p.end()
    try:
        os.makedirs(folder, exist_ok=True)
        canvas.save(path, "PNG")
    except OSError:
        return None
    return path


# Sidebar pages → SF Symbol names (as in the mockup)
NAV_SYMBOLS = {
    "home": "square.grid.2x2", "projects": "folder", "connect": "cable.connector",
    "backup": "externaldrive", "flash": "bolt", "patches": "puzzlepiece.extension",  # backups are local files, not iCloud
    "diagnostics": "stethoscope", "liveview": "gauge.open.with.lines.needle.33percent",
    "logger": "chart.line.uptrend.xyaxis", "playback": "chart.bar.xaxis", "dyno": "speedometer",
    "maps": "tablecells", "help": "questionmark.circle", "settings": "gearshape",
    "assistant": "sparkles",
}
# Older macOS versions lack some: try these in turn.
FALLBACKS = {"cable.connector.horizontal": ("cable.connector", "powerplug", "link"),
             "gauge.open.with.lines.needle.33percent": ("gauge.with.needle", "gauge"),
             "chart.line.uptrend.xyaxis": ("chart.xyaxis.line", "waveform.path.ecg"), "puzzlepiece.extension": ("puzzlepiece", "square.on.square"),
             "gauge.with.dots.needle.33percent": ("gauge", "speedometer"), "chart.xyaxis.line": ("waveform.path.ecg",)}


def nav_icon(key, size, color):
    name = NAV_SYMBOLS.get(key)
    if not name:
        return None
    for candidate in (name,) + FALLBACKS.get(name, ()):
        result = icon(candidate, size, color)
        if result is not None:
            return result
    return None


# The Mac interface draws the app's Material icons as the nearest SF Symbol (main_window._material_icon).
MATERIAL_TO_SF = {
    # The last Material-only icons (2026-11-22), each checked to exist and not to mean
    # something else already (one icon, one meaning).
    "autorenew": "arrow.triangle.2.circlepath",           # Logger: Rolling / Keep last
    "notifications": "bell",                              # Set alarm…, alarm card
    "height": "arrow.up.and.down",                        # Axis, Scale
    "open_with": "arrow.up.and.down.and.arrow.left.and.right",  # Axes and mouse
    "mouse": "computermouse",                             # Follow mouse
    "analytics": "chart.bar.doc.horizontal",              # Min / Max / Avg use
    "view_carousel": "capsule.on.rectangle",              # Show smart preset chips
    "restart_alt": "arrow.counterclockwise",              # Reset columns
    "low_priority": "arrow.up.arrow.down",                # Split: Move … to
    "splitscreen": "rectangle.split.1x2",                 # Split: Row n (same as Split view)
    "fiber_manual_record": "circle.fill",                 # record
    "lock": "lock",                                       # app lock card
    "\ue945": "line.3.horizontal",                       # drag_indicator: Split view row grip
    "close": "xmark", "ios_share": "square.and.arrow.up", "delete": "trash", "more_vert": "ellipsis",
    "arrow_drop_down": "chevron.down", "file_open": "doc", "clear_all": "xmark.circle", "add": "plus",
    "tune": "slider.horizontal.3", "history": "clock.arrow.circlepath", "function": "function",
    "fit_screen": "arrow.up.left.and.arrow.down.right", "bookmark_add": "bookmark", "bookmark": "bookmark",
    "auto_awesome": "sparkles", "refresh": "arrow.clockwise", "left_panel_open": "sidebar.left",
    "left_panel_close": "sidebar.left", "instant_mix": "slider.vertical.3", "frame_inspect": "camera.viewfinder",
    "edit": "pencil", "content_copy": "doc.on.doc", "checklist": "checklist", "visibility_off": "eye.slash",
    "visibility": "eye", "usb": "cable.connector", "upload": "square.and.arrow.up", "toc": "list.bullet.indent",
    "table_chart": "tablecells", "stacked_line_chart": "chart.xyaxis.line", "settings_ethernet": "cable.connector.horizontal",
    "settings": "gearshape", "search": "magnifyingglass", "palette": "paintpalette", "database": "cylinder.split.1x2", "save_as": "bookmark.fill", "notes": "text.alignleft",
    "memory": "cpu", "list_alt": "list.bullet.rectangle", "list": "list.bullet", "info": "info.circle",
    "folder_open": "folder", "filter_center_focus": "scope", "expand_more": "chevron.down", "chevron_left": "chevron.left", "chevron_right": "chevron.right",
    "download": "square.and.arrow.down", "done_all": "checklist.checked", "check_circle": "checkmark.circle", "directions_car": "car",
    "description": "doc.text", "compare": "rectangle.split.2x1", "call_split": "arrow.triangle.branch",
    # avg_pace = the Values button (which readings each channel shows): user pick 2026-11-23
    "avg_pace": "squares.below.rectangle", "arrow_upward": "arrow.up", "arrow_downward": "arrow.down", "acute": "slider.horizontal.below.rectangle",
    "show_chart": "chart.xyaxis.line", "view_agenda": "rectangle.split.1x2", "grid_on": "square.grid.3x3",
    "scatter_plot": "circle.grid.cross", "timeline": "chart.line.uptrend.xyaxis", "speed": "speedometer",
    "dashboard_2": "square.grid.2x2", "troubleshoot": "chart.bar.xaxis", "query_stats": "chart.bar.xaxis",
    "ssid_chart": "speedometer", "image": "photo", "pace": "metronome", "density_medium": "equal.square",
    "ink_highlighter": "highlighter", "play_arrow": "play.fill", "stop": "stop.fill", "map": "map",
    "help": "questionmark.circle", "bolt": "bolt", "extension": "puzzlepiece.extension", "warning": "exclamationmark.triangle",
    "backup": "externaldrive", "send": "paperplane", "my_location": "location", "change_history": "triangle",
    "functions": "sum", "tag": "number", "grid_view": "square.grid.2x2", "dashboard_customize": "square.grid.2x2",
    "view_quilt": "rectangle.3.group", "speed_4_progress": "gauge.with.needle", "\U000ffe94": "gauge.with.needle",
    "\ue8be": "cable.connector.horizontal", "\ue864": "externaldrive", "\uea0b": "bolt",
    "\ue87b": "puzzlepiece.extension", "\ue002": "exclamationmark.triangle", "\ue9e4": "gauge.with.needle",
    "\ue55b": "map", "\ue887": "questionmark.circle", "\ue8b8": "gearshape", "\ue65f": "sparkles",
}
