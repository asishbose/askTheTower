// The Stage and the page's derived state (doc 11 §2 "Presentation").
//
// It reads only what the page already receives: the SSE frames HTMX swaps into the panes and the data-*
// attributes those fragments carry (each one a value the fragment already shows). It never calls the backend
// beyond fetching scene.svg once, and it only toggles classes on the scene's ids; it never redraws.
// Items are animated only when they are new since the previous swap of that target; the frames replayed when a
// page connects set the state without animating. prefers-reduced-motion turns the motion off, not the state.

const SCENE_URL = "/static/img/scene.svg";
const BEAM_MS = 600;
const PULSE_MS = 650;
const FLASH_MS = 900;

// Coral in the doc 11 colour table: solid (changed or alert) and outline (refusals).
const CORAL = new Set([
  "SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET", "UNREACHABLE", "ALERT_FAILED",
  "NOT_BOUND", "NO_CONSENT", "STALE_DATA", "CARRIER_ERROR", "SERVICE_UNAVAILABLE",
]);

// Feed target → the health-strip dot it stands for.
const FEED_SERVICE = { carrier: "mock", sms: "alerts", grants: "binding", audit: "dynamodb" };
const SERVICE_NAME = { mock: "mock carrier", alerts: "Alerts", binding: "binding page", dynamodb: "DynamoDB" };

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const moving = () => !reducedMotion.matches;

const state = { beacon: false, shieldOff: false, phoneLit: false, phoneRole: "", lastCodes: [] };
const primed = new Set();
const seen = { sms: new Set(), audit: new Set() };
const down = {};
const timers = new Map();
let scene = null;
let carrierSig = null;
let shieldSig = null;
let running = false;

const byId = (id) => document.getElementById(id);
const part = (id) => (scene ? scene.querySelector(`#${id}`) : null);
const codesOf = (el) => (el?.dataset.codes || "").split(" ").filter(Boolean);

function pulse(id, cls, ms) {
  const el = part(id);
  if (!el || !moving()) return;
  const key = `${id}.${cls}`;
  clearTimeout(timers.get(key));
  el.classList.remove(cls);
  void el.getBoundingClientRect(); // restart the keyframes
  el.classList.add(cls);
  timers.set(key, setTimeout(() => el.classList.remove(cls), ms));
}

function applyState() {
  part("beacon")?.classList.toggle("alert", state.beacon);
  part("shield")?.classList.toggle("off", state.shieldOff);
  const phone = part("phone");
  if (phone) {
    phone.classList.toggle("lit", state.phoneLit);
    const title = phone.querySelector("title");
    if (title) title.textContent = state.phoneRole ? `SMS from Alerts to: ${state.phoneRole}` : "SMS from Alerts";
  }
  const row = document.querySelector(".stage-codes");
  if (row) row.replaceChildren(...state.lastCodes.map(chip));
}

function chip(code) {
  const outer = document.createElement("span");
  outer.className = `rc rc-${code}`;
  const inner = document.createElement("span");
  inner.className = "chip";
  inner.textContent = code;
  outer.append(inner);
  return outer;
}

function announce(text) {
  const a = byId("announcer");
  if (a) a.textContent = text;
}

// ---------- health strip and the stack-down state ----------

function setDot(svc, up, title) {
  const li = document.querySelector(`.health li[data-svc="${svc}"]`);
  if (!li) return;
  li.classList.toggle("up", up);
  li.classList.toggle("down", !up);
  li.title = title;
}

function feedHealth(target) {
  const svc = FEED_SERVICE[target.id];
  const off = target.querySelector("[data-down]");
  down[svc] = Boolean(off);
  setDot(svc, !off, off ? `${SERVICE_NAME[svc]}: ${off.title}` : `${SERVICE_NAME[svc]}: answering`);
  updateStackDown();
}

function updateStackDown() {
  const env = document.querySelector("#pill [data-env]")?.dataset.env;
  const all = Object.keys(SERVICE_NAME).every((svc) => down[svc] === true);
  document.body.classList.toggle("stack-down", env === "local" && all);
}

// ---------- per-target handlers ----------

function onCarrier(target) {
  const time = target.querySelector("time[datetime]");
  const dial = target.querySelector(".dial");
  const t = time ? new Date(time.getAttribute("datetime")) : null;
  if (dial && t && !Number.isNaN(t.getTime())) {
    const m = t.getUTCMinutes();
    dial.style.setProperty("--m", `${m * 6}deg`);
    dial.style.setProperty("--h", `${((t.getUTCHours() % 12) + m / 60) * 30}deg`);
  }
  const cards = [...target.querySelectorAll(".line-card")].map((c) =>
    [c.dataset.ref, c.dataset.sim, c.dataset.cf, c.dataset.reach, c.dataset.conn].join("|"));
  const subs = [...target.querySelectorAll(".sub")].map((s) => s.dataset.events);
  const sig = [...cards, ...subs].join(";");
  if (primed.has("carrier") && carrierSig !== null && sig !== carrierSig && cards.length) pulse("rings", "pulse", PULSE_MS);
  carrierSig = sig;
}

