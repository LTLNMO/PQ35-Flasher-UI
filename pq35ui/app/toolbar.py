"""ToolIcon: the one toolbar icon button. Every icon in a page's toolbar row is one of these.

Modes (combine as needed):
    ToolIcon("refresh", tip)                          plain: connect .clicked
    ToolIcon("list", tip, checkable=True)             toggle: accent (Classic) / grey square (Mac) while on
    ToolIcon("bookmark", tip, dropdown=True)          icon + ▾ (a button that opens a menu)
    ToolIcon("more_vert", tip, menu=fill)             opens a menu on press, refilled by fill(menu) each time

One size rule (analyze_tools.tool_metrics: Classic 36 / 50 with ▾ x 34; Mac 30 / 42 x 28), which
sizeHint reports truthfully, and one paint path: the style draws only the background (hover, on),
and the glyph is drawn at the same centred spot on every button, so no icon sits a pixel off its
neighbours. Colours follow the theme (re-tinted on theme.apply). Subclasses with a glyph of their
own (Live View's layout picker) override paint_glyph()."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QPushButton, QStyle, QStyleOptionButton, QStylePainter


class ToolIcon(QPushButton):

    def __init__(self, glyph, tip="", *, checkable=False, dropdown=False, menu=None, parent=None):
        super().__init__(parent)
        from . import analyze_tools as T, theme
        self.glyph, self.dropdown = glyph, dropdown
        self.color_key = None  # a theme colour overriding the usual grey / accent (set_color)
        self.setObjectName("iconTool")
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        w, h = T.tool_metrics(dropdown)[:2]
        self._size = QSize(w, h)
        self.setFixedSize(self._size)
        self.setIconSize(T.icon_size(glyph, dropdown) if glyph else QSize(*T.tool_metrics(dropdown)[2:]))
        self.menu_ = None
        if menu is not None:
            self.menu_ = QMenu(self)
            self.menu_.setToolTipsVisible(True)
            self.menu_.aboutToShow.connect(lambda: (self.menu_.clear(), menu(self.menu_)))
            self.setMenu(self.menu_)
            T.anchor_menu(self.menu_, self)
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
        from . import analyze_tools as T
        self.setIconSize(T.icon_size(glyph, self.dropdown))
        self.retint()

    def retint(self, *_):
        from . import analyze_tools as T, theme
        try:
            color = (theme.colors()[self.color_key] if self.color_key
                     else T.tool_color(self.isCheckable() and self.isChecked()))
            if self.glyph is not None:
                self.setIcon(T.toolbar_icon(self.glyph, color, self.dropdown))
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
