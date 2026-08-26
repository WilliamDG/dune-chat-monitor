# Bridge API

Addons run inside an iframe. They call back into Dune Docker Console through the bridge helper in `web/dune-addon-bridge.js`.

Use it like this:

```js
const result = await window.DuneAddon.request("leadership.players.list");
```

## Available Actions

| Action | Required permission | Purpose |
| --- | --- | --- |
| `leadership.players.list` | `players:read` | Read player summary data exposed by the console. |
| `database.query` | `database:read` | Run read-only SQL. |
| `database.execute` | `database:write` | Run write SQL. The console creates a database backup first. |

Keep bridge calls small and explicit. Ask only for the permissions your addon actually uses.

## Local Development

The real bridge exists only when your addon is opened inside Dune Docker Console.
If you open `web/index.html` directly in a browser, use mock data for local UI
work.

Example:

```js
async function getPlayers() {
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

For testing real bridge calls before publishing, install the addon into your own
local Dune Docker Console instance. See [Local Development](local-development.md).
