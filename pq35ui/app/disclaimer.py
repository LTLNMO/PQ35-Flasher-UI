"""Notices over a dimmed main window: the disclaimer shown on every start (unless "Don't show
this again"), the patcher
warning, and "this tab needs a module"."""
from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

TEXT = """
<p><b>You are at your own risk using this project.</b> I am not responsible for thermonuclear
war, broken timing chains, divorce, etc.</p>

<p><b>A failed flash means whatever you flashed will not be functional until it is
recovered.</b> This means no EPS, no Haldex, no whatever.</p>

<p>Take faint care when flashing. This tool can usually recover from an interrupted flash, but
that doesn't mean you want to end up in that scenario. Flashing a steer dataset takes seconds,
but full module flashes can take several minutes: <b>a battery tender or otherwise is
recommended</b>, and turn off high-draw items (blower, headlights, heated seats).
<b>The engine cannot be running.</b></p>

<p>There is no warranty or anything provided, and I cannot help you recover your shit without
logs and useful information (exact component information).</p>

<p><b>Do not Dumb</b>, and have fun.</p>
"""

PATCH_TEXT = """
<p>The patches here really do what they say they do. <b>That does not make them safe. At all.</b></p>

<p><b>The EPS is faster than you. The EPS is stronger than you.</b> Usually it just cuts EPS
should you sufficiently piss it off, but <b>it is physically capable of much worse.</b></p>

<p><b>Do not fuck with it without knowing what you are doing.</b> HCA speed/timeout is the
only "safe" patch here.</p>
"""


class Dimmer(QWidget):
    """Darkens the window behind a notice, follows its size, and swallows clicks meant for
    the page underneath. Emits clicked when the dimmed area (not the notice) is clicked."""

    clicked = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.setGeometry(window.rect())
        window.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, event):
        # Painted, not a stylesheet: the app stylesheet would override or cascade into the card.
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        p.end()

    def mousePressEvent(self, event):
        event.accept()  # clicks on the dimmed page never reach the page
        self.clicked.emit()


