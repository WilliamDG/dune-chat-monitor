async function readDatabaseTime() {
  return window.DuneAddon.request("database.query", {
    query: "select current_database() as database_name, now() as server_time"
  });
}
