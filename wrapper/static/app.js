"use strict";
let LOGIN_ID = null, POLL = null, HEALTH_POLL = null, CUR_THREAD = null, SELF_UID = null;

const $ = (id) => document.getElementById(id);
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status >= 500) throw new Error("bridge error " + r.status);
  return r.json();
}

function show(view) {
  for (const v of ["connect", "chats", "health"]) {
    $("view-" + v).classList.toggle("hidden", v !== view);
    $("tab-" + v).classList.toggle("active", v === view);
  }
  if (view === "health") startHealth(); else stopHealth();
}

/* ---- connect flow ---- */
const LABELS = {
  opening_browser: "Opening a login window…",
  waiting_login: "Log in to TikTok in the window that opened.",
  connecting: "Signing in and reading your conversations…",
  connected: "Connected.",
  needs_user: "Login needs you (challenge, 2FA, or it timed out).",
  error: "Something went wrong.",
};
const DOT = { connected: "ok", needs_user: "warn", error: "bad" };

async function connect(flow) {
  $("connect-actions").classList.add("hidden");
  $("connect-status").classList.remove("hidden");
  setStatus("opening_browser");
  const res = await api("/api/connect", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ flow: flow || "password" }) });
  LOGIN_ID = res.login_id;
  CONNECT_FLOW = flow || "password";
  POLL = setInterval(pollStatus, 1200);
}
let CONNECT_FLOW = "password";

function setStatus(state, err) {
  const dot = DOT[state] || "";
  $("connect-status").innerHTML =
    (state === "connected" || state === "needs_user" || state === "error"
      ? `<span class="dot ${dot}"></span>`
      : `<span class="spinner"></span>`) +
    `<span>${LABELS[state] || state}</span>`;
  $("connect-hint").textContent =
    state === "waiting_login"
      ? (CONNECT_FLOW === "qr"
          ? "A window opened with a QR code. Open TikTok on your phone → Profile → menu → Scan, and approve."
          : "A real Chrome window opened on this machine. Your password goes only into TikTok's page.")
      : err || "";
}

async function pollStatus() {
  if (!LOGIN_ID) return;
  let st;
  try { st = await api("/api/status?login_id=" + LOGIN_ID); } catch (e) { return; }
  setStatus(st.state, st.last_error);
  if (st.state === "connected") {
    clearInterval(POLL); POLL = null;
    SELF_UID = st.account && st.account.uid;
    $("tab-chats").disabled = false;
    renderAccount(st.account);
    show("chats");
    refreshChats();
    POLL = setInterval(refreshChats, 5000);
  } else if (st.state === "needs_user" || st.state === "error") {
    clearInterval(POLL); POLL = null;
    $("connect-actions").classList.remove("hidden");
  }
}

/* ---- chats ---- */
function renderAccount(a) {
  if (!a) { $("acct").innerHTML = ""; return; }
  const av = a.avatar ? `<img class="avatar" src="${a.avatar}">` : `<div class="avatar"></div>`;
  $("acct").innerHTML = `${av}<div><div class="h">${a.nickname || a.handle || "You"}</div>
    <div class="u">@${a.handle || ""}</div></div>`;
}

async function refreshChats() {
  if (!LOGIN_ID) return;
  const data = await api("/api/chats?login_id=" + LOGIN_ID);
  const byId = {};
  for (const c of data.contacts) byId[c.user_id] = c;
  const rows = data.threads.map((t) => {
    const other = otherParty(t);
    const c = byId[other] || {};
    const name = c.nickname || c.handle || shortConv(t.thread_id);
    const av = c.avatar_url ? `<img class="avatar" src="${c.avatar_url}">` : `<div class="avatar"></div>`;
    return `<div class="row ${t.thread_id === CUR_THREAD ? "active" : ""}"
              onclick="openThread('${t.thread_id}','${(name || "").replace(/'/g, "")}')">
              ${av}<div><div class="name">${name}</div>
              <div class="sub">${t.thread_type === "group" ? "group" : "direct message"}</div></div></div>`;
  });
  // contacts without a thread yet, so the list is never empty after connect
  const threadOthers = new Set(data.threads.map(otherParty));
  const extra = data.contacts.filter((c) => !threadOthers.has(c.user_id)).slice(0, 50).map((c) => {
    const av = c.avatar_url ? `<img class="avatar" src="${c.avatar_url}">` : `<div class="avatar"></div>`;
    return `<div class="row" style="opacity:.7"><div>${av}</div><div><div class="name">${c.nickname || c.handle}</div>
            <div class="sub">contact</div></div></div>`;
  });
  $("threads").innerHTML = rows.join("") + extra.join("") ||
    `<div style="padding:16px;color:var(--muted)">Syncing your conversations…</div>`;
  if (CUR_THREAD) loadMessages(CUR_THREAD);
}

