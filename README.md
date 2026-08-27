# Dune Chat Monitor

A read-only chat monitor addon for the RedBlink Dune: Awakening self-hosted Docker Console.

> Development status: public beta. Tested with RedBlink Dune Docker Console; feedback and compatibility reports are welcome.

## What it does

Dune Chat Monitor follows the existing `dune-text-router` Docker logs, extracts structured `TextChat` messages, stores them in its **own SQLite database**, and exposes a lightweight chat UI inside Dune Docker Console. All Text Router chat channel types are retained and the UI creates channel tabs dynamically as channels appear.

The collector does **not** consume RabbitMQ queues and does **not** connect to or write to the Dune PostgreSQL database.

For display-only player identity enrichment, the web UI requests RedBlink's existing read-only player API (`players:read`) so a Funcom chat identity can be shown as the in-game character name and, when available, SteamID64. The addon performs no SQL and stores no Dune database credentials.

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
                         +--> read-only player identity lookup (players:read)
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
- channel tabs for Map, Proximity, Guild, Faction, Party and Whispers, plus any additional channel types discovered at runtime, with total message counts;
- sender search;
- character name as the primary identity; clicking a player name opens a compact action menu to copy SteamID, open the Steam profile, copy Funcom ID, or copy the player name;
- whisper messages show the resolved recipient when RedBlink can map the destination identity;
- Map messages show the map context when it is available from the chat payload/routing metadata, with a read-only player-map fallback for current players;
- Funcom ID fallback when identity resolution is unavailable;
- coordinates only when the message contains a meaningful non-zero origin;
- a compact **Live** status badge without a permanent server-name header;
- collector errors only appear when something is actually wrong;
- the newest messages load first, while older history is fetched incrementally as the user scrolls down.

## Runtime layout

Recommended installation path:

```text
/opt/dune-chat-monitor/
â”œâ”€â”€ collector/
â”œâ”€â”€ config/
â”‚   â””â”€â”€ dune-chat-monitor.env
â”œâ”€â”€ data/
â”‚   â””â”€â”€ chat.sqlite3
â”œâ”€â”€ logs/
â”œâ”€â”€ web/
â”œâ”€â”€ addon.json
â”œâ”€â”€ install.sh
â”œâ”€â”€ doctor.sh
â”œâ”€â”€ update.sh
â””â”€â”€ uninstall.sh
```

The RedBlink Console receives only the installed addon copy under:

```text
<DUNE_ROOT>/runtime/addons/installed/dune-chat-monitor/
```

## Install

Clone the repository, then:

```bash
./install.sh
```

The installer:
1. detects the RedBlink Dune installation;
2. asks only for local runtime values such as timezone/retention;
3. installs/enables the Console UI addon;
4. approves the read-only `players:read` addon permission for the manual/local install;
5. installs a `dune-chat-monitor.service` systemd service;
6. starts the collector.

No server name is required.

## Diagnose

```bash
./doctor.sh
sudo journalctl -u dune-chat-monitor -n 100 --no-pager
```

## Update

```bash
git pull --ff-only
./update.sh
```

## Uninstall

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

For each intercepted `TextChat` message, regardless of channel type, the collector stores:
- message ID;
- channel type;
- Funcom ID of the sender;
- destination field;
- message text;
- game timestamp;
- map metadata when present/recoverable from the chat payload or Text Router routing values;
- coordinates when present;
- spoofed-name metadata;
- the decoded raw inner JSON.

Messages are deduplicated by the game's `m_Id`.

Character names and SteamID64 values are **not copied into the addon SQLite database**. They are resolved by the web UI from RedBlink's read-only player endpoints when the panel is open.

## Chat retention and privacy

The Text Router may expose multiple channel types, including private/direct chat such as whispers. Dune Chat Monitor now stores **all intercepted `TextChat` channels** in its own SQLite database so the monitor can reproduce the complete server chat stream. Server owners should treat `data/chat.sqlite3` and the generated `web/live/` history files as sensitive administrative data and restrict access accordingly.

The addon still never writes to Dune game data, never consumes Dune queues, and never copies resolved character names or SteamID64 values into its SQLite database.

History is retained according to `CHAT_RETENTION_DAYS`. The browser initially loads only the latest `CHAT_PAGE_SIZE` messages; older history is split into fixed-size chunks and fetched lazily while scrolling.

## RedBlink v1.4.3 local-development note

RedBlink v1.4.3 currently marks manually installed addons that are absent from the Community Catalog as `removed`, despite the official local-development workflow. A temporary, reversible Console compatibility patch may be required during local development while the addon is not yet catalogued.

See `docs/redblink-v1.4.3-local-addon-workaround.md`. This workaround is **not part of the addon runtime** and must not be shipped as a permanent RedBlink modification.
