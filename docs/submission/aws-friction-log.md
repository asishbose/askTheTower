# AWS friction log and feature requests — Build, Ship, Shape submission

What we hit while building Ask the Tower on AWS between 2026-10-11 and 2026-10-22, in the form the AWS Builder
mini-challenge asks for: the task, the steps, expected versus actual, a severity, the workaround we shipped, and a
suggestion. Every entry is reproducible from this repository; the build logs under `docs/submission/build-log/`
and the Ops transcripts under `artifacts/ops-log/` hold the raw evidence.

Severity: **High** blocked the demo until worked around · **Medium** cost a day or recurs on every run ·
**Low** cost an hour.

---

## Friction log

### 1. MCP calls to AgentCore Runtime took 7–8 s each (2026-10-14)

- **Task.** Run the demo's three scenarios against Tower on AgentCore Runtime.
- **Steps.** `make demo ENV=aws` (`ref-client demo --env aws`), then `scripts/latency.py` at 200 calls per path.
- **Expected.** A few hundred milliseconds per call, as locally.
- **Actual.** 7–8 s per call. The runtime log group showed a container start for every request.
- **Severity.** High.
- **Workaround.** One `Mcp-Session-Id` / `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` per process
  (`services/ref-client/src/ref_client/mcp_client.py`): ~400 ms per call. We then found that a session is
  retired after roughly 200 calls and after an idle stretch, and that the next call on it fails with a generic
  MCP error; clients now reconnect on a fresh session id (`TowerClient.call`, `scripts/latency.py`).
- **Suggestion.** State in the Runtime MCP documentation that a request without the session header lands on a
  cold microVM; document the session limits (calls, idle time); answer a retired session with a distinguishable
  error so clients can reconnect deliberately.

### 2. Gateway refuses a 3LO outbound credential with the IAM authorizer (2026-10-19)

- **Task.** Register CAMARA Number Verification as an AgentCore Gateway target.
- **Steps.** `terraform apply` with an OAuth2 authorization-code credential provider on a Gateway whose inbound
  authorizer is `AWS_IAM`.
- **Expected.** The target registers; Tower proves line possession through the Gateway like the other APIs.
- **Actual.** `3LO Auth is not supported when gateway authorizer type is AWS_IAM`.
- **Severity.** High.
- **Workaround.** `binding_carrier_client = "direct"`: the binding page calls that one API directly (a NAT
  gateway was added for its egress); the other four CAMARA APIs stay behind the Gateway.
- **Suggestion.** Support authorization-code targets with IAM inbound auth, or state the restriction on the
  credential-provider page and in the Terraform provider's resource documentation.

### 3. The first Gateway call after a cold start answers 400 (2026-10-20 → 22)

- **Task.** The first question after Tower starts.
- **Steps.** Tower warms up with `tools/list` on a persistent Gateway session; the first `tools/call` arrives
  about two seconds later.
- **Expected.** A normal answer.
- **Actual.** HTTP 400 from the Gateway on that one call, on every cold start; the next call succeeds. Tower's
  log: `/aws/bedrock-agentcore/runtimes/att_dev_tower-*`, 2026-10-21 18:32:07Z.
- **Severity.** Medium: every demo opened with "I can't reach your carrier right now".
- **Workaround.** Tower makes one throwaway call during warm-up (`tower_mcp/deps.py::_prime_gateway`).
- **Suggestion.** Fetch the outbound credential when the session initialises, or document that the first call
  pays for it.

### 4. Nova Micro model id in us-east-2 (2026-10-20)

- **Task.** Call Nova Micro from a laptop in the stack's region.
- **Steps.** `bedrock-runtime converse` with `amazon.nova-micro-v1:0`.
- **Expected.** An answer.
- **Actual.** `ValidationException`; the model is reachable only through the cross-region inference profile
  `us.amazon.nova-micro-v1:0`.
- **Severity.** Low.
- **Workaround.** The `us.` profile id everywhere (`BEDROCK_MODEL_ID`, Terraform default).
- **Suggestion.** Let the error name the inference profile to use.

### 5. A new account's Bedrock daily token quota is 0 (2026-10-13)

- **Task.** Run the model-driven agent from a freshly created account.
- **Steps.** Converse from the account after enabling model access; every limit in the console looked normal.
- **Actual.** Throttled: a 0 tokens-per-day quota on new accounts.
- **Severity.** Medium.
- **Workaround.** A cross-account role in an older account, assumed by the runtime role and the developer user
  (`BEDROCK_ASSUME_ROLE_ARN`; `ref_client/agent.py::bedrock_client`).
- **Suggestion.** Show the daily token quota next to model access in the console, with the request-increase link.

### 6. ECS Exec through a corporate proxy (2026-10-16)

- **Task.** Seed the mock carrier on Fargate with `aws ecs execute-command`.
- **Steps.** From a laptop behind a TLS-inspecting proxy.
- **Expected.** A shell in the task.
- **Actual.** `Cannot perform start session: EOF`: the Session Manager WebSocket is cut by the proxy.
- **Severity.** Low.
- **Workaround.** The task loads its scenario on boot (`MOCK_SCENARIO=demo`); an admin route behind API Gateway
  does the rest.
- **Suggestion.** A clearer error naming the WebSocket endpoint that must be reachable.

### 7. Local Terraform state and two operators (2026-10-20)

- **Task.** Two team members deploying to one account.
- **Steps.** Each ran the pipeline with local state (the account's organisation policies blocked an S3 backend).
- **Expected.** Terraform detects the existing stack.
- **Actual.** The second apply created about 60 resources and then failed on about 25 "already exists" errors,
  leaving orphans neither state knew.
- **Severity.** Medium.
- **Workaround.** One operator per account; the pipeline now tags images uniquely per push so a deploy always
  rolls the runtime, the Lambdas and the ECS task (`mk/vars.mk`).
- **Suggestion.** Not an AWS defect. The AgentCore samples could ship a remote-state layout, since the Runtime and
  Gateway resources are the ones nobody wants to recreate.

---

## Feature requests

### 1. Authorization-code (3LO) targets on AgentCore Gateway with the IAM authorizer — Critical

CAMARA Number Verification proves possession of a line by sending the user's phone through the carrier's
authorization-code flow. A Gateway with `AWS_IAM` inbound auth refuses a 3LO outbound credential (friction 2), so
one CAMARA API is called directly while four go through the Gateway. Supporting authorization-code targets, with
the MCP `meta` carrying the code and redirect URI, would put every carrier call behind one Gateway and remove the
project's only direct carrier dependency.

### 2. A documented session lifecycle for AgentCore Runtime (MCP) — Important

A call without a session header lands on a fresh microVM (7–8 s); with one, ~400 ms. Sessions are retired after
an idle period and after roughly 200 calls, and a stale session answers with a generic MCP error (friction 1).
Documenting the limits and returning a specific "session retired" error would let clients reconnect deliberately
instead of by measurement.

### 3. First-call warm-up for Gateway targets with outbound OAuth — Nice-to-have

The first `tools/call` after a Gateway client connects fails with a 400 while the outbound credential is fetched;
the next call works (friction 3). Fetching the credential at session initialise, or retrying the first call on
the Gateway side, would remove the warm-up call every client currently needs.
