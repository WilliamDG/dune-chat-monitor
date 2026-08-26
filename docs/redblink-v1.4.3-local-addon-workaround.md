# RedBlink v1.4.3 local-addon workaround

This file documents the **temporary development-only** compatibility change used while `dune-chat-monitor` is private and not yet present in the RedBlink Community Addons catalog.

## Why it exists

RedBlink v1.4.3 currently runs `syncInstalledAddonLifecycle()` when installed addons are loaded. An installed addon that is absent from the Community Catalog is assigned lifecycle `removed`, which prevents its content from running.

The official addon template documents local/private testing without publishing first, so this behavior is treated as a temporary v1.4.3 incompatibility during development.

## What was changed on the test server

Host source file:

```text
/home/ubuntu/dune-awakening-selfhost-docker/console/api/src/addons.js
```

Original backup:

```text
/home/ubuntu/dune-awakening-selfhost-docker/console/api/src/addons.js.before-dune-chat-monitor-local-dev
```

The temporary code exception applies only to addon ID:

```text
dune-chat-monitor
```

The running Console image executes `/app/src/server.js`, so during private testing the patched host copy is also copied into the running container as `/app/src/addons.js`.

## Re-apply the temporary test patch

```bash
cd /home/ubuntu/dune-awakening-selfhost-docker

docker cp \
  console/api/src/addons.js \
  redblink-dune-docker-console:/app/src/addons.js

docker restart redblink-dune-docker-console
```

## Restore original RedBlink code

```bash
cd /home/ubuntu/dune-awakening-selfhost-docker

docker cp \
  console/api/src/addons.js.before-dune-chat-monitor-local-dev \
  redblink-dune-docker-console:/app/src/addons.js

docker restart redblink-dune-docker-console
```

To restore the host source as well:

```bash
cd /home/ubuntu/dune-awakening-selfhost-docker

cp -a \
  console/api/src/addons.js.before-dune-chat-monitor-local-dev \
  console/api/src/addons.js
```

The known original v1.4.3 SHA-256 used during this development session was:

```text
e5b5b0fa0755843e97bb9599d6c17ea3d11838aba98b54341b7a075a0d628848
```

## Removal condition

Remove this workaround when either:
- RedBlink fixes local/manual addon lifecycle upstream; or
- `dune-chat-monitor` is accepted into the Community Catalog and the workaround is no longer needed.

Do not make this compatibility change part of the public addon installer.
