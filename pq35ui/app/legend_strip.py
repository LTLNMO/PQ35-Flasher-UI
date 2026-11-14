"""Legend strip under Analyze's graph, beside the timeline (like Datazap's legend): one chip per
channel on the graph, the same list as the sidebar (order, colours). A chip shows the channel's
name (values are in the sidebar and the cursor box); click it to hide or show its line,
right-click for more. The + at the end
opens the channel picker, so channels can be chosen with the sidebar hidden."""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QScrollArea, QSizePolicy, QWidget

from . import theme
from .widgets import FlowLayout

BAND_H = 52        # starting height; follows the text size (LegendStrip.fit_to_font)
CHIP_H = 21        # likewise: the font's height + 6
NAME_MAX = 220     # px; longer names are cut off with …
DOT = 7


def _fmt(v):
    a = abs(v)
    text = f"{v:.0f}" if a >= 100 else f"{v:.1f}" if a >= 10 else f"{v:.2f}"
    return text.replace("-", "−")


class _Chip(QWidget):
    toggled = Signal(str)
    menu_requested = Signal(str, object)

    def __init__(self, name, color, hidden, parent=None):
        super().__init__(parent)
        self.name, self.color, self.hidden = name, color, hidden
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _name_w(self):
        return min(self.fontMetrics().horizontalAdvance(self.name), NAME_MAX)

    def sizeHint(self):
        # Name only (the sidebar and the cursor box have the values); as tall as the text needs.
        return QSize(8 + DOT + 6 + self._name_w() + 10, self.fontMetrics().height() + 6)

    def minimumSizeHint(self):
        return self.sizeHint()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.toggled.emit(self.name)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.name, e.globalPos())

    def paintEvent(self, _):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover:
            bg = QColor(c["border"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, self.height() / 2, self.height() / 2)
        cy = self.height() / 2
        x = 8
        color = QColor(self.color)
        if self.hidden:  # hollow dot: the line is off the graph
            p.setPen(QPen(color, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x + 0.7, cy - DOT / 2 + 0.7, DOT - 1.4, DOT - 1.4))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(x, cy - DOT / 2, DOT, DOT))
        x += DOT + 6
        p.setPen(QColor(c["muted"] if self.hidden else c["text"]))
        p.setFont(self.font())
        name = self.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, NAME_MAX)
        nw = self._name_w()
        p.drawText(QRectF(x, 0, nw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, name)
        p.end()


class _AddChip(QWidget):
    """The + at the end of the legend: opens the channel picker. Drawn like a chip, so the app's
    toolbar-button style (34 px tall) doesn't stretch the legend's rows."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setFixedSize(26, self.fontMetrics().height() + 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Select channels")

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
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["border"]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.height() / 2, self.height() / 2)
        icon = _material_icon("add", 18, c["text"] if self._hover else c["tab_text"])
        p.drawPixmap((self.width() - 18) // 2, (self.height() - 18) // 2, icon.pixmap(QSize(18, 18)))
        p.end()


class LegendStrip(QScrollArea):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.chips = {}
        self.setFixedHeight(BAND_H)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.flow = FlowLayout(self.body, spacing=2)
        self.flow.setContentsMargins(4, 4, 4, 2)
        self.setWidget(self.body)
        self.add_button = _AddChip()
        self.add_button.clicked.connect(lambda: page.picker.open_for(self.add_button))
        self._paint()
        theme.on_apply(self._paint)
        theme.on_apply(self.fit_to_font)  # the text size lives in the theme's stylesheet
        self.fit_to_font()

    def fit_to_font(self):
        """Two rows of chips, sized from the text; the timeline above it is sized from the text too."""
        chip = self.fontMetrics().height() + 6
        self.add_button.setFixedSize(chip + 4, chip)
        h = 2 * chip + 2 + 6
        self.setFixedHeight(h)
        ov = getattr(self.page, "overview", None)
        if ov is not None:
            ov.setFixedHeight(chip + 14)  # slim: it sits right under the graph's time axis
        for c in self.chips.values():
            c.updateGeometry()
        self.body.updateGeometry()

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit_to_font()
        super().changeEvent(e)

    def _paint(self):
        c = theme.colors()
        self.add_button.update()
        self.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['surface']}; }}")
        for chip in self.chips.values():
            chip.update()

    def rebuild(self):
        """One chip per ticked channel, in the sidebar's order and colours."""
        page = self.page
        while self.flow.count():
            item = self.flow.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.add_button:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.chips = {}
        if page.times is not None:
            for name in page._checked_channels():
                if page.columns.get(name) is None:
                    continue
                color = page.channel_color(name)
                chip = _Chip(name, color, name in page.hidden, self.body)
                chip.setToolTip(self._tip(name))
                chip.toggled.connect(page.toggle_hidden)
                chip.menu_requested.connect(self._menu)
                self.flow.addWidget(chip)
                self.chips[name] = chip
        self.flow.addWidget(self.add_button)
        self.add_button.show()
        self.body.updateGeometry()

    def _tip(self, name):
        page = self.page
        rng = page._ranges.get(name) if hasattr(page, "_ranges") else None
        extra = f"\nRange in this log: {_fmt(rng[0])} – {_fmt(rng[1])}" if rng else ""
        return (f"{name}{extra}\nClick to {'show' if name in page.hidden else 'hide'} its line. "
                "Right-click for more.")

    def _menu(self, name, pos):
        from . import analyze_tools as T
        page = self.page
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        hidden = name in page.hidden
        T.menu_header(menu, "This channel")
        T.menu_action(menu, "Show line" if hidden else "Hide line",
                      "visibility" if hidden else "visibility_off", lambda: page.toggle_hidden(name))
        T.menu_action(menu, "Show only this", "filter_center_focus", lambda: page.show_only(name))
        T.menu_toggle(menu, "Own y axis", "stacked_line_chart", name in page.own_axis,
                      lambda _on: page.toggle_own_axis(name), enabled=not page._fitted and not hidden)
        T.menu_action(menu, "Highlight", "ink_highlighter", lambda: page.highlight_channel(name))
        T.menu_action(menu, "Remove from graph", "close", lambda: page.remove_channel(name))
        T.menu_header(menu, "All channels")
        T.menu_action(menu, "Show all", "visibility", lambda: page.show_only(None), enabled=bool(page.hidden))
        menu.exec(pos)
