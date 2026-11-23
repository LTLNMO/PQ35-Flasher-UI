"""Tooltips for every control, kept in one place.

apply(window) fills in any control that has no tooltip yet (it never replaces
one set in the page code). It runs at start-up and on every tab switch, so
widgets created later (module cards, flash range options) are covered too."""
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit

# By visible text, on any page.
BY_TEXT = {
    # shared
    "Save": "Save the current selection under a name so you can load it again.",
    "Delete": "Delete one of the saved items (you pick which).",
    "Remove": "Remove the selected item from the list.",
    "Open in Analyze": "Open this datalog on the Analyze tab.",
    "Open in map viewer": "Show this file (or change) as a table on the Maps tab.",
    # Dashboard
    "Read fault codes now": "Go to Diagnostics to read fault codes from every module.",
    "Open Projects": "Go to the Projects tab.",
    # Connect
    "Scan": "Ask every supported module address what's there (ignition on, engine off). Read-only.",
    "Select": "Work on this module in the other tabs.",
    "Read info": "Read this module's part number and software version (checks it's there).",
    "Re-read info": "Read this module's part number and software version again.",
    "Recover…": "For a module stuck in its bootloader: open Flash in recovery mode. "
                "Only with the exact image made for it.",
    # Back up
    "Read module": "Copy the module's firmware into your library. Takes about 7 minutes; nothing is written.",
    "Open module folder": "Show this module's backups and history in Finder / Explorer.",
    # Flash
    "Hold to flash": "Press and hold for 3 seconds to start writing. Don't touch the car, cable or "
                     "computer until it finishes.",
    "Show advanced options": "Show options for special cases (checksums, recovery, crossflash…).",
    "Check programming conditions": "BCM: read the supply voltage, attempt counter and flags a flash needs. "
                                    "Nothing is written.",
    "Choose stock reference…": "BCM: the stock full image the module runs now, so only changed sectors are written.",
    "Choose driver .bin…": "BCM: the 918-byte RAM driver the loader needs before writing.",
    "Patch…": "EPS: apply patches to a copy of this image before flashing it. The original never changes.",
    "I've cycled the ignition: check again": "Re-read the module after switching the ignition off and on.",
    "Fix wrong internal checksums (only for images you edited yourself)":
        "Recalculate the image's internal checksums before writing. Don't use on images from a tuner.",
    "Bench simulator patches. BENCH ONLY: never put this in a car":
        "Haldex: patches for running the module on a bench without the car.",
    "Recovery: the module is stuck in its bootloader and won't identify":
        "Skips the checks that this is the module you think it is. The wrong image bricks it.",
    "Flash without a backup (not recommended)": "Allow flashing before a backup of this module exists.",
    "Crossflash: allow an image for a different part number":
        "Write firmware for another part number. Only if you know exactly what your car needs.",
    "Battery charger or a stable 13-14 V supply is connected":
        "A voltage drop mid-flash can leave the module stuck in its bootloader.",
    "Ignition on, engine off": "The module must be powered and the engine not running.",
    "This computer is plugged in and won't be closed or unplugged":
        "If the computer sleeps or loses power mid-flash, the module can be left unusable.",
    "Undo": "Undo the last change to the image, patches or options (Ctrl+Z).",
    "Redo": "Redo (Ctrl+Shift+Z).",
    # Diagnostics
    "Dump BCM page FF (read-only, safe)": "Save the BCM's 16 KiB boot page to a file. Nothing is written.",
    "Open dump": "Open the saved page FF dump.",
    "Open sidecar": "Open the JSON file saved next to the dump.",
    "Open folder": "Show the folder the dump was saved in.",
    # Logger / Live View
    "Open datalogs": "Show the folder your datalogs are saved in.",
    "Start": "Start showing live values. Nothing is saved.",
    "Stop": "Stop reading live values.",
    "Add": "Log every field of this measuring group, even ones without names.",
    # Analyze
    "Open CSV…": "Open a datalog from this app or another tool (the CSV logs Datazap takes).",
    "Open datalogs folder": "Show the folder your datalogs are saved in.",
    "Export…": "Save this log to Datalogs or anywhere: Standard CSV (Datazap), Integrated Engineering, MegaLogViewer or generic CSV.",
    # Projects
    "New project…": "Start a project. The module you're connected to is added to it.",
    "Rename…": "Rename the selected project.",
    "Open projects folder": "Show the Projects folder in Finder / Explorer.",
    "Add version…": "Copy a tune file into this project with a label and notes.",
    "Open in Flash": "Load this version on the Flash tab.",
    "Compare with previous": "Compare this version with the one above it on Maps → Compare files.",
    "Add the current module": "Add the module you're connected to.",
    "Add a module from the library…": "Add any module you've connected to before.",
    "Add logs…": "Link datalogs to this project (they stay where they are).",
    # Maps
    "Open file…": "Open an ECU file to look at its maps. Read-only.",
    "Compare with…": "Load a second file to see this map side by side and as a difference.",
    "Show map": "Read and show the map with these settings.",
    "Choose File A (e.g. stock)…": "The first file to compare, usually the stock one.",
    "Choose File B (e.g. tuned)…": "The second file to compare, usually the tuned one.",
    # Log
    "Demo mode": "Use pretend modules: nothing talks to real hardware. The app restarts.",
    "Show protocol detail": "Include low-level protocol messages in the log.",
    "Save support bundle…": "Zip up the logs to send when asking for help.",
    "Open logs folder": "Show the log files in Finder / Explorer.",
    # Settings
    "Dark": "Dark theme.",
    "Light": "Light theme.",
    "System": "Follow the computer's light/dark setting.",
    "Icons only": "Show only icons in the tab bar (labels appear when you hover).",
    "Icons + Text": "Show icons and labels in the tab bar.",
    "Windowed": "Open PQ35 Flasher in a normal window.",
    "Full screen": "Open PQ35 Flasher full screen. F11 (⌃⌘F on a Mac) switches back.",
    "Metric (°C, mbar, km/h, Nm)": "Show values in metric units.",
    "Imperial (°F, psi, mph, lb-ft)": "Show values in imperial units. Logs are still saved in metric.",
    "Save key": "Save this API key on this computer (in its own file, not in settings).",
    "Fetch models": "Ask the provider which models your key can use.",
    "Test": "Send a short test message to check the AI provider works.",
    "Choose…": "Point to the tool's program file yourself if it wasn't found.",
    "Sign in…": "Open a terminal window with the tool's sign-in. Do it once.",
    "Set password…": "Ask for a password before flashing, clearing codes, patching or recovery.",
    "Change password…": "Change the lock password or what it protects.",
    "Remove lock…": "Turn the password lock off (needs the current password).",
    "Lock now": "Lock again straight away instead of waiting 10 minutes.",
}

