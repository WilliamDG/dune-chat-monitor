const IDENTITY_REFRESH_MS = 5 * 60 * 1000;
const PROFILE_CONCURRENCY = 6;
const MAX_PLAYER_PAGES = 10;
const PLAYER_PAGE_SIZE = 200;
const REFRESH_MS = 1500;

const CHANNEL_PRIORITY = [
  "Map",
  "Proximity",
  "Guild",
  "Faction",
  "Party",
  "Whispers",
];

const state = {
  messages: [],
  status: null,
  selectedChannel: "",
  identities: new Map(),
  identityDirectoryLoadedAt: 0,
  identityRefreshPromise: null,
  identityProfileAttempts: new Map(),
  refreshing: false,
  historyBuckets: [],
  loadedHistoryBuckets: new Set(),
  historyLoading: false,
  historyError: "",
  lastHeadSignature: "",
};

const els = {
  channelTabs: document.getElementById("channelTabs"),
  searchInput: document.getElementById("searchInput"),
  refreshButton: document.getElementById("refreshButton"),
  liveBadge: document.getElementById("liveBadge"),
  liveLabel: document.getElementById("liveLabel"),
  healthBanner: document.getElementById("healthBanner"),
  messageCount: document.getElementById("messageCount"),
  lastUpdate: document.getElementById("lastUpdate"),
  messages: document.getElementById("messages"),
  historySentinel: document.getElementById("historySentinel"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeText(value) {
  return String(value ?? "").trim();
}

function normalizeChannel(value) {
  return normalizeText(value);
}

function channelKey(value) {
  return normalizeChannel(value).toLowerCase();
}

function configuredTimezone() {
  const timezone = normalizeText(state.status?.timezone);
  if (!timezone) return undefined;
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: timezone }).format(new Date());
    return timezone;
  } catch {
    return undefined;
  }
}

function parseMessageDate(message) {
  const value = message.gameTimestampUtc || message.gameTimestampLocal || message.receivedAt;
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function messageSortValue(message) {
  const parsed = parseMessageDate(message);
  if (parsed) return parsed.getTime();
  const received = new Date(message.receivedAt || "");
  return Number.isNaN(received.getTime()) ? 0 : received.getTime();
}

function prettyTime(message) {
  const parsed = parseMessageDate(message);
  if (!parsed) {
    return normalizeText(message.gameTimestampLocal || message.gameTimestampUtc || message.receivedAt) || "—";
  }

  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: configuredTimezone(),
  }).format(parsed);
}

function prettyUpdateTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: configuredTimezone(),
  }).format(parsed);
}

function channelTone(channel) {
  const key = channelKey(channel);
  if (key === "map") return "tone-map";
  if (key === "proximity") return "tone-proximity";
  if (key === "whispers" || key === "whisper") return "tone-whispers";
  if (key === "sietch") return "tone-sietch";
  if (key === "party" || key === "group") return "tone-party";
  if (key === "guild") return "tone-guild";
  if (key === "faction") return "tone-faction";
  return "tone-default";
}

function globalChannelCounts() {
  const counts = new Map();
  const names = new Map();

  for (const name of CHANNEL_PRIORITY) {
    const key = channelKey(name);
    names.set(key, name);
    counts.set(key, 0);
  }

  for (const row of Array.isArray(state.status?.channels) ? state.status.channels : []) {
    const name = normalizeChannel(row?.name);
    if (!name) continue;
    const key = channelKey(name);
    names.set(key, name);
    counts.set(key, Number(row?.count) || 0);
  }

  for (const message of state.messages) {
    const name = normalizeChannel(message.channel);
    if (!name) continue;
    const key = channelKey(name);
    if (!names.has(key)) names.set(key, name);
    if (!counts.has(key)) counts.set(key, 0);
  }

  return { counts, names };
}

