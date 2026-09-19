"use strict";
let LOGIN_ID = null, POLL = null, HEALTH_POLL = null, CUR_THREAD = null, SELF_UID = null;
let CONTACTS = {};          // user_id -> contact (names for bubbles and rows)
let CUR_GROUP = false;      // the open thread is a group (sender names matter)

const $ = (id) => document.getElementById(id);
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status >= 500) throw new Error("bridge error " + r.status);
  return r.json();
}

// transient bottom notification
let TOAST_T = null;
function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (kind ? " " + kind : "");
  clearTimeout(TOAST_T);
  TOAST_T = setTimeout(() => { t.className = "toast"; }, 1900);
}

// run an async action with a spinner on the button that triggered it
async function busy(btn, fn) {
  if (btn) btn.classList.add("busy");
  try { return await fn(); }
  finally { if (btn) btn.classList.remove("busy"); }
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
  opening_browser: "Opening TikTok…",
  waiting_login: "Log in on TikTok's page…",
  connecting: "Reading your chats…",
  connected: "Connected",
  needs_user: "Login needs you",
  error: "Something went wrong",
};
const DOT = { connected: "ok", needs_user: "warn", error: "bad" };

async function connect() {
  $("connect-actions").classList.add("hidden");
  $("connect-status").classList.remove("hidden");
  setStatus("opening_browser");
  if (POLL) { clearInterval(POLL); POLL = null; }
  let res;
  try {
    res = await api("/api/connect", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flow: "password" }) });
  } catch (e) {
    setStatus("error", "Bridge not reachable: " + e.message);
    $("connect-actions").classList.remove("hidden");
    return;
  }
  LOGIN_ID = res.login_id;
  POLL = setInterval(pollStatus, 1200);
}

function setStatus(state, err) {
  const dot = DOT[state] || "";
  $("connect-status").innerHTML =
    (state === "connected" || state === "needs_user" || state === "error"
      ? `<span class="dot ${dot}"></span>`
      : `<span class="spinner"></span>`) +
    `<span>${LABELS[state] || state}</span>`;
  $("connect-hint").textContent =
    state === "waiting_login"
      ? "Finish the login in the TikTok window (QR code or phone / email). It closes by itself."
      : (err || "").replace(/^\w+Error: /, "");
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
    refreshChats().catch(() => {});
    POLL = setInterval(() => refreshChats().catch(() => {}), 5000);
  } else if (st.state === "needs_user" || st.state === "error") {
    // keep polling: after a throttle message or a timeout the TikTok window is
    // still open and still watched, so finishing the login there connects us
    if (st.state === "error") { clearInterval(POLL); POLL = null; }
    $("connect-actions").classList.remove("hidden");
  }
}

/* ---- chats ---- */
function renderAccount(a) {
  if (!a) { $("acct").innerHTML = ""; return; }
  const av = a.avatar ? `<img class="avatar" src="${escapeHtml(a.avatar)}">` : `<div class="avatar"></div>`;
  $("acct").innerHTML = `${av}<div><div class="h">${escapeHtml(a.nickname || a.handle || "You")}</div>
    <div class="u">@${escapeHtml(a.handle || "")}</div></div>`;
}

// manual Refresh button: same sync, but with visible feedback
async function refresh(btn) {
  try {
    await busy(btn, async () => { await refreshChats(); });
    toast("Up to date", "ok");
  } catch (e) { toast("Refresh failed: " + e.message, "bad"); }
}

async function refreshChats() {
  if (!LOGIN_ID) return;
  const data = await api("/api/chats?login_id=" + LOGIN_ID);
  const byId = {};
  for (const c of data.contacts) byId[c.user_id] = c;
  CONTACTS = byId;
  const rows = data.threads.map((t) => {
    const other = otherParty(t);
    const c = byId[other] || {};
    const group = t.thread_type === "group";
    const name = c.nickname || c.handle || shortConv(t.thread_id);
    const av = c.avatar_url ? `<img class="avatar" src="${escapeHtml(c.avatar_url)}">` : `<div class="avatar"></div>`;
    return `<div class="row ${t.thread_id === CUR_THREAD ? "active" : ""}"
              data-tid="${escapeHtml(t.thread_id)}" data-name="${escapeHtml(name || "")}" data-group="${group ? 1 : ""}">
              ${av}<div><div class="name">${escapeHtml(name)}</div>
              ${group ? `<div class="sub">Group</div>` : ""}</div></div>`;
  });
  // contacts without a thread yet, so the list is never empty after connect
  const threadOthers = new Set(data.threads.map(otherParty));
  const extra = data.contacts.filter((c) => !threadOthers.has(c.user_id)).slice(0, 50).map((c) => {
    const av = c.avatar_url ? `<img class="avatar" src="${escapeHtml(c.avatar_url)}">` : `<div class="avatar"></div>`;
    return `<div class="row" style="opacity:.7">${av}<div><div class="name">${escapeHtml(c.nickname || c.handle)}</div>
            <div class="sub">Contact</div></div></div>`;
  });
  let header = "";
  if (rows.length === 0) {
    header = extra.length
      ? `<div class="note">No chats yet. Start one in TikTok and it shows up here.</div>`
      : `<div class="note">Syncing…</div>`;
  }
  $("threads").innerHTML = header + rows.join("") + extra.join("");
  if (CUR_THREAD) loadMessages(CUR_THREAD);
}

