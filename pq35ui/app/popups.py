"""Every popup in the app as an in-window card, styled like the startup disclaimer and the
"Module needed" notice: a coloured header band with an icon either side of the title, over the
dimmed window. Drawn inside the main window, so macOS full screen is never disturbed.

Drop-in stand-ins with the same calls and answers as Qt's:
  MessageBox.information / warning / critical / question   (like QMessageBox)
  InputDialog.getText / getItem                             (like QInputDialog)
  OverlayDialog                                             (a QDialog whose exec() shows it in a card)

When there's no visible window to draw on (very early at startup), they fall back to Qt's
own popups, so a message is never lost."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import Qt as QtGuiNamespace  # mightBeRichText lives in QtGui's Qt namespace
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QVBoxLayout, QWidget)

from .disclaimer import Disclaimer, run_card


def _host(parent):
    """The visible window a card should dim, or None."""
    window = parent.window() if isinstance(parent, QWidget) else QApplication.activeWindow()
    if window is None or not window.isVisible():
        return None
    return window


def _html(text):
    """QMessageBox-style text (plain with newlines, or rich) as rich text for the card."""
    text = str(text or "")
    if QtGuiNamespace.mightBeRichText(text):
        return text
    return "<p>" + escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"


# (icon, theme level) per kind of message
_KINDS = {
    "information": ("info", "info"),
    "warning": ("warning", "warn"),
    "critical": ("error", "err"),
    "question": ("help", "info"),
}


class MessageBox:
    """QMessageBox's static calls, as cards. Answers are QMessageBox.StandardButton values."""

    StandardButton = QMessageBox.StandardButton

    @staticmethod
    def _show(kind, parent, title, text, *args, **kwargs):
        window = _host(parent)
        if window is None:
            return getattr(QMessageBox, kind)(parent, title, text, *args, **kwargs)
        icon, level = _KINDS[kind]
        if kind == "question":
            ok = run_card(window, lambda overlay: Disclaimer(
                overlay, title=title, text=_html(text), decline="No", accept="Yes", icon=icon,
                level=level, closable=True, click_away=False, size="compact"))
            return QMessageBox.StandardButton.Yes if ok else QMessageBox.StandardButton.No
        run_card(window, lambda overlay: Disclaimer(
            overlay, title=title, text=_html(text), decline=None, accept="OK", icon=icon,
            level=level, closable=True, size="compact"))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        return MessageBox._show("information", parent, title, text, *args, **kwargs)

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        return MessageBox._show("warning", parent, title, text, *args, **kwargs)

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        return MessageBox._show("critical", parent, title, text, *args, **kwargs)

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        return MessageBox._show("question", parent, title, text, *args, **kwargs)


def _field(label, widget):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    if label:
        text = QLabel(_html(label))
        text.setObjectName("cautionBody")
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # copyable
        layout.addWidget(text)
    layout.addWidget(widget)
    return box


class InputDialog:
    """QInputDialog's getText / getItem, as cards. Answers are (value, ok) like Qt's."""

    @staticmethod
    def getText(parent, title, label, echo=QLineEdit.EchoMode.Normal, text="", *args, **kwargs):
        window = _host(parent)
        if window is None:
            return QInputDialog.getText(parent, title, label, echo, text, *args, **kwargs)
        edit = QLineEdit(text or "")
        edit.setEchoMode(echo)
        edit.selectAll()

        def make(overlay):
            card = Disclaimer(overlay, title=title, text="", decline="Cancel", accept="OK",
                              icon="password" if echo == QLineEdit.EchoMode.Password else "edit",
                              level="info", closable=True, click_away=False, size="compact",
                              content=_field(label, edit))
            card.focus_widget = edit
            edit.returnPressed.connect(card.agree.click)
            return card

        ok = run_card(window, make)
        return edit.text(), ok

    @staticmethod
    def getItem(parent, title, label, items, current=0, editable=True, *args, **kwargs):
        window = _host(parent)
        if window is None:
            return QInputDialog.getItem(parent, title, label, items, current, editable, *args, **kwargs)
        combo = QComboBox()
        combo.addItems([str(i) for i in items])
        combo.setEditable(editable)
        if 0 <= current < combo.count():
            combo.setCurrentIndex(current)

        def make(overlay):
            card = Disclaimer(overlay, title=title, text="", decline="Cancel", accept="OK",
                              icon="list", level="info", closable=True, click_away=False,
                              size="compact", content=_field(label, combo))
            card.focus_widget = combo
            return card

        ok = run_card(window, make)
        return combo.currentText(), ok


def _first_focusable(widget):
    """The first child that takes keyboard focus with Tab, or None."""
    w = widget
    for _ in range(500):
        w = w.nextInFocusChain()
        if w is widget or w is None:
            return None
        if widget.isAncestorOf(w) and w.isEnabled() and w.isVisibleTo(widget) \
                and w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value:
            return w
    return None


class OverlayDialog(QDialog):
    """A QDialog that, when exec()'d, shows inside a card over the dimmed window instead of as a
    window of its own. The dialog keeps its own widgets and OK/Cancel buttons; the card adds
    the header band (its window title, icon either side). exec() answers like QDialog's."""

    CARD_ICON = "tune"
    CARD_LEVEL = "info"

    def exec(self):
        window = _host(self.parentWidget())
        if window is None:
            return super().exec()
        parent, flags = self.parentWidget(), self.windowFlags()
        self.setResult(0)
        # Drawn on the card's surface, not the theme's page background for dialogs.
        self.setStyleSheet(self.styleSheet() + "\nQDialog { background: transparent; }")

        def make(overlay):
            card = Disclaimer(overlay, title=self.windowTitle(), text="", decline=None, accept=None,
                              icon=self.CARD_ICON, level=self.CARD_LEVEL, closable=False,
                              size="form", content=self)
            self.show()  # now a child of the card, not a window
            card.focus_widget = _first_focusable(self)
            return card

        try:
            run_card(window, make, finished=self.finished)
        finally:
            # Back out of the card before it's deleted, so the caller can still read the fields.
            self.hide()
            self.setParent(parent, flags)
        return self.result()

    def open(self):  # pragma: no cover - same as exec here; nothing in the app uses open()
        return self.exec()
