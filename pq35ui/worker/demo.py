"""Demo adapter: a pretend module for trying the UI with no hardware.

Every operation is simulated. Images still go through the real flasher
checks (prepare_image), so a file the demo accepts is one the real flash
would accept too. The pretend module remembers what was "flashed" to it in
<data dir>/demo/<family>.bin, so backups and range planning behave like the
real thing. Seed it with a real image via PQ35_DEMO_HALDEX / PQ35_DEMO_EPS.
"""
import hashlib
import json
import os
import random
import time
from pathlib import Path

from flasher.controllers.registry import get_family, parse_module, family_for_module

HALDEX, EPS, BCM = "haldex-gen4", "pq-eps", "pq-bcm"
DEFAULT_IDENTITY = {
    HALDEX: ("0BR907554A", "6816", "Haldex 4Motion"),
    EPS: ("1K0909144R", "3501", "EPS_ZFLS Kl.3"),
    BCM: ("8P0907063E", "0575", "Bordnetz-SG"),
    "engine": ("1K0907115J", "0030", "2.0l R4/4V TFSI"),
    "transmission": ("02E927770AL", "0260", "GSG DSG AG6"),
}
# Pretend fault codes. "sticky" ones are faults still present: they come straight back after a
# clear (so the app marks them "still active"); the others clear for good. Flasher stuff →
# Demo mode → Reset demo fault codes brings them all back.
def _code(p_number, text, intermittent=False, sticky=False):
    """A demo code from its OBD P0xxx number (VAG 5-digit = 16384 + the P0 number).
    sticky = the fault is still there, so it comes straight back after clearing."""
    vag = 16384 + int(p_number[1:])
    entry = {"code": vag, "code_text": str(vag), "status": 0x23 if intermittent else 0x20,
             "status_text": f"{p_number} {text}" + (" (intermittent)" if intermittent else "")}
    if sticky:
        entry["sticky"] = True
    return entry

DEMO_VIN = "WAUZZZ8P2BA000123"  # the pretend car: a 2011 Audi S3 (8P)


# Several codes so the fault lists can be looked through; a few are sticky ("still active").
DEMO_DTCS = {
    "engine": [
        _code("P0300", "Random/Multiple Cylinder Misfire Detected", intermittent=True),
        _code("P0102", "Mass or Volume Air Flow Circuit Low Input", sticky=True),
        _code("P0299", "Turbocharger/Supercharger Underboost Condition", intermittent=True),
        _code("P0171", "System Too Lean (Bank 1)"),
        _code("P0420", "Catalyst System Efficiency Below Threshold (Bank 1)", sticky=True),
        _code("P0441", "Evaporative Emission System Incorrect Purge Flow", intermittent=True),
        _code("P0016", "Crankshaft Position - Camshaft Position Correlation (Bank 1 Sensor A)"),
    ],
    "transmission": [
        {"code": 18258, "code_text": "18258", "status": 0x23,
         "status_text": "P1850 Data-Bus Powertrain: Missing Message from Engine Control (intermittent)"},
        _code("P0715", "Input/Turbine Speed Sensor Circuit", sticky=True),
        _code("P0731", "Gear 1 Incorrect Ratio", intermittent=True),
        _code("P0741", "Torque Converter Clutch Circuit Performance or Stuck Off"),
    ],
}
SEED_ENV = {HALDEX: "PQ35_DEMO_HALDEX", EPS: "PQ35_DEMO_EPS"}


def _vag(code, text, intermittent=False, sticky=False):
    """A demo code from a 5-digit VAG code (non-engine modules)."""
    entry = {"code": code, "code_text": f"{code:05d}", "status": 0x23 if intermittent else 0x20,
             "status_text": text + (" (intermittent)" if intermittent else "")}
    if sticky:
        entry["sticky"] = True
    return entry


