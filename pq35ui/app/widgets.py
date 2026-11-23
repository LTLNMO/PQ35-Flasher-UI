"""Small reusable widgets."""
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCore import QObject, QPoint, QRect, QSize
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .storage import images_dir


def restyle(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def discard(widget):
    """Take a widget off screen now and delete it soon. deleteLater alone leaves it painted until
    the event loop gets to it (old cards / buttons showing under the new ones meanwhile)."""
    if widget is None:
        return
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


# ── shared controls: one look on every tab ──

def segment_button(label, tip="", parent=None):
    """One choice of a segmented control (Analyze's Single | Split…, Logger's modes, Settings'
    sections, Select channels' All | Panel 1…): QPushButton#viewTab, styled in theme.py. Set
    "active" with set_segment_active. Put the buttons in a segment_track on Mac."""
    return style_segment(QPushButton(label, parent), tip)


def style_segment(b, tip=""):
    """segment_button's setup, for a QPushButton subclass made elsewhere (the Logger's mode buttons)."""
    b.setObjectName("viewTab")
    b.setFixedHeight(34)  # Classic: as tall as every toolbar button (segment_track makes it 22 on Mac)
    if tip:
        b.setToolTip(tip)
    b.setProperty("active", "false")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setIconSize(QSize(20, 20))
    return b


def set_segment_active(buttons, index):
    for i, b in enumerate(buttons):
        on = "true" if i == index else "false"
        if b.property("active") != on:
            b.setProperty("active", on)
            restyle(b)


def segment_track(widget, layout, buttons, equal=False):
    """Mac: *widget* becomes the macOS segmented control's track (theme #macSegments: a 2 px inset,
    26 px tall, 22 px segments with control-sized icons). Classic: a plain row, 2 px apart.
    equal: every segment the same width (else each as wide as its label)."""
    mac = theme.is_mac()
    if mac:
        widget.setObjectName("macSegments")
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        widget.setFixedHeight(26)
    else:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
    for b in buttons:
        if mac:
            b.setFixedHeight(22)
            px = theme.icon_px("control")
            b.setIconSize(QSize(px, px))
        if equal:
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setStyleSheet("QPushButton#viewTab { text-align: center; }")  # the base style is left
        layout.addWidget(b, 1 if equal else 0)


def pulldown(button):
    """A pull-down button: its text, then the menu chevron on the right (Select channels ▾, Choose a
    module ▾). Mac: AppKit's small grey chevron; Classic: the bigger ▾ in the text colour."""
    button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def tint():
        from .main_window import _material_icon
        try:
            c = theme.colors()
            size, color = (12, c.get("sub", c["muted"])) if theme.is_mac() else (20, c["text"])
            button.setIconSize(QSize(size, size))
            button.setIcon(_material_icon("arrow_drop_down", size, color))
        except RuntimeError:  # deleted
            pass
    tint()
    theme.on_apply(tint)
    return button


class Card(QFrame):
    def __init__(self, title: str = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        self.title_label = None
        if title:
            from . import theme
            # Mac interface: small grey capitals, like the mockup's (and Finder's) section titles.
            label = QLabel(title.upper() if theme.is_mac() else title)
            label.setObjectName("cardTitle")
            self.body.addWidget(label)
            self.title_label = label

    def hint(self, text):
        """What the card is for, shown on hover instead of taking a line of the page."""
        self.setToolTip(text)
        if self.title_label is not None:
            self.title_label.setToolTip(text)

    def add(self, item):
        if isinstance(item, QLayout):
            self.body.addLayout(item)
        else:
            self.body.addWidget(item)
        return item


class Badge(QLabel):
    def __init__(self, text="", level="idle", parent=None):
        super().__init__(text, parent)
        self.setProperty("level", level)

    def set(self, text, level):
        self.setText(text)
        self.setProperty("level", level)
        restyle(self)


class Banner(QLabel):
    """A full-width message. Hidden when empty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setWordWrap(True)
        self.setContentsMargins(12, 10, 12, 10)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(self._link)
        self._actions = {}
        self.active = False
        self.hide()

    def show_message(self, text, level="info", actions=None):
        """actions: {link text: callable}, shown as links after the message (e.g. Show in Finder)."""
        self.active = bool(text)
        self._actions = dict(actions or {})
        if not text:
            self.hide()
            return
        if self._actions:
            import html
            links = " · ".join(f'<a href="{i}">{html.escape(label)}</a>'
                               for i, label in enumerate(self._actions))
            self.setTextFormat(Qt.TextFormat.RichText)
            self.setText(f"{html.escape(text)}&nbsp;&nbsp;{links}")
        else:
            self.setTextFormat(Qt.TextFormat.PlainText)
            self.setText(text)
        self.setProperty("level", level)
        restyle(self)
        self.show()

    def _link(self, href):
        try:
            callback = list(self._actions.values())[int(href)]
        except (ValueError, IndexError):
            return
        callback()


class FlowRow(QWidget):
    """A row of badges that wraps onto new lines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.items = []

    def clear(self):
        while self.layout_.count():
            row = self.layout_.takeAt(0)
            if row.layout():
                while row.layout().count():
                    child = row.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        self.items = []

    def set_badges(self, badges, per_row=4):
        self.clear()
        row = None
        for index, (text, level, tooltip) in enumerate(badges):
            if index % per_row == 0:
                row = QHBoxLayout()
                row.setSpacing(6)
                self.layout_.addLayout(row)
            badge = Badge(text, level)
            if tooltip:
                badge.setToolTip(tooltip)
            row.addWidget(badge)
            self.items.append(badge)
        if row is not None:
            row.addStretch(1)


class HoldButton(QPushButton):
    """Must be held down for `seconds` to fire. Releasing early cancels."""
    confirmed = Signal()

    def __init__(self, text, seconds=3.0, parent=None):
        super().__init__(text, parent)
        self.setObjectName("primary")
        self.seconds = seconds
        self.base_text = text
        self.fraction = 0.0
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)
        self.setMinimumHeight(44)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clock.start()
            self.timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._reset()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # Space/Enter must not bypass the hold
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return
        super().keyPressEvent(event)

    def _reset(self):
        self.timer.stop()
        self.fraction = 0.0
        self.setText(self.base_text)
        self.update()

    def _tick(self):
        self.fraction = min(1.0, self.clock.elapsed() / (self.seconds * 1000))
        left = max(0.0, self.seconds * (1 - self.fraction))
        self.setText(f"Keep holding… {left:.1f} s")
        self.update()
        if self.fraction >= 1.0:
            self._reset()
            self.setDown(False)
            self.confirmed.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.fraction > 0:
            painter = QPainter(self)
            color = QColor("#ffffff")
            color.setAlpha(70)
            painter.fillRect(0, 0, int(self.width() * self.fraction), self.height(), color)
            painter.end()


class DropZone(QFrame):
    """Drag a .bin here or click to browse."""
    picked = Signal(str)

    def __init__(self, prompt="Drop a .bin or .sgo file here, or click to choose", parent=None):
        super().__init__(parent)
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        self.title = QLabel(prompt)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-weight: 600;")
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        self.start_dir = str(images_dir())

    def set_file(self, name, detail):
        self.title.setText(name)
        self.detail.setText(detail)

    def mouseReleaseEvent(self, event):
        self.choose()

    def choose(self):
        """Open the file picker (a public entry point for other pages)."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose firmware image", self.start_dir,
                                              "Firmware (*.bin *.sgo);;All files (*)")
        if path:
            self.start_dir = str(Path(path).parent)
            self.picked.emit(path)

    def _hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        restyle(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, event):
        self._hover(False)

    def dropEvent(self, event):
        self._hover(False)
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.picked.emit(urls[0])


def muted(text) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def h1(text) -> QLabel:
    label = QLabel(text)
    label.setObjectName("h1")
    return label


def kib(size: int) -> str:
    return f"{size / 1024:.0f} KiB" if size >= 1024 else f"{size} B"


def colors():
    return theme.colors()


STAGES = {
    "READ": "Reading", "EPS READ": "Reading", "DEMO READ": "Reading", "PAGE FF": "Reading page FF",
    "CONNECTING": "Connecting", "SECURITY": "Unlocking", "PROGRAMMING": "Entering programming",
    "ERASING": "Erasing", "WRITING": "Writing", "VERIFYING": "Verifying checksum",
    "REBOOTING": "Restarting the module", "COMPLETE": "Done",
}
BYTES = re.compile(r"(\d+)/(\d+) bytes")


def rate(value: float) -> str:
    return f"{value:.0f} B/s" if value < 1024 else f"{value / 1024:.1f} KiB/s"


def clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class ProgressPanel(QWidget):
    """Overall bar plus "96 / 224 KiB · 0.6 KiB/s · 3:40 left · 1:52 elapsed".
    Animates until the first real number arrives, so it never looks stuck."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.stage = QLabel("")
        self.stage.setStyleSheet("font-weight: 600;")
        self.percent = QLabel("")
        top.addWidget(self.stage)
        top.addStretch(1)
        top.addWidget(self.percent)
        layout.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.bar)
        self.detail = muted("")
        layout.addWidget(self.detail)
        self.started = None
        self.figures = ""
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._render_detail)
        self.hide()

    def start(self, stage="Starting…"):
        self.started = self.started or time.monotonic()
        self.stage.setText(stage)
        self.percent.setText("")
        self.figures = ""
        self.bar.setRange(0, 0)  # busy animation until a percentage arrives
        self.ticker.start()
        self._render_detail()
        self.show()

    def update_from(self, message: dict):
        if self.started is None:
            self.start()
        percent = float(message.get("percent") or 0)
        stage = str(message.get("stage", "")).strip()
        self.stage.setText(STAGES.get(stage, stage.title()))
        self.percent.setText(f"{percent:.0f}%")
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(percent * 10))
        parts = []
        match = BYTES.search(str(message.get("detail", "")))
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            parts.append(f"{done / 1024:.0f} / {total / 1024:.0f} KiB")
        elif message.get("detail"):
            parts.append(str(message["detail"]))
        if message.get("speed"):
            parts.append(rate(float(message["speed"])))
        if message.get("eta_sec"):
            parts.append(f"{clock(float(message['eta_sec']))} left")
        self.figures = "  ·  ".join(parts)
        self._render_detail()

    def _render_detail(self):
        elapsed = f"{clock(time.monotonic() - self.started)} elapsed" if self.started else ""
        self.detail.setText("  ·  ".join(p for p in (self.figures, elapsed) if p))

    def stop(self):
        self.ticker.stop()
        self.started = None
        self.hide()


