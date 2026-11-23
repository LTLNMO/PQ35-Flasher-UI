"""Analyze's channel list, laid out like a datalog viewer's: only the channels on the graph,
each row = drag handle, colour dot, name, value(s), and a thin bar under it showing where the
value at the cursor sits between the log's lowest and highest. A "Select channels (n/N)" button
opens a searchable checklist of every channel.

The rows stay QTreeWidgetItems (check state = on the graph, ROLE = channel name), so the page's
code that walks the tree keeps working. Unticked rows are just hidden."""

from contextlib import contextmanager

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (

    QAbstractItemView, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QStyle, QStyledItemDelegate, QTreeWidget, QVBoxLayout, QWidget,
)

from . import theme
from .widgets import PopupCard

ROLE = Qt.ItemDataRole.UserRole          # channel name
COLOR_ROLE = Qt.ItemDataRole.UserRole + 1  # "#rrggbb" while on the graph
HIDDEN_ROLE = Qt.ItemDataRole.UserRole + 7  # True: ticked but its line hidden (legend click)
FRAC_ROLE = Qt.ItemDataRole.UserRole + 2   # 0..1 position of the cursor value in the log's range
SLOT_ROLE = Qt.ItemDataRole.UserRole + 3   # colour slot, kept while ticked so colours don't jump
VALUE_ROLE = Qt.ItemDataRole.UserRole + 8  # Logger / Live View: the channel's catalog entry (dict)
CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 9  # heading it sits under in Select channels ("Boost"…)
FIXED_ROLE = Qt.ItemDataRole.UserRole + 10  # always on, not in Select channels (Logger raw groups)
RAW_GROUP_ROLE = Qt.ItemDataRole.UserRole + 11  # Logger / Live View: the raw group (int) a row belongs to (✕ removes it)
STALE_ROLE = Qt.ItemDataRole.UserRole + 12  # Logger: True while a channel has stopped updating (values in amber)
ALARM_ROLE = Qt.ItemDataRole.UserRole + 13  # Logger: True while a channel is past its alarm limit (values in red)
RAW_CATEGORY = "Raw groups"

ROW_H = 46
BAR_H = 4
BAR_GAP = 10   # bar's distance from the row's bottom edge
TEXT_H = ROW_H - BAR_GAP - BAR_H - 4  # name/value band above the bar
ROW_H_NO_BARS = 32  # rows without range bars
HANDLE_W = 16


