"""What the AI should know about the engine's logged channels, in one place (no Qt).

Two kinds of knowledge:
  * READING: how the Logger reads the car (one measuring group per request, so every extra group
    slows every channel), and how its CSV relates to real reads.
  * NOTES: facts about individual channels on this car's ECU, checked against raw bytes, the
    engine-off group dump and real logs (2026-11-23), e.g. that the knock "voltage" isn't volts
    or that load can't show more than 191.7 %. Each note is keyed by catalog id and carries the
    catalog name, so logs without ids (older, imported) still get their notes by name.

Used by the smart-preset / Ask AI prompts (smart_presets.LoggerProfile), the Logger's Assistant
context, the log digest the AI reads, and the channel tooltips (channel_info)."""
import re

READ_S = 0.06  # seconds per measuring-group read on this ECU (KWP2000 over TP2.0), measured in logs

READING = (
    "How the logger reads this engine ECU (VW/Audi PQ35, KWP2000 over TP2.0): it asks for one "
    "measuring group at a time, about 0.06 s per group, and cycles through the groups that hold the "
    "chosen channels. A group returns up to 10 fields read at the same instant, so channels in the same "
    "group cost nothing extra; each extra group slows every channel (with N groups each channel "
    "refreshes about every N x 0.06 s). The CSV has one row per group read with every other column "
    "carried forward from its last read, so a value is only fresh on rows from its own group. "
    "Values are decoded from each field's formula byte and two data bytes (A, B); a value pinned at a "
    "formula's ceiling (B = 255) means 'at least this', not an exact reading.")

# id -> (catalog name, note). Facts, not advice; the AI and the tooltips both show them.
NOTES = {
    "engine.load.actual": ("Engine load (actual)",
        "Formula 100*B/A with A = 133 on this ECU, so it cannot show more than 191.7 %. At 191.7 % the "
        "real load is at least that (seen at wide-open throttle above ~4,500 rpm)."),
    "engine.load.spec": ("Engine load (specified)",
        "Same 191.7 % ceiling as actual load. It also sits at 191.7 % on overrun right after a pull "
        "(pedal at 0), which is what the ECU sends, not a fault."),
    "engine.boost.actual_absolute": ("Boost pressure (actual absolute)",
        "Absolute pressure before the throttle (charge pressure). At idle it equals the outside air "
        "pressure: about 1000 mbar at sea level, about 860 mbar in this car's logs (roughly 1,300 m "
        "altitude). Boost above atmosphere = this minus the idle value."),
    "engine.boost.spec_absolute": ("Boost pressure (specified absolute)",
        "Reads about 330 mbar at idle, below outside pressure, so at part load it behaves like an intake "
        "manifold pressure target rather than a charge pressure target. Compare it with actual boost only "
        "under boost (wide-open throttle), where the two agree."),
    "engine.knock.cylinder1.sensor_voltage": ("Knock sensor voltage cylinder 1",
        "Not real volts: the ECU's knock signal level (formula 0.001*A*B with A = 156). It reads 16.7 on "
        "every cylinder with the engine off, about 1-2 at idle and 15-23 above 5,000 rpm. Compare "
        "cylinders with each other; use timing retard to judge knock."),
    "engine.timing_retard.cylinder1": ("Timing retard cylinder 1",
        "Negative = retard, 0 = none, in 0.75 degree steps on this ECU. The worst value is the minimum, "
        "not the maximum. This is knock control acting, cylinder by cylinder."),
    "engine.lambda.bank1.actual": ("Lambda control bank 1 (actual)",
        "Measured lambda. 1.992 (B = 255, the formula's ceiling) appears on overrun fuel cut (pedal 0, "
        "injection near 0) and with the engine off: ignore it, it is not a lean condition. Under wide-open throttle the target is "
        "about 0.84."),
    "engine.lambda.bank1.specified": ("Lambda control bank 1 (specified)",
        "The lambda target: 1.0 at cruise, dropping to about 0.84 at wide-open throttle on this car."),
    "engine.lambda.bank1.sensor1.control": ("Lambda control bank 1 sensor 1",
        "Short-term correction from the front sensor: positive = the ECU is adding fuel, negative = taking "
        "fuel away. Exactly 0 while the controller is off (fuel cut, warm-up)."),
    "engine.ignition_timing": ("Ignition timing",
        "Signed: positive = before top dead centre (advance), negative = after (retarded, e.g. on "
        "overrun or a big knock pull)."),
    "engine.pedal_position": ("Accelerator pedal position (G79)",
        "0 to about 99 %; the logger's trigger channel by default."),
    "engine.fuel_rail.actual": ("Fuel rail pressure (actual)",
        "High-pressure rail in bar, formula (256*A+B)/100. Integrated Engineering logs label group 106 "
        "differently (their 'actual' is the target); trust these names."),
    "engine.fuel_rail.spec": ("Fuel rail pressure (specified)", "The high-pressure rail target in bar."),
    "engine.misfire.cylinder1.count": ("Misfire counter cylinder 1",
        "Misfires counted by the ECU since the counters were last reset (a count, not a rate)."),
}
for _n in (2, 3, 4):  # per-cylinder twins share their note
    for _base, _name in (("engine.knock.cylinder{}.sensor_voltage", "Knock sensor voltage cylinder {}"),
                         ("engine.timing_retard.cylinder{}", "Timing retard cylinder {}"),
                         ("engine.misfire.cylinder{}.count", "Misfire counter cylinder {}")):
        NOTES[_base.format(_n)] = (_name.format(_n), NOTES[_base.format(1)][1])

