# Tower latency — measured against http://localhost:8080/mcp

> **measured against http://localhost:8080/mcp.** Measured against a running Tower over the network.
>
> Local compose stack (`make up`, prompt 12): Tower, mock and DynamoDB Local in containers on one Docker network on a WSL2 laptop (Docker 29.2.1, 24 cores); the client is on the host via the published port. Not the AWS number (`make latency-aws`).

- **date:** 2026-10-06 21:38 UTC
- **machine:** Linux 6.18.40.1-microsoft-standard-WSL2 x86_64, 24 CPUs, Python 3.12.9
- **target:** http://localhost:8080/mcp
- **store:** as deployed
- **mock:** mock-carrier 0.1.0 (scenario demo.yaml, clock 2026-10-05T14:00Z)
- **fastmcp:** 4.0.11
- **calls:** 200 per path, sequential, after 5 uncounted warm-up calls; client-side wall time per JSON-RPC `tools/call` POST

| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate (p95 < 400 ms) |
|---|---:|---:|---:|---:|---:|---|
| line_is_ok(self) — source carrier | 200 | 150.6 | 236.7 | 298.8 | 327.1 | pass |
| line_is_ok(mom) — source watch | 200 | 162.2 | 228.6 | 264.7 | 350.4 | reported (not gated) |
| is_reachable(mom) — source carrier | 200 | 195.0 | 311.7 | 400.6 | 438.8 | pass |
| watch_line(self) — source None | 200 | 213.3 | 370.5 | 395.6 | 543.3 | reported (not gated) |


Where the time goes: run Tower with `LOG_LEVEL=debug`; each call logs per-step timings (`resolve`, `load`, `carrier`, `policy`, `audit`) with `line_id` only.