class _FitToWindow(QWidget):
    """Holds a page section so it is sized to the window, not to its preferred
    size: graphs and tables shrink to fit, and the scroll bar only appears when
    the section genuinely can't fit (its minimum, with wrapped rows counted)."""

    def __init__(self, content):
        super().__init__()
        self.setObjectName(content.objectName())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(content)
        self.content = content
        self._last_min = None

    def hasHeightForWidth(self):
        return False

    def heightForWidth(self, width):
        # QScrollArea asks this (via our layout) and would otherwise get the
        # *preferred* height of everything, making graphs push past the window.
        return self.minimumSizeHint().height()

    def minimumSizeHint(self):
        hint = self.content.minimumSizeHint()
        layout = self.content.layout()
        if layout is None:
            return hint
        width = max(self.width(), hint.width())
        m = layout.contentsMargins()
        height, count = m.top() + m.bottom(), 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.isEmpty():
                continue
            inner = width - m.left() - m.right()
            widget = item.widget()
            wraps = isinstance(item.layout(), FlowLayout) or isinstance(widget, QLabel) or (
                widget is not None and isinstance(widget.layout(), FlowLayout))
            # Wrapped text and wrapping button rows need their real height at this width;
            # anything else (graphs, tables, card grids) counts at its minimum so it can shrink.
            height += max(item.heightForWidth(inner), item.minimumSize().height()) \
                if wraps and item.hasHeightForWidth() else item.minimumSize().height()
            count += 1
        height += max(count - 1, 0) * max(layout.spacing(), 0)
        return QSize(hint.width(), max(hint.height(), height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        current = self.minimumSizeHint()
        if current != self._last_min:  # wrapped rows changed: let the scroll area re-check
            self._last_min = current
            self.updateGeometry()


def scrollable(widget):
    """Wrap a page section so it fits the window, scrolling only when the window
    is smaller than the section's minimum size."""
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(_FitToWindow(widget))
    return area


class WheelGuard(QObject):
    """App-wide: spin boxes, sliders and dropdowns only take the mouse wheel when
    they have focus (clicked into). Otherwise the wheel scrolls the page, so
    scrolling past a field never changes its value by accident."""

    # This sees every event in the app (tens of thousands while the window is built), so the
    # common case must cost almost nothing: no imports in here, and a plain type check first.
    from PySide6.QtCore import QEvent as _QEvent
    from PySide6.QtWidgets import (QAbstractScrollArea as _ScrollArea, QAbstractSlider as _Slider,
                                   QAbstractSpinBox as _SpinBox, QComboBox as _Combo,
                                   QScrollBar as _ScrollBar)
    _WHEEL = _QEvent.Type.Wheel
    _GUARDED = (_SpinBox, _Combo, _Slider)

    def eventFilter(self, obj, event):
        if event.type() != self._WHEEL or not isinstance(obj, self._GUARDED):
            return False
        if not obj.hasFocus() and not isinstance(obj.parent(), self._Slider):
            QAbstractScrollArea = self._ScrollArea
            if isinstance(obj, self._ScrollBar):
                return False
            # Hand the wheel to the nearest scroll area so the page still scrolls.
            parent = obj.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                from PySide6.QtWidgets import QApplication
                QApplication.sendEvent(parent.verticalScrollBar(), event)
            return True
        return False


_OBJC = None


def _objc():
    """Typed objc_msgSend calls (ctypes), loaded once. None if unavailable."""
    global _OBJC
    if _OBJC is None:
        import ctypes
        import ctypes.util
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        addr = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
        vp = ctypes.c_void_p
        _OBJC = {
            "lib": lib,
            "id": ctypes.CFUNCTYPE(vp, vp, vp)(addr),               # -> id
            "set_id": ctypes.CFUNCTYPE(None, vp, vp, vp)(addr),     # setX:(id)
            "set_bool": ctypes.CFUNCTYPE(None, vp, vp, ctypes.c_bool)(addr),  # setX:(BOOL)
        }
    return _OBJC


def _mac_clear_popup_window(widget):
    """Make a rounded popup's macOS window truly see-through, then redo its shadow.

    Qt only clears the NSWindow's background colour when a window is see-through from the
    start; a tooltip is made see-through a moment later, so its window kept a solid background
    that showed as a square behind the rounded box. And macOS works out the shadow once, often
    before Qt has painted, so it can stay square too. This sets the window non-opaque with a
    clear background and asks for the shadow again. No-op off the cocoa platform or on error."""
    from PySide6.QtGui import QGuiApplication
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return  # winId is only an NSView on the cocoa platform (offscreen tests would crash)
    try:
        if not widget.isVisible():
            return
        o = _objc()
        sel = o["lib"].sel_registerName
        win = o["id"](int(widget.winId()), sel(b"window"))
        if not win:
            return
        clear = o["id"](o["lib"].objc_getClass(b"NSColor"), sel(b"clearColor"))
        o["set_bool"](win, sel(b"setOpaque:"), False)
        o["set_id"](win, sel(b"setBackgroundColor:"), clear)
        o["id"](win, sel(b"invalidateShadow"))
    except Exception:  # cosmetic only
        pass


class RoundPopups(QObject):
    """App-wide: tooltips, menus and combo-box lists are drawn as rounded boxes by the
    stylesheet, but Qt gives their windows an opaque square background, so square corners
    showed around them. Making those windows see-through leaves only the rounded box; on macOS
    the shadow is then recomputed after the first paint so it follows the rounded shape too.
    Cheap: one type check per event, class checks only on Polish/Show."""
    from PySide6.QtCore import QEvent as _QEvent
    _POLISH = _QEvent.Type.Polish
    _SHOW = _QEvent.Type.Show
    _CLASSES = ("QTipLabel", "QMenu", "QComboBoxPrivateContainer")

    def _is_popup(self, obj):
        return obj.isWidgetType() and any(obj.inherits(c) for c in self._CLASSES)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == self._POLISH and self._is_popup(obj):
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            obj.setAutoFillBackground(False)
        elif (kind == self._SHOW and sys.platform == "darwin" and obj.isWidgetType() and obj.isWindow()
              and obj.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)):  # incl. PopupCard
            from shiboken6 import isValid
            for ms in (0, 40, 150):  # again once the first paint has reached the window server
                QTimer.singleShot(ms, lambda w=obj: isValid(w) and _mac_clear_popup_window(w))
        return False


class AppEventFilter(QObject):
    """WheelGuard and RoundPopups as one app-wide filter. Every event in the app goes through
    an app-wide filter, and the costly part is Qt handing each event to Python, so one filter
    instead of two halves that cost."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wheel = WheelGuard(self)
        self.popups = RoundPopups(self)
        self._wheel_type = WheelGuard._WHEEL
        self._popup_types = (RoundPopups._POLISH, RoundPopups._SHOW)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == self._wheel_type:
            return bool(self.wheel.eventFilter(obj, event))
        if kind in self._popup_types:
            self.popups.eventFilter(obj, event)
        return False


RoundTooltips = RoundPopups  # old name


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto new lines when the
    window is too narrow, so a row of controls never forces sideways scrolling."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.min_line_height = 0  # mac_shell.first_row: every line as tall as row 1, controls centred
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def addStretch(self, *_):  # rows built for QHBoxLayout may call this; wrapping ignores it
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        size = size.expandedTo(QSize(0, self.min_line_height))
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _visible(self):
        return [i for i in self._items
                if not (i.widget() is not None and i.widget().isHidden()) and not i.spacerItem()]

    @staticmethod
    def _fills(item):
        """A widget with property flowFill=True takes the rest of its line
        (down to its minimum width) instead of wrapping, e.g. a file name label."""
        w = item.widget()
        return w is not None and bool(w.property("flowFill"))

    def _arrange(self, rect, apply):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        right = area.right() + 1
        # Split into lines first, so each line can be centred vertically.
        lines, line, x = [], [], area.x()
        for item in self._visible():
            hint = item.sizeHint()
            width = hint.width()
            if x + width > right and self._fills(item):
                width = max(item.minimumSize().width(), right - x)
            if x + width > right and line:
                lines.append(line)
                line, x = [], area.x()
                width = min(hint.width(), area.width()) if self._fills(item) else hint.width()
            if self._fills(item):
                width = max(width, right - x)  # fill to the edge
            line.append((item, width, hint.height()))
            x += width + self._spacing
        if line:
            lines.append(line)
        y = area.y()
        for line in lines:
            line_h = max(self.min_line_height, *(h for _i, _w, h in line))
            x = area.x()
            for item, width, height in line:
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (line_h - height) // 2), QSize(width, height)))
                x += width + self._spacing
            y += line_h + self._spacing
        return (y - self._spacing if lines else y) - rect.y() + m.bottom()


class ElidedLabel(QLabel):
    """One line of text that shortens with "…" when there isn't room (full text on hover)."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(80)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._elide()
        self.updateGeometry()  # new natural width

    def fullText(self):
        return self._full

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, hint.height())

    def minimumSizeHint(self):
        return QSize(80, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 10))
        if shown != super().text():
            QLabel.setText(self, shown)