function otherParty(t) {
  const parts = (t.thread_id || "").split(":");
  const ids = parts.slice(2);
  return ids.find((x) => x !== SELF_UID) || ids[ids.length - 1] || "";
}
function shortConv(id) { const p = (id || "").split(":"); return "chat " + (p[p.length - 1] || id).slice(-6); }

async function openThread(tid, name) {
  CUR_THREAD = tid;
  $("peer").textContent = name || "Conversation";
  LAST_RENDER = "";                       // force a fresh render + scroll to bottom
  $("older").classList.remove("hidden", "loading");   // offer "load older"
  document.querySelectorAll(".threads .row").forEach((r) => r.classList.remove("active"));
  await loadMessages(tid, { force: true, toBottom: true });
}

async function loadOlder() {
  if (!CUR_THREAD) return;
  const box = $("msgs");
  const older = $("older");
  older.classList.add("loading");
  const before = box.scrollHeight;
  let res;
  try {
    res = await api("/api/load_older", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login_id: LOGIN_ID, thread_id: CUR_THREAD }) });
  } catch (e) { older.classList.remove("loading"); return; }
  if (res.added > 0) {
    await loadMessages(CUR_THREAD, { force: true, keepScroll: true });
    box.scrollTop = box.scrollHeight - before;   // keep the same message in view
  }
  older.classList.toggle("hidden", res.has_more === false);
  older.classList.remove("loading");
}

let LAST_RENDER = "";   // thread + last message id: skip re-render when nothing changed

async function loadMessages(tid, opts) {
  opts = opts || {};
  const msgs = await api(`/api/messages?login_id=${LOGIN_ID}&thread_id=${encodeURIComponent(tid)}`);
  const box = $("msgs");
  if (!msgs.length) {
    box.innerHTML = `<div class="empty">No messages synced yet in this conversation.</div>`;
    LAST_RENDER = tid + ":empty";
    return;
  }
  const sig = tid + ":" + msgs.length + ":" + msgs[msgs.length - 1].message_id;
  if (sig === LAST_RENDER && !opts.force) return;   // unchanged: keep scroll position
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const prevTop = box.scrollTop;
  LAST_RENDER = sig;
  const MEDIA = ["gif", "image", "sticker"];
  box.innerHTML = msgs.map((m) => {
    const me = m.sender_id === SELF_UID;
    const who = !me ? `<div class="who">${m.sender_id.slice(-6)}</div>` : "";
    let inner;
    if (MEDIA.includes(m.kind) && /^https?:\/\//.test(m.content || "")) {
      inner = `<img class="dm-media" src="${m.content}" alt="${m.kind}" loading="lazy"
                onerror="this.replaceWith(document.createTextNode('[${m.kind}]'))">`;
    } else {
      inner = escapeHtml(m.content || (m.kind !== "text" ? "[" + m.kind + "]" : ""));
    }
    return `<div class="bubble ${me ? "me" : ""}">${who}${inner}</div>`;
  }).join("");
  if (opts.keepScroll) box.scrollTop = prevTop;          // caller manages the scroll
  else if (opts.toBottom || atBottom) box.scrollTop = box.scrollHeight;  // newest at bottom
  else box.scrollTop = prevTop;                          // user scrolled up: leave them
}

function escapeHtml(s) { return (s || "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function logout() {
  if (POLL) { clearInterval(POLL); POLL = null; }
  if (LOGIN_ID) await api("/api/logout", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ login_id: LOGIN_ID }) });
  LOGIN_ID = null; CUR_THREAD = null; SELF_UID = null;
  $("tab-chats").disabled = true;
  $("connect-actions").classList.remove("hidden");
  $("connect-status").classList.add("hidden");
  $("acct").innerHTML = "";
  show("connect");
}

/* ---- health ---- */
function startHealth() { refreshHealth(); if (!HEALTH_POLL) HEALTH_POLL = setInterval(refreshHealth, 3000); }
function stopHealth() { if (HEALTH_POLL) { clearInterval(HEALTH_POLL); HEALTH_POLL = null; } }
async function refreshHealth() {
  let h; try { h = await api("/api/health"); } catch (e) { return; }
  const pct = Math.round(h.live_session_ratio * 100);
  $("hero").textContent = pct + "%";
  $("herobar").style.width = pct + "%";
  $("m-pw").textContent = Math.round(h.password_login_share * 100) + "%";
  $("m-lag").textContent = h.delivery_lag_p95_seconds + " s";
  $("m-reauth").textContent = Math.round(h.reauth_share * 100) + "%";
  $("m-total").textContent = h.logins_total;
  $("m-states").textContent = h.states.length ? h.states.join(", ") : "—";
  const errs = Object.entries(h.error_totals || {});
  $("m-errors").textContent = errs.length ? errs.map(([k, v]) => `${k}:${v}`).join("  ") : "none";
}
