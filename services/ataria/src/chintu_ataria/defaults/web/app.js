// Ataria tailnet app. One item, one id, shown on Today and its section; one tick marks it done
// everywhere. Data: bin/serve.py (today.md + done/). One section is shown at a time: the left rail
// switches sections on a desktop, the bottom tab bar on a phone.
"use strict";

const API = "api";
const POLL_MS = 60000;
const PRI = ["P0", "P1", "P2", "P3", "OK"];
const PRI_NAME = { P0: "Urgent", P1: "High", P2: "Normal", P3: "Low", OK: "Healthy" };
const CAPS = { P0: 3, P1: 5, P2: 7, P3: 7, OK: 7 };            // ataria-core section 3
const SOURCE = { work: "Work", meetings: "Calendar", finance: "Finance", ledger: "Spending", personal: "Personal", notes: "Notes" };
const notePillar = (e, p) => e.lane === "notes" && e.pillar === p;      // notes route to any pillar
const NOTE_PILLARS = ["work", "finance", "kids", "growth"];             // anything else is Home
const isKids = (e) => e.area === "kids" || hasTag(e, "kids") || notePillar(e, "kids");
const isGrowth = (e) => e.area === "growth" || hasTag(e, "growth") || notePillar(e, "growth");
const PILLARS = [
  { id: "today",    name: "Today",   filter: () => true, today: true },
  { id: "digest",   name: "Digest",  filter: () => true, digest: true },
  { id: "work",     name: "Work",    filter: (e) => e.lane === "work" || e.lane === "meetings" || notePillar(e, "work") },
  { id: "finance",  name: "Finance", filter: (e) => e.lane === "finance" || e.lane === "ledger" || notePillar(e, "finance") },
  { id: "kids",     name: "Kids",    filter: (e) => (e.lane === "personal" && isKids(e)) || notePillar(e, "kids") },
  { id: "growth",   name: "Growth",  filter: (e) => (e.lane === "personal" && isGrowth(e)) || notePillar(e, "growth") },
  { id: "personal", name: "Home & travel", filter: (e) => (e.lane === "personal" && !isKids(e) && !isGrowth(e)) || (e.lane === "notes" && !NOTE_PILLARS.includes(e.pillar)) },
  { id: "month",    name: "Month",   month: true },
];
const TABBAR = ["today", "digest", "work", "finance"];             // the rest sit under More on a phone
const ICONS = {
  today: "M10 3v2M10 15v2M3 10h2M15 10h2M5 5l1.4 1.4M13.6 13.6L15 15M5 15l1.4-1.4M13.6 6.4L15 5M10 7a3 3 0 100 6 3 3 0 000-6z",
  work: "M3 7h14v9H3zM7 7V5h6v2M3 11h14",
  finance: "M6 4h8M6 8h8M6 4c5 0 5 8 0 8l7 5",
  digest: "M4 4h10v12H5a1 1 0 01-1-1zM14 7h2v8a1 1 0 01-2 0M7 7h4M7 10h4M7 13h2",
  kids: "M10 3l2 4.5 5 .5-3.8 3.3 1.1 4.9L10 13.7 5.7 16.2l1.1-4.9L3 8l5-.5z",
  more: "M5 10h.01M10 10h.01M15 10h.01",
};

// pending: uid -> wanted done state. Ticks are staged here and sent together by the Save button.
const state = { data: null, days: [], date: null, months: null, q: "", pri: new Set(), open: new Set(), busy: false,
  pending: new Map(), todayTab: "todo", view: viewFromHash() };
const $ = (s) => document.querySelector(s);

// The owner's settings from the profile (GET api/config); safe defaults until it loads.
let CONFIG = { owner: "", times: [], kids: true, work: true, region: { name: "", tag: "" }, course: null };
const isOwner = (who) => /^me\b/i.test(who) || (!!CONFIG.owner && who.toLowerCase().startsWith(CONFIG.owner.toLowerCase()));
const runTimes = () => CONFIG.times.length ? CONFIG.times.join(" and ") : "each scheduled run";
async function loadConfig() {
  try { CONFIG = { ...CONFIG, ...(await api("config")) }; } catch {}
  // Sections the owner has no use for stay hidden: Kids without children, Work when work is off.
  for (const [id, on] of [["kids", CONFIG.kids], ["work", CONFIG.work]]) {
    const i = PILLARS.findIndex((p) => p.id === id);
    if (!on && i >= 0) PILLARS.splice(i, 1);
  }
  const t = TABBAR.indexOf("work");
  if (!CONFIG.work && t >= 0) TABBAR.splice(t, 1);
  if (!PILLARS.some((p) => p.id === state.view)) state.view = "today";
}

function viewFromHash() { const h = location.hash.slice(1); return PILLARS.some((p) => p.id === h) ? h : "today"; }
function hasTag(e, t) { return (e.tags || []).some((x) => String(x).toLowerCase() === t); }
function kidsOf(e) { return e.kind === "item" ? items().filter((t) => t.kind === "todo" && t.lane === e.lane && t.item === e.id) : []; }
function isDone(e) {
  if (state.pending.has(e.uid)) return state.pending.get(e.uid);
  if (e.group === "done") return true;
  // Preview of the server rule: when unsaved todo ticks leave every todo done, the item closes on Save.
  const ks = kidsOf(e);
  return ks.some((k) => state.pending.has(k.uid)) && ks.every((k) => state.pending.has(k.uid) ? state.pending.get(k.uid) : k.group === "done");
}
// Calendar events: meetings-lane items with a start time (meetings and interviews). They render as a
// calendar, not as todos: no tick, no priority, not counted. Conflict notes have no start and stay todos.
const isEvent = (e) => e.lane === "meetings" && e.kind === "item" && !!e.start;
// Lead with the short title the lanes write; a raw WhatsApp note leads with its own text.
const headline = (e) => (e.tt && e.tt !== "WhatsApp note" ? e.tt : e.title);

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v;            // CSSOM: allowed by the page's CSP, unlike style=""
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function svg(path, cls = "ico") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 20 20"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path); s.append(p); return s;
}
const CARET = () => svg("M7 5l6 5-6 5", "caret");

