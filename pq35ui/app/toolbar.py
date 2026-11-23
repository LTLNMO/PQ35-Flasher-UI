"""ToolIcon: the one toolbar icon button. Every icon in a page's toolbar row is one of these.

Modes (combine as needed):
    ToolIcon("refresh", tip)                          plain: connect .clicked
    ToolIcon("list", tip, checkable=True)             toggle: accent (Classic) / grey square (Mac) while on
    ToolIcon("bookmark", tip, dropdown=True)          icon + ▾ (a button that opens a menu)
    ToolIcon("more_vert", tip, menu=fill)             opens a menu on press, refilled by fill(menu) each time

One size rule (tool_metrics, below: Classic 36 / 50 with ▾ x 34; Mac 30 / 42 x 28), which
sizeHint reports truthfully, and one paint path: the style draws only the background (hover, on),
and the glyph is drawn at the same centred spot on every button, so no icon sits a pixel off its
neighbours. Colours follow the theme (re-tinted on theme.apply). Subclasses with a glyph of their
own (Live View's layout picker) override paint_glyph()."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QPushButton, QStyle, QStyleOptionButton, QStylePainter

from . import menus


class ToolIcon(QPushButton):

    def __init__(self, glyph, tip="", *, checkable=False, dropdown=False, menu=None, parent=None):
        super().__init__(parent)
        from . import theme
        self.glyph, self.dropdown = glyph, dropdown
        self.color_key = None  # a theme colour overriding the usual grey / accent (set_color)
        self.setObjectName("iconTool")
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        w, h = tool_metrics(dropdown)[:2]
        self._size = QSize(w, h)
        self.setFixedSize(self._size)
        self.setIconSize(icon_size(glyph, dropdown) if glyph else QSize(*tool_metrics(dropdown)[2:]))
        self.menu_ = None
        if menu is not None:
            self.menu_ = QMenu(self)
            self.menu_.setToolTipsVisible(True)
            self.menu_.aboutToShow.connect(lambda: (self.menu_.clear(), menu(self.menu_)))
            self.setMenu(self.menu_)
            menus.anchor_menu(self.menu_, self)
        self.toggled.connect(self.retint)
        self.retint()
        theme.on_apply(self.retint)

    # --- colour ---

    def set_color(self, key):
        """Draw the glyph in theme colour `key` (None: back to the usual grey / accent)."""
        self.color_key = key
        self.retint()

    def set_glyph(self, glyph):
        self.glyph = glyph
        self.setIconSize(icon_size(glyph, self.dropdown))
        self.retint()

    def retint(self, *_):
        from . import theme
        try:
            color = (theme.colors()[self.color_key] if self.color_key
                     else tool_color(self.isCheckable() and self.isChecked()))
            if self.glyph is not None:
                self.setIcon(toolbar_icon(self.glyph, color, self.dropdown))
            self.update()
        except RuntimeError:  # deleted
            pass

    # --- size: the fixed size, not the stylesheet padding's guess ---

    def sizeHint(self):
        return QSize(self._size)

    def minimumSizeHint(self):
        return QSize(self._size)

    def set_box(self, w, h):
        self._size = QSize(w, h)
        self.setFixedSize(self._size)

    # --- painting ---

    def paintEvent(self, event):
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.icon = QIcon()
        opt.text = ""
        opt.features &= ~QStyleOptionButton.ButtonFeature.HasMenu  # no menu arrow: ▾ is in the glyph
        p.drawControl(QStyle.ControlElement.CE_PushButton, opt)
        self.paint_glyph(p)
        p.end()

    def paint_glyph(self, p):
        icon = self.icon()
        if icon.isNull():
            return
        size = self.iconSize()
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        state = QIcon.State.On if self.isChecked() else QIcon.State.Off
        icon.paint(p, (self.width() - size.width()) // 2, (self.height() - size.height()) // 2,
                   size.width(), size.height(), Qt.AlignmentFlag.AlignCenter, mode, state)


# ── sizes, colours and icons of toolbar buttons; the round Record / Start button ──

def tool_metrics(dropdown=False):
    """Toolbar icon buttons: (button w, h, icon strip w, glyph px). Classic: 36 (50 with ▾) x 34,
    22 px glyphs. Mac (mockup .tbtn): 30 (42 with a chevron) x 28, 16 px SF Symbols (measured
    against the mockup at 2x: its bookmark is 25 px tall, history 26 px; 18 px came out 15 % bigger)."""
    from . import theme
    if theme.is_mac():
        g = theme.icon_px("toolbar")  # 19 at 13 pt text: a step bigger than the text, as AppKit toolbars
        return (42 if dropdown else 30), 28, g + (12 if dropdown else 0), g
    return (50 if dropdown else 36), 34, (36 if dropdown else 22), 22


def tool_color(checked):
    """A toolbar icon's colour. Classic: accent while a toggle is on. Mac: always the grey glyph
    colour; an on toggle shows a soft square behind it instead (QPushButton#iconTool:checked)."""
    from . import theme
    c = theme.colors()
    if checked and not theme.is_mac():
        return c["accent"]
    return c["tab_text"]


def _optical(name):
    """Mac: (point scale, box ratio) for a toolbar symbol drawn larger than the rest, else None."""
    from . import theme
    if not theme.is_mac():
        return None
    from . import sf_symbols
    return sf_symbols.OPTICAL_SCALE.get(sf_symbols.MATERIAL_TO_SF.get(name))


def icon_size(glyph, dropdown=False):
    """The QSize a toolbar button's icon is drawn at (wider for an OPTICAL_SCALE symbol)."""
    from PySide6.QtCore import QSize
    _w, _h, strip, g = tool_metrics(dropdown)
    wide = None if dropdown else _optical(glyph.partition("@")[0])
    return QSize(round(g * wide[1]) if wide else strip, g)


def toolbar_icon(glyph, color, dropdown=False):
    """A toolbar icon (Classic: Material glyph, 22 px; Mac: SF Symbol, theme.icon_px()); dropdown: with a
    small ▾ / chevron after it."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from . import theme
    from .main_window import _material_icon
    _w, _h, strip, g = tool_metrics(dropdown)
    name, _, rot = glyph.partition("@")  # "glyph@90" = rotated glyph
    wide = _optical(name)
    if wide and not dropdown:  # Mac: a text-like symbol (f(x)) drawn as big as its neighbours
        from . import sf_symbols
        scale, ratio = wide
        sym = sf_symbols.icon(sf_symbols.MATERIAL_TO_SF[name], g, color, point=g * 0.8 * scale,
                              width=round(g * ratio))
        if sym is not None:
            return sym
    icon = _material_icon(name, g, color, int(rot or 0))
    if not dropdown:
        return icon
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    px = QPixmap(int(strip * dpr), int(g * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.drawPixmap(0, 0, icon.pixmap(QSize(g, g)))
    if theme.is_mac():  # a small chevron 2 px after the glyph
        p.drawPixmap(g + 2, max(0, (g - 10) // 2), _material_icon("arrow_drop_down", 10, color).pixmap(QSize(10, 10)))
    else:
        p.drawPixmap(20, 3, _material_icon("arrow_drop_down", 16, color).pixmap(QSize(16, 16)))
    p.end()
    return QIcon(px)


class RunButton(QPushButton):
    """Start / Stop as a round 34 px button with a filled Material Symbol: `glyph` in the theme
    colour `color` (Logger: red fiber_manual_record; Live View: green play_arrow) or, while
    running (set_running), a red stop square in a red ring. Greyed out while disabled."""

    SIZE = 34
    GLYPH = 26  # glyph box; the dot / square / triangle inside is about half that

    def __init__(self, glyph, color, parent=None, stop_glyph="stop", fill=True, frame=True):
        super().__init__(parent)
        self.glyph, self.color = glyph, color
        self.stop_glyph, self.fill = stop_glyph, fill
        # frame=False: no circle of our own; the glyph's own circle is the button (bigger glyph)
        self.frame = frame
        self.glyph_px = self.GLYPH if frame else self.SIZE
        self.running = False
        from . import theme
        self.mac = theme.is_mac()
        if self.mac:  # mockup .rec: a 28 px pill, icon + "Start" / "Record"
            from PySide6.QtGui import QFontMetrics
            self.label = "Start" if glyph == "play_arrow" else "Record"
            f = self.font()
            f.setWeight(f.Weight.Medium)
            width = max(QFontMetrics(f).horizontalAdvance(t) for t in (self.label, "Stop"))
            self.setFixedSize(9 + 12 + 7 + width + 12, 28)
        else:
            self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_running(self, on):
        self.running = on
        self.update()

    def _paint_mac(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        from . import sf_symbols, theme
        c = theme.colors()
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        key = "err" if self.running else self.color
        color = c[key] if enabled else c["muted"]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # The page's main action, so it stands out: a tinted pill in its own colour (Start green,
        # Record / Stop red); still faintly tinted while it can't be used yet.
        bg = theme._mix(c["bg"], c[key], (0.26 if hover else 0.18) if enabled else 0.08)
        p.setBrush(QColor(bg))
        color = c[key] if enabled else theme._mix(c["muted"], c[key], 0.35)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        symbol = "stop.fill" if self.running else ("play.fill" if self.color == "ok" else "circle.fill")
        icon = sf_symbols.icon(symbol, 12, color, point=9 if symbol != "circle.fill" else 10)
        if icon is not None:
            p.drawPixmap(9, (self.height() - 12) // 2, icon.pixmap(12, 12))
        f = self.font()
        f.setWeight(f.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(c["text"] if enabled else c["muted"]))
        p.drawText(QRectF(28, 0, self.width() - 28, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Stop" if self.running else self.label)
        p.end()

    set_recording = set_running  # the Logger's word for it

    def paintEvent(self, event):
        if self.mac:
            self._paint_mac()
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter, QPen
        from . import theme
        from .main_window import _material_icon
        c = theme.colors()
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        live = self.running and enabled
        key = "err" if self.running else self.color
        color = c[key] if enabled else c["muted"]
        tint = c["err_bg"] if key == "err" else c.get(f"{key}_bg", c["accent_bg"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.frame:
            ring = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(color if live else c["border"]), 1))
            p.setBrush(QColor(tint if (hover or live) else c["surface2"]))
            p.drawEllipse(ring)
        elif hover:
            p.setOpacity(0.8)  # the only hover hint without a circle of our own
        size = self.glyph_px
        icon = _material_icon(self.stop_glyph if self.running else self.glyph, size, color,
                              fill=self.fill)
        offset = (self.SIZE - size) // 2
        p.drawPixmap(offset, offset, icon.pixmap(size, size))
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