class ElidedButton(QPushButton):
    """Push button whose text shortens at the END with "…" when squeezed (Qt just clips
    centred button text, losing the start). `suffix` (e.g. a ▾) is always kept whole.
    While shortened, the tooltip shows the full text above `tip`."""

    def __init__(self, text="", suffix="", tip="", parent=None):
        super().__init__(parent)
        self._full, self._suffix, self._tip = "", suffix, tip
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._elide()
        self.updateGeometry()  # new natural width

    def fullText(self):
        return self._full

    def _extra(self):
        """Width of padding/border/suffix around the text, from the shown text's hint."""
        return super().sizeHint().width() - self.fontMetrics().horizontalAdvance(super().text())

    def sizeHint(self):
        hint = super().sizeHint()
        fm = self.fontMetrics()
        return QSize(self._extra() + fm.horizontalAdvance(self._full + self._suffix) + 8, hint.height())

    def minimumSizeHint(self):
        fm = self.fontMetrics()
        return QSize(self._extra() + fm.horizontalAdvance("Haldex…" + self._suffix) + 8, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        room = self.width() - self._extra() - fm.horizontalAdvance(self._suffix) - 2
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(room, 10))
        if shown + self._suffix != super().text():
            QPushButton.setText(self, shown + self._suffix)
        self.setToolTip(f"{self._full}\n{self._tip}" if shown != self._full and self._tip
                        else self._full if shown != self._full else self._tip)


