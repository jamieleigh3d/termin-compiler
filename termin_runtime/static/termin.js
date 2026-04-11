/**
 * Termin Client Runtime — SSR hydration + WebSocket reactive updates.
 *
 * Loaded as an ES module after SSR page render. Connects to the server
 * via WebSocket, subscribes to content changes, and patches the DOM
 * when push events arrive. Progressive enhancement: page works without JS.
 *
 * No build step. No npm dependencies. CEL evaluator loaded from CDN (already in page).
 */

const TERMIN_VERSION = "0.3.0";

// ── State ──
const state = {
  registry: null,
  bootstrap: null,
  identity: null,
  ws: null,
  wsUrl: null,
  connected: false,
  subscriptions: new Map(),   // channel_id -> Set<callback>
  cache: new Map(),           // "content.<name>" -> Map<id, record>
  pendingRequests: new Map(), // ref -> { resolve, reject }
  refCounter: 0,
  reconnectDelay: 1000,
  reconnectTimer: null,
  indicator: null,
};

// ── Bootstrap ──

async function init() {
  try {
    // Fetch registry and bootstrap in parallel
    const [registryRes, bootstrapRes] = await Promise.all([
      fetch("/runtime/registry"),
      fetch("/runtime/bootstrap"),
    ]);

    if (!registryRes.ok || !bootstrapRes.ok) {
      console.warn("[Termin] Bootstrap endpoints not available, running in SSR-only mode");
      return;
    }

    state.registry = await registryRes.json();
    state.bootstrap = await bootstrapRes.json();
    state.identity = state.bootstrap.identity;

    // Register client-safe compute functions (if available)
    registerComputes(state.bootstrap.computes || []);

    // Derive WebSocket URL from page origin (same host)
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    state.wsUrl = `${proto}//${location.host}/runtime/ws`;

    // Create connection indicator
    createIndicator();

    // Connect WebSocket
    connectWebSocket();

    // Hydrate DOM
    hydrateAll();

    console.log(`[Termin] Client runtime ${TERMIN_VERSION} initialized`);
  } catch (err) {
    console.warn("[Termin] Bootstrap failed, SSR-only mode:", err.message);
  }
}

// ── Compute Registration ──

function registerComputes(computes) {
  // Compute functions are registered on the page-level context object
  // by the server-generated inline script (ctx["FuncName"] = function...).
  // This function is a placeholder for future client-side compute
  // registration from the bootstrap API response.
  // Currently, server-side JS generation handles this in app.py.
}

// ── WebSocket ──

function connectWebSocket() {
  if (!state.wsUrl) return;

  try {
    state.ws = new WebSocket(state.wsUrl);
  } catch (err) {
    console.warn("[Termin] WebSocket creation failed:", err.message);
    scheduleReconnect();
    return;
  }

  state.ws.onopen = () => {
    state.connected = true;
    state.reconnectDelay = 1000;
    updateIndicator(true);
    console.log("[Termin] WebSocket connected");

    // Re-subscribe all active subscriptions
    for (const [ch] of state.subscriptions) {
      sendFrame("subscribe", ch, {});
    }
  };

  state.ws.onmessage = (event) => {
    try {
      const frame = JSON.parse(event.data);
      handleFrame(frame);
    } catch (err) {
      console.warn("[Termin] Bad frame:", err.message);
    }
  };

  state.ws.onclose = () => {
    state.connected = false;
    updateIndicator(false);
    console.log("[Termin] WebSocket disconnected");
    scheduleReconnect();
  };

  state.ws.onerror = () => {
    // onclose will fire after onerror
  };
}

function scheduleReconnect() {
  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 30000);
    connectWebSocket();
  }, state.reconnectDelay);
}

function sendFrame(op, ch, payload, ref) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return null;
  ref = ref || `ref-${++state.refCounter}`;
  state.ws.send(JSON.stringify({ v: 1, ch, op, ref, payload }));
  return ref;
}

// ── Frame Handler ──

function handleFrame(frame) {
  const { ch, op, ref, payload } = frame;

  if (op === "push") {
    // Update cache and notify subscribers
    if (ch === "runtime.identity") {
      state.identity = payload;
      return;
    }
    updateCache(ch, payload);
    notifySubscribers(ch, payload);
  } else if (op === "response") {
    // Resolve pending request
    const pending = state.pendingRequests.get(ref);
    if (pending) {
      pending.resolve(payload);
      state.pendingRequests.delete(ref);
    }
    // If this is a subscribe response with current data, populate cache
    if (payload && payload.current) {
      const parts = ch.split(".");
      if (parts.length >= 2 && parts[0] === "content") {
        const contentName = parts[1];
        const cacheKey = `content.${contentName}`;
        const recordMap = new Map();
        for (const record of payload.current) {
          recordMap.set(record.id, record);
        }
        state.cache.set(cacheKey, recordMap);
      }
    }
  } else if (op === "error") {
    const pending = state.pendingRequests.get(ref);
    if (pending) {
      pending.reject(new Error(payload.message || "Unknown error"));
      state.pendingRequests.delete(ref);
    }
  }
}

