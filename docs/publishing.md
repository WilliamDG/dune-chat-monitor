# Publishing

Addon code stays in this repository. The Community Index points server owners to reviewed release packages.

## Before publishing

1. Remove the private RedBlink local-development compatibility workaround from the test server, or confirm it is no longer needed.
2. Verify the addon works with only the permissions declared in `addon.json`.
3. Confirm the collector still has no RabbitMQ consumer and no direct Dune DB credentials/SQL.
4. Verify the privacy allowlist stores/exports only `Map` and `Proximity` and rejects private/direct channels.
5. Validate and package:

```bash
node scripts/validate.js
python3 -m py_compile collector/collector.py
bash -n install.sh update.sh uninstall.sh doctor.sh
bash scripts/package.sh
```

## Release

For version `0.2.1`:

```bash
git tag v0.2.1
git push origin v0.2.1
```

GitHub Actions should publish the package and checksum.

## Community Index

Submit the release to:

```text
https://github.com/Red-Blink/dune-docker-addons
```

The public addon should request only the permissions it actually uses. Dune Chat Monitor currently requests `players:read` for character-name / Steam platform identity enrichment in the UI.
