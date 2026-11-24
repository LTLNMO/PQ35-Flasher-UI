"""Help: what each tab is for and how to use its features."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QStackedWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from . import theme
from .widgets import collapsible_sidebar, scrollable

# (key, title, html body). Keys match the NAV keys in main_window.py.
TOPICS = [
    ("start", "Getting started", """
<p>PQ35 Flasher reads, backs up, flashes and monitors VW PQ35-platform modules
(01 Engine, 02 Transmission, 22 AWD (Haldex Gen4), 44 Steering Assist (the ZF EPS rack) and 09 Central Electronics) over a CAN adapter. Modules go by their VCDS address and name.</p>
<h3>The usual order</h3>
<ol>
<li><b>Connect</b>: pick your interface at the top right, turn the ignition on (engine off) and press
<i>Scan</i>. Choose the module you want to work on.</li>
<li><b>Back up</b>: read the module before you change anything. The backup is your way back.</li>
<li><b>Flash</b>: choose an image, check what will be written, tick the checklist and hold the button.</li>
</ol>
<p>Logging, Live View, Diagnostics, Analyze and Maps never write to the module.</p>
<h3>Top bar</h3>
<ul>
<li><b>Interface menu</b>: the CAN adapter to use (J2534, SocketCAN, or the Demo interface).</li>
<li><b>Module chip</b>: the module you've selected and its part number once identified.</li>
</ul>
<h3>Demo mode</h3>
<p>Turn it on in <b>Settings → Demo mode</b> to try everything with pretend modules. Nothing talks to real
hardware and demo files are kept separately in <i>Documents/PQ35 Flasher/Demo</i>.</p>
<p>Tabs that need a module stay greyed out until you've chosen one on Connect.</p>
"""),
    ("home", "Dashboard", """
<p>The app opens here. It's an overview of everything.</p>
<ul>
<li><b>Scan</b>: the big button. Everything starts with a scan, so it opens Connect and
looks for the modules on the car (read-only). Choose the module to work on there. The interface and
module you're on always show at the top right.</li>
<li><b>Your modules</b>: every module you've connected to, how many backups it has, and its last backup
and flash. "never" under Last backup means: back it up before you flash it.</li>
<li><b>Fault codes</b>: every module the last scan read and its stored codes; the one you're
working on is marked <b>●</b>. A row for each module with codes or a problem, the rest named in one line
(the arrow shows each code). <b>Re-read all</b> reads them again; <b>Clear all…</b> clears every module
that has codes (after one confirmation) and reads each back, and a code that comes straight back shows
<b style="color:#ef4444">in red, "still active"</b>. Every <b>Scan</b> reads all of them; a red dot on the
Diagnostics tab means some were found (hover it to see which modules). Double-click a module to open it in
Diagnostics, where the full scan log is. Results from an earlier session show in grey until read again.</li>
<li><b>Recent datalogs</b>: your newest logs with their length and peak rpm, boost, coolant and knock.
Double-click one to open it in Analyze.</li>
<li><b>Projects</b>: your recent projects.</li>
</ul>
"""),
    ("projects", "Projects", """
<p>A project keeps one car's (or one tune's) things together: the modules, labelled tune versions,
datalogs, the backup and flash history, and your notes. Projects live in
<i>Documents/PQ35 Flasher/Projects</i>.</p>
<ul>
<li><b>New project…</b>: the module you're connected to is added automatically, and so is the car (one of your
<b>Cars</b>, from the VIN); the new project becomes that car's active one.</li>
<li><b>Car</b>: which car the project is for (<b>Cars…</b> to add or edit one). <b>Active project for this car</b>
(★ in the list): every new log and dyno session recorded on that car is added to this project by itself. One active
project per car; untick it to stop. Linked logs stay in Datalogs where they were recorded.</li>
<li><b>Tune versions</b>: <i>Add version…</i> copies a file into the project with a label
(e.g. "Stock", "Stage 1 v2") and notes; the file is copied so it can't go missing. Select one to <i>Open in Flash</i>, <i>Open in map viewer</i>,
or <i>Compare with previous</i> (opens Maps → Compare files with both).</li>
<li><b>Modules &amp; history</b>: the modules in the project and every backup, flash and post-flash check
made on them.</li>
<li><b>Logs</b>: datalogs and dyno sessions in the project (the active project gets new ones by itself; <i>Add
logs…</i> links others). Open them in Analyze. Linked logs stay where they are.</li>
<li><b>Notes</b>: saved automatically.</li>
<li><b>Delete…</b> moves the project's folder (notes and tune version copies) to the Trash, so it can be put back.
The original tune files and the logs it links stay where they are.</li>
</ul>
"""),
    ("connect", "Connect", """
<p>Finds the modules on the bus and lets you choose which one to work on.</p>
<ul>
<li><b>Scan</b>: like a VCDS Auto-Scan. It asks the gateway for its list of installed modules, then
opens <i>every</i> one (engine, ABS, airbags, instruments, doors, the gateway itself…), reads its part
number and its stored fault codes, and lists any it couldn't reach (not fitted, asleep or on another bus).
About 1–2 s per module; it only reads. Every module that answered gets its own card here, in two columns
(the selected one first, then modules with fault codes, then by address). <b>Select</b> one to see its
codes on <b>Diagnostics</b>, where <b>Re-read</b> and <b>Clear…</b> work on it alone. Modules the app
only reads faults from keep Flash, Back up and logging locked; modules that don't report fault codes show
without a Select button. The full scan log is on <b>Diagnostics</b>; the codes also show in the Dashboard's <b>Fault codes</b> card.</li>
<li><b>Select manually</b>: choose a module without scanning (e.g. on a bench harness).</li>
<li><b>Module cards</b>: each found module shows its part number and software version.
  <ul>
  <li><b>Select</b>: makes it the module the other tabs work on (its badge turns to <i>Selected</i>).</li>
  <li><b>Read info</b> / <b>Re-read info</b> (on the selected module): reads its part number, software version and name over the cable, e.g. to check a manually selected module is there, or after a flash.</li>
  <li><b>Recover…</b>: for a module stuck in its bootloader. Opens Flash in recovery mode.
  Use only with the exact image made for that module.</li>
  </ul></li>
</ul>
<p><b>Tip:</b> if nothing answers, check the ignition is on and the adapter is plugged in.</p>
"""),
    ("backup", "Back up", """
<p>Copies the firmware off the module into your library (<i>Documents/PQ35 Flasher/Modules</i>).
It's your way back if a tune misbehaves, and new images are compared against it.
A full read takes about 7 minutes over OBD. Nothing is written.</p>
<ul>
<li><b>Read module</b>: starts the read. Keep the ignition on and don't unplug anything.</li>
<li><b>Open module folder</b>: shows the saved backups.</li>
<li><b>Backups of this module</b>: every backup you've made of this exact module.</li>
</ul>
<p>The tab only appears for modules that can be backed up. EPS racks can't be read back;
for the BCM, only the boot page and coding are saved (see Diagnostics).</p>
"""),
    ("flash", "Flash", """