// ── Cache ──

function updateCache(channelId, data) {
  // Parse: content.<name>.created/updated/deleted
  const parts = channelId.split(".");
  if (parts.length < 3 || parts[0] !== "content") return;

  const contentName = parts[1];
  const action = parts[2]; // created, updated, deleted
  const cacheKey = `content.${contentName}`;

  if (!state.cache.has(cacheKey)) {
    state.cache.set(cacheKey, new Map());
  }
  const records = state.cache.get(cacheKey);

  if (action === "deleted") {
    const id = data.record_id || (data.id);
    if (id != null) records.delete(id);
  } else if (data && data.id != null) {
    records.set(data.id, data);
  }
}

function getCachedRecords(contentName) {
  const cacheKey = `content.${contentName}`;
  const records = state.cache.get(cacheKey);
  return records ? Array.from(records.values()) : [];
}

// ── Subscriptions ──

function subscribe(channelId, callback) {
  if (!state.subscriptions.has(channelId)) {
    state.subscriptions.set(channelId, new Set());
    // Send subscribe frame to server
    sendFrame("subscribe", channelId, {});
  }
  state.subscriptions.get(channelId).add(callback);

  // Return unsubscribe function
  return () => {
    const cbs = state.subscriptions.get(channelId);
    if (cbs) {
      cbs.delete(callback);
      if (cbs.size === 0) {
        state.subscriptions.delete(channelId);
        sendFrame("unsubscribe", channelId, {});
      }
    }
  };
}

function notifySubscribers(channelId, data) {
  // Exact match
  const exact = state.subscriptions.get(channelId);
  if (exact) exact.forEach(cb => cb(channelId, data));

  // Prefix match: "content.products.created" notifies "content.products" subscribers
  const parts = channelId.split(".");
  for (let i = parts.length - 1; i >= 2; i--) {
    const prefix = parts.slice(0, i).join(".");
    const prefixCbs = state.subscriptions.get(prefix);
    if (prefixCbs) prefixCbs.forEach(cb => cb(channelId, data));
  }
}

// ── DOM Hydration ──

function hydrateAll() {
  hydrateDataTables();
  hydrateAggregations();
  hydrateForms();
}

function hydrateDataTables() {
  const tables = document.querySelectorAll("[data-termin-component='data_table']");
  for (const table of tables) {
    const source = table.dataset.terminSource;
    if (!source) continue;

    const channelPrefix = `content.${source}`;
    subscribe(channelPrefix, (ch, data) => {
      const action = ch.split(".")[2]; // created, updated, deleted

      if (action === "updated" && data && data.id != null) {
        // Update existing row
        const row = table.querySelector(`tr[data-termin-row-id="${data.id}"]`);
        if (row) {
          updateRow(row, data);
          flashRow(row);
        }
      } else if (action === "created" && data && data.id != null) {
        // Append new row — but skip if already exists
        const existing = table.querySelector(`tr[data-termin-row-id="${data.id}"]`);
        if (existing) {
          // Row already exists — just update it in case fields changed
          updateRow(existing, data);
          flashRow(existing);
        } else {
          const tbody = table.querySelector("tbody");
          if (tbody) {
            const row = createRow(table, data);
            tbody.appendChild(row);
            flashRow(row);
          }
        }
      } else if (action === "deleted") {
        const id = data.record_id || data.id;
        if (id != null) {
          const row = table.querySelector(`tr[data-termin-row-id="${id}"]`);
          if (row) row.remove();
        }
      }
    });
  }
}

function updateRow(row, data) {
  const cells = row.querySelectorAll("td[data-termin-field]");
  for (const cell of cells) {
    const field = cell.dataset.terminField;
    if (field in data) {
      cell.textContent = data[field] ?? "";
    }
  }
}