class _RowDelegate(QStyledItemDelegate):
    """Paints one cell: column 0 = handle, dot, name; others = right-aligned value. No item
    background (the tree paints a whole-row one so it reads as one card)."""

    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), ROW_H if self.tree.show_bars else ROW_H_NO_BARS)

    def paint(self, painter, option, index):
        c = theme.colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        band = QRect(r.left(), r.top() + 4, r.width(), TEXT_H) if self.tree.show_bars else r
        if index.column() == 0:
            x = r.left() + 8
            # drag handle: 2 x 3 dots
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["muted"]))
            cy = band.center().y()
            for dx in (0, 4):
                for dy in (-4, 0, 4):
                    painter.drawEllipse(QRectF(x + dx, cy + dy - 1, 2, 2))
            x += HANDLE_W
            color = index.data(COLOR_ROLE)
            hidden = bool(index.data(HIDDEN_ROLE))
            if hidden:  # hollow dot: on the list, but its line is hidden
                painter.setPen(QPen(QColor(color or c["muted"]), 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(x + 0.7, cy - 2.8, 5.6, 5.6))
            else:
                painter.setBrush(QColor(color or c["muted"]))
                painter.drawEllipse(QRectF(x, cy - 3.5, 7, 7))
            x += 15
            painter.setPen(QColor(c["muted"] if hidden else c["text"]))
            painter.setFont(option.font)
            text = option.fontMetrics.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight,
                                                 r.right() - x - 4)
            painter.drawText(QRect(x, band.top(), r.right() - x, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        else:
            text = str(index.data() or "")
            font = self.mono
            font.setPointSizeF(option.font.pointSizeF())
            painter.setFont(font)
            if index.siblingAtColumn(0).data(STALE_ROLE):
                painter.setPen(QColor(c["warn"]))  # stopped updating ("no data 4 s")
            elif index.siblingAtColumn(0).data(ALARM_ROLE):
                painter.setPen(QColor(c["err"]))  # past its alarm limit
            else:
                painter.setPen(QColor(c["text"] if index.column() == self.tree.primary_column() else c["muted"]))
            painter.drawText(band.adjusted(0, 0, -10, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)
        painter.restore()


CHANNEL_MIME = "application/x-pq35-channel"  # channel names, one per line (Split rows take it)


class ChannelTree(QTreeWidget):
    """Flat list of the graph's channels with drag-to-reorder and a range bar under each row."""

    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_RowDelegate(self))
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # The rows paint their own background (drawRow), so the theme's per-cell one is switched off.
        self.setStyleSheet("QTreeWidget::item, QTreeWidget::item:hover, QTreeWidget::item:selected "
                           "{ background: transparent; border: none; padding: 0; }")
        self.empty_text = ""
        self.show_bars = True

    def set_show_bars(self, on):
        """Range bars under the rows on or off (rows get shorter without them)."""
        self.show_bars = bool(on)
        self.scheduleDelayedItemsLayout()  # row heights come from the delegate's sizeHint
        self.viewport().update()

    def primary_column(self):
        """The first value column shown (drawn brighter than the rest)."""
        for col in range(1, self.columnCount()):
            if not self.isColumnHidden(col):
                return col
        return -1

    @contextmanager
    def bulk(self):
        """Build many rows at once: new_item() makes them detached (setting their data then
        costs nothing) and they join the list together at the end. Attached one by one, every
        setData repainted through dataChanged: ~0.6 ms a row. sync_rows() afterwards shows the
        ticked ones (rows join hidden)."""
        self._pending = []
        try:
            yield
        finally:
            pending, self._pending = self._pending, None
            self.addTopLevelItems(pending)
            for item in pending:
                item.setHidden(True)  # a detached row can't be hidden: do it now it's in the list

    def new_item(self, name, key=None):
        """A row for a channel: shown as `name`, known by `key` (ROLE; the name when not given)."""
        from PySide6.QtWidgets import QTreeWidgetItem
        pending = getattr(self, "_pending", None)
        texts = [name] + [""] * (self.columnCount() - 1)
        if pending is not None:
            item = QTreeWidgetItem(texts)
            pending.append(item)
        else:
            item = QTreeWidgetItem(self, texts)
        # Draggable, never a drop target (a flat list: no channel inside another). Not
        # user-checkable: ticking happens in the Select channels list.
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                      & ~Qt.ItemFlag.ItemIsDropEnabled & ~Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setData(0, ROLE, key if key is not None else name)
        item.setHidden(True)
        item.setToolTip(0, name)
        return item

    def items(self):
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount())]

    def checked_items(self):
        return [it for it in self.items() if it.checkState(0) == Qt.CheckState.Checked]

    def sync_rows(self, settings=None):
        """Show only ticked channels; give each a colour slot it keeps while ticked (so adding,
        removing or reordering others never recolours it). Returns {key: colour}."""
        from . import graphstyle
        items = self.items()
        on = [it for it in items if it.checkState(0) == Qt.CheckState.Checked]
        used, colors = set(), {}
        self.blockSignals(True)
        for it in items:
            if it.checkState(0) != Qt.CheckState.Checked:
                it.setHidden(True)
                it.setData(0, SLOT_ROLE, None)
                it.setData(0, COLOR_ROLE, None)
                it.setData(0, FRAC_ROLE, None)
        for it in on:  # keep existing slots first
            slot = it.data(0, SLOT_ROLE)
            if slot is not None and slot not in used:
                used.add(slot)
            else:
                it.setData(0, SLOT_ROLE, None)
        for it in on:
            slot = it.data(0, SLOT_ROLE)
            if slot is None:
                slot = next(s for s in range(len(used) + 1) if s not in used)  # lowest free
                used.add(slot)
                it.setData(0, SLOT_ROLE, slot)
            it.setHidden(False)
            color = graphstyle.color(slot, settings)  # never runs out: palette, then extras
            it.setData(0, COLOR_ROLE, color)
            colors[it.data(0, ROLE)] = color
        self.blockSignals(False)
        return colors

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()

    def mimeData(self, items):
        """Drags carry the channel names too, so they can be dropped onto Split rows."""
        mime = super().mimeData(items)
        mime.setData(CHANNEL_MIME, "\n".join(it.data(0, ROLE) for it in items).encode())
        return mime

    def dataChanged(self, top_left, bottom_right, roles=()):
        """The range bar runs under every column, but Qt only repaints the cell that changed:
        the rest of the old bar would stay behind. So repaint the whole width of those rows."""
        super().dataChanged(top_left, bottom_right, roles)
        if not self.show_bars or (roles and FRAC_ROLE not in roles):
            return
        top = self.visualRect(top_left.siblingAtColumn(0))
        bottom = self.visualRect(bottom_right.siblingAtColumn(0))
        if top.isValid() and bottom.isValid():
            vp = self.viewport()
            vp.update(QRect(0, top.top(), vp.width(), bottom.bottom() - top.top() + 1))

    def drawRow(self, painter, option, index):
        c = theme.colors()
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = self.selectionModel().isSelected(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(QColor(c["border"]) if selected else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["surface2"]))
            painter.drawRoundedRect(r, 7, 7)
        painter.restore()
        super().drawRow(painter, option, index)
        # Range bar across the whole row
        item = self.itemFromIndex(index)
        if item is None or not self.show_bars:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left = option.rect.left() + 10
        right = self.viewport().width() - 10
        track = QRectF(left, option.rect.bottom() - BAR_GAP - BAR_H + 1, max(right - left, 0), BAR_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["border"]))
        painter.drawRoundedRect(track, BAR_H / 2, BAR_H / 2)
        frac = item.data(0, FRAC_ROLE)
        color = item.data(0, COLOR_ROLE)
        if frac is not None and color:
            fill = QRectF(track)
            fill.setWidth(max(track.width() * min(max(float(frac), 0.0), 1.0), BAR_H))
            path = QPainterPath()
            path.addRoundedRect(fill, BAR_H / 2, BAR_H / 2)
            # Saturation rises along the track: greyish at the log's low end, the line's full
            # colour at its high end, so a bar near the top of its range reads stronger.
            full = QColor(color)
            h, sat, v, _a = full.getHsvF()
            low = QColor.fromHsvF(max(h, 0.0), sat * 0.18, v * 0.85 + 0.15 * (1 - v))
            grad = QLinearGradient(track.left(), 0, track.right(), 0)
            grad.setColorAt(0.0, low)
            grad.setColorAt(1.0, full)
            painter.fillPath(path, grad)
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.empty_text and not any(not it.isHidden() for it in self.items()):
            p = QPainter(self.viewport())
            p.setPen(QColor(theme.colors()["muted"]))
            p.drawText(self.viewport().rect().adjusted(16, 16, -16, -16),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            p.end()


def _ui_scale():
    """Settings → Font size relative to the default 13: rows and switches grow and shrink with it."""
    return max(0.7, min(1.6, theme.font_size() / 13))


class _SwitchRowDelegate(QStyledItemDelegate):
    """Picker row: colour dot, channel name, on/off switch at the right; whole row hovers.
    Every switch (channels and category headings) is one size, scaled with the font size."""

    ROW = 38       # at font size 13; see row()
    HEADING = 34
    SWITCH = (38.0, 22.0)

    def __init__(self, picker):
        super().__init__(picker.list)
        self.picker = picker

    @classmethod
    def row(cls, heading=False):
        if theme.is_mac():  # Mac lists: compact rows, as in a popover or Finder sidebar
            return round((28 if heading else 26) * _ui_scale())
        return round((cls.HEADING if heading else cls.ROW) * _ui_scale())

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), self.row(bool(index.data(_HEADING_ROLE))))

    @classmethod
    def switch_rect(cls, row_rect):
        k = _ui_scale()
        size = (26.0, 15.0) if theme.is_mac() else cls.SWITCH  # Mac: System Settings' switch (mini, measured)
        w, h = size[0] * k, size[1] * k
        r = QRectF(row_rect).adjusted(2, 1, -2, -1)
        return QRectF(r.right() - 12 - w, r.center().y() - h / 2, w, h)

    @staticmethod
    def remove_rect(sw):
        """The ✕ left of a raw group row's switch."""
        k = _ui_scale()
        size = 18 * k
        return QRectF(sw.left() - 10 * k - size, sw.center().y() - size / 2, size, size)

    def _paint_add(self, painter, option, c):
        """＋ Add group… (accent, like a link)."""
        from .main_window import _material_icon
        painter.save()
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["surface2"]))
            painter.drawRoundedRect(r, 7, 7)
        icon = _material_icon("add", 18, c["accent"])
        painter.drawPixmap(int(r.left() + 7), int(r.center().y() - 9), icon.pixmap(QSize(18, 18)))
        painter.setPen(QColor(c["accent"]))
        painter.setFont(option.font)
        painter.drawText(QRect(int(r.left() + 30), int(r.top()), int(r.width() - 40), int(r.height())),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Add group…")
        painter.restore()

    @staticmethod
    def draw_switch(painter, sw, state, c):
        """macOS-style switch; state "on", "off" or "some" (part of a category on: dimmer
        track, knob in the middle)."""
        mac = theme.is_mac()
        h = sw.height()
        inset = h / 15.0 if mac else 2.0  # Mac: the knob is 13 of 15 pt (measured from System Settings)
        cy = sw.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        if state == "off":  # a light grey wash, like the system switch when off
            track = QColor(c["text"])
            track.setAlpha(28 if mac else 45)
        else:
            track = QColor(c["accent"])
            if state == "some":
                track.setAlpha(120)
        painter.setBrush(track)
        if mac and state == "off":  # the system switch's hairline edge
            edge = QColor(c["text"])
            edge.setAlpha(40)
            painter.setPen(QPen(edge, 0.5))
            painter.drawRoundedRect(sw.adjusted(0.25, 0.25, -0.25, -0.25), h / 2, h / 2)
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.drawRoundedRect(sw, h / 2, h / 2)
        knob = h - 2 * inset
        kx = {"on": sw.right() - inset - knob, "off": sw.left() + inset}.get(
            state, sw.center().x() - knob / 2)
        painter.setBrush(QColor(0, 0, 0, 70))  # soft shadow under the knob
        painter.drawEllipse(QRectF(kx - 0.3, cy - knob / 2 + 0.8, knob + 0.6, knob + 0.6))
        dark = QColor(c["bg"]).lightness() < 128
        painter.setBrush(QColor("#cdcdcd" if mac and dark else "#ffffff"))  # dark mode's knob is light grey
        painter.drawEllipse(QRectF(kx, cy - knob / 2, knob, knob))

    def _paint_heading(self, painter, option, index, c):
        """A category: ▸/▾ (click to open or close it), its name and how many are on, and a
        switch for the whole category (all on / some on / off)."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if index.row() == self.picker.hover_row:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["surface2"]))
            painter.drawRoundedRect(r, 7, 7)
        from .main_window import _material_icon
        open_ = index.data(_OPEN_ROLE)
        icon = _material_icon("expand_more" if open_ else "chevron_right", 18, c["muted"])
        painter.drawPixmap(int(r.left() + 6), int(r.center().y() - 9), icon.pixmap(QSize(18, 18)))
        on, total = index.data(_COUNT_ROLE) or (0, 0)
        fixed = bool(index.data(_FIXED_ROLE))
        if not fixed:
            state = "off" if on == 0 else "on" if on == total else "some"
            self.draw_switch(painter, self.switch_rect(option.rect), state, c)
        f = QFont(option.font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(c["text"]))
        left = int(r.left() + 28)
        right = int(r.right() - (12 + self.SWITCH[0] * _ui_scale() + 10 if not fixed else 12))
        name = str(index.data())
        count = f"  {on}/{total}" if total else ""
        fm = QFontMetrics(f)
        name = fm.elidedText(name, Qt.TextElideMode.ElideRight,
                             max(right - left - QFontMetrics(option.font).horizontalAdvance(count), 20))
        painter.drawText(QRect(left, int(r.top()), right - left, int(r.height())),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        painter.setFont(option.font)
        painter.setPen(QColor(c["muted"]))
        painter.drawText(QRect(left + fm.horizontalAdvance(name), int(r.top()), right - left, int(r.height())),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, count)
        painter.restore()

    def paint(self, painter, option, index):
        c = theme.colors()
        if index.data(_HEADING_ROLE):
            self._paint_heading(painter, option, index, c)
            return
        if index.data(_ADD_ROLE):
            self._paint_add(painter, option, c)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["surface2"]))
            painter.drawRoundedRect(r, 7, 7)
        state = index.data(Qt.ItemDataRole.CheckStateRole)  # may come back as a plain int
        on = state is not None and Qt.CheckState(state) == Qt.CheckState.Checked
        cy = r.center().y()
        # dot: the line's colour while it's on the graph
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.picker.colour_of(index.data(_KEY_ROLE)) or c["muted"]))
        painter.drawEllipse(QRectF(r.left() + 12, cy - 4, 8, 8))
        # switch, proportioned like macOS's: the knob nearly fills the track's height
        sw = self.switch_rect(option.rect)
        self.draw_switch(painter, sw, "on" if on else "off", c)
        end = sw.left()
        if index.data(_RAW_ROLE) is not None:  # ✕: remove this raw group
            from .main_window import _material_icon
            x = self.remove_rect(sw)
            hot = option.state & QStyle.StateFlag.State_MouseOver
            icon = _material_icon("close", int(x.width()), c["text"] if hot else c["muted"])
            painter.drawPixmap(x.topLeft().toPoint(), icon.pixmap(QSize(int(x.width()), int(x.height()))))
            end = x.left()
        # name
        painter.setPen(QColor(c["text"] if on else c["muted"]))
        painter.setFont(option.font)
        left = int(r.left() + 30)
        width = int(end - 10 - left)
        text = option.fontMetrics.elidedText(str(index.data()), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(left, int(r.top()), width, int(r.height())),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


_KEY_ROLE = Qt.ItemDataRole.UserRole       # picker row: the channel's key (tree ROLE)
_HEADING_ROLE = Qt.ItemDataRole.UserRole + 1  # picker row: a category heading, not a channel
_FIXED_ROLE = Qt.ItemDataRole.UserRole + 2    # picker row: always on (can't be switched off)
_OPEN_ROLE = Qt.ItemDataRole.UserRole + 3     # heading: its category is open (channels shown)
_COUNT_ROLE = Qt.ItemDataRole.UserRole + 4    # heading: (channels on, channels in it)
_MATCH_ROLE = Qt.ItemDataRole.UserRole + 5    # channel row: matches the search (shown or not)
_ADD_ROLE = Qt.ItemDataRole.UserRole + 6      # the "＋ Add group…" row at the end of Raw groups
_RAW_ROLE = Qt.ItemDataRole.UserRole + 7      # picker row: its raw group (int), drawn with ✕


class ChannelSelector(QWidget):
    """Every channel with an on/off switch, a search box and Select all / Hide all; switching
    one applies straight away. Channels with a category (Logger, Live View) sit under its
    heading. Used inside Analyze's Select channels popup (ChannelPicker) and, embedded, as the
    whole channel sidebar of the Logger and Live View. `tree` (a ChannelTree, shown or not)
    holds the channels, their on/off state and colours."""

    menu_requested = Signal(str, object)  # channel key, global position (right-click)
    focused = Signal(str)                 # channel key under the mouse / clicked
    add_raw_group = Signal()              # ＋ Add group… (only when raw groups are enabled)
    remove_raw_group = Signal(int)        # ✕ on a raw group's row

    def __init__(self, tree, on_change, embedded=False, parent=None):
        super().__init__(parent)
        self.tree, self.on_change, self.embedded = tree, on_change, embedded
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0) if embedded else lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("pickerSearch")
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self._search_icon = self.search.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        top.addWidget(self.search, 1)
        self.top_row = top  # pages may add buttons (e.g. × to hide the sidebar)
        self.select_all_button = QPushButton()
        self.select_all_button.setToolTip("Select all: switch on every channel the search finds")
        self.select_all_button.clicked.connect(lambda: self._set_shown(True))
        self.hide_all_button = QPushButton()
        self.hide_all_button.setToolTip("Hide all: switch off every channel the search finds")
        self.hide_all_button.clicked.connect(lambda: self._set_shown(False))
        for b in (self.select_all_button, self.hide_all_button):
            if theme.is_mac():
                b.setObjectName("iconTool")  # plain glyphs, like the Mac toolbars
            b.setFixedSize(36, 34)
            b.setIconSize(QSize(20, 20))
            top.addWidget(b)
        lay.addLayout(top)
        # Optional (Live View): All · 1 Dials · 2 Graph … — the switches then say what that panel
        # shows instead of what's read. `scope_source` (set by the page) provides the panels.
        self.scope_source = None
        self.scope = None
        self.scope_bar = QWidget()
        self.scope_row = QHBoxLayout(self.scope_bar)
        self.scope_row.setContentsMargins(0, 0, 0, 0)
        self.scope_row.setSpacing(2)
        self.scope_bar.hide()
        lay.addWidget(self.scope_bar)
        if not embedded:
            rule = QFrame()
            rule.setObjectName("optionsRule")
            rule.setFixedHeight(1)
            lay.addWidget(rule)
        self.list = QListWidget()
        self.list.setObjectName("pickerList")
        self.list.setItemDelegate(_SwitchRowDelegate(self))
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(False)  # category headings are shorter than channel rows
        self.list.itemClicked.connect(self._clicked)
        self.list.itemChanged.connect(self._changed)
        self.list.itemEntered.connect(lambda li: self._focus(li))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.list, 1)
        self._filling = False
        self._by_name = {}
        self.raw_groups_enabled = False  # Logger / Live View: a Raw groups category with ＋ Add group…
        # Categories start closed (so the list isn't one long scroll); raw groups start open.
        self._open = {"Raw groups"}
        self.hover_row = -1
        self.list.viewport().installEventFilter(self)  # headings: open/close, whole-category switch
        self._restyle_picker()
        theme.on_apply(self._restyle_picker)

    def _restyle_picker(self):
        from .main_window import _material_icon
        c = theme.colors()
        self._search_icon.setIcon(_material_icon("search", 18, c["muted"]))
        self.select_all_button.setIcon(_material_icon("done_all", 20, c["tab_text"]))
        self.hide_all_button.setIcon(_material_icon("visibility_off", 20, c["tab_text"]))
        # embedded: the list sits in a card like the other lists; in the popup, the card is the popup
        frame = (f"background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;"
                 if self.embedded else "background: transparent; border: none;")
        self.setStyleSheet(
            f"QLineEdit#pickerSearch {{ background: {c['bg']}; border: 1px solid {c['border']}; "
            f"border-radius: 17px; padding: 6px 10px 6px 4px; min-height: 22px; }}"
            f"QLineEdit#pickerSearch:focus {{ border-color: {c['accent']}; }}"
            f"QListWidget#pickerList {{ {frame} }}"
            f"QListWidget#pickerList::item, QListWidget#pickerList::item:hover {{ background: transparent; }}")

    def colour_of(self, name):
        item = self._by_name.get(name)
        return item.data(0, COLOR_ROLE) if item is not None else None

    def _fill_scopes(self):
        """The All · panel segments (only with a scope_source and two or more panels)."""
        scopes = self.scope_source.scopes() if self.scope_source is not None else []
        if len(scopes) < 3:  # All + one panel: the panel shows what's read, nothing to choose
            self.scope = None
            self.scope_bar.hide()
            return
        if self.scope not in [sid for sid, _l, _t in scopes]:
            self.scope = None
        if scopes == getattr(self, "_scopes", None):  # same panels: just move the highlight
            from .widgets import set_segment_active
            set_segment_active(self._scope_buttons, [sid for sid, _l, _t in scopes].index(self.scope))
            self.scope_bar.show()
            return
        from .widgets import discard, segment_button, segment_track, set_segment_active
        for b in getattr(self, "_scope_buttons", ()):
            discard(b)
        self._scopes, self._scope_buttons = scopes, []
        for sid, label, tip in scopes:
            b = segment_button(label, tip)
            b.clicked.connect(lambda _=False, sid=sid: self.set_scope(sid))
            self._scope_buttons.append(b)
        segment_track(self.scope_bar, self.scope_row, self._scope_buttons, equal=True)
        set_segment_active(self._scope_buttons, [sid for sid, _l, _t in scopes].index(self.scope))
        self.scope_bar.show()

    def set_scope(self, scope):
        """All (None) or one panel: the switches follow it."""
        self.scope = scope
        self.fill()

    def fill(self):
        """(Re)build the rows from the tree, keeping what's typed in the search box."""
        self._fill_scopes()
        self._filling = True
        self.list.clear()
        items = self.tree.items()
        if self.scope is not None:  # a panel: every channel, on = it shows it (and so it's read)
            items = [it for it in items if not it.data(0, FIXED_ROLE)]
        self._by_name = {it.data(0, ROLE): it for it in items}
        heading = None

        def is_raw(it):
            return it.data(0, FIXED_ROLE) or it.data(0, CATEGORY_ROLE) == RAW_CATEGORY

        raw = sorted((it for it in items if is_raw(it)),
                     key=lambda it: (bool(it.data(0, FIXED_ROLE)), self._header_index(it)))
        rows = sorted((it for it in items if not is_raw(it)), key=self._header_index)
        # One heading per category (as Drew's logger had): the catalog interleaves them, so
        # gather each category's channels together, categories in first-seen order.
        first_seen = {}
        for it in rows:
            first_seen.setdefault(it.data(0, CATEGORY_ROLE), len(first_seen))
        rows.sort(key=lambda it: first_seen[it.data(0, CATEGORY_ROLE)])  # stable: keeps catalog order inside

        def add_heading(category, fixed):
            hi = QListWidgetItem(category)
            hi.setData(_HEADING_ROLE, True)
            hi.setData(_OPEN_ROLE, category in self._open)
            hi.setData(_FIXED_ROLE, fixed)
            hi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            hi.setToolTip("Click to open or close; the switch turns the whole category on or off")
            self.list.addItem(hi)

        for item in rows + raw:
            category = RAW_CATEGORY if is_raw(item) else item.data(0, CATEGORY_ROLE)
            if category and category != heading:  # Logger / Live View: grouped like the catalog
                heading = category
                add_heading(category, category == RAW_CATEGORY and all(it.data(0, FIXED_ROLE) for it in raw))
            li = QListWidgetItem(item.text(0))
            li.setData(_KEY_ROLE, item.data(0, ROLE))
            li.setData(_HEADING_ROLE, False)
            li.setData(_FIXED_ROLE, bool(item.data(0, FIXED_ROLE)))
            li.setData(CATEGORY_ROLE, category or "")
            li.setFlags((li.flags() | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsUserCheckable)
            if self.scope is None:
                li.setCheckState(item.checkState(0))
            else:
                shown = self.scope_source.is_on(self.scope, item.data(0, ROLE))
                li.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
            li.setToolTip(item.toolTip(0) or item.text(0))
            group = item.data(0, RAW_GROUP_ROLE)
            if group is not None:
                li.setData(_RAW_ROLE, int(group))
            self.list.addItem(li)
        if self.raw_groups_enabled and self.scope is None:  # ＋ Add group… closes the list (under its own heading if none yet)
            if heading != RAW_CATEGORY:
                heading = RAW_CATEGORY
                add_heading(RAW_CATEGORY, True)
            li = QListWidgetItem("Add group")
            li.setData(_HEADING_ROLE, False)
            li.setData(_ADD_ROLE, True)
            li.setData(_FIXED_ROLE, True)
            li.setData(CATEGORY_ROLE, RAW_CATEGORY)
            li.setFlags(Qt.ItemFlag.ItemIsEnabled)
            li.setToolTip("Add a measuring group by number: every field it has, unnamed. Handy for "
                          "groups with no named values, or to find out what a field shows.")
            self.list.addItem(li)
        self._filling = False
        self._filter(self.search.text())
        return heading is not None

    def _header_index(self, item):
        order = getattr(self.tree, "log_order", None) or []
        name = item.data(0, ROLE)
        return order.index(name) if name in order else len(order)

    def _filter(self, text=None):
        """Show the channels whose name (or category) has every word typed. While searching,
        every category with a match shows its matches; otherwise closed categories show just
        their heading."""
        if text is None:
            text = self.search.text()
        words = text.lower().split()
        searching = bool(words)
        # Storing these on the rows fires the list's itemChanged, which means "a switch moved":
        # mark it as our own update so it isn't applied (and doesn't loop back here).
        was, self._filling = self._filling, True
        heading, heading_match = None, False
        for i in range(self.list.count()):
            li = self.list.item(i)
            if li.data(_HEADING_ROLE):
                if heading is not None:
                    heading.setHidden(not heading_match)
                heading, heading_match = li, False
                li.setData(_OPEN_ROLE, searching or li.text() in self._open)
                continue
            hay = (li.text() + " " + (li.data(CATEGORY_ROLE) or "")).lower()
            match = all(w in hay for w in words) or (li.data(_ADD_ROLE) and not searching)
            li.setData(_MATCH_ROLE, match)
            closed = heading is not None and not heading.data(_OPEN_ROLE)
            li.setHidden(not match or closed)
            heading_match = heading_match or match
        if heading is not None:
            heading.setHidden(not heading_match)
        self._filling = was
        self._count_headings()
        self._update_all_button()

    def _count_headings(self):
        """Each heading's (on, total), for its count and its switch."""
        was, self._filling = self._filling, True  # not a switch change (see _filter)
        heading, on, total = None, 0, 0
        for i in range(self.list.count() + 1):
            li = self.list.item(i) if i < self.list.count() else None
            if li is None or li.data(_HEADING_ROLE):
                if heading is not None:
                    heading.setData(_COUNT_ROLE, (on, total))
                heading, on, total = li, 0, 0
                continue
            if li.data(_ADD_ROLE):
                continue
            total += 1
            on += li.checkState() == Qt.CheckState.Checked
        self._filling = was

    def _category_rows(self, heading):
        rows, i = [], self.list.row(heading) + 1
        while i < self.list.count() and not self.list.item(i).data(_HEADING_ROLE):
            rows.append(self.list.item(i))
            i += 1
        return rows

    def _shown_items(self):
        """The channels the search finds (in closed categories too), not the always-on ones."""
        return [li for li in (self.list.item(i) for i in range(self.list.count()))
                if not li.data(_HEADING_ROLE) and not li.data(_FIXED_ROLE)
                and li.data(_MATCH_ROLE) is not False]

    def eventFilter(self, obj, event):
        """Headings: a click on the switch turns the whole category on or off; anywhere else on
        the heading opens or closes it. Also tracks which heading the mouse is over."""
        from PySide6.QtCore import QEvent
        if obj is self.list.viewport():
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Leave):
                li = self.list.itemAt(event.position().toPoint()) if event.type() == QEvent.Type.MouseMove else None
                if li is not None and li is not getattr(self, "_hovered", None):
                    self._hovered = li
                    self._focus(li)  # the description box follows the mouse (no click needed)
                elif li is None:
                    self._hovered = None
                row = self.list.row(li) if li is not None and li.data(_HEADING_ROLE) else -1
                if row != self.hover_row:
                    self.hover_row = row
                    self.list.viewport().update()
                    self.list.viewport().setCursor(Qt.CursorShape.PointingHandCursor if row >= 0
                                                   else Qt.CursorShape.ArrowCursor)
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                li = self.list.itemAt(pos)
                if li is not None and li.data(_ADD_ROLE):
                    self.add_raw_group.emit()
                    return True
                if li is not None and li.data(_RAW_ROLE) is not None:
                    sw = _SwitchRowDelegate.switch_rect(self.list.visualItemRect(li))
                    if _SwitchRowDelegate.remove_rect(sw).adjusted(-5, -5, 5, 5).contains(pos.toPointF()):
                        self.remove_raw_group.emit(int(li.data(_RAW_ROLE)))
                        return True
                if li is not None and li.data(_HEADING_ROLE):
                    sw = _SwitchRowDelegate.switch_rect(self.list.visualItemRect(li))
                    if not li.data(_FIXED_ROLE) and sw.adjusted(-6, -6, 6, 6).contains(pos.toPointF()):
                        self._toggle_category(li)
                    else:
                        self._toggle_open(li)
                    return True
        return super().eventFilter(obj, event)

    def _toggle_open(self, heading):
        name = heading.text()
        if self.search.text().strip():
            return  # while searching every category with a match is open
        self._open.symmetric_difference_update({name})
        self._filter()

    def _toggle_category(self, heading):
        """All on → all off; otherwise → all on (of what the search finds in it)."""
        rows = [li for li in self._category_rows(heading)
                if not li.data(_FIXED_ROLE) and li.data(_MATCH_ROLE) is not False]
        on = not all(li.checkState() == Qt.CheckState.Checked for li in rows)
        self._filling = True
        for li in rows:
            li.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._filling = False
        self._apply()

    def _update_all_button(self):
        """Select all only when something found is off; Hide all only when something is on."""
        states = [li.checkState() == Qt.CheckState.Checked for li in self._shown_items()]
        self.select_all_button.setEnabled(not all(states) if states else False)
        self.hide_all_button.setEnabled(any(states))

    def _set_shown(self, on):
        """Switch on or off every channel the search shows."""
        self._filling = True
        for li in self._shown_items():
            li.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._filling = False
        self._apply()

    def _focus(self, li):
        if li is not None and not li.data(_HEADING_ROLE) and not li.data(_ADD_ROLE):
            self.focused.emit(li.data(_KEY_ROLE))

    def _clicked(self, li):
        if li.data(_HEADING_ROLE):
            return
        self._focus(li)
        if li.data(_FIXED_ROLE):  # always on (Logger raw groups: Clear below removes them)
            return
        on = li.checkState() != Qt.CheckState.Checked
        li.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)  # -> _changed

    def _context_menu(self, pos):
        li = self.list.itemAt(pos)
        if li is not None and not li.data(_HEADING_ROLE) and not li.data(_ADD_ROLE):
            self.menu_requested.emit(li.data(_KEY_ROLE), self.list.viewport().mapToGlobal(pos))

    def _changed(self, item):
        if not self._filling and not item.data(_HEADING_ROLE):
            self._apply()

    def _apply(self):
        wanted = {self.list.item(i).data(_KEY_ROLE) for i in range(self.list.count())
                  if not self.list.item(i).data(_HEADING_ROLE) and not self.list.item(i).data(_ADD_ROLE)
                  and self.list.item(i).checkState() == Qt.CheckState.Checked}
        if self.scope is not None:  # a panel's channels: the page stores them, nothing is read anew
            self.scope_source.set_on(self.scope, wanted)
            self._count_headings()
            self._update_all_button()
            self.list.viewport().update()
            return
        self.tree.blockSignals(True)
        for item in self.tree.items():
            if item.data(0, FIXED_ROLE):
                continue
            on = item.data(0, ROLE) in wanted
            if (item.checkState(0) == Qt.CheckState.Checked) != on:
                item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if on:  # newly added channels go to the bottom of the list, like adding a row
                    idx = self.tree.indexOfTopLevelItem(item)
                    self.tree.takeTopLevelItem(idx)
                    self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.on_change()
        self._count_headings()
        self._update_all_button()
        self.list.viewport().update()  # dots pick up the colours the graph just gave them