async function api(path, opts = {}) {
  const r = await fetch(`${API}/${path}`, { cache: "no-store", ...opts, headers: { "X-Ataria": "1", "content-type": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) {
    // Surface the server's reason (e.g. "bad uid") instead of a bare status code.
    let msg = `${r.status}`;
    try { const e = await r.json(); if (e && e.error) msg += ` ${e.error}` + (e.uids?.length ? `: ${e.uids.join(", ")}` : ""); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------- data ---------- */
async function load() {
  try {
    // Today first; the list of earlier days fills in when it arrives and never holds up the page.
    api("days").then((d) => { state.days = d; renderChrome(); }).catch(() => {});
    const today = await api("today" + (state.date ? `?date=${state.date}` : ""));
    state.data = today.frontmatter;
    $("#dbnote").hidden = true;
  } catch (err) {
    $("#dbnote").hidden = false;
    $("#dbnote").textContent = "Could not load Ataria data (" + err.message + "). Check that ataria-app is running.";
  }
  render();
}

async function loadMonths() {
  if (state.months) return;
  try { state.months = await api("months"); } catch { state.months = []; }
}

function items() { return state.data?.items || []; }

function matches(e) {
  if (state.pri.size && !state.pri.has(e.p)) return false;
  if (!state.q) return true;
  const hay = [e.title, e.tt, e.next, e.why, e.area, e.lane, e.owner, ...(e.tags || [])].join(" ").toLowerCase();
  return state.q.split(/\s+/).every((w) => hay.includes(w));
}

/* ---------- small pieces ---------- */
const day0 = () => { const t = new Date(); t.setHours(0, 0, 0, 0); return t; };
const hm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function dueBit(due) {
  if (!due) return null;
  const d = new Date(due + "T00:00:00");
  const days = Math.round((d - day0()) / 86400000);
  if (days < 0) return el("span", { class: "m late" }, `Overdue ${-days} day${days < -1 ? "s" : ""}`);
  if (days === 0) return el("span", { class: "m hot" }, "Due today");
  if (days === 1) return el("span", { class: "m" }, "Due tomorrow");
  return el("span", { class: "m" }, "Due " + d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }));
}

function linksOf(e) {
  return e.links?.length ? el("div", { class: "links" }, e.links.map((l) => /^https?:|^obsidian:/.test(l.u)
    ? el("a", { href: l.u, target: "_blank", rel: "noopener noreferrer" }, l.t) : el("span", {}, l.t))) : null;
}

/* ---------- rows ---------- */
function subTodos(e) {
  if (e.kind !== "item") return null;
  const kids = items().filter((t) => t.nested && t.lane === e.lane && t.item === e.id);
  if (!kids.length) return null;
  return el("div", { class: "steps" }, el("p", { class: "steps-h" }, "Steps"), kids.map((t) => {
    const td = isDone(t);
    const box = el("input", { type: "checkbox", "aria-label": `Done: ${t.title}`, checked: td, disabled: t.linked_done });
    box.addEventListener("change", () => tick(t, box.checked));
    return el("label", { class: "step" + (td ? " done" : "") + (state.pending.has(t.uid) ? " pending" : "") }, box,
      el("span", {}, t.title), t.due ? dueBit(t.due) : null);
  }));
}

function metaBits(e, showSource) {
  const bits = [];
  if (showSource) bits.push(el("span", { class: "m src" }, hasTag(e, "whatsapp") ? "WhatsApp" : SOURCE[e.lane] || e.lane));
  bits.push(dueBit(e.due));
  if (!e.due && e.backlog) bits.push(el("span", { class: "m" }, `Open ${e.age_days + 1} days`));
  if (e.outcome === "answered") bits.push(el("span", { class: "m" }, e.answered_via === "email" ? "Answered by email" : e.answered_via ? `Answered on ${e.answered_via}` : "Answered"));
  if (hasTag(e, "flagged") && e.state !== "closed") bits.push(el("span", { class: "m hot" }, "Flagged"));
  if (e.reopened) bits.push(el("span", { class: "m hot" }, "Reopened"));
  if (e.stale) bits.push(el("span", { class: "m" }, "May be out of date"));
  const ks = kidsOf(e);
  if (ks.length > 1) bits.push(el("span", { class: "m" }, `${ks.filter(isDone).length} of ${ks.length} steps`));
  if (e.owner && !isOwner(e.owner)) bits.push(el("span", { class: "m who" }, e.owner));
  return bits.filter(Boolean);
}

function row(e, showSource) {
  const done = isDone(e), key = e.uid, open = state.open.has(key);
  const pr = e.capped ? "P0" : e.p;
  const box = el("input", { type: "checkbox", "aria-label": `Done: ${headline(e)}`, checked: done, disabled: e.state === "closed" || e.linked_done });
  box.addEventListener("change", () => tick(e, box.checked));
  const meta = metaBits(e, showSource);
  const head = el("button", { class: "rhead", type: "button", "aria-expanded": String(open), onclick: () => toggleOpen(key) },
    el("span", { class: "rt" }, headline(e)),
    meta.length ? el("span", { class: "rm" }, meta) : null);
  const det = el("div", { class: "rdet" },
    headline(e) !== e.title ? el("p", { class: "full" }, e.title) : null,
    e.why && e.why !== e.title ? el("p", { class: "why" }, e.why) : null,
    e.next ? el("p", { class: "next" }, el("b", {}, "Next step: "), e.next) : null,
    linksOf(e), subTodos(e),
    el("div", { class: "ract" }, el("button", { class: "btn ghost", type: "button", onclick: () => openFile(e) }, "Open note"),
      el("span", { class: "prov" }, [e.first_seen && `First seen ${new Date(e.first_seen + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`, e.verified && "reviewed"].filter(Boolean).join(", "))));
  const cls = ["row", `pr-${pr.toLowerCase()}`, done && "done", state.pending.has(key) && "pending", e.stale && "stale", open && "open"].filter(Boolean).join(" ");
  return el("div", { class: cls, "data-uid": key },
    el("label", { class: "tick" + (e.linked_done ? " locked" : "") }, box),
    el("div", { class: "rmain" }, head, det),
    pr === "P0" || pr === "P1" ? el("span", { class: "rpri", title: PRI_NAME[pr] }, pr) : el("span", { class: "rpri" }),
    el("button", { class: "rcaret", type: "button", tabindex: "-1", "aria-hidden": "true", onclick: () => toggleOpen(key) }, CARET()));
}

const rows = (list, showSource) => el("div", { class: "rows" }, list.map((e) => row(e, showSource)));

function group(title, count, body, id) {
  return el("section", { class: "grp", id }, el("h3", {}, title, count !== null && count !== undefined ? el("span", { class: "n" }, String(count)) : null), body);
}

function priorityGroups(list, showSource) {
  const out = [];
  list = list.filter((e) => !e.nested);                       // todos render inside their item
  for (const p of PRI) {
    const g = list.filter((e) => e.group === "open" && (e.capped ? "P0" : e.p) === p);
    if (!g.length) continue;
    const cap = CAPS[p];
    const body = [rows(g.slice(0, cap), showSource)];
    if (g.length > cap) body.push(el("details", { class: "more" }, el("summary", {}, `Show ${g.length - cap} more`), rows(g.slice(cap), showSource)));
    out.push(group(PRI_NAME[p], g.length, body));
  }
  const done = list.filter((e) => e.group === "done");
  if (done.length) out.push(el("details", { class: "more donegrp" }, el("summary", {}, `Done or answered today, ${done.length}`), rows(done, showSource)));
  return out;
}

/* ---------- calendar ---------- */
function calendarDays(list) {
  const t0 = day0();
  const evs = list.filter(isEvent).filter((e) => new Date(e.end || e.start) >= t0).sort((a, b) => new Date(a.start) - new Date(b.start));
  if (!evs.length) return [el("p", { class: "empty" }, `No meetings or interviews today or tomorrow. The calendar refreshes ${runTimes()}.`)];
  const days = new Map();
  for (const e of evs) {
    const d = new Date(e.start); d.setHours(0, 0, 0, 0);
    if (!days.has(d.getTime())) days.set(d.getTime(), []);
    days.get(d.getTime()).push(e);
  }
  return [...days].map(([k, es]) => {
    const d = new Date(k), diff = Math.round((d - t0) / 86400000);
    const name = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : d.toLocaleDateString("en-GB", { weekday: "long" });
    return el("div", { class: "calday" },
      el("h4", {}, name, el("span", {}, d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }))),
      el("ol", { class: "cal" }, es.map((e) => calRow(e, es))));
  });
}

function calRow(e, es) {
  const now = new Date(), s = new Date(e.start), en = new Date(e.end || e.start);
  const clash = es.some((o) => o !== e && new Date(o.start) < en && s < new Date(o.end || o.start));
  const live = s <= now && now < en, past = en <= now, key = e.uid;
  const title = e.title.replace(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+/, "").replace(/^\d{1,2}:\d{2}(\s*(to|-|–)\s*\d{1,2}:\d{2})?:?\s+/, "");
  const head = el("button", { class: "evhead", type: "button", "aria-expanded": String(state.open.has(key)), onclick: () => toggleOpen(key) },
    el("span", { class: "rt" }, title),
    el("span", { class: "rm" },
      live ? el("span", { class: "m now" }, "Happening now") : null,
      e.area === "hiring" ? el("span", { class: "m" }, "Interview") : null,
      clash ? el("span", { class: "m late" }, "Overlaps") : null,
      e.next ? el("span", { class: "m prep" }, e.next) : null));
  const det = el("div", { class: "rdet" }, e.why ? el("p", { class: "why" }, e.why) : null, linksOf(e), subTodos(e),
    el("div", { class: "ract" }, el("button", { class: "btn ghost", type: "button", onclick: () => openFile(e) }, "Open note")));
  const cls = ["ev", past && "past", live && "live", e.area === "hiring" && "hire", state.open.has(key) && "open"].filter(Boolean).join(" ");
  return el("li", { class: cls, "data-uid": key },
    el("div", { class: "evt" }, el("b", {}, hm(s)), e.end ? el("span", {}, hm(en)) : null),
    el("div", { class: "rmain" }, head, det),
    el("button", { class: "rcaret", type: "button", tabindex: "-1", "aria-hidden": "true", onclick: () => toggleOpen(key) }, CARET()));
}

/* ---------- section-specific blocks ---------- */
function scheduleBlocks(all) {
  const out = [group("Calendar", null, calendarDays(all), "work-calendar")];
  const conf = all.filter((e) => e.lane === "meetings" && e.kind === "item" && !e.start && e.group === "open");
  if (conf.length) out.push(group("Calendar clashes to sort out", conf.length, rows(conf, false)));
  return out;
}

const inr = (n) => n === null || n === undefined ? "–" : (n < 0 ? "−₹" : "₹") + Math.abs(Math.round(n)).toLocaleString("en-IN");
const monthName = (ym) => /^\d{4}-\d{2}$/.test(ym || "") ? new Date(ym + "-01T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" }) : (ym || "");
function budgetBlocks(all) {
  const weeks = all.filter((e) => e.lane === "ledger" && e.id.startsWith("ledger-week-") && e.stats).sort((a, b) => b.id.localeCompare(a.id));
  const alerts = all.filter((e) => e.lane === "ledger" && (hasTag(e, "alert") || hasTag(e, "new-account")) && e.group === "open").sort((a, b) => PRI.indexOf(a.p) - PRI.indexOf(b.p));
  const used = new Set([...weeks, ...alerts].map((e) => e.uid));
  const out = [];
  const w = weeks[0]?.stats;
  if (!w) {
    out.push(group("Budget", null, el("p", { class: "empty" }, "The first weekly spending review runs on Friday at 19:00. Budget and alerts appear here after it.")));
  } else {
    const m = w.month_to_date || {}, wk = w.week || {};
    const usedPct = m.used_pct ?? null;
    const stat = (k, v, n, cls = "") => el("div", { class: "stat " + cls }, el("span", { class: "k" }, k), el("span", { class: "v" }, v), el("span", { class: "n" }, n));
    out.push(group(`Budget, ${monthName(m.month)}`, null, [
      el("div", { class: "stats" },
        stat("This week", inr(wk.spend), `${wk.transactions ?? 0} transactions`),
        stat("Month to date", inr(m.mtd), m.budget ? `of ${inr(m.budget)}` : "No budget set"),
        stat("Budget used", usedPct === null ? "–" : `${usedPct}%`, `Day ${m.day || "?"} of ${m.days_in_month || "?"}`, usedPct >= 100 ? "bad" : usedPct >= 90 ? "warn" : ""),
        stat("Uncategorised", String(wk.uncategorised ?? 0), "this week")),
      el("div", { class: "bars" }, (m.categories || []).filter((c) => c.budget || c.mtd).map((c) => {
        const pct = c.used_pct ?? (c.mtd ? 100 : 0);
        return el("div", { class: "bar " + (c.level || "ok").toLowerCase() },
          el("span", { class: "bn" }, c.category),
          el("span", { class: "bt" }, el("i", { style: `width:${Math.max(0, Math.min(pct, 100))}%` })),
          el("span", { class: "bv" }, `${inr(c.mtd)}${c.budget ? " of " + inr(c.budget) : ""}`));
      })),
      el("div", {}, el("button", { class: "btn ghost", type: "button", onclick: () => openFile(weeks[0]) }, "Open the weekly review"))], "finance-budget"));
  }
  if (alerts.length) out.push(group("Spending alerts", alerts.length, rows(alerts, false)));
  return { blocks: out, used };
}