function onSms(target) {
  const items = [...target.querySelectorAll(".ev-sms")];
  const fresh = items.filter((li) => !seen.sms.has(li.dataset.n));
  items.forEach((li) => seen.sms.add(li.dataset.n));
  state.phoneLit = items.length > 0;
  if (items[0]) state.phoneRole = items[0].dataset.role || "";
  if (primed.has("sms") && fresh.length) {
    pulse("beam-sms", "go", BEAM_MS);
    pulse("phone", "buzz", BEAM_MS);
    const n = fresh[0];
    announce(`SMS sent to ${n.dataset.role}: ${n.dataset.template}`);
  }
}

function onAudit(target) {
  const rows = [...target.querySelectorAll(".ev-audit")];
  const fresh = rows.filter((li) => !seen.audit.has(li.dataset.key));
  rows.forEach((li) => seen.audit.add(li.dataset.key));
  if (!primed.has("audit") || !fresh.length) return;
  pulse("ledger", "tick", FLASH_MS);
  if (fresh.some((li) => codesOf(li).includes("SUPPRESSED_REVOKED"))) pulse("shield", "flash", FLASH_MS);
  const r = fresh[0];
  const what = r.querySelector(".ev-what")?.textContent.replace(/\s+/g, " ").trim();
  announce(`audit row: ${what}`);
}

function onGrants(target) {
  const resolve = target.querySelector(".resolve[data-resolve-grant]");
  const revoked = [...target.querySelectorAll(".graph > li[data-grant]")].map((e) => `${e.dataset.grant}:${e.dataset.revoked}`).join(",");
  if (resolve) state.shieldOff = resolve.dataset.resolveGrant === "none";
  const sig = `${resolve?.dataset.resolveGrant}|${revoked}`;
  if (primed.has("grants") && shieldSig !== null && sig !== shieldSig) pulse("shield", "flash", FLASH_MS);
  shieldSig = sig;
}

// A turn fragment's metadata sits on its <header> (the <article> tag itself is pinned by the runner tests).
// `replay` is read from the header's "replay" tag, never from the article's class: #log keeps every run, so a
// replayed step's <article id> repeats an earlier run's and htmx gives the new element the old one's attributes
// until it settles, which is after this handler runs.
function isReplay(meta) {
  const tag = meta.querySelector(":scope > .tag");
  return tag !== null && tag.textContent.trim() === "replay";
}

function onTurn(article) {
  if (!article || article.tagName !== "ARTICLE") return;
  const meta = article.querySelector(":scope > header");
  if (!meta) return;
  const { kind } = meta.dataset;
  const replay = isReplay(meta);
  addStep(article, meta, replay);
  if (kind === "say") {
    const codes = codesOf(meta);
    state.lastCodes = codes;
    state.beacon = codes.some((c) => CORAL.has(c));
    if (codes.length) setDot("tower", true, "Tower: answered this session");
    if (!replay) {
      const from = meta.dataset.who === "mom" ? "mom" : "you";
      pulse("beam-req", `go-${from}`, BEAM_MS);
      if (from === "you") pulse("house-you", "speaking", BEAM_MS);
      if (state.beacon) pulse("beacon", "flash", FLASH_MS);
      floatChips(codes);
    }
  } else if (kind === "fire" || kind === "advance") {
    pulse("rings", "pulse", PULSE_MS);
  } else if (kind === "grant") {
    pulse("shield", "flash", FLASH_MS);
  }
}

function floatChips(codes) {
  const box = document.querySelector(".stage-float");
  if (!box || !moving() || !codes.length) return;
  const group = document.createElement("div");
  group.append(...codes.map(chip));
  group.querySelectorAll(".rc").forEach((rc) => rc.addEventListener("animationend", () => group.remove(), { once: true }));
  box.replaceChildren(group);
}

// ---------- the stepper and the story tiles ----------

function stepItem(cls, glyph, label) {
  const li = document.createElement("li");
  li.className = cls;
  const g = document.createElement("span");
  g.className = "g";
  g.setAttribute("aria-hidden", "true");
  g.textContent = glyph;
  const text = document.createElement("span");
  text.className = "label";
  text.textContent = label;
  li.append(g, text);
  return li;
}

