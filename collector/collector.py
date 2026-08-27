#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TIMEZONE_NAME = os.environ.get("TIMEZONE", "UTC")
RETENTION_DAYS = max(1, int(os.environ.get("CHAT_RETENTION_DAYS", "30")))
PAGE_SIZE = max(10, int(os.environ.get("CHAT_PAGE_SIZE", "50")))
BOOTSTRAP_SINCE = os.environ.get("CHAT_BOOTSTRAP_SINCE", "1h").strip() or "1h"

TEXT_ROUTER_CONTAINER = os.environ.get("TEXT_ROUTER_CONTAINER", "dune-text-router")
DOCKER_BIN = os.environ.get("DOCKER_BIN", "/usr/bin/docker")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", str(PROJECT_DIR / "data" / "chat.sqlite3")))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", str(PROJECT_DIR / "web" / "live")))

CLOG_RE = re.compile(
    r"\[\d{2}:\d{2}:\d{2}\s+\d+\s+INF\s+CLOG\]\s+"
    r"Intercepted message from (.+?) to (.+?):\s+(\{.*\})\s*$"
)

DOCKER_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s(?P<line>.*)$"
)

GAME_TS_FORMAT = "%Y.%m.%d-%H.%M.%S"

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except Exception:
        return ZoneInfo("UTC")


LOCAL_TZ = local_zone()


def parse_game_timestamp(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    try:
        dt = datetime.strptime(value, GAME_TS_FORMAT).replace(tzinfo=timezone.utc)
        return iso_utc(dt), dt.astimezone(LOCAL_TZ).isoformat()
    except Exception:
        return value, value


def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


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
    path.parent.mkdir(parents=True, exist_ok=True)
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
        "timezone": TIMEZONE_NAME,
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
        "timezone": TIMEZONE_NAME,
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
            "timezone": TIMEZONE_NAME,
            "bucket": str(bucket),
            "updatedAt": iso_utc(),
            "messages": [message_payload(row) for row in rows],
        },
    )


def rebuild_history(conn: sqlite3.Connection) -> None:
    history_dir = EXPORT_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
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
            "timezone": TIMEZONE_NAME,
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

    message_id = inner.get("m_Id")
    channel = inner.get("m_ChannelType")
    if not message_id or not channel:
        return None

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
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    assert proc.stdout is not None

    export_messages(conn, connected=True)

    handled_since_cleanup = 0

    for line in proc.stdout:
        raw_line = line.rstrip("\r\n")

        ts_match = DOCKER_TS_RE.match(raw_line)
        docker_ts = ts_match.group("ts") if ts_match else None

        message = decode_chat_line(raw_line)
        if not message:
            continue

        if docker_ts:
            set_state(conn, "last_docker_timestamp", docker_ts)

        inserted_id = save_message(conn, message)

        if inserted_id is None:
            continue

        handled_since_cleanup += 1

        print(
            "[CHAT] "
            f"channel={message['channel']} "
            f"from={message['funcom_id_from'] or '-'} "
            f"id={message['message_id']} "
            f"text={safe_preview(message['message'])}",
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

    rc = proc.wait()
    raise RuntimeError(f"docker logs exited with code {rc}")


def main() -> int:
    ensure_dirs()
    conn = open_db()

    cleanup_old(conn)
    rebuild_history(conn)
    export_messages(conn, connected=False, error="Waiting for text-router log stream")

    retry = 2

    while True:
        if not docker_container_running():
            error = f"{TEXT_ROUTER_CONTAINER} is not running; retrying"
            print(f"[WAIT] {error}", flush=True)
            export_messages(conn, connected=False, error=error)
            time.sleep(retry)
            continue

        try:
            stream_logs(conn)
        except KeyboardInterrupt:
            export_messages(conn, connected=False, error="Collector stopped")
            return 0
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"[RETRY] {error}", file=sys.stderr, flush=True)
            export_messages(conn, connected=False, error=error)
            time.sleep(retry)


if __name__ == "__main__":
    raise SystemExit(main())