function createRow(table, data) {
  // Determine column fields from existing rows or thead
  const fields = [];
  const tbody = table.querySelector("tbody");
  const existingRow = tbody && tbody.querySelector("tr[data-termin-row-id] td[data-termin-field]");
  if (existingRow) {
    // Copy field order from existing row that has cells
    existingRow.closest("tr").querySelectorAll("td[data-termin-field]").forEach(td => {
      fields.push(td.dataset.terminField);
    });
  }
  if (fields.length === 0) {
    // Fall back to thead column headers
    table.querySelectorAll("thead th").forEach(th => {
      const text = th.textContent.trim().toLowerCase().replace(/\s+/g, "_");
      if (text && text !== "actions") {
        fields.push(text);
      }
    });
  }

  const tr = document.createElement("tr");
  tr.className = "border-t";
  tr.setAttribute("data-termin-row-id", data.id);
  for (const field of fields) {
    const td = document.createElement("td");
    td.className = "px-4 py-2 text-sm";
    td.setAttribute("data-termin-field", field);
    td.textContent = data[field] ?? "";
    tr.appendChild(td);
  }
  return tr;
}

function flashRow(row) {
  row.classList.add("termin-updated");
  setTimeout(() => row.classList.remove("termin-updated"), 600);
}

function hydrateAggregations() {
  const aggs = document.querySelectorAll("[data-termin-component='aggregation'], [data-termin-component='stat_breakdown']");
  for (const agg of aggs) {
    const source = agg.dataset.terminSource;
    if (!source) continue;

    subscribe(`content.${source}`, () => {
      // Re-fetch aggregation value on any change
      // For now, show "updating..." briefly — full compute requires server round-trip
      const valueEl = agg.querySelector("[data-termin-agg]");
      if (valueEl) {
        valueEl.classList.add("termin-updating");
        // The SSR page will need a full reload for server-computed aggregations
        // Future: compute client-side from cache for simple count/sum
        setTimeout(() => valueEl.classList.remove("termin-updating"), 1000);
      }
    });
  }
}

function hydrateForms() {
  // Intercept form submits — use fetch() instead of full page redirect.
  // This keeps the WebSocket connection alive so real-time updates
  // (like LLM responses) are pushed to the client without needing a refresh.
  document.querySelectorAll("form[method='post'], form:not([method])").forEach(form => {
    // Skip the role-switcher form
    if (form.action && form.action.includes("/set-role")) return;

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const formData = new FormData(form);
      const url = form.action || window.location.href;

      try {
        const resp = await fetch(url, {
          method: "POST",
          body: formData,
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        });

        if (resp.ok) {
          // Clear the form inputs
          form.querySelectorAll("input[type='text'], textarea").forEach(input => {
            input.value = "";
          });
          form.querySelectorAll("select").forEach(select => {
            select.selectedIndex = 0;
          });
          // Don't add the row here — the WebSocket subscription will push
          // the new record to the table. Adding from both sources causes
          // duplicate rows. Let WebSocket be the single source of truth.
        } else {
          // Show error
          try {
            const err = await resp.json();
            alert(err.detail || "Error saving record");
          } catch {
            alert("Error saving record (HTTP " + resp.status + ")");
          }
        }
      } catch (fetchErr) {
        // Network error — fall back to normal form submit
        console.warn("[Termin] AJAX submit failed, falling back to form POST:", fetchErr);
        form.submit();
      }
    });
  });
}

// ── Connection Indicator ──

function createIndicator() {
  const el = document.createElement("div");
  el.id = "termin-status";
  el.style.cssText = "position:fixed;bottom:8px;right:8px;padding:4px 10px;border-radius:4px;" +
    "font-size:11px;font-family:system-ui;z-index:9999;transition:opacity 0.3s;pointer-events:none;";
  document.body.appendChild(el);
  state.indicator = el;
  updateIndicator(false);
}

function updateIndicator(connected) {
  if (!state.indicator) return;
  if (connected) {
    state.indicator.textContent = "Connected";
    state.indicator.style.background = "#10b981";
    state.indicator.style.color = "#fff";
    state.indicator.style.opacity = "0.6";
    // Fade out after 3 seconds
    setTimeout(() => {
      if (state.connected && state.indicator) state.indicator.style.opacity = "0.2";
    }, 3000);
  } else {
    state.indicator.textContent = "Reconnecting\u2026";
    state.indicator.style.background = "#ef4444";
    state.indicator.style.color = "#fff";
    state.indicator.style.opacity = "1";
  }
}

// ── CSS for flash animation ──

const style = document.createElement("style");
style.textContent = `
  .termin-updated {
    animation: termin-flash 0.6s ease-out;
  }
  @keyframes termin-flash {
    0% { background-color: #fef3c7; }
    100% { background-color: transparent; }
  }
  .termin-updating {
    opacity: 0.5;
    transition: opacity 0.2s;
  }
`;
document.head.appendChild(style);

// ── Public API ──

window.__termin = { state, subscribe, getCachedRecords, TERMIN_VERSION };

// ── Initialize on DOM ready ──

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