# The pretend car's gateway list (17 modules): workshop address -> TP2 ID. 09 (the BCM) doesn't
# answer in demo, like before; everything else does.
DEMO_GATEWAY = ((0x01, 0x01), (0x02, 0x02), (0x03, 0x03), (0x08, 0x08), (0x09, 0x20), (0x15, 0x15),
                (0x16, 0x16), (0x17, 0x17), (0x19, 0x1F), (0x22, 0x0A), (0x42, 0x42), (0x44, 0x09),
                (0x46, 0x46), (0x52, 0x52), (0x55, 0x55), (0x56, 0x56), (0x65, 0x65))
# Identities of the modules the app has no family for (keyed by TP2 ID).
DEMO_OTHERS = {
    0x03: ("1K0907379AC", "0101", "ESP MK60EC1 H31"),
    0x08: ("1K0907044DT", "0120", "Climatronic"),
    0x15: ("1K0909605AB", "0012", "AirbagVW10"),
    0x16: ("1K0953549AQ", "0060", "Lenksäulenmodul"),
    0x17: ("1K0920874B", "0503", "KOMBI+WEGFAHRS. VDO"),
    0x1F: ("1K0907530L", "0204", "J533 Gateway H"),
    0x42: ("1K0959701P", "0100", "Tuer-SG"),
    0x46: ("1K0959433CT", "0058", "KSG PQ35 RDK"),
    0x52: ("1K0959702P", "0100", "Tuer-SG"),
    0x55: ("1K0907357A", "0013", "AFS-Steuergeraet"),
    0x56: ("1K0035180AC", "0026", "Radio RCD510"),
    0x65: ("1K0907273B", "0010", "RDK"),
}
DEMO_OTHER_DTCS = {
    0x03: [_vag(778, "Steering Angle Sensor (G85): No or Incorrect Basic Setting / Adaptation", sticky=True)],
    0x15: [_vag(532, "Supply Voltage B+: Signal too Low", intermittent=True)],
    0x17: [_vag(1314, "Engine Control Module: No Signal/Communication", intermittent=True),
           _vag(1321, "Control Module for Airbags (J234): No Signal/Communication", intermittent=True)],
    0x1F: [_vag(1312, "Powertrain Data Bus: Defective", intermittent=True)],
    0x46: [_vag(1331, "Door Control Module; Driver Side (J386): No Signal/Communication", sticky=True)],
}


def _decimals(name, unit):
    """How many decimals a reading shows, roughly as the ECU sends them."""
    unit = (unit or "").lower()
    if "lambda" in (name or "").lower() and not unit:
        return 3
    return {"rpm": 0, "/min": 0, "mbar": 0, "km/h": 0, "nm": 0, "km": 0, "s": 0, "ma": 0,
            "v": 2, "ms": 2, "a": 2}.get(unit, 1)


def adapter_entry():
    return {"adapter": "demo", "label": "Demo · pretend module (no hardware)", "present": True}


