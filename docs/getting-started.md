# Getting Started

Use this template when you want to build a community addon for Dune Docker Console.

## Files You Usually Edit

```text
addon.json       addon name, version, entry path, and permissions
web/index.html   page markup
web/addon.js     addon behavior
web/addon.css    addon styling
```

## First Steps

1. Click **Use this template** on GitHub.
2. Update `addon.json` with your addon ID, name, author, and permissions.
3. Update `data-addon-id` in `web/index.html` to match `addon.json.id`.
4. Build your UI in `web/`.
5. Run validation before committing:

   ```bash
   node scripts/validate.js
   ```

For local layout work, open `web/index.html` in a browser and use mock data.
Bridge requests only use the real Dune Docker Console bridge after the addon is
installed inside Dune Docker Console.

For the full local testing workflow, see [Local Development](local-development.md).
