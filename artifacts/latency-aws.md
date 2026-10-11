# Tower latency — AWS (AgentCore Runtime → Gateway → mock on Fargate)

> **NOT MEASURED.** The autonomous build had no AWS credentials, so nothing was deployed. The only measured
> numbers so far are in `artifacts/latency.md`, labelled "in-process, not representative" (p95: 249.9 ms for
> `line_is_ok` live, 173.3 ms for `is_reachable` live). Spike C's question, "does p95 stay under 400 ms with
> real hops", is still open.

TODO(human), after `make deploy ENV=aws`:

```bash
export TOWER_JWT=<a bearer from the issuer configured as tower_jwt_discovery_url>
make latency-aws                      # = ENV=aws uv run python scripts/latency.py --out artifacts/latency-aws.md
uv run pytest tests/aws -m nightly    # conformance through Gateway + this latency run
```

`scripts/latency.py --url` overwrites this file with the same table as `latency.md`: 200 calls per path, and
p50/p95/p99/max per tool. If p95 is over 400 ms, add the per-hop breakdown (prompt 13 acceptance) from the
CloudWatch dashboard (`<name>-tower`): the Runtime invocation latency, the Gateway tool spans in `aws/spans`,
and the mock's ALB `TargetResponseTime`. Then update the deck's "measured not assumed" note to the real number.

Hops on this path, for the breakdown:

| Hop | Where it shows |
|---|---|
| Alexa+ / client → Runtime (JWT authorizer) | Runtime `Latency` metric minus Tower's span duration |
| Tower: consent read (DynamoDB) + KMS Decrypt of `msisdn_enc` | Tower tool span |
| Tower → Gateway (SigV4, MCP `tools/call`, persistent session) | Tower client span for `<api>___<op>` |
| Gateway → Identity token (cached) → mock ALB (managed VPC resource) | Gateway target span |
| mock (Fargate, 0.25 vCPU arm64) | ALB `TargetResponseTime` |
