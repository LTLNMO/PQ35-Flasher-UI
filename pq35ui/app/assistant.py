"""The in-app Assistant: a chat with the user's chosen AI that knows how
PQ35 Flasher works (the Help text) and what's on screen right now."""
import re

from . import ai
from .help_page import TOPICS


def _plain(html):
    text = re.sub(r"<(br|/p|/li|/h3|/tr)>", "\n", html)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def manual():
    return "\n\n".join(f"### {title}\n{_plain(body)}" for _key, title, body in TOPICS)


def app_context(window):
    s = window.state
    current = next((k for k, b in window.tab_buttons.items() if b.property("active") == "true"), "?")
    lines = [f"Tab the user is on: {current}",
             f"Interface: {(s.adapter or {}).get('label') or (s.adapter or {}).get('adapter') or 'none chosen'}",
             f"Module: {(s.family or {}).get('label', 'none selected')}"]
    if s.identity:
        lines.append(f"Identified: part {s.part()}, software {s.identity.get('sw_version', '?')}")
    if s.record:
        lines.append("Backup of this module exists: " + ("yes" if s.record.has_backup() else "no"))
    lines.append("A job is running right now" if s.busy else "Nothing is running")
    lines.append("Demo mode is on (pretend modules)" if getattr(window, "demo", False) else "Real hardware mode")
    disabled = [k for k, b in window.tab_buttons.items() if b.property("locked")]
    if disabled:
        lines.append("Tabs locked right now (need a module): " + ", ".join(disabled))
    lines.append(fault_code_context(window))
    lines.append(page_context(window, current))
    return "\n".join(lines)


