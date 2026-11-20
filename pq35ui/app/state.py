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
    request_scan = Signal()               # "Scan" from anywhere (Dashboard): Connect runs the quick scan
    request_full_scan = Signal()          # "Full Scan" from anywhere (Diagnostics): Connect runs it
    car_changed = Signal(str)             # the car connected now: its VIN once read, "" when it's gone
    connect_needed = Signal(str)          # a job was asked for with no interface: the window prompts

    def __init__(self, pool, settings, parent=None):
        super().__init__(parent)
        self.pool = pool
        self.settings = settings
        self.adapter = None
        self.family = None
        self.last_scan = None  # Connect's latest scan result, quick or full
        self.last_full_scan = None  # the latest Full Scan (Diagnostics' scan log and compare read it)
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
        # Whether that VIN was read from the car connected NOW (this run, this interface). Only then
        # is it the car: recordings are tagged with it, its profile and project load (car_watch).
        self.vin_live = False
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
            # As in Drew's version: unplugging (or another adapter) keeps the picked module and
            # the last scan on show; only the module's identification is dropped. Scan again
            # to refresh them.
            self.adapter = adapter
            self.identity = None
            self.record = None
            self.car_gone()  # recordings carry no car until one answers again
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

    @property
    def setup_family(self):
        """The module whose channel lists Logger and Live View show (and whose presets, alarms
        and layouts they edit): the picked one, else the last one used, else the engine. Lets
        you set things up unplugged without anything showing as connected on Connect."""
        if self.family is not None:
            return self.family
        return self.family_by_name(self.settings.get("module") or "engine") or self.family_by_name("engine")

    def family_by_name(self, name):
        return next((f for f in self.families if f["name"] == name), None) or self.extra_families.get(name)

    def set_identity(self, identity):
        self.identity = identity
        # No backup/flash history folder for fault-code-only modules (keeps Modules/ to real ECUs).
        self.record = (EcuRecord(ecu_key(self.family["name"], identity))
                       if identity and not self.family.get("generic") else None)
        self.changed.emit()

    def set_vehicle(self, vin, source=""):
        """The car connected now, read by its VIN (a scan, or car_watch's quick read)."""
        if not vin:
            return
        from .vehicle import decode
        vehicle = dict(decode(vin), source=source)
        was = self.vin()
        if vehicle.get("vin") != (self.vehicle or {}).get("vin"):
            self.vehicle = vehicle
            self.settings.set("vehicle", vehicle)
        self.vin_live = True
        if vin != was:
            self.car_changed.emit(vin)
            self.changed.emit()

    def car_gone(self):
        """No car answers any more (unplugged, ignition off, another interface)."""
        if self.vin_live:
            self.vin_live = False
            self.car_changed.emit("")
            self.changed.emit()

    def vin(self) -> str:
        """The VIN of the car connected now, or "" (not read yet this run, or no car answering).
        Recordings are tagged with this, never with a car from an earlier run."""
        return (self.vehicle or {}).get("vin", "") if self.vin_live else ""

    def known_vin(self) -> str:
        """The last car read, even if it isn't connected now (e.g. which scans to list offline)."""
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

    def need_adapter(self, operation=""):
        """True (and the "Connect first" card shows) when no interface is plugged in. Pages call
        it before setting anything up for a job (a log file, cleared graphs)."""
        if self.adapter:
            return False
        self.connect_needed.emit(operation)
        return True

    def run(self, operation, params=None, on_done=None, on_error=None, module=None):
        """Start a hardware job. Returns False (and reports) if one can't start.
        module: a family dict to target instead of the selected one (all-module scans)."""
        if self.busy:
            if on_error:
                on_error({"message": "Another operation is still running."})
            return False
        if not self.adapter:
            self.connect_needed.emit(operation)  # "Connect first" card, not a dead button
            if on_error:
                on_error({"message": "No interface plugged in.", "type": "NoAdapter"})
            return False
        if not (module or self.family) and operation != "scan":
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