function sortedChannels() {
  const { names } = globalChannelCounts();
  const priority = new Map(CHANNEL_PRIORITY.map((name, index) => [name.toLowerCase(), index]));
  return [...names.entries()]
    .sort(([keyA, nameA], [keyB, nameB]) => {
      const rankA = priority.has(keyA) ? priority.get(keyA) : 1000;
      const rankB = priority.has(keyB) ? priority.get(keyB) : 1000;
      if (rankA !== rankB) return rankA - rankB;
      return nameA.localeCompare(nameB, undefined, { sensitivity: "base" });
    })
    .map(([, name]) => name);
}

function renderChannelTabs() {
  const channels = sortedChannels();
  const { counts } = globalChannelCounts();

  if (state.selectedChannel && !channels.some((channel) => channelKey(channel) === channelKey(state.selectedChannel))) {
    state.selectedChannel = "";
  }

  const total = Number(state.status?.messageCount) || state.messages.length;
  const tabs = [
    { value: "", label: "All", count: total },
    ...channels.map((channel) => ({
      value: channel,
      label: channel,
      count: counts.get(channelKey(channel)) || 0,
    })),
  ];

  els.channelTabs.innerHTML = tabs.map((tab) => {
    const active = channelKey(tab.value) === channelKey(state.selectedChannel);
    return `<button class="channel-tab${active ? " is-active" : ""}" type="button" role="tab" aria-selected="${active}" data-channel="${escapeHtml(tab.value)}">${escapeHtml(tab.label)}<span class="tab-count">${tab.count}</span></button>`;
  }).join("");
}

function numericCoordinate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function hasMeaningfulOrigin(origin) {
  const values = [origin?.x, origin?.y, origin?.z]
    .map(numericCoordinate)
    .filter((value) => value !== null);
  return values.some((value) => Math.abs(value) > 0.0001);
}

function formatCoordinate(value) {
  const number = numericCoordinate(value);
  if (number === null) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(number);
}

function avatarLetter(value) {
  const text = normalizeText(value);
  return text ? text.slice(0, 1).toUpperCase() : "?";
}

function identityFor(message) {
  const funcomId = normalizeText(message.from);
  const resolved = funcomId ? state.identities.get(funcomId.toLowerCase()) : null;
  const spoofedName = message.spoofed ? normalizeText(message.spoofedUsername) : "";

  if (spoofedName) {
    return {
      name: spoofedName,
      steamId: "",
      secondary: funcomId ? `Spoofed sender · ${funcomId}` : "Spoofed sender",
    };
  }

  if (resolved?.name) {
    return {
      name: resolved.name,
      steamId: resolved.steamId || "",
      secondary: resolved.steamId ? "" : funcomId,
    };
  }

  return {
    name: funcomId || "Unknown player",
    steamId: "",
    secondary: "",
  };
}

function searchableText(message) {
  const identity = identityFor(message);
  return [
    message.from,
    message.message,
    message.channel,
    identity.name,
    identity.steamId,
    identity.secondary,
  ].join(" ").toLowerCase();
}

function filteredMessages() {
  const query = els.searchInput.value.trim().toLowerCase();
  return state.messages.filter((message) => {
    if (state.selectedChannel && channelKey(message.channel) !== channelKey(state.selectedChannel)) return false;
    return !query || searchableText(message).includes(query);
  });
}

function locationMarkup(origin) {
  if (!hasMeaningfulOrigin(origin)) return "";
  return `
    <span class="location" title="Message origin">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>
      X ${escapeHtml(formatCoordinate(origin?.x))} · Y ${escapeHtml(formatCoordinate(origin?.y))} · Z ${escapeHtml(formatCoordinate(origin?.z))}
    </span>`;
}

function totalStoredMessages() {
  return Number(state.status?.messageCount) || state.messages.length;
}

function nextHistoryBucket() {
  return state.historyBuckets.find((bucket) => !state.loadedHistoryBuckets.has(bucket.key)) || null;
}

