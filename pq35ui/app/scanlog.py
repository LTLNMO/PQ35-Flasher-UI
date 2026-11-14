"""Scan logs in the layout of a VCDS Auto-Scan log, so people who read those feel at home.

PRIMARY REFERENCE (2026-11-10): a real VCDS 18.9 Auto-Scan of a PQ35 car (Audi A3 8P,
WAUZZZ8P6CA114311) the user pasted: KWP modules, so the fault lines, freeze frames, header
(two 80-dash lines), "Part No:" lines, 8-space "Labels:" gap and End line follow that.
The Ross-Tech MQB sample below was the first reference (UDS-era layout).

Matched line for line to Ross-Tech's published sample (Scan-WVW487CD4NW186292-20220610-
1101-175km-108mi-2.txt): the date line, the two blank lines, "VIN: …   License Plate: ",
"Chassis Type:", "Scan:" wrapped at 20 addresses with a 10-space continuation, the
"01-Engine -- Status: Malfunction 0010" list, 79-dash separators, "Address 01: …" blocks with
3-space field indents, "N Faults Found:", fault detail lines at 10 spaces, freeze frames at
13/20 spaces, trailing spaces where VCDS has them, the "End----------(Elapsed Time: mm:ss…)"
line, LF line endings and Latin-1 text (for "°C").

Deliberately different: the second header line names this app, never VCDS (it isn't a VCDS
file). Lines that come from Ross-Tech's own data (ASAM Dataset, ROD, VCID, J-numbers, label
files) are left out rather than faked, and so are lines we don't read yet (hardware part
number, coding, shop #) unless the scan has them. The fault "elaboration" and the
Intermittent/Confirmed words need the status byte decoded, which isn't verified for VAG KWP
modules yet, so only the raw status number is shown, as VCDS does in its [brackets].
"""
import platform
import time

SEPARATOR = "-" * 79
HEADER_RULE = "-" * 80
# VCDS's own names per address (from the PQ35 sample + the flasher's table), so the log reads
# like a VCDS one even where the app's cards use longer names.
VCDS_NAMES = {
    0x01: "Engine", 0x02: "Auto Trans", 0x03: "ABS Brakes", 0x04: "Steering Angle",
    0x08: "Auto HVAC", 0x09: "Cent. Elect.", 0x10: "Park/Steer Assist", 0x15: "Airbags",
    0x16: "Steering wheel", 0x17: "Instruments", 0x19: "CAN Gateway", 0x22: "AWD",
    0x25: "Immobilizer", 0x42: "Door Elect, Driver", 0x44: "Steering Assist",
    0x46: "Central Conv.", 0x47: "Sound System", 0x52: "Door Elect, Pass.", 0x56: "Radio",
    0x61: "Battery Regul.", 0x77: "Telephone",
}
# VIN maker (WMI) -> VCDS's chassis-family letters ("8P-AU35", "1K-VW35").
MAKES = {"WAU": "AU", "TRU": "AU", "WVW": "VW", "WV1": "VW", "WV2": "VW", "3VW": "VW",
         "1VW": "VW", "VSS": "SE", "TMB": "SK"}
KM_PER_MILE = 1.609344
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
FREEZE_FIRST = (("Priority", "Fault Priority"), ("Frequency", "Fault Frequency"),
                ("Reset Counter", "Reset counter"), ("Mileage", "Mileage"))


def part_spaced(part):
    """VCDS writes part numbers in groups: 1K0907115J -> "1K0 907 115 J"."""
    part = (part or "").replace(" ", "")
    if len(part) < 9:
        return part
    return " ".join(p for p in (part[:3], part[3:6], part[6:9], part[9:]) if p)


def _mileage(result):
    for module in (result.get("gateway") or {}).get("modules") or ():
        km = (module.get("identity") or {}).get("mileage_km")
        if km:
            return int(km)
    for entry in result.get("result") or ():
        km = (entry.get("identity") or {}).get("mileage_km")
        if km:
            return int(km)
    return None


def _vin(result):
    return ((result.get("gateway") or {}).get("vehicle") or {}).get("vin", "")


def filename(result, when=None):
    """Scan-<VIN>-<YYYYMMDD>-<HHMM>-<km>km-<miles>mi.txt, like VCDS."""
    when = time.localtime(when or time.time())
    name = f"Scan-{_vin(result) or 'NOVIN'}-{time.strftime('%Y%m%d-%H%M', when)}"
    km = _mileage(result)
    if km is not None:
        name += f"-{km}km-{round(km / KM_PER_MILE)}mi"
    return name + ".txt"


