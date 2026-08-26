(function () {
  const playersEl = document.querySelector("#players");
  const logEl = document.querySelector("#log");
  const queryResultEl = document.querySelector("#queryResult");
  const refreshPlayersButton = document.querySelector("#refreshPlayers");
  const runQueryButton = document.querySelector("#runQuery");

  function log(message) {
    logEl.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderPlayers(players) {
    if (!Array.isArray(players) || players.length === 0) {
      playersEl.innerHTML = '<p class="empty">No players found.</p>';
      return;
    }

    playersEl.innerHTML = players
      .map((player) => {
        const name = escapeHtml(player.name || player.player_name || "Unknown");
        const level = escapeHtml(player.level ?? "-");
        const faction = escapeHtml(player.faction || "No faction");
        const guild = escapeHtml(player.guild || "No guild");

        return `
          <article class="card">
            <h3>${name}</h3>
            <dl>
              <div><dt>Level</dt><dd>${level}</dd></div>
              <div><dt>Faction</dt><dd>${faction}</dd></div>
              <div><dt>Guild</dt><dd>${guild}</dd></div>
            </dl>
          </article>
        `;
      })
      .join("");
  }

  async function loadPlayers() {
    refreshPlayersButton.disabled = true;
    playersEl.innerHTML = '<p class="empty">Loading players...</p>';

    try {
      const result = await window.DuneAddon.request("leadership.players.list");
      renderPlayers(result.players || result || []);
      log("Loaded player data.");
    } catch (error) {
      playersEl.innerHTML = `<p class="empty error">${escapeHtml(error.message)}</p>`;
      log(error.message);
    } finally {
      refreshPlayersButton.disabled = false;
    }
  }

  async function runSampleQuery() {
    runQueryButton.disabled = true;
    queryResultEl.textContent = "Running query...";

    try {
      const result = await window.DuneAddon.request("database.query", {
        query: "select current_database() as database_name, now() as server_time"
      });
      queryResultEl.textContent = JSON.stringify(result, null, 2);
      log("Sample query completed.");
    } catch (error) {
      queryResultEl.textContent = error.message;
      log(error.message);
    } finally {
      runQueryButton.disabled = false;
    }
  }

  refreshPlayersButton.addEventListener("click", loadPlayers);
  runQueryButton.addEventListener("click", runSampleQuery);
})();
