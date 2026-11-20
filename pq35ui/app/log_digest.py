"""What the AI reads about a datalog: a tuner's digest instead of a raw dump.

A raw sample of every Nth row misses the few seconds that matter, so this finds the
full-throttle pulls, bins them by rpm, runs a few objective checks (timing retard, boost vs
target, rail pressure vs target, lambda control, temperatures, voltage) and samples rows from
the pulls at a higher density than the cruising parts.

Channels are found by what they are (role patterns), not by exact name, so Standard (ECU
names) and Integrated Engineering logs line up, and several logs can be compared side by side
(compare()). Pure numpy: no Qt, so it can be tested on its own."""
import re

import numpy as np

# role: (include, exclude, several?)  first header matching include and not exclude wins
ROLES = (
    ("rpm", r"engine speed|\brpm\b", r"specified|idle", False),
    ("throttle", r"throttle", r"specified|request", False),
    ("pedal", r"accelerator|pedal", None, False),
    ("load", r"(engine )?load \(actual\)|actual load|^engine load$|^load\b", r"specified|request", False),
    ("load_spec", r"load \(specified\)|specified load|request(ed)? load", r"corrected", False),
    ("boost", r"boost pressure \(actual|actual boost|boost.*\bactual\b", None, False),
    ("boost_spec", r"boost pressure \(specified|requested boost|specified boost|boost.*specified", None, False),
    ("n75", r"n75|wastegate|boost control valve", None, False),
    ("timing", r"ignition timing|ignition angle|timing advance", r"retard|correct", False),
    ("retard", r"timing retard|ignition correction|knock retard|timing correction", None, True),
    ("knock_v", r"knock sensor", None, True),
    ("rail", r"rail pressure \(actual|rail actual|rail.*\bactual\b", None, False),
    ("rail_spec", r"rail pressure \(specified|rail spec|rail.*specified", None, False),
    ("lpfp", r"lpfp|low.pressure fuel", None, False),
    ("pump", r"fuel pump duty", None, False),
    ("lambda", r"lambda.*\(actual\)|lambda actual|actual lambda|\bafr\b", r"control|adapt", False),
    ("lambda_spec", r"lambda.*\(specified\)|lambda spec|specified lambda|requested lambda", r"control|adapt", False),
    ("lambda_ctrl", r"lambda control", r"actual|specified|bank 2", False),
    ("injection", r"injection (duration|time)", None, False),
    ("maf", r"mass air flow|\bmaf\b|air mass", None, False),
    ("torque", r"engine torque \(actual|engine moment|actual torque", None, False),
    ("iat", r"intake air temp|\biat\b|charge air temp", None, False),
    ("ect", r"coolant", None, False),
    ("voltage", r"terminal 30|battery|supply voltage|^voltage", r"knock", False),
    ("speed", r"vehicle speed|road speed", None, False),
    ("gear", r"\bgear\b", None, False),
)
# Shown in the rpm tables, in this order: (role, how a bin is summarised)
TABLE = (("load", "avg"), ("boost", "avg"), ("boost_spec", "avg"), ("n75", "avg"),
         ("timing", "avg"), ("retard", "worst"), ("rail", "avg"), ("rail_spec", "avg"),
         ("lambda", "avg"), ("lambda_spec", "avg"), ("lambda_ctrl", "avg"), ("maf", "avg"),
         ("torque", "avg"), ("iat", "avg"))
BIN = 500  # rpm


def roles(headers):
    """{role: header, or [headers] for per-cylinder ones} for the channels this log has."""
    out, used = {}, set()
    for role, include, exclude, several in ROLES:
        found = [h for h in headers if h not in used and re.search(include, h, re.I)
                 and not (exclude and re.search(exclude, h, re.I))]
        if not found:
            continue
        if several:
            out[role] = found[:8]
            used.update(found[:8])
        else:
            out[role] = found[0]
            used.add(found[0])
    return out


def _ffill(a):
    """Blank cells take the last value (IE logs write one group per row)."""
    a = np.asarray(a, dtype=float)
    if not np.isnan(a).any():
        return a
    idx = np.where(~np.isnan(a), np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    return a[idx]  # rows before the first value stay blank


def _g(n):
    return "" if n is None or not np.isfinite(n) else f"{n:.4g}"


class Log:
    """One log ready for digesting: forward-filled columns, roles and pulls."""

    def __init__(self, name, headers, times, columns, mask=None):
        self.name = name
        times = np.asarray(times, dtype=float)
        keep = np.ones(len(times), bool) if mask is None else np.asarray(mask, bool)
        self.times = times[keep]
        self.headers = list(headers)
        self.cols = {h: _ffill(np.asarray(columns[h], dtype=float)[keep]) for h in self.headers}
        self.roles = roles(self.headers)
        self.pulls = self._find_pulls()

    # -- helpers --
    def col(self, role):
        h = self.roles.get(role)
        if h is None:
            return None
        if isinstance(h, list):
            return np.vstack([self.cols[x] for x in h])
        return self.cols[h]

    def worst_retard(self):
        """Per row, the cylinder with the most retard (by size, keeping the log's sign)."""
        r = self.col("retard")
        if r is None:
            return None
        safe = np.where(np.isnan(r), 0, r)
        pick = np.abs(safe).argmax(axis=0)
        out = r[pick, np.arange(r.shape[1])]
        return out

    def label(self, role):
        h = self.roles.get(role)
        if isinstance(h, list):
            return f"{role} (worst of {len(h)} cylinders: {h[0]} …)"
        return h

    @property
    def duration(self):
        return float(self.times[-1] - self.times[0]) if len(self.times) > 1 else 0.0

    # -- pulls --
    def _wot(self):
        for role in ("pedal", "throttle"):
            c = self.col(role)
            if c is not None and np.nanmax(c) >= 60:
                return c >= 80, f"{self.roles[role]} ≥ 80"
        load = self.col("load")
        if load is not None and np.isfinite(load).any() and np.nanmax(load) > 0:
            lim = 0.8 * np.nanmax(load)
            return load >= lim, f"{self.roles['load']} ≥ {lim:.0f} (no throttle or pedal channel)"
        return None, ""

    def _find_pulls(self):
        """[(i0, i1)] row ranges at full throttle for 1.5 s+ with rpm climbing 800+."""
        self.wot_rule = ""
        if len(self.times) < 3:
            return []
        wot, self.wot_rule = self._wot()
        if wot is None:
            return []
        rpm = self.col("rpm")
        t = self.times
        runs, start = [], None
        for i, on in enumerate(wot):
            if on and start is None:
                start = i
            elif not on and start is not None:
                runs.append([start, i - 1])
                start = None
        if start is not None:
            runs.append([start, len(wot) - 1])
        merged = []
        for r in runs:  # a dip of under 0.6 s (a shift, a lift) keeps it one pull
            if merged and t[r[0]] - t[merged[-1][1]] < 0.6:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        if rpm is not None:  # a DSG upshift keeps the throttle open: split where rpm falls back
            split = []
            for i0, i1 in merged:
                start, peak = i0, i0
                for i in range(i0 + 1, i1 + 1):
                    if not np.isfinite(rpm[i]):
                        continue
                    if not np.isfinite(rpm[peak]) or rpm[i] >= rpm[peak]:
                        peak = i
                    elif rpm[peak] - rpm[i] > 400:
                        split.append([start, peak])
                        start = peak = i
                split.append([start, i1])
            merged = split
        out = []
        for i0, i1 in merged:
            if t[i1] - t[i0] < 1.5:
                continue
            if rpm is not None:
                seg = rpm[i0:i1 + 1]
                if not np.isfinite(seg).any() or np.nanmax(seg) - np.nanmin(seg[:max(1, len(seg) // 3)]) < 800:
                    continue
            out.append((i0, i1))
        return out

    def pull_mask(self, settle=0.0):
        """Rows in a pull; settle: leave out each pull's first seconds (the turbo spooling)."""
        m = np.zeros(len(self.times), bool)
        for i0, i1 in self.pulls:
            m[i0:i1 + 1] = self.times[i0:i1 + 1] >= self.times[i0] + settle
        return m

    # -- tables --
    def bins(self, mask):
        """{rpm bin start: {role: value}} over the rows in mask (needs an rpm channel)."""
        rpm = self.col("rpm")
        if rpm is None or not mask.any():
            return {}
        worst = self.worst_retard()
        out = {}
        b = np.floor(rpm / BIN) * BIN
        for start in np.unique(b[mask & np.isfinite(b)]):
            sel = mask & (b == start)
            row = {"n": int(sel.sum())}
            for role, how in TABLE:
                c = worst if role == "retard" else self.col(role)
                if c is None:
                    continue
                v = c[sel]
                v = v[np.isfinite(v)]
                if len(v):
                    row[role] = float(v[np.abs(v).argmax()]) if how == "worst" else float(v.mean())
            out[int(start)] = row
        return out


# ── checks: objective numbers for the AI to judge (no verdicts here) ──

def _at(log, i):
    rpm = log.col("rpm")
    where = f"t={log.times[i]:.1f} s"
    if rpm is not None and np.isfinite(rpm[i]):
        where += f", {rpm[i]:.0f} rpm"
    return where


def checks(log, mask):
    """Plain facts from the rows in mask: [str]."""
    out = []
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return out
    # Actual vs target leaves out each pull's first second: boost is always short while spooling.
    settled = mask & log.pull_mask(settle=1.0) if log.pulls and (mask & log.pull_mask(1.0)).any() else mask
    sidx = np.nonzero(settled)[0]
    r = log.col("retard")
    if r is not None:
        parts = []
        for name, row in zip(log.roles["retard"], r):
            v = row[idx]
            ok = np.isfinite(v)
            if ok.any():
                j = idx[ok][np.abs(v[ok]).argmax()]
                parts.append(f"{name}: worst {row[j]:.2f} at {_at(log, j)}; "
                             f"rows with ≥2° {int((np.abs(v[ok]) >= 2).sum())}/{int(ok.sum())}")
        if parts:
            out.append("Timing retard per cylinder (size matters, the sign depends on the logger):\n  - "
                       + "\n  - ".join(parts))
    for act, spec, what, unit_hint in (("boost", "boost_spec", "Boost", ""),
                                       ("rail", "rail_spec", "Fuel rail pressure", "")):
        a, s = log.col(act), log.col(spec)
        if a is None or s is None:
            continue
        d = (a - s)[sidx]
        ok = np.isfinite(d) & (s[sidx] > 0)
        if not ok.any():
            continue
        dd, ii = d[ok], sidx[ok]
        lo, hi = ii[dd.argmin()], ii[dd.argmax()]
        pct = 100 * (a[lo] - s[lo]) / s[lo] if s[lo] else 0
        out.append(f"{what} actual − specified ({log.roles[act]} vs {log.roles[spec]}): mean {dd.mean():+.4g}, "
                   f"furthest below target {a[lo] - s[lo]:+.4g} ({pct:+.0f}%) at {_at(log, lo)}, "
                   f"furthest above {a[hi] - s[hi]:+.4g} at {_at(log, hi)}"
                   + (" (first second of each pull left out: spool-up)" if settled is not mask else ""))
    for role, what in (("lambda_ctrl", "Lambda control"), ("lambda", "Lambda actual"), ("n75", "N75 duty"),
                       ("load", "Load actual"), ("timing", "Ignition timing"), ("lpfp", "LPFP"),
                       ("pump", "Fuel pump duty"), ("maf", "Mass air flow"), ("torque", "Torque")):
        c = log.col(role)
        if c is None:
            continue
        v = c[idx]
        ok = np.isfinite(v)
        if ok.any():
            ii = idx[ok]
            out.append(f"{what} ({log.roles[role]}): min {np.nanmin(v):.4g} at {_at(log, ii[v[ok].argmin()])}, "
                       f"max {np.nanmax(v):.4g} at {_at(log, ii[v[ok].argmax()])}, avg {np.nanmean(v):.4g}")
    for role, what in (("iat", "Intake air temperature"), ("ect", "Coolant temperature")):
        c = log.col(role)
        if c is None:
            continue
        v = c[idx]
        if np.isfinite(v).any():
            line = f"{what} ({log.roles[role]}): {np.nanmin(v):.4g} to {np.nanmax(v):.4g}"
            if log.pulls and role == "iat":
                rises = [f"{c[i0]:.4g}→{c[i1]:.4g}" for i0, i1 in log.pulls if np.isfinite(c[i0]) and np.isfinite(c[i1])]
                if rises:
                    line += "; start→end of each pull: " + ", ".join(rises)
            out.append(line)
    v = log.col("voltage")
    if v is not None and np.isfinite(v[idx]).any():
        ii = idx[np.isfinite(v[idx])]
        out.append(f"Supply voltage ({log.roles['voltage']}): min {np.nanmin(v[idx]):.4g} at "
                   f"{_at(log, ii[v[ii].argmin()])}, max {np.nanmax(v[idx]):.4g}")
    return out


def _table(rows, cols, title):
    """A small CSV block: rows = [(label, [values])]."""
    lines = [title, ",".join(cols)]
    lines += [",".join([str(label)] + [_g(v) for v in vals]) for label, vals in rows]
    return "\n".join(lines)


def _pull_lines(log):
    rpm, gear, speed = log.col("rpm"), log.col("gear"), log.col("speed")
    out = []
    for n, (i0, i1) in enumerate(log.pulls, 1):
        s = f"Pull {n}: t={log.times[i0]:.1f}–{log.times[i1]:.1f} s ({log.times[i1] - log.times[i0]:.1f} s)"
        if rpm is not None:
            seg = rpm[i0:i1 + 1]
            s += f", {np.nanmin(seg):.0f}→{np.nanmax(seg):.0f} rpm"
        if gear is not None and np.isfinite(gear[i0:i1 + 1]).any():
            g = np.unique(gear[i0:i1 + 1][np.isfinite(gear[i0:i1 + 1])]).astype(int)
            s += f", gear {'/'.join(map(str, g[:4]))}"
        if speed is not None and np.isfinite(speed[i0:i1 + 1]).any():
            s += f", {np.nanmin(speed[i0:i1 + 1]):.0f}→{np.nanmax(speed[i0:i1 + 1]):.0f} {_unit(log.roles['speed'])}"
        out.append(s)
    return out


def _unit(header):
    m = re.search(r"\(([^()]*)\)\s*$", header or "")
    return m.group(1) if m else ""


def _header_lines(log):
    rate = len(log.times) / max(log.duration, 1e-3)
    out = [f"Log: {log.name}", f"Duration {log.duration:.1f} s, {len(log.times)} rows (about {rate:.1f} rows/s)."]
    if log.pulls:
        out.append(f"Full-throttle pulls found ({log.wot_rule}, 1.5 s+, rpm climbing):")
        out += ["- " + p for p in _pull_lines(log)]
    else:
        out.append("No full-throttle pull found" + (f" ({log.wot_rule})." if log.wot_rule else
                                                     " (no throttle, pedal or load channel)."))
    return out


def digest(name, headers, times, columns, mask=None, focus=(), max_rows=120, max_cols=16):
    """The whole digest of one log as text. mask: rows to use (a selection); focus: channels the
    user has on the graph (they go first in the sampled rows)."""
    log = Log(name, headers, times, columns, mask)
    if not len(log.times):
        return "The selected range is empty."
    lines = _header_lines(log)
    pm = log.pull_mask()
    where = pm if pm.any() else np.ones(len(log.times), bool)
    scope = "during the pulls" if pm.any() else "over the whole log"
    lines.append(f"\n## Checks {scope}")
    lines += ["- " + c for c in checks(log, where)] or ["- (no channels for the checks)"]
    b = log.bins(where)
    if b:
        present = [(role, how) for role, how in TABLE if any(role in r for r in b.values())]
        cols = ["rpm", "rows"] + [log.label(role) + (" [worst]" if how == "worst" else "") for role, how in present]
        rows = [(f"{k}-{k + BIN - 1}", [r["n"]] + [r.get(role) for role, _h in present]) for k, r in sorted(b.items())]
        lines.append("")
        lines.append(_table(rows, cols, f"## By rpm, {scope} (average per {BIN} rpm; retard = worst cylinder)"))
    lines.append("\n## Every channel over the whole log (min / max / avg)")
    for h in log.headers:
        v = log.cols[h]
        v = v[np.isfinite(v)]
        if len(v):
            lines.append(f"- {h}: {v.min():.4g} / {v.max():.4g} / {v.mean():.4g}")
    # Sampled rows: dense in the pulls, sparse elsewhere.
    key = [log.roles[r] for r, *_ in ROLES if isinstance(log.roles.get(r), str)]
    retard = log.roles.get("retard") or []
    names = list(dict.fromkeys([f for f in focus if f in log.cols] + key[:1] + retard[:4] + key[1:]))[:max_cols]
    n = len(log.times)
    if pm.any():
        inside, outside = np.nonzero(pm)[0], np.nonzero(~pm)[0]
        take_in = inside[np.linspace(0, len(inside) - 1, min(len(inside), int(max_rows * 0.75))).astype(int)]
        k = min(len(outside), max_rows - len(take_in))
        take_out = outside[np.linspace(0, len(outside) - 1, k).astype(int)] if k > 0 else np.array([], int)
        idx = np.unique(np.concatenate([take_in, take_out]))
        how = f"{len(take_in)} of the {len(inside)} pull rows and {len(take_out)} of the other {len(outside)}"
    else:
        idx = np.unique(np.linspace(0, n - 1, min(n, max_rows)).astype(int))
        how = f"{len(idx)} of {n} rows, evenly spaced"
    lines.append(f"\n## Sampled rows ({how}; P = in a pull), CSV")
    lines.append(",".join(["Time (s)", "P"] + names))
    for i in idx:
        lines.append(",".join([f"{log.times[i]:.2f}", "P" if pm[i] else ""] + [_g(log.cols[h][i]) for h in names]))
    return "\n".join(lines)


def compare(logs, pulls_only=True):
    """Side-by-side digest of several logs. logs: [(name, headers, times, columns)], first =
    baseline. Channels are matched by role, so differently named logs line up."""
    items = [Log(*l) for l in logs]
    letters = [chr(ord("A") + i) if i < 26 else f"L{i + 1}" for i in range(len(items))]
    lines = [f"{len(items)} logs, labelled {', '.join(letters)} (A is the first, usually the baseline).", ""]
    masks = []
    for letter, log in zip(letters, items):
        pm = log.pull_mask()
        use = pm if pulls_only and pm.any() else np.ones(len(log.times), bool)
        masks.append(use)
        scope = "pulls only" if pulls_only and pm.any() else "whole log"
        lines.append(f"### {letter}: " + "\n".join(_header_lines(log)) + f"\nCompared over: {scope}.")
    # Which channel each log uses for each role, when the names differ.
    lines.append("\n## Channels matched across the logs")
    for role, _inc, _exc, _sev in ROLES:
        names = [log.roles.get(role) for log in items]
        if sum(n is not None for n in names) < 2:
            continue
        shown = {str(n) for n in names if n is not None}
        label = names[next(i for i, n in enumerate(names) if n is not None)]
        label = f"{label[0]} … ({len(label)} cylinders)" if isinstance(label, list) else label
        if len(shown) == 1:
            lines.append(f"- {role}: {label}")
        else:
            lines.append(f"- {role}: " + "; ".join(f"{l}={n if not isinstance(n, list) else n[0] + ' …'}"
                                                   for l, n in zip(letters, names) if n is not None))
    # Per-role tables by rpm, one column per log.
    all_bins = [log.bins(m) for log, m in zip(items, masks)]
    keys = sorted({k for b in all_bins for k in b})
    if keys:
        lines.append(f"\n## By rpm, side by side (average per {BIN} rpm; retard = worst cylinder; blank = no data)")
        for role, how in TABLE:
            if sum(any(role in r for r in b.values()) for b in all_bins) < 1:
                continue
            first = next(log for log in items if log.roles.get(role))
            title = f"### {first.label(role)}" + (" [worst]" if how == "worst" else "")
            rows = [(f"{k}-{k + BIN - 1}", [b.get(k, {}).get(role) for b in all_bins]) for k in keys]
            rows = [r for r in rows if any(v is not None for v in r[1])]
            lines.append(_table(rows, ["rpm"] + letters, title))
        lines.append(_table([(f"{k}-{k + BIN - 1}", [b.get(k, {}).get("n") for b in all_bins]) for k in keys],
                            ["rpm"] + letters, "### Rows per bin (how much data each average rests on)"))
    else:
        lines.append("\n(No engine speed channel in common, so no rpm tables.)")
    for letter, log, m in zip(letters, items, masks):
        lines.append(f"\n## Checks for {letter} ({log.name})")
        lines += ["- " + c for c in checks(log, m)] or ["- (no channels for the checks)"]
    return "\n".join(lines)


def summary(name, headers, times, columns):
    """One line for a list: '0:42 · 2 pulls · 2400–6500 rpm'."""
    log = Log(name, headers, times, columns)
    parts = [f"{int(log.duration // 60)}:{int(log.duration % 60):02d}"]
    parts.append(f"{len(log.pulls)} pull{'s' if len(log.pulls) != 1 else ''}" if log.pulls else "no WOT pull")
    rpm = log.col("rpm")
    if rpm is not None and log.pulls:
        seg = rpm[log.pull_mask()]
        parts.append(f"{np.nanmin(seg):.0f}–{np.nanmax(seg):.0f} rpm")
    return " · ".join(parts)


ANALYSIS_SYSTEM = (
    "You are reviewing datalogs as an experienced VW/Audi 2.0 TFSI / 1.8T tuner would. The app "
    "sends a digest, not the raw file: the full-throttle pulls it found, objective checks, tables "
    "by rpm, per-channel min/max/avg and sampled rows (P = in a pull).\n"
    "How to answer:\n"
    "- Start with a one-line verdict.\n"
    "- Then findings, most serious first. Quote the numbers, times and rpm from the data for each. "
    "Say what's normal as well as what's not, briefly.\n"
    "- When comparing logs, give a short table of the differences that matter (by rpm where it "
    "helps), and say which run is better and which is safer, and why.\n"
    "- Finish with what to check or change next, and what to log to confirm it.\n"
    "Rules: use only numbers in the data; never invent values. Say when a conclusion rests on few "
    "rows or a slow log rate (under ~5 rows/s hides short events). Boost may be absolute (about "
    "1000 mbar at rest) or relative: check the idle values. Timing retard is about size: the sign "
    "depends on the logger. Integrated Engineering logs can carry wrong unit labels: trust the "
    "channel's name and its values over the unit in brackets. Differences in conditions (intake "
    "temperature, gear, rpm range, pull length) can explain differences between runs: point them out."
)
