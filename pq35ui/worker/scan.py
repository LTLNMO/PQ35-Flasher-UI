"""The scan: one pass over every module the gateway lists, like a VCDS Auto-Scan.

Read-only. Asks the gateway (TP2 0x1F) for its installation list, which maps workshop
addresses to TP2 IDs (AWD 22 -> 0x0A), then opens each module once: supported modules
(registry.FAMILIES) get their own identity read and loader check, every module running its
application gets its 1A 9B/9C identity and stored fault codes (optionally with freeze frames).
Supported addresses the list doesn't name (a bench with no gateway) are tried too: the list
is the gateway's coding, not a presence check.
"""
import argparse
import logging
import re

from flasher.controllers.registry import FAMILIES
from flasher.engine import parse_vag_identification
from flasher.vag_protocols.kwp import KWPClient, KWPProfile
from flasher.vag_protocols.tp2 import TP20Transport

# Read-only services the scan uses. A module that's busy (NRC 0x21, e.g. ABS right after the
# gateway woke it) is asked again a few times instead of being reported as refusing.
SCAN_PROFILE = KWPProfile(busy_retry_services=frozenset({0x10, 0x12, 0x13, 0x18, 0x1A, 0x22, 0x03}),
                          busy_retries=5, busy_delay=0.25)

# Application channels answer on these TP2 addresses; anything else after a
# successful channel setup means a loader is talking (see HaldexFlasher._ident).
HALDEX_APP_TX = 0x764


def _in_bootloader(family, info, tp, raw_ident: bytes) -> bool:
    if family.name == "haldex-gen4":
        return tp.tx_addr != HALDEX_APP_TX or b"B_111545" in raw_ident[2:]
    if family.name == "pq-eps":
        return "EPS_ZFLS BB" in str(info.get("system_desc", "")).upper()
    return False


def _real_application(info) -> bool:
    """A proper VAG part number plus a version: some other controller's application."""
    part = str(info.get("software_part_number") or info.get("part_number") or "").strip()
    version = str(info.get("sw_version", "")).strip()
    return bool(re.fullmatch(r"[0-9][A-Z0-9]{2}[0-9]{6}[A-Z]{0,3}", part)) and bool(version)


_VIN = re.compile(r"(?<![A-Z0-9])([A-HJ-NPR-Z0-9]{17})(?![A-Z0-9])")


def read_vin(tp, log):
    """The VIN this module stores (KWP 1A 90, then UDS-style 22 F190), or None.
    Read-only; engine and gateway modules normally answer, others may not."""
    kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
    for request in (b"\x1A\x90", b"\x22\xF1\x90"):
        try:
            reply = kwp.request(request)
        except Exception:
            continue
        match = _VIN.search(bytes(reply[2:]).decode("latin-1", "replace").upper())
        if match:
            return match.group(1)
    return None


