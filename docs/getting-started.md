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
install.sh          local host installation
update.sh           local test update
```

Validate before committing:

```bash
node scripts/validate.js
python3 -m py_compile collector/collector.py
bash -n install.sh update.sh uninstall.sh doctor.sh
```

For the current local RedBlink v1.4.3 lifecycle issue, see `redblink-v1.4.3-local-addon-workaround.md`.

## Chat channels and lazy history

The collector stores every intercepted `TextChat` channel and exposes channel totals in `status.json`. The UI builds its channel tabs dynamically, so new/unknown channel types do not require a frontend release.

Only the newest `CHAT_PAGE_SIZE` messages are exported in `messages.json`. Older retained messages are exported in fixed-size chunks under `web/live/history/`; the browser loads those chunks only as the user scrolls toward the end of the currently loaded list.

Because Text Router can include private/direct channels, treat the addon's SQLite database and generated history exports as sensitive administrative data.

The UI resolves sender and whisper-recipient aliases through RedBlink's read-only player API. Player names open an action menu for copying SteamID/Funcom ID/name or opening the public Steam profile. Map chat displays the map inline with the channel badge (for example `MAP - Hagga Basin`) when stored map metadata is available and otherwise uses the current read-only player map as a best-effort fallback.
