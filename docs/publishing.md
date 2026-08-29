# Publishing

Addon code stays in this repository. The Community Index points server owners to reviewed release packages.

## Before publishing

1. Remove the private RedBlink local-development compatibility workaround from the test server, or confirm it is no longer needed.
2. Verify the addon works with only the permissions declared in `addon.json`, and that player identity access goes through `DuneAddon.request(...)` rather than direct Console player REST endpoints.
3. Confirm the collector still has no RabbitMQ consumer and no direct Dune DB credentials/SQL. Hagga Basin Sietch labels must be resolved only through RedBlink's read-only `runtime/scripts/dune sietches dimensions Survival_1 --active-only --labels` command.
4. Verify companion ownership/lifecycle boundaries: installer/updater/uninstaller must not deploy/remove Console addon files or edit `runtime/addons/state.json`; the collector must collect only while the Console addon is installed and enabled, skip disabled-time chat, and exit cleanly after uninstall.
5. Verify the release and **catalog description** clearly state that private/direct Whispers are excluded from collection/storage and that upgrades purge any legacy Whisper rows from the addon database.
6. Validate and package:

```bash
node scripts/validate.js
python3 -m py_compile collector/collector.py
python3 scripts/test_collector_lifecycle.py
bash -n install.sh update.sh uninstall.sh doctor.sh
bash scripts/package.sh
```

## Release

For version `0.2.7`:

```bash
git tag v0.2.7
git push origin v0.2.7
```

GitHub Actions should publish the package and checksum.

## Community Index

Submit the release to:

```text
https://github.com/Red-Blink/dune-docker-addons
```

The public addon should request only the permissions it actually uses. Dune Chat Monitor requests `players:read` only through the RedBlink addon permission bridge for best-effort identity enrichment. Companion install/update/uninstall scripts must not edit `runtime/addons/state.json` or replace the Console-installed addon directory.

The catalog description must state that private/direct Whispers are excluded from collection and storage.
