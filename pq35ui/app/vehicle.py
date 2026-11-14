"""The car: its VIN (read from the modules, or found in a log's file name)
and what that VIN says it is (make, model, year, plant)."""
import re
import time

# 17 characters, no I, O or Q.
VIN_CHARS = "A-HJ-NPR-Z0-9"
VIN_RE = re.compile(rf"(?<![A-Z0-9])([{VIN_CHARS}]{{17}})(?![A-Z0-9])")

WMI = {
    "WVW": "Volkswagen", "WVG": "Volkswagen", "WV1": "Volkswagen Commercial", "WV2": "Volkswagen Commercial",
    "3VW": "Volkswagen (Mexico)", "1VW": "Volkswagen (USA)", "9BW": "Volkswagen (Brazil)",
    "WAU": "Audi", "WA1": "Audi", "TRU": "Audi (Hungary)", "WUA": "Audi Sport",
    "VSS": "SEAT", "TMB": "Škoda",
}
# VIN characters 7-8: the model code on VAG cars.
MODELS = {
    "1K": "Golf / Jetta Mk5-6 (1K)", "5K": "Golf Mk6 (5K)", "AJ": "Golf Mk6 (5K)", "1T": "Touran (1T)",
    "5M": "Golf Plus (5M)", "1F": "Eos (1F)", "5N": "Tiguan (5N)", "5C": "Beetle (5C)",
    "13": "Scirocco (13)", "1Q": "Eos (1Q)", "8P": "A3 (8P)", "FM": "A3 (8P)", "FH": "A3 Cabriolet (8P)",
    "8J": "TT (8J)", "1P": "Leon (1P)", "5P": "Altea (5P)", "1Z": "Octavia (1Z)", "5L": "Yeti (5L)",
    "3T": "Superb (3T)", "3C": "Passat B6/B7 (3C)", "35": "Passat CC (35)",
}
PLANTS = {
    "A": "Ingolstadt", "N": "Neckarsulm", "1": "Győr", "W": "Wolfsburg", "M": "Puebla",
    "K": "Osnabrück", "V": "Palmela", "B": "Brussels", "X": "Poznań", "P": "Mosel",
}
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 30-year cycle starting at 1980 / 2010


def find_vin(text: str):
    """The first VIN-shaped run of characters in text (e.g. an Integrated Engineering log's file name), or None."""
    match = VIN_RE.search((text or "").upper())
    return match.group(1) if match else None


def from_response(data: bytes):
    """A VIN from a module's reply (KWP 1A 90 or UDS 22 F190), or None."""
    return find_vin(bytes(data or b"").decode("latin-1", "replace"))


def model_year(vin: str):
    code = vin[9]
    if code not in _YEAR_CODES:
        return None
    offset = _YEAR_CODES.index(code)
    latest = time.localtime().tm_year + 1
    year = 2010 + offset
    while year > latest:
        year -= 30
    return year


def decode(vin: str) -> dict:
    """What the VIN says: {"vin", "make", "model", "year", "plant", "label"}."""
    vin = (vin or "").strip().upper()
    out = {"vin": vin, "make": WMI.get(vin[:3], ""), "model": MODELS.get(vin[6:8], ""),
           "year": model_year(vin) if len(vin) == 17 else None, "plant": PLANTS.get(vin[10:11], "")}
    make = out["make"].split(" (")[0]
    parts = [str(out["year"]) if out["year"] else "", make, out["model"]]
    out["label"] = " ".join(p for p in parts if p) or "Unknown car"
    return out


def ie_log_name(vin=None, when=None) -> str:
    """Integrated Engineering's file name: Datalog-MM-DD-YYYY_HH-MM_<VIN>.csv"""
    stamp = time.strftime("%m-%d-%Y_%H-%M", time.localtime(when) if when else time.localtime())
    return f"Datalog-{stamp}_{vin}.csv" if vin else f"Datalog-{stamp}.csv"