# Same text means different things on some pages.
BY_PAGE_TEXT = {
    ("liveview", "Clear"): "Clear the graphs and readings (keeps the channels).",
    ("playback", "Clear"): "Remove the compared log.",
    ("maps", "Clear"): "Stop comparing with the second file.",
}

# Controls without text, by attribute path on the page.
BY_ATTR = {
    "logger": {
        "rate": "How often each measuring group is read. Fewer groups log faster.",
        "mode": "Continuous saves everything; Triggered saves only while a condition holds; "
                "Rolling keeps just the last few minutes.",
        "trigger_channel": "The value that starts a triggered log.",
        "trigger_op": "Start when the value is above (>) or below (<) the number.",
        "trigger_value": "The value that starts a triggered log.",
        "pre_s": "Seconds kept from before the trigger.",
        "post_s": "Seconds kept after the condition ends.",
        "keep_min": "How many minutes the loop keeps.",
        "raw_spin": "Measuring group number to log in full.",
        "search": "Filter the channel list.",
        "presets.combo": "Load a saved set of channels.",
    },
    "liveview": {
        "window_combo": "How many seconds the graphs show.",
        "search": "Filter the channel list.",
        "presets.combo": "Load a saved set of channels.",
    },
    "playback": {
        "scatter_x": "Channel along the bottom of the scatter plot.",
        "scatter_y": "Channel up the side of the scatter plot.",
        "scatter_color": "Colour the points by a third channel (or none).",
        "hist_channel": "Channel to show the histogram for.",
        "hist_bins": "Number of bars in the histogram.",
        "offset": "Move the compared log in time to line the runs up.",
    },
    "maps": {
        "viewer.name": "A name for this map (used when you save it).",
        "viewer.address": "Where the map starts in the file, in hex (e.g. 0x1A040).",
        "viewer.rows": "Number of rows (Y axis).",
        "viewer.cols": "Number of columns (X axis).",
        "viewer.dtype": "How each value is stored: 8/16/32-bit, signed or not, byte order, or float.",
        "viewer.factor": "Multiply each raw value by this.",
        "viewer.offset": "Then add this.",
        "viewer.decimals": "Decimal places shown in the table.",
        "viewer.saved": "Load a map definition you saved.",
        "viewer.view_mode": "Show this file, the other file, or the difference between them.",
    },
    "diagnostics": {"samples": "How many readings to take."},
    "help": {"search": "Find topics that mention a word."},
    "projects": {"car": "The car this project is for."},
}
AXIS_TIPS = {"address": "Where the axis values start in the file (hex). Leave empty to number 0, 1, 2…",
             "dtype": "How each axis value is stored.",
             "factor": "Multiply each raw axis value by this.",
             "offset": "Then add this."}


