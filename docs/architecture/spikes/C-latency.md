# Spike C — hot-path latency harness

> **Status: run in-process** (2026-10-06, WSL2 x86_64, 24 CPUs, Python 3.12.9, fastmcp 4.0.11). The
> through-Gateway variant is deferred (no AWS). Scripts: `scripts/spikes/c_three_hop.py` (this spike's
> throwaway server) and `scripts/latency.py` (the kept harness, which prompt 08 already grew into Tower's gate).

**Question.** What does a 3-hop request cost before any real code, so the p95 < 400 ms gate is grounded?

**Shape.** This is one FastMCP tool, `three_hops`, called over Streamable HTTP (stateless, JSON responses, the
same settings as Tower). It does one `GetItem`, then two parallel `POST /sim-swap/v2/check` calls to the spike B
stub (bearer from `/oauth2/token`, fetched once), then one `PutItem`. There are 200 sequential calls after 5
warm-ups. The full table is in `artifacts/spikes/C-three-hop.md`, labelled "harness only".

| Store | p50 ms | p95 ms | p99 ms | max ms |
|---|---:|---:|---:|---:|
| DynamoDB Local (testcontainers) | 183.4 | 226.8 | 242.9 | 288.5 |
| moto (in-process) | 149.9 | 221.4 | 244.9 | 288.5 |

**Where it goes** (p50, separate 100-call runs, moto):
- The MCP layer plus GetItem/PutItem with a no-op carrier takes **61 ms**.
- The two parallel stub calls over in-process ASGI (FastAPI) take **107 ms**.
- A bare `GetItem` takes **1.3 ms** (DynamoDB Local) and **1.1 ms** (moto).

So the database is noise. The cost is per-request framework overhead in one Python process on a `/mnt/c`
checkout, which a networked deployment spreads across processes.

**DynamoDB Local warm-up.** The first `GetItem` on a new store took 31 ms here, because testcontainers had
already waited for the JVM's start-up log. On a cold `docker run` the first requests take seconds. The script
issues an uncounted `GetItem` before timing, and `scripts/latency.py` issues 5 uncounted calls. p95 is under
250 ms, so no further explanation is needed.

**Compared with Tower** (`artifacts/latency.md`, same harness, same machine): `line_is_ok` live p95 is
249.9 ms. Tower's real path costs about the same as this empty 3-hop skeleton. The gate has headroom in-process.
Real hops are deferred to `artifacts/latency-aws.md`.

**Through Gateway** (deferred):
`TODO(human): uv run python scripts/spikes/c_three_hop.py --gateway-url https://<gw>/mcp --sigv4 --region <r> --out artifacts/spikes/C-three-hop-gateway.md`
(this needs spike B's Gateway with the stub as its target).

**Verdict:** p95 227 ms in-process for the bare 3-hop shape, so the 400 ms gate is reachable; `components/02` §4's ≈ 300 ms budget stands. Updates: none (`components/02` §4 already says "measured, not assumed"; numbers are in `artifacts/`).