def _name(module):
    return VCDS_NAMES.get(module.get("address"), module.get("name") or "Unknown")


def _chassis(result, vin):
    """"8P-AU35 (7N0)": VIN chassis code, make + platform, gateway part-number prefix."""
    code = vin[6:8] if len(vin) == 17 else "??"
    make = MAKES.get(vin[:3], "VW")
    gateway = next((m for m in (result.get("gateway") or {}).get("modules") or ()
                    if m.get("address") == 0x19), None)
    part = ((gateway or {}).get("identity") or {}).get("software_part_number") or ""
    return f"{code}-{make}35" + (f" ({part[:3]})" if part else "")


def _component(ident):
    """The component field as the module sends it (internal and trailing padding kept),
    then the software version: "R4 1.6l TDI   H43 9978  "."""
    raw = ident.get("raw_9b")
    desc = ident.get("system_desc", "")
    if raw:
        try:
            desc = bytes.fromhex(raw)[2 + 26:].decode("latin-1").rstrip("\x00")
        except ValueError:
            pass
    return f"   Component: {desc} {ident.get('sw_version', '')}  "


def _status(module):
    """The module-list status, with VCDS's four-digit codes where we know them."""
    report = module.get("dtc") or {}
    if module.get("status") == "ok" and "dtc" in module and not report.get("error"):
        return "Malfunction 0010" if report.get("codes") else "OK 0000"
    if module.get("status") == "ok":
        if str(report.get("error") or "").startswith("Request rejected"):
            return "Fault codes not supported"  # answers, but refused every fault-code request
        return "Fault codes couldn't be read"  # the read broke (busy, transport), not a refusal
    if module.get("status") == "bootloader":
        return "In bootloader"
    return "Cannot be reached 1100"


def _listed(module):
    """VCDS lists what the gateway has coded; an uncoded address that didn't answer isn't scanned."""
    return module.get("address") is not None and not (module.get("coded") is False
                                                       and module.get("status") != "ok")


def _fault_lines(code, active, descriptions):
    """One fault, KWP style (VCDS on a PQ35 car):

    03306 - Coding Not Valid 
                000 -  -  - Intermittent
                 Freeze Frame:
                        Fault Status: 00100000
                        ...

    The status byte decoding is from that sample only (0x20 -> Intermittent shown,
    0x60 -> not; elaboration number = low 5 bits, 000 in both): treat as provisional."""
    lines = []
    text = code.get("code_text") or str(code.get("code"))
    pcode = code.get("p_code")
    description = descriptions(code) if descriptions else None
    lines.append(f"{text} - {description or 'No description available'} ")
    status = code.get("status")
    detail = "            " + (f"{pcode} - " if pcode else "")
    if isinstance(status, int):
        elaboration = status & 0x1F
        intermittent = bool(status & 0x20) and not status & 0x40
        detail += f"{elaboration:03d} -  - " + (" - Intermittent" if intermittent else "")
    else:
        detail += "--- -  - "
    lines.append(detail)
    if text in active:
        lines.append("            Still present: came straight back after clearing")
    frame = code.get("freeze_frame") or {}
    fields = frame.get("fields") or []
    if fields or isinstance(status, int) and frame:
        known = {f["label"]: f for f in fields}
        lines.append("             Freeze Frame:")
        if isinstance(status, int):
            lines.append(f"                    Fault Status: {status:08b}")
        if frame.get("layout") == "vag":
            # Decoded from VAG's fixed layout (worker/scan.decode_freeze_frame): shown as it
            # comes, the record's own fields first, its measuring values in a second block.
            for field in [f for f in fields if f.get("known")]:
                unit = f" {field['unit']}" if field.get("unit") else ""
                lines.append(f"                    {field['label']}: {field['value']}{unit}")
            rest = [f for f in fields if not f.get("known")]
        else:
            for label, shown in FREEZE_FIRST:
                field = known.get(label)
                if field is not None:
                    unit = f" {field['unit']}" if field.get("unit") else ""
                    lines.append(f"                    {shown}: {field['value']}{unit}")
            lines.append("                    Time Indication: 0")
            rest = [f for f in fields if f["label"] not in dict(FREEZE_FIRST)]
        if rest:
            lines += ["", "             Freeze Frame:"]
            for field in rest:
                unit = f" {field['unit']}" if field.get("unit") else ""
                lines.append(f"                    {field['label']}: {field['value']}{unit}")
    lines.append("")
    return lines


