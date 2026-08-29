# Getting Started

Dune Chat Monitor is composed of two parts:

```text
collector/collector.py   host-side read-only Text Router log collector
web/                     RedBlink Console addon UI
```

## Development files

The files most commonly changed are:

```text
addon.json          addon metadata and permissions
collector/          chat parsing/export logic
web/index.html      addon markup
web/app.js          chat UI and player identity enrichment
web/style.css       addon styling
install.sh          companion collector installation (Console UI must already be installed)
update.sh           companion collector update only
```

Validate before committing:

```bash
node scripts/validate.js
python3 -m py_compile collector/collector.py
python3 scripts/test_collector_lifecycle.py
bash -n install.sh update.sh uninstall.sh doctor.sh
```

For the current local RedBlink v1.4.3 lifecycle issue, see `redblink-v1.4.3-local-addon-workaround.md`.

## Console ownership and collector lifecycle

The Console is the sole owner of `runtime/addons/installed/dune-chat-monitor/` and `runtime/addons/state.json`. The companion `install.sh`, `update.sh`, and `uninstall.sh` do not deploy/remove UI files and do not edit addon state. The collector reads the Console lifecycle state only as a fail-closed gate: it follows Text Router logs only while the addon is installed and `enabled: true`; it stops the log stream while disabled, skips the entire disabled interval on re-enable, and exits cleanly after the Console addon is uninstalled.

## Chat channels and lazy history

While the Console addon is enabled, the collector stores allowed intercepted `TextChat` channels and exposes channel totals in `status.json`. The UI builds its channel tabs dynamically, so new/unknown channel types do not require a frontend release.

Only the newest `CHAT_PAGE_SIZE` messages are exported in `messages.json`. Older retained messages are exported in fixed-size chunks under `web/live/history/`; the browser loads those chunks only as the user scrolls toward the end of the currently loaded list.

Timestamps are stored/exported in UTC. The web UI formats them in the browser's local timezone, so the addon does not require a configured server timezone.

Private/direct `Whisper` / `Whispers` channel records are explicitly excluded before persistence and are not written to the addon's SQLite database or history exports. On upgrade, legacy Whisper rows from older releases are removed from the addon's SQLite database and the exported history is rebuilt. The normal retention setting applies only to the remaining allowed chat channels.

The UI requests optional identity enrichment only through RedBlink's addon permission bridge (`DuneAddon.request("leadership.players.list")`), which enforces `players:read`. It does not call Console player REST endpoints directly. When the bridge cannot provide a stable Funcom/platform mapping, the UI keeps the Funcom-ID fallback and leaves Steam-specific actions unavailable. For Map chat, the collector also observes Text Router routing keys such as `HaggaBasin.0`. The numeric dimension is stored with the message and resolved through RedBlink's read-only `runtime/scripts/dune sietches dimensions Survival_1 --active-only --labels` command, so Hagga Basin messages can display `Hagga Basin - Sietch Abbir` (or the server's configured Sietch display name). If a Sietch label cannot be resolved, the UI falls back to the map name only.