// one delegated click handler for the thread list (rows carry data-tid / data-name)
document.addEventListener("DOMContentLoaded", () => {
  $("threads").addEventListener("click", (ev) => {
    const row = ev.target.closest(".row[data-tid]");
    if (row) openThread(row.dataset.tid, row.dataset.name, !!row.dataset.group);
  });
});

function otherParty(t) {
  const parts = (t.thread_id || "").split(":");
  const ids = parts.slice(2);
  return ids.find((x) => x !== SELF_UID) || ids[ids.length - 1] || "";
}
function shortConv(id) { const p = (id || "").split(":"); return "Chat " + (p[p.length - 1] || id).slice(-4); }
function nameOf(uid) { const c = CONTACTS[uid] || {}; return c.nickname || c.handle || ""; }

async function openThread(tid, name, group) {
  CUR_THREAD = tid;
  CUR_GROUP = !!group;
  $("chat").classList.add("open");             // phone: show the messages pane
  $("peer").textContent = name || "Chat";
  LAST_RENDER = "";                       // force a fresh render + scroll to bottom
  $("older").classList.remove("hidden", "loading");   // offer "load older"
  document.querySelectorAll(".threads .row").forEach((r) => r.classList.remove("active"));
  await loadMessages(tid, { force: true, toBottom: true });
}

// phone: back to the chat list (the thread stays selected on wide screens)
function closeThread() {
  $("chat").classList.remove("open");
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
    toast(`Loaded ${res.added} older message${res.added === 1 ? "" : "s"}`, "ok");
  } else if (res.error) {
    toast(res.error, "bad");
  } else {
    toast("No older messages", "ok");
  }
  older.classList.toggle("hidden", res.has_more === false);
  older.classList.remove("loading");
}

let LAST_RENDER = "";   // thread + last message id: skip re-render when nothing changed

// what an empty-bodied bubble of each kind says (never a blank bubble)
const KIND_LABEL = { text: "Empty message", sticker: "Sticker", gif: "GIF", image: "Photo",
                     video: "Video", share: "Shared a video", system: "System notice",
                     unknown: "Unsupported message" };

async function loadMessages(tid, opts) {
  opts = opts || {};
  const msgs = await api(`/api/messages?login_id=${LOGIN_ID}&thread_id=${encodeURIComponent(tid)}`);
  const box = $("msgs");
  // one-sided server notices ("say hi" hints, request banners) are not chat
  const shown = msgs.filter((m) => m.kind !== "system");
  const hidden = msgs.length - shown.length;
  const hiddenNote = hidden
    ? `<div class="sysnote">${hidden} system notice${hidden === 1 ? "" : "s"} hidden</div>` : "";
  if (!shown.length) {
    box.innerHTML = `<div class="empty">No messages yet.${hiddenNote}</div>`;
    LAST_RENDER = tid + ":empty:" + msgs.length;
    return;
  }
  const sig = tid + ":" + msgs.length + ":" + msgs[msgs.length - 1].message_id;
  if (sig === LAST_RENDER && !opts.force) return;   // unchanged: keep scroll position
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const prevTop = box.scrollTop;
  LAST_RENDER = sig;
  const MEDIA = ["gif", "image", "sticker"];
  box.innerHTML = hiddenNote + shown.map((m) => {
    const me = m.sender_id === SELF_UID;
    // sender name only in groups; in a direct chat the header already says who
    const who = (!me && CUR_GROUP && nameOf(m.sender_id))
      ? `<div class="who">${escapeHtml(nameOf(m.sender_id))}</div>` : "";
    let inner;
    if (MEDIA.includes(m.kind) && /^https?:\/\//.test(m.content || "")) {
      inner = `<img class="dm-media" src="${m.content}" alt="${m.kind}" loading="lazy"
                onerror="this.replaceWith(document.createTextNode('[${m.kind}]'))">`;
    } else if (m.kind === "share") {
      // a shared TikTok video/post: label it, plus its caption when we have one
      inner = `<span class="label">▶ Shared a video</span>` +
              (m.content ? `<div>${escapeHtml(m.content)}</div>` : "");
    } else if (m.content) {
      inner = escapeHtml(m.content);
    } else {
      inner = `<span class="label">${KIND_LABEL[m.kind] || "Unsupported message"}</span>`;
    }
    return `<div class="bubble ${me ? "me" : ""}">${who}${inner}</div>`;
  }).join("");
  if (opts.keepScroll) box.scrollTop = prevTop;          // caller manages the scroll
  else if (opts.toBottom || atBottom) box.scrollTop = box.scrollHeight;  // newest at bottom
  else box.scrollTop = prevTop;                          // user scrolled up: leave them
}

function escapeHtml(s) { return (s || "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function logout(btn) {
  if (POLL) { clearInterval(POLL); POLL = null; }
  await busy(btn, async () => {
    if (LOGIN_ID) await api("/api/logout", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ login_id: LOGIN_ID }) });
  });
  LOGIN_ID = null; CUR_THREAD = null; SELF_UID = null; CONTACTS = {}; CUR_GROUP = false;
  $("chat").classList.remove("open");
  $("peer").textContent = "Pick a chat";
  $("msgs").innerHTML = `<div class="empty">Pick a chat to read it.</div>`;
  $("threads").innerHTML = "";
  $("tab-chats").disabled = true;
  $("connect-actions").classList.remove("hidden");
  $("connect-status").classList.add("hidden");
  $("acct").innerHTML = "";
  show("connect");
  toast("Logged out", "ok");
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
