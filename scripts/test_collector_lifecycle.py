#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = REPO_ROOT / "collector" / "collector.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("dune_chat_monitor_collector_test", COLLECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load collector module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_state(path: Path, *, enabled: bool, lifecycle: str = "active") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"dune-chat-monitor": {"enabled": enabled, "lifecycle": lifecycle}}) + "\n",
        encoding="utf-8",
    )


def configure_runtime(collector, dune_root: Path):
    addon_root = dune_root / "runtime" / "addons" / "installed" / "dune-chat-monitor"
    web_root = addon_root / "web"
    state_path = dune_root / "runtime" / "addons" / "state.json"
    export_dir = web_root / "live"

    web_root.mkdir(parents=True, exist_ok=True)
    (addon_root / "addon.json").write_text("{}\n", encoding="utf-8")

    collector.DUNE_ROOT = dune_root
    collector.ADDON_STATE_PATH = state_path
    collector.ADDON_MANIFEST_PATH = addon_root / "addon.json"
    collector.EXPORT_DIR = export_dir
    collector.DB_PATH = dune_root / "data" / "chat.sqlite3"
    collector.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    collector._sietch_label_cache.clear()

    return addon_root, web_root, state_path, export_dir


def test_lifecycle_gate(collector) -> None:
    with tempfile.TemporaryDirectory(prefix="dune-chat-monitor-lifecycle-") as tmp_name:
        dune_root = Path(tmp_name)
        addon_root, _, state_path, export_dir = configure_runtime(collector, dune_root)

        write_state(state_path, enabled=False)
        assert collector.addon_collection_state()[0] == "disabled"

        write_state(state_path, enabled=True)
        assert collector.addon_collection_state()[0] == "enabled"
        collector.ensure_export_dir()
        assert export_dir.is_dir()

        write_state(state_path, enabled=True, lifecycle="blocked")
        assert collector.addon_collection_state()[0] == "disabled"

        shutil.rmtree(addon_root)
        assert collector.addon_collection_state()[0] == "missing"

        try:
            collector.ensure_export_dir()
        except collector.AddonCollectionPaused:
            pass
        else:
            raise AssertionError("export must fail closed after Console addon uninstall")

        assert not addon_root.exists(), "collector must not recreate the Console-owned addon directory"


def test_pause_persists_across_collector_restart(collector) -> None:
    """Regression: disabled interval must remain skipped after a process restart."""
    with tempfile.TemporaryDirectory(prefix="dune-chat-monitor-pause-restart-") as tmp_name:
        dune_root = Path(tmp_name)
        _, _, state_path, _ = configure_runtime(collector, dune_root)

        write_state(state_path, enabled=False)
        conn = collector.open_db()
        collector.set_state(conn, "last_docker_timestamp", "2000-01-01T00:00:00Z")

        assert collector.mark_collection_paused(conn) is True
        assert collector.get_state(conn, collector.COLLECTION_PAUSED_STATE_KEY) == "1"
        paused_cursor = collector.get_state(conn, "last_docker_timestamp")
        assert paused_cursor != "2000-01-01T00:00:00Z"
        conn.close()

        # Simulate: service stopped while disabled, addon re-enabled, service restarted.
        write_state(state_path, enabled=True)
        restarted_conn = collector.open_db()

        assert collector.get_state(
            restarted_conn, collector.COLLECTION_PAUSED_STATE_KEY
        ) == "1", "pause marker must survive collector restart"

        assert collector.resume_from_persisted_pause(restarted_conn) is True
        resumed_cursor = collector.get_state(restarted_conn, "last_docker_timestamp")
        assert resumed_cursor != "2000-01-01T00:00:00Z"
        assert collector.get_state(
            restarted_conn, collector.COLLECTION_PAUSED_STATE_KEY
        ) == "0"
        assert collector.resume_from_persisted_pause(restarted_conn) is False
        restarted_conn.close()