<p>Writes a firmware image to the selected module. Work through the four steps from top to bottom.</p>
<ol>
<li><b>Choose the image</b>: drop a .bin (or an EPS .sgo) on the box. It's checked against the module:
part number, size, checksums. <b>Patch…</b> (EPS) applies patches to a copy first.</li>
<li><b>What to write</b>: the ranges that can be written, with the recommended one picked.</li>
<li><b>Before you flash</b>: confirm the battery supply, ignition and computer power.</li>
<li><b>Flash</b>: hold the button for 3 seconds. Don't touch the car, cable or computer until it's done.</li>
</ol>
<h3>Advanced options</h3>
<ul>
<li><b>Fix wrong internal checksums</b>: only for images you edited yourself.</li>
<li><b>Bench simulator patches</b> (Haldex): bench only, never in a car.</li>
<li><b>Recovery</b>: the module is stuck in its bootloader. Skips the identity checks.</li>
<li><b>Flash without a backup</b>: not recommended.</li>
<li><b>Crossflash</b>: allow an image for a different part number.</li>
</ul>
<h3>Undo / Redo</h3>
<p>The buttons at the top (or <b>Ctrl+Z</b> / <b>Ctrl+Shift+Z</b>) step back and forward through
changes to the chosen image, its patches, the write range and the advanced options.
The "Before you flash" checklist is never undone or redone: tick it yourself each time.</p>
<p><b>BCM only:</b> <i>Check programming conditions</i>, a stock reference image and the RAM driver
are needed before a BCM flash.</p>
"""),
    ("patches", "Patches", """
<p>Offline tools for an image file. Nothing here talks to the car.</p>
<ul>
<li><b>EPS</b>: shows which known patches and HCA gains an image has, and applies new ones.
Patching always writes a new file; the original is never changed.
<b>Flash this file</b> sends the result to the Flash tab.</li>
<li><b>Haldex</b>: checks the Layer-1 checksums. Anti-brick patches are added automatically when
you flash, so there's nothing to apply here.</li>
</ul>
"""),
    ("diagnostics", "Diagnostics", """
