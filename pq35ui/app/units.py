"""Display units: metric (what the modules report) or imperial.

Only what's shown changes. Logs are always written in the module's own units,
math-channel formulas always see those units, and alarm limits are stored in
them too (converted on the way in and out of the dialog)."""

KEY = "units"

# raw unit (lower-case) -> (imperial unit, factor, offset): shown = raw * factor + offset
IMPERIAL = {
    "°c": ("°F", 9 / 5, 32.0), "c": ("°F", 9 / 5, 32.0), "degc": ("°F", 9 / 5, 32.0),
    "mbar": ("psi", 0.0145038, 0.0), "hpa": ("psi", 0.0145038, 0.0),
    "kpa": ("psi", 0.145038, 0.0), "bar": ("psi", 14.5038, 0.0),
    "km/h": ("mph", 0.621371, 0.0), "kph": ("mph", 0.621371, 0.0),
    "km": ("mi", 0.621371, 0.0), "m": ("ft", 3.28084, 0.0), "mm": ("in", 1 / 25.4, 0.0),
    "nm": ("lb-ft", 0.737562, 0.0),
    "l": ("gal", 0.264172, 0.0), "l/h": ("gal/h", 0.264172, 0.0),
    "l/100km": None,  # not linear; left as is
    "kg/h": ("lb/h", 2.20462, 0.0), "g/s": ("lb/min", 0.132277, 0.0), "kg": ("lb", 2.20462, 0.0),
}

IDENTITY = (1.0, 0.0)


def system(settings) -> str:
    return (settings.get(KEY) or "metric") if settings else "metric"


def conversion(unit: str, settings):
    """(display unit, (factor, offset)) for a raw unit under the current setting."""
    if system(settings) != "imperial":
        return unit, IDENTITY
    entry = IMPERIAL.get((unit or "").strip().lower())
    if not entry:
        return unit, IDENTITY
    shown, factor, offset = entry
    return shown, (factor, offset)


def to_display(value, conv):
    if value is None:
        return None
    factor, offset = conv
    return value * factor + offset


def to_raw(value, conv):
    if value is None:
        return None
    factor, offset = conv
    return (value - offset) / factor


def limit_to_display(limit, conv):
    if not limit:
        return limit
    out = dict(limit)
    for side in ("min", "max"):
        out[side] = to_display(limit.get(side), conv)
    if conv[0] < 0:  # never happens with the table above, but keep min < max
        out["min"], out["max"] = out["max"], out["min"]
    return out


def limit_to_raw(limit, conv):
    if not limit:
        return limit
    out = dict(limit)
    for side in ("min", "max"):
        out[side] = to_raw(limit.get(side), conv)
    return out