def test_console_package_replacement_rebuilds_exports(collector) -> None:
    """Regression: replacing the Console addon package must self-heal web/live history."""
    with tempfile.TemporaryDirectory(prefix="dune-chat-monitor-package-replace-") as tmp_name:
        dune_root = Path(tmp_name)
        addon_root, web_root, state_path, export_dir = configure_runtime(collector, dune_root)

        write_state(state_path, enabled=True)
        conn = collector.open_db()
        conn.execute(
            """
            INSERT INTO messages(message_id, received_at, channel, raw_inner_json)
            VALUES (?, ?, ?, ?)
            """,
            ("replacement-regression-1", collector.iso_utc(), "Map", "{}"),
        )
        conn.commit()

        assert collector.ensure_exports_initialized(conn, False) is True
        assert collector.export_layout_ready()
        assert (export_dir / "history" / "index.json").is_file()

        # Simulate Console update replacing the installed addon package.
        shutil.rmtree(addon_root)
        web_root.mkdir(parents=True, exist_ok=True)
        (addon_root / "addon.json").write_text("{}\n", encoding="utf-8")

        assert collector.addon_collection_state()[0] == "enabled"
        assert not collector.export_layout_ready()

        try:
            collector.atomic_json(export_dir / "history" / "probe.json", {"ok": True})
        except collector.AddonExportReset:
            pass
        else:
            raise AssertionError("missing generated history directory must trigger AddonExportReset")

        assert collector.ensure_exports_initialized(conn, True) is True
        assert collector.export_layout_ready()
        assert (export_dir / "messages.json").is_file()
        assert (export_dir / "status.json").is_file()

        history = json.loads(
            (export_dir / "history" / "index.json").read_text(encoding="utf-8")
        )
        assert history["buckets"], "rebuilt history index must include retained DB rows"
        conn.close()



def test_retention_runs_without_new_messages_and_refreshes_exports(collector) -> None:
    """Regression: retention must run on wall-clock time without new chat."""
    with tempfile.TemporaryDirectory(prefix="dune-chat-monitor-retention-") as tmp_name:
        dune_root = Path(tmp_name)
        _, _, state_path, export_dir = configure_runtime(collector, dune_root)

        write_state(state_path, enabled=True)
        conn = collector.open_db()

        now = collector.now_utc()
        expired_at = now - timedelta(days=collector.RETENTION_DAYS, seconds=10)
        retained_at = now - timedelta(days=collector.RETENTION_DAYS) + timedelta(seconds=10)

        conn.execute(
            """
            INSERT INTO messages(message_id, received_at, channel, raw_inner_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "retention-expired",
                collector.iso_utc(expired_at),
                "Map",
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO messages(message_id, received_at, channel, raw_inner_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "retention-retained",
                collector.iso_utc(retained_at),
                "Map",
                "{}",
            ),
        )
        conn.commit()

        assert collector.ensure_exports_initialized(conn, False) is True

        # Make the wall-clock cleanup due. No save_message()/new chat occurs
        # between export initialization and retention enforcement.
        collector.set_state(
            conn,
            collector.RETENTION_CLEANUP_STATE_KEY,
            collector.iso_utc(
                now - timedelta(
                    seconds=collector.RETENTION_CLEANUP_INTERVAL_SECONDS + 1
                )
            ),
        )

        removed = collector.maybe_enforce_retention(
            conn,
            connected=True,
            refresh_exports=True,
            now=now,
        )

        assert removed == 1

        ids = {
            row["message_id"]
            for row in conn.execute("SELECT message_id FROM messages").fetchall()
        }
        assert "retention-expired" not in ids
        assert "retention-retained" in ids

        messages_payload = json.loads(
            (export_dir / "messages.json").read_text(encoding="utf-8")
        )
        exported_ids = {
            item["id"] for item in messages_payload.get("messages", [])
        }
        assert "retention-expired" not in exported_ids
        assert "retention-retained" in exported_ids

        history_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (export_dir / "history").glob("*.json")
        )
        assert "retention-expired" not in history_text
        assert "retention-retained" in history_text

        conn.close()



def main() -> int:
    collector = load_collector()

    test_lifecycle_gate(collector)
    test_pause_persists_across_collector_restart(collector)
    test_console_package_replacement_rebuilds_exports(collector)
    test_retention_runs_without_new_messages_and_refreshes_exports(collector)

    print("Collector lifecycle regression tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())