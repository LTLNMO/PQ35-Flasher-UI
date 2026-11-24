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
        "injection near 0): ignore it, it is not a lean condition. Under wide-open throttle the target is "
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
