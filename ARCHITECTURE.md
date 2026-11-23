# How PQ35 Flasher UI is built

A map of the code for anyone changing it. The README says what the app does; this says where
things are and the few rules that keep it consistent.

## The three parts

```
┌──────────────────────────────┐   JSON lines    ┌──────────────────────────────┐
│ pq35ui/app   (the window)    │ ◄─────────────► │ pq35ui/worker  (a process)   │
│ PySide6. Never touches CAN.  │   stdin/stdout  │ Owns the CAN adapter. Stdlib │
│ Pages, state, logs, AI.      │                 │ only. Scans, live reads,     │
└──────────────────────────────┘                 │ flashing, demo car.          │
                                                 └──────────────┬───────────────┘
                                                                │ imports
                                                 ┌──────────────▼───────────────┐
                                                 │ flasher/   (Drew's flasher)  │
                                                 │ KWP2000 / TP2.0 / UDS, BSL,  │
                                                 │ measuring groups + formulas  │
                                                 └──────────────────────────────┘
```

- **The worker is a separate process** (`app/client.py` starts it; `worker/server.py` runs it). A
  stuck adapter can't freeze the window, and J2534 adapters get a 32-bit worker next to the
  normal one. Messages: `worker/protocol.py`, one JSON object per line.
- **`flasher/`** is Drew's flasher as a plain folder (not a submodule), with this fork's fixes.
  The worker imports it; the window never does.
- **Demo mode** (`--demo`): `worker/demo.py` and `worker/simcar.py` pretend to be a car, so every
  page works with no hardware. Tests use it.

## The window (`pq35ui/app`)

| Area | Files |
|---|---|
| Start-up, window, tab nav | `main.py`, `main_window.py` (NAV lists the tabs), `mac_shell.py` (Mac sidebar, header, layout rules) |
| Shared state | `state.py` (`AppState`: adapter, module, ECU, busy, signals), `storage.py` (folders, `Settings`) |
| Pages | `home_page.py`, `projects.py`, `pages.py` (Connect, Back up, Patches, Diagnostics), `flash_page.py`, `liveview_page.py`, `logger_page.py`, `playback_page.py` + `playback_*.py` (Analyze, one file per section), `dyno_page.py`, `maps_page.py`, `help_page.py`, `settings_page.py` |
| Page building blocks | `page_frame.py` (row 1 + title-bar action), `toolbar.py` (`ToolIcon`, toolbar sizes, Record/Start button), `menus.py`, `widgets.py` (incl. the app's one event filter, overlay scroll bars), `channel_list.py` / `channel_sidebar.py` (channel lists and Select channels) |
| Logs | `log_writer.py` (writing a log as it records; no Qt), `log_info.py` (per-recording companion), `recent_logs.py`, `log_import.py` / `log_export.py` (other tools' formats), `live_data.py` (Live View history; no Qt) |
| Analysis | `dyno.py`, `log_digest.py`, `smart_presets.py`, `mathchannels.py`, `heatmap.py`, `split_view.py`, `graphscale.py`, `graphstyle.py` |
| AI | `ai.py` (providers, sending, consent), `assistant.py` (⌘K chat), `log_compare_ai.py` |
| Look | `theme.py` (colours, Classic and Mac stylesheets, Mac icon sizes), `sf_symbols.py` (SF Symbols on Mac) |

### Two interfaces, one structure

Settings → Appearance → Interface: **Classic** (top tab bar) or **Mac** (sidebar, title bar).
`theme.is_mac()` decides. Each page is **built once for the interface in use**: a page creates its
controls, then places them once (`page_frame()`, or a page's own `_classic_*` / `_mac_*` placement).
Pages never build the Classic layout and then rearrange it for Mac; that pattern caused most
alignment bugs and has been removed.

### Rules the code enforces

- **One toolbar button class.** Every toolbar icon is a `toolbar.ToolIcon`; `mac_shell.row_icons()`
  refuses anything else. Right-hand icons go in one order: the page's tools, Recent, Presets,
  settings, ⋯ (`row_icons(tools=, recent=, presets=, settings=, more=)`).
- **Row 1 via `page_frame()`**: a page declares its main action, left controls, a stretch label
  and right icons by role; the frame places them for either interface.
- **Mac layout rules** (`mac_shell.py`, "LAYOUT RULES"): one row height, content margins, the
  content line under row 1, side columns, the icon grid. `tests/test_layout.py` checks every tab.
- **One app-wide event filter** (`widgets.AppEventFilter`). Each extra one makes every event in the
  app (repaints, mouse moves) cross into Python again. Add handlers with
  `widgets.app_filter().add(handler, event_types)`; `tests/test_performance.py` fails otherwise.
- **Theme callbacks**: `theme.on_apply(widget.method)` is dropped when the widget is destroyed.
- **Settings**: read as `settings.get("key", default)`; a key read in several places must use
  one default (`tests/test_settings_keys.py`).

## Tests

```
.venv/bin/python -m unittest discover -s pq35ui/tests -t .        # the app (offscreen, ~45 s)
cd flasher && ../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Every push runs both on GitHub (`.github/workflows/tests.yml`).

- `test_layout.py`: builds the real window in both interfaces at 72 dpi and checks every tab:
  no sideways scrolling, icon sizes and grid, the row-1 and content lines, the bottom margin.
- `layout_tools.py`: `snapshot NAME` / `diff A B` saves every tab's geometry and a screenshot,
  then compares them. Use it around UI refactors to prove nothing moved.
- `test_performance.py`: Live View's cost per reading, one app-wide filter, window build time.
- `test_log_writer.py`, `test_live_data.py`, `test_log_info.py`, `test_dyno.py`,
  `test_smart_presets.py`, `test_projects.py`: logic, without a window.
- Tests never call the app's entry point and always isolate `HOME`, `PQ35_USER_DIR` and
  `PQ35_DATA_DIR` (`layout_tools.isolate()`), so they can't touch real settings, logs or the lock file.

## Where things are kept on disk

| | macOS |
|---|---|
| App code | `~/Library/Application Support/PQ35-Flasher-UI/app` |
| Settings, lock files | `~/Library/Application Support/PQ35-Flasher` |
| Modules, Datalogs, Projects, Scans, Images | `~/Documents/PQ35 Flasher` (demo mode: its `Demo` folder) |

`storage.py` has the details and the Windows / Linux equivalents.

## Adding a page

1. Write the page class; build its controls once.
2. Place row 1 with `page_frame(self, layout, action=…, left=…, tools=…, more=…)`.
3. Add it to `NAV` in `main_window.py`.
4. Run `tests/test_layout.py`; take a `layout_tools` snapshot if you changed shared parts.