function growthBlocks(all) {
  const open = all.filter((e) => e.group === "open");
  const groups = [
    ["GitHub rising", (e) => hasTag(e, "github") || e.id.startsWith("github-rising")],
    ["Papers", (e) => hasTag(e, "paper")],
    ["Physical AI", (e) => hasTag(e, "physical-ai")],
    ["LinkedIn article draft", (e) => e.id.startsWith("linkedin-") || hasTag(e, "linkedin")],
  ];
  const used = new Set(), out = [];
  for (const [name, test] of groups) {
    const g = open.filter((e) => !used.has(e.uid) && test(e));
    g.forEach((e) => used.add(e.uid));
    if (g.length) out.push(group(name, g.length, rows(g, false)));
  }
  return { blocks: out, used };
}

// Things to know today (items marked `info`): nobody is waiting on the owner. No tick, no count.
function infoBlock(list) {
  const info = list.filter((e) => e.group === "info");
  if (!info.length) return [];
  const irow = (e) => {
    const key = e.uid, open = state.open.has(key);
    return el("div", { class: "row info" + (open ? " open" : ""), "data-uid": key },
      el("span", { class: "tick" }),
      el("div", { class: "rmain" },
        el("button", { class: "rhead", type: "button", "aria-expanded": String(open), onclick: () => toggleOpen(key) },
          el("span", { class: "rt" }, e.area === "alert" && e.links?.[0] ? `${e.links[0].t}: ${headline(e)}` : headline(e))),
        el("div", { class: "rdet" }, headline(e) !== e.title ? el("p", { class: "full" }, e.title) : null,
          e.why && e.why !== e.title ? el("p", { class: "why" }, e.why) : null, linksOf(e),
          el("div", { class: "ract" }, el("button", { class: "btn ghost", type: "button", onclick: () => openFile(e) }, "Open note")))),
      el("span", { class: "rpri" }),
      el("button", { class: "rcaret", type: "button", tabindex: "-1", "aria-hidden": "true", onclick: () => toggleOpen(key) }, CARET()));
  };
  return [el("details", { class: "more know" }, el("summary", {}, `Good to know, ${info.length}`, el("span", { class: "sub" }, "Updates, nothing to do")),
    el("div", { class: "rows" }, info.map(irow)))];
}

