# Dune Chat Monitor

A read-only chat monitor addon for the RedBlink Dune: Awakening self-hosted Docker Console.

> Development status: private/experimental. The current branch is intended for testing before public release.

## What it does

Dune Chat Monitor follows the existing `dune-text-router` Docker logs, extracts structured public `TextChat` messages, stores them in its **own SQLite database**, and exposes a lightweight chat UI inside Dune Docker Console. The collector has a strict allowlist and retains only **Map** and **Proximity** chat.

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
- channel tabs with message counts;
- sender search;
- character name as the primary identity;
- SteamID64 in parentheses when RedBlink exposes it;
- Funcom ID fallback when identity resolution is unavailable;
- coordinates only when the message contains a meaningful non-zero origin;
- no permanent server-name or connection-status header;
- collector errors only appear when something is actually wrong.

## Runtime layout

Recommended installation path:

```text
/opt/dune-chat-monitor/
├── collector/
├── config/
│   └── dune-chat-monitor.env
├── data/
│   └── chat.sqlite3
├── logs/
├── web/
├── addon.json
├── install.sh
├── doctor.sh
├── update.sh
└── uninstall.sh
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

For each allowed public `TextChat` message (`Map` or `Proximity`), the collector stores:
- message ID;
- channel type;
- Funcom ID of the sender;
- destination field;
- message text;
- game timestamp;
- coordinates when present;
- spoofed-name metadata;
- the decoded raw inner JSON.

Messages are deduplicated by the game's `m_Id`.

Character names and SteamID64 values are **not copied into the addon SQLite database**. They are resolved by the web UI from RedBlink's read-only player endpoints when the panel is open.

## Privacy

The Text Router may intercept channels beyond Map and Proximity, including private/direct chat. Dune Chat Monitor deliberately **does not store or export those channels**. The collector allowlist accepts only `Map` and `Proximity`, advances its Docker-log cursor past rejected messages, and never logs their text or sender.

On startup, the collector also removes any non-public rows that may exist in its own SQLite database from an older development build. This cleanup affects only `data/chat.sqlite3`; it never writes to Dune game data.

## RedBlink v1.4.3 local-development note

RedBlink v1.4.3 currently marks manually installed addons that are absent from the Community Catalog as `removed`, despite the official local-development workflow. Our private test server uses a temporary, reversible Console compatibility patch only while the addon is not yet catalogued.

See `docs/redblink-v1.4.3-local-addon-workaround.md`. This workaround is **not part of the addon runtime** and must not be shipped as a permanent RedBlink modification.