function renderHistorySentinel() {
  const next = nextHistoryBucket();
  if (state.historyLoading) {
    els.historySentinel.hidden = false;
    els.historySentinel.innerHTML = `<span class="history-spinner" aria-hidden="true"></span><span>Loading older messages…</span>`;
    return;
  }
  if (state.historyError) {
    els.historySentinel.hidden = false;
    els.historySentinel.innerHTML = `<span>Could not load older messages. Scroll here or press Refresh to retry.</span>`;
    return;
  }
  if (next) {
    els.historySentinel.hidden = false;
    els.historySentinel.innerHTML = `<span>Scroll for older messages</span>`;
    return;
  }
  els.historySentinel.hidden = true;
  els.historySentinel.innerHTML = "";
}

function render() {
  renderChannelTabs();
  const filtered = filteredMessages();
  const total = totalStoredMessages();

  if (state.messages.length) {
    const shown = filtered.length;
    els.messageCount.textContent = total > state.messages.length
      ? `${shown} shown · ${state.messages.length} loaded · ${total} total`
      : `${shown} shown · ${state.messages.length} loaded`;
  } else {
    els.messageCount.textContent = total ? `${total} stored messages` : "No captured messages";
  }

  const updated = prettyUpdateTime(state.status?.updatedAt);
  els.lastUpdate.textContent = updated ? `Updated ${updated}` : "";

  const statusError = normalizeText(state.status?.error);
  const collectorUnavailable = state.status && state.status.collectorConnected === false;
  const live = state.status?.collectorConnected === true && !statusError;
  els.liveBadge.classList.toggle("is-offline", !live);
  els.liveBadge.classList.toggle("is-live", live);
  els.liveLabel.textContent = live ? "Live" : "Offline";

  if (collectorUnavailable || statusError) {
    els.healthBanner.hidden = false;
    els.healthBanner.textContent = statusError || "Chat collector is currently unavailable.";
  } else {
    els.healthBanner.hidden = true;
    els.healthBanner.textContent = "";
  }

  if (!filtered.length) {
    els.messages.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">•••</span>
        <span>${state.messages.length ? "No loaded messages match the current filters yet." : "Waiting for chat messages…"}</span>
      </div>`;
    renderHistorySentinel();
    return;
  }

  els.messages.innerHTML = filtered.map((message) => {
    const identity = identityFor(message);
    const steamId = identity.steamId
      ? `<span class="steam-id">(${escapeHtml(identity.steamId)})</span>`
      : "";
    const secondary = identity.secondary
      ? `<div class="sender-secondary">${escapeHtml(identity.secondary)}</div>`
      : "";
    const footerParts = locationMarkup(message.origin);

    return `
      <article class="message-card ${channelTone(message.channel)}">
        <div class="message-content">
          <div class="message-topline">
            <span class="channel-badge">${escapeHtml(message.channel || "Unknown")}</span>
            <time class="message-time">${escapeHtml(prettyTime(message))}</time>
          </div>

          <div class="identity-row">
            <span class="avatar" aria-hidden="true">${escapeHtml(avatarLetter(identity.name))}</span>
            <div class="identity">
              <div class="sender-line">
                <span class="sender-name">${escapeHtml(identity.name)}</span>
                ${steamId}
              </div>
              ${secondary}
            </div>
          </div>

          <div class="message-text">${escapeHtml(message.message || "")}</div>
          ${footerParts ? `<div class="message-footer">${footerParts}</div>` : ""}
        </div>
      </article>`;
  }).join("");

  renderHistorySentinel();
}

async function fetchJson(path) {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}_=${Date.now()}`, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function messageKey(message) {
  const id = normalizeText(message?.id);
  if (id) return `id:${id}`;
  return [message?.receivedAt, message?.channel, message?.from, message?.message].map(normalizeText).join("|");
}

function mergeMessages(incoming) {
  const byKey = new Map(state.messages.map((message) => [messageKey(message), message]));
  let added = 0;

  for (const raw of Array.isArray(incoming) ? incoming : []) {
    if (!raw || typeof raw !== "object") continue;
    const message = { ...raw, channel: normalizeChannel(raw.channel) || "Unknown" };
    const key = messageKey(message);
    if (!byKey.has(key)) added += 1;
    byKey.set(key, message);
  }

  state.messages = [...byKey.values()].sort((a, b) => messageSortValue(b) - messageSortValue(a));
  return added;
}

function updateHistoryBuckets(payload) {
  const buckets = Array.isArray(payload?.buckets) ? payload.buckets : [];
  state.historyBuckets = buckets
    .map((bucket) => ({
      key: normalizeText(bucket?.key),
      file: normalizeText(bucket?.file),
      count: Number(bucket?.count) || 0,
    }))
    .filter((bucket) => bucket.key && bucket.file);
}

async function loadMoreHistory() {
  if (state.historyLoading) return;
  const bucket = nextHistoryBucket();
  if (!bucket) {
    renderHistorySentinel();
    return;
  }

  state.historyLoading = true;
  state.historyError = "";
  renderHistorySentinel();

  try {
    const payload = await fetchJson(`./live/history/${encodeURIComponent(bucket.file)}`);
    state.loadedHistoryBuckets.add(bucket.key);
    mergeMessages(payload?.messages);
    render();
    void refreshPlayerIdentities(false);
  } catch (error) {
    state.historyError = String(error?.message || error);
    renderHistorySentinel();
  } finally {
    state.historyLoading = false;
    renderHistorySentinel();
  }
}

function rowsFromPlayerPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.rows)) return payload.rows;
  if (Array.isArray(payload?.players)) return payload.players;
  if (Array.isArray(payload?.result?.rows)) return payload.result.rows;
  return [];
}