function sourcesLine() {
  const lanes = state.data?.lanes || {};
  const ent = Object.entries(lanes);
  if (!ent.length) return null;
  const daily = ["work", "finance", "personal", "google", "meetings", "notes"];
  const st = (lane, r) => {
    if (r.result === "failed" || r.result === "missing" || (daily.includes(lane) && r.result === "not run today")) return "down";
    const bad = (r.sources_health || []).filter((s) => !["ok", "skipped"].includes(s.status));
    return r.result === "ok" && !bad.length ? "ok" : "partial";
  };
  const down = ent.filter(([l, r]) => st(l, r) === "down").length, part = ent.filter(([l, r]) => st(l, r) === "partial").length;
  return el("details", { class: "more sources" },
    el("summary", {}, down ? `Sources: ${down} did not run` : part ? `Sources: all ran, ${part} with gaps` : "Sources: all ran"),
    el("ul", { class: "srcs" }, ent.map(([lane, r]) => {
      const bad = (r.sources_health || []).filter((s) => !["ok", "skipped"].includes(s.status));
      const at = r.at ? hm(new Date(r.at)) : r.result;
      const s2 = st(lane, r);
    return el("li", { class: s2 }, el("b", {}, lane), el("span", {}, s2 === "ok" ? `Ran ${at}` : s2 === "down" ? `Did not run (${r.result})` : `Ran ${at}, with gaps`),
        r.error ? el("span", { class: "why" }, r.error) : null,
        bad.length ? el("span", { class: "why" }, bad.map((s) => `${s.name}: ${s.note || s.status}`).join("; ")) : null);
    })));
}

/* ---------- Your focus: the daily commitments, above everything else ---------- */
// Work: every open work P0 (the top P1 when there is none). Growth: one daily tick for writing or
// study, the next lesson of the owner's course (profile: personal.growth.course) and today's article draft.
// Course progress is kept as focus ticks `focus.<slug>-<lesson>`; the next lesson is the first one not ticked.
function courseNext() {
  const course = CONFIG.course;
  const lessons = Array.isArray(course?.lessons) ? course.lessons.filter((l) => Array.isArray(l) && l.length >= 2) : [];
  if (!lessons.length) return null;
  const name = String(course.name || "Course"), slug = String(course.slug || "course").replace(/[^A-Za-z0-9-]/g, "");
  const fd = state.data?.focus_done || {};
  const uid = (n) => `focus.${slug}-${n}`;
  const doneN = lessons.filter(([n]) => fd[uid(n)]).length;
  const next = lessons.find(([n]) => !fd[uid(n)]);
  if (!next) return el("p", { class: "snote" }, `${name}: all ${lessons.length} lessons done.`);
  const [n, title, time] = next;
  const pseudo = { uid: uid(n), group: "open" };
  const box = el("input", { type: "checkbox", "aria-label": `Lesson ${n} done`, checked: isDone(pseudo) });
  box.addEventListener("change", () => tick(pseudo, box.checked));
  const link = /^https:\/\//.test(String(course.url || "")) ? course.url : null;
  return el("div", { class: "lesson" + (state.pending.has(pseudo.uid) ? " pending" : "") },
    el("label", { class: "tick" }, box),
    el("div", { class: "lbody" },
      el("span", { class: "lk" }, `${name}, next lesson · ${doneN} of ${lessons.length} done`),
      link ? el("a", { class: "lt", href: link, target: "_blank", rel: "noopener noreferrer" }, `${n} ${title}`) : el("span", { class: "lt" }, `${n} ${title}`),
      time ? el("span", { class: "lm" }, time) : null));
}

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function focusTick(slot, label) {
  const day = state.date ? new Date(state.date + "T00:00:00") : new Date();
  const fd = state.data?.focus_done || {};
  const uid = `focus.${slot}-${ymd(day)}`;
  const pseudo = { uid, group: fd[uid] ? "done" : "open" };
  const done = isDone(pseudo);
  // Streak: consecutive ticked days ending today (or yesterday, while today is still open).
  let streak = 0;
  const d = new Date(day);
  if (!done) d.setDate(d.getDate() - 1);
  while (fd[`focus.${slot}-${ymd(d)}`] || (ymd(d) === ymd(day) && done)) { streak++; d.setDate(d.getDate() - 1); }
  const box = el("input", { type: "checkbox", "aria-label": label, checked: done });
  box.addEventListener("change", () => tick(pseudo, box.checked));
  return el("label", { class: "ftick" + (done ? " done" : "") + (state.pending.has(uid) ? " pending" : "") },
    el("span", { class: "tick" }, box), el("span", { class: "ft" }, label),
    streak ? el("span", { class: "streak" }, `${streak} day${streak > 1 ? "s" : ""} in a row`) : null);
}