class ChannelPicker(PopupCard):
    """The Select channels popup (Logger, Live View, Analyze): a ChannelSelector in a rounded
    card under the button. Under the list, the same on every page: what the channel under the
    mouse shows (describe(key) -> rich text), then `footer` (e.g. the groups summary)."""

    HINT = "Point at a channel to see what it shows."

    def __init__(self, tree, on_change, parent, footer=None, describe=None):
        super().__init__(parent, min_width=340)
        self.tree, self.describe = tree, describe
        self.selector = ChannelSelector(tree, on_change)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.selector)
        self.info = None
        if footer is not None or describe is not None:
            rule = QFrame()
            rule.setObjectName("optionsRule")
            rule.setFixedHeight(1)
            lay.addWidget(rule)
            box = QVBoxLayout()
            box.setContentsMargins(12, 8, 12, 12)
            box.setSpacing(8)
            if describe is not None:
                from .widgets import muted
                self.info = muted(self.HINT)
                self.info.setWordWrap(True)
                self.info.setTextFormat(Qt.TextFormat.RichText)
                self.info.setMinimumHeight(self.info.fontMetrics().lineSpacing() * 3)
                self.info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                box.addWidget(self.info)
                self.selector.focused.connect(self._show_info)
            if footer is not None:
                box.addWidget(footer)
            lay.addLayout(box)
        # what callers and tests reach for
        self.list, self.search = self.selector.list, self.selector.search
        self._clicked, self.colour_of = self.selector._clicked, self.selector.colour_of

    def _show_info(self, key):
        try:
            text = self.describe(key) if key else ""
        except Exception:  # the page's describe() is cosmetic: a blank box, never a broken picker
            text = ""
        self.info.setText(text or self.HINT)

    def open_for(self, anchor):
        self.selector.search.clear()
        self.selector.fill()
        if self.info is not None:
            self.info.setText(self.HINT)
        # One width on every page (Logger, Live View, Analyze), growing with the font size.
        width = round(390 * _ui_scale())
        self.body.setMinimumWidth(width)
        self.body.setMaximumWidth(width)
        # The card reaches the bottom of the window (16 px gap, like the page gutter) and the
        # list takes whatever room the search row and footer leave; it scrolls inside that.
        # Set as one fixed card height, so nothing depends on the size from the last opening.
        from PySide6.QtGui import QGuiApplication
        minimum = 3 * _SwitchRowDelegate.row() + 4
        self.list.setMinimumHeight(minimum)
        self.list.setMaximumHeight(16777215)
        window = anchor.window()
        below = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
        bottom = min(window.mapToGlobal(window.rect().bottomLeft()).y(),
                     screen.availableGeometry().bottom()) - 16
        top = below.y() + 6
        # Opened from low down (the legend's ＋ under Analyze's graph): open upwards instead,
        # reaching the top of the window, so it's as roomy as from a Select channels button.
        above = anchor.mapToGlobal(anchor.rect().topLeft()).y() - 6
        ceiling = max(window.mapToGlobal(window.rect().topLeft()).y(),
                      screen.availableGeometry().top()) + 16
        upwards = above - ceiling > 2 * (bottom - top)
        room = above - ceiling if upwards else bottom - top
        self.setFixedHeight(max(room, self.minimumSizeHint().height()))
        self.open_at(anchor, align="left")
        if upwards:
            self.move(self.x(), above - self.height())
        self.search.setFocus()


