# PQ35 Flasher UI

A slick UI for [PQ35-Flasher](https://github.com/DSparks156x/PQ35-Flasher): back up,
patch and flash Haldex Gen4 and ZF EPS rack modules, and read EPS diagnostics, without
touching the command line.

You are at your own risk using this project. I am not responsible for thermonuclear war, broken timing chains, divorce, or you being sad because your EPS no longer works if something goes wrong. 

This project is intended to limit the ways you can screw things up, and make the flashing process simple - but it cannot stop you from being a dumbass. If you use the ignore safeguards buttons and flash a binary of the game tetris to your EPS, I cannot help you. 

A failed flash means that module does not function until it has been successfully recovered. In very rare cases, it may not be recoverable.  This means no EPS. 

In practice its pretty hard to brick shit beyond recovery, but please - 
Do not Dumb. 

## About this fork

Working on the code? [ARCHITECTURE.md](ARCHITECTURE.md) maps the parts and the rules the tests enforce.

This is LTLNMO's fork of [DSparks156x/PQ35-Flasher-UI](https://github.com/DSparks156x/PQ35-Flasher-UI).
All credit for the original app and the flasher goes to Drew (DSparks156x). It's based on his
commit `63d5fec` (2026-11-04). Flashing works the way it does in his version. Most of the
changes are in logging, diagnostics and the interface.

### Added

- **Dashboard**: a Quick Scan button, a fault-code summary for every module, recent
  datalogs, your modules and projects.
- **Scan and Full Scan**: Scan (on Connect, and Quick Scan on the Dashboard) reads just
  the main modules: engine, transmission, ABS brakes, AWD and steering by default, and you
  can change the list in Settings → General → Scan. Full Scan reads every module the
  gateway lists.
- **Diagnostics**: a VCDS-style auto-scan (Full Scan). It reads every module in the gateway's
  installation list in one pass, with fault codes and freeze frames (status, mileage, date
  and time). You can re-read or clear one module or all of them, every scan is saved, a new
  scan is compared with the last one, and you can copy or save the scan log.
- **Logger**:
  - A channel picker with presets, including an Integrated Engineering preset.
  - Smart presets: channel sets for your module suggested by your AI (or built-in rules), each kept
    to a few measuring groups so it reads fast; in Presets and as optional chips. Ask AI for a
    preset in your own words, and sets for the fault codes the last scan found come first.
  - Three recording modes: Continuous, Triggered (records around a trigger) and Rolling
    (keeps the last few minutes).
  - Alarms (the same limits as Live View's): a channel past its limit turns red, the window
    flashes red, and a popup lets you keep recording or stop.
  - Only the channels your car has: a scan checks, once per engine/transmission/AWD
    module (part number and software version), which measuring groups it answers, and
    the channel lists leave out the ones it doesn't (a 2011 A3's MED17 has no groups
    above 127, for example).
  - Per-group read rates. A value that sits in several groups (engine speed, for example)
    is filled from every group that's read, so it updates more often.
  - Each recording keeps which measuring group every row came from and the raw bytes the
    module sent (a small companion file, not in the CSV). Analyze uses it to show each
    channel at its real read time, so channels from different groups line up, and any log
    can be decoded again if a formula is ever corrected.
  - Every recording goes to Recent logs automatically. Export saves a copy as Standard
    (Datazap CSV), Integrated Engineering, MegaLogViewer or generic CSV, and optional
    auto-export does this when you press Stop.
- **Live View**: gauges and graphs, alarms, saved dashboards and math channels.
- **Analyze** (log playback, inspired by Datazap):
  - Graphs: stacked or split graphs, axis groups, a legend strip, timeline zoom, a Raw
    table, a heatmap and min/max/avg.
  - Working with logs: compare two logs, smart presets, trimming and renaming logs, and
    following a recording live.
- **Dyno** (its own tab): records full-throttle pulls and estimates wheel and engine power
  and torque, using car weight and drag settings.
- **Set up without the car**: every tab except Flash and Patches opens unplugged, so you can
  pick channels and build presets, alarms and layouts. Buttons that need the car (Record,
  Start, Scan, Read) look greyed out, and clicking one says to connect first.
- **Importing logs** from other tools (CSV formats that Datazap reads).
- **Assistant (Ask AI)**: optional. It uses your own AI provider and knows what's on the
  current page.
- **Help**: topics, a troubleshooting page with the activity log and a support bundle, and
  version and update info.
- **Settings**: sections and search, colours and themes, tab style, a Lock option and a
  Demo mode switch.

### Changed and fixed

- **Measuring blocks**: formula 33 now reads `100*B/A`. The original `0.01*A*B` read load,
  throttle, N75 and fuel-pump duty up to 6.5x too high. Formula 34's unit is crank degrees,
  not kW, and formula 31 (lambda) has no unit. Formula 27 (ignition timing) and 4 keep
  their before/after-TDC sign. Formula 36 is the odometer (`A*2560+B*10` km), formula 14 is
  `0.005*A*B` bar, and formulas 80, 90-92, 96, 97, 99 and 102 are decoded. Checked against a
  real car's group read, kw1281test and VCDS logs of a MED17.5.
- **Channel names**: group 122 field 4 is "Transmission torque intervention" (Ross-Tech's
  standard name; it's a status code), and the duplicate "Throttle valve angle" is gone (the
  same reading as Throttle valve position). Older logs open with the corrected names and units
  (lambda without "°C", for example); the files themselves aren't changed.
- **Group 122** is the transmission's torque group. It's no longer treated as a copy of 120.
- **candleLight / gs_usb on macOS**: the adapter can be unplugged and plugged back in
  without restarting the app.
- **Fault-code reads**: if one read service answers with no codes, the next one is tried.
  Modules that report "busy" are retried, and a dropped TP2 channel is reopened.
- **BCM 8P0907063K** is no longer mistaken for a module in its bootloader.
- **Name boxes opened from a menu** (Save preset… and others) take typing again: the closing
  menu no longer steals the keyboard focus.
- `flasher/` is kept in this repo as a plain folder with these fixes, not as a submodule.
- The updater and installers point at this fork.

Status: the new logging, Dyno and hot-plug features have been tested mostly in demo mode
and against a simulator. Testing on the car is ongoing.

## Install

**Windows** (PowerShell, no admin needed):

```powershell
irm https://raw.githubusercontent.com/LTLNMO/PQ35-Flasher-UI/main/install.ps1 | iex
```

**Linux and macOS** (needs git; on macOS run `xcode-select --install` first if you
don't have it):

```bash
curl -LsSf https://raw.githubusercontent.com/LTLNMO/PQ35-Flasher-UI/main/install.sh | sh
```

The installer sets up [uv](https://docs.astral.sh/uv/) and a private Python for the
app. On Windows it also adds a private 32-bit Python for J2534 adapters. It doesn't touch
any Python you already have. It then asks whether to add shortcuts: Start menu and
desktop on Windows, app menu and desktop on Linux, and on macOS an app in `/Applications`
(or `~/Applications` if `/Applications` isn't writable) that shows in Launchpad and Spotlight.
Set `PQ35_YES=1` to answer yes to everything. Run the installer again to update.

From a terminal on Linux/macOS, run `pq35-flasher` (add `--demo` for demo mode). The
installer links it into `~/.local/bin`. On Windows, run `run.cmd` in the install folder.
The app itself lives in the `app` folder inside the install folder; `uv run -m pq35ui`
only works from there (or with `uv run --project <install folder>/app -m pq35ui`).

| | Install folder |
|---|---|
| Windows | `%LOCALAPPDATA%\PQ35-Flasher-UI\app` |
| Linux | `~/.local/share/pq35-flasher-ui/app` |
| macOS | `~/Library/Application Support/PQ35-Flasher-UI/app` |

From a checkout: `git clone --recurse-submodules …`, then `.\install.ps1` or `./install.sh`.

## Updating

The bottom-left corner shows the installed version (and the flasher commit). Press
**Check for updates**: if GitHub has something newer, it lists what changed and offers
**Update now**. That closes the app, runs the installer in update mode (pulls, or
re-downloads a zip install, re-syncs, keeps your shortcuts and data), and reopens the app.
On Windows you see the update in a console window. Updating is blocked while the app is
talking to a module.

Re-running the one-line installer does the same thing by hand.

## Adapters

| Adapter | Windows | Linux | macOS |
|---|---|---|---|
| J2534 (Scanmatik, Tactrix, …) | Install the vendor's driver | Not supported | Not supported |
| CANable / candleLight (gs_usb firmware) | Works out of the box on Windows 10/11 | Shows up as `can0`; pick SocketCAN | gs_usb (untested) |
| SocketCAN | n/a | Brought up from the Connect page | n/a |
| Panda | `uv pip install pandacan` into `.venv` (untested) | same | same |

candleLight firmware tells Windows to use the built-in WinUSB driver, so no Zadig is
needed. Use Zadig (bind WinUSB) only if the adapter doesn't appear, for example because
another driver claimed it earlier. CANables shipped with the older slcan firmware show
up as a COM port and need [candleLight firmware](https://canable.io/updater/) instead.

On Linux, a SocketCAN interface that is down, or not at 500 kbit/s, gets a **Bring up**
button on the Connect page. It runs `ip link set … up type can bitrate 500000` through
`pkexec`, so you're asked for your password.

## Data logger and fault codes

**Find modules** first asks the gateway for its installation list, so each module shows
its workshop address next to the TP2 ID it answers on (AWD 22 is TP2 0x0A), and the
gateway's other modules are listed underneath. Every supported module is still probed
even if the gateway doesn't list it, since the list is the gateway's coding rather than
a live check. On a bench with no gateway, it just probes.

It also finds the engine (0x01) and transmission/TCU (0x02). They're read-only
here: no flashing or backups. Their Diagnostics page reads fault codes, and clears them after
asking (clearing is the one write). Codes show as the 5-digit VAG code, with the P-code
where there is one, and the raw status byte.

**Data logger** (engine, transmission or Haldex, one module at a time): tick named values
(searchable, by category), or add a whole measuring group when a value isn't listed. Pick
a rate and press Start. Values update live, and a CSV goes to
`Documents/PQ35 Flasher/Datalogs`: a `Time` column in seconds, then one "Name (unit)"
column per value, all numeric, values carried forward between reads. That's the layout
Datazap reads for generic CSV logs. Nothing is written to the module while logging.

The named values come from one car's measuring-group sheets (a 2.0T engine, a DSG, Haldex
groups 1/3/5), so on other ECUs some names may not match what's in the group. Raw
groups always work. Fewer groups log faster. The sheets are titled with workshop (VCDS)
addresses; the app talks to TP2 IDs, which the gateway's installation list maps (AWD
22 is TP2 0x0A).

## SGO files (EPS)

Pick or drop an EPS `.sgo` and it's converted to a `.bin` in
`Documents/PQ35 Flasher/Images` first: a full firmware SGO becomes a 384 KiB image, a
steer-dataset SGO becomes a 4 KiB 0x5E dataset. Some dataset SGOs (v074N…) also carry a
0x5D block and two small low-address records; only the 0x5E block is used. Gen2
datasets (firmware below 3000) and partial-code SGOs are refused. Everything after that (checks, patches,
flashing) uses the `.bin`, which goes through exactly the same rules as any other
image. Only PQ35 racks (1K0/8J0 909 144) are accepted; SGOs for other controllers,
such as 5N1 909 144 PQ46 racks, are refused. The converter follows
`unpack_firmware_sgos.py` in [DSparks156x/pq-flasher](https://github.com/DSparks156x/pq-flasher)
(a fork of Willem Melching's pq-flasher, MIT) and reproduces its output byte for byte. Haldex: `.bin` only.

## How it works

```
PySide6 UI (64-bit)  ──JSON lines──▶  worker  ──▶  flasher (submodule)  ──▶  CAN adapter
                                     │
                                     └─ 32-bit worker for J2534 (stdlib only)
```

- The UI never opens the adapter. A worker process owns it, one job at a time, behind a
  lock file, so two windows can't talk to a module at once.
- J2534 DLLs are 32-bit, so on Windows J2534 jobs run in a worker under a 32-bit Python.
  Every other adapter uses the app's own Python.
- `flasher/` is a copy of [PQ35-Flasher](https://github.com/DSparks156x/PQ35-Flasher)
  with local fixes, kept in this repo (not a submodule).

## Safety rails

- **The module must identify as the one you picked** before anything past Connect unlocks.
- **Images are checked when you choose them**: size, internal checksums, which module
  they're for, and whether the part number matches the connected ECU. A Haldex image
  can't be flashed with the EPS selected.
- **Haldex: a backup is required before the first flash**, and goes into a per-module
  library. The full application is the default. Smaller ranges appear under advanced
  options, and only when they cover every byte that differs from the module's last known
  image.
- **EPS: no backup step**, because racks can't be read back over OBD. What can be written
  depends on the firmware the module reports:
  - 0x5E steer dataset: firmware 3000 or newer, and never the factory `000` placeholder.
    It's the default.
  - 0x5D configuration block: only onto the exact firmware version it was built for. It
    becomes the default when the image has a 0x5D patch such as HCA applied.
  - Full firmware: modules on 2901 or newer. Upgrading from older firmware isn't
    understood yet.
  - A full flash of a factory image with the `000` placeholder dataset prompts you to
    flash a real dataset straight after.
  - **Patch…** on the Flash page patches the chosen image before you flash it. The
    choices are HCA timeout/minimum speed, slew, the HCA torque trip and HCA gains,
    whichever fit its firmware.
    The patched copy goes in `Patched` and the chosen file never changes. Patching again
    starts from the original, so it replaces the earlier choice. The default write is the
    block the patches changed. When they change both 0x5D and 0x5E, write the two
    blocks in two flashes.
  - Full EPS flashes are disabled (only Recovery may try one): the loader's erase of the
    application drops the connection, so the flash fails and leaves the rack erased in
    its bootloader.
- **A checklist and a 3-second hold-to-flash** replace typing `YES`. Recovery mode
  needs `RECOVER` typed.
- **While flashing**, the window refuses to close and the computer is kept awake.
- **After a flash**, the worker releases the adapter and identifies the module again from
  scratch. Success is only reported after that.
- Fixing checksums, bench simulator patches, recovery and flashing without a backup are
  hidden under advanced options.

## Where things are kept

Your files go in **Documents/PQ35 Flasher** on every OS:

| Folder | What's in it |
|---|---|
| `Modules/<module>/Backups` | Readouts of that module, one folder each |
| `Modules/<module>/Flashed` | A copy of every image written to it, timestamped |
| `Modules/<module>/history.json` | Backups and flashes, with results |
| `Images` | Default place the file picker opens; keep your firmware here |
| `Patched` | Where patched images are saved by default |
| `Logs` | One log per session; the newest 20 are kept, each capped at 5 MB |
| `Datalogs` | Data logger CSVs (Datazap-friendly) |

A module's folder is named after its family and part number, not its software
version, so backups stay with it across flashes. Demo mode uses `PQ35 Flasher/Demo`.
Settings and lock files live in the per-user app-data folder (`%LOCALAPPDATA%\PQ35-Flasher`,
`~/.local/share/pq35-flasher`, `~/Library/Application Support/PQ35-Flasher`).
**Log → Save support bundle** zips the session log and module history (no firmware).

## Demo mode

Turn on **Demo mode** under **Flasher stuff** (bottom of the sidebar); the app restarts
in demo mode, and turning it off restarts it normally. `python -m pq35ui --demo` does the
same from a terminal. Demo mode adds a "Demo · pretend module" interface that simulates
identify, backup, flash and diagnostics with no hardware. Images still go through the
real flasher checks. The pretend module remembers what you flash to it, and its backups
are kept apart from the real library. To start it from a real image, set
`PQ35_DEMO_HALDEX` / `PQ35_DEMO_EPS` to a 320 KiB / 384 KiB file.

## Development

```bash
uv sync
uv run python -m pq35ui            # the app
uv run python -m pq35ui --demo     # with the pretend module
uv run python -m pq35ui.worker     # a worker on stdin/stdout, for poking at the protocol
```