function focusPanel() {
  const work = items().filter((e) => PILLARS.find((x) => x.id === "work").filter(e) && !isEvent(e) && !e.nested && e.group === "open");
  let p0 = work.filter((e) => e.p === "P0" || e.capped), note = null;
  if (!p0.length) { p0 = work.filter((e) => e.p === "P1").slice(0, 1); note = p0.length ? "No P0 today. Top P1:" : null; }
  const growth = items().filter((e) => e.lane === "personal" && isGrowth(e) && e.group === "open");
  const draft = growth.filter((e) => e.id.startsWith("linkedin-")).sort((a, b) => b.id.localeCompare(a.id))[0];
  return el("section", { class: "focus", "aria-label": "Your focus" },
    el("h2", {}, "Your focus"),
    el("div", { class: "slot" }, el("h3", {}, "Work"),
      el("div", { class: "sbody" }, note ? el("p", { class: "snote" }, note) : null,
        p0.length ? rows(p0, false) : el("p", { class: "snote" }, "No P0 or P1 at work. Pick from the To do list."))),
    el("div", { class: "slot" }, el("h3", {}, "Growth"),
      el("div", { class: "sbody" }, focusTick("growth", "Write or study today"), courseNext(),
        draft ? el("div", { class: "slinks" }, el("button", { class: "btn ghost", type: "button", onclick: () => openFile(draft) }, `Article draft: ${headline(draft)}`)) : null)));
}

/* ---------- Today ---------- */
function todayBuckets(list) {
  const t0 = day0();
  const late = (e) => e.due && new Date(e.due + "T00:00:00") < t0;
  const evs = list.filter(isEvent);
  const rest = list.filter((e) => !isEvent(e));
  const open = rest.filter((e) => e.group === "open");
  const carried = open.filter((e) => late(e) || (!e.due && e.backlog));
  return { evs, todo: open.filter((e) => !carried.includes(e)), carried, done: rest.filter((e) => e.group === "done" && !e.nested) };
}

function hero(all, b) {
  const now = new Date();
  const upcoming = all.filter(isEvent).filter((e) => new Date(e.end || e.start) > now && new Date(e.start) < new Date(day0().getTime() + 86400000))
    .sort((x, y) => new Date(x.start) - new Date(y.start));
  const nx = upcoming[0];
  let next = el("p", { class: "next-up quiet" }, "No more meetings today.");
  if (nx) {
    const s = new Date(nx.start), en = new Date(nx.end || nx.start), live = s <= now;
    const mins = Math.round((live ? en - now : s - now) / 60000);
    const span = mins >= 60 ? `${Math.floor(mins / 60)} h ${mins % 60} min` : `${mins} min`;
    const name = nx.title.replace(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+/, "").replace(/^\d{1,2}:\d{2}(\s*(to|-|–)\s*\d{1,2}:\d{2})?:?\s+/, "");
    next = el("button", { class: "next-up", type: "button", onclick: () => { state.todayTab = "cal"; render(); } },
      el("span", { class: "when" }, live ? "Now" : hm(s)),
      el("span", { class: "what" }, el("b", {}, name), el("span", {}, live ? `ends in ${span}` : `starts in ${span}`)));
  }
  const n = (l) => l.filter((e) => !e.nested).length;
  const d = state.date ? new Date(state.date + "T00:00:00") : now;
  return el("header", { class: "hero" },
    el("h1", {}, el("span", { class: "wd" }, d.toLocaleDateString("en-GB", { weekday: "long" })),
      el("span", { class: "dm" }, d.toLocaleDateString("en-GB", { day: "numeric", month: "long" }))),
    next,
    el("p", { class: "tally" },
      el("span", {}, el("b", {}, n(b.todo)), " to do"),
      el("span", { class: b.carried.length ? "warn" : "" }, el("b", {}, n(b.carried)), " overdue or carried"),
      el("span", {}, el("b", {}, b.done.length), " done")));
}

function todayView(all, list) {
  const ball = todayBuckets(all), b = todayBuckets(list);
  const now = new Date(), t1 = new Date(day0().getTime() + 86400000);
  const left = ball.evs.filter((e) => new Date(e.start) < t1 && new Date(e.end || e.start) > now).length;
  const n = (l) => l.filter((e) => !e.nested).length;
  const tabs = [
    { id: "todo", name: "To do", n: n(b.todo), body: () => priorityGroups(b.todo, true), empty: "Nothing due. Check Overdue for older items." },
    { id: "cal", name: "Calendar", n: left, body: () => calendarDays(b.evs), empty: "" },
    { id: "carried", name: "Overdue", n: n(b.carried), body: () => priorityGroups(b.carried, true), empty: "Nothing overdue or carried over." },
    { id: "done", name: "Done", n: b.done.length, body: () => b.done.length ? [rows(b.done, true)] : [], empty: "Nothing ticked yet today." },
  ];
  const cur = tabs.find((t) => t.id === state.todayTab) || tabs[0];
  const seg = el("div", { class: "seg", role: "tablist", "aria-label": "Today" }, tabs.map((t) =>
    el("button", { type: "button", role: "tab", class: t === cur ? "on" : "", "aria-selected": String(t === cur),
      onclick: () => { state.todayTab = t.id; render(); } }, t.name, el("span", { class: "n" }, String(t.n)))));
  const body = cur.body();
  const nd = items().filter((e) => e.group === "info").length;
  const digestLink = nd && !state.q && !state.pri.size ? el("a", { class: "digest-link", href: "#digest", onclick: (ev) => { ev.preventDefault(); go("digest"); } },
    el("b", {}, "Read today's digest"), el("span", {}, `${nd} ${nd === 1 ? "story" : "stories"} from work, finance, growth and alerts`)) : null;
  return [hero(all, ball), state.q || state.pri.size ? null : focusPanel(), digestLink, seg,
    el("div", { class: "panel", role: "tabpanel" }, body.length ? body : el("p", { class: "empty" }, state.q || state.pri.size ? "Nothing matches the filter." : cur.empty)),
    sourcesLine()];
}

