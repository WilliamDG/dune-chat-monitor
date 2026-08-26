# Permissions

Server owners review addon permissions before installing an addon. Keep permissions narrow and easy to understand.

## Supported Permission Keys

| Permission | Allows |
| --- | --- |
| `players:read` | Read player summary data exposed by the console. |
| `database:read` | Run read-only database queries through the console bridge. |
| `database:write` | Run write database statements through the console bridge. The console creates a database backup first. |
| `server:status` | Reserved for reading server status data. |
| `server:restart` | Reserved for restarting services. |
| `files:addon-data` | Reserved for storing addon-owned data. |
| `broadcast:send` | Reserved for sending in-game broadcasts. |

## Examples

Read players only:

```json
"permissions": {
  "players": ["read"]
}
```

Read database only:

```json
"permissions": {
  "database": ["read"]
}
```

Read and write database:

```json
"permissions": {
  "database": ["read", "write"]
}
```
