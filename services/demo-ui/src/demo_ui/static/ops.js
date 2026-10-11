// Pane 5, Ops (doc 11 §2 "Pane 5"): log auto-scroll, copy buttons, re-check. No dependency, no request of its own:
// the runner's lines arrive on /events as `ops` frames, swapped by htmx-sse into #ops-log (beforeend).

const BOTTOM_SLACK_PX = 24;
let followLog = true; // false once the presenter scrolls up; true again at the bottom

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

// A re-rendered "Run here" column (job start or end) starts at the bottom of its backlog.
document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.id === "ops-run") {
    const log = document.getElementById("ops-log");
    if (log) {
      log.scrollTop = log.scrollHeight;
      followLog = true;
    }
  }
});

// The run and stop routes answer 403, 404 and 409 with a status fragment that says why: show it, don't drop it.
document.body.addEventListener("htmx:beforeSwap", (e) => {
  const status = e.detail.xhr ? e.detail.xhr.status : 0;
  if (e.detail.target && e.detail.target.id === "ops-msg" && [403, 404, 409].includes(status)) {
    e.detail.shouldSwap = true;
    e.detail.isError = false;
  }
});

// Re-check skips the checklist's 5 s cache; the 15 s poll does not.
document.body.addEventListener("htmx:configRequest", (e) => {
  const trigger = e.detail.triggeringEvent;
  if (e.detail.elt && e.detail.elt.id === "ops-checklist" && trigger && trigger.type === "click") {
    e.detail.parameters.fresh = "1";
  }
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
