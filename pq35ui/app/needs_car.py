"""Action buttons that need the car (Record, Start, Scan, Flash, Read…).

Plugged in, a button works as it always did (enabled when its page says it's ready). Unplugged,
it looks greyed out, but a click shows the "Connect first" card (AppState.connect_needed) instead
of doing nothing, and the action itself never runs. Everything else on the page stays usable."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect

DIM = 0.38  # about how the theme greys out a disabled button


class _Gate(QObject):
    """Eats clicks and key presses on a button while unplugged, and asks for the car instead."""

    _CLICKS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
               QEvent.Type.MouseButtonDblClick)

    def __init__(self, button, state):
        super().__init__(button)
        self.button, self.state = button, state
        self.unplugged = False
        self.effect = None
        self.tip = None  # the button's own tooltip, kept while ours shows
        button.installEventFilter(self)

    def eventFilter(self, obj, event):
        if not self.unplugged:
            return False
        kind = event.type()
        if kind in self._CLICKS:
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton \
                    and self.button.rect().contains(event.position().toPoint()):
                self.state.connect_needed.emit("")
            return True  # never reaches the button: the action doesn't run
        if kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                                            Qt.Key.Key_Enter):
            self.state.connect_needed.emit("")
            return True
        return False

    def apply(self, unplugged, ready):
        b = self.button
        if unplugged != self.unplugged:
            self.unplugged = unplugged
            if unplugged:
                self.tip = b.toolTip()
                b.setToolTip("Plug in your adapter first")
            else:
                b.setToolTip(self.tip or "")
        if unplugged:
            b.setEnabled(True)  # clickable, so the click can explain
            if b.graphicsEffect() is None:
                self.effect = QGraphicsOpacityEffect(b)
                self.effect.setOpacity(DIM)
                b.setGraphicsEffect(self.effect)
        else:
            if self.effect is not None and b.graphicsEffect() is self.effect:
                b.setGraphicsEffect(None)  # deletes the effect
            self.effect = None
            b.setEnabled(bool(ready))


def gate(button, state, ready=True):
    """Set an action button that needs the car: enabled when `ready` (plugged in), or greyed out
    with a "Connect first" click when no interface is plugged in. Call it wherever the page used
    to call button.setEnabled(...)."""
    g = getattr(button, "_needs_car", None)
    if g is None:
        g = button._needs_car = _Gate(button, state)
    g.apply(not state.adapter, ready)


def unplugged(button):
    """True while gate() shows this button greyed out for want of the car."""
    g = getattr(button, "_needs_car", None)
    return bool(g and g.unplugged)
