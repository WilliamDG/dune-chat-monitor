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
  identityAliases: new Map(),
  identityDirectoryLoadedAt: 0,
  identityRefreshPromise: null,
  identityProfileAttempts: new Map(),
  refreshing: false,
  historyBuckets: [],
  loadedHistoryBuckets: new Set(),
  historyLoading: false,
  historyError: "",
  lastHeadSignature: "",
  playerMenuTarget: null,
  toastTimer: null,
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
  playerActionMenu: document.getElementById("playerActionMenu"),
  playerActionTitle: document.getElementById("playerActionTitle"),
  actionToast: document.getElementById("actionToast"),
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

function identityKeyForAlias(value) {
  const alias = normalizeText(value).toLowerCase();
  if (!alias) return "";
  if (state.identities.has(alias)) return alias;
  return state.identityAliases.get(alias) || "";
}

function resolvedIdentity(value) {
  const key = identityKeyForAlias(value);
  return key ? state.identities.get(key) || null : null;
}

function identityFor(message) {
  const funcomId = normalizeText(message.from);
  const resolved = resolvedIdentity(funcomId);
  const spoofedName = message.spoofed ? normalizeText(message.spoofedUsername) : "";

  if (spoofedName) {
    return {
      ...(resolved || {}),
      name: spoofedName,
      funcomId: funcomId || resolved?.funcomId || "",
      steamId: resolved?.steamId || "",
      secondary: funcomId ? `Spoofed sender · ${funcomId}` : "Spoofed sender",
    };
  }

  if (resolved?.name) {
    return {
      ...resolved,
      name: resolved.name,
      funcomId: resolved.funcomId || funcomId,
      steamId: resolved.steamId || "",
      secondary: resolved.funcomId ? "" : funcomId,
    };
  }

  return {
    name: funcomId || "Unknown player",
    funcomId,
    steamId: "",
    secondary: "",
    map: "",
  };
}

function recipientIdentityFor(message) {
  const recipient = normalizeText(message.to);
  if (!recipient) return null;
  const resolved = resolvedIdentity(recipient);
  if (resolved) {
    return {
      ...resolved,
      name: resolved.name || recipient,
      funcomId: resolved.funcomId || "",
      steamId: resolved.steamId || "",
      secondary: "",
    };
  }
  return {
    name: recipient,
    funcomId: "",
    steamId: "",
    secondary: "",
    map: "",
  };
}

