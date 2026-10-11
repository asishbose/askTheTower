# Tower latency — in-process, not representative

> **in-process, not representative.** Mock carrier, DynamoDB and Tower in one Python process; calls go through the real MCP Streamable HTTP stack over an ASGI transport, so there is no network hop and the mock answers in microseconds. This measures our code path, not a deployment. The real numbers come from `make latency-aws` (deferred).

- **date:** 2026-10-06 20:00 UTC
- **machine:** Linux 6.18.40.1-microsoft-standard-WSL2 x86_64, 24 CPUs, Python 3.12.9
- **target:** in-process ASGI (Streamable HTTP)
- **store:** DynamoDB Local (testcontainers)
- **mock:** mock-carrier 0.1.0 (scenario demo.yaml, clock 2026-10-05T14:00Z)
- **fastmcp:** 4.0.11
- **calls:** 200 per path, sequential, after 5 uncounted warm-up calls; client-side wall time per JSON-RPC `tools/call` POST

| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate (p95 < 400 ms) |
|---|---:|---:|---:|---:|---:|---|
| line_is_ok — live (carrier) | 200 | 185.3 | 249.9 | 266.1 | 297.6 | pass |
| line_is_ok — stored state (Watch) | 200 | 71.2 | 96.6 | 105.9 | 132.7 | reported (not gated) |
| is_reachable — live (carrier) | 200 | 120.4 | 173.3 | 185.4 | 204.1 | pass |
| watch_line — status | 200 | 85.2 | 109.6 | 156.6 | 188.5 | reported (not gated) |

- *line_is_ok — live (carrier)*: no Watch: SIM swap check + call forwarding in parallel (no swap, so no date call)
- *line_is_ok — stored state (Watch)*: fresh Watches.last_state: zero carrier calls
- *is_reachable — live (carrier)*: one Device Reachability Status call
- *watch_line — status*: grants + recent_checks reads; no carrier call

Where the time goes: run Tower with `LOG_LEVEL=debug`; each call logs per-step timings (`resolve`, `load`, `carrier`, `policy`, `audit`) with `line_id` only.

## Where the time goes (hand-added, 2026-10-06)

The stored-state path missed the "expect < 50 ms" note (p95 96.6 ms). Per-step timings from a 40-call
`LOG_LEVEL=debug` run of the same harness (DynamoDB Local) show the tool itself is not where it goes:

| Path | resolve | load (Watch) | carrier | policy | audit | handler total | client wall p50 (that run) |
|---|---:|---:|---:|---:|---:|---:|---:|
| line_is_ok — stored | 3.5–3.8 ms | 2.3–2.8 ms | — | 0.1 ms | 5–7.5 ms | 11–14 ms | 47 ms |
| line_is_ok — live | 3.5–4.1 ms | 4.7–5.6 ms | 29–44 ms | 0.0 ms | 9–10 ms | 48–63 ms | 136 ms |
| is_reachable — live | 2.8 ms | 3.5 ms | 24 ms | — | 6.2 ms | 37 ms | 51 ms |

The remaining 35–90 ms per call is the in-process stack: FastMCP's Streamable HTTP layer (a fresh
transport per stateless request), JSON-RPC parsing, pydantic serialisation of the result, and the mock
carrier and DynamoDB client sharing one event loop and one Python process with the caller. A deployed
Tower pays a network hop instead; re-measure with `make latency-aws` before reading anything into these.
