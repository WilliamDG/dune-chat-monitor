async function loadPlayers() {
  const result = await window.DuneAddon.request("players.identity.list");
  return result.players || result || [];
}