<h3>Every module and its scan log</h3>
<p>Laid out like a VCDS Auto-Scan. On the left, <b>Modules</b>: every module on the gateway's list
with its number of fault codes (red ⚠), ✓ when it has none, and the rest greyed out (can't be reached,
doesn't report fault codes). Modules with faults come first. <b>Scan</b> reads them all again
(the same scan as on Connect), <b>Re-read all</b> reads every module's codes again, <b>Clear all…</b>
clears every module that has codes and reads each back. The ‹ hides the list.</p>
<p>On the right, the <b>Scan log</b>: exactly what gets saved, in the layout of a VCDS scan log
(module list with <i>Status: OK 0000 / Malfunction 0010</i>, then a block per module with its part
number, component, fault codes and freeze frames). Click a module on the left to jump to its block;
each block has <b>[Re-read]</b> and <b>[Clear…]</b> (clearing deletes its stored codes and freeze
frames, then reads it straight back; read and note codes before clearing). A code marked
<b style="color:#ef4444">"Still present"</b> came straight back after clearing: the fault is there
now. Double-click a module (or right-click → <i>Work on this module</i>) to make it the module the
other tabs use; right-click also has Re-read, Clear…, Ask AI and Copy.</p>
<p>Every scan's log is saved on its own to <i>Documents/PQ35 Flasher/Scans</i>, named like VCDS's
(<i>Scan-&lt;VIN&gt;-&lt;date&gt;-&lt;time&gt;-&lt;km&gt;km-&lt;miles&gt;mi.txt</i>; a second scan in
the same minute gets <i>-2</i>). <b>History ▾</b> opens earlier scans of this car; the amber line
says what changed since the previous scan (new codes, codes gone). The <b>⋮</b> button has <b>Copy log</b>, which copies the whole log
(e.g. for a forum post), and <b>Export scan log…</b>, which copies it somewhere else. <b>Freeze frames</b> also reads when
and how often each fault happened (about half a second per fault). The log's second line names
this app; lines that only VCDS has (labels, ASAM, VCID) are left out, and fault descriptions aren't
included yet.</p>
<h3>Module tests</h3>
<p>Shown under the log only when you're working on Steering Assist or Central Electronics.
Module-specific measurements (EPS: stored faults, supply voltage, assist, motion,
CAN configuration, serial EEPROM). <i>Samples</i> sets how many readings to take.</p>
<p><b>BCM:</b> <i>Dump BCM page FF</i> saves the 16 KiB boot page with a JSON sidecar. Read-only.</p>
"""),
    ("liveview", "Live View", """
<p>Shows live values on screen. Nothing is saved and nothing is written to the module.
Switch channels on in <b>Select channels ▾</b> at the left of the toolbar (or load a set from
<b>Presets</b>, the bookmark ▾ at the top right), then press the round <b>▶ start</b> button (it turns into <b>■ stop</b> while running, and each start begins with empty graphs). To empty them mid-run: <b>⋯ → Clear graphs and readings</b>, or <b>⇧⌘K</b> (Ctrl+Shift+K).
The dropdown has every channel under its category, a search box, and <i>Select all</i> / <i>Hide all</i> for
what the search finds; a channel that's on shows its colour (the same as its line on the graphs).
Categories open and close with a click on their name; the switch on a category turns all of it on
(or off when it's all on). Channels can't change while Live View is running. Point at a channel to see what it
shows and its alarm in the box under the list; the line under that counts the measuring groups (fewer update faster).</p>
<p><b>Raw groups</b> (the last category): <i>＋ Add group…</i> reads any measuring group by number. Press start: once
it's been read, each of its fields becomes a channel (<i>Group 115 · field 3</i>, switched on) you can put on graphs,
dials and alarms. Watch a field while you change something (rpm, throttle, a switch) to work out what it shows.
The fields are remembered for next time; <b>✕</b> removes the group.</p>
<h3>Layout</h3>
<ul>
<li><b>sliders icon → Scale</b>: <i>Auto</i> stretches each line to its own min–max when channels are very different sizes (rpm next to load or timing), so every line uses the full height; channels of a similar size (boost actual vs requested) stay on one real-unit axis. <i>Fit each channel</i> always stretches; <i>Real values</i> never does. When stretched, the legend shows each line's real range, and a channel that barely moves keeps zero on its scale so small wobbles aren't exaggerated.</li>
<li>The layout button at the top right (beside the sliders icon) chooses how many panels there are and how they're arranged.</li>
<li><b>Right-click a panel</b> to make it <b>Numbers</b>, <b>Dials</b>, <b>Bars</b> or a <b>Graph</b>.</li>
<li>Graphs have channel dropdowns above them; <b>sliders icon → Time shown</b> sets how many seconds are shown.</li>
<li><b>Saved dashboards</b> (the dashboard ▾ beside the layout button): save the whole layout (panels, channels, graph picks) under a name, load it
again, or export/import it as a file to share. The last layout for each module comes back by itself.</li>
</ul>
<h3>Dials and bars</h3>
<p>The scale comes from the unit (rpm 0–8000, % 0–100, …) and widens if a value goes past it.
Alarm limits show as a red zone with an amber warning band just inside.</p>
<h3>Alarms</h3>
<p><b>Right-click a channel</b> (in Select channels or on its card) and choose <b>Set alarm…</b>.
Set a value it mustn't go above and/or below, and whether to beep. When it trips, the card flashes
red, a banner lists every active alarm, and it beeps once. Hover a channel in Select channels to see its limit.</p>
<h3>Math channels</h3>
<p><b>Math channels</b> (the ƒ button at the top right) makes new channels from formulas. Put channel names in [brackets]:</p>
<ul>
<li>Boost in psi: <code>([Boost pressure actual] - 1013) * 0.0145</code></li>
<li>AFR: <code>[Lambda actual] * 14.7</code></li>
<li>Over 6000 rpm: <code>where([Engine speed] &gt; 6000, 1, 0)</code></li>
</ul>
<p>Functions: abs, min, max, sqrt, log, log10, exp, round, clip, where. Math channels appear in a
<i>Math</i> group in Select channels and work with gauges, graphs and alarms. They're shared with
Analyze. Formulas always use the module's own (metric) units.</p>
"""),
    ("logger", "Logger", """
<p>Records live values from the module. Every recording saves itself into <i>Documents/PQ35 Flasher/Datalogs</i>
(change the folder in Settings → Datalogs) as it records, named <i>module_date_time.csv</i>, e.g.
<i>engine_2026-11-09_073012.csv</i>. Nothing is ever deleted on its own: only <b>Discard…</b> removes a
recording. Nothing is written to the module.</p>
<h3>Choosing what to log</h3>
<ul>
<li>Switch values on in <b>Select channels ▾</b> at the left of the toolbar: every value under its category, with a search
box and <i>Select all</i> / <i>Hide all</i> for what the search finds. Categories open and close with a click on their name; the switch on a category turns all of it on
(or off when it's all on). Point at or click a value
to see what it shows (the box under the list). The line under that counts the values and the measuring groups they
come from: each group is another read from the module, so fewer groups log faster.
After a scan, the list only has the values your car's module has: the first scan of a module
checks which of its measuring groups answer, and values it doesn't have are left out.</li>
<li><b>Presets</b> (the bookmark ▾ at the top right, beside the sliders icon, as on Analyze): load a ready-made or
saved set, <i>Save preset…</i> the switched-on channels under a name (e.g. "Boost monitoring"),
<i>Overwrite preset</i> with them, or <i>Manage presets…</i> to rename or delete. Presets are shared with
Live View. The engine ECU also has a ready-made
<b>Integrated Engineering Logger</b> preset: the same 27 values Integrated Engineering's POWERlink logger reads. The normal
log still uses the ECU's own names. Loading it also makes <b>Export…</b> start on Integrated Engineering CSV,
so an exported log looks like a POWERlink log (pick another format there if you don't want that).</li>
<li><b>Smart presets</b> (top of Presets ▾): sets of this module's channels for one job each (boost, timing,
fuel, mixture, …), suggested by your AI (only the channel names are sent) or built in when AI is off. Each set keeps
to at most 4 measuring groups, because every extra group slows every channel down; the menu shows how many channels
and groups each one has. Click one to switch on exactly its channels. Presets → Smart preset options: Regenerate with
AI, <i>Show smart preset chips</i> (a row of them under the controls), and whether to ask your AI automatically.</li>
<li><b>Ask AI for a preset…</b> (top of Presets ▾, or the <i>Ask AI…</i> chip; Analyze has it too): say what you
want to look at, e.g. "check for a boost leak" or "why it hesitates at 3000 rpm", and your AI picks the channels
connected to it and switches them on. It's kept at the front of the smart presets; Smart preset options → Remove my
Ask AI presets clears them. <b>Fault codes:</b> when the last Diagnostics check found codes on the module, the smart
presets start with one per fault (⚠, e.g. "Fault P0299") holding what you'd watch to diagnose it.</li>
<li><b>Raw groups</b> (the last category in the list): <i>＋ Add group…</i> logs every field of any measuring group by
number, unnamed (<i>Group 115 field 1</i>, <i>field 2</i>…), e.g. a group with no named values. A raw group is always
logged while it's in the list; its <b>✕</b> removes it.</li>
</ul>
<h3>Rates</h3>
<ul>
<li><b>sliders icon → Rate</b>: how often each group is read.</li>
<li><b>sliders icon → Group rates…</b>: a different rate per group. Slow down groups that change slowly
(temperatures) so fast ones (rpm, boost) are read more often.</li>
</ul>
<h3>Recording</h3>
<p>Pick the mode with the buttons under <b>Select channels</b>: <b>Continuous</b>, <b>Triggered</b> or <b>Rolling</b> (horsepower and torque have their own <b>Dyno</b> tab)
(the highlighted one is what the round <b>record</b> button ● beside Select channels will use; while recording it turns into <b>stop</b> ■):</p>
<ul>
<li><b>Continuous</b>: every reading is saved.</li>
<li><b>Triggered</b>: saves only while a condition is true, e.g. <i>Throttle &gt; 90</i>, plus a few seconds
before and after. Clicking Triggered (it has a ▾) opens its settings: the channel, &gt; or &lt;, the value,
and how many seconds to keep before and after. Re-arms after each event, so several pulls land in one file. The status line
shows <i>armed</i> or <i>TRIGGERED</i>.</li>
<li><b>Rolling</b>: keeps only the last few minutes (<b>sliders icon → Keep last</b>) in memory and saves them when you stop. Good for
catching something you didn't expect.</li>
</ul>
<p>Engine speed (and other values) appear in several measuring groups. While logging, a value is also taken from
any other group already being read, so it's updated more often at no extra cost.</p>
<h3>While logging</h3>
<p>The list (the same channel list as Analyze's sidebar) shows each switched-on value with its colour,
its unit beside the name, the reading <b>Now</b> and a gauge bar (the unit's usual range, as Live View's bar
gauges; the bars button in the toolbar hides them). <b>Group</b> shows where each value is read
from (measuring group.field). The columns button switches columns on and off: Group, and the session's
<b>Min</b>, <b>Max</b> and <b>Avg</b>, which stay after you stop. A value that stops updating turns amber with
<i>no data 4 s</i>, so you can see at a glance that everything is still being logged. Drag a value up or
down (before Start) to change its column in the log; right-click for Remove / Select channels… / Remove all.</p>
<p><b>Watch in Analyze</b> (the Analyze icon in the toolbar, while recording) opens the recording on Analyze
and graphs it live: the graph grows as rows are written and follows the newest data while the view is at the
end (scroll back to look around). When you stop, Analyze shows the whole log. Not available for Rolling,
whose file is written when you stop.</p>
<p><b>Read times.</b> The module answers one measuring group at a time, so each value is only fresh when its
own group is read (with ten groups, about every 0.7 s). Every recording also keeps which group each row came
from and the raw bytes the module sent. Analyze uses that to show each value at the moment it was really read,
drawing a straight line between two reads instead of holding the old value, so channels from different groups
line up in time. Status codes and bit fields are shown as read. The CSV itself stays as recorded, and Export
writes the recorded values.</p>
<h3>Saving and Export</h3>
<p>There's nothing to save by hand: the log in Datalogs is the Standard CSV (Datazap-compatible) that Analyze reads.
<b>Export…</b> always means a copy in another format or place. Which style it starts on is shown on the menu item
(e.g. <i>Export last log… (Integrated Engineering CSV)</i>) and set in <b>sliders icon → Saving → Export style</b>
(it also follows the format you last picked in the Export dialog; loading the Integrated Engineering Logger preset
switches it to Integrated Engineering CSV). The log name next to the recording modes says what's saved, e.g.
<i>Last log: engine_….csv · Standard CSV</i>.
<b>Auto-export</b> (sliders icon → Saving): pick a format (Integrated Engineering, MegaLogViewer or Generic)
and every time you stop, a copy in that format goes straight into Datalogs too, no dialog, named as Export… would
name it. The stop message says where it went (Show in Finder). Off by default.</p>
<p>Each log is written to Datalogs as it records (Rolling: when you stop), so a crash or a pulled cable
doesn't lose it. When you stop, the message offers <b>Open in Analyze</b>, <b>Export…</b> and <b>Discard…</b>.
At the right end of the Continuous / Triggered / Rolling row:</p>
<ul>
<li><b>Recent logs</b> (clock icon ▾): your latest 20 recordings, newest first, with their length. Each one can be
opened in Analyze, exported or discarded. Older ones are still in Datalogs.</li>
<li><b>⋮ → Export last log…</b>: copy the last log (⋮ also has <b>Open Datalogs folder</b>). The dialog starts in Datalogs on the format you used last:
<b>Standard CSV</b> (the log as recorded: Datazap-compatible, the ECU's own names, units and values),
<b>Integrated Engineering CSV</b> (named like theirs, <i>Datalog-MM-DD-YYYY_HH-MM_&lt;VIN&gt;.csv</i>),
<b>MegaLogViewer</b> (.msl) or a <b>generic CSV</b> with separate name and unit rows. A name that's taken gets
<i>(2)</i>, <i>(3)</i>… added.</li>
</ul>
<p>The car's VIN (read when you plug in) is kept with each recording rather than in its name; exports and Analyze
use it (an Integrated Engineering export is named like theirs, <i>Datalog-MM-DD-YYYY_HH-MM_VIN.csv</i>).</p>
<p>The <b>Integrated Engineering CSV</b> matches what POWERlink writes for this car: Integrated Engineering's variable names,
order, time format and rounding. Integrated Engineering's own mistakes are copied on purpose. On this ECU, "Fuel Rail Actual"
is really the rail pressure setpoint, "LPFP Duty" is the actual rail pressure (both shown in Integrated Engineering's wrong
scale), "Fuel Rail Spec" is the high-pressure pump duty, "Fuel Pump Shut-Off Time" is fuel temperature,
"Intervention" is the transmission status and "Lambda Control Value Bank 2" is a status field.
Use the normal log for real values. <b>Help → Integrated Engineering logging format</b> lists every Integrated Engineering variable and
what it really shows.</p>
<h3>What each value shows</h3>
<p>Point at a value in Select channels, or click it, to see a description in the box under the list. The same
description shows when you hover a row in the table.</p>
"""),
    ("playback", "Analyze", """
<p>Opens a saved log to look through it. Nothing talks to the car.</p>
<ul>
<li><b>Open</b> (file icon, far left): load a log. The <b>gauge</b> icon beside it
shows or hides the channel list; while it's hidden <b>Select channels</b> moves to the front of the
toolbar, so you can still choose channels. <b>Select channels</b> above the list picks which channels are
on the graph (search, Select all, Clear); ✕ closes the channel list (the gauge icon in the toolbar opens it again; Remove all on the right-click menus takes every channel off). The list shows only graphed
channels: colour, name, value at the cursor and a bar showing where that value sits between the
log's lowest and highest. Drag rows to reorder, click one to thicken its line, right-click to remove.</li>
<li><b>Values icon</b> (dial with a dashed ring) beside Select channels: choose the values the list shows: <i>Now</i>,
<i>Compared</i>, <i>Δ</i> (now minus compared), <i>Min</i>, <i>Max</i> and <i>Avg</i>.</li>
<li><b>Bars icon</b> (lines) next to it: range bars under each channel on or off (rows get shorter without them).</li>
<li><b>Axis</b>: <i>Shared</i> puts every channel on one y axis in real units. <i>Independent</i> stretches
each line to its own min–max so every line uses the full height; hover a channel in the legend to see its real range.
In <i>Shared</i>, <b>click a line</b> to give that channel its own
y axis on the right, in its colour; click it again to put it back. Right-clicking a channel in the
sidebar has the same <b>Own y axis</b> switch. Axis applies to the Single graph and to Split rows
holding several channels.</li>
<li><b>Smart presets</b> (chips above the graph; Presets → Smart preset options → Show smart preset chips shows or hides them): ready-made
layouts for this log, like Datazap's. Click one: its channels go on the graph, one Split row per chart.
With an AI chosen in Settings → AI they're suggested by your AI the first time a log's channels are seen
(only the channel names are sent, never the values) and remembered for logs with the same channels;
otherwise built-in ones are used. Presets → Smart preset options: Regenerate with AI, show the chips,
generate automatically (also in Settings → Data).</li>
<li><b>Legend</b> (under the graph, above the timeline, both the same height): one chip per channel with
its value at the cursor, the same channels as the sidebar. <b>Click a chip</b> to hide its line (the dot goes
hollow; the channel stays on the list) and again to show it. Right-click: Show only this, Show all, Own y
axis, Highlight, Remove from graph. <b>+</b> at the end opens Select channels. Right-click a sidebar row
for Hide line too.</li>
<li><b>Toolbar icons</b>: legend on/off, timeline on/off, <b>Math channels</b> (ƒ), <b>Presets</b>
(bookmark: smart presets picked from the log's channel names, or your own; <i>Save preset</i>, <i>Overwrite preset</i> with the channels on the graph now, and <i>Manage presets</i> to rename or delete), and <b>⋮</b>:
Log details, Edit log (rename, or save the shown/selected part as a new log), Reset view and
Export….</li>
<li><b>Fit</b> (the button under ⚙ on the graph): the whole log across and every line back in view.</li>
<li><b>⚙ on the graph</b> (or right-click the graph): Reset view, tick channels on or off, Axis,
Legend, Timeline, Follow mouse, Min / Max / Avg range, Export graph (image or CSV), axis ranges,
mouse mode and plot options.</li>
<li><b>Cursor</b>: with <i>Follow mouse</i> on, the cursor follows the mouse over the graph;
otherwise click or drag it. A box on the graph shows each graphed channel's value there. Click the
graph, then use ← / → to step one sample (Shift: ten).</li>
<li><b>Zoom</b>: scroll (two fingers on a trackpad) over the graph to zoom. Scroll over the value
numbers on the left (or a channel's own axis) to stretch or flatten the lines only, or over the time
numbers to zoom the time only. Double-click the value numbers to put the values back. On Split,
scrolling over the graphs moves up and down the rows (when there are more than fit); pinch to zoom
the time, or scroll over the timeline. Scrolling over a row's value numbers changes just that row.</li>
<li><b>Timeline</b> (the strip under the graph): the whole log in miniature. Drag the band to move
through the log, drag its edges or scroll to zoom, click to move the cursor, double-click to show it all.</li>
<li><b>Min / Max / Avg use</b>: which part of the log Min / Max / Avg, the heatmap, scatter
and the Assistant use. <i>What's shown</i> follows the zoom; <i>Whole log</i> always uses
everything; <i>Selection</i> uses the blue band on the graph (drag it or its edges; it also shows on
the timeline). <b>Shift-drag</b> across the graph to draw a new selection (e.g. one WOT pull); this
switches to <i>Selection</i> for you.</li>
</ul>
<h3>Views</h3>
<ul>
<li><b>Single</b>: channels against time.</li>
<li><b>Split</b>: stacked chart rows sharing the time axis, one channel per row to start, each on its
own real-unit scale. <b>Drag a channel's name</b> onto another row to plot them together (or right-click a
row → <i>Move … to</i>; <i>Split this row</i> undoes it). A row with several channels follows the Axis
setting. <b>Drag the gap between rows</b> to make one taller. Zoom, pan and the cursor stay in step with
Single and the timeline; each row shows its channels' values at the cursor.</li>
<li><b>Heatmap</b>: a table laid out like an ECU map. Pick X (e.g. engine speed), Y (e.g. load) and a
value (e.g. timing retard); each cell shows the value's average, max, min or sample count where X and
Y were near that breakpoint, coloured blue (low) to red (high). Hover a cell for its sample count.</li>
<li><b>Scatter</b>: one channel against another (e.g. rpm vs boost), optionally coloured by a third.</li>
<li><b>Raw</b>: the numbers as a table, one column per channel on the graph (every channel when none are ticked), each headed by its colour dot; hover a cut-off name to read it in full. The cursor's row is
highlighted; click a row to move the cursor there.</li>
</ul>
<h3>Comparing runs</h3>
<ul>
<li><b>Compare logs ▾</b> (beside Open, right of the log's name) → <i>Overlay a log on the graph…</i>:
load a second log. Its channels draw as dashed lines in the same colours, and
the cursor box shows its value too. Turn on the <i>Compared</i> and <i>Δ</i> columns to see them
for every channel.</li>
<li>While comparing, a row under the toolbar has <b>Shift</b>: move the compared log in time. <b>Auto-align</b> lines both logs up where the first
ticked channel (throttle or rpm works best) first rises past halfway.</li>
<li><b>Clear</b>: remove the compared log.</li>
<li><b>Compare logs ▾ → Compare logs with AI…</b>: pick any number of logs (up to 12; <i>Add logs…</i>, <i>Recent ▾</i>,
drag to reorder; A is the baseline), then <b>Analyze with AI</b>. The app finds each log's full-throttle pulls,
matches channels by what they are (so Standard and IE logs line up) and sends your AI tables averaged per
500 rpm with a column per log, plus checks (timing retard per cylinder, boost and rail pressure against target,
lambda control, temperatures, voltage). Untick <i>full-throttle pulls only</i> to use the whole logs. The Ask AI
window opens with one-click questions: rank the runs, what changed, which is safest, real change or noise, what next.</li>
<li>The Assistant reads a log the same way: pulls, checks and rpm tables rather than a thin sample, and with a log
overlaid, both side by side.</li>
</ul>
<p><b>⚙ → Math channels…</b> adds formula channels to the loaded log (see Live View).
<b>⋮ → Export…</b> saves the log as Standard CSV (Datazap), Integrated Engineering CSV,
MegaLogViewer or generic CSV.</p>
<p><b>Open ▾</b> lists the recent recordings and <i>Open file…</i>. Besides this app's own logs it opens the
text/CSV logs Datazap takes: COBB Accessport, MHD, bootmod3, JB4, EcuTek, VCDS, ME7Logger, EvoScan, Torque,
DashCommand, RomRaider, Simos Tools, Tactrix, Haltech CSV, MegaLogViewer (.msl) and Integrated Engineering. The file is
converted to the Standard format for Analyze (the original isn't changed); text columns are dropped. Integrated
Engineering logs get the ECU's own names back where their values match, so they line up with your logs in Compare;
their two fuel rail pressure columns keep Integrated Engineering's names because their scaling can't be undone.
Binary logs (MegaSquirt .mlg) aren't supported: save them as .msl or CSV first.</p>
<p>Drag the divider between the graph and the values table to give either more room.</p>
"""),
    ("dyno", "Dyno", """
<p>A virtual dyno: horsepower and torque against engine speed from full-throttle pulls in one gear, worked out
from how fast the car speeds up (its weight, drag and tyres), like an inertia dyno with the car as the drum.</p>
<h3>Recording</h3>
<p>Choose the engine on <b>Connect</b>, then press the round <b>record</b> button ●. A dyno session reads only what the
dyno needs: engine speed, vehicle speed, the accelerator pedal, mass air flow and the ECU's torque (for the
cross-check), plus atmospheric pressure and outside temperature every few seconds. Engine speed comes with every one
of those groups, so it's read about 20 times a second. Floor it in one gear (3rd is ideal) from low rpm to near
redline, on a flat, empty road; each full-throttle pull (pedal over 90 %, 2 s before, 1 s after) is saved, and the
curve appears a few seconds into the pull. A run each way on the same road cancels slope and wind. Press ■ to stop.
The interface reads one thing at a time, so Record waits while the Logger or Live View is reading the module.</p>
<h3>Sessions</h3>
<p>Each recording is a session in the list on the left, saved as you record in <i>Datalogs/Dyno</i>, named like any log
(<i>engine_date_time.csv</i>; nothing is deleted unless you discard it). Click one to see it; right-click to open it in Analyze, show the file, or discard it. The open-file button
opens any other log (Standard / Datazap or Integrated Engineering CSV) and finds its pulls. The sidebar button at the
far left of the top row hides or shows the list.</p>
<h3>Export</h3>
<p>The session saves itself; the <b>⋮</b> button (top right) exports three different things, each named after the
session with <i>dyno_</i> in front (e.g. <i>dyno_2026-11-14_121055_graph.png</i>): <b>Export graph as image…</b> (a PNG picture of the sheet as shown, to share),
<b>Export numbers as table…</b> (a CSV of the curves: power and torque at each rpm, wheel and
engine, plus ECU torque and airflow power when logged, in your metric / imperial units, for a spreadsheet), and
<b>Export log…</b> (the whole recording, every reading, as Standard, Integrated Engineering, MegaLogViewer or generic
CSV).</p>
<h3>The car and the load</h3>
<p>The <b>car icon</b> beside the record button holds the session's <b>car</b> and <b>load</b> (hover it for both; the
sheet's details line shows them too, e.g. <i>1,580 kg · 2011 A3 (8P)</i>). The car comes from the VIN by itself; click
the icon to change the people, cargo and fuel, or to pick another car if it's wrong (or the log had no VIN). The icon
turns amber when there's no saved car for the session. <b>Edit car…</b> opens <b>Cars</b>. Each session
remembers its car and load from when it was recorded, so its numbers never change because of a later session; with
a session picked the button shows (and can correct) that session's, before you record it sets the next one's.
Weight matters most: 5 % off is 5 % off.</p>
<h3>Reading the sheet</h3>
<p>Pick a pull, the <b>Average</b> of all of them or, with three or more pulls, back-to-back pairs (<b>Pulls 1 + 2
averaged</b>: one each way cancels slope and wind). If pulls in the same gear differ by 4 % or more it says so.
<b>Wheel</b> is what a chassis dyno shows; <b>Engine (estimate)</b> adds what the rotating parts and the
drivetrain take (the loss is in the car profile), with the engine ECU's own torque dashed and airflow power dotted
when they were logged. The <b>Cross-check</b> line compares peak engine power three ways: this dyno, the ECU's torque,
and mass air flow × 1.3 hp per g/s. Within 8 % is green; further apart points at the weight, tyre size or speed signal.
Power is worked out like an inertia dyno, with the car as the drum: weight × acceleration plus air drag and rolling
resistance, times speed. Speed comes from engine speed × the gearing measured from the log (no gear ratios to type in);
if road speed and engine speed disagree (clutch slip, wheelspin, a shift) it warns you. With the gearbox set to the
6-speed DSG in the car profile it also checks the speed signal against the gear ratios and tyre size (a speedo 3 %
high makes power 6 % high) and by default blends the two (<b>Speed from → Automatic</b>), weighting each by how far it's typically off,
which beats either alone; more than 4 % apart it uses the speed signal and warns you. Each pull gets a rating
(<b>Good / Fair / Poor</b>, also in the pull list; hover the headline for the reasons): read rate, slip, rpm covered,
a pull cut off before peak power, the cross-check, and the speed sources disagreeing. The correction (SAE J1349, STD,
DIN 70020 or none) uses the air pressure and outside temperature the engine ECU reports (a dyno session logs them), else standard air (20 °C, 1013 mbar). Weight matters most: 5 % off is 5 % off, so a weighbridge figure is best; the profile adds the people
in the car, cargo and how full the tank is (set them before each session). Good
for comparing runs and tunes on the same road; the absolute number won't match every chassis dyno (neither do they).</p>
"""),
    ("cars", "Cars", """
<p>Your saved car profiles: what each car is and its specs. Open them by clicking the car in the top bar, or
<b>Cars…</b> on the Dyno tab.</p>
<ul>
<li><b>Filled in by themselves.</b> Plug the interface into a car (ignition on) and the app reads its VIN by
itself within a few seconds: the car shows in the top bar, its profile is made if it's new (typical figures for
the model, then your AI looks up that exact car in the background) and its active project opens on the Projects
tab. A scan on Connect adds the modules found, the engine's part number and the mileage (and all-wheel drive when
a Haldex answers). Every recording is tagged with the car connected then; until a car's VIN has been read, a
recording carries no car (never the one from last time).</li>
<li><b>Yours to change.</b> Kerb weight, fuel tank, tyre size (width / profile / rim, as on the sidewall), gearbox, where the dyno's
road speed comes from, drivetrain loss, drag and rolling resistance, plus notes and mods (the AI sees those too).
Anything you change is saved as yours and never replaced automatically. <b>Fill in with AI</b> asks again.</li>
<li><b>Add</b> a car by hand (with or without a VIN); <b>Delete…</b> removes a profile (logs and dyno sessions
stay).</li>
<li><b>Projects</b>: a car's projects are listed with it; pick the car for a project on the Projects tab.</li>
</ul>
"""),
    ("maps", "Maps", """
<p>Looks inside ECU files. Read-only: nothing is written to a file or a module. Switch between the
<b>Map viewer</b> and <b>Compare files</b> at the top of the sidebar; the sidebar holds the files and
settings, the right side shows the result.</p>
<h3>Map viewer</h3>
<ol>
<li><b>Open file…</b>: choose a .bin.</li>
<li>Describe the map: <b>address</b> (hex, e.g. 0x1A040), <b>rows</b> and <b>columns</b>,
<b>data type</b> (8/16/32-bit, signed or unsigned, little or big endian, or float) and
the <b>factor</b> and <b>offset</b> that turn raw numbers into real units.</li>
<li>Optionally give the <b>X</b> and <b>Y axis</b> addresses so rows and columns are labelled
(e.g. rpm and load).</li>
</ol>
<p>The <b>Table</b> view colours cells from blue (low) to red (high); the <b>Graph</b> view draws one line
per row; the <b>3D</b> view shows the map as a surface: drag to turn it, scroll to zoom.
<b>Save</b> keeps the definition so you can load it again from <i>Saved maps</i>.</p>
<h3>Comparing a map between two files</h3>
<p><b>Compare…</b> loads a second file (e.g. the tuned one) and reads the same map from it. The
<b>View</b> menu above the table then picks <i>This file</i>, <i>Other file</i> or the <i>Difference</i>: red where the
other file is higher, blue where it's lower, grey where nothing changed. The line above the table counts
the changed cells. The graph shows the other file as dashed lines, and 3D works for the difference too.</p>
<p>Map addresses depend on the exact software version, so a definition for one file may not fit another.</p>
<h3>Compare files</h3>
<p>Choose <b>File A</b> (e.g. stock) and <b>File B</b> (e.g. tuned) to list every range of bytes that
differs (listed in the sidebar). Select a row to see the bytes side by side with the changes highlighted, then
<b>Open in map viewer</b> to look at that change as a table.</p>
"""),
    ("settings", "Settings", """
<ul>
<li><b>Theme</b>: dark, light or follow the system.</li>
<li><b>Font size</b> and <b>accent colour</b>.</li>
<li><b>Tab style</b>: icons (the name slides out on hover) or text.</li>
<li><b>Tooltips</b>: turn the hover tips off once you know your way around.</li>
<li><b>Features</b>: untick tabs you don't use (Projects, Back up, Flash, Patches, Diagnostics, Live View,
Logger, Analyze, Maps) and they disappear from the tab bar. Maps starts off. Nothing is deleted.</li>
<li><b>Window</b>: open in a window or full screen. <b>F11</b> (⌃⌘F on a Mac) switches at any time.</li>
<li><b>Caution</b>: the safety caution shows on every start unless you tick <i>Don't show this
again</i> on it; <i>Show the caution when the app opens</i> brings it back.</li>
<li><b>Units</b>: metric (°C, mbar, km/h, Nm) or imperial (°F, psi, mph, lb-ft) in Live View, the
Logger and Analyze. Logs are always saved in metric, and formulas and alarm limits keep
working when you switch.</li>
<li><b>Lock</b>: ask for a password before flashing, clearing fault codes, patching or recovery mode.
Unlocking lasts 10 minutes; <i>Lock now</i> locks again straight away. It stops a shared laptop
being used to flash by accident; it isn't protection against someone who can edit the
settings file.</li>
<li><b>Demo mode</b>: try the app with pretend modules; nothing talks to real hardware and the app
restarts when you switch. While it's on, <i>Reset demo fault codes</i> puts the pretend fault codes
back. Demo mode offers two interfaces (top-bar interface menu): <i>pretend module</i> (quick canned
answers) and <i>simulated car (tests the real scan)</i>, a pretend 17-module car that answers message by
message like a real one, so Scan and fault-code reads/clears run the real code. After each scan
on it, Connect says whether the scan found exactly what the car has (passed / FAILED with the
differences). Its codes reset when the app restarts.</li>
</ul>
<p>Changes are saved automatically and applied straight away.</p>
"""),
    ("ai", "AI and the Assistant", """
<p>PQ35 Flasher can use the AI you already use to give feedback. It's off until you choose one in
<b>Settings → AI</b>, and nothing is sent until you press <b>Ask</b>.</p>
<h3>Choosing your AI</h3>
<ul>
<li><b>Anthropic (Claude API)</b>, <b>OpenAI (ChatGPT API)</b>, <b>Google Gemini API</b>, <b>OpenRouter</b>:
paste an API key from that company's developer site and press <i>Save key</i>. Pick a model
(<i>Fetch models</i> lists what your key can use) and press <i>Test</i>.</li>
<li><b>Claude Code (your Claude subscription, no API key)</b>: if you have Claude Code or its VS Code
extension (the installer adds it) and you're signed in with a Claude Pro or Max account, PQ35 Flasher asks it for you. No key and
no API bill: it counts toward your plan's usage. It's found automatically; pick a model (or leave
Claude Code's default) and press <i>Test</i>. It runs with all of Claude Code's tools switched off, so
it can only answer, never touch files.</li>
<li><b>Gemini CLI (your Google account)</b> and <b>Codex CLI (your ChatGPT account)</b>: the same idea
for Google and ChatGPT accounts. Gemini has a free daily allowance; Codex counts toward your ChatGPT plan.
The installer adds these tools (and Claude Code) for you. Choose one, press <b>Sign in…</b> once (a
terminal window opens for the sign-in), then <i>Test</i>. Like Claude Code, they run in an empty folder
in read-only mode and can only answer.</li>
<li><b>Ollama</b> or <b>LM Studio</b>: free models that run on this computer, so nothing leaves it.</li>
<li><b>Other OpenAI-compatible server</b>: any service with an OpenAI-style API (Groq, Together, vLLM…).</li>
<li><b>My AI chat in the browser (no key)</b>: uses your normal ChatGPT, Claude or Gemini subscription.
The question and data are copied to the clipboard and your chat site opens. Paste and send.</li>
</ul>
<p>Chat subscriptions can't be signed in to from other apps directly, which is why there's the Claude
Code, API key, local, or copy-to-chat choice.</p>
<h3>✦ Assistant</h3>
<p>The <b>✦</b> icon next to Help and Settings (or <b>⌘K</b> / <b>Ctrl+K</b>) opens the assistant. It
knows this whole help guide and what's on screen: the tab, module, interface, every fault code the app
has read, and the data on the page you're on. So on a page with data you can just ask "analyze this" or
"anything wrong?":</p>
<ul>
<li><b>Analyze</b>: the open log (or the selected range), or both runs when comparing.</li>
<li><b>Maps</b>: the map shown; on Compare files, the changed byte ranges.</li>
<li><b>Diagnostics</b>: the codes of the module you're working on (and all modules).
<b>Right-click a code</b> (or double-click it) for <i>Ask AI about this code</i>.</li>
<li><b>Flash</b>: the image checks and the write plan, for a second opinion. It never replaces the
app's own safety checks.</li>
<li><b>Live View</b>: the current values, their recent range, and any tripped alarms.</li>
<li><b>Logger</b>: the readings in the list with their min, max and average so far.</li>
</ul>
<p>It reads the page again with every message, so you can switch pages mid-chat. You can watch the
answer appear, ask follow-ups, see exactly what's sent (<i>Show what's sent</i>), and copy the answer.</p>
<h3>Privacy</h3>
<p>The first time you use a cloud provider you see exactly what will be sent. Only text summaries are
sent (stats and a sample of rows, map tables, fault codes), never your files. API keys are stored only
on this computer in their own file. AI can be wrong: double-check anything before you flash.</p>
"""),
    ("safety", "Safety tips", """
<ul>
<li>Always <b>back up</b> before flashing.</li>
<li>Use a <b>battery charger or stable 13–14 V supply</b> while flashing. A voltage drop mid-flash
can leave a module stuck in its bootloader.</li>
<li>Keep the <b>computer plugged in</b>, turn off sleep, and don't touch the cable during a flash.</li>
<li>Only use <b>Recovery</b> and <b>Crossflash</b> when you know exactly which image the module needs.</li>
<li>Read and note fault codes before clearing them.</li>
<li>Log on a closed road or dyno, and keep your eyes on the road; use alarms rather than watching the screen.</li>
</ul>
"""),
    ("shortcuts", "Keyboard shortcuts", """
<table cellpadding="4">
<tr><td><b>Ctrl+Z</b></td><td>Undo (Flash tab)</td></tr>
<tr><td><b>Ctrl+Shift+Z</b></td><td>Redo (Flash tab)</td></tr>
<tr><td><b>F11</b> / <b>⌃⌘F</b></td><td>Full screen on / off</td></tr>
<tr><td><b>F5</b></td><td>Reload the theme</td></tr>
</table>
<p>On a Mac, use ⌘ instead of Ctrl.</p>
"""),
    # Shown as the live log (session_log.LogViewer); this text is for search and the Assistant.
    ("logs", "Troubleshooting & logs", """
<p>When something goes wrong. This section shows the app version, checks for updates, and the
live activity log: what the app and the flasher did this session.</p>
<ul>
<li><b>Check for updates</b>: compares this copy with the latest version on GitHub.</li>
<li><b>Show protocol detail</b>: include low-level messages in the log.</li>
<li><b>Save support bundle…</b>: zips this session's log and module history (not your firmware
files) so you can send them when asking for help.</li>
<li><b>Open logs folder</b>: shows the log files. The last 20 sessions are kept, and crash reports
are saved there too.</li>
</ul>
"""),
]



def _add_ie_topic():
    """Integrated Engineering logging format, right after Logger. Built from the Integrated Engineering export's own column map."""
    try:
        from .channel_info import ie_help_html
        body = ie_help_html()
    except Exception as e:  # never break Help over this
        body = f"<p>Couldn't build this page: {e}</p>"
    at = next((i + 1 for i, t in enumerate(TOPICS) if t[0] == "logger"), len(TOPICS))
    TOPICS.insert(at, ("ie_logging", "Integrated Engineering logging format", body))


_add_ie_topic()


class HelpPage(QWidget):
    title = "Help"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("page")
        self._history, self._ahead, self._moving = [], [], False
        if theme.is_mac():
            self._build_mac()
            return
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Top row: search, then the topics' toggle (a picker first, its column's toggle beside it)
        top = QHBoxLayout()
        top.setContentsMargins(16, 16, 16, 0)
        top.setSpacing(8)
        from .toolbar import ToolIcon
        self.sidebar_button = ToolIcon("toc", "Show or hide the topics", checkable=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help (alarm, trigger, map…)")
        self.search.setMaximumWidth(360)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.sidebar_button)
        top.addStretch(1)
        outer.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(6)
        outer.addWidget(split, 1)

        side = QWidget()
        side.setObjectName("page")
        sb = QVBoxLayout(side)
        sb.setContentsMargins(16, 10, 8, 16)
        sb.setSpacing(10)
        self.topics = QListWidget()
        for key, title, _body in TOPICS:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.topics.addItem(item)
        self.topics.currentItemChanged.connect(lambda item, _prev: self._show(item))
        sb.addWidget(self.topics, 1)
        split.addWidget(scrollable(side))

        right = QWidget()
        right.setObjectName("page")
        rl = QHBoxLayout(right)
        rl.setContentsMargins(8, 10, 16, 16)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)  # web links (e.g. Integrated Engineering's glossary) open in the browser
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.view)
        self.log_viewer = None  # built the first time "Troubleshooting & logs" is opened
        rl.addWidget(self.right_stack)
        split.addWidget(right)
        split.setSizes([260, 800])
        split.setCollapsible(1, False)
        toggle = collapsible_sidebar(split, state.settings, "help", "Topics", button=self.sidebar_button)
        # Topics hidden: the text keeps the page's 16 px edge
        edge = lambda shown: rl.setContentsMargins(8 if shown else 16, 10, 16, 16)
        toggle.shown_changed.connect(edge)
        edge(self.sidebar_button.isChecked())
        self.topics.setCurrentRow(0)
        theme.on_apply(lambda: self._show(self.topics.currentItem()))  # page colours follow the theme

    # --- Mac interface: the mockup's Help window (Mockups/Swift native/14-help.png) ---

    def _build_mac(self):
        """Sidebar (search + topics) running up under the traffic lights; on the right a title row
        "PQ35 Flasher Help" with back / forward, a hairline, then the text: plain, no box."""
        from PySide6.QtWidgets import QFrame
        from .toolbar import ToolIcon
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        self._split = split
        outer.addWidget(split)

        side = QWidget()
        side.setObjectName("helpSide")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sb = QVBoxLayout(side)
        sb.setContentsMargins(10, 44, 10, 10)  # 44: clear of the traffic lights
        sb.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        from .main_window import _material_icon
        from PySide6.QtWidgets import QLineEdit as _LE
        self._search_icon = self.search.addAction(_material_icon("search", 16, theme.colors()["muted"]),
                                                  _LE.ActionPosition.LeadingPosition)
        sb.addWidget(self.search)
        self.topics = QListWidget()
        self.topics.setObjectName("helpTopics")
        self.topics.setFrameShape(QFrame.Shape.NoFrame)
        self.topics.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.topics.setTextElideMode(Qt.TextElideMode.ElideRight)  # long titles end in …
        for key, title, _body in TOPICS:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.topics.addItem(item)
        self.topics.currentItemChanged.connect(lambda item, _prev: self._show(item))
        sb.addWidget(self.topics, 1)
        side.setMinimumWidth(180)
        split.addWidget(side)

        right = QWidget()
        right.setObjectName("page")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar = QWidget()
        bar.setFixedHeight(52)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 0, 16, 0)
        row.setSpacing(6)
        title = QLabel("PQ35 Flasher Help")
        title.setObjectName("h1")
        row.addWidget(title)
        row.addSpacing(10)
        self.back_button = ToolIcon("chevron_left", "Back")
        self.forward_button = ToolIcon("chevron_right", "Forward")
        self.back_button.clicked.connect(lambda: self._step(-1))
        self.forward_button.clicked.connect(lambda: self._step(1))
        row.addWidget(self.back_button)
        row.addWidget(self.forward_button)
        row.addStretch(1)
        rl.addWidget(bar)
        self._rule = QFrame()
        self._rule.setFixedHeight(1)
        rl.addWidget(self._rule)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        # Lines no longer than the mockup's 780 px column, however wide the window.
        self.view.setLineWrapMode(QTextBrowser.LineWrapMode.FixedPixelWidth)
        self.view.setLineWrapColumnOrWidth(780)
        self.view.setViewportMargins(44, 26, 24, 26)
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.view)
        self.log_viewer = None
        rl.addWidget(self.right_stack, 1)
        split.addWidget(right)
        split.setCollapsible(1, False)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 880])
        self._mac_style()
        theme.on_apply(self._mac_style)
        self.topics.setCurrentRow(0)
        self._nav_buttons()
        theme.on_apply(lambda: self._show(self.topics.currentItem()))

    def _mac_style(self):
        c = theme.colors()
        from .main_window import _material_icon
        self._search_icon.setIcon(_material_icon("search", 16, c["muted"]))
        line = c.get("sidebar_line", c["border"])
        self._split.setStyleSheet(f"QSplitter::handle {{ background: {line}; }}")
        self._rule.setStyleSheet(f"background: {c.get('header_line', line)};")
        self.findChild(QWidget, "helpSide").setStyleSheet(
            f"#helpSide {{ background: {c.get('sidebar', c['surface'])}; }}"
            f" #helpSide QLineEdit {{ background: {c.get('conn_bg', c['surface2'])}; border: none;"
            f" border-radius: 7px; padding: 4px 8px; }}"
            f" QListWidget#helpTopics {{ background: transparent; border: none; outline: none; }}"
            f" QListWidget#helpTopics::item {{ padding: 4px 10px; border-radius: 6px; color: {c['text']}; }}"
            f" QListWidget#helpTopics::item:hover {{ background: transparent; }}"
            f" QListWidget#helpTopics::item:selected {{ background: {c.get('sidebar_sel', c['surface2'])};"
            f" color: {c['text']}; }}")
        self.view.setStyleSheet("QTextBrowser { background: transparent; }")

    def _step(self, direction):
        """Back (-1) / Forward (+1) through the topics read, like Help Viewer."""
        source, target = (self._history, self._ahead) if direction < 0 else (self._ahead, self._history)
        if direction < 0 and len(source) < 2 or direction > 0 and not source:
            return
        if direction < 0:
            target.append(source.pop())
            key = source[-1]
        else:
            key = source.pop()
            target.append(key)
        row = next(i for i, (k, _t, _b) in enumerate(TOPICS) if k == key)
        self._moving = True
        self.topics.setCurrentRow(row)
        self._moving = False
        self._nav_buttons()

    def _remember(self, key):
        if self._moving or (self._history and self._history[-1] == key):
            return
        self._history.append(key)
        self._ahead.clear()
        self._nav_buttons()

    def _nav_buttons(self):
        if hasattr(self, "back_button"):
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            for button, on in ((self.back_button, len(self._history) > 1),
                               (self.forward_button, bool(self._ahead))):
                button.setEnabled(on)
                fade = button.graphicsEffect() or QGraphicsOpacityEffect(button)
                fade.setOpacity(1.0 if on else 0.4)  # the mockup's faded arrow
                button.setGraphicsEffect(fade)

    def refresh(self):
        pass

    def show_logs(self):
        """Troubleshooting & logs: the live activity log instead of help text."""
        if self.log_viewer is None:
            from .session_log import LogViewer
            self.log_viewer = LogViewer(self.window())
            self.right_stack.addWidget(self.log_viewer)
        self.right_stack.setCurrentWidget(self.log_viewer)
        row = next(i for i, (k, _t, _b) in enumerate(TOPICS) if k == "logs")
        if self.topics.currentRow() != row:
            self.topics.setCurrentRow(row)

    def showEvent(self, event):
        super().showEvent(event)
        self._show(self.topics.currentItem())  # pick up theme changes

    def _filter(self, text):
        text = text.strip().lower()
        first = None
        for i, (_key, title, body) in enumerate(TOPICS):
            match = not text or text in title.lower() or text in body.lower()
            self.topics.item(i).setHidden(not match)
            if match and first is None:
                first = i
        current = self.topics.currentItem()
        if first is not None and (current is None or current.isHidden()):
            self.topics.setCurrentRow(first)
        else:
            self._show(self.topics.currentItem())

    def _show(self, item):
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self._remember(key)
        if key == "logs":
            self.show_logs()
            return
        self.right_stack.setCurrentWidget(self.view)
        title, body = next((t, b) for k, t, b in TOPICS if k == key)
        c = theme.colors()
        query = self.search.text().strip()
        if query:
            import re
            body = re.sub(f"(?i)(?<![<&\\w])({re.escape(query)})(?![^<]*>)",
                          f"<span style='background:{c['accent']};color:{c['on_accent']}'>\\1</span>", body)
        if theme.is_mac():  # the mockup: 26 px title, 17 px white headings, roomy lines
            fs = theme.font_size()
            self.view.setHtml(f"""
<style>
  body {{ color: {c['text']}; font-size: {fs + 1}pt; }}
  p, li {{ line-height: 150%; }}
  h2 {{ color: {c['text']}; font-size: {fs + 12}pt; font-weight: 700; margin-bottom: 10px; }}
  h3 {{ color: {c['text']}; font-size: {fs + 4}pt; font-weight: 700; margin-top: 18px; margin-bottom: 6px; }}
  code {{ background: {c.get('surface2', c['surface'])}; }}
  li {{ margin-bottom: 4px; }}
  i {{ color: {c['muted']}; }}
</style>
<h2>{title}</h2>{body}""")
            return
        self.view.setHtml(f"""
<style>
  body {{ color: {c['text']}; }}
  h2 {{ color: {c['text']}; margin-bottom: 6px; }}
  h3 {{ color: {c['accent']}; margin-top: 14px; margin-bottom: 4px; }}
  code {{ background: {c.get('surface2', c['surface'])}; }}
  li {{ margin-bottom: 4px; }}
  i {{ color: {c['muted']}; }}
</style>
<h2>{title}</h2>{body}""")
