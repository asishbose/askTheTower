"""Integration layer (`-m integration`): component pairs over their real interfaces.

One source of truth per test (prompt 18 guardrail): the pairwise tests stay in the package/service that owns them
and carry the `integration` marker; this folder *links* them by kind and holds the cross-cutting gates.

- Tower ↔ consent (DynamoDB Local + moto)      services/tower-mcp/tests/test_server.py, test_paths.py, test_audit_first.py
- Tower ↔ CarrierClient ↔ mock                 services/tower-mcp/tests/test_envelope.py, test_failures.py
- Alerts ↔ mock subscriptions                  services/alerts/tests/test_handler.py, test_escalation.py, test_hooks.py
- binding page ↔ mock auth-code                services/binding-page/tests/test_bind_flow.py, test_playwright.py
- audit writer ↔ DynamoDB Local                packages/tower-audit/tests/test_append_release.py, test_chain.py
- conformance/   schemathesis vs vendored specs; fixtures through DirectClient and the fake Gateway (+ report gate)
- privacy/       log / result / SMS / table greps (+ the registry of privacy tests)
- latency/       200 calls per tool, p95 gate (+ the artefact gate)
"""