/* ---------- sections ---------- */
function counts(list) {
  list = list.filter((e) => !isEvent(e) && !e.nested);
  const open = list.filter((e) => e.group === "open").length;
  const done = list.filter((e) => e.group === "done").length;
  return { open, done, pct: open + done ? Math.round((done / (open + done)) * 100) : 0 };
}

function sectionHead(p, c) {
  return el("header", { class: "shead" },
    el("h1", {}, p.name),
    c ? el("p", { class: "tally" }, el("span", {}, el("b", {}, c.open), " open"), el("span", {}, el("b", {}, c.done), " done today")) : null,
    c ? el("div", { class: "meter", "aria-hidden": "true" }, el("i", { style: `width:${c.pct}%` })) : null);
}

/* ---------- Digest: everything to know today, read like a newspaper ---------- */
const DESKS = [
  ["Clients", (e) => e.area === "client"],
  ["Product", (e) => e.area === "product"],
  ["Team and hiring", (e) => e.area === "hiring" || e.area === "people" || e.area === "team"],
  ["Work", (e) => e.lane === "work" || e.lane === "meetings" || notePillar(e, "work")],
  ["Money", (e) => e.lane === "finance" || e.lane === "ledger" || notePillar(e, "finance")],
  ["Kids", (e) => isKids(e)],
  ["The world", (e) => isGrowth(e) && (hasTag(e, "world") || hasTag(e, "regulation"))],
  [CONFIG.region.name || "Your region", (e) => isGrowth(e) && !!CONFIG.region.tag && hasTag(e, CONFIG.region.tag)],
  ["Business and sectors", (e) => isGrowth(e) && hasTag(e, "business")],
  ["AI and agents", (e) => isGrowth(e) && (hasTag(e, "ai") || hasTag(e, "paper") || hasTag(e, "physical-ai") || hasTag(e, "github"))],
  ["Tech, cloud and security", (e) => isGrowth(e) && (hasTag(e, "tech") || hasTag(e, "cloud") || hasTag(e, "security"))],
  ["Growth and reading", (e) => isGrowth(e)],
  ["Home and personal", () => true],
];

function story(e, lead) {
  const src = e.links?.length ? el("p", { class: "byline" }, e.links.map((l) => /^https?:|^obsidian:/.test(l.u)
    ? el("a", { href: l.u, target: "_blank", rel: "noopener noreferrer" }, l.t) : el("span", {}, l.t))) : null;
  const when = e.updated_at ? hm(new Date(e.updated_at)) : "";
  return el("article", { class: "story" + (lead ? " lead" : "") },
    el("p", { class: "kicker" }, [SOURCE[e.lane] || e.lane, e.area && e.area !== e.lane ? e.area : "", when].filter(Boolean).join(", ")),
    el("h3", {}, headline(e)),
    headline(e) !== e.title ? el("p", { class: "deck" }, e.title) : null,
    e.why && e.why !== e.title ? el("p", { class: "body" }, e.why) : null,
    src,
    el("button", { class: "more-link", type: "button", onclick: () => openFile(e) }, "Read the full note"));
}

function digestView(list) {
  const info = list.filter((e) => e.group === "info");
  const d = state.date ? new Date(state.date + "T00:00:00") : new Date();
  const gen = state.data?.generated?.at ? new Date(state.data.generated.at) : null;
  const out = [el("header", { class: "masthead" },
    el("h1", {}, "The Ataria Digest"),
    el("p", {}, d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }),
      el("span", {}, `${info.length} ${info.length === 1 ? "story" : "stories"}`),
      gen ? el("span", {}, `Edition of ${hm(gen)}`) : null))];
  if (!info.length) { out.push(el("p", { class: "empty" }, state.q || state.pri.size ? "No stories match the filter." : `No stories yet today. The digest fills after the ${runTimes()} runs.`)); return out; }
  const alerts = info.filter((e) => e.area === "alert");
  let rest = info.filter((e) => e.area !== "alert");
  const lead = rest.find((e) => e.area === "client") || rest.find((e) => e.area === "product") || rest[0];
  if (lead) { out.push(story(lead, true)); rest = rest.filter((e) => e !== lead); }
  for (const [name, test] of DESKS) {
    const g = rest.filter(test);
    if (!g.length) continue;
    rest = rest.filter((e) => !g.includes(e));
    out.push(el("section", { class: "desk" }, el("h2", {}, name, el("span", {}, String(g.length))), el("div", { class: "cols" }, g.map((e) => story(e, false)))));
  }
  if (alerts.length) out.push(el("section", { class: "desk wire" }, el("h2", {}, "Alerts wire", el("span", {}, String(alerts.length))),
    el("ul", {}, alerts.map((e) => el("li", {},
      el("b", {}, e.links?.[0]?.t ? `${e.links[0].t.split(":")[0]}: ` : ""), headline(e),
      e.why && e.why !== e.title ? el("span", { class: "body" }, " ", e.why) : null)))));
  return out;
}

function section(p) {
  const sec = el("div", { class: "view", id: p.id });
  if (p.month) {
    sec.append(sectionHead(p));
    const ms = state.months || [];
    sec.append(ms.length ? el("div", { class: "rows" }, ms.map((m) => el("div", { class: "row plainrow" },
      el("span", { class: "tick" }),
      el("div", { class: "rmain" }, el("div", { class: "rhead static" }, el("span", { class: "rt" }, `${m.month}, ${SOURCE[m.lane] || m.lane}`),
        el("span", { class: "rm" }, Object.entries(m.counts || {}).map(([k, v]) => el("span", { class: "m" }, `${v} ${k}`))))),
      el("button", { class: "btn ghost", type: "button", onclick: () => openFile({ title: m.title || `${m.lane} ${m.month}`, path: m.path }) }, "Open"))))
      : el("p", { class: "empty" }, "No monthly summaries yet. Each month closes on the 1st."));
    return sec;
  }
  const all = items().filter(p.filter);
  const list = all.filter(matches);
  if (p.today) { sec.append(...todayView(all, list)); return sec; }
  if (p.digest) { sec.classList.add("paper"); sec.append(...digestView(list)); return sec; }
  sec.append(sectionHead(p, counts(all)));
  if (p.id === "work") sec.append(...scheduleBlocks(list));
  let rest = p.id === "work" ? list.filter((e) => !(e.lane === "meetings" && e.kind === "item" && (isEvent(e) || e.area === "meeting" || e.area === "hiring"))) : list;
  if (p.id === "finance") {
    const b = budgetBlocks(all);
    sec.append(...b.blocks);
    rest = rest.filter((e) => !b.used.has(e.uid) && !(e.lane === "ledger" && e.id.startsWith("ledger-week-")));
  }
  if (p.id === "growth") {
    const g = growthBlocks(list);
    sec.append(...g.blocks);
    rest = rest.filter((e) => !g.used.has(e.uid));
  }
  const groups = priorityGroups(rest, false);
  if (groups.length) sec.append(...groups);
  else sec.append(el("p", { class: "empty" }, state.q || state.pri.size ? "Nothing matches the filter." : "Nothing open here."));
  sec.append(...infoBlock(list));
  return sec;
}