def read_gateway(device, log) -> dict:
    """The gateway's installation list, or why there isn't one (bench, no gateway)."""
    try:
        from flasher.vag_protocols.installation_list import GATEWAY_ID, read_installation_list
        from flasher.vag_protocols.kwp import KWPNegativeResponse
    except ImportError:  # older flasher submodule
        return {"available": False, "entries": [], "error": "not supported by this flasher build"}
    try:
        tp = TP20Transport(device, module=GATEWAY_ID, timeout=1.0, log_fn=log)
    except Exception as exc:
        return {"available": False, "entries": [], "error": f"no gateway answered ({exc})"}
    try:
        kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
        try:
            entries = read_installation_list(kwp)
        except KWPNegativeResponse:
            kwp.session(0x89)
            entries = read_installation_list(kwp)
        return {"available": True, "entries": entries}
    except Exception as exc:
        return {"available": False, "entries": [], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            tp.disconnect()
        except Exception:
            pass


def annotate(results, gateway):
    """Workshop address per module, and whether the gateway lists it at all."""
    by_tp2 = {e["tp2"]: e for e in gateway.get("entries") or ()}
    for entry in results:
        workshop = by_tp2.get(entry["module"])
        entry["workshop"] = workshop
        entry["in_gateway"] = bool(workshop) if gateway.get("available") else None
    return results


MILEAGE_GROUP = 85  # engine measuring group 85, field 1: odometer in km (catalog engine.vehicle_mileage)


def read_mileage(kwp, log):
    """The odometer as the engine ECU knows it (km), or None. Best effort: not every ECU has it."""
    from flasher.vag_protocols.measuring import decode_group
    try:
        fields = decode_group(kwp.read_local_identifier(MILEAGE_GROUP))
    except Exception as exc:
        log(f"Mileage not read: {exc}")
        return None
    field = fields[0] if fields else None
    if not field or not field["numeric"] or field["unit"] not in ("km", ""):
        return None
    km = int(round(float(field["value"])))
    return km if 0 < km < 2_000_000 else None


# Workshop addresses the flasher's name table doesn't know yet (Ross-Tech wiki,
# VW Golf (1K) CAN-Gateway coding). Used only when the flasher has no name.
EXTRA_NAMES = {
    0x04: "Steering Angle", 0x0F: "Digital Radio", 0x11: "Engine II", 0x13: "Auto Dist. Reg.",
    0x1C: "Position Sensing", 0x32: "Differential Lock", 0x3C: "Lane Change",
    0x3D: "Special Function", 0x4C: "Tire Pressure II", 0x4F: "Central Elect. II",
    0x53: "Parking Brake", 0x59: "Tow Protection", 0x5C: "Lane Keeping", 0x5D: "Operations",
    0x62: "Door, Rear Left", 0x63: "Entry Assist, Driver", 0x6C: "Rear View Camera",
    0x6D: "Trunk Elect.", 0x72: "Door, Rear Right", 0x73: "Entry Assist, Pass.",
    0x7D: "Aux. Heating",
}
GATEWAY_ADDRESS = 0x19
# Same names as the app's module labels (server.FAMILY_LABELS), so one module isn't
# called two things.
PREFERRED_NAMES = {0x01: "Engine", 0x02: "Transmission", 0x09: "Central Electronics",
                   0x22: "AWD", 0x44: "Steering Assist"}


# Each supported family's workshop address (what a quick scan's address list names). Not its
# TP2 channel id: the EPS opens on TP2 0x09, the same number as the BCM's workshop address.
FAMILY_ADDRESS = {"engine": 0x01, "transmission": 0x02, "pq-bcm": 0x09, "haldex-gen4": 0x22,
                  "pq-eps": 0x44}


def address_name(address: int, name: str = "") -> str:
    """The VCDS-style name for a workshop address."""
    if address in PREFERRED_NAMES:
        return PREFERRED_NAMES[address]
    if name and not name.startswith("Address "):
        return name
    return EXTRA_NAMES.get(address, name or f"Address {address:02X}")


def _datetime(raw4):
    """VAG's packed fault time: 6 bits year (2000+), 4 month, 5 day, 5 hour, 6 minute, 6 second."""
    v = int.from_bytes(raw4, "big")
    year, month, day = 2000 + (v >> 26), (v >> 22) & 0xF, (v >> 17) & 0x1F
    hour, minute, second = (v >> 12) & 0x1F, (v >> 6) & 0x3F, v & 0x3F
    if not v or not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60):
        return None
    return f"{year}.{month:02d}.{day:02d}", f"{hour:02d}:{minute:02d}:{second:02d}"


def decode_freeze_frame(raw, code=None):
    """Fields of a VAG KWP2000 freeze frame (the 12 00 04 answer without its 0x52):

        nn 6C | 01 status | priority | frequency | reset counter | mileage (3, km) |
        time indication (2) | date+time (4, packed) | [measuring triples] | 04 code (echo)

    Checked on a 2011 A3's gateway, HVAC, radio and navigation records: the same mileage in
    every module, and dates that land in the right month. None if it isn't this layout."""
    b = bytes(raw)
    if code is not None and len(b) >= 3 and b[-3] == 0x04 and (b[-2] << 8 | b[-1]) == code:
        b = b[:-3]
    start = b.find(0x6C)
    if start < 0 or start > 2 or len(b) < start + 15 or b[start + 1] != 0x01:
        return None
    r = b[start + 2:]
    fields = [
        {"label": "Fault Priority", "value": r[1], "known": True},
        {"label": "Fault Frequency", "value": r[2], "known": True},
        {"label": "Reset counter", "value": r[3], "known": True},
    ]
    km = int.from_bytes(r[4:7], "big")
    if km:
        fields.append({"label": "Mileage", "value": km, "unit": "km", "known": True})
    fields.append({"label": "Time Indication", "value": int.from_bytes(r[7:9], "big"), "known": True})
    when = _datetime(r[9:13])
    if when:
        fields += [{"label": "Date", "value": when[0], "known": True},
                   {"label": "Time", "value": when[1], "known": True}]
    extra = r[13:]
    for k in range(0, len(extra) - 2, 3):
        formula, a, c = extra[k], extra[k + 1], extra[k + 2]
        if formula == 0x24:  # mileage (a*2560 + b*10 km); the flasher's table has it as degrees
            value, unit = a * 2560 + c * 10, "km"
        else:
            try:
                from flasher.vag_protocols.measuring import decode_value
                value, unit = decode_value(formula, a, c)
            except Exception:
                value, unit = f"0x{a:02X}{c:02X}", f"Type_{formula}"
        if isinstance(value, float):
            value = round(value, 2)
        fields.append({"label": f"Value {k // 3 + 1}", "value": value,
                       "unit": "" if str(unit).startswith("Type_") else unit, "known": False})
    return fields


