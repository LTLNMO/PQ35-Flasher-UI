"""Per-channel alarm limits for Live View (Haltech-style warnings).

A limit is {"min": float|None, "max": float|None, "beep": bool}, stored per
module family in settings under "alarms:<family>" keyed by channel key."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QLabel,
    QVBoxLayout, QWidget,
)

from .widgets import muted
from .popups import OverlayDialog


def tripped(limit, value):
    """True when value is outside the limit's min/max."""
    if not limit or value is None:
        return False
    low, high = limit.get("min"), limit.get("max")
    return (low is not None and value < low) or (high is not None and value > high)


def _num(value):
    return f"{value:g}"


def describe(limit):
    """Short text for the tree column, e.g. '> 110' or '< 0.7, > 1.1'."""
    if not limit:
        return ""
    parts = []
    if limit.get("min") is not None:
        parts.append(f"< {_num(limit['min'])}")
    if limit.get("max") is not None:
        parts.append(f"> {_num(limit['max'])}")
    return ", ".join(parts)


class AlarmDialog(OverlayDialog):
    """Edit the min/max limit for one channel."""

    CARD_ICON = "notifications"  # header icon when shown as a card

    def __init__(self, name, unit, limit=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alarm")
        self.setMinimumWidth(360)
        limit = limit or {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{name}</b>" + (f" ({unit})" if unit else "")))
        layout.addWidget(muted("Warn when the value goes outside these limits."))

        grid = QGridLayout()
        self.min_on = QCheckBox("Below")
        self.max_on = QCheckBox("Above")
        self.min_value = self._spin(limit.get("min"))
        self.max_value = self._spin(limit.get("max"))
        self.min_on.setChecked(limit.get("min") is not None)
        self.max_on.setChecked(limit.get("max") is not None)
        for row, (check, spin) in enumerate(((self.max_on, self.max_value),
                                             (self.min_on, self.min_value))):
            spin.setEnabled(check.isChecked())
            check.toggled.connect(spin.setEnabled)
            grid.addWidget(check, row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(QLabel(unit), row, 2)
        layout.addLayout(grid)

        self.beep = QCheckBox("Beep when it trips")
        self.beep.setChecked(limit.get("beep", True))
        layout.addWidget(self.beep)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.remove_button = buttons.addButton("Remove alarm", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.remove_button.setVisible(bool(limit))
        self.removed = False
        self.remove_button.clicked.connect(self._remove)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(value):
        spin = QDoubleSpinBox()
        spin.setRange(-1e6, 1e6)
        spin.setDecimals(2)
        spin.setValue(value if value is not None else 0.0)
        return spin

    def _remove(self):
        self.removed = True
        self.accept()

    def limit(self):
        """The edited limit, or None when removed / both sides unticked."""
        if self.removed or not (self.min_on.isChecked() or self.max_on.isChecked()):
            return None
        return {
            "min": self.min_value.value() if self.min_on.isChecked() else None,
            "max": self.max_value.value() if self.max_on.isChecked() else None,
            "beep": self.beep.isChecked(),
        }


class ScreenFlash(QWidget):
    """A see-through red layer over the whole window that pulses on and off while an alarm is
    tripped. Clicks go straight through it; popups opened after it sit above it."""

    PERIOD_MS = 400  # on for this long, then off for this long

    def __init__(self, window):
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lit = False
        self.timer = QTimer(self)
        self.timer.setInterval(self.PERIOD_MS)
        self.timer.timeout.connect(self._blink)
        window.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    def start(self):
        if self.timer.isActive():
            return
        self.setGeometry(self.parentWidget().rect())
        self.lit = True
        self.show()
        self.raise_()  # once: an alarm popup opened after this stays on top of it
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.lit = False
        self.hide()

    def _blink(self):
        self.lit = not self.lit
        self.update()

    def paintEvent(self, event):
        if not self.lit:
            return
        from . import theme
        red = QColor(theme.colors()["err"])
        p = QPainter(self)
        red.setAlpha(120)
        p.fillRect(self.rect(), red)