def pair(*widgets, spacing=6):
    """Keep a label and its control together when a FlowLayout wraps."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for w in widgets:
        row.addWidget(w)
    return box


def adjustable_columns(header, settings=None, key=None, widths=()):
    """Every column can be dragged wider or narrower; the last one fills what's left.
    Widths are remembered under `key` in settings when given."""
    from PySide6.QtWidgets import QHeaderView
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    header.setMinimumSectionSize(40)
    header.setSectionsMovable(False)
    saved = (settings.get(key) if settings is not None and key else None) or []
    for i, w in enumerate(widths):
        width = saved[i] if i < len(saved) and isinstance(saved[i], int) else w
        header.resizeSection(i, max(width, 40))
    if settings is not None and key:
        timer = QTimer(header)
        timer.setSingleShot(True)
        timer.setInterval(400)

        def save():
            count = header.count()
            settings.set(key, [header.sectionSize(i) for i in range(max(count - 1, 0))])
        timer.timeout.connect(save)
        header.sectionResized.connect(lambda *_: timer.start())


def reveal_file(path):
    """Show a file selected in Finder / Explorer (other systems: open its folder)."""
    import subprocess
    import sys
    path = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
            return
    except OSError:
        pass
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class PopupCard(QFrame):
    """A small rounded panel that pops up below a button and closes when you click elsewhere,
    styled like the app's cards (rounded, surface colour, thin border). Put content in .body."""

    RADIUS = 12

    def __init__(self, parent, min_width=0):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # rounded corners show through
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QFrame()
        self.body.setObjectName("popupCard")
        if min_width:
            self.body.setMinimumWidth(min_width)
        outer.addWidget(self.body)
        self._restyle()
        theme.on_apply(self._restyle)

    def _restyle(self):
        c = theme.colors()
        radius = 10 if theme.is_mac() else self.RADIUS
        self.setStyleSheet(
            f"QFrame#popupCard {{ background: {c['surface']}; border: 1px solid {c.get('card_border', c['border'])}; "
            f"border-radius: {radius}px; }}"
            f"QLabel#optionsHeading {{ color: {c['muted']}; font-size: 11px; font-weight: 700; "
            f"letter-spacing: 1px; }}"
            f"QFrame#optionsRule {{ background: {c['border']}; border: none; }}")

    def open_at(self, anchor, align="right"):
        """Show below `anchor` (right edges lined up, or left), kept on screen."""
        from PySide6.QtGui import QGuiApplication
        self.adjustSize()
        size = self.sizeHint().expandedTo(self.minimumSizeHint())
        self.resize(size)
        corner = anchor.rect().bottomRight() if align == "right" else anchor.rect().bottomLeft()
        below = anchor.mapToGlobal(corner)
        x = below.x() - size.width() if align == "right" else below.x()
        y = below.y() + 6
        screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left() + 4, min(x, area.right() - size.width() - 4))
        win = anchor.window()  # and inside the window, like the menus (analyze_tools._place)
        w_left = win.mapToGlobal(win.rect().topLeft()).x()
        x = max(w_left + 4, min(x, w_left + win.width() - size.width() - 4))
        if y + size.height() > area.bottom():
            y = max(area.top(), anchor.mapToGlobal(anchor.rect().topLeft()).y() - size.height() - 6)
        self.move(x, y)
        self.show()


