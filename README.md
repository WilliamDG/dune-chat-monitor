# Dune Chat Monitor

A read-only chat monitor addon for the RedBlink Dune: Awakening self-hosted Docker Console.

> Development status: private/experimental. The current branch is intended for testing before public release.

## Architecture

Dune Chat Monitor does **not** consume RabbitMQ queues and does **not** access the Dune game database.

The collector runs as a small host-side systemd service and follows the existing `dune-text-router` Docker logs. It stores only lines emitted by the Text Router as:

```text
[... INF CLOG] Intercepted message ...
```

Those lines already contain the structured `TextChat` payload.

Flow:

```text
dune-text-router Docker logs
        |
        v
collector/collector.py
        |
        +--> data/chat.sqlite3
        |
        +--> Dune Console addon web/live/*.json
```

This avoids:
- consuming or competing with Dune RabbitMQ queues;
- creating RabbitMQ users, queues, or bindings;
- storing RabbitMQ administrative credentials;
- accessing or modifying the Dune PostgreSQL database;
- mounting the Docker socket into another container.

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

The RedBlink Console receives only the installed UI copy under:

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
2. writes local configuration;
3. installs/enables the Console UI addon;
4. installs a `dune-chat-monitor.service` systemd service;
5. starts the collector.

The installer does not modify RabbitMQ or the Dune database.

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

For each intercepted `TextChat` message, the collector currently stores:
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

## Privacy

The Text Router may intercept channels beyond Map and Proximity depending on the game/server version. Administrators should decide which channels they intend to retain and expose before deploying the addon to other users.
