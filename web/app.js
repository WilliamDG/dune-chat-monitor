const state = {
  messages: [],
  status: null,
};

const els = {
  serverName: document.getElementById("serverName"),
  statusBadge: document.getElementById("statusBadge"),
  channelFilter: document.getElementById("channelFilter"),
  searchInput: document.getElementById("searchInput"),
  refreshButton: document.getElementById("refreshButton"),
  messageCount: document.getElementById("messageCount"),
  lastUpdate: document.getElementById("lastUpdate"),
  messages: document.getElementById("messages"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function prettyTime(message) {
  const value =
    message.gameTimestampLocal ||
    message.gameTimestampUtc ||
    message.receivedAt;

  if (!value) return "—";

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(parsed);
}

function channelClass(channel) {
  return `channel-${String(channel || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "")}`;
}

function updateChannelOptions() {
  const current = els.channelFilter.value;
  const channels = [...new Set(
    state.messages.map((m) => m.channel).filter(Boolean)
  )].sort();

  els.channelFilter.innerHTML =
    `<option value="">All channels</option>` +
    channels
      .map((channel) =>
        `<option value="${escapeHtml(channel)}">${escapeHtml(channel)}</option>`
      )
      .join("");

  if (channels.includes(current)) {
    els.channelFilter.value = current;
  }
}

function render() {
  const status = state.status || {};
  const serverName =
    status.serverName ||
    (state.messages[0] && state.messages[0].serverName) ||
    "Dune Server";

  els.serverName.textContent = serverName;

  if (status.collectorConnected) {
    els.statusBadge.textContent = "LIVE";
    els.statusBadge.className = "badge badge-ok";
  } else {
    els.statusBadge.textContent = "OFFLINE";
    els.statusBadge.className = "badge badge-warn";
  }

  const selectedChannel = els.channelFilter.value;
  const query = els.searchInput.value.trim().toLowerCase();

  const filtered = state.messages.filter((message) => {
    if (selectedChannel && message.channel !== selectedChannel) {
      return false;
    }

    if (!query) return true;

    return [
      message.from,
      message.to,
      message.message,
      message.channel,
    ].some((value) =>
      String(value || "").toLowerCase().includes(query)
    );
  });

  els.messageCount.textContent =
    `${filtered.length} shown · ${state.messages.length} loaded`;

  els.lastUpdate.textContent =
    `Last update: ${status.updatedAt ? prettyTime({receivedAt: status.updatedAt}) : "—"}`;

  if (!filtered.length) {
    els.messages.innerHTML =
      `<div class="empty">No chat messages match the current filter.</div>`;
    return;
  }

  els.messages.innerHTML = filtered
    .map((message) => {
      const destination =
        message.to && message.to !== message.from
          ? `<span class="to">→ ${escapeHtml(message.to)}</span>`
          : "";

      const origin = message.origin || {};
      const hasOrigin =
        Number.isFinite(origin.x) ||
        Number.isFinite(origin.y) ||
        Number.isFinite(origin.z);

      const coordinates = hasOrigin
        ? `<div class="coordinates">X ${escapeHtml(origin.x ?? "—")} · Y ${escapeHtml(origin.y ?? "—")} · Z ${escapeHtml(origin.z ?? "—")}</div>`
        : "";

      return `
        <article class="message-card">
          <div class="message-head">
            <span class="channel ${channelClass(message.channel)}">${escapeHtml(message.channel || "Unknown")}</span>
            <span class="time">${escapeHtml(prettyTime(message))}</span>
          </div>
          <div class="sender">
            <strong>${escapeHtml(message.from || "Unknown")}</strong>
            ${destination}
          </div>
          <div class="text">${escapeHtml(message.message || "")}</div>
          ${coordinates}
        </article>
      `;
    })
    .join("");
}

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function refresh() {
  try {
    const [status, payload] = await Promise.all([
      fetchJson("./live/status.json"),
      fetchJson("./live/messages.json"),
    ]);

    state.status = status;
    state.messages = Array.isArray(payload.messages) ? payload.messages : [];
    updateChannelOptions();
    render();
  } catch (error) {
    state.status = {
      ...(state.status || {}),
      collectorConnected: false,
      error: String(error),
    };
    render();
  }
}

els.channelFilter.addEventListener("change", render);
els.searchInput.addEventListener("input", render);
els.refreshButton.addEventListener("click", refresh);

refresh();
setInterval(refresh, 1500);