class _OptionsSection(QWidget):
    """One group in an OptionsButton panel: a small heading, then label | control rows."""

    LABEL_W = 104

    def __init__(self, owner, title, first):
        super().__init__()
        from PySide6.QtWidgets import QFormLayout
        self.owner = owner
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0 if first else 12, 0, 0)
        box.setSpacing(8)
        if not first:
            line = QFrame()
            line.setObjectName("optionsRule")
            line.setFixedHeight(1)
            box.addWidget(line)
            box.addSpacing(4)
        self.heading = QLabel(title.upper())
        self.heading.setObjectName("optionsHeading")
        box.addWidget(self.heading)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(8)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.form.setFieldGrowthPolicy(self.form.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        box.addLayout(self.form)

    def _label(self, text):
        label = QLabel(text)
        label.setObjectName("muted")
        label.setFixedWidth(self.LABEL_W)
        return label

    def add_row(self, label, *widgets):
        """Label | control(s) side by side."""
        field = widgets[0] if len(widgets) == 1 else pair(*widgets)
        self.form.addRow(self._label(label), field)
        return field

    def add_widget(self, widget):
        self.form.addRow(widget)
        return widget

    def add_note(self, text):
        """A small muted line under the controls, aligned with them."""
        note = muted(text)
        note.setStyleSheet("font-size: 12px;")
        self.form.addRow(self._label(""), note)
        return note

    def add_button(self, button, label="", on_click=None):
        """A button at its natural size (not stretched). Pass its action as on_click: the panel
        closes first and the action runs right after, so a dialog it opens isn't left sitting
        behind the panel."""
        button.clicked.connect(self.owner.panel.hide)
        if on_click is not None:
            button.clicked.connect(lambda: QTimer.singleShot(0, on_click))
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(button)
        row.addStretch(1)
        self.form.addRow(self._label(label), holder)
        return button


class OptionsButton(QPushButton):
    """⚙ at the end of a toolbar that opens a small panel of the page's settings, so the
    toolbar keeps to one row of actions. Build the panel with section("Title").add_row(...);
    hide a whole section with section.setVisible()."""

    GLYPH = "\ue8b8"  # Material Symbols "settings"

    def __init__(self, tip="Settings for this page", parent=None):
        super().__init__(parent)
        self.setToolTip(tip)
        self.setObjectName("optionsGear")  # scoped style: the panel is our child and would inherit it
        self.setFixedSize(40, 34)
        self.setIconSize(QSize(22, 22))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panel = PopupCard(self, min_width=360)
        self.box = QVBoxLayout(self.panel.body)
        self.box.setContentsMargins(16, 14, 16, 16)
        self.box.setSpacing(0)
        self.sections = []
        self.clicked.connect(self.open)
        self._restyle()
        theme.on_apply(self._restyle)

    def section(self, title):
        sec = _OptionsSection(self, title, first=not self.sections)
        self.sections.append(sec)
        self.box.addWidget(sec)
        return sec

    def _restyle(self):
        from .main_window import _material_icon
        c = theme.colors()
        self.setIcon(_material_icon(self.GLYPH, 22, c["tab_text"]))
        self.setStyleSheet("QPushButton#optionsGear { padding: 0; }")

    def open(self):
        self.panel.open_at(self)

    def refit(self):
        """Call after showing/hiding sections while the panel is open."""
        if self.panel.isVisible():
            self.open()
        else:
            self.panel.adjustSize()


class _VerticalLabel(QWidget):
    """Text drawn top-to-bottom (the collapsed sidebar's name on its rail)."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.color = "#888"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.height() + 4, fm.horizontalAdvance(self.text) + 8)

    minimumSizeHint = sizeHint

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setPen(QColor(self.color))
        f = self.font()
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        p.rotate(90)  # x now runs down the widget, y runs right-to-left (widget = y in [-width, 0])
        p.drawText(QRect(4, -self.width(), self.height() - 4, self.width()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text)
        p.end()


class SidebarToggle(QObject):
    """Collapsible sidebar for a sidebar | content QSplitter.
    Open: a small "collapse" icon sits in the sidebar's top row (beside `header`).
    Collapsed: the sidebar becomes a slim rail on the left edge with an "open" icon and the
    sidebar's name written down it, so it's plain something is tucked away; click anywhere on
    the rail to bring it back at its old width. Double-clicking the divider also toggles.
    Remembered per page (settings key "<key>:sidebar")."""

    RAIL_W = 36
    # Emitted when the sidebar is shown or hidden, so the page can rearrange in the same step
    # (it all lands in one repaint, see set_collapsed).
    shown_changed = Signal(bool)

    def __init__(self, splitter, settings=None, key=None, title="", header=None, button=None):
        super().__init__(splitter)
        self.splitter, self.settings, self.key, self.title = splitter, settings, key, title
        self.sidebar = splitter.widget(0)
        self._width = max(splitter.sizes()[0], 200)
        # `button`: a checkable toolbar button elsewhere on the page that shows (checked) or hides
        # the sidebar. With one there's no close button and no rail: the sidebar simply goes.
        self.button = button
        if button is not None:
            self.close_button = self.rail = None
            button.setCheckable(True)
            button.setChecked(True)
            button.toggled.connect(lambda on: self.set_collapsed(not on))
            splitter.setCollapsible(0, False)
            splitter.splitterMoved.connect(self._moved)
            splitter.handle(1).installEventFilter(self)
            saved = (settings.get(f"{key}:sidebar") if settings and key else None) or {}
            if saved.get("width"):
                self._width = int(saved["width"])
            if saved.get("collapsed"):
                self.set_collapsed(True, save=False)
            return
        # Close button, beside the sidebar's top-row widget.
        self.close_button = QPushButton()
        self.close_button.setObjectName("sidebarClose")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setIconSize(QSize(20, 20))
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setToolTip(f"Hide {title.lower() or 'the sidebar'}")
        self.close_button.clicked.connect(lambda: self.set_collapsed(True))
        if header is not None:
            _beside(header, self.close_button)
        # Rail, left of the splitter.
        self.rail = QFrame()
        self.rail.setObjectName("sidebarRail")
        self.rail.setFixedWidth(self.RAIL_W)
        self.rail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rail.setToolTip(f"Show {title.lower() or 'the sidebar'}")
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(0, 22, 0, 16)  # icon centred on the toolbar row (16 + (34 - 22) / 2)
        rl.setSpacing(14)
        self.rail_icon = QLabel()
        self.rail_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.rail_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        rl.addWidget(self.rail_icon)
        self.rail_text = _VerticalLabel(title)
        rl.addWidget(self.rail_text, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch(1)
        self.rail.hide()
        self.rail.installEventFilter(self)
        holder = splitter.parentWidget().layout()
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        holder.replaceWidget(splitter, box)
        row.addWidget(self.rail)
        row.addWidget(splitter, 1)
        splitter.setCollapsible(0, False)  # only the button/rail hide it, so it never vanishes by accident
        splitter.splitterMoved.connect(self._moved)
        splitter.handle(1).installEventFilter(self)
        theme.on_apply(self._restyle)
        self._restyle()
        saved = (settings.get(f"{key}:sidebar") if settings and key else None) or {}
        if saved.get("width"):
            self._width = int(saved["width"])
        if saved.get("collapsed"):
            self.set_collapsed(True, save=False)

    def collapsed(self):
        return self.sidebar.isHidden()

    def toggle(self):
        self.set_collapsed(not self.collapsed())

    def set_collapsed(self, on, save=True):
        """Show/hide in one clean step: drawing is paused while the sidebar, the page beside it
        and anything they re-align on the next tick rearrange, then the window repaints once,
        so no half-laid-out frames flash by."""
        # Only the splitter (sidebar + page beside it) pauses, never the whole window: pausing a
        # macOS window left other widgets (the button just clicked) showing stale paint.
        area = self.splitter
        freeze = area.isVisible() and area.updatesEnabled()
        if freeze:
            area.setUpdatesEnabled(False)
        try:
            self._apply(on, save)
        finally:
            if freeze:
                QTimer.singleShot(0, lambda: area.setUpdatesEnabled(True))  # after queued re-aligns

    def _content_width(self):
        """Narrowest the sidebar can be without cutting off its top row (0 if unknown)."""
        inner = getattr(self.sidebar, "widget", lambda: None)()
        return inner.minimumSizeHint().width() if isinstance(inner, QWidget) else 0

    def _apply(self, on, save=True):
        if on:
            if not self.collapsed() and self.splitter.sizes()[0]:
                self._width = self.splitter.sizes()[0]
            self.sidebar.hide()
            if self.rail is not None:
                self.rail.show()
        else:
            total = sum(self.splitter.sizes())
            if self.rail is not None:
                self.rail.hide()
            self.sidebar.show()
            if total:
                width = min(max(self._width, self._content_width()), max(total - 300, 200))
                self.splitter.setSizes([width, max(total - width, 0)])
        if self.button is not None and self.button.isChecked() == on:
            self.button.setChecked(not on)  # e.g. double-clicked divider; re-entry is a no-op
        self.shown_changed.emit(not on)
        if save:
            self._save()

    def _moved(self, *_):
        if not self.collapsed():
            self._width = self.splitter.sizes()[0]
            self._save()

    def _save(self):
        if self.settings and self.key:
            self.settings.set(f"{self.key}:sidebar", {"collapsed": self.collapsed(), "width": self._width})

    def _restyle(self):
        from .main_window import _material_icon
        c = theme.colors()
        self.close_button.setIcon(_material_icon("left_panel_close", 20, c["tab_text"]))
        self.close_button.setStyleSheet(
            f"QPushButton#sidebarClose {{ padding: 0; min-width: 0; min-height: 0; border: none; "
            f"border-radius: 7px; background: transparent; }}"
            f"QPushButton#sidebarClose:hover {{ background: {c['accent_bg']}; }}")
        self.rail_icon.setPixmap(_material_icon("left_panel_open", 22, c["tab_text"]).pixmap(QSize(22, 22)))
        self.rail_text.color = c["tab_text"]
        self.rail.setStyleSheet(
            f"QFrame#sidebarRail {{ background: {c['surface2']}; border: none; "
            f"border-right: 1px solid {c['border']}; }}"
            f"QFrame#sidebarRail:hover {{ background: {c['accent_bg']}; }}")
        self.rail_text.update()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.rail and event.type() == QEvent.Type.MouseButtonRelease:
            self.set_collapsed(False)
            return True
        if obj is not self.rail and event.type() == QEvent.Type.MouseButtonDblClick:
            self.toggle()
            return True
        return False


def _beside(widget, extra):
    """Put `extra` to the right of `widget`, in the same spot of its layout."""
    layout = widget.parentWidget().layout()
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    layout.replaceWidget(widget, box)
    row.addWidget(widget, 1)
    row.addWidget(extra, 0, Qt.AlignmentFlag.AlignVCenter)


def collapsible_sidebar(splitter, settings=None, key=None, title="", header=None, button=None):
    """Make a sidebar | content QSplitter's sidebar collapsible (see SidebarToggle). Call after
    the splitter is in its parent's layout. `header`: the sidebar's top-row widget, which gets
    the collapse button beside it. `button`: a checkable toolbar button that shows/hides the
    sidebar instead (no collapse button, no rail)."""
    splitter.sidebar_toggle = SidebarToggle(splitter, settings, key, title, header, button)
    return splitter.sidebar_toggle
