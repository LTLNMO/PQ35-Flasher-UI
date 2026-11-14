"""Shared app state: the selected interface and module, the identified ECU,
and the one hardware job allowed at a time."""
from PySide6.QtCore import QObject, Signal

from .storage import EcuRecord, ecu_key


class AppState(QObject):
    changed = Signal()
    progress = Signal(dict)
    busy_changed = Signal(bool)
    open_in_flash = Signal(str)
    open_patched_in_flash = Signal(str, str)  # patched image, the image it was patched from
    refresh_interfaces = Signal()
    start_recovery = Signal()
    restart_app = Signal(bool)  # True: restart in demo mode, False: normal mode
    sample = Signal(dict)  # one live measuring-block read from a "log" job
    open_in_playback = Signal(str)        # a datalog to open on Analyze
    follow_log = Signal(str)              # a recording in progress for Analyze to follow live ("" = it ended)
    stop_logging = Signal()               # Analyze's live banner → Stop recording: the Logger stops
    logger_recording = Signal(bool)       # the Logger started / stopped recording (Live View mirrors it)
    open_in_maps = Signal(str, str)       # file A, file B ("" = none) for Maps → Compare
    open_tab = Signal(str)                # switch to a tab by NAV key
    modules_scanned = Signal(dict)        # Connect's scan finished (scan result)
    faults_changed = Signal(int)          # fault codes found by the last check (all modules)
    request_scan = Signal()               # "Scan" from anywhere (Diagnostics): Connect runs it

    def __init__(self, pool, settings, parent=None):
        super().__init__(parent)
        self.pool = pool
        self.settings = settings
        self.adapter = None
        self.family = None
        self.last_scan = None  # Connect's latest scan result (Diagnostics reads from it)
        self.fault_summary = {}  # family name -> (codes, still active), read this session
        self.families = []
        # Other gateway modules from the last full scan, as family-like dicts (generic=True):
        # selectable for their fault codes only. Keyed "addr:XX".
        self.extra_families = {}
        self.identity = None
        self.record = None
        self.busy = False
        self.operation = None  # the hardware job running ("log", "flash", …) while busy
        self.hello = {}
        # The car: {"vin", "label", ...} from the last scan that read a VIN (kept between runs).
        self.vehicle = settings.get("vehicle") or None
        pool.event.connect(self._event)

    def _event(self, message):
        if message.get("event") == "progress":
            self.progress.emit(message)
        elif message.get("event") == "sample":
            self.sample.emit(message)

    @staticmethod
    def same_adapter(a, b) -> bool:
        keys = ("adapter", "dll", "channel", "serial")
        return bool(a and b) and all(a.get(k) == b.get(k) for k in keys)

    def set_adapter(self, adapter):
        if self.same_adapter(adapter, self.adapter):
            # Same device, fresher details (e.g. a SocketCAN link came up).
            self.adapter = adapter
            self.changed.emit()
            return
        if adapter != self.adapter:
            self.adapter = adapter
            self.identity = None
            self.record = None
            self.last_scan = None  # a different (or no) adapter: what was found no longer applies
            self.fault_summary = {}
            self.extra_families = {}
            self.changed.emit()

    def select_module(self, family, identity=None):
        """Pick the module to work on (from the Connect page), optionally with
        the identification a scan already read from it."""
        self.settings.set("module", family["name"] if family else None)
        self.family = family
        self.identity = None
        self.record = None
        if identity:
            self.set_identity(identity)  # emits changed
        else:
            self.changed.emit()

    def family_by_name(self, name):
        return next((f for f in self.families if f["name"] == name), None) or self.extra_families.get(name)

    def set_identity(self, identity):
        self.identity = identity
        # No backup/flash history folder for fault-code-only modules (keeps Modules/ to real ECUs).
        self.record = (EcuRecord(ecu_key(self.family["name"], identity))
                       if identity and not self.family.get("generic") else None)
        self.changed.emit()

    def set_vehicle(self, vin, source=""):
        """Remember the car a scan identified by its VIN."""
        if not vin:
            return
        from .vehicle import decode
        vehicle = dict(decode(vin), source=source)
        if vehicle != self.vehicle:
            self.vehicle = vehicle
            self.settings.set("vehicle", vehicle)
            self.changed.emit()

    def vin(self) -> str:
        return (self.vehicle or {}).get("vin", "")

    def part(self) -> str:
        if not self.identity:
            return ""
        return (self.identity.get("software_part_number") or self.identity.get("part_number") or "").strip()

    def stop(self):
        """Ask the running job to stop (a live log ends at its next read)."""
        if self.busy and self.adapter:
            try:
                self.pool.for_adapter(self.adapter).call("stop")
            except RuntimeError:
                pass

    def run(self, operation, params=None, on_done=None, on_error=None, module=None):
        """Start a hardware job. Returns False (and reports) if one can't start.
        module: a family dict to target instead of the selected one (all-module scans)."""
        if self.busy:
            if on_error:
                on_error({"message": "Another operation is still running."})
            return False
        if not self.adapter or (not (module or self.family) and operation != "scan"):
            if on_error:
                on_error({"message": "Choose an interface and a module first."})
            return False
        try:
            client = self.pool.for_adapter(self.adapter)
        except RuntimeError as exc:
            if on_error:
                on_error({"message": str(exc)})
            return False
        adapter = {k: v for k, v in self.adapter.items() if k in ("adapter", "dll", "channel", "serial", "bus")}
        request = dict(params or {}, operation=operation, adapter=adapter)
        if module or self.family:
            request["module"] = (module or self.family)["alias"]
        self.operation = operation
        self._set_busy(True)

        def done(result):
            self.operation = None
            self._set_busy(False)
            if on_done:
                on_done(result)

        retried = []

        def failed(error):
            kind = (error or {}).get("type")
            if operation == "scan" and kind in ("AdapterLost", "WorkerDied") and not retried:
                # Unplugged / wedged adapter, or the worker crashed in libusb: start a fresh
                # worker (fresh USB state, like restarting the app) and scan once more.
                retried.append(True)
                self.pool.event.emit({"event": "log", "level": "warning", "logger": "app",
                                      "message": "Adapter problem during the scan "
                                                 f"({error.get('message')}): restarting the "
                                                 "worker and trying again."})
                client.restart()
                client.call("run", request, done, failed)
                return
            self.operation = None
            self._set_busy(False)
            if kind == "AdapterLost":
                error = dict(error, message="The CAN adapter isn't responding. Check its USB cable "
                                            "(a different port or cable can help), then scan again.")
            if on_error:
                on_error(error)

        client.call("run", request, done, failed)
        return True

    def offline(self, method, params, on_done=None, on_error=None):
        self.pool.native().call(method, params, on_done, on_error)

    def _set_busy(self, busy):
        self.busy = busy
        self.busy_changed.emit(busy)
