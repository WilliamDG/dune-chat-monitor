(() => {
  const $ = (s) => document.querySelector(s);
  const els = {
    badge: $("#connectionBadge"),
    serverName: $("#serverName"),
    messageCount: $("#messageCount"),
    queueName: $("#queueName"),
    lastMessage: $("#lastMessage"),
    messages: $("#messages"),
    search: $("#search"),
    refresh: $("#refresh")
  };
  let latestMessages = [];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
  }

  async function load(path) {
    const r = await fetch(`${path}?_=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }

  function render() {
    const q = els.search.value.trim().toLowerCase();
    const messages = latestMessages.filter((m) => !q || [
      m.routing_key, m.exchange_name, m.body_utf8, m.user_id, JSON.stringify(m.headers || {})
    ].join(" ").toLowerCase().includes(q));

    if (!messages.length) {
      els.messages.innerHTML = '<p class="empty">No matching captured messages.</p>';
      return;
    }

    els.messages.innerHTML = messages.map((m) => `
      <article class="message">
        <div class="message-meta">
          <span class="route">${escapeHtml(m.routing_key || "(empty)")}</span>
          <span>${escapeHtml(formatDate(m.received_at))}</span>
          <span>${Number(m.body_size || 0)} B</span>
        </div>
        <pre>${escapeHtml(m.body_utf8 || "[binary payload]")}</pre>
        <details>
          <summary>Metadata</summary>
          <pre>${escapeHtml(JSON.stringify(m, null, 2))}</pre>
        </details>
      </article>
    `).join("");
  }

  async function refresh() {
    try {
      const [status, data] = await Promise.all([
        load("./live/status.json"),
        load("./live/messages.json")
      ]);
      const online = status.collectorConnected === true;
      els.badge.className = online ? "badge badge-ok" : "badge badge-warn";
      els.badge.textContent = online ? "Collector online" : "Collector offline";
      els.serverName.textContent = `${status.serverName || "Dune Server"} · Read-only RAW capture`;
      els.messageCount.textContent = status.messageCount ?? 0;
      els.queueName.textContent = status.queue || "—";
      els.lastMessage.textContent = formatDate(status.lastReceivedAt);
      latestMessages = Array.isArray(data.messages) ? data.messages : [];
      render();
    } catch (e) {
      els.badge.className = "badge badge-warn";
      els.badge.textContent = "Waiting for collector";
    }
  }

  els.search.addEventListener("input", render);
  els.refresh.addEventListener("click", refresh);
  refresh();
  setInterval(refresh, 1500);
})();
