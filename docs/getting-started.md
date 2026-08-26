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

## Public-chat privacy boundary

The collector stores only `Map` and `Proximity` messages. Other Text Router chat channels are rejected before SQLite storage/export, and any legacy non-public rows are removed from the addon's own SQLite database when the collector starts.
