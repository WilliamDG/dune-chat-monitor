#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
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


def main() -> int:
    collector = load_collector()

    with tempfile.TemporaryDirectory(prefix="dune-chat-monitor-lifecycle-") as tmp_name:
        dune_root = Path(tmp_name)
        addon_root = dune_root / "runtime" / "addons" / "installed" / "dune-chat-monitor"
        web_root = addon_root / "web"
        state_path = dune_root / "runtime" / "addons" / "state.json"
        export_dir = web_root / "live"

        web_root.mkdir(parents=True)
        (addon_root / "addon.json").write_text("{}\n", encoding="utf-8")

        collector.DUNE_ROOT = dune_root
        collector.ADDON_STATE_PATH = state_path
        collector.ADDON_MANIFEST_PATH = addon_root / "addon.json"
        collector.EXPORT_DIR = export_dir

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

    print("Collector lifecycle gate tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