def current_page_request(window, key=None):
    """The current page's data (title, context, question, extra) or None."""
    key = key or next((k for k, b in window.tab_buttons.items() if b.property("active") == "true"), None)
    page = window.pages.get(key) if key else None
    fn = getattr(page, "ai_context", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # never let a page's summary break the chat
        return None


def page_context(window, key):
    """What the page the user is on shows (log, live values, map, flash plan), so "analyze this",
    "what do you think?" or "is this OK?" need no copying."""
    req = current_page_request(window, key)
    if not req:
        return ""
    out = [f"\n## On the page right now: {req['title']}", req["context"],
           "\nWhen the user asks for help with what they're looking at (\"analyze this\", \"what do you "
           "think?\", \"anything wrong?\") or asks about these values, use the data above. The usual request "
           f"here is: {req['question']}"]
    if req.get("extra"):
        out.append(req["extra"])
    return "\n".join(out)


def _current_key(window):
    return next((k for k, b in window.tab_buttons.items() if b.property("active") == "true"), None)


def _has(names, *words):
    """Any channel name containing all of these words (lower-case)?"""
    return any(all(w in n for w in words) for n in names)


def _module(window):
    return (window.state.family or {}).get("label", "").split(" (")[0] or "this module"


def _analyze_suggestions(window, page, has):
    if not has:
        return [("What to look for in a log", "When I review a datalog from this car, what should I look at "
                 "first (boost vs target, timing/knock, fuel pressure, lambda, temperatures) and what are "
                 "warning signs?"),
                ("Record a good pull", "How do I record a good WOT pull for analysing: which values, rate, "
                 "gear and rpm range, and how to do it safely?"),
                ("Compare two runs", "How do I compare two datalogs in Analyze and line them up?"),
                ("Before / after a tune", "I want to check a tune change. How should I log and compare runs "
                 "before and after so the comparison is fair?")]
    names = [h.lower() for h in page.headers]
    out = []
    if page.compare is not None:
        out += [("What changed between runs", "Compare the two runs: what changed (boost, timing, fuelling, "
                 "temperatures), where in the rev range, and is the second one better or worse? Quote the "
                 "biggest differences with times."),
                ("Which run is safer", "Which of these two runs is safer for the engine and why? Point to knock, "
                 "timing pulled, lambda and temperatures.")]
    if page.stats_mode.currentData() == "sel":
        out.append(("Explain the selection", "Focus only on the selected range (Min/Max/Avg are for it): "
                    "what is the car doing there and is anything wrong?"))
    out.append(("Review this log", "Review this datalog like a tuner would: summarise the run (rpm range, "
                "load, how long), then list anything wrong or worth watching with the time it happens, "
                "and finish with what to check or change next."))
    if _has(names, "boost") and _has(names, "specified"):
        out.append(("Boost vs target", "Compare boost actual against specified through the run: does it reach "
                    "target, overshoot or fall away at high rpm? Give the worst gaps with times and the likely "
                    "causes (N75, leaks, wastegate)."))
    if _has(names, "retard") or _has(names, "knock"):
        out.append(("Knock / timing pulled", "Look at timing retard and knock per cylinder: is timing being "
                    "pulled, on which cylinders, at what rpm/load and time? How serious is it?"))
    if _has(names, "rail"):
        out.append(("Fuel pressure", "Does fuel rail pressure (actual) keep up with specified, especially under "
                    "load? Any drop that points at the HPFP or LPFP?"))
    if _has(names, "lambda"):
        out.append(("Fuelling", "How does fuelling look from the lambda values: lean or rich under load, "
                    "and is the lambda control within a sensible range?"))
    out.append(("What to change next", "Based on this log, what would you change or check next, in order of "
                "importance, and what should I log to confirm it?"))
    if _has(names, "intake air") or _has(names, "coolant"):
        out.append(("Temperatures", "Are intake air and coolant temperatures OK through the run? Any heat soak?"))
    if not page.compare:
        out.append(("What's missing from this log?", "Which values should I add next time to judge this run "
                    "better?"))
    return out


def _liveview_suggestions(window, page, has):
    mod = _module(window)
    if not has:
        return [("Quick health check" + (f" ({mod})" if window.state.family else ""), f"Which values on the {mod} should I watch live for a quick "
                 "health check, and what's normal at idle and when driving? Name them as in the channel list."),
                ("Hunt a boost leak", "Which live values show a boost leak or a boost control problem, and what "
                 "pattern should I look for while driving?"),
                ("Safety alarms", "Which alarms should I set in Live View to protect the engine (with limits), "
                 "and how do I set them?"),
                ("Build a dashboard", "How do I set up a Live View dashboard with gauges for driving?")]
    out = []
    for key in getattr(page, "alarm_active", ()) or ():
        ch = page.channel_data.get(key)
        if ch:
            out.append((f"Why is {ch['name'].split(' (')[0]} alarming?",
                        f"The alarm on {ch['name']} has tripped. What does that value mean, is this reading "
                        "dangerous, and what should I do or check right now?"))
    out += [("Is this normal?", "Look at the current live values: is anything outside the normal range for "
             "this module at these conditions? Say if the car seems idle or driving."),
            ("Anything to worry about?", "From these values and their recent min/max, is there anything that "
             "could hurt the engine or point to a fault?"),
            ("What do these values mean?", "Explain each of the values on screen in plain words and what a "
             "healthy reading looks like."),
            ("What to watch next", "Given these values, what should I add to the graphs or keep an eye on, "
             "and what would be a warning sign?")]
    return out


def _logger_suggestions(window, page, has):
    mod = _module(window)
    ticked = [v.get("name", "") for v in page._checked_values()] if hasattr(page, "_checked_values") else []
    if page.running or has:
        return [("Anything off so far?", "Look at the values logged so far (now, min, max, avg): anything off "
                 "for this module? What should I look at in Analyze afterwards?"),
                ("Suggest a trigger", "Suggest a good trigger condition for capturing pulls with these values "
                 "(value, > or <, limit, seconds before/after)."),
                ("When should I stop?", "How long or how many pulls should I log for a useful datalog?")]
    if ticked:
        listed = ", ".join(ticked[:40])
        return [("Check my channel choice", f"I've ticked these values to log: {listed}. Is this a good set for "
                 "checking boost, timing and fuelling on a WOT pull? What's missing or not needed?"),
                ("Make it log faster", f"I'm logging: {listed}. How can I get a faster rate (fewer measuring "
                 "groups, Group rates) without losing what matters?"),
                ("Set up a trigger", "Which trigger condition should I use to catch WOT pulls with these values, "
                 "and how many seconds before and after?"),
                ("Which recording mode?", "Should I use continuous, triggered or loop recording for this, and "
                 "with what settings?")]
    return [("Log a WOT pull", f"Set me up to log a WOT pull on the {mod}: which values to tick (names as in "
             "the channel list), rate, and recording mode."),
            ("Health check log", f"Which values should I log on the {mod} for a general health check drive?"),
            ("Diagnose a problem", f"I have a driveability problem (hesitation, misfire or low power). What "
             f"should I log on the {mod} to find the cause?"),
            ("Which recording mode?", "When should I use continuous, triggered or loop recording?")]


def _maps_suggestions(window, page, has):
    tool = page.content_stack.currentWidget() if hasattr(page, "content_stack") else None
    if tool is getattr(page, "compare", None):
        if not has:
            return [("Compare two files", "How do I compare two ECU files here and find what changed?"),
                    ("Stock vs tuned", "I have a stock and a tuned file. How do I find which maps the tune changed?")]
        return [("Which changes are maps?", "Which of these changed ranges look like maps (tables of values) "
                 "rather than checksums, code or ID strings? Which should I open in the map viewer first?"),
                ("What kind of tune is this?", "From where and how much these files differ, what kind of tune "
                 "or change does it look like?"),
                ("Is there a checksum change?", "Do any of these changed ranges look like checksums, and does the "
                 "app handle checksums when I flash?")]
    if not has:
        return [("Find a map", "How do I find and show a map in an ECU file with the map viewer: address, size, "
                 "data type and scaling?"),
                ("Find the timing map", "How would I find the main ignition timing map in a file like this?"),
                ("Read a map definition", "I have a map definition (address, size, scaling) from somewhere else. "
                 "How do I enter it here?")]
    name = (page.viewer.definition().get("name") or "").strip()
    if getattr(page.viewer, "other", None) is not None:
        return [("What changed in this map?", "What changed in this map between the two files, where (which "
                 "rpm/load area), and what would it do to how the car drives? Anything risky?"),
                ("Is the change safe?", "Is the change in this map within safe limits for this engine?"),
                ("Explain the axes", "What are this map's axes likely to be (rpm, load, …) and in which area "
                 "is the change?")]
    return [(f"What does {name} do?" if name and len(name) < 32 else "What does this map do?",
             "What does this map likely control, given its axes, values and units, and how would changing it "
             "affect the car?"),
            ("Does it look right?", "Do these values look like a real map (smooth, sensible range), or is the "
             "address, size, data type or scaling probably wrong?"),
            ("Explain the axes", "What are this map's axes likely to be (rpm, load, …), and what do rows and "
             "columns mean?"),
            ("Is it safe to change?", "If I change this map, what are the risks and what should I log to check it?")]


def _flash_suggestions(window, page, has):
    mod = _module(window)
    record = window.state.record
    backed_up = bool(record and record.has_backup())
    if not has:
        out = [("Walk me through flashing", f"Walk me through flashing the {mod} safely with PQ35 Flasher, "
                "from backup to verify."),
               ("Which file should I use?", f"Which image file should I flash on the {mod}, and how do I know it "
                "matches my part number?"),
               ("What if it fails?", "What happens if a flash fails or the power drops halfway, and how does "
                "recovery work?")]
        if not backed_up:
            out.insert(0, ("Back up first?", f"I don't have a backup of the {mod} yet. Why do I need one and "
                           "how do I make it?"))
        return out
    out = []
    for check in (page.inspection or {}).get("checks") or []:
        if str(check.get("status", "")).lower() not in ("ok", "pass", "passed", "good"):
            out.append((f"Why does “{check.get('key')}” fail?", f"The image check “{check.get('key')}” says: "
                        f"{check.get('status', '')} {check.get('detail', '')}. What does that mean and how do I fix it?"))
    if not backed_up:
        out.append(("Back up first?", f"I don't have a backup of the {mod} yet. Why do I need one and how do I "
                    "make it before flashing?"))
    out += [("Is this file right?", "Does this image match my module (part number, software version), and is "
             "it the kind of file this module expects?"),
            ("Second opinion", "Review this flash before I do it: does the image match the module, is the write "
             "plan sensible, and is there anything risky I should fix first?"),
            ("What if it fails?", "What happens if this flash fails partway, and how would I recover?")]
    return out


def _fault_suggestions(window):
    health = getattr(window.state, "faults", None)
    rows = getattr(health, "rows", {}) or {}
    codes = [(row["label"], c, c["code"] in set(row["active"])) for row in rows.values() for c in row["codes"]]
    if not codes:
        return [("Read my fault codes", "How do I read the fault codes of every module, and what do I do with "
                 "the results?"),
                ("Car health check", "How do I do a full health check of my car with PQ35 Flasher (codes, live "
                 "values, a log)?"),
                ("Where do I start?", "I'm new to PQ35 Flasher. What should I do first with my car?")]
    out = []
    selected = health.selected_code() if health is not None else None
    if selected:
        out.append((f"Explain {selected.split(' ')[0]}", f"Explain fault code {selected}: what it means, common "
                    "causes on this car, how serious it is, and what to check first."))
    for label, c, active in codes:
        if active and len(out) < 2:
            out.append((f"Why does {c['code']} come back?", f"{c['code']} ({c['text']}) on the {label} comes "
                        "straight back after clearing. What's likely still wrong and how do I find it?"))
    out += [("Explain my fault codes", "Explain all the fault codes the app has read: what each means, how "
             "serious it is, and which to deal with first."),
            ("What should I fix first?", "Rank these fault codes by how urgent they are and tell me what to check "
             "first for each.")]
    if len({label for label, _c, _a in codes}) > 1:
        out.append(("Are they related?", "Could these codes on different modules have one common cause "
                    "(e.g. low voltage, a wiring or CAN problem)?"))
    out.append(("Safe to clear?", "Is it safe to clear these codes now, and which will come back if the "
                "problem is still there?"))
    return out


def _home_suggestions(window):
    out = []
    if getattr(window, "fault_count", 0):
        out += _fault_suggestions(window)[:3]
    out += [("Health check of my car", "Give me a health summary of my car from what the app knows: modules "
             "found, fault codes, backups and flashes. What needs attention?"),
            ("What should I do next?", "Based on what the app shows (modules, backups, fault codes), what should "
             "I do next, in order?"),
            ("Plan a tune safely", "I want to tune my car. What's the safe order of steps in PQ35 Flasher "
             "(backup, logging before and after, flashing, checking)?")]
    return out


def _connect_suggestions(window, page):
    results = getattr(page, "scan_results", {}) or {}
    if not results:
        return [("Help me connect", "Walk me through connecting: which interface to pick, ignition and engine "
                 "state, and what a scan does."),
                ("Which interface?", "Which CAN interface should I pick and how do I set it up?"),
                ("Bench setup", "How do I connect to a module on the bench instead of in the car?"),
                ("Is scanning safe?", "Does a scan change anything on the car?")]
    statuses = [e.get("status") for e in results.values()]
    if all(st == "absent" for st in statuses):
        return [("Nothing answered: why?", "The scan found nothing. What are the usual reasons (ignition, "
                 "cable, interface, bench power) and how do I check each?"),
                ("Check my interface", "How can I tell whether my CAN interface is working and wired right?")]
    out = []
    for name, e in results.items():
        if e.get("status") in ("bootloader", "responding"):
            out.append((f"Recover the {name}", f"The {name} module is waiting in its loader. What happened and "
                        "how do I recover it safely?"))
    out += [("Which module first?", "Which of the modules found should I work on first, and what can I do with "
             "each in PQ35 Flasher?"),
            (f"What can I do with the {_module(window)}?" if window.state.family else "What can I do with each?", f"What can PQ35 Flasher do with the {_module(window)}: "
             "back up, flash, patches, logging, diagnostics?"),
            ("A module is missing", "A module I expected wasn't found. Why might that be?")]
    return out


GENERAL = [("What can this app do?", "What can PQ35 Flasher do, and where do I start?"),
           ("Where do I start?", "I'm new to PQ35 Flasher. What should I do first with my car?"),
           ("Why is a tab greyed out?", "Which tabs are greyed out right now and what unlocks each?"),
           ("Keyboard shortcuts", "Which keyboard shortcuts does PQ35 Flasher have?")]


def suggestions(window, key=None):
    """Up to 5 one-click suggestions built from what's on the page: (label, request) or a question."""
    key = key or _current_key(window)
    page = window.pages.get(key) if key else None
    has = current_page_request(window, key) is not None
    mod = _module(window)
    out = []
    try:
        if key == "playback":
            out = _analyze_suggestions(window, page, has)
        elif key == "liveview":
            out = _liveview_suggestions(window, page, has)
        elif key == "logger":
            out = _logger_suggestions(window, page, has)
        elif key == "maps":
            out = _maps_suggestions(window, page, has)
        elif key == "flash":
            out = _flash_suggestions(window, page, has)
        elif key == "diagnostics":
            out = _fault_suggestions(window)
        elif key == "home":
            out = _home_suggestions(window)
        elif key == "connect":
            out = _connect_suggestions(window, page)
        elif key == "backup":
            out = [((f"Back up the {mod}" if window.state.family else "How to back up"),
                    f"Walk me through backing up the {mod}: what's read, how long it takes, and where the file goes."),
                   ("Why back up first?", "Why should I back up before flashing, and how would I restore it?"),
                   ("Is my backup good?", "How do I know my backup is complete and usable?")]
        elif key == "patches":
            out = [("What do these patches do?", f"What do the patches for the {mod} do, and which are safe "
                    "for a road car?"),
                   ("Which patch for me?", f"Which patch should I use on my {mod}, and what are the risks?"),
                   ("Undo a patch", "How do I go back to the original if I don't like a patch?")]
        elif key == "projects":
            out = [("How do projects work?", "How do I use Projects to keep track of my tune versions, files "
                    "and logs?"),
                   ("Track a tune change", "What's a good way to record each tune change and its logs in a project?")]
    except Exception:  # never let a suggestion break the Assistant
        out = []
    seen = {label for label, _r in out}
    for general in GENERAL:
        if len(out) >= 4:
            break
        if general[0] not in seen:
            out.append(general)
    return out[:5]


# Text box hint per page: (with data on the page, without).
PAGE_HINTS = {
    "playback": ("Ask about this log, e.g. “Anything wrong in this pull?”",
                 "Open a log, then ask about it. Or ask how to use Analyze."),
    "liveview": ("Ask about these live values, e.g. “Does this look normal?”",
                 "Press Start to see values, then ask about them. Or ask how Live View works."),
    "logger": ("Ask about the logged values, e.g. “What should I log next?”",
               "Ask what to log, or start logging and ask about the values."),
    "maps": ("Ask about this map, e.g. “What does this map do?”",
             "Show a map, then ask about it. Or ask how to find one."),
    "flash": ("Ask about this flash, e.g. “Is this plan safe?”",
              "Choose an image, then ask for a second opinion. Or ask how flashing works."),
}


def refresh(window, dlg=None):
    """Hint + suggestions for the page the user is on (open, tab switch, window focus, after answers)."""
    dlg = dlg or getattr(window, "_assistant", None)
    if dlg is None:
        return
    key = _current_key(window)
    has = current_page_request(window, key) is not None
    with_data, without = PAGE_HINTS.get(key, (None, None))
    hint = (with_data if has else without) or ("Type a question, or pick one above. E.g. “How do I log a "
                                               "WOT pull and compare it with my last one?”")
    dlg.input.setPlaceholderText(hint)
    dlg.set_suggestions(suggestions(window, key))


def fault_code_context(window):
    """Fault codes the app has read (the all-modules list), and which one the user has
    selected, so "this code" needs no typing."""
    out = ["\n## Fault codes the app has read"]
    selected = None
    health = getattr(window.state, "faults", None)
    rows = []
    if health is not None and health.rows:
        rows = health.context_lines()
        selected = health.selected_code()
    else:
        last = window.state.settings.get("health_last") or {}
        for m in last.get("modules") or []:
            rows.append(f"- {m.get('module')}: {m.get('status')} {m.get('codes', '')}".rstrip())
        if rows:
            rows.insert(0, f"(from the check on {last.get('time', 'an earlier date')})")
    if rows:
        out.append("All-modules check:")
        out += rows
    if len(out) == 1:
        return out[0] + "\nNone read yet. If the user asks about a code, suggest a scan on Connect " \
                        "(it reads every module's codes), or ask them for the code."
    if selected:
        out.append(f"\nThe user has this code selected right now: {selected}. When they say \"this code\" "
                   "or \"it\", they mean this one.")
    out.append("When the user asks about \"my codes\", \"these codes\" or \"the faults\" without naming one, "
               "explain all of the codes above. Don't ask them to type a code that is listed here.")
    return "\n".join(out)


ASSISTANT_SYSTEM = (
    "You are now acting as the PQ35 Flasher help assistant. Answer questions about how to use the app, "
    "step by step, naming the exact tabs and buttons from the manual below. If a tab is greyed out, "
    "explain what unlocks it. If the user asks about something the app can't do, say so. You may also "
    "answer general questions about these VW modules and tuning, with the safety rules above.\n\n"
    "## PQ35 Flasher manual\n"
)


def open_assistant(window):
    """One Assistant window per main window; reopening brings it to the front."""
    existing = getattr(window, "_assistant", None)
    if existing is not None and existing.isVisible():
        refresh(window, existing)
        existing.raise_()
        existing.activateWindow()
        return existing
    if not ai.enabled(window.state.settings):
        ai.ask(window, window.state.settings, "", "", "")  # shows the "AI is off" prompt
        return None
    dlg = ai.AskDialog(window.state.settings, "Assistant", "", "",
                       extra_system=ASSISTANT_SYSTEM + manual(), effort="medium", parent=window,
                       live_system=lambda: "## What the app shows right now\n" + app_context(window))
    dlg.setWindowTitle("PQ35 Flasher Assistant")
    window._assistant = dlg
    dlg.refresh_hook = lambda d: refresh(window, d)
    refresh(window, dlg)
    dlg.show()
    return dlg
