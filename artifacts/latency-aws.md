# Tower latency — measured against https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-2%3A471507663540%3Aruntime%2Fatt_dev_tower-Rytnjj93AV/invocations?qualifier=DEFAULT

> **measured against https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-2%3A471507663540%3Aruntime%2Fatt_dev_tower-Rytnjj93AV/invocations?qualifier=DEFAULT.** Measured against a running Tower over the network.

- **date:** 2026-10-14 20:30 UTC
- **machine:** Linux 6.18.54.1-microsoft-standard-WSL2 x86_64, 24 CPUs, Python 3.12.9
- **target:** https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-2%3A471507663540%3Aruntime%2Fatt_dev_tower-Rytnjj93AV/invocations?qualifier=DEFAULT
- **store:** as deployed
- **mock:** mock-carrier 0.1.0 (scenario demo.yaml, clock 2026-10-05T14:00Z)
- **fastmcp:** 4.0.11
- **calls:** 200 per path, sequential, after 5 uncounted warm-up calls; client-side wall time per JSON-RPC `tools/call` POST

| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate (p95 < 400 ms) |
|---|---:|---:|---:|---:|---:|---|
| line_is_ok(self) — source carrier | 200 | 429.6 | 528.4 | 567.5 | 617.9 | **FAIL** |
| line_is_ok(mom) — source carrier | 195 | 404.4 | 492.2 | 557.9 | 9797.9 | **FAIL** |
| is_reachable(mom) — source carrier | 195 | 310.9 | 385.1 | 453.6 | 10010.3 | pass |
| watch_line(self) — source None | 200 | 297.5 | 359.6 | 380.7 | 392.1 | reported (not gated) |

- *line_is_ok(mom) — source carrier*: 5 call(s) failed and are not in the samples: TowerError: cannot connect to Tower at https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-2%3A471507663540%3Aruntime%2Fatt_dev_tower-Rytnjj93AV/invocations?qualifier=DEFAULT: MCPError; TowerError: line_is_ok: MCPError
- *is_reachable(mom) — source carrier*: 5 call(s) failed and are not in the samples: TowerError: cannot connect to Tower at https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-2%3A471507663540%3Aruntime%2Fatt_dev_tower-Rytnjj93AV/invocations?qualifier=DEFAULT: MCPError; TowerError: is_reachable: MCPError

Where the time goes: run Tower with `LOG_LEVEL=debug`; each call logs per-step timings (`resolve`, `load`, `carrier`, `policy`, `audit`) with `line_id` only.
