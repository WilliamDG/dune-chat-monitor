# Local Development

You do not need to publish your addon before testing it.

The normal development loop is:

1. Build the UI locally with mock data.
2. Validate the addon files.
3. Copy the addon into a local Dune Docker Console install.
4. Enable it locally and test the real bridge.
5. Publish only when you are ready for other server owners to install it.

## 1. Build The UI Locally

An addon is a static web page loaded inside Dune Docker Console as an iframe.
You can use plain HTML, React, Vue, Svelte, Vite, or any other frontend setup as
long as the final addon package contains:

```text
addon.json
web/index.html
web/...
```

For quick layout work, open the template directly:

```bash
open web/index.html
```

On Linux without `open`, use your browser's **File > Open** option.

When the page is opened directly in a browser, the real Dune Docker Console
bridge is not available. Use mock data for this part.

## 2. Mock The Bridge Locally

The template includes the bridge helper at:

```text
web/dune-addon-bridge.js
```

When your addon is inside Dune Docker Console, calls like this go through the
real bridge:

```js
const result = await window.DuneAddon.request("leadership.players.list");
```

When your addon is opened directly in a browser, there is no parent console
iframe. For that case, add a small mock in your own addon code:

```js
async function loadPlayersForDevelopment() {
  if (window.parent === window) {
    return [
      {
        name: "Local Test Player",
        level: 42,
        faction: "Atreides",
        guild: "Dev Guild",
        status: "Online",
        map: "Survival_1"
      }
    ];
  }

  const result = await window.DuneAddon.request("leadership.players.list");
  return result.players || result || [];
}
```

This lets you build the UI quickly without needing a running server for every
CSS or layout change.

## 3. Validate Before Testing

Run the validator from your addon repo:

```bash
node scripts/validate.js
```

This checks `addon.json`, the entry path, and requested permissions.

## 4. Test Privately In Dune Docker Console

To test the real bridge, copy your addon into a local Dune Docker Console
install. You do not need the community addon index for this.

Replace `my-dune-addon` with your real `addon.json` ID:

```bash
CONSOLE_DIR="$HOME/dune-awakening-selfhost-docker"
ADDON_ID="my-dune-addon"

mkdir -p "$CONSOLE_DIR/runtime/addons/installed/$ADDON_ID"
cp -a addon.json web "$CONSOLE_DIR/runtime/addons/installed/$ADDON_ID/"
```

Then enable it and approve the permissions you are testing:

```bash
cd "$CONSOLE_DIR"

python3 - <<'PY'
import json
from pathlib import Path

addon_id = "my-dune-addon"
permissions = ["players:read", "database:read"]

state_path = Path("runtime/addons/state.json")
state_path.parent.mkdir(parents=True, exist_ok=True)

try:
    state = json.loads(state_path.read_text())
except Exception:
    state = {}

state[addon_id] = {
    "enabled": True,
    "approvedPermissions": permissions
}

state_path.write_text(json.dumps(state, indent=2) + "\n")
PY
```

Refresh Dune Docker Console and open **Addons**. Your addon should appear as an
installed addon. Open it there to test the real bridge.

## 5. Updating Your Local Test Copy

After making changes, copy the addon files again:

```bash
CONSOLE_DIR="$HOME/dune-awakening-selfhost-docker"
ADDON_ID="my-dune-addon"

rm -rf "$CONSOLE_DIR/runtime/addons/installed/$ADDON_ID"
mkdir -p "$CONSOLE_DIR/runtime/addons/installed/$ADDON_ID"
cp -a addon.json web "$CONSOLE_DIR/runtime/addons/installed/$ADDON_ID/"
```

Then refresh the console page.

## 6. Package Locally

When you want to verify the release package:

```bash
bash scripts/package.sh
```

This creates:

```text
dist/<addon-id>-<version>.zip
dist/<addon-id>-<version>.zip.sha256
```

## 7. Publishing Comes Later

Private testing does not require a pull request.

Only submit to the community addon index when your addon is ready for public
discovery in Dune Docker Console.

The community index repo is:

```text
https://github.com/Red-Blink/dune-docker-addons
```

