async function writeWithBackup() {
  return window.DuneAddon.request("database.execute", {
    query: "update dune.example_table set example_value = $1 where id = $2",
    params: ["new value", 1]
  });
}
