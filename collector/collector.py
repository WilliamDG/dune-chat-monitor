#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import select
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


RETENTION_DAYS = max(1, int(os.environ.get("CHAT_RETENTION_DAYS", "30")))
PAGE_SIZE = max(10, int(os.environ.get("CHAT_PAGE_SIZE", "50")))
BOOTSTRAP_SINCE = os.environ.get("CHAT_BOOTSTRAP_SINCE", "1h").strip() or "1h"

TEXT_ROUTER_CONTAINER = os.environ.get("TEXT_ROUTER_CONTAINER", "dune-text-router")
DOCKER_BIN = os.environ.get("DOCKER_BIN", "/usr/bin/docker")

DUNE_ROOT_VALUE = os.environ.get("DUNE_ROOT", "").strip()
DUNE_ROOT = Path(DUNE_ROOT_VALUE).expanduser() if DUNE_ROOT_VALUE else None
DUNE_CLI = (DUNE_ROOT / "runtime" / "scripts" / "dune") if DUNE_ROOT else None
SIETCH_CACHE_TTL_SECONDS = 5 * 60

ADDON_ID = "dune-chat-monitor"
ADDON_STATE_PATH = (DUNE_ROOT / "runtime" / "addons" / "state.json") if DUNE_ROOT else None
ADDON_MANIFEST_PATH = (
    DUNE_ROOT / "runtime" / "addons" / "installed" / ADDON_ID / "addon.json"
) if DUNE_ROOT else None
LIFECYCLE_POLL_SECONDS = 1.0
UNINSTALLED_GRACE_SECONDS = 10.0

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", str(PROJECT_DIR / "data" / "chat.sqlite3")))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", str(PROJECT_DIR / "web" / "live")))

CLOG_RE = re.compile(
    r"\[\d{2}:\d{2}:\d{2}\s+\d+\s+INF\s+CLOG\]\s+"
    r"Intercepted message from (.+?) to (.+?):\s+(\{.*\})\s*$"
)

ROUTED_CLOG_RE = re.compile(
    r"\[\d{2}:\d{2}:\d{2}\s+\d+\s+INF\s+CLOG\]\s+"
    r"Redirected message from (.+?) to (.+?) using routing key ([^:]+):\s+(\{.*\})\s*$"
)

DOCKER_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s(?P<line>.*)$"
)

GAME_TS_FORMAT = "%Y.%m.%d-%H.%M.%S"

# Internal localization/UI notifications can be emitted through Text Router as
# TextChat records even though they are not player chat. Keep these out of the
# monitor so they do not appear as fake Guild messages or inflate counters.
INTERNAL_UI_NOTIFICATION_PREFIXES = (
    "ui/guildnotification_",
)

# Private/direct Whispers are intentionally excluded from collection. They may
# appear in Text Router logs, but the addon must not persist or export them.
EXCLUDED_PRIVATE_CHANNELS = frozenset((
    "whisper",
    "whispers",
))

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")