class ChannelDropdown(QPushButton):
    """Logger / Live View: "Select channels (n/N) ▾", opening the Analyze-style ChannelPicker
    (instead of a sidebar that's always there). `tree` (hidden) holds the channels; `footer`
    (optional) sits under the list in the popup. Disabled while running."""

    def __init__(self, tree, on_change, parent, footer=None, describe=None):
        super().__init__("Select channels", parent)
        self.tree, self._on_change = tree, on_change
        self.compact = False  # row 1 too narrow (mac_shell rule 6): just "(n/N)", the name in the tooltip
        self.picker = ChannelPicker(tree, self._changed, parent, footer=footer, describe=describe)
        self.selector = self.picker.selector
        self.setToolTip("Choose which channels to read")
        self.clicked.connect(lambda: self.picker.open_for(self))
        from .widgets import pulldown
        pulldown(self)  # text + chevron (the same on every tab)

    def _changed(self):
        self._on_change()
        self.update_count()

    def refresh(self):
        """After the channels behind it change (catalog, preset, alarms)."""
        self.selector.fill()
        self.update_count()

    def update_count(self):
        rows = [it for it in self.tree.items() if not it.data(0, FIXED_ROLE)]
        on = sum(it.checkState(0) == Qt.CheckState.Checked for it in rows)
        count = f"({on}/{len(rows)})" if rows else ""
        if self.compact:
            self.setText(count or "Channels")
            self.setToolTip(f"Select channels {count}: choose which channels to read".replace("  ", " "))
        else:
            self.setText(f"Select channels {count}".strip())
            self.setToolTip("Choose which channels to read")

    def set_compact(self, on):
        """mac_shell.fit_row: the short form while row 1 doesn't fit."""
        if on != self.compact:
            self.compact = on
            self.update_count()

    def close_picker(self):
        self.picker.hide()