# Factory ranges and bit meanings, read off OBDeleven's descriptions for this car's ECU
# (screenshots 2026-11-23, Documents/PQ35 Flasher/Verify). Same group/field positions as the catalog.
# id -> (catalog name, text); appended to the note above when there is one.
SPECS = {
    "engine.rpm": ("Engine speed", "Spec at idle: 640-800 rpm (range 0-6,500)."),
    "engine.coolant_temperature": ("Coolant temperature (G62)", "Spec warm: 80-115 C."),
    "engine.intake_temperature": ("Intake air temperature (G42)", "Spec warm: below 80 C (max 115 C)."),
    "engine.load.actual": ("Engine load (actual)", "Spec at idle: 16-35 %."),
    "engine.maf": ("Mass air flow (G70)", "Spec: 2-5 g/s at idle, 150-170 g/s at full load (range 0-200)."),
    "engine.injection_time": ("Injection duration", "Range 0-20 ms."),
    "engine.terminal30_voltage": ("Terminal 30 voltage (engine ECU)", "Battery voltage; spec 11-15 V."),
    "engine.ignition_timing": ("Ignition timing", "Range -20 to +50 degrees."),
    "engine.altitude_correction_factor": ("Altitude correction factor",
        "Linear with altitude: 0 % = sea level, -10 % per 1,000 m (-50 % = 5,000 m, +20 % = -2,000 m). "
        "This car reads -13 to -15 %, i.e. about 1,300-1,500 m."),
    "engine.lambda.control.bank1": ("Lambda control bank 1", "Spec -10 to +10 % (range -25 to +25 %)."),
    "engine.lambda.bank1.sensor1.control": ("Lambda control bank 1 sensor 1",
                                            "Spec -10 to +10 % (range -25 to +25 %)."),
    "engine.lambda.bank1.sensor1.adaptation.idle": ("Lambda adaptation bank 1 sensor 1 (idle)",
        "Long-term fuel trim at idle; spec -10 to +10 % (range -25 to +25 %). Beyond +10 % = running "
        "lean and the ECU adds fuel (vacuum leak, weak fuel supply)."),
    "engine.lambda.bank1.sensor1.adaptation.partial": ("Lambda adaptation bank 1 sensor 1 (partial)",
        "Long-term fuel trim at part load; spec -10 to +10 % (range -25 to +25 %). Beyond +10 % = lean "
        "under load (boost/intake leak, MAF reading low, fuel pressure)."),
    "engine.lambda.bank1.sensor1.voltage": ("Lambda sensor voltage bank 1 sensor 1", "Range 0-5 V."),
    "engine.timing_retard.cylinder1": ("Timing retard cylinder 1",
        "Factory spec: up to 12 degrees of crank (shown as -12 here)."),
    "engine.misfire.count": ("Misfire counter", "Spec 0-8 (total, all cylinders)."),
    "engine.misfire.recognition": ("Misfire recognition",
                                   "Whether misfire detection is enabled or blocked (blocked with the engine off)."),
    "engine.load.status": ("Engine load status",
        "Idle / part throttle / wide-open throttle (enrichment) / overrun (deceleration)."),
    "engine.knock.cylinder1.sensor_voltage": ("Knock sensor voltage cylinder 1",
        "OBDeleven shows the same 16.692 V engine-off reading, with a 0-40 V range."),
    "engine.basic_setting.requirements": ("Basic setting requirements",
        "8 bits, right to left, 1 = met: coolant above 80 C; rpm below 2,000; throttle closed; lambda "
        "control OK; idle switch closed; A/C compressor off; catalyst above 380 C; no fault codes."),
    "engine.lambda.bank1.sensor1.condition": ("Lambda sensor condition bank 1 sensor 1",
        "5 bits, left to right: cylinder-1 lambda control active; catalyst purge active; sensor heating "
        "on; sensor ready; lambda control active."),
    "engine.lambda.bank1.sensor2.condition": ("Lambda sensor condition bank 1 sensor 2",
        "4 bits, left to right: P-portion control active; sensor heating on; sensor ready; "
        "I-portion control active."),
}
for _n in (2, 3, 4):
    for _base in ("engine.timing_retard.cylinder{}", "engine.knock.cylinder{}.sensor_voltage"):
        _one = SPECS[_base.format(1)]
        SPECS[_base.format(_n)] = (_one[0][:-1] + str(_n), _one[1])