def _transport_error(report) -> bool:
    """A fault-code read that broke in the transport (TP2 desync, timeout), not a module saying no."""
    err = str((report or {}).get("error") or "")
    return bool(err) and not err.startswith("Request rejected")


def _read_codes(tp, log, freeze_frames=False) -> dict:
    """Stored fault codes over an open channel (diagnostic session first), optionally with
    each code's freeze frame (12 00 04, read-only, ~0.5 s per code). Never raises."""
    from flasher.vag_protocols import dtc
    kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
    try:
        kwp.session(0x89)  # diagnostic session; some modules answer fine without it
    except Exception as exc:
        log(f"diagnostic session 0x89 not accepted ({exc})")
    try:
        report = dtc.read_dtcs(kwp, freeze_frames=freeze_frames)
    except Exception as exc:
        return {"count": 0, "codes": [], "error": f"{type(exc).__name__}: {exc}"}
    for entry in report.get("codes") or ():
        frame = entry.get("freeze_frame") or {}
        if frame.get("raw"):
            try:
                fields = decode_freeze_frame(bytes.fromhex(frame["raw"]), entry.get("code"))
            except ValueError:
                fields = None
            if fields:
                frame["fields"] = fields
                frame["layout"] = "vag"
    return report


def _identify_family(tp, family, log, vehicle):
    """Who a supported module is, over an open channel: its own identity read where it has
    one, the loader check, and (once per car) the VIN; mileage on the engine.
    Returns (entry fields, vehicle)."""
    fields = {"status": "responding", "tp_tx": tp.tx_addr, "tp_rx": tp.rx_addr}
    try:
        proto = family.protocol()
        ident_hook = getattr(proto, "read_identification", None)
        info = None
        raw_ident = b""
        mismatch_part = ""
        if ident_hook is not None:
            # Families with a non-1A identity (e.g. the service-22 BCM, which NRC-11s on
            # 1A 9B/9C and would otherwise look "stuck") identify through their own hook.
            try:
                info = ident_hook(tp)
            except Exception as exc:
                # Some other controller at this address (e.g. a Golf's 1K0937049 Central
                # Electronics: KWP 1A, no service 22). Try the common identity first.
                log(f"{family.name}: own identity read failed ({exc}); trying 1A 9B/9C")
                ident_hook = None
                found = re.search(r"received '([^']+)'", str(exc))
                if found:  # it answered with its part number: a running module, just not ours
                    mismatch_part = found.group(1).strip()
        kwp = None
        if info is None:
            kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
            try:
                raw_ident = kwp.read_ecu_ident(0x9B)
                raw_status = kwp.read_ecu_ident(0x9C)
                info = parse_vag_identification(raw_ident, raw_status)
            except Exception:
                if not mismatch_part:
                    raise
                # e.g. an 8P0907063K Central Electronics where the flasher knows the 063E: it
                # identified itself over service 22 but has no 1A. Not a loader.
                info = {"part_number": mismatch_part, "software_part_number": mismatch_part,
                        "system_desc": "", "sw_version": ""}
        decorate = getattr(proto, "decorate_identification", None)
        if decorate is not None:
            info = decorate(info)
        info["in_bootloader"] = _in_bootloader(family, info, tp, raw_ident)
        if not vehicle and not info["in_bootloader"]:
            vin = read_vin(tp, log)
            if vin:
                vehicle = {"vin": vin, "source": family.name}
                info["vin"] = vin
        if family.name == "engine" and not info["in_bootloader"] and kwp is not None:
            km = read_mileage(kwp, log)
            if km is not None:
                info["mileage_km"] = km
        info.update(connected=True, module=family.module, controller_family=family.name)
        fields["identity"] = info
        if info["in_bootloader"]:
            fields["status"] = "bootloader"
        elif family.matches_identification(info) and str(info.get("sw_version", "")).strip():
            fields["status"] = "ok"
        elif _real_application(info) or mismatch_part:
            fields["status"] = "unexpected"
        else:
            # Loader markers vary; a half-answer at a module address is treated as "stuck"
            # so recovery is offered.
            fields["status"] = "bootloader"
    except Exception as exc:
        # Opened a channel but wouldn't identify: typically a loader.
        fields["detail"] = f"{type(exc).__name__}: {exc}"
    return fields, vehicle