function addStep(article, meta, replay) {
  const stepper = byId("stepper");
  const { step, kind, ok, ms } = meta.dataset;
  if (!stepper || !step) return; // a /say turn: not a macro step
  if (replay) {
    let node = stepper.querySelector("li.replay");
    if (!node) {
      node = stepItem("replay", "↺", "");
      node.dataset.count = "0";
      stepper.append(node);
    }
    node.dataset.count = String(Number(node.dataset.count) + 1);
    if (ok === "fail") node.classList.add("fail");
    node.querySelector(".label").textContent = `replayed ${node.dataset.count} earlier steps`;
    return;
  }
  const cls = ok === "pass" ? "pass" : ok === "fail" ? "fail" : "neutral";
  const glyph = ok === "pass" ? "✓" : ok === "fail" ? "✗" : "•";
  const node = stepItem(cls, glyph, `${step} ${kind}`);
  const time = document.createElement("span");
  time.className = "ms";
  time.textContent = `${ms} ms`;
  node.append(time);
  if (ok === "fail") {
    const a = document.createElement("a");
    a.href = `#${article.id}`;
    a.textContent = "see transcript";
    node.append(" ", a);
  }
  stepper.append(node);
}

function setTiles(name, value) {
  document.querySelectorAll(".tile").forEach((t) => {
    if (name === null || t.dataset.macro === name) t.dataset.state = value;
    else t.dataset.state = "";
  });
}

let pendingTile = null;

function onRunStatus(target) {
  const s = target.querySelector(".status");
  if (!s) return;
  const level = s.dataset.level;
  const stepper = byId("stepper");
  if (level === "info" && !running) {
    running = true;
    document.body.classList.add("running");
    stepper?.replaceChildren();
    if (pendingTile) setTiles(pendingTile, "running");
  } else if (level === "pass" || level === "fail") {
    running = false;
    document.body.classList.remove("running");
    if (level === "fail") stepper?.append(stepItem("stopped", "✗", `stopped: ${s.textContent.trim()}`));
    else stepper?.append(stepItem("done", "✓", s.textContent.trim()));
    if (pendingTile) setTiles(pendingTile, level);
    pendingTile = null;
  }
}

// ---------- clear view (recording aid) ----------
// "clear" on a pane hides what is on screen now and nothing else: the audit chain, the sent list and the
// transcript are untouched, and rows that arrive later still show. Keys are the rows' own data-key / data-n.
const hiddenFeed = { audit: new Set(), sms: new Set() };

function feedRowKey(li) {
  return li.dataset.key || li.dataset.n || "";
}

function applyFeedClear(target) {
  const hidden = hiddenFeed[target.id];
  if (!hidden || hidden.size === 0) return;
  for (const li of target.querySelectorAll("li.ev")) li.hidden = hidden.has(feedRowKey(li));
}

function clearFeedView() {
  for (const id of ["audit", "sms"]) {
    const pane = document.getElementById(id);
    if (!pane) continue;
    for (const li of pane.querySelectorAll("li.ev")) hiddenFeed[id].add(feedRowKey(li));
    applyFeedClear(pane);
  }
}

function clearLogView() {
  const log = document.getElementById("log");
  const stepper = document.getElementById("stepper");
  if (log) log.replaceChildren();
  if (stepper) stepper.replaceChildren();
}

document.getElementById("clear-feed")?.addEventListener("click", clearFeedView);
document.getElementById("clear-log")?.addEventListener("click", clearLogView);

// ---------- wiring ----------

const SSE_HANDLERS = { carrier: onCarrier, sms: onSms, audit: onAudit, grants: onGrants };

document.body.addEventListener("htmx:sseMessage", (e) => {
  const target = e.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "log") onTurn(target.firstElementChild);
  else if (target.id === "run-status") onRunStatus(target);
  else if (SSE_HANDLERS[target.id]) {
    feedHealth(target);
    SSE_HANDLERS[target.id](target);
    applyFeedClear(target);
    primed.add(target.id);
  }
  applyState();
});

document.body.addEventListener("htmx:beforeRequest", (e) => {
  const elt = e.detail?.elt;
  if (elt instanceof HTMLElement && elt.matches(".tile")) {
    pendingTile = elt.dataset.macro;
    setTiles(null, "");
  }
});

document.body.addEventListener("htmx:afterSwap", (e) => {
  const target = e.detail?.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "pill") updateStackDown();
  // a macro refused at the door (ENV, busy) never starts: clear the tile it marked
  if (target.id === "run-status" && !running && target.querySelector(".status.fail")) {
    setTiles(null, "");
    pendingTile = null;
  }
});

async function loadScene() {
  const holder = byId("stage");
  if (!holder) return;
  const r = await fetch(SCENE_URL, { credentials: "same-origin" });
  if (!r.ok) return;
  holder.innerHTML = await r.text(); // our own static file, same origin
  scene = holder.querySelector("svg");
  if (!scene) return;
  scene.setAttribute("aria-hidden", "true");
  scene.setAttribute("focusable", "false");
  for (const id of ["house-you", "house-mom"]) {
    part(id)?.addEventListener("click", () =>
      byId("pane-binding")?.scrollIntoView({ behavior: moving() ? "smooth" : "auto", block: "start" }));
  }
  applyState();
}

loadScene();