function playerField(row, ...keys) {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function upsertDirectoryIdentity(row) {
  const funcomId = playerField(row, "funcom_id", "funcomId");
  if (!funcomId) return;

  const key = funcomId.toLowerCase();
  const previous = state.identities.get(key) || {};
  state.identities.set(key, {
    ...previous,
    funcomId,
    name: playerField(row, "character_name", "characterName", "name") || previous.name || "",
    actorId: playerField(row, "actor_id", "actorId", "player_pawn_id", "playerPawnId") || previous.actorId || "",
  });
}

async function loadPlayerDirectory() {
  let page = 0;
  let loaded = 0;

  while (page < MAX_PLAYER_PAGES) {
    const payload = await fetchJson(`/api/players?page=${page}&pageSize=${PLAYER_PAGE_SIZE}&sortColumn=character_name&sortDirection=asc`);
    const rows = rowsFromPlayerPayload(payload);
    rows.forEach(upsertDirectoryIdentity);
    loaded += rows.length;

    const total = Number(payload?.totalCount ?? payload?.totalPlayers ?? payload?.result?.totalCount);
    if (!rows.length || rows.length < PLAYER_PAGE_SIZE || (Number.isFinite(total) && loaded >= total)) break;
    page += 1;
  }
}

function profilePlayer(payload) {
  if (payload?.player && typeof payload.player === "object") return payload.player;
  if (payload?.profile?.player && typeof payload.profile.player === "object") return payload.profile.player;
  if (payload?.profile && typeof payload.profile === "object") return payload.profile;
  return payload && typeof payload === "object" ? payload : {};
}

async function enrichIdentityProfile(identity) {
  if (!identity?.actorId) return;
  const payload = await fetchJson(`/api/players/${encodeURIComponent(identity.actorId)}`);
  const player = profilePlayer(payload);
  const funcomId = playerField(player, "funcom_id", "funcomId") || identity.funcomId;
  if (!funcomId) return;

  const platformName = playerField(player, "platform_name", "platformName").toLowerCase();
  const platformId = playerField(player, "platform_id", "platformId");
  const steamId = (!platformName || platformName === "steam") && /^\d{17}$/.test(platformId) ? platformId : "";
  const key = funcomId.toLowerCase();
  const previous = state.identities.get(key) || identity;

  state.identities.set(key, {
    ...previous,
    funcomId,
    name: playerField(player, "character_name", "characterName", "name") || previous.name || "",
    steamId: steamId || previous.steamId || "",
    actorId: playerField(player, "actor_id", "actorId", "player_pawn_id", "playerPawnId") || previous.actorId || "",
  });
}

async function runBatched(items, worker, concurrency = PROFILE_CONCURRENCY) {
  for (let index = 0; index < items.length; index += concurrency) {
    const batch = items.slice(index, index + concurrency);
    await Promise.allSettled(batch.map(worker));
  }
}

async function refreshPlayerIdentities(force = false) {
  if (window.parent === window) return;
  if (state.identityRefreshPromise) return state.identityRefreshPromise;

  state.identityRefreshPromise = (async () => {
    try {
      const directoryStale = force || Date.now() - state.identityDirectoryLoadedAt >= IDENTITY_REFRESH_MS;
      if (directoryStale) {
        await loadPlayerDirectory();
        state.identityDirectoryLoadedAt = Date.now();
      }

      const now = Date.now();
      const senders = [...new Set(state.messages.map((message) => normalizeText(message.from).toLowerCase()).filter(Boolean))];
      const identities = senders
        .map((sender) => state.identities.get(sender))
        .filter((identity) => {
          if (!identity?.actorId || identity.steamId) return false;
          const key = normalizeText(identity.funcomId).toLowerCase();
          const lastAttempt = state.identityProfileAttempts.get(key) || 0;
          return force || now - lastAttempt >= IDENTITY_REFRESH_MS;
        });

      for (const identity of identities) {
        const key = normalizeText(identity.funcomId).toLowerCase();
        if (key) state.identityProfileAttempts.set(key, now);
      }

      await runBatched(identities, enrichIdentityProfile);
      render();
    } catch (error) {
      console.debug("Dune Chat Monitor: player identity enrichment unavailable", error);
      state.identityDirectoryLoadedAt = Date.now();
    } finally {
      state.identityRefreshPromise = null;
    }
  })();

  return state.identityRefreshPromise;
}

function headSignature(status, payload, history) {
  const ids = Array.isArray(payload?.messages) ? payload.messages.slice(0, 4).map((message) => message?.id || "").join(",") : "";
  const buckets = Array.isArray(history?.buckets) ? history.buckets.slice(0, 3).map((bucket) => `${bucket?.key}:${bucket?.count}`).join(",") : "";
  return [status?.updatedAt || "", status?.messageCount || 0, payload?.updatedAt || "", ids, buckets].join("|");
}

async function refresh({ forceIdentities = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  els.refreshButton.classList.add("is-busy");

  try {
    const [status, payload, history] = await Promise.all([
      fetchJson("./live/status.json"),
      fetchJson("./live/messages.json"),
      fetchJson("./live/history/index.json"),
    ]);

    const signature = headSignature(status, payload, history);
    const changed = signature !== state.lastHeadSignature;
    state.lastHeadSignature = signature;
    state.status = status;
    updateHistoryBuckets(history);
    state.historyError = "";
    mergeMessages(payload?.messages);

    if (changed || forceIdentities) render();
    void refreshPlayerIdentities(forceIdentities);
  } catch (error) {
    state.status = {
      ...(state.status || {}),
      collectorConnected: false,
      error: `Unable to load chat data: ${String(error?.message || error)}`,
    };
    render();
  } finally {
    state.refreshing = false;
    els.refreshButton.classList.remove("is-busy");
  }
}

els.channelTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-channel]");
  if (!button) return;
  state.selectedChannel = button.dataset.channel || "";
  render();
});

els.searchInput.addEventListener("input", render);
els.refreshButton.addEventListener("click", () => refresh({ forceIdentities: true }));
els.historySentinel.addEventListener("click", () => loadMoreHistory());

const historyObserver = new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting)) void loadMoreHistory();
}, { root: null, rootMargin: "320px 0px", threshold: 0 });

historyObserver.observe(els.historySentinel);

refresh();
setInterval(refresh, REFRESH_MS);