def read_module(device, tp2: int, log, identity=None, identify=True) -> dict:
    """Open one module, read who it is (unless already known) and its stored
    fault codes. Read-only. {"status": "ok" | "no_answer" | "error", ...}"""
    out = {"status": "no_answer"}
    try:
        tp = TP20Transport(device, module=tp2, timeout=1.0, log_fn=log)
    except Exception as exc:  # no channel setup reply: not fitted, asleep or on another bus
        out["detail"] = str(exc)
        return out
    out.update(status="ok", tp_tx=tp.tx_addr, tp_rx=tp.rx_addr)
    try:
        kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
        if identity:
            out["identity"] = identity
        elif identify:
            try:
                out["identity"] = parse_vag_identification(kwp.read_ecu_ident(0x9B),
                                                           kwp.read_ecu_ident(0x9C))
            except Exception as exc:
                out["identity_error"] = f"{type(exc).__name__}: {exc}"
        out["dtc"] = _read_codes(tp, log)
    except Exception as exc:
        out.update(status="error", detail=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            tp.disconnect()
        except Exception:
            pass
    return out


def scan(adapter: dict, progress, open_adapter, known_vehicle=None, freeze_frames=False, only=None):
    """(supported-module results, gateway info with "modules": every listed module read).

    only: workshop addresses to read (a quick scan), or None for every module (a full scan).
    The gateway is always asked first: its list says which channel each address opens on.

    known_vehicle: a {"vin", ...} already read in an earlier scan. When given, the VIN read is
    skipped (it only needs reading once per car)."""
    args = argparse.Namespace(
        adapter=adapter["adapter"], dll=adapter.get("dll"), channel=adapter.get("channel"),
        serial=adapter.get("serial"), bus=int(adapter.get("bus") or 0),
        baud=int(adapter.get("baud") or 500000))
    log = logging.getLogger("ModuleScan").info
    vehicle = dict(known_vehicle) if known_vehicle and known_vehicle.get("vin") else {}
    device = open_adapter(args)
    try:
        progress("SCAN", 0, "Asking the gateway what's installed")
        gateway = read_gateway(device, log)
        for entry in gateway.get("entries") or ():
            entry["name"] = address_name(entry["address"], entry.get("name", ""))
        results, gateway["modules"], vehicle = _one_pass(device, gateway, progress, log, vehicle,
                                                         freeze_frames, only)
        progress("SCAN", 100, "Done")
    finally:
        device.close()
    gateway["vehicle"] = vehicle
    return annotate(results, gateway), gateway


def _one_pass(device, gateway, progress, log, vehicle, freeze_frames=False, only=None):
    """Every gateway-listed module, then supported addresses the list doesn't name (e.g. a
    bench with no gateway), each opened once; listed modules that didn't answer get a second,
    slower try at the end. Returns (results, modules, vehicle):
    results = one entry per supported family (status ok/unexpected/bootloader/responding/absent),
    modules = one entry per listed module (key, address, name, tp2, family, coded, status, identity, dtc)."""
    families = {family.module: family for family in FAMILIES}
    targets, seen = [], set()
    only = None if only is None else {int(a) for a in only}
    for entry in gateway.get("entries") or ():
        if only is not None and entry["address"] not in only:
            seen.add(entry["tp2"])  # listed, just not part of this quick scan
            continue
        if entry["tp2"] not in seen:
            seen.add(entry["tp2"])
            targets.append({"address": entry["address"], "name": entry["name"], "tp2": entry["tp2"],
                            "coded": entry.get("coded", True)})
    for family in FAMILIES:
        if only is not None and FAMILY_ADDRESS.get(family.name) not in only:
            continue
        if family.module not in seen:
            targets.append({"address": None, "name": "", "tp2": family.module, "coded": None})
    visits = []  # (target, result or None, module or None)
    for index, target in enumerate(targets):
        family = families.get(target["tp2"])
        label = f"{target['address']:02X} {target['name']}" if target["address"] is not None else family.name
        progress("SCAN", 100 * index / len(targets), f"Reading {label} ({index + 1}/{len(targets)})")
        result, module, vehicle = _visit(device, target, family, log, vehicle, freeze_frames, 1.0)
        visits.append([target, result, module])
    # Second chance: a coded module that didn't open a channel may just have been slow (the
    # same car's immobilizer answered one scan and not the next). Try those again, slower.
    again = [v for v in visits if v[2] is not None and v[2].get("status") == "no_answer"
             and v[0].get("coded", True) is not False]
    for k, visit in enumerate(again):
        target = visit[0]
        progress("SCAN", 99, f"Trying {target['address']:02X} {target['name']} again ({k + 1}/{len(again)})")
        result, module, vehicle = _visit(device, target, families.get(target["tp2"]), log, vehicle,
                                         freeze_frames, 2.0)
        if module is not None and module.get("status") != "no_answer":
            visit[1], visit[2] = result, module
    results = [r for _t, r, _m in visits if r is not None]
    modules = [m for _t, _r, m in visits if m is not None]
    return results, modules, vehicle


def _visit(device, target, family, log, vehicle, freeze_frames, timeout):
    """Open one module's channel and read it. Returns (family result or None, listed-module entry
    or None, vehicle)."""
    listed = target["address"] is not None
    module = dict(target, family=family.name if family else None,
                  key=family.name if family else f"addr:{target['address']:02X}")
    result = None
    try:
        tp = TP20Transport(device, module=target["tp2"], timeout=timeout, log_fn=log)
    except Exception as exc:  # no channel setup reply: not fitted, asleep or on another bus
        if family:
            result = {"family": family.name, "module": family.module, "status": "absent",
                      "detail": str(exc)}
        return result, (dict(module, status="no_answer", detail=str(exc)) if listed else None), vehicle
    try:
        if family:
            fields, vehicle = _identify_family(tp, family, log, vehicle)
            result = dict({"family": family.name, "module": family.module}, **fields)
            status = fields["status"]
            if status in ("bootloader", "responding"):
                # A loader waiting for a flash: leave it alone (recovery is on Connect).
                return result, dict(module, status="bootloader", identity=fields.get("identity")), vehicle
            if status == "unexpected":
                if not listed:
                    return result, None, vehicle  # not ours and not on the list: nothing more
                # Some other controller at a supported address: read it as a plain
                # gateway module (its own card, fault codes only).
                module.update(family=None, key=f"addr:{target['address']:02X}")
            identity = fields.get("identity")
        else:
            kwp = KWPClient(tp, log_fn=log, profile=SCAN_PROFILE)
            identity = None
            try:
                identity = parse_vag_identification(kwp.read_ecu_ident(0x9B), kwp.read_ecu_ident(0x9C))
            except Exception as exc:
                module["identity_error"] = f"{type(exc).__name__}: {exc}"
        report = _read_codes(tp, log, freeze_frames)
        if _transport_error(report):
            # The channel broke mid-read (e.g. an out-of-order TP2 ack): open it again, read once more.
            log(f"{module['key']}: fault-code read broke ({report['error']}); reopening the channel")
            try:
                tp.disconnect()
            except Exception:
                pass
            try:
                tp = TP20Transport(device, module=target["tp2"], timeout=max(timeout, 1.5), log_fn=log)
                second = _read_codes(tp, log, freeze_frames)
                if not _transport_error(second):
                    report = second
            except Exception as exc:
                log(f"{module['key']}: couldn't reopen ({exc})")
        module.update(status="ok", tp_tx=tp.tx_addr, tp_rx=tp.rx_addr, dtc=report)
        if identity:
            module["identity"] = identity
        return result, module, vehicle  # (a supported module off the list too: a bench)
    except Exception as exc:
        return result, (dict(module, status="error", detail=f"{type(exc).__name__}: {exc}")
                        if listed else None), vehicle
    finally:
        try:
            tp.disconnect()
        except Exception:
            pass
