async function loadPlayers() {
  const result = await window.DuneAddon.request("leadership.players.list");
  return result.players || result || [];
}