class DemoModule:
    def __init__(self, server, data_dir: Path):
        self.server = server
        self.root = data_dir / "demo"
        self.root.mkdir(parents=True, exist_ok=True)

    # --- pretend ECU contents ---

    def image_path(self, family) -> Path:
        return self.root / f"{family}.bin"

    def image(self, family) -> bytes:
        path = self.image_path(family)
        if not path.is_file():
            seed = os.environ.get(SEED_ENV.get(family, ""), "")
            data = Path(seed).read_bytes() if seed and Path(seed).is_file() else self._synthetic(family)
            path.write_bytes(data)
        return path.read_bytes()

    def _synthetic(self, family) -> bytes:
        if family == HALDEX:
            patches = get_family(HALDEX).patches()
            data = bytearray(b"\xff" * patches.IMAGE_SIZE)
            for start, size in patches.APP_BLOCKS:
                checksum = patches.calculate_app_checksum(bytes(data[start:start + size]))
                data[start + size - 2:start + size] = checksum.to_bytes(2, "little")
            return bytes(data)
        if family == EPS:
            patches = get_family(EPS).patches()
            data = bytearray(b"\xff" * patches.IMAGE_SIZE)
            data[0x5DFF8:0x5DFFC] = patches.CRC_MARKER
            return patches.update_checksums(bytes(data))
        if family == BCM:
            # Full-span 1 MiB image: byte 0 = global 0x700000, running through
            # 0x7FFFFF, boot-block padded. The part string lives in the
            # application window (0x780000), not the all-0xFF 0x700000 head.
            from flasher.controllers.pq_bcm import flash as bcm_flash
            data = bytearray(b"\xff" * bcm_flash.BCM_IMAGE_SIZE)
            app = bcm_flash.PFLASH_REGION[0] - bcm_flash.BCM_IMAGE_BASE
            data[app:app + 15] = b"8P0907063E 0040"
            return bytes(data)
        return b"\xff" * 0x1000

    def identity(self, family) -> dict:
        part, version, desc = DEFAULT_IDENTITY.get(family, ("000000000", "0001", family))
        if family in SEED_ENV:
            from .images import _part_numbers
            found = _part_numbers(self.image(family))
            prefixes = get_family(family).part_prefixes
            found = [p for p in found if p.startswith(prefixes)]
            part = found[-1] if found else part
            if family == EPS:
                from .images import eps_firmware
                firmware = eps_firmware(self.image(family))
                if firmware:
                    part, version = firmware
        history = self._history(family)
        return {
            "software_part_number": part, "part_number": part, "sw_version": version,
            "firmware_revision": version, "system_desc": desc + " (demo)",
            "flash_status": 0, "flash_attempts": history["attempts"],
            "flash_counter": history["successes"], "flash_date": history["date"],
            "connected": True, "controller_family": family, "demo": True,
        }

    def _history(self, family):
        path = self.root / f"{family}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {"attempts": 0, "successes": 0, "date": None}

    def _save_history(self, family, history):
        (self.root / f"{family}.json").write_text(json.dumps(history))

    # --- operations ---

    def _sleep(self, seconds):
        time.sleep(seconds)

    def _progress(self, stage, start, end, seconds, detail=lambda f: "", steps=25):
        for step in range(steps + 1):
            fraction = step / steps
            self.server.progress(stage, start + (end - start) * fraction, detail(fraction))
            self._sleep(seconds / steps)

    def _transfer(self, stage, start, end, total, seconds, steps=60):
        """Byte-level progress with speed and ETA, like the real readout/flash."""
        began = time.monotonic()
        for step in range(steps + 1):
            done = total * step // steps
            elapsed = max(time.monotonic() - began, 1e-3)
            speed = done / elapsed if step else 0.0
            eta = (total - done) / speed if speed else 0.0
            self.server.progress(stage, start + (end - start) * step / steps,
                                 f"{done}/{total} bytes", speed, eta)
            if step < steps:
                self._sleep(seconds / steps)

    def log(self, message):
        self.server.emit({"event": "log", "level": "info", "logger": "demo", "message": message})

    def _families(self):
        """The supported modules as a real scan would report them.
        PQ35_DEMO_BOOTLOADER=awd,eps pretends those modules are stuck in their loader."""
        from flasher.controllers.registry import FAMILIES
        stuck = {a.strip() for a in os.environ.get("PQ35_DEMO_BOOTLOADER", "").split(",") if a.strip()}
        results = []
        for family in FAMILIES:
            entry = {"family": family.name, "module": family.module, "status": "absent"}
            results.append(entry)
            if family.name == BCM:
                continue  # the pretend car has no BCM answering
            identity = self.identity(family.name)
            if stuck & set(family.module_aliases):
                entry.update(status="bootloader", identity=dict(identity, in_bootloader=True, sw_version="",
                                                                 system_desc="Bootloader (demo)"))
            else:
                identity["in_bootloader"] = False
                if family.name == "engine":
                    identity["mileage_km"] = 148213
                entry.update(status="ok", identity=identity)
        return results

    def scan(self, freeze_frames=False, only=None):
        """(results, gateway) like scan.scan: one pass over the pretend gateway list
        (only: the workshop addresses of a quick scan, or None for all)."""
        from .scan import FAMILY_ADDRESS, address_name, annotate
        gateway = self._gateway()
        gateway["vehicle"] = {"vin": DEMO_VIN, "source": "engine"}
        families = self._families()
        if only is not None:
            only = {int(a) for a in only}
            families = [r for r in families if FAMILY_ADDRESS.get(r["family"]) in only]
        results = annotate(families, gateway)
        by_tp2 = {e["module"]: e for e in results}
        entries = [e for e in gateway.get("entries") or [] if only is None or e["address"] in only]
        modules = []
        for index, entry in enumerate(entries):
            name = address_name(entry["address"], entry.get("name", ""))
            self.server.progress("SCAN", 100 * index / len(entries),
                                 f"Reading {entry['address']:02X} {name} ({index + 1}/{len(entries)})")
            self._sleep(0.25)
            found = by_tp2.get(entry["tp2"])
            family = found["family"] if found else None
            module = {"key": family or f"addr:{entry['address']:02X}", "address": entry["address"],
                      "name": name, "tp2": entry["tp2"], "family": family, "coded": entry.get("coded", True)}
            if found is not None:
                if found["status"] != "ok":
                    module.update(status="no_answer" if found["status"] == "absent" else "bootloader",
                                  identity=found.get("identity"))
                else:
                    module.update(status="ok", identity=found["identity"],
                                  dtc=self._dtc(family, False, freeze_frames=freeze_frames))
            elif entry["tp2"] in DEMO_OTHERS:
                part, version, desc = DEMO_OTHERS[entry["tp2"]]
                module.update(status="ok", dtc=self._dtc(None, False, entry["tp2"], freeze_frames),
                              identity={"software_part_number": part, "part_number": part,
                                        "sw_version": version, "system_desc": desc + " (demo)", "demo": True})
            else:
                module.update(status="no_answer",
                              detail=f"TP2 channel setup to 0x{entry['tp2']:02X} timed out (demo)")
            modules.append(module)
        gateway["modules"] = modules
        self.server.progress("SCAN", 100, "Done")
        return results, gateway

    @staticmethod
    def _gateway():
        """A pretend installation list: [workshop address, TP2 ID, coded]."""
        try:
            from flasher.vag_protocols.installation_list import WORKSHOP_NAMES
            from .scan import address_name
        except ImportError:
            return {"available": False, "entries": [], "error": "not supported by this flasher build"}
        rows = DEMO_GATEWAY
        return {"available": True, "entries": [
            {"address": a, "address_text": f"{a:02X}", "tp2": tp2, "bus": 0, "status": 1,
             "coded": True, "name": address_name(a, WORKSHOP_NAMES.get(a, ""))} for a, tp2 in rows]}

    # --- live logging and fault codes ---

    def _catalog(self, module):
        try:
            from flasher.measuring_catalog import values_for_module
            return values_for_module(module)
        except ImportError:
            return []

    def _log(self, family, params):
        """Fake samples, round-robin over the groups, until the UI sends stop."""
        module = get_family(family).module
        groups = sorted({int(g) for g in params.get("groups") or ()})
        if not groups:
            raise ValueError("Choose at least one value to log.")
        # (group, field) -> (name, unit); where two catalog names share a field, the later wins
        # Every location a value has (like a real module, engine speed is in groups 1, 5, 54, 113 …)
        known = {}
        for v in self._catalog(module):
            for g, f in v.get("locations") or [(v["group"], v["field"])]:
                known.setdefault((g, f), (v["name"], v["unit"]))
        known.update({(v["group"], v["field"]): (v["name"], v["unit"]) for v in self._catalog(module)})
        from .dyno import DynoPull
        car = DynoPull()  # every reading comes from one simulated 3rd-gear pull, 30 → 80 mph
        interval = max(int(params.get("interval_ms") or 0), 40) / 1000.0
        from .live import group_periods
        periods = group_periods(groups, params) or {}
        next_due = {g: 0.0 for g in groups}
        started = time.monotonic()
        count = 0
        while not self.server.stop_event.is_set():
            emitted = False
            for group in groups:
                if self.server.stop_event.is_set():
                    break
                t = time.monotonic() - started
                if t < next_due[group]:
                    continue
                car.advance(t)
                next_due[group] = t + (periods.get(group) or 0) / 1000.0
                fields = []
                for index in range(1, 5):
                    name, unit = known.get((group, index), ("", ""))
                    value = car.value(name, unit, seed=index + group)
                    decimals = _decimals(name, unit)
                    value = round(value, decimals) if decimals else int(round(value))
                    fields.append({"index": index, "formula": 0, "a": 0, "b": 0, "value": value,
                                   "unit": unit, "numeric": True,
                                   "text": f"{value:.{decimals}f} {unit}".strip()})
                self.server.emit({"event": "sample", "module": module, "group": group,
                                  "time": time.monotonic(), "wall_time": time.time(),
                                  "fields": fields, "raw": "", "error": None,
                                  "round_trip_ms": 25.0})
                count += 1
                emitted = True
                time.sleep(interval)
            if not emitted:
                time.sleep(0.01)
        elapsed = max(time.monotonic() - started, 1e-3)
        return {"samples": count, "errors": 0, "reconnects": 0,
                "achieved_hz": {str(g): round(count / len(groups) / elapsed, 1) for g in groups}}

    def _dtc(self, family, clear, tp2=None, freeze_frames=False):
        """family: a family name, or None for another gateway module (tp2 = its TP2 ID)."""
        path = self.root / (f"{family}.dtc.json" if family else f"addr{tp2:02X}.dtc.json")
        try:
            codes = json.loads(path.read_text())
        except (OSError, ValueError):
            codes = DEMO_DTCS.get(family, []) if family else DEMO_OTHER_DTCS.get(tp2, [])
        result = {}
        if clear:
            codes = [c for c in codes if c.get("sticky")]  # faults still present come straight back
            path.write_text(json.dumps(codes))
            result["clear"] = {"cleared": True, "raw": "54ff00"}
        if freeze_frames:  # a pretend freeze frame per code, shaped like decode_freeze_frame's
            codes = [dict(c, freeze_frame={"raw": "", "fields": [
                {"label": "Priority", "value": 2, "known": True},
                {"label": "Frequency", "value": 1 + i % 3, "known": True},
                {"label": "Reset Counter", "value": 13, "known": True},
                {"label": "Mileage", "value": 148213 - 9 * i, "unit": "km", "known": True}]})
                for i, c in enumerate(codes)]
        result.update(method="18 02 FF 00 (demo)", codes=codes, raw="")
        return result

    def run(self, params) -> dict:
        if params["operation"] == "scan":
            self.log("DEMO scan: no CAN traffic")
            results, gateway = self.scan(bool(params.get("freeze_frames")), params.get("only"))
            return {"operation": "scan", "exit_code": 0, "demo": True,
                    "result": results, "gateway": gateway, "full": params.get("only") is None}
        operation = params["operation"]
        tp2 = parse_module(params["module"])
        try:
            family = family_for_module(tp2).name
        except ValueError:
            if operation not in ("dtc", "dtc_clear"):
                raise
            # Another gateway module (full scan): only fault codes.
            self.log(f"DEMO {operation} on 0x{tp2:02X}: no CAN traffic")
            self._sleep(0.6)
            if tp2 not in DEMO_OTHERS:
                raise RuntimeError(f"TP2 channel setup to 0x{tp2:02X} timed out (demo)")
            return {"operation": operation, "exit_code": 0, "demo": True,
                    "result": self._dtc(None, operation == "dtc_clear", tp2,
                                        bool(params.get("freeze_frames")))}
        started = time.monotonic()
        result = {"operation": operation, "exit_code": 0, "argv": ["(demo)"], "out": params.get("out"),
                  "demo": True}
        self.log(f"DEMO {operation} on {family}: no CAN traffic")
        self._sleep(0.6)
        if operation == "identify":
            result["result"] = self.identity(family)
        elif operation == "log":
            result["result"] = self._log(family, params)
        elif operation in ("dtc", "dtc_clear"):
            result["result"] = self._dtc(family, clear=operation == "dtc_clear",
                                         freeze_frames=bool(params.get("freeze_frames")))
        elif operation == "readout":
            result.update(self._readout(family, params))
        elif operation == "flash":
            result.update(self._flash(family, params))
        elif operation == "eeprom":
            out = Path(params.get("out") or self.root / "eeprom.bin")
            out.write_bytes(bytes(random.Random(1).randrange(256) for _ in range(1024)))
            result.update(out=str(out), result={"status": "ok", "size": 1024, "path": str(out)})
        elif operation == "pageff":
            result.update(self._pageff(family, params))
        elif operation == "preconditions":
            result["result"] = self._preconditions(family, params)
        else:
            result["result"] = self._diagnostic(operation, params)
        result["elapsed_sec"] = round(time.monotonic() - started, 1)
        return result

    def _readout(self, family, params):
        data = self.image(family)
        out = Path(params.get("out") or self.root / f"readout-{int(time.time())}")
        out.mkdir(parents=True, exist_ok=False)
        # The real Haldex readout covers the application range only.
        total = 0x38000 if family == HALDEX else len(data)
        self._transfer("READ", 0, 100, total, 10.0)
        name = "pass1.bin" if family == HALDEX else "kwp_readout.bin"
        (out / name).write_bytes(data)
        status = {HALDEX: "captured_checksums_valid", EPS: "captured_complete"}.get(family, "captured_complete")
        report = {"operation": "readout", "controller_family": family, "status": status,
                  "controller": self.identity(family), "demo": True,
                  "sha256": hashlib.sha256(data).hexdigest().upper()}
        (out / "report.json").write_text(json.dumps(report, indent=2))
        return {"out": str(out), "result": report, "image": str(out / name)}

    def _flash(self, family, params):
        if family == BCM:
            return self._flash_bcm(family, params)
        patches = get_family(family).patches()
        prepared = patches.prepare_image(
            params["input"], params["start"], params["end"],
            simulator_mode=params.get("simulator_mode", False),
            correct_checksums=params.get("correct_checksum", False))
        metadata = prepared["metadata"]
        if params.get("dry_run"):
            return {"result": dict(metadata, status="validated", dry_run=True)}
        history = self._history(family)
        history["attempts"] += 1
        self._save_history(family, history)
        size = len(prepared["region"])
        for stage, percent, detail in (("CONNECTING", 5, "Opening exclusive diagnostic session"),
                                       ("SECURITY", 15, "Seed/key accepted"),
                                       ("PROGRAMMING", 22, "Entered programming session")):
            self.server.progress(stage, percent, detail)
            self._sleep(0.8)
        self._progress("ERASING", 25, 35, 1.5, lambda f: "Erasing selected regions", steps=5)
        speed = 3400.0
        seconds = min(12.0, max(3.0, size / speed / 4))
        self._transfer("WRITING", 35, 90, size, seconds)
        self.server.progress("VERIFYING", 93, "Checking transferred image checksum")
        self._sleep(1.0)
        self.server.progress("REBOOTING", 96, "Committing; fresh application verification required")
        self._sleep(1.5)
        image = bytearray(self.image(family))
        if len(image) == len(prepared["image"]):
            image[params["start"]:params["end"] + 1] = prepared["region"]
        self.image_path(family).write_bytes(bytes(image))
        history["successes"] += 1
        history["date"] = time.strftime("%Y-%m-%d")
        self._save_history(family, history)
        if os.environ.get("PQ35_DEMO_AMBIGUOUS"):
            # Like a real bench Haldex: booted, but 0x82 StopCommunication unanswered.
            return {"error": "RuntimeError: Application boot observed but commit outcome remains ambiguous",
                    "result": dict(metadata, status="recovery_required", checksum_verified=True,
                                   boot_verified=True, commit_outcome="ambiguous",
                                   commit_responses={"20": "acknowledged", "82": "Timed out"},
                                   recovery_required=True, application=self.identity(family),
                                   demo=True)}
        self.server.progress("COMPLETE", 100, "Transfer/checksum, commit and fresh application boot verified")
        return {"result": dict(metadata, status="ok", checksum_verified=True, boot_verified=True,
                               commit_outcome="acknowledged", recovery_required=False,
                               recovery_mode=params.get("recovery", False),
                               application=self.identity(family), demo=True)}

    def _flash_bcm(self, family, params):
        """Pretend BCM flash: by 16 KiB sector, no patches module. Mirrors the real
        multi-segment path (one session, a cycle per sector-run)."""
        from flasher.controllers.pq_bcm import flash as bcm_flash
        # The loader stages a verified RAM driver as download #1 before any
        # application page; mirror the real gating so the demo refuses without
        # one, exactly as the live flasher CLI does (--bcm-driver is required).
        driver = params.get("driver")
        if not driver:
            raise bcm_flash.BCMFlashNotConfigured(
                "BCM flash requires a verified RAM driver (download #1); none was supplied.")
        driver_segment = bcm_flash.prepare_driver(driver)
        if params.get("sectors"):
            app_segments = bcm_flash.plan_sectors(params["input"], list(params["sectors"]))
        else:
            app_segments = [bcm_flash.prepare_image(params["input"], params["start"], params["end"])]
        segments = bcm_flash.build_segment_plan(driver_segment, app_segments)
        meta = dict(app_segments[0]["metadata"])
        plan = [{"start_addr": s["metadata"]["start_addr"], "end_addr": s["metadata"]["end_addr"],
                 "size": s["metadata"]["size"], "checksum": s["metadata"]["checksum"]}
                for s in segments]
        meta["segments"] = plan
        if params.get("dry_run"):
            return {"result": dict(meta, status="validated", dry_run=True)}
        history = self._history(family)
        history["attempts"] += 1
        self._save_history(family, history)
        for stage, percent, detail in (("CONNECTING", 5, "Opening exclusive diagnostic session"),
                                       ("SECURITY", 15, "Seed/key accepted (27 01/02)"),
                                       ("PROGRAMMING", 22, "Resident loader ready")):
            self.server.progress(stage, percent, detail)
            self._sleep(0.6)
        image = bytearray(self.image(family))
        for index, seg in enumerate(segments):
            md = seg["metadata"]
            lo = 25 + 65 * index // len(segments)
            hi = 25 + 65 * (index + 1) // len(segments)
            self.server.progress("ERASING", lo,
                                 f"Erasing 0x{md['start_addr']:06X}..0x{md['end_addr']:06X}")
            self._sleep(0.4)
            self._transfer("WRITING", lo, hi, md["size"], min(6.0, max(1.0, md["size"] / 3400 / 4)))
            off = md["start_addr"] - bcm_flash.BCM_IMAGE_BASE
            if 0 <= off and off + md["size"] <= len(image):
                image[off:off + md["size"]] = seg["region"]
        self.server.progress("VERIFYING", 93, "Checking transferred sector checksums (31 C5)")
        self._sleep(0.8)
        self.server.progress("REBOOTING", 96, "Committing; marker cleared, booting application")
        self._sleep(1.0)
        self.image_path(family).write_bytes(bytes(image))
        history["successes"] += 1
        history["date"] = time.strftime("%Y-%m-%d")
        self._save_history(family, history)
        self.server.progress("COMPLETE", 100, "Sectors, checksum, commit and application boot verified")
        return {"result": dict(meta, status="ok", checksum_verified=True, boot_verified=True,
                               commit_outcome="acknowledged", recovery_required=False,
                               recovery_mode=params.get("recovery", False),
                               application=self.identity(family), demo=True)}

    def _pageff(self, family, params):
        """Like --read-bcm-pageff: 256 uploads of 0x40 bytes, then a .bin and a sidecar."""
        identity = self.identity(family)
        out = Path(params.get("out_dir") or self.root)
        out.mkdir(parents=True, exist_ok=True)
        data = bytearray()
        for chunk in range(1, 257):
            data += bytes(random.Random(chunk).randrange(256) for _ in range(0x40))
            self.server.progress("PAGE FF", 100 * chunk / 256, f"chunk {chunk}/256")
            self._sleep(0.02)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        image = out / f"{identity['software_part_number']}_{identity['sw_version']}_pageFF_{stamp}.bin"
        image.write_bytes(bytes(data))
        sidecar = image.with_suffix(".json")
        report = {"operation": "read_bcm_pageff", "status": "ok", "start": "0x7FC000",
                  "end": "0x7FFFFF", "size": len(data), "chunks": 256,
                  "sha256": hashlib.sha256(data).hexdigest().upper(), "demo": True}
        sidecar.write_text(json.dumps(report, indent=2))
        return {"result": report, "image": str(image), "sidecar": str(sidecar), "out": str(out)}

    def _preconditions(self, family, params):
        """Like --bcm-preconditions: read-only check of the four gates a BCM flash's
        10 85 needs. PQ35_DEMO_PRECOND_FAIL=1 returns a blocked (low-supply) set so the
        UI's red path can be demoed."""
        scaling_note = ("voltage scaling (mV) is inferred, not read from the module; treat "
                        "the volts as approximate.")
        if os.environ.get("PQ35_DEMO_PRECOND_FAIL"):
            return {"supply_raw": 11800, "supply_volts": 11.8, "supply_ok": False,
                    "attempt_counter": 0, "counter_ok": True,
                    "flag_2d09": 0, "flag_2d09_ok": True, "flag_0068": 0, "flag_0068_ok": True,
                    "all_ok": False, "blocking": ["supply 11.80 V (needs >= 12.00 V)"],
                    "scaling_note": scaling_note, "demo": True}
        return {"supply_raw": 13200, "supply_volts": 13.2, "supply_ok": True,
                "attempt_counter": 0, "counter_ok": True,
                "flag_2d09": 0, "flag_2d09_ok": True, "flag_0068": 0, "flag_0068_ok": True,
                "all_ok": True, "blocking": [], "scaling_note": scaling_note, "demo": True}

    def _diagnostic(self, operation, params):
        rng = random.Random()
        samples = int(params.get("samples") or 1)
        if operation == "faults":
            return {"status": "ok", "demo": True, "fault_count": 1, "faults": [
                {"slot": 0, "code": "00573", "text": "Terminal 30 voltage too low (demo)",
                 "snapshot": {"voltage_v": 10.8, "speed_kph": 0, "steering_angle_deg": -3.5}}]}
        if operation == "supply":
            rows = [{"terminal_30_v": round(rng.uniform(12.3, 12.7), 2),
                     "motor_supply_v": round(rng.uniform(12.1, 12.6), 2)} for _ in range(samples)]
        elif operation == "assist":
            rows = [{"assist_torque_nm": round(rng.uniform(-0.4, 0.4), 2),
                     "driver_torque_nm": round(rng.uniform(-0.2, 0.2), 2)} for _ in range(samples)]
        elif operation == "motion":
            rows = [{"steering_angle_deg": round(rng.uniform(-5, 5), 1),
                     "rack_position": rng.randrange(-40, 40)} for _ in range(samples)]
        else:
            rows = [{"hca_enabled": True, "gateway_seen": True, "messages": ["0x0D0", "0x3D0"]}]
        return {"status": "ok", "demo": True, "operation": operation, "samples": rows}