def _resolve(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def apply(window):
    for key, page in window.pages.items():
        for path, tip in BY_ATTR.get(key, {}).items():
            widget = _resolve(page, path)
            if widget is not None and not widget.toolTip():
                widget.setToolTip(tip)
        if key == "maps":
            for axis in (page.viewer.x_axis, page.viewer.y_axis):
                for name, tip in AXIS_TIPS.items():
                    if not axis[name].toolTip():
                        axis[name].setToolTip(tip)
        for cls in (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit):
            for widget in page.findChildren(cls):
                if widget.toolTip() or not hasattr(widget, "text") or not callable(widget.text):
                    continue
                text = widget.text()
                tip = BY_PAGE_TEXT.get((key, text)) or BY_TEXT.get(text)
                if tip:
                    widget.setToolTip(tip)
    for name in ("interface_button", "status"):
        widget = getattr(window, name, None)
        if widget is not None and not widget.toolTip():
            widget.setToolTip({"interface_button": "The CAN adapter to use.",
                               "status": "The module you're working on. Click to switch module."}.get(name, ""))


class TooltipGate:
    """App-wide switch for hover tooltips (Settings → General → Tooltips). While tooltips are off
    a filter on the QApplication swallows every tooltip event; while they're on (the usual case)
    nothing is added (it rides on widgets.AppEventFilter, the app's one filter, only for ToolTip events)."""

    _instance = None

    def __init__(self, settings):
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QApplication

        tooltip = QEvent.Type.ToolTip

        class _Filter(QObject):
            def eventFilter(self, obj, event):
                return event.type() == tooltip

        self.settings = settings
        self.filter = _Filter(QApplication.instance())
        self.installed = False
        self.sync()

    def enabled(self):
        return bool(self.settings.get("tooltips", True))

    def sync(self):
        """Add or remove the handler on the app's one event filter to match the setting."""
        from PySide6.QtCore import QEvent
        from .widgets import app_filter
        want = not self.enabled()
        if want != self.installed:
            f = app_filter()
            f.add(self.filter, (QEvent.Type.ToolTip,)) if want else f.remove(self.filter)
            self.installed = want

    @classmethod
    def install(cls, settings):
        if cls._instance is None:
            cls._instance = cls(settings)
        else:
            cls._instance.settings = settings
            cls._instance.sync()
        return cls._instance
