"""Which modules the quick Scan reads (Connect's Scan and the Dashboard's Quick Scan).

Full Scan reads every module the gateway lists; the quick one reads only these workshop
addresses, chosen in Settings → General → Scan."""

KEY = "quick_scan:addresses"
DEFAULT = (0x01, 0x02, 0x03, 0x22, 0x44)  # engine, transmission, brakes, AWD, steering

# The common PQ35 modules, in address order (VCDS names; the app's own for its five families).
COMMON = (
    (0x01, "Engine"), (0x02, "Transmission"), (0x03, "ABS Brakes"), (0x08, "Auto HVAC"),
    (0x09, "Central Electronics"), (0x15, "Airbags"), (0x16, "Steering Wheel"),
    (0x17, "Instruments"), (0x19, "CAN Gateway"), (0x22, "AWD"), (0x25, "Immobilizer"),
    (0x42, "Door Elect. Driver"), (0x44, "Steering Assist"), (0x46, "Central Convenience"),
    (0x52, "Door Elect. Pass."), (0x55, "Headlight Range"), (0x56, "Radio"),
    (0x5F, "Information Electr."),
)


def addresses(settings):
    """The quick scan's workshop addresses (sorted ints)."""
    saved = settings.get(KEY) if settings else None
    if not isinstance(saved, list) or not saved:
        return sorted(DEFAULT)
    return sorted({int(a) for a in saved})


def set_addresses(settings, values):
    settings.set(KEY, sorted({int(a) for a in values}))


def choices(state):
    """(address, name) for the Settings list: the common modules plus any other module the last
    full scan found on this car."""
    names = dict(COMMON)
    scan = getattr(state, "last_full_scan", None) or {}
    for module in (scan.get("gateway") or {}).get("modules") or ():
        address = module.get("address")
        if address is not None and address not in names:
            names[address] = module.get("name") or f"Address {address:02X}"
    return sorted(names.items())


def describe(settings):
    """'Engine, Transmission, ABS Brakes, AWD, Steering Assist' for hints and tooltips."""
    names = dict(COMMON)
    return ", ".join(names.get(a, f"{a:02X}") for a in addresses(settings))
