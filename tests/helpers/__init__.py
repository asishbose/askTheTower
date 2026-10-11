"""Helpers shared by every test layer (prompt 18).

- `tests.helpers.patterns`     — the privacy regexes (E.164, health words, keys, bearers). They are *defined* in
                                 `tests/privacy/patterns.py` — the one module every privacy test and script
                                 imports (agent brief) — and re-exported here; `tests/unit/test_privacy_single_source.py`
                                 asserts no other file defines its own.
- `tests.helpers.transcripts`  — the transcript comparator (tool calls + reason codes; wording and timestamps
                                 ignored) and the golden set in `tests/e2e/golden/`.
- `tests.helpers.markers`      — the four layer markers and the "exactly one per test" rule the root conftest enforces.
- `tests.helpers.dynamo`       — DynamoDB backends for tests (moto / DynamoDB Local via testcontainers).
- `tests.helpers.env`          — ENV=local|eks|aws → endpoints; `MockAdmin`, the mock carrier's admin API.
"""