def parse_game_timestamp(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    try:
        dt = datetime.strptime(value, GAME_TS_FORMAT).replace(tzinfo=timezone.utc)
        return iso_utc(dt), None
    except Exception:
        return value, value


def ensure_local_dirs() -> None:
    # The companion collector owns only its local data/config tree. The Console
    # owns runtime/addons/installed/<addon-id>, so never create that tree while
    # the addon is disabled or uninstalled.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def ensure_export_dir() -> None:
    # Called only after addon_collection_state() confirms that the Console
    # addon exists and is enabled. Never create missing Console-owned parents:
    # if the package disappears during uninstall/update, fail closed instead of
    # recreating runtime/addons/installed/<addon-id>.
    if addon_collection_state()[0] != "enabled":
        raise AddonCollectionPaused("Console addon is not enabled for export")
    if not EXPORT_DIR.parent.is_dir():
        raise AddonCollectionPaused("Console addon web directory is unavailable")
    EXPORT_DIR.mkdir(exist_ok=True)


def addon_collection_state() -> tuple[str, str]:
    """Return (state, reason) for the Console-managed addon lifecycle.

    States:
      enabled  -> collection may run
      disabled -> addon exists but collection must stay paused
      missing  -> addon/state entry is absent; treat as uninstall candidate
      invalid  -> lifecycle metadata cannot be trusted; fail closed
    """
    if not DUNE_ROOT or not ADDON_STATE_PATH or not ADDON_MANIFEST_PATH:
        return "invalid", "DUNE_ROOT is unavailable; collection is paused"

    if not ADDON_MANIFEST_PATH.is_file():
        return "missing", "Console addon package is not installed"

    if not ADDON_STATE_PATH.is_file():
        return "missing", "Console addon state entry is unavailable"

    try:
        payload = json.loads(ADDON_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return "invalid", f"Console addon state could not be parsed: {exc}"

    if not isinstance(payload, dict):
        return "invalid", "Console addon state is not a JSON object"

    addon = payload.get(ADDON_ID)
    if not isinstance(addon, dict):
        return "missing", "Console addon state entry is missing"

    lifecycle = str(addon.get("lifecycle") or "").strip().lower()
    if lifecycle in {"unsupported", "removed", "blocked"}:
        return "disabled", f"Console addon lifecycle is {lifecycle}"

    if addon.get("enabled") is not True:
        return "disabled", "Console addon is disabled; collection is paused"

    return "enabled", "Console addon is enabled"


class AddonCollectionPaused(RuntimeError):
    pass


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            received_at TEXT NOT NULL,
            docker_timestamp TEXT,
            game_timestamp_utc TEXT,
            game_timestamp_local TEXT,
            channel TEXT NOT NULL,
            map_name TEXT,
            routing_key TEXT,
            map_dimension INTEGER,
            sietch_name TEXT,
            funcom_id_from TEXT,
            username_to TEXT,
            message TEXT,
            use_spoofed_username INTEGER NOT NULL DEFAULT 0,
            spoofed_username TEXT,
            origin_x REAL,
            origin_y REAL,
            origin_z REAL,
            interceptor_source TEXT,
            interceptor_target TEXT,
            outer_type TEXT,
            raw_inner_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_received_at
            ON messages(received_at DESC);

        CREATE INDEX IF NOT EXISTS idx_messages_channel
            ON messages(channel);

        CREATE INDEX IF NOT EXISTS idx_messages_funcom
            ON messages(funcom_id_from);

        CREATE TABLE IF NOT EXISTS collector_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    # Schema migrations for existing addon databases.
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(messages)")}
    if "map_name" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN map_name TEXT")
    if "routing_key" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN routing_key TEXT")
    if "map_dimension" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN map_dimension INTEGER")
    if "sietch_name" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN sietch_name TEXT")

    # Backfill map metadata for existing rows when it can be recovered from
    # the preserved raw chat payload or Text Router source/target strings.
    rows = conn.execute(
        "SELECT id, raw_inner_json, interceptor_source, interceptor_target, map_name FROM messages"
    ).fetchall()
    for row in rows:
        if str(row["map_name"] or "").strip():
            continue
        try:
            inner = json.loads(row["raw_inner_json"] or "{}")
        except Exception:
            inner = {}
        recovered = extract_map_name(inner, row["interceptor_source"], row["interceptor_target"])
        if recovered:
            conn.execute("UPDATE messages SET map_name = ? WHERE id = ?", (recovered, row["id"]))

    removed_internal = purge_internal_ui_notifications(conn)
    if removed_internal:
        print(
            f"[CLEANUP] removed {removed_internal} internal UI notification row(s)",
            flush=True,
        )

    removed_private = purge_excluded_private_chat(conn)
    if removed_private:
        print(
            f"[CLEANUP] removed {removed_private} excluded private Whisper row(s)",
            flush=True,
        )

    conn.commit()
    return conn


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM collector_state WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO collector_state(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def atomic_json(path: Path, payload: Any) -> None:
    # Export parents are prepared only while the Console addon is enabled. Do
    # not recreate them here: they may have disappeared because the Console
    # addon was disabled, updated, or uninstalled.
    if addon_collection_state()[0] != "enabled":
        raise AddonCollectionPaused("Console addon is not enabled for export")
    if not path.parent.is_dir():
        raise AddonCollectionPaused(f"Export directory is unavailable: {path.parent}")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def message_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["message_id"],
        "receivedAt": row["received_at"],
        "dockerTimestamp": row["docker_timestamp"],
        "gameTimestampUtc": row["game_timestamp_utc"],
        "gameTimestampLocal": row["game_timestamp_local"],
        "channel": row["channel"],
        "mapName": row["map_name"],
        "routingKey": row["routing_key"],
        "mapDimension": row["map_dimension"],
        "sietchName": row["sietch_name"],
        "from": row["funcom_id_from"],
        "to": row["username_to"],
        "message": row["message"],
        "spoofed": bool(row["use_spoofed_username"]),
        "spoofedUsername": row["spoofed_username"],
        "origin": {
            "x": row["origin_x"],
            "y": row["origin_y"],
            "z": row["origin_z"],
        },
        "interceptorSource": row["interceptor_source"],
        "interceptorTarget": row["interceptor_target"],
        "type": row["outer_type"],
    }


def message_select_sql(where: str = "") -> str:
    return f"""
        SELECT
            id,
            message_id,
            received_at,
            docker_timestamp,
            game_timestamp_utc,
            game_timestamp_local,
            channel,
            map_name,
            routing_key,
            map_dimension,
            sietch_name,
            funcom_id_from,
            username_to,
            message,
            use_spoofed_username,
            spoofed_username,
            origin_x,
            origin_y,
            origin_z,
            interceptor_source,
            interceptor_target,
            outer_type
        FROM messages
        {where}
    """


def channel_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT channel, COUNT(*) AS total
        FROM messages
        GROUP BY channel
        ORDER BY lower(channel), channel
        """
    ).fetchall()
    return [
        {"name": str(row["channel"]), "count": int(row["total"] or 0)}
        for row in rows
        if str(row["channel"] or "").strip()
    ]


def status_payload(
    conn: sqlite3.Connection,
    *,
    connected: bool,
    error: str | None = None,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            MAX(received_at) AS last_received
        FROM messages
        """
    ).fetchone()

    return {
        "source": f"docker-logs:{TEXT_ROUTER_CONTAINER}",
        "collectorConnected": connected,
        "messageCount": int(row["total"] or 0),
        "lastReceivedAt": row["last_received"],
        "updatedAt": iso_utc(),
        "channels": channel_summary(conn),
        "error": error,
    }


def history_bucket_number(row_id: int) -> int:
    return max(0, (int(row_id) - 1) // PAGE_SIZE)


def history_file_name(bucket: int) -> str:
    return f"chunk-{int(bucket):08d}.json"


def history_bucket_bounds(bucket: int) -> tuple[int, int]:
    start_id = int(bucket) * PAGE_SIZE + 1
    end_id = start_id + PAGE_SIZE - 1
    return start_id, end_id


def history_index_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id FROM messages ORDER BY id DESC"
    ).fetchall()

    buckets: dict[int, dict[str, int]] = {}
    for row in rows:
        row_id = int(row["id"])
        bucket = history_bucket_number(row_id)
        current = buckets.setdefault(
            bucket,
            {"count": 0, "minId": row_id, "maxId": row_id},
        )
        current["count"] += 1
        current["minId"] = min(current["minId"], row_id)
        current["maxId"] = max(current["maxId"], row_id)

    payload_buckets = []
    for bucket in sorted(buckets, reverse=True):
        meta = buckets[bucket]
        payload_buckets.append(
            {
                "key": str(bucket),
                "file": history_file_name(bucket),
                "count": meta["count"],
                "minId": meta["minId"],
                "maxId": meta["maxId"],
            }
        )

    return {
        "updatedAt": iso_utc(),
        "pageSize": PAGE_SIZE,
        "buckets": payload_buckets,
    }


def export_history_index(conn: sqlite3.Connection) -> None:
    atomic_json(EXPORT_DIR / "history" / "index.json", history_index_payload(conn))


def export_history_bucket(conn: sqlite3.Connection, bucket: int) -> None:
    start_id, end_id = history_bucket_bounds(bucket)
    rows = conn.execute(
        message_select_sql("WHERE id BETWEEN ? AND ? ORDER BY id DESC"),
        (start_id, end_id),
    ).fetchall()
    path = EXPORT_DIR / "history" / history_file_name(bucket)
    if not rows:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    atomic_json(
        path,
        {
            "bucket": str(bucket),
            "updatedAt": iso_utc(),
            "messages": [message_payload(row) for row in rows],
        },
    )


def rebuild_history(conn: sqlite3.Connection) -> None:
    if addon_collection_state()[0] != "enabled":
        raise AddonCollectionPaused("Console addon is not enabled for history export")
    history_dir = EXPORT_DIR / "history"
    if not EXPORT_DIR.is_dir():
        raise AddonCollectionPaused("Console addon live export directory is unavailable")
    history_dir.mkdir(exist_ok=True)
    for path in history_dir.glob("*.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    index = history_index_payload(conn)
    for bucket in index["buckets"]:
        export_history_bucket(conn, int(bucket["key"]))
    atomic_json(history_dir / "index.json", index)


def export_messages(
    conn: sqlite3.Connection,
    connected: bool,
    error: str | None = None,
    *,
    history_bucket: int | None = None,
) -> None:
    rows = conn.execute(
        message_select_sql("ORDER BY id DESC LIMIT ?"),
        (PAGE_SIZE,),
    ).fetchall()

    messages = [message_payload(row) for row in rows]

    atomic_json(
        EXPORT_DIR / "messages.json",
        {
            "updatedAt": iso_utc(),
            "pageSize": PAGE_SIZE,
            "messages": messages,
        },
    )
    atomic_json(
        EXPORT_DIR / "status.json",
        status_payload(conn, connected=connected, error=error),
    )

    if history_bucket is not None:
        export_history_bucket(conn, history_bucket)
        export_history_index(conn)

def cleanup_old(conn: sqlite3.Connection) -> None:
    cutoff = now_utc() - timedelta(days=RETENTION_DAYS)
    conn.execute(
        "DELETE FROM messages WHERE received_at < ?",
        (iso_utc(cutoff),),
    )
    conn.commit()


def safe_preview(value: str | None, limit: int = 120) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


MAP_FIELD_CANDIDATES = (
    "m_MapName",
    "m_Map",
    "m_WorldName",
    "m_LevelName",
    "m_OriginMapName",
    "m_OriginMap",
    "MapName",
    "mapName",
    "map",
)

MAP_ROUTE_RE = re.compile(
    r"(?i)(DeepDesert_\d+|Survival_\d+|HaggaBasin(?:\.\d+)?|Social_\d+|Overmap(?:_\d+)?)"
)


def _map_candidate(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("Name", "name", "MapName", "mapName", "m_MapName", "Value", "value"):
            candidate = _map_candidate(value.get(key))
            if candidate:
                return candidate
    return None


def extract_map_name(inner: dict[str, Any], source: Any = None, target: Any = None) -> str | None:
    """Best-effort map extraction without querying the Dune database.

    Current game builds may expose the map directly in the TextChat payload,
    while others only reveal a map-shaped routing/source value. We preserve the
    raw value and let the UI turn known internal map names into friendly labels.
    """
    for key in MAP_FIELD_CANDIDATES:
        candidate = _map_candidate(inner.get(key))
        if candidate:
            return candidate

    for container_key in ("m_Context", "m_Origin", "m_Source", "Context", "Origin", "Source"):
        container = inner.get(container_key)
        if isinstance(container, dict):
            for key in MAP_FIELD_CANDIDATES:
                candidate = _map_candidate(container.get(key))
                if candidate:
                    return candidate

    for value in (source, target):
        text = str(value or "").strip()
        if not text:
            continue
        match = MAP_ROUTE_RE.search(text)
        if match:
            return match.group(1)

    return None


ROUTING_MAP_RE = re.compile(
    r"(?i)^(?P<map>HaggaBasin|Survival_\d+|DeepDesert_\d+|Social_\d+|Overmap(?:_\d+)?)"
    r"(?:\.(?:dim_?)?(?P<dimension>\d+))?$"
)

_sietch_label_cache: dict[str, tuple[float, dict[int, str]]] = {}


def route_map_context(routing_key: str | None) -> tuple[str | None, int | None]:
    raw = str(routing_key or "").strip()
    if not raw:
        return None, None
    match = ROUTING_MAP_RE.match(raw)
    if not match:
        return None, None
    map_name = match.group("map")
    dimension_raw = match.group("dimension")
    dimension = int(dimension_raw) if dimension_raw is not None else None
    return map_name, dimension


def sietch_dimension_labels(map_name: str = "Survival_1") -> dict[int, str]:
    """Resolve active Sietch display names through RedBlink's own read-only CLI.

    The collector never opens the Dune database directly. The CLI command is
    cached so Map chat does not invoke it for every message.
    """
    now = time.monotonic()
    cached = _sietch_label_cache.get(map_name)
    if cached and now - cached[0] < SIETCH_CACHE_TTL_SECONDS:
        return dict(cached[1])

    if DUNE_ROOT is None or DUNE_CLI is None or not DUNE_CLI.is_file():
        return dict(cached[1]) if cached else {}

    try:
        output = subprocess.check_output(
            [
                str(DUNE_CLI),
                "sietches",
                "dimensions",
                map_name,
                "--active-only",
                "--labels",
            ],
            cwd=str(DUNE_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:
        # Keep a previous good cache on transient CLI/DB availability issues.
        return dict(cached[1]) if cached else {}

    labels: dict[int, str] = {}
    pattern = re.compile(
        rf"^{re.escape(map_name)}\s+Dimension\s+(\d+)\s+"
        r"Display Name:\s*(.*?)\s+Password:\s*.*$",
        re.IGNORECASE,
    )
    for raw_line in output.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        display_name = match.group(2).strip()
        if display_name:
            labels[int(match.group(1))] = display_name

    _sietch_label_cache[map_name] = (now, labels)
    return dict(labels)


def sietch_name_for_route(map_name: str | None, dimension: int | None) -> str | None:
    if dimension is None or not map_name:
        return None
    if map_name.lower() not in {"haggabasin", "survival_1"}:
        return None
    return sietch_dimension_labels("Survival_1").get(dimension)


def is_internal_ui_notification(
    inner: dict[str, Any],
    message_text: Any,
    spoofed_name: Any,
) -> bool:
    candidates = (
        inner.get("m_FuncomIdFrom"),
        message_text,
        spoofed_name,
    )
    for candidate in candidates:
        value = str(candidate or "").strip().casefold()
        if any(value.startswith(prefix) for prefix in INTERNAL_UI_NOTIFICATION_PREFIXES):
            return True
    return False


def purge_internal_ui_notifications(conn: sqlite3.Connection) -> int:
    # v0.2.6 could persist Guild UI localization events such as
    # UI/GuildNotification_Guild_Title. Purge only this known internal family
    # from the addon's private SQLite database; the Dune database is untouched.
    cur = conn.execute(
        """
        DELETE FROM messages
        WHERE instr(lower(coalesce(funcom_id_from, '')), 'ui/guildnotification_') = 1
           OR instr(lower(coalesce(spoofed_username, '')), 'ui/guildnotification_') = 1
           OR instr(lower(coalesce(message, '')), 'ui/guildnotification_') = 1
           OR instr(lower(coalesce(raw_inner_json, '')), 'ui/guildnotification_') > 0
        """
    )
    conn.commit()
    return max(0, int(cur.rowcount or 0))


def is_excluded_private_channel(channel: Any) -> bool:
    return str(channel or "").strip().casefold() in EXCLUDED_PRIVATE_CHANNELS


def purge_excluded_private_chat(conn: sqlite3.Connection) -> int:
    # Older releases retained Whisper/private-direct chat. Remove those rows
    # from the addon's private SQLite database before rebuilding any exports.
    # This never touches the Dune PostgreSQL database.
    cur = conn.execute(
        """
        DELETE FROM messages
        WHERE lower(trim(coalesce(channel, ''))) IN ('whisper', 'whispers')
        """
    )
    conn.commit()
    return max(0, int(cur.rowcount or 0))


def decode_outer_text_chat(outer_text: str) -> tuple[str, dict[str, Any]] | None:
    try:
        outer = json.loads(outer_text)
    except json.JSONDecodeError:
        return None

    outer_type = outer.get("Type") or outer.get("type")
    if outer_type != "TextChat":
        return None

    inner_value = outer.get("content")
    if inner_value is None:
        inner_value = outer.get("Content")

    if isinstance(inner_value, str):
        try:
            inner = json.loads(inner_value)
        except json.JSONDecodeError:
            return None
    elif isinstance(inner_value, dict):
        inner = inner_value
    else:
        return None

    return str(outer_type), inner


def decode_routed_chat_line(line: str) -> dict[str, Any] | None:
    m_ts = DOCKER_TS_RE.match(line)
    if m_ts:
        line = m_ts.group("line")

    match = ROUTED_CLOG_RE.search(line)
    if not match:
        return None

    source, exchange, routing_key, outer_text = match.groups()
    decoded = decode_outer_text_chat(outer_text)
    if not decoded:
        return None
    _, inner = decoded

    message_id = inner.get("m_Id")
    channel = str(inner.get("m_ChannelType") or "")
    if not message_id or channel.lower() != "map":
        return None

    map_name, dimension = route_map_context(routing_key)
    return {
        "message_id": str(message_id),
        "channel": channel,
        "routing_key": routing_key.strip(),
        "map_name": map_name,
        "map_dimension": dimension,
        "sietch_name": sietch_name_for_route(map_name, dimension),
        "route_source": source,
        "route_exchange": exchange,
    }


def apply_routed_context(conn: sqlite3.Connection, routed: dict[str, Any]) -> int | None:
    row = conn.execute(
        "SELECT id FROM messages WHERE message_id = ?",
        (routed["message_id"],),
    ).fetchone()
    if not row:
        return None

    conn.execute(
        """
        UPDATE messages
        SET routing_key = ?,
            map_name = COALESCE(?, map_name),
            map_dimension = COALESCE(?, map_dimension),
            sietch_name = COALESCE(?, sietch_name)
        WHERE message_id = ?
        """,
        (
            routed.get("routing_key"),
            routed.get("map_name"),
            routed.get("map_dimension"),
            routed.get("sietch_name"),
            routed["message_id"],
        ),
    )
    conn.commit()
    return int(row["id"])


def decode_chat_line(line: str) -> dict[str, Any] | None:
    docker_ts = None
    m_ts = DOCKER_TS_RE.match(line)
    if m_ts:
        docker_ts = m_ts.group("ts")
        line = m_ts.group("line")

    match = CLOG_RE.search(line)
    if not match:
        return None

    source, target, outer_text = match.groups()

    decoded = decode_outer_text_chat(outer_text)
    if not decoded:
        return None
    outer_type, inner = decoded

    message_id = inner.get("m_Id")
    channel = inner.get("m_ChannelType")
    if not message_id or not channel:
        return None

    # Do not inspect, persist, export, or render private/direct Whispers. Return
    # a discard marker so stream_logs can still advance its Docker timestamp.
    if is_excluded_private_channel(channel):
        return {
            "_discard": True,
            "message_id": str(message_id),
            "channel": str(channel),
        }

    message_obj = inner.get("m_Message") or {}
    text = message_obj.get("m_UnlocalizedMessage")
    if text is None:
        localized = message_obj.get("m_LocalizedMessage") or {}
        text = localized.get("m_Key") or ""

    spoofed_obj = inner.get("m_SpoofedUserNameFrom") or {}
    spoofed_name = (
        spoofed_obj.get("m_UnlocalizedName")
        or spoofed_obj.get("m_Key")
        or None
    )

    if is_internal_ui_notification(inner, text, spoofed_name):
        return None

    origin = inner.get("m_OriginLocation") or {}
    game_utc, game_local = parse_game_timestamp(inner.get("m_Timestamp"))

    return {
        "message_id": str(message_id),
        "received_at": iso_utc(),
        "docker_timestamp": docker_ts,
        "game_timestamp_utc": game_utc,
        "game_timestamp_local": game_local,
        "channel": str(channel),
        "map_name": extract_map_name(inner, source, target),
        "routing_key": None,
        "map_dimension": None,
        "sietch_name": None,
        "funcom_id_from": inner.get("m_FuncomIdFrom"),
        "username_to": inner.get("m_UserNameTo"),
        "message": text,
        "use_spoofed_username": 1 if inner.get("m_bUseSpoofedUserName") else 0,
        "spoofed_username": spoofed_name,
        "origin_x": origin.get("X"),
        "origin_y": origin.get("Y"),
        "origin_z": origin.get("Z"),
        "interceptor_source": source,
        "interceptor_target": target,
        "outer_type": outer_type,
        "raw_inner_json": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
    }


def save_message(conn: sqlite3.Connection, message: dict[str, Any]) -> int | None:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO messages (
            message_id,
            received_at,
            docker_timestamp,
            game_timestamp_utc,
            game_timestamp_local,
            channel,
            map_name,
            routing_key,
            map_dimension,
            sietch_name,
            funcom_id_from,
            username_to,
            message,
            use_spoofed_username,
            spoofed_username,
            origin_x,
            origin_y,
            origin_z,
            interceptor_source,
            interceptor_target,
            outer_type,
            raw_inner_json
        ) VALUES (
            :message_id,
            :received_at,
            :docker_timestamp,
            :game_timestamp_utc,
            :game_timestamp_local,
            :channel,
            :map_name,
            :routing_key,
            :map_dimension,
            :sietch_name,
            :funcom_id_from,
            :username_to,
            :message,
            :use_spoofed_username,
            :spoofed_username,
            :origin_x,
            :origin_y,
            :origin_z,
            :interceptor_source,
            :interceptor_target,
            :outer_type,
            :raw_inner_json
        )
        """,
        message,
    )
    conn.commit()
    if cur.rowcount <= 0:
        return None
    return int(cur.lastrowid)


def docker_container_running() -> bool:
    try:
        output = subprocess.check_output(
            [
                DOCKER_BIN,
                "inspect",
                "-f",
                "{{.State.Running}}",
                TEXT_ROUTER_CONTAINER,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        return output == "true"
    except Exception:
        return False


def docker_since_value(conn: sqlite3.Connection) -> str:
    cursor = get_state(conn, "last_docker_timestamp")
    return cursor or BOOTSTRAP_SINCE


def stream_logs(conn: sqlite3.Connection) -> None:
    since = docker_since_value(conn)
    cmd = [
        DOCKER_BIN,
        "logs",
        "--follow",
        "--timestamps",
        "--since",
        since,
        TEXT_ROUTER_CONTAINER,
    ]

    print(
        f"[SOURCE] following {TEXT_ROUTER_CONTAINER} "
        f"(since={since})",
        flush=True,
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )

    assert proc.stdout is not None

    export_messages(conn, connected=True)
    handled_since_cleanup = 0
    pending = b""

    def stop_log_process() -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

    def handle_line(raw_line: str) -> None:
        nonlocal handled_since_cleanup

        ts_match = DOCKER_TS_RE.match(raw_line)
        docker_ts = ts_match.group("ts") if ts_match else None

        routed = decode_routed_chat_line(raw_line)
        if routed:
            if docker_ts:
                set_state(conn, "last_docker_timestamp", docker_ts)
            routed_id = apply_routed_context(conn, routed)
            if routed_id is not None:
                context = routed.get("sietch_name") or routed.get("routing_key") or "-"
                print(
                    "[ROUTE] "
                    f"channel={routed['channel']} "
                    f"id={routed['message_id']} "
                    f"context={safe_preview(str(context), 80)}",
                    flush=True,
                )
                export_messages(
                    conn,
                    connected=True,
                    history_bucket=history_bucket_number(routed_id),
                )
            return

        message = decode_chat_line(raw_line)
        if not message:
            return

        if docker_ts:
            set_state(conn, "last_docker_timestamp", docker_ts)

        if message.get("_discard"):
            return

        inserted_id = save_message(conn, message)
        if inserted_id is None:
            return

        handled_since_cleanup += 1
        print(
            "[CHAT] "
            f"channel={message['channel']} "
            f"from={message['funcom_id_from'] or '-'} "
            f"id={message['message_id']}",
            flush=True,
        )

        if handled_since_cleanup >= 100:
            cleanup_old(conn)
            rebuild_history(conn)
            handled_since_cleanup = 0

        export_messages(
            conn,
            connected=True,
            history_bucket=history_bucket_number(inserted_id),
        )

    try:
        while True:
            lifecycle_state, lifecycle_reason = addon_collection_state()
            if lifecycle_state != "enabled":
                stop_log_process()
                # Move the cursor to the pause boundary. main() moves it again
                # when collection is re-enabled, so chat produced while the addon
                # is disabled is never backfilled later.
                set_state(conn, "last_docker_timestamp", iso_utc())
                raise AddonCollectionPaused(lifecycle_reason)

            ready, _, _ = select.select(
                [proc.stdout],
                [],
                [],
                LIFECYCLE_POLL_SECONDS,
            )
            if not ready:
                rc = proc.poll()
                if rc is not None:
                    raise RuntimeError(f"docker logs exited with code {rc}")
                continue

            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                rc = proc.wait()
                if pending:
                    handle_line(pending.decode("utf-8", errors="replace").rstrip("\r"))
                raise RuntimeError(f"docker logs exited with code {rc}")

            # Re-check once immediately after data becomes readable, before
            # parsing/persisting the batch. This closes the normal disable race
            # without re-reading state.json for every Text Router log line.
            lifecycle_state, lifecycle_reason = addon_collection_state()
            if lifecycle_state != "enabled":
                set_state(conn, "last_docker_timestamp", iso_utc())
                raise AddonCollectionPaused(lifecycle_reason)

            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                handle_line(raw.decode("utf-8", errors="replace").rstrip("\r"))
    finally:
        stop_log_process()


def main() -> int:
    ensure_local_dirs()
    conn = open_db()
    cleanup_old(conn)

    retry = 2
    was_paused = False
    missing_since: float | None = None
    last_pause_reason = ""
    export_initialized = False

    while True:
        lifecycle_state, lifecycle_reason = addon_collection_state()

        if lifecycle_state == "missing":
            if missing_since is None:
                missing_since = time.monotonic()
            was_paused = True
            if lifecycle_reason != last_pause_reason:
                print(f"[PAUSED] {lifecycle_reason}", flush=True)
                last_pause_reason = lifecycle_reason
            if time.monotonic() - missing_since >= UNINSTALLED_GRACE_SECONDS:
                print(
                    "[STOP] Console addon appears to be uninstalled; "
                    "collector is exiting cleanly",
                    flush=True,
                )
                return 0
            time.sleep(LIFECYCLE_POLL_SECONDS)
            continue

        missing_since = None

        if lifecycle_state != "enabled":
            was_paused = True
            if lifecycle_reason != last_pause_reason:
                print(f"[PAUSED] {lifecycle_reason}", flush=True)
                last_pause_reason = lifecycle_reason
            time.sleep(LIFECYCLE_POLL_SECONDS)
            continue

        if last_pause_reason:
            print("[LIFECYCLE] Console addon enabled; collection may resume", flush=True)
            last_pause_reason = ""

        if was_paused:
            # Skip the entire interval during which the Console addon was
            # disabled/unavailable. This prevents disabled-time chat from being
            # replayed from Docker logs when the addon is enabled again.
            set_state(conn, "last_docker_timestamp", iso_utc())
            was_paused = False

        ensure_export_dir()
        if not export_initialized:
            rebuild_history(conn)
            export_messages(conn, connected=False, error="Waiting for text-router log stream")
            export_initialized = True

        if not docker_container_running():
            error = f"{TEXT_ROUTER_CONTAINER} is not running; retrying"
            print(f"[WAIT] {error}", flush=True)
            export_messages(conn, connected=False, error=error)
            time.sleep(retry)
            continue

        try:
            stream_logs(conn)
        except AddonCollectionPaused as exc:
            was_paused = True
            last_pause_reason = str(exc)
            print(f"[PAUSED] {exc}", flush=True)
            time.sleep(LIFECYCLE_POLL_SECONDS)
        except KeyboardInterrupt:
            if addon_collection_state()[0] == "enabled":
                export_messages(conn, connected=False, error="Collector stopped")
            return 0
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"[RETRY] {error}", file=sys.stderr, flush=True)
            if addon_collection_state()[0] == "enabled":
                export_messages(conn, connected=False, error=error)
            time.sleep(retry)


if __name__ == "__main__":
    raise SystemExit(main())
