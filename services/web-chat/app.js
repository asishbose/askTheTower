// Ask the Tower — web chat page (docs/architecture/components/09-reference-client.md §6.4).
// Plain JS, no framework, no build step. Sign in (Cognito Hosted UI with PKCE, or the local stub) → ask →
// render the agent's text. The only link this page ever shows is a bind link under BINDING_BASE_URL/bind/,
// taken from the agent's next_step (which is Tower's, copied); no URL is built here or taken from model text.
(function () {
  "use strict";

  const cfg = window.WEB_CHAT_CONFIG || {};
  const LOCAL = !cfg.COGNITO_DOMAIN; // no Cognito configured → the local sign-in stub (TOWER_ENV=local only)
  const BINDING_BASE = String(cfg.BINDING_BASE_URL || "").replace(/\/+$/, "");
  const BIND_PREFIX = BINDING_BASE + "/bind/";
  const KEY = { token: "atb.token", user: "atb.user", verifier: "atb.pkce", state: "atb.state", session: "atb.session" };
  const store = window.sessionStorage;
  const $ = (id) => document.getElementById(id);

  // --- the bind-link rule (09 §6.2 rule 5, applied again here) ---------------------------------------------
  function isBindLink(url) {
    if (!BINDING_BASE || typeof url !== "string" || !url.startsWith(BIND_PREFIX)) return false;
    try {
      const u = new URL(url);
      return u.protocol === "https:" || u.protocol === "http:";
    } catch (e) {
      return false;
    }
  }

  // --- small helpers ------------------------------------------------------------------------------------------
  function b64url(bytes) {
    let s = "";
    bytes.forEach((b) => { s += String.fromCharCode(b); });
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function randomString(n) {
    return b64url(crypto.getRandomValues(new Uint8Array(n)));
  }

  function sessionId() {
    // [A-Za-z0-9-]{33,128}: the AgentCore Runtime session header needs at least 33 characters.
    let id = store.getItem(KEY.session);
    if (!id) {
      id = (crypto.randomUUID() + "-" + crypto.randomUUID()).slice(0, 73);
      store.setItem(KEY.session, id);
    }
    return id;
  }

  // --- sign-in -------------------------------------------------------------------------------------------------
  async function startCognito() {
    const verifier = randomString(48);
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    const state = randomString(16);
    store.setItem(KEY.verifier, verifier);
    store.setItem(KEY.state, state);
    const q = new URLSearchParams({
      response_type: "code",
      client_id: cfg.CLIENT_ID,
      redirect_uri: cfg.REDIRECT_URI,
      scope: "openid",
      code_challenge_method: "S256",
      code_challenge: b64url(new Uint8Array(digest)),
      state: state,
    });
    window.location.assign(cfg.COGNITO_DOMAIN + "/oauth2/authorize?" + q.toString());
  }

  async function finishCognito(params) {
    const expected = store.getItem(KEY.state);
    const verifier = store.getItem(KEY.verifier);
    store.removeItem(KEY.state);
    store.removeItem(KEY.verifier);
    window.history.replaceState(null, "", window.location.pathname);
    if (!expected || params.get("state") !== expected || !verifier) return;
    const r = await fetch(cfg.COGNITO_DOMAIN + "/oauth2/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: cfg.CLIENT_ID,
        code: params.get("code"),
        redirect_uri: cfg.REDIRECT_URI,
        code_verifier: verifier,
      }),
    });
    if (!r.ok) return;
    const body = await r.json();
    if (body.access_token) store.setItem(KEY.token, body.access_token);
  }

  function signOut() {
    [KEY.token, KEY.user, KEY.session].forEach((k) => store.removeItem(k));
    if (!LOCAL) {
      const q = new URLSearchParams({ client_id: cfg.CLIENT_ID, logout_uri: cfg.REDIRECT_URI });
      window.location.assign(cfg.COGNITO_DOMAIN + "/logout?" + q.toString());
      return;
    }
    show();
  }

  // --- chat ----------------------------------------------------------------------------------------------------
  function addMessage(who, text) {
    const li = document.createElement("li");
    li.className = who;
    const p = document.createElement("p");
    p.textContent = text;
    li.appendChild(p);
    $("messages").appendChild(li);
    li.scrollIntoView({ block: "end" });
    return li;
  }

  function renderNextStep(li, step) {
    if (!step || step.kind !== "bind_line") return; // other kinds: the text covers them (09 §6.4)
    if (!isBindLink(step.url)) {
      console.warn("NEXT_STEP_REJECTED"); // never the URL itself
      return;
    }
    const card = $("bind-card").content.cloneNode(true);
    const a = card.querySelector("a");
    a.href = step.url;
    a.textContent = step.url;
    card.querySelector(".copy").addEventListener("click", (ev) => {
      navigator.clipboard.writeText(step.url).then(() => { ev.target.textContent = "Copied"; });
    });
    li.appendChild(card);
  }

  const FAILURES = {
    401: "Your sign-in has expired. Please sign in again.",
    422: "Please ask in words. Don't type phone numbers here; say \"my line\" or \"Mom's line\".",
    502: "Tower is unavailable, try again.",
    503: "The agent is unavailable, try again.",
  };

  async function ask(text) {
    addMessage("me", text);
    const headers = {
      "Content-Type": "application/json",
      Authorization: "Bearer " + store.getItem(KEY.token),
    };
    if (LOCAL) headers["X-Tower-User"] = store.getItem(KEY.user);
    else headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] = sessionId();
    let r;
    try {
      r = await fetch(cfg.AGENT_URL, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ input: text, session_id: sessionId() }),
      });
    } catch (e) {
      addMessage("tower", FAILURES[502]);
      return;
    }
    if (r.status === 401) {
      store.removeItem(KEY.token);
      addMessage("tower", FAILURES[401]);
      show();
      return;
    }
    if (!r.ok) {
      addMessage("tower", FAILURES[r.status] || FAILURES[502]);
      return;
    }
    const reply = await r.json();
    const li = addMessage("tower", reply.text || "");
    renderNextStep(li, reply.next_step);
  }

  // --- wiring --------------------------------------------------------------------------------------------------
  function show() {
    const signedIn = Boolean(store.getItem(KEY.token)) && (!LOCAL || Boolean(store.getItem(KEY.user)));
    $("sign-in").hidden = signedIn;
    $("chat").hidden = !signedIn;
    $("who").hidden = !signedIn;
    $("sign-in-cognito").hidden = LOCAL;
    $("sign-in-stub").hidden = !LOCAL;
    $("who-name").textContent = LOCAL ? "Signed in as " + (store.getItem(KEY.user) || "") : "Signed in";
    if (signedIn) $("question").focus();
  }

  async function main() {
    const params = new URLSearchParams(window.location.search);
    if (!LOCAL && params.has("code")) await finishCognito(params);
    $("sign-in-button").addEventListener("click", startCognito);
    $("sign-out").addEventListener("click", signOut);
    document.querySelectorAll("#sign-in-stub [data-user]").forEach((b) => {
      b.addEventListener("click", () => {
        store.setItem(KEY.user, b.dataset.user);
        store.setItem(KEY.token, cfg.LOCAL_BEARER || "");
        show();
      });
    });
    $("ask").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const q = $("question").value.trim();
      $("question").value = "";
      if (q) ask(q);
    });
    show();
  }

  main();
})();