def build(result, *, started=None, elapsed=None, app_version="", interface="", rows=None,
          descriptions=None):
    """The log text for a scan result (Connect's scan: result["gateway"]["modules"] etc.).

    rows: the fault controller's rows ({key: {"active": [...]}}) to mark codes that came back
    after a clear. descriptions: optional fn(code dict) -> description text."""
    started = started or time.time()
    lt = time.localtime(started)
    fraction = int((started % 1) * 100000)
    gateway = result.get("gateway") or {}
    modules = [m for m in gateway.get("modules") or () if _listed(m)]
    vin = _vin(result)
    km = _mileage(result)
    out = [
        f"{WEEKDAYS[lt.tm_wday]},{lt.tm_mday:02d},{MONTHS[lt.tm_mon - 1]},{lt.tm_year},"
        f"{time.strftime('%H:%M:%S', lt)}:{fraction:05d}",
        f"PQ35 Flasher -- PQ35 diagnostic scan Running on {platform.system()} {platform.release()} "
        f"{platform.machine()}",
        f"PQ35 Flasher Version: {app_version or 'dev'}",
        "Data version: TP2.0 / KWP2000, gateway installation list",
        f"Interface: {interface or 'unknown'}",
        "",
        "",
        f"VIN: {vin}   License Plate: ",
        "",
        "",
        "",
        HEADER_RULE,
        HEADER_RULE,
        "",
        "",
        f"Chassis Type: {_chassis(result, vin)}",
    ]
    addresses = [f"{m['address']:02X}" for m in modules]
    for start in range(0, max(len(addresses), 1), 20):
        chunk = " ".join(addresses[start:start + 20])
        out.append(("Scan: " if start == 0 else " " * 10) + chunk)
    out += ["", f"VIN: {vin}   " + (f"Mileage: {km}km-{round(km / KM_PER_MILE)}miles" if km else ""), ""]
    for m in modules:
        out.append(f"{m['address']:02X}-{_name(m)} -- Status: {_status(m)}")
    out.append(" ")
    for m in modules:
        out.append(SEPARATOR)
        # No J-number from us, so VCDS's 8-space gap before "Labels:".
        out.append(f"Address {m['address']:02X}: {_name(m)}        Labels: None")
        status = m.get("status")
        if status not in ("ok", "bootloader"):
            out += ["Cannot be reached", ""]
            continue
        ident = m.get("identity") or {}
        part = ident.get("software_part_number") or ident.get("part_number")
        if part:
            hw = ident.get("hardware_part_number")
            out.append(f"   Part No SW: {part_spaced(part)}    HW: {part_spaced(hw)}" if hw
                       else f"   Part No: {part_spaced(part)}")
            out.append(_component(ident))
        if ident.get("coding"):
            out.append(f"   Coding: {ident['coding']}")
        if ident.get("shop"):
            out.append(f"   Shop #: {ident['shop']}")
        out.append("")
        if status == "bootloader":
            out += ["Module is in its bootloader: no fault codes read.", ""]
            continue
        report = m.get("dtc") or {}
        if report.get("error") or "dtc" not in m:
            err = str(report.get("error") or "")
            out += [("Fault codes could not be read (the module refused the request)."
                     if err.startswith("Request rejected") else
                     f"Fault codes could not be read ({err or 'no answer'})."), ""]
            continue
        codes = report.get("codes") or []
        if not codes:
            out += ["No fault code found.", ""]
            continue
        active = set(((rows or {}).get(m.get("key")) or {}).get("active") or ())
        out.append(f"{len(codes)} Fault{'s' if len(codes) != 1 else ''} Found:")
        for code in codes:
            out += _fault_lines(code, active, descriptions)
        out.append("")  # VCDS leaves an extra blank line after a module's faults
    seconds = int(elapsed) if elapsed is not None else 0
    out.append(f"End{'-' * 25}(Elapsed Time: {seconds // 60:02d}:{seconds % 60:02d}){'-' * 26}")
    return "\n".join(out) + "\n"


def encode(text):
    """Bytes as VCDS writes them (Latin-1; anything outside it becomes '?')."""
    return text.encode("latin-1", errors="replace")