function friendlyMapName(value) {
  const raw = normalizeText(value);
  if (!raw) return "";
  const lower = raw.toLowerCase();
  if (lower.includes("deepdesert")) return "Deep Desert";
  if (lower.includes("survival_1") || lower.includes("haggabasin") || lower.includes("hagga basin")) return "Hagga Basin";
  if (lower.includes("overmap")) return "Overmap";
  return raw
    .replace(/\.dim_?\d+$/i, "")
    .replace(/\.\d+$/i, "")
    .replace(/_+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function messageMapName(message, identity = identityFor(message)) {
  return friendlyMapName(
    message.mapName
      || identity?.map
      || identity?.partitionMap
      || ""
  );
}

function messageMapContextLabel(message, identity = identityFor(message)) {
  const mapName = messageMapName(message, identity);
  const sietchName = normalizeText(message.sietchName);
  if (mapName && sietchName && mapName.toLowerCase() === "hagga basin") {
    return `${mapName} - ${sietchName}`;
  }
  return mapName;
}

function searchableText(message) {
  const identity = identityFor(message);
  const recipient = recipientIdentityFor(message);
  return [
    message.from,
    message.to,
    message.message,
    message.channel,
    message.mapName,
    message.routingKey,
    message.mapDimension,
    message.sietchName,
    messageMapContextLabel(message, identity),
    identity.name,
    identity.steamId,
    identity.funcomId,
    identity.secondary,
    recipient?.name,
    recipient?.steamId,
    recipient?.funcomId,
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

function playerButtonMarkup(identity, message, role = "sender") {
  const name = normalizeText(identity?.name) || "Unknown player";
  const messageId = normalizeText(message?.id);
  return `<button class="player-name-button" type="button" data-player-role="${escapeHtml(role)}" data-message-id="${escapeHtml(messageId)}" aria-haspopup="menu" aria-expanded="false">${escapeHtml(name)}<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4"/></svg></button>`;
}

function recipientMarkup(message) {
  const key = channelKey(message.channel);
  if (key !== "whispers" && key !== "whisper") return "";
  const recipient = recipientIdentityFor(message);
  if (!recipient) return "";
  return `<div class="whisper-recipient"><span class="whisper-arrow" aria-hidden="true">→</span><span class="whisper-to-label">To</span>${playerButtonMarkup(recipient, message, "recipient")}</div>`;
}

function channelBadgeMarkup(message, identity) {
  const channel = normalizeChannel(message.channel) || "Unknown";
  if (channelKey(channel) === "map") {
    const contextLabel = messageMapContextLabel(message, identity);
    if (contextLabel) {
      return `<span class="channel-badge" title="Map and Sietch where the message was sent"><span class="channel-map-name">${escapeHtml(contextLabel)}</span></span>`;
    }
  }
  return `<span class="channel-badge"><span class="channel-label">${escapeHtml(channel)}</span></span>`;
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
    const secondary = identity.secondary
      ? `<div class="sender-secondary">${escapeHtml(identity.secondary)}</div>`
      : "";
    const footerParts = [
      locationMarkup(message.origin),
    ].filter(Boolean).join("");
    const recipient = recipientMarkup(message);

    return `
      <article class="message-card ${channelTone(message.channel)}">
        <div class="message-content">
          <div class="message-topline">
            ${channelBadgeMarkup(message, identity)}
            <time class="message-time">${escapeHtml(prettyTime(message))}</time>
          </div>

          <div class="identity-row">
            <span class="avatar" aria-hidden="true">${escapeHtml(avatarLetter(identity.name))}</span>
            <div class="identity">
              <div class="sender-line">
                ${playerButtonMarkup(identity, message, "sender")}
              </div>
              ${secondary}
              ${recipient}
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

function identityAliasesFromRow(row) {
  return [
    playerField(row, "funcom_id", "funcomId"),
    playerField(row, "fls_id", "flsId"),
    playerField(row, "action_player_id", "actionPlayerId", "player_id", "playerId"),
    playerField(row, "account_id", "accountId"),
    playerField(row, "actor_id", "actorId", "player_pawn_id", "playerPawnId"),
    playerField(row, "player_controller_id", "playerControllerId", "controller_id", "controllerId"),
    playerField(row, "character_name", "characterName", "name"),
    playerField(row, "platform_id", "platformId"),
  ].map(normalizeText).filter(Boolean);
}

function registerIdentityAliases(key, aliases) {
  for (const alias of aliases) {
    const normalized = normalizeText(alias).toLowerCase();
    if (normalized) state.identityAliases.set(normalized, key);
  }
}

function upsertIdentity(row, fallback = {}) {
  const funcomId = playerField(row, "funcom_id", "funcomId") || fallback.funcomId || "";
  if (!funcomId) return null;

  const key = funcomId.toLowerCase();
  const previous = state.identities.get(key) || fallback || {};
  const platformName = playerField(row, "platform_name", "platformName").toLowerCase();
  const platformId = playerField(row, "platform_id", "platformId");
  const steamId = (!platformName || platformName === "steam") && /^\d{17}$/.test(platformId) ? platformId : "";

  const identity = {
    ...previous,
    funcomId,
    name: playerField(row, "character_name", "characterName", "name") || previous.name || "",
    steamId: steamId || previous.steamId || "",
    actorId: playerField(row, "actor_id", "actorId", "player_pawn_id", "playerPawnId") || previous.actorId || "",
    flsId: playerField(row, "fls_id", "flsId") || previous.flsId || "",
    accountId: playerField(row, "account_id", "accountId") || previous.accountId || "",
    actionPlayerId: playerField(row, "action_player_id", "actionPlayerId", "player_id", "playerId") || previous.actionPlayerId || "",
    controllerId: playerField(row, "player_controller_id", "playerControllerId", "controller_id", "controllerId") || previous.controllerId || "",
    map: playerField(row, "map", "player_map", "playerMap") || previous.map || "",
    partitionMap: playerField(row, "partition_map", "partitionMap") || previous.partitionMap || "",
    partitionId: playerField(row, "partition_id", "partitionId") || previous.partitionId || "",
    dimensionIndex: playerField(row, "dimension_index", "dimensionIndex") || previous.dimensionIndex || "",
  };

  state.identities.set(key, identity);
  registerIdentityAliases(key, [
    ...identityAliasesFromRow(row),
    identity.funcomId,
    identity.name,
    identity.steamId,
    identity.flsId,
    identity.accountId,
    identity.actionPlayerId,
    identity.actorId,
    identity.controllerId,
  ]);
  return identity;
}

function upsertDirectoryIdentity(row) {
  upsertIdentity(row);
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
  upsertIdentity(player, identity);
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
      const aliases = [...new Set(state.messages.flatMap((message) => [message.from, message.to]).map((value) => normalizeText(value).toLowerCase()).filter(Boolean))];
      const identities = [...new Map(aliases
        .map((alias) => resolvedIdentity(alias))
        .filter(Boolean)
        .map((identity) => [normalizeText(identity.funcomId).toLowerCase(), identity])).values()]
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

function messageById(id) {
  const wanted = normalizeText(id);
  return state.messages.find((message) => normalizeText(message?.id) === wanted) || null;
}

function playerTargetFor(message, role) {
  if (!message) return null;
  return role === "recipient" ? recipientIdentityFor(message) : identityFor(message);
}

function showActionToast(text) {
  if (!els.actionToast) return;
  clearTimeout(state.toastTimer);
  els.actionToast.textContent = text;
  els.actionToast.hidden = false;
  state.toastTimer = setTimeout(() => {
    els.actionToast.hidden = true;
  }, 1800);
}

async function copyText(value, label) {
  const text = normalizeText(value);
  if (!text) {
    showActionToast(`${label} unavailable`);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showActionToast(`${label} copied`);
}

function closePlayerMenu() {
  if (!els.playerActionMenu || els.playerActionMenu.hidden) return;
  const previousAnchor = state.playerMenuTarget?.anchor;
  if (previousAnchor?.isConnected) previousAnchor.setAttribute("aria-expanded", "false");
  els.playerActionMenu.hidden = true;
  state.playerMenuTarget = null;
}

function positionPlayerMenu(anchor) {
  if (!anchor || !els.playerActionMenu) return;
  const rect = anchor.getBoundingClientRect();
  const menu = els.playerActionMenu;
  const margin = 10;
  const width = menu.offsetWidth || 220;
  const height = menu.offsetHeight || 190;
  let left = rect.left;
  let top = rect.bottom + 7;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  if (left < margin) left = margin;
  if (top + height > window.innerHeight - margin) top = Math.max(margin, rect.top - height - 7);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function openPlayerMenu(anchor, target) {
  if (!els.playerActionMenu || !target) return;
  closePlayerMenu();

  const steamId = /^\d{17}$/.test(normalizeText(target.steamId)) ? normalizeText(target.steamId) : "";
  const funcomId = normalizeText(target.funcomId);
  const name = normalizeText(target.name) || "Unknown player";
  state.playerMenuTarget = { anchor, target: { ...target, name, steamId, funcomId } };

  els.playerActionTitle.textContent = name;
  for (const button of els.playerActionMenu.querySelectorAll("[data-player-action]")) {
    const action = button.dataset.playerAction;
    button.disabled = (action === "copy-steam" || action === "open-steam") ? !steamId
      : action === "copy-funcom" ? !funcomId
      : action === "copy-name" ? !name
      : false;
  }

  anchor.setAttribute("aria-expanded", "true");
  els.playerActionMenu.hidden = false;
  positionPlayerMenu(anchor);
}

async function handlePlayerAction(action) {
  const target = state.playerMenuTarget?.target;
  if (!target) return;
  if (action === "copy-steam") {
    await copyText(target.steamId, "SteamID");
  } else if (action === "open-steam") {
    if (/^\d{17}$/.test(target.steamId)) {
      const popup = window.open(`https://steamcommunity.com/profiles/${encodeURIComponent(target.steamId)}`, "_blank", "noopener,noreferrer");
      if (popup) popup.opener = null;
    }
  } else if (action === "copy-funcom") {
    await copyText(target.funcomId, "Funcom ID");
  } else if (action === "copy-name") {
    await copyText(target.name, "Player name");
  }
  closePlayerMenu();
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

els.messages.addEventListener("click", (event) => {
  const button = event.target.closest(".player-name-button[data-message-id][data-player-role]");
  if (!button) return;
  event.stopPropagation();
  const message = messageById(button.dataset.messageId);
  const target = playerTargetFor(message, button.dataset.playerRole || "sender");
  if (!target) return;

  if (state.playerMenuTarget?.anchor === button && !els.playerActionMenu.hidden) {
    closePlayerMenu();
    return;
  }
  openPlayerMenu(button, target);
});

els.playerActionMenu.addEventListener("click", (event) => {
  const button = event.target.closest("[data-player-action]");
  if (!button || button.disabled) return;
  void handlePlayerAction(button.dataset.playerAction || "");
});

document.addEventListener("click", (event) => {
  if (els.playerActionMenu.hidden) return;
  if (els.playerActionMenu.contains(event.target)) return;
  if (event.target.closest?.(".player-name-button")) return;
  closePlayerMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePlayerMenu();
});

window.addEventListener("resize", () => {
  if (state.playerMenuTarget?.anchor && !els.playerActionMenu.hidden) positionPlayerMenu(state.playerMenuTarget.anchor);
});

window.addEventListener("scroll", () => {
  if (state.playerMenuTarget?.anchor && !els.playerActionMenu.hidden) positionPlayerMenu(state.playerMenuTarget.anchor);
}, true);

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
