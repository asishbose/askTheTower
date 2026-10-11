# Spike A — Alexa+ MCP Toolkit: access, registration, identity shape

> **Status: NOT RUN** (autonomous build, 2026-10-06: no Alexa+ developer account). This page is the template
> to fill on the run. Script: `scripts/spikes/a_alexa_echo.py` (tested offline by `tests/spikes`).

## Assumption being tested

The build already codes an answer (RUN-ALL Decisions; `components/01` §4; `services/tower-mcp/src/tower_mcp/auth.py`):
every `tools/call` carries `Authorization: Bearer <JWT>` issued by *our* account-linking provider, verifiable
against `TOWER_JWKS_URL`, with `exp` and `sub` (and `aud` or `client_id`); `user_id` = `sub`.

| If the spike shows… | Change |
|---|---|
| Bearer JWT with `sub` on every call | Nothing. Record issuer, `alg`, `kid`, and whether `aud` or `client_id` is set → `TOWER_JWT_*`. |
| An opaque (non-JWT) bearer | `auth.py`: introspect or call the provider's userinfo instead of JWKS; cache per token. |
| A user id in `_meta` or a header, no bearer | `auth.py`: read it from there; Tower needs another way to trust the caller (Runtime authorizer or mTLS). |
| Nothing per user | Tower must run its own linking (01 §7 first bullet): a one-time code spoken or texted to bind the voice user. |

## Steps (fill in each one with minutes and the exact error, if any)

1. Toolkit onboarding from a Canadian developer account. For each step, note whether it is region-gated (US-only)
   or partner-gated ("select partners only"). _Result:_
2. `uv run python scripts/spikes/a_alexa_echo.py serve --port 8765`, then expose it, for example with
   `cloudflared tunnel --url http://127.0.0.1:8765`. Register `<tunnel>/mcp`. _Result:_
3. In the simulator say "say back hello tower" (calls `echo`), then "is my line okay" (calls `summary_probe`). _Result:_
4. `uv run python scripts/spikes/a_alexa_echo.py analyse artifacts/spikes/A-capture.jsonl`. Paste the verdict and
   one redacted `tools/call` record below. The capture is gitignored; the script keeps only token *shape* and
   replaces digit runs. _Result:_

## Captured request (redacted)

```json
(paste one record from A-capture.jsonl)
```

## Identity field(s)

- Header / `_meta` key: · JWT? · `iss`: · `alg`/`kid`: · `aud` / `client_id`: · `sub` shape:

## Summary verbatim or paraphrased?

`summary_probe` returns `"Your line looks fine. Nothing has changed on it in the last seven days."`.
What Alexa+ said: _ → verbatim / paraphrased. If paraphrased, the reference client's transcript tests stay the
regression guard (01 §7).

## Gating findings (for the friction log, prompt 17)

- Region: · Partner status: · Simulator usable from Ottawa:

**If blocked:** record the exact error here. Prompt 15 then becomes "the reference client is the demo surface"
(already the local default).

**Verdict:** _not run — the bearer-JWT `sub` assumption in `auth.py` stands untested._ Updates: `components/01` §4, §7 (link to this page).
