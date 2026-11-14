"""Optional password lock on the actions that change a module.

Only a salted PBKDF2-SHA256 hash is stored (settings "lock"). Unlocking lasts
for UNLOCK_MINUTES in this session so a flash doesn't prompt at every step.
This keeps a borrowed or shared laptop from being used to flash by accident;
it is not protection against someone who can edit the settings file."""
import hashlib
import hmac
import os
import time

from PySide6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from .widgets import Card, muted
from .popups import InputDialog, MessageBox, OverlayDialog

KEY = "lock"
ITERATIONS = 200_000
UNLOCK_MINUTES = 10
ACTIONS = {
    "flash": "Flashing a module",
    "dtc_clear": "Clearing fault codes",
    "patch": "Patching an image",
    "recovery": "Recovery mode",
}
_unlocked_until = 0.0


def _hash(password: str, salt: bytes, iterations=ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def config(settings):
    return dict(settings.get(KEY) or {})


def enabled(settings) -> bool:
    c = config(settings)
    return bool(c.get("hash") and c.get("salt"))


def protects(settings, action) -> bool:
    c = config(settings)
    return enabled(settings) and action in c.get("actions", list(ACTIONS))


def check(settings, password) -> bool:
    c = config(settings)
    if not enabled(settings):
        return True
    digest = _hash(password, bytes.fromhex(c["salt"]), c.get("iterations", ITERATIONS))
    return hmac.compare_digest(digest, c["hash"])


def set_password(settings, password, actions):
    salt = os.urandom(16)
    settings.set(KEY, {"salt": salt.hex(), "hash": _hash(password, salt), "iterations": ITERATIONS,
                       "actions": list(actions)})


def lock_now():
    global _unlocked_until
    _unlocked_until = 0.0


def require(parent, settings, action) -> bool:
    """True if action may go ahead (not protected, recently unlocked, or the
    right password was entered now)."""
    global _unlocked_until
    if not protects(settings, action) or time.monotonic() < _unlocked_until:
        return True
    for attempt in range(3):
        password, ok = InputDialog.getText(
            parent, "Locked", f"{ACTIONS.get(action, action)} is password protected.\n\nPassword:",
            QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if check(settings, password):
            _unlocked_until = time.monotonic() + UNLOCK_MINUTES * 60
            return True
        time.sleep(0.5 * (attempt + 1))
    MessageBox.warning(parent, "Locked", "Wrong password.")
    return False


class _PasswordDialog(OverlayDialog):
    CARD_ICON = "lock"  # header icon when shown as a card

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set password")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.current = None
        if enabled(settings):
            self.current = QLineEdit()
            self.current.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Current password", self.current)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.again = QLineEdit()
        self.again.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", self.new)
        form.addRow("New password again", self.again)
        layout.addLayout(form)
        layout.addWidget(muted("Protect:"))
        current_actions = config(settings).get("actions", list(ACTIONS))
        self.boxes = {}
        for key, label in ACTIONS.items():
            box = QCheckBox(label)
            box.setChecked(key in current_actions)
            layout.addWidget(box)
            self.boxes[key] = box
        self.error = QLabel("")
        from . import theme
        self.error.setStyleSheet(f"color:{theme.colors()['err']}")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.settings = settings

    def _ok(self):
        if self.current is not None and not check(self.settings, self.current.text()):
            self.error.setText("The current password is wrong.")
            return
        if len(self.new.text()) < 4:
            self.error.setText("Use at least 4 characters.")
            return
        if self.new.text() != self.again.text():
            self.error.setText("The new passwords don't match.")
            return
        actions = [k for k, b in self.boxes.items() if b.isChecked()]
        if not actions:
            self.error.setText("Tick at least one action to protect.")
            return
        set_password(self.settings, self.new.text(), actions)
        self.accept()


class SecurityCard(Card):
    def __init__(self, settings):
        super().__init__("Lock")
        self.settings = settings
        self.hint("Ask for a password before actions that change a module. Stops a shared "
                  f"laptop being used to flash by accident. Unlocks for {UNLOCK_MINUTES} minutes.")
        self.status = muted("")
        self.status.setWordWrap(False)
        row = QHBoxLayout()
        name = muted("Password")
        name.setFixedWidth(110)  # same label column as the other Settings cards
        row.addWidget(name)
        row.addSpacing(16)
        row.addWidget(self.status)
        row.addSpacing(8)
        self.set_button = QPushButton("Set password…")
        self.set_button.clicked.connect(self._set)
        row.addWidget(self.set_button)
        self.remove_button = QPushButton("Remove lock…")
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.remove_button)
        self.lock_button = QPushButton("Lock now")
        self.lock_button.clicked.connect(lambda: (lock_now(), self.refresh()))
        row.addWidget(self.lock_button)
        row.addStretch(1)
        self.add(row)
        self.refresh()

    def refresh(self):
        on = enabled(self.settings)
        if on:
            actions = config(self.settings).get("actions", list(ACTIONS))
            self.status.setText("On for: " + ", ".join(ACTIONS[a].lower() for a in actions if a in ACTIONS) + ".")
        else:
            self.status.setText("Off")
        self.set_button.setText("Change password…" if on else "Set password…")
        self.remove_button.setEnabled(on)
        self.lock_button.setEnabled(on)

    def _set(self):
        if _PasswordDialog(self.settings, self).exec():
            self.refresh()

    def _remove(self):
        password, ok = InputDialog.getText(self, "Remove lock", "Current password:", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if not check(self.settings, password):
            MessageBox.warning(self, "Remove lock", "Wrong password.")
            return
        self.settings.set(KEY, None)
        lock_now()
        self.refresh()
