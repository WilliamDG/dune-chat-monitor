import base64
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pika

RMQ_HOST = os.getenv("RMQ_HOST", "dune-rmq-game")
RMQ_PORT = int(os.getenv("RMQ_PORT", "5672"))
RMQ_VHOST = os.getenv("RMQ_VHOST", "/")
RMQ_USER = os.environ["RMQ_USER"]
RMQ_PASSWORD = os.environ["RMQ_PASSWORD"]
RMQ_QUEUE = os.getenv("RMQ_QUEUE", "dune.chat.monitor")

DB_PATH = Path(os.getenv("DB_PATH", "/data/chat.sqlite3"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/export"))
RETENTION_DAYS = max(1, int(os.getenv("CHAT_RETENTION_DAYS", "30")))
EXPORT_LIMIT = max(20, min(int(os.getenv("CHAT_EXPORT_LIMIT", "250")), 1000))
SERVER_NAME = os.getenv("SERVER_NAME", "Dune Server")
TIMEZONE_NAME = os.getenv("TIMEZONE", "UTC")

_cleanup_counter = 0

def utc_now():
    return datetime.now(timezone.utc)

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_storage():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                exchange_name TEXT NOT NULL DEFAULT '',
                routing_key TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT '',
                content_encoding TEXT NOT NULL DEFAULT '',
                message_id TEXT NOT NULL DEFAULT '',
                correlation_id TEXT NOT NULL DEFAULT '',
                app_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                rmq_timestamp INTEGER,
                headers_json TEXT NOT NULL DEFAULT '{}',
                body_utf8 TEXT,
                body_base64 TEXT NOT NULL,
                body_size INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_messages_received_at
            ON raw_messages(received_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_messages_routing_key
            ON raw_messages(routing_key)
        """)

def atomic_write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

def safe_headers(headers):
    if not headers:
        return {}
    try:
        return json.loads(json.dumps(headers, ensure_ascii=False, default=str))
    except Exception:
        return {"_raw": str(headers)}

def export_snapshot(connected=True, error=None):
    with db_connect() as conn:
        stats = conn.execute(
            "SELECT COUNT(*) AS total, MAX(received_at) AS last_received_at FROM raw_messages"
        ).fetchone()
        rows = conn.execute("""
            SELECT id, received_at, exchange_name, routing_key, content_type,
                   content_encoding, message_id, correlation_id, app_id, user_id,
                   rmq_timestamp, headers_json, body_utf8, body_base64, body_size
            FROM raw_messages
            ORDER BY id DESC
            LIMIT ?
        """, (EXPORT_LIMIT,)).fetchall()

    messages = []
    for row in rows:
        item = dict(row)
        try:
            item["headers"] = json.loads(item.pop("headers_json") or "{}")
        except Exception:
            item["headers"] = {}
            item.pop("headers_json", None)
        messages.append(item)

    generated_at = utc_now().isoformat()

    atomic_write_json(EXPORT_DIR / "status.json", {
        "ok": error is None,
        "collectorConnected": bool(connected),
        "serverName": SERVER_NAME,
        "timezone": TIMEZONE_NAME,
        "queue": RMQ_QUEUE,
        "exchange": "chat.intercept",
        "retentionDays": RETENTION_DAYS,
        "messageCount": int(stats["total"] or 0),
        "lastReceivedAt": stats["last_received_at"],
        "generatedAt": generated_at,
        "error": error
    })

    atomic_write_json(EXPORT_DIR / "messages.json", {
        "ok": True,
        "generatedAt": generated_at,
        "count": len(messages),
        "messages": messages
    })

def cleanup_old_rows():
    cutoff = (utc_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with db_connect() as conn:
        conn.execute("DELETE FROM raw_messages WHERE received_at < ?", (cutoff,))

def store_message(method, properties, body):
    global _cleanup_counter
    received_at = utc_now().isoformat()
    try:
        body_utf8 = body.decode("utf-8")
    except UnicodeDecodeError:
        body_utf8 = body.decode("utf-8", errors="replace")

    headers = safe_headers(properties.headers)

    with db_connect() as conn:
        conn.execute("""
            INSERT INTO raw_messages (
                received_at, exchange_name, routing_key, content_type,
                content_encoding, message_id, correlation_id, app_id, user_id,
                rmq_timestamp, headers_json, body_utf8, body_base64, body_size
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            received_at,
            method.exchange or "",
            method.routing_key or "",
            properties.content_type or "",
            properties.content_encoding or "",
            properties.message_id or "",
            properties.correlation_id or "",
            properties.app_id or "",
            properties.user_id or "",
            properties.timestamp,
            json.dumps(headers, ensure_ascii=False),
            body_utf8,
            base64.b64encode(body).decode("ascii"),
            len(body)
        ))

    _cleanup_counter += 1
    if _cleanup_counter >= 250:
        cleanup_old_rows()
        _cleanup_counter = 0

    export_snapshot(connected=True)

    preview = body_utf8.replace("\r", " ").replace("\n", " ")[:160]
    print(f"[CHAT] exchange={method.exchange!r} routing_key={method.routing_key!r} bytes={len(body)} preview={preview!r}")

def consume_forever():
    while True:
        connection = None
        try:
            print(f"[RMQ] Connecting to {RMQ_HOST}:{RMQ_PORT} vhost={RMQ_VHOST!r} queue={RMQ_QUEUE!r}")
            credentials = pika.PlainCredentials(RMQ_USER, RMQ_PASSWORD)
            params = pika.ConnectionParameters(
                host=RMQ_HOST,
                port=RMQ_PORT,
                virtual_host=RMQ_VHOST,
                credentials=credentials,
                heartbeat=30,
                blocked_connection_timeout=30,
                connection_attempts=5,
                retry_delay=3
            )

            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.basic_qos(prefetch_count=100)

            def callback(ch, method, properties, body):
                try:
                    store_message(method, properties, body)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as exc:
                    print(f"[ERROR] Failed to persist message: {exc}")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            channel.basic_consume(
                queue=RMQ_QUEUE,
                on_message_callback=callback,
                auto_ack=False
            )

            print("[RMQ] Collector online (read-only consumer)")
            export_snapshot(connected=True)
            channel.start_consuming()

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            print(f"[RMQ] {message}")
            try:
                export_snapshot(connected=False, error=message)
            except Exception as export_exc:
                print(f"[EXPORT] Could not write status: {export_exc}")
            time.sleep(5)
        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass

def main():
    init_storage()
    cleanup_old_rows()
    export_snapshot(connected=False, error="Collector starting")
    consume_forever()

if __name__ == "__main__":
    main()