for _cid, (_name, _spec) in SPECS.items():
    NOTES[_cid] = (_name, f"{NOTES[_cid][1]} {_spec}" if _cid in NOTES else _spec)

_BY_NAME = {name.lower(): note for name, note in NOTES.values()}


def _base(header):
    """'Engine load (actual) (%)' -> 'engine load (actual)': the name without its unit."""
    h = header.strip()
    m = re.match(r"^(.*\S)\s*\(([^()]*)\)$", h)
    if m and (m.group(1).lower() in _BY_NAME or not any(k.startswith(h.lower()) for k in _BY_NAME)):
        return m.group(1).lower()
    return h.lower()


def note_for(cid=None, name=None):
    """The note for a catalog id, else for a channel name or log header (with or without its unit)."""
    if cid and cid in NOTES:
        return NOTES[cid][1]
    if name:
        return _BY_NAME.get(name.strip().lower()) or _BY_NAME.get(_base(name))
    return None


def notes_text(headers=(), ids=()):
    """'Channel notes' lines for the channels given (by id or name), each once; '' if none."""
    seen, lines = set(), []
    for cid in ids:
        n = note_for(cid=cid)
        if n and n not in seen:
            seen.add(n)
            lines.append(f"- {NOTES[cid][0]}: {n}")
    for h in headers:
        n = note_for(name=h)
        if n and n not in seen:
            seen.add(n)
            lines.append(f"- {h}: {n}")
    return "Channel notes (checked on this car):\n" + "\n".join(lines) if lines else ""


def first_sentence(text, limit=110):
    text = (text or "").strip()
    cut = text.find(". ")
    text = text[:cut + 1] if 0 < cut < limit else text
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