class Disclaimer(QFrame):
    """A notice drawn inside the main window (not a window of its own, so macOS full screen
    is never disturbed): a coloured header band with the title between two Material Symbols
    icons, the text, and its buttons. Emits answered(True/False).

    icon: Material Symbols name. level: theme status colour ("warn", "err", "info", "ok").
    decline=None shows only the accept button; accept=None shows no buttons (the content brings
    its own). closable=False (the default, for cautions) ignores Escape and clicks outside, so it
    can only be answered with a button; closable=True lets Escape dismiss it (answered False),
    and a click on the dimmed area too unless click_away=False (questions and forms, where a
    stray click shouldn't answer or lose what was typed).
    content: an extra widget under the text (a text box, a list, a whole form).
    size: "large" for safety disclaimers, "compact" for routine notices (about half the weight),
    "form" for content that needs its own width."""

    answered = Signal(bool)

    def __init__(self, parent=None, title="Caution", text=TEXT, decline="Quit",
                 accept="I understand", icon="warning", level="warn", closable=False, size="large",
                 content=None, click_away=None):
        super().__init__(parent)
        extra = content
        compact = size in ("compact", "form")
        icon_px, title_px, spacing = (26, 16, 2) if compact else (44, 26, 3)
        self._closable = closable
        self.click_away = closable if click_away is None else click_away
        self.focus_widget = None  # what gets keyboard focus when shown (default: accept button)
        from . import theme
        c = theme.colors()
        warn, warn_bg = c[level], c[f"{level}_bg"]

        self.setObjectName("cautionCard")
        self.setStyleSheet(f"""
            #cautionCard {{ background: {c['surface']}; border: none; border-radius: 12px; }}
            #cautionHead {{ background: {warn_bg}; border-top-left-radius: 12px;
                            border-top-right-radius: 12px; }}
            #cautionIcon {{ color: {warn}; background: transparent;
                            font-family: "Material Symbols Rounded"; font-size: {icon_px}px; }}
            #cautionTitle {{ color: {warn}; background: transparent; font-size: {title_px}px; font-weight: 800;
                             letter-spacing: {spacing}px; }}
            #cautionBody {{ color: {c['text']}; background: transparent; line-height: 140%; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QFrame()
        head.setObjectName("cautionHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(*((16, 8, 16, 8) if compact else (22, 14, 22, 14)))
        hl.setSpacing(10 if compact else 16)
        hl.addStretch(1)
        for side in ("left", "right"):  # ⚠ CAUTION ⚠, centred
            if side == "right":
                heading = QLabel(title.upper())
                heading.setObjectName("cautionTitle")
                hl.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
            glyph = QLabel(icon)  # Material Symbols ligature
            glyph.setObjectName("cautionIcon")
            glyph.setFont(QFont("Material Symbols Rounded", icon_px))  # the stylesheet size wins; this is a fallback
            hl.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch(1)
        if compact:
            head.setFixedHeight(46)  # the icon font reserves tall line height; keep the band slim
        layout.addWidget(head)

        content = QVBoxLayout()
        content.setContentsMargins(*((20, 12, 20, 14) if compact else (24, 18, 24, 18)))
        content.setSpacing(10 if compact else 14)
        if text:
            body = QLabel(text)
            body.setObjectName("cautionBody")
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.RichText)
            # Selectable, so an error can be copied into a support request (drag, then Cmd/Ctrl+C,
            # or right-click > Copy). Mouse only: Tab still moves between the buttons.
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            content.addWidget(body)
        if extra is not None:
            content.addWidget(extra)
        row = QHBoxLayout()
        row.addStretch(1)
        self.buttons = []
        if decline:
            quit_button = QPushButton(decline)
            quit_button.clicked.connect(lambda: self.answered.emit(False))
            row.addWidget(quit_button)
            self.buttons.append(quit_button)
        self.agree = None
        if accept:
            self.agree = QPushButton(accept)
            self.agree.setObjectName("primary")
            self.agree.setDefault(True)
            self.agree.clicked.connect(lambda: self.answered.emit(True))
            row.addWidget(self.agree)
            self.buttons.append(self.agree)
        else:
            for b in self.buttons:  # no accept button: nothing to put a row of buttons in
                b.hide()
        if self.buttons and self.agree is not None:
            content.addLayout(row)
        layout.addLayout(content)
        if size == "form":  # as wide as the content needs, within reason
            self.setMinimumWidth(440)
            self.setMaximumWidth(960)
        elif compact:  # fixed width, height fitted to the wrapped text
            self.setFixedWidth(440)
        else:
            self.setMinimumWidth(600)
            self.setMaximumWidth(720)

    def mousePressEvent(self, event):
        event.accept()  # a click on the card itself mustn't reach the Dimmer and dismiss it

    def dismiss(self):
        """Escape: close (answered False) if this notice may be closed that way."""
        if self._closable:
            self.answered.emit(False)

    def clicked_away(self):
        """A click on the dimmed area around the card."""
        if self.click_away:
            self.answered.emit(False)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.dismiss()
            return  # otherwise answer with the buttons only
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.agree is not None:
            focused = next((b for b in self.buttons if b.hasFocus()), self.agree)
            focused.click()
            return
        super().keyPressEvent(event)


class _InputGuard(QObject):
    """While a notice is up, keyboard input and shortcuts only reach the notice: the page
    behind can't be typed into, and F11 etc. don't fire. Tab moves between the notice's own controls."""

    _KEYS = (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride,
             QEvent.Type.Shortcut)

    def __init__(self, overlay, card):
        super().__init__(overlay)
        self.overlay, self.card = overlay, card

    def eventFilter(self, obj, event):
        if event.type() not in self._KEYS or not _open or _open[-1] is not self.overlay:
            return False  # a notice opened on top of this one handles the keys
        inside = isinstance(obj, QWidget) and (obj is self.overlay or self.overlay.isAncestorOf(obj))
        if not inside:
            if event.type() == QEvent.Type.KeyPress:
                self.card.keyPressEvent(event)  # e.g. Return/Escape while focus is elsewhere
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._cycle(event.key() == Qt.Key.Key_Backtab)  # Tab never leaves the notice
            return True
        return False

    def _cycle(self, backward):
        start = QApplication.focusWidget() or self.card
        w = start
        for _ in range(1000):
            w = w.previousInFocusChain() if backward else w.nextInFocusChain()
            if w is None or w is start:
                return
            if self.overlay.isAncestorOf(w) and w.isVisible() and w.isEnabled() \
                    and w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value:
                w.setFocus(Qt.FocusReason.BacktabFocusReason if backward else Qt.FocusReason.TabFocusReason)
                return


_open = []  # overlays currently shown, innermost last (a warning over a form, say)


CAUTION_HIDE = "caution_hide"  # True: "Don't show this again" (Settings → General undoes it)


def should_show_caution(settings) -> bool:
    """The startup caution shows on every start unless "Don't show this again" was chosen."""
    return not settings.get(CAUTION_HIDE, False)


def startup_caution(window, settings) -> bool:
    """The caution with a "Don't show this again" box under the text. False: the user quit."""
    from PySide6.QtWidgets import QCheckBox
    dont = QCheckBox("Don't show this again")
    dont.setToolTip("You can turn it back on in Settings → General")
    if not confirm(window, content=dont):
        return False
    if dont.isChecked():
        settings.set(CAUTION_HIDE, True)
    return True


def confirm(window, **dialog) -> bool:
    """Show the disclaimer (or another one, via Disclaimer's arguments) over the dimmed
    window and wait for an answer. True if acknowledged."""
    return run_card(window, lambda overlay: Disclaimer(overlay, **dialog))


def run_card(window, make_card, finished=None) -> bool:
    """Dim window, show the card make_card(overlay) builds, and wait until it's answered (or
    until the finished signal fires, for cards whose content answers for itself). True if
    accepted."""
    overlay = Dimmer(window)
    card = make_card(overlay)
    grid = QGridLayout(overlay)
    grid.setContentsMargins(24, 24, 24, 24)
    grid.addWidget(card, 0, 0, Qt.AlignmentFlag.AlignCenter)
    loop = QEventLoop()
    result = []

    def answer(ok):
        if not result:  # first answer wins (Return can reach both a text box and the card)
            result.append(ok)
        loop.quit()

    card.answered.connect(answer)
    if finished is not None:
        finished.connect(lambda *_: loop.quit())
    overlay.clicked.connect(card.clicked_away)
    overlay.destroyed.connect(loop.quit)  # window torn down underneath us: don't hang
    guard = _InputGuard(overlay, card)
    app = QApplication.instance()
    app.installEventFilter(guard)
    focus_before = app.focusWidget()
    _open.append(overlay)
    overlay.show()
    overlay.raise_()
    target = card.focus_widget or card.agree
    if target is not None:
        target.setFocus()
    try:
        loop.exec()
    finally:
        if overlay in _open:
            _open.remove(overlay)
        app.removeEventFilter(guard)
        try:
            overlay.hide()
            overlay.deleteLater()
        except RuntimeError:
            pass  # already gone with the window
        if focus_before is not None:
            try:
                focus_before.setFocus()
            except RuntimeError:
                pass
    return bool(result and result[0])


_patch_warning_seen = False


def confirm_patching(widget) -> bool:
    """The patcher warning, once per session, before the patcher first opens."""
    global _patch_warning_seen
    if _patch_warning_seen:
        return True
    _patch_warning_seen = confirm(widget.window(), title="Before you patch", text=PATCH_TEXT,
                                  decline="Cancel")
    return _patch_warning_seen
