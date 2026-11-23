"""Menus the app's way: headers, items with Material icons, toggles, sub-menus, the presets
manager, and placing a menu under the button that opens it (menus open below their button,
left-aligned, kept on screen). Used by every page."""
from PySide6.QtCore import QObject, Qt


def menu_header(menu, text, before=None):
    """Start a new group of menu items: a separator (no title; the user preferred plain
    separators). `text` names the group in the code only. Qt drops a separator at the very top
    of a menu or right after another one, so groups can start anywhere."""
    menu.setSeparatorsCollapsible(True)
    return menu.insertSeparator(before) if before is not None else menu.addSeparator()


def menu_icon(glyph, color=None):
    from .main_window import _material_icon
    from . import theme
    return _material_icon(glyph, 18, color or theme.colors()["tab_text"])


def menu_action(menu, text, glyph, slot=None, enabled=True, tip=""):
    """A menu item with a Material icon."""
    act = menu.addAction(menu_icon(glyph), text)
    act.setIconVisibleInMenu(True)  # macOS hides menu icons unless asked (AA_DontShowIconsInMenus)
    act.setEnabled(enabled)
    if tip:
        act.setToolTip(tip)
    if slot is not None:
        act.triggered.connect(slot)
    return act


def menu_toggle(menu, text, glyph, on, slot, enabled=True, tip=""):
    """An on/off item shown the way the toolbar shows its toggles: the icon in the accent colour
    while on, grey while off (an icon hides the menu's own check mark). slot gets the new state."""
    from . import theme
    c = theme.colors()
    act = menu.addAction(menu_icon(glyph, c["accent"] if on else c["tab_text"]), text)
    act.setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
    act.setEnabled(enabled)
    act.setToolTip((tip + "\n" if tip else "") + ("On: click to turn off" if on else "Off: click to turn on"))
    act.triggered.connect(lambda _=False: slot(not on))
    return act


def menu_sub(menu, text, glyph, sub=None):
    """A submenu with an icon (sub: an existing QMenu to add, else a new one)."""
    if sub is None:
        sub = menu.addMenu(text)
    else:
        menu.addMenu(sub)
    sub.menuAction().setIcon(menu_icon(glyph))
    sub.menuAction().setIconVisibleInMenu(True)  # macOS hides menu icons unless asked
    sub.setToolTipsVisible(True)
    return sub


def manage_presets(parent, get, rename, delete, empty_text, tip=None):
    """Rename or delete saved presets. get() -> {name: anything}; rename(old, new); delete(name);
    tip(name, data) -> a row's tooltip."""
    from PySide6.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                                   QPushButton, QVBoxLayout)
    from .popups import InputDialog, MessageBox, OverlayDialog
    dlg = OverlayDialog(parent)
    dlg.setWindowTitle("Manage presets")
    lay = QVBoxLayout(dlg)
    lst = QListWidget()
    lst.setMinimumSize(320, 220)
    lay.addWidget(lst)
    empty = QLabel(empty_text)
    empty.setObjectName("muted")
    empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(empty)
    row = QHBoxLayout()
    rename_button = QPushButton(menu_icon("edit"), "Rename…")
    delete_button = QPushButton(menu_icon("delete"), "Delete")
    row.addWidget(rename_button)
    row.addWidget(delete_button)
    row.addStretch(1)
    lay.addLayout(row)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dlg.reject)
    box.accepted.connect(dlg.accept)
    lay.addWidget(box)

    def fill(select=None):
        lst.clear()
        for name, data in get().items():
            lst.addItem(name)
            if tip:
                lst.item(lst.count() - 1).setToolTip(tip(name, data))
            if name == select:
                lst.setCurrentRow(lst.count() - 1)
        if lst.currentRow() < 0 and lst.count():
            lst.setCurrentRow(0)
        lst.setVisible(lst.count() > 0)
        empty.setVisible(lst.count() == 0)
        rename_button.setEnabled(lst.currentRow() >= 0)
        delete_button.setEnabled(lst.currentRow() >= 0)

    def do_rename():
        it = lst.currentItem()
        if it is None:
            return
        old = it.text()
        new, ok = InputDialog.getText(parent, "Rename preset", "New name:", text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        if new in get() and MessageBox.question(
                parent, "Rename preset", f"“{new}” already exists. Replace it?") != MessageBox.StandardButton.Yes:
            return
        rename(old, new)
        fill(new)

    def do_delete():
        it = lst.currentItem()
        if it is None:
            return
        if MessageBox.question(parent, "Delete preset", f"Delete “{it.text()}”?") != MessageBox.StandardButton.Yes:
            return
        delete(it.text())
        fill()

    rename_button.clicked.connect(do_rename)
    delete_button.clicked.connect(do_delete)
    lst.itemDoubleClicked.connect(lambda _: do_rename())
    lst.currentRowChanged.connect(lambda r: (rename_button.setEnabled(r >= 0), delete_button.setEnabled(r >= 0)))
    fill()
    dlg.exec()


def _place(popup, anchor, align=None, dy=0):
    """Move a shown popup under `anchor`: right edges lined up when the anchor is in the right half
    of its window (left edges otherwise), and always inside the window."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication
    win = anchor.window()
    w_left = win.mapToGlobal(QPoint(0, 0)).x()
    a = anchor.mapToGlobal(QPoint(0, 0))
    width, height = popup.width(), popup.height()
    if align is None:
        align = "right" if a.x() + anchor.width() / 2 > w_left + win.width() / 2 else "left"
    x = a.x() + anchor.width() - width if align == "right" else a.x()
    x = max(w_left + 4, min(x, w_left + win.width() - width - 4))
    y = a.y() + anchor.height() + dy
    screen = QGuiApplication.screenAt(a) or QGuiApplication.primaryScreen()
    if y + height > screen.availableGeometry().bottom():
        y = max(screen.availableGeometry().top(), a.y() - height - dy)
    popup.move(x, y)


class _MenuAnchor(QObject):
    def __init__(self, menu, anchor, align, dy):
        super().__init__(menu)
        self.anchor, self.align, self.dy = anchor, align, dy
        menu.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Show and self.anchor is not None:
            try:
                _place(obj, self.anchor, self.align, self.dy)
            except RuntimeError:  # anchor deleted
                self.anchor = None
        return False


def anchor_menu(menu, anchor, align=None, dy=0):
    """Make `menu` always open under `anchor` the app's way (see _place), however it's opened
    (QToolButton/QPushButton setMenu, or exec). Safe to call again (re-anchors)."""
    old = getattr(menu, "_pq35_anchor", None)
    if old is not None:
        old.anchor, old.align, old.dy = anchor, align, dy
    else:
        menu._pq35_anchor = _MenuAnchor(menu, anchor, align, dy)
    return menu


def popup_menu(menu, anchor, align=None, dy=0):
    """Open `menu` under `anchor`, placed the app's way, and wait for a choice."""
    anchor_menu(menu, anchor, align, dy)
    return menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