/* ---------- chrome: rail, tab bar, More sheet ---------- */
function go(id) {
  if (state.view === id) { window.scrollTo({ top: 0 }); return; }
  state.view = id;
  history.replaceState(null, "", "#" + id);
  if (id === "month") loadMonths().then(render); else render();
  window.scrollTo({ top: 0 });
}

function historySelect(sel) {
  const days = state.days?.length ? state.days : [{ date: "" }];   // "Latest" while the list loads
  sel.replaceChildren(...days.slice(0, 21).map((dd, i) => el("option", { value: i === 0 ? "" : dd.date, selected: (state.date || "") === (i === 0 ? "" : dd.date) },
    i === 0 ? "Latest" : `${new Date(dd.date + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}, ${dd.counts?.open ?? "–"} open`)));
  sel.onchange = () => { state.date = sel.value || null; $("#more").open && $("#more").close(); load(); };
}

function renderChrome() {
  const d = state.data;
  const gen = d?.generated?.at ? new Date(d.generated.at) : null;
  $("#stamp").textContent = gen ? `${state.date ? "Snapshot" : "Updated"} ${hm(gen)}` : "Loading";
  const lanes = d?.lanes || {};
  const daily = ["work", "finance", "personal", "google"];
  const bad = Object.entries(lanes).filter(([l, r]) => r.result === "failed" || (daily.includes(l) && ["missing", "not run today"].includes(r.result))).length;
  const errs = [...new Set(Object.values(lanes).filter((r) => r.result === "failed" && r.error).map((r) => r.error))];
  $("#health").textContent = bad ? `${bad} source${bad > 1 ? "s" : ""} did not run${errs.length ? ": " + errs.join("; ") : ". See Today, Sources."}` : "All sources ran.";
  $("#health").classList.toggle("warn", !!bad);

  $("#chips").replaceChildren(...["P0", "P1", "P2", "P3"].map((p) =>
    el("button", { type: "button", class: "chip", "aria-pressed": String(state.pri.has(p)), title: PRI_NAME[p],
      onclick: () => { state.pri.has(p) ? state.pri.delete(p) : state.pri.add(p); render(); } }, p)));

  const cnt = (p) => p.month ? null : p.digest ? items().filter((e) => e.group === "info").length : counts(items().filter(p.filter)).open;
  $("#nav").replaceChildren(...PILLARS.map((p, i) => el("a", { class: "navi" + (state.view === p.id ? " on" : ""), href: `#${p.id}`,
    "aria-current": state.view === p.id ? "page" : null, title: `Key ${i + 1}`, onclick: (ev) => { ev.preventDefault(); go(p.id); } },
    el("span", {}, p.name), cnt(p) !== null ? el("span", { class: "n" }, String(cnt(p))) : null)));
  historySelect($("#history"));

  const inMore = !TABBAR.includes(state.view);
  $("#tabbar").replaceChildren(...TABBAR.map((id) => {
    const p = PILLARS.find((x) => x.id === id);
    return el("a", { class: "tab" + (state.view === id ? " on" : ""), href: `#${id}`, "aria-current": state.view === id ? "page" : null,
      onclick: (ev) => { ev.preventDefault(); go(id); } }, svg(ICONS[id]), el("span", {}, p.name), cnt(p) ? el("i", {}, String(cnt(p))) : null);
  }), el("button", { class: "tab" + (inMore ? " on" : ""), type: "button", onclick: openMore }, svg(ICONS.more),
    el("span", {}, inMore ? PILLARS.find((x) => x.id === state.view).name : "More")));
}

function openMore() {
  const dlg = $("#more");
  const sel = el("select", { "aria-label": "Earlier days" });
  historySelect(sel);
  dlg.replaceChildren(
    el("div", { class: "sheet-h" }, el("b", {}, "More"), el("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")),
    el("nav", { class: "sheet-nav" }, PILLARS.filter((p) => !TABBAR.includes(p.id)).map((p) =>
      el("a", { class: "navi" + (state.view === p.id ? " on" : ""), href: `#${p.id}`, onclick: (ev) => { ev.preventDefault(); dlg.close(); go(p.id); } },
        el("span", {}, p.name), p.month ? null : el("span", { class: "n" }, String(counts(items().filter(p.filter)).open))))),
    el("label", { class: "history" }, "Earlier days", sel),
    el("div", { class: "ctl" },
      el("button", { class: "btn ghost", type: "button", onclick: () => { items().forEach((e) => state.open.add(e.uid)); dlg.close(); render(); } }, "Expand all"),
      el("button", { class: "btn ghost", type: "button", onclick: () => { state.open.clear(); dlg.close(); render(); } }, "Collapse all")),
    el("p", { class: "note" }, $("#health").textContent));
  dlg.showModal();
}

function render() {
  renderChrome();
  renderSaveBar();
  const page = $("#page");
  const y = window.scrollY;
  page.replaceChildren();
  if (state.date) page.append(el("p", { class: "snapshot" }, `Showing ${new Date(state.date + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}. Ticks still apply to live items. `,
    el("a", { href: "#", onclick: (ev) => { ev.preventDefault(); state.date = null; load(); } }, "Back to today")));
  page.append(section(PILLARS.find((p) => p.id === state.view) || PILLARS[0]));
  window.scrollTo(0, y);
}

/* ---------- interactions ---------- */
function toggleOpen(key) {
  state.open.has(key) ? state.open.delete(key) : state.open.add(key);
  document.querySelectorAll(`[data-uid="${CSS.escape(key)}"]`).forEach((n) => {
    n.classList.toggle("open", state.open.has(key));
    n.querySelector(".rhead, .evhead")?.setAttribute("aria-expanded", String(state.open.has(key)));
  });
}