def groups_summary(values, groups, raw=0):
    """The line under Logger's and Live View's channel list: '5 values · 3 groups · fewer groups
    update faster'. Each group is another read from the module per cycle."""
    if not values and not raw:
        return ""
    parts = [f"{values} value{'s' if values != 1 else ''}"]
    if raw:
        parts[0] += f" + {raw} raw group{'s' if raw != 1 else ''}"
    parts.append(f"{len(groups)} group{'s' if len(groups) != 1 else ''}")
    if len(groups) > 1:
        parts.append("fewer groups update faster")
    return " · ".join(parts)


def ask_raw_group(parent, existing=()):
    """＋ Add group…: a measuring group number (1–255), or None."""
    from .popups import InputDialog, MessageBox
    while True:
        text, ok = InputDialog.getText(parent, "Add raw group",
                                       "Measuring group number (1–255). Every field it has is read, "
                                       "unnamed; the fields show up once it's been read.")
        if not ok or not text.strip():
            return None
        try:
            group = int(text.strip())
        except ValueError:
            group = 0
        if not 1 <= group <= 255:
            MessageBox.warning(parent, "Add raw group", "Enter a number from 1 to 255.")
            continue
        if group in existing:
            MessageBox.information(parent, "Add raw group", f"Group {group} is already in the list.")
            return None
        return group
