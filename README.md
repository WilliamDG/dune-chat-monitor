# Dune Chat Monitor

A read-only chat monitor addon for the RedBlink Dune: Awakening self-hosted Docker Console.

> Development status: public beta. Tested with RedBlink Dune Docker Console; feedback and compatibility reports are welcome.

## What it does

Dune Chat Monitor follows the existing `dune-text-router` Docker logs, extracts structured `TextChat` messages, stores allowed server-chat channels in its **own SQLite database**, and exposes a lightweight chat UI inside Dune Docker Console. Private/direct **Whispers are explicitly excluded before persistence**, while the UI creates tabs dynamically for the remaining channels as they appear.

The collector does **not** consume RabbitMQ queues and does **not** connect to or write to the Dune PostgreSQL database. For Hagga Basin Map chat only, it uses RedBlink's existing read-only `sietches dimensions` CLI command to translate a routed dimension such as `HaggaBasin.0` into the configured Sietch display name; the result is cached and stored with the chat message.

For display-only player identity enrichment, the web UI uses RedBlink's addon permission bridge via `DuneAddon.request("leadership.players.list")`. The Console enforces the declared `players:read` permission. The UI never calls Console player REST endpoints directly. When the bridge does not expose a stable Funcom/platform mapping, the monitor falls back to the Funcom identity already present in chat instead of bypassing the bridge. The addon performs no SQL and stores no Dune database credentials.

## Architecture

```text
dune-text-router Docker logs
        |
        v
collector/collector.py
        |
        +--> data/chat.sqlite3
        |
        +--> <DUNE_ROOT>/runtime/addons/installed/dune-chat-monitor/web/live/*.json
                         |
                         v
                 RedBlink Console iframe
                         |
                         +--> DuneAddon permission bridge (players:read)
```

This avoids:
- consuming or competing with Dune RabbitMQ queues;
- creating RabbitMQ users, queues, or bindings;
- storing RabbitMQ administrative credentials;
- mounting the Docker socket into another container;
- direct SQL or database credentials in the addon;
- writing to Dune game data.

## UI

The chat panel is intentionally compact and Console-like:
- channel tabs for Map, Proximity, Guild, Faction and Party, plus any additional non-Whisper channel types discovered at runtime, with total message counts;
- sender search;
- Funcom chat identity is always available; character-name and SteamID enrichment is best-effort through the permission bridge. Clicking the displayed identity opens a compact action menu; Steam actions are disabled when the bridge does not provide a SteamID;
- Map messages use Text Router routing metadata to preserve the message dimension; Hagga Basin chat is shown as `Hagga Basin - <Sietch display name>` when RedBlink can resolve that active dimension (for example `Hagga Basin - Sietch Abbir`), with map-only fallback when the Sietch label is unavailable;
- Funcom ID fallback when identity resolution is unavailable;
- coordinates only when the message contains a meaningful non-zero origin;
- a compact **Live** status badge without a permanent server-name header;
- collector errors only appear when something is actually wrong;
- the newest messages load first, while older history is fetched incrementally as the user scrolls down.

## Runtime layout

Recommended installation path:

```text
/opt/dune-chat-monitor/
|-- collector/
|-- config/
|   `-- dune-chat-monitor.env
|-- data/
|   `-- chat.sqlite3
|-- logs/
|-- web/
|-- addon.json
|-- install.sh
|-- doctor.sh
|-- update.sh
`-- uninstall.sh
```

The RedBlink Console owns the installed addon package under:

```text
<DUNE_ROOT>/runtime/addons/installed/dune-chat-monitor/
```

## Install

Install **Dune Chat Monitor** from Dune Docker Console first. Then clone this source repository on the host and install only the companion collector:

```bash
./install.sh
```

The companion installer:
1. detects the RedBlink Dune installation and verifies that the Console addon package already exists;
2. uses generic runtime defaults (`30` retention days, `50` messages per history page) without asking for server-specific values;
3. does **not** copy, replace, remove, or mutate the Console-owned addon directory or `runtime/addons/state.json`;
4. leaves addon enablement and `players:read` approval entirely to Dune Docker Console / the administrator;
5. installs a `dune-chat-monitor.service` systemd service with `Restart=on-failure`;
6. starts the companion collector.

The collector is lifecycle-gated by the Console state in read-only mode. While the addon is disabled it closes the `docker logs --follow` stream and collects nothing. Messages produced during the disabled interval are deliberately skipped rather than backfilled after re-enable. If the Console addon is uninstalled, the collector exits cleanly after a short grace period and systemd does not respawn it.

No server name or timezone is required. Chat timestamps are stored in UTC and displayed using the browser's local timezone. Runtime defaults can be changed later in `config/dune-chat-monitor.env`.

## Diagnose

```bash
./doctor.sh
sudo journalctl -u dune-chat-monitor -n 100 --no-pager
```

## Update

Update the Console UI through Dune Docker Console. To update only the host-side companion collector source:

```bash
git pull --ff-only
./update.sh
```

`update.sh` never deploys UI files and never changes Console addon state.

## Uninstall

The commands below uninstall only the host-side companion collector. They intentionally leave the Console addon package and Console addon state untouched; disable or uninstall the UI separately in Dune Docker Console.

Preserve configuration and chat data:

```bash
./uninstall.sh
```

Remove the SQLite database as well:

```bash
./uninstall.sh --purge-data
```

Remove both data and local configuration:

```bash
./uninstall.sh --purge-all
```

## Stored chat fields

For each intercepted player `TextChat` message, regardless of channel type, the collector stores the fields below. Internal UI localization notifications such as `UI/GuildNotification_*` are filtered and are not treated as player chat:
- message ID;
- channel type;
- Funcom ID of the sender;
- destination field;
- message text;
- game timestamp;
- map metadata when present/recoverable from the chat payload or Text Router routing values;
- Map routing key and dimension when Text Router exposes them;
- the Hagga Basin Sietch display name resolved through RedBlink's read-only `runtime/scripts/dune sietches dimensions Survival_1 --active-only --labels` command;
- coordinates when present;
- spoofed-name metadata;
- the decoded raw inner JSON.

Messages are deduplicated by the game's `m_Id`.

Character names and SteamID64 values are **not copied into the addon SQLite database**. Any available identity enrichment is requested only through RedBlink's `players:read` addon permission bridge while the panel is open.

## Chat retention and privacy

The Text Router may expose private/direct chat such as **Whispers**. Dune Chat Monitor explicitly excludes `Whisper` / `Whispers` channel records **before persistence**, so they are not stored in the addon SQLite database and are not exported to `web/live/`. When upgrading from a release that previously retained Whispers, the collector deletes those legacy rows from its own SQLite database and rebuilds history exports. The configured `CHAT_RETENTION_DAYS` applies only to the remaining allowed chat channels. Server owners should still treat `data/chat.sqlite3` and generated history files as administrative data and restrict access accordingly.

The addon still never writes to Dune game data, never consumes Dune queues, and never copies resolved character names or SteamID64 values into its SQLite database.

History is retained according to `CHAT_RETENTION_DAYS`. The browser initially loads only the latest `CHAT_PAGE_SIZE` messages; older history is split into fixed-size chunks and fetched lazily while scrolling.

## RedBlink v1.4.3 local-development note

RedBlink v1.4.3 currently marks manually installed addons that are absent from the Community Catalog as `removed`, despite the official local-development workflow. A temporary, reversible Console compatibility patch may be required during local development while the addon is not yet catalogued.

See `docs/redblink-v1.4.3-local-addon-workaround.md`. This workaround is **not part of the addon runtime** and must not be shipped as a permanent RedBlink modification.