let toastTimer;
function toast(msg, undo) {
  $("#toastMsg").textContent = msg;
  const t = $("#toast"); t.classList.add("show");
  $("#toastUndo").hidden = !undo;
  $("#toastUndo").onclick = () => { t.classList.remove("show"); undo && undo(); };
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 5000);
}

// A tick only stages the change; nothing is sent until Save. Ticking back to the saved state unstages it.
function tick(e, checked) {
  if (checked === (e.group === "done")) state.pending.delete(e.uid);
  else state.pending.set(e.uid, checked);
  render();
}

function renderSaveBar() {
  const n = state.pending.size;
  $("#savebar").hidden = n === 0;
  if (n) $("#saveMsg").textContent = `${n} change${n > 1 ? "s" : ""} not saved`;
  $("#save").disabled = state.busy;
  $("#save").textContent = state.busy ? "Saving" : "Save";
}

function discard() { state.pending.clear(); render(); }

async function save() {
  if (state.busy || !state.pending.size) return;
  const done = [...state.pending].filter(([, v]) => v).map(([k]) => k);
  const undone = [...state.pending].filter(([, v]) => !v).map(([k]) => k);
  state.busy = true; renderSaveBar();
  try {
    await api("done", { method: "POST", body: JSON.stringify({ done, undone }) });
    state.pending.clear();
    await load();
    const n = done.length + undone.length;
    toast(`Saved ${n} change${n > 1 ? "s" : ""}`, async () => {
      await api("done", { method: "POST", body: JSON.stringify({ done: undone, undone: done }) });
      await load();
    });
  } catch (err) {
    toast("Not saved: " + err.message + ". Try again.");
  } finally {
    state.busy = false; renderSaveBar();
  }
}

async function openFile(e) {
  let dlg = $("#filedlg");
  if (!dlg) { dlg = el("dialog", { id: "filedlg", class: "sheet doc" }); document.body.append(dlg); }
  dlg.replaceChildren(el("div", { class: "sheet-h" }, el("b", {}, headline(e)), el("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")),
    el("div", { class: "md", id: "filebody" }, "Loading"));
  dlg.showModal();
  try {
    const doc = await api("md?path=" + encodeURIComponent(e.path));
    $("#filebody").replaceChildren(markdown(doc.body || "", doc.path.split("/").slice(0, -1).join("/")));
  } catch (err) {
    $("#filebody").textContent = "Could not open the note: " + err.message;
  }
}

// Safe Markdown: DOM nodes only (headings, bullets, tables, links, bold).
function markdown(src, base = "") {
  const out = document.createDocumentFragment();
  let list = null, table = null;
  const inline = (text) => {
    const span = el("span"); const re = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_/g; let last = 0, m;
    while ((m = re.exec(text))) {
      span.append(text.slice(last, m.index));
      if (m[1]) span.append(/^https?:|^obsidian:/.test(m[2]) ? el("a", { href: m[2], target: "_blank", rel: "noopener noreferrer" }, m[1]) : m[1]);
      else if (m[3]) span.append(el("strong", {}, m[3]));
      else span.append(el("em", {}, m[4] || m[5]));
      last = re.lastIndex;
    }
    span.append(text.slice(last)); return span;
  };
  for (const line of src.split("\n")) {
    const img = line.match(/^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$/);
    if (img && !/^[a-z]+:/i.test(img[2])) {
      // Resolve the image against the note's folder; also try the day folder (DAY_DIR/assets).
      const parts = [];
      for (const seg of (base + "/" + img[2]).split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
      let rel = parts.join("/");
      if (/\/items\/assets\//.test(rel)) rel = rel.replace("/items/assets/", "/assets/");
      out.append(el("img", { src: `api/asset?path=${encodeURIComponent(rel)}`, alt: img[1], loading: "lazy", class: "fig" }));
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/), li = line.match(/^\s*[-*]\s+(?:\[( |x)\]\s+)?(.*)/), tr = line.match(/^\|(.*)\|\s*$/);
    if (!tr) table = null; if (!li) list = null;
    if (h) out.append(el("h3", {}, inline(h[2])));
    else if (li) { if (!list) { list = el("ul"); out.append(list); } list.append(el("li", {}, li[1] === "x" ? "✓ " : "", inline(li[2]))); }
    else if (tr) { if (/^\s*:?-+/.test(tr[1])) continue; if (!table) { table = el("table"); out.append(table); } table.append(el("tr", {}, tr[1].split("|").map((c) => el("td", {}, inline(c.trim()))))); }
    else if (line.trim()) out.append(el("p", {}, inline(line)));
  }
  return out;
}

/* ---------- wiring ---------- */
function toggleSearch(force) {
  const on = force ?? !document.body.classList.contains("searching");
  document.body.classList.toggle("searching", on);
  $("#searchBtn").setAttribute("aria-expanded", String(on));
  if (on) $("#q").focus();
}
$("#searchBtn").addEventListener("click", () => toggleSearch());
$("#q").addEventListener("input", (ev) => { state.q = ev.target.value.trim().toLowerCase(); render(); });
$("#expandAll").addEventListener("click", () => { items().forEach((e) => state.open.add(e.uid)); render(); });
$("#collapseAll").addEventListener("click", () => { state.open.clear(); render(); });
$("#save").addEventListener("click", save);
$("#discard").addEventListener("click", discard);
$("#more").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) ev.currentTarget.close(); });   // tap outside closes
window.addEventListener("hashchange", () => { const v = viewFromHash(); if (v !== state.view) go(v); });
window.addEventListener("beforeunload", (ev) => { if (state.pending.size) { ev.preventDefault(); ev.returnValue = ""; } });
document.addEventListener("keydown", (ev) => {
  if (ev.target.matches("input, textarea, select") && ev.key !== "Escape") return;
  if (ev.key === "/") { ev.preventDefault(); toggleSearch(true); }
  else if (ev.key === "Escape") { $("#q").value = ""; state.q = ""; state.pri.clear(); $("#q").blur(); toggleSearch(false); render(); }
  else if (ev.key === "e") { items().forEach((e) => state.open.add(e.uid)); render(); }
  else if (ev.key === "s") save();
  else if (/^[1-8]$/.test(ev.key)) go(PILLARS[+ev.key - 1].id);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && !state.date) load(); });

loadConfig().then(() => (state.view === "month" ? loadMonths() : Promise.resolve())).then(load);
setInterval(() => { if (!document.hidden && !state.date) load(); }, POLL_MS);
