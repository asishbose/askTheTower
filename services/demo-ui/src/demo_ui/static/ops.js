// The Ops tab (doc 11 §2 "The Ops tab"): tab switching from the hash, `ops:open` the first time Ops is shown, log
// auto-scroll, copy buttons, typed-confirm fields, the /32 field, re-check, and the 4xx status fragments. No
// dependency, no request of its own: htmx fetches the fragments, the runner's lines arrive on /events as `ops`
// frames (htmx-sse swaps them into #ops-log, beforeend). The server checks every word again; this only helps.

const BOTTOM_SLACK_PX = 24;
let followLog = true; // false once the presenter scrolls up; true again at the bottom
let opened = false; // `ops:open` fires once: presenting on Control room starts no probe and no AWS call
let ready = false; // htmx has processed the page (its triggers exist): only then may `ops:open` fire

function showTab() {
  const ops = window.location.hash === "#ops";
  document.body.dataset.tab = ops ? "ops" : "control";
  const section = document.getElementById("tab-ops");
  if (section) section.hidden = !ops;
  for (const [id, on] of [["tab-btn-control", !ops], ["tab-btn-ops", ops]]) {
    const tab = document.getElementById(id);
    if (tab) tab.setAttribute("aria-selected", on ? "true" : "false");
  }
  if (ops && ready && !opened) {
    opened = true;
    document.body.dispatchEvent(new CustomEvent("ops:open", { bubbles: true }));
  }
}

window.addEventListener("hashchange", showTab);
showTab(); // the right tab at once (no flash of Control room on /#ops) …
// … and `ops:open` once htmx is ready. A module script runs before DOMContentLoaded (readyState "interactive"), and
// htmx (a classic script in <head>) processes the page in its own DOMContentLoaded handler, registered first.
function whenReady() {
  ready = true;
  showTab();
}
if (document.readyState === "complete") setTimeout(whenReady, 0);
else document.addEventListener("DOMContentLoaded", () => setTimeout(whenReady, 0));

function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
}

document.body.addEventListener("scroll", (e) => {
  if (e.target && e.target.id === "ops-log") followLog = atBottom(e.target);
}, true);

document.body.addEventListener("htmx:sseMessage", (e) => {
  const log = e.target;
  if (log && log.id === "ops-log" && followLog) log.scrollTop = log.scrollHeight;
});

// --- typed confirmation: Run enables only when the field holds the word (and nothing else blocks it) ---------------

function syncRun(form) {
  const button = form.querySelector("button.run");
  if (!button || button.dataset.why) return; // blocked by the gate, a job or the order: stays disabled
  const field = form.querySelector("input.confirm");
  button.disabled = field ? field.value.trim() !== field.dataset.word : false;
}

document.body.addEventListener("input", (e) => {
  const form = e.target && e.target.closest ? e.target.closest("form.ck-run") : null;
  if (form) syncRun(form);
});

// The stepper re-renders every 15 s and after each job: keep what the presenter typed (word, /32) across a swap.
const typed = new Map();
const KEEP = "#ops-checklist, #ops-teardown";

document.body.addEventListener("htmx:beforeSwap", (e) => {
  const target = e.detail.target;
  const status = e.detail.xhr ? e.detail.xhr.status : 0;
  // The run and stop routes answer 400, 403, 404 and 409 with a status fragment that says why: show it.
  if (target && target.id === "ops-msg" && [400, 403, 404, 409].includes(status)) {
    e.detail.shouldSwap = true;
    e.detail.isError = false;
  }
  if (target && target.matches && target.matches(KEEP)) {
    for (const input of target.querySelectorAll("form.ck-run input")) {
      const form = input.closest("form");
      if (input.value) typed.set(`${form.getAttribute("hx-post")}|${input.name}`, input.value);
    }
  }
});

document.body.addEventListener("htmx:afterSwap", (e) => {
  const target = e.target;
  if (target && target.id === "ops-run") { // a job started or ended: the log starts at the bottom of its backlog
    const log = document.getElementById("ops-log");
    if (log) {
      log.scrollTop = log.scrollHeight;
      followLog = true;
    }
  }
  if (target && target.matches && target.matches(KEEP)) {
    for (const form of target.querySelectorAll("form.ck-run")) {
      for (const input of form.querySelectorAll("input")) {
        const kept = typed.get(`${form.getAttribute("hx-post")}|${input.name}`);
        if (kept !== undefined && !input.disabled) input.value = kept;
      }
      syncRun(form);
    }
  }
});

// A job that started clears the words typed for it (a second run asks again).
document.body.addEventListener("htmx:afterRequest", (e) => {
  const form = e.detail.elt && e.detail.elt.closest ? e.detail.elt.closest("form.ck-run") : null;
  if (!form || !e.detail.xhr || e.detail.xhr.status !== 202) return;
  const field = form.querySelector("input.confirm");
  if (field) {
    typed.delete(`${form.getAttribute("hx-post")}|confirm`);
    field.value = "";
    syncRun(form);
  }
});

// Re-check skips every cache (?fresh=1); the 15 s poll (no triggering event) only re-reads files (?tick=1).
document.body.addEventListener("htmx:configRequest", (e) => {
  const elt = e.detail.elt;
  if (!elt || !["ops-checklist", "ops-header", "ops-teardown"].includes(elt.id)) return;
  const trigger = e.detail.triggeringEvent;
  if (trigger && trigger.type === "click") e.detail.parameters.fresh = "1";
  else if (!trigger && elt.id === "ops-checklist") e.detail.parameters.tick = "1";
});

document.body.addEventListener("click", async (e) => {
  const button = e.target.closest ? e.target.closest("button.copy") : null;
  if (!button) return;
  const code = document.getElementById(button.dataset.copy || "");
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code.textContent.trim());
    button.textContent = "copied";
  } catch {
    button.textContent = "select and copy";
  }
  setTimeout(() => { button.textContent = "copy"; }, 1500);
});
