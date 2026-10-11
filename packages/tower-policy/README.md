# tower-policy

The one package both Tower (02) and the Alerts service (06) import to decide. Carrier facts and
consent state go in; one outcome (`ok | changed | refuse`) and its reason codes come out. Same
inputs → same outcome, always. The model never decides whether a call is allowed or what a fact means.

Design: [`docs/architecture/components/03-policy-engine.md`](../../docs/architecture/components/03-policy-engine.md).

## What it does

- `evaluate_line(facts, consent, now, thresholds=None)` — `line_is_ok`: SIM swap and call forwarding.
- `evaluate_reachability(facts, consent, now, thresholds=None)` — `is_reachable`: the instant fact only.
- `phrase(outcome, facts, *, alias, tz, form)` — the sentence Alexa+ speaks (`voice`) or Alerts texts (`sms`).
- `load_thresholds(path=None)` — reads and validates `thresholds.yaml` (the packaged one when `path` is `None`).
- `policy_version()` — sha256 of `thresholds.yaml` + the templates; goes into every audit row.

Rules, in order (03 §3): not bound → `NOT_BOUND`; no grant or revoked → `NO_CONSENT`; carrier didn't
answer *either* call (swap or forwarding unknown) and the known fact doesn't alarm → `CARRIER_ERROR`
(a half answer is never `OK`; a known alarm is still `changed`, D4); facts older than `STALE` → `STALE_DATA`; then the facts. Consent is checked
before facts so an unconsented line never leaks whether a swap happened; carrier errors are checked
before staleness so an outage is reported as an outage.

## What it doesn't do

- No I/O (except reading the thresholds file), no clock (`now` is an argument), no randomness, no
  logging, no network. `tests/test_determinism.py` greps `src/` for all of it.
- No windows. `UNREACHABLE` is the instant fact; the 20-min / 4-h continuity, escalation, rate
  limiting and ack-distrust windows are applied by Alerts (06) using the values in `thresholds.yaml`.
- No decision about *who* is told, and no distinction between grant levels beyond "some grant / none":
  which tools a `watch` or `reachability` grant allows is Tower's (02) check.
- No phone numbers, no locations, no health words in any template (tests assert it with the shared
  regexes in `tests/privacy/patterns.py`).

## Public API

```python
from tower_policy import (
    Facts,
    ConsentView,
    Outcome,
    ReasonCode,
    Thresholds,
    evaluate_line,
    evaluate_reachability,
    load_thresholds,
    default_thresholds,
    phrase,
    policy_version,
)


class Facts(BaseModel):  # frozen; datetimes must be timezone-aware
    fetched_at: datetime  # required
    sim_swapped: bool | None = None  # None = carrier didn't answer
    latest_sim_change: datetime | None = None
    call_forwarding: Literal["none", "unconditional", "conditional", "unknown"] = "unknown"
    reachable: bool | None = None
    connectivity: Literal["DATA", "SMS", "NONE", "UNKNOWN"] = "UNKNOWN"
    last_status_time: datetime | None = None


class ConsentView(BaseModel):  # frozen
    bound: bool
    grant: Literal["owner", "watch", "reachability", "none"]
    revoked_at: datetime | None = None
    line_id: str | None = None  # carried for audit; never consulted by the rules


class Outcome(BaseModel):  # frozen
    kind: Literal["ok", "changed", "refuse"]
    reason_codes: list[ReasonCode]  # ok → [OK]; refuse → one code; changed → one or two


def evaluate_line(
    facts: Facts, consent: ConsentView, now: datetime, thresholds: Thresholds | None = None
) -> Outcome: ...
def evaluate_reachability(
    facts: Facts, consent: ConsentView, now: datetime, thresholds: Thresholds | None = None
) -> Outcome: ...
def phrase(
    outcome: Outcome,
    facts: Facts,
    *,
    alias: str | None,
    tz: str,
    form: Literal["voice", "sms"],
    now: datetime | None = None,
) -> str: ...
def load_thresholds(path: str | Path | None = None) -> Thresholds: ...  # raises ThresholdError
def policy_version() -> str: ...
```

`ReasonCode` has exactly twelve members: `OK`, `SIM_SWAPPED_RECENT`, `CALL_FORWARDING_SET`,
`UNREACHABLE`, `NOT_BOUND`, `NO_CONSENT`, `STALE_DATA`, `CARRIER_ERROR`, `SERVICE_UNAVAILABLE`, and the
audit-only `SUPPRESSED_REVOKED`, `ALERT_FAILED`, `ACK_IGNORED_SWAPPED_LINE` (written by Alerts, never
returned by the engine; `phrase()` renders them as `""`). `SERVICE_UNAVAILABLE` is a refusal the
*callers* raise (store or carrier client down); the engine itself never returns it.

`phrase()` details: `{time}` is `latest_sim_change` for `SIM_SWAPPED_RECENT`, `last_status_time` for
`UNREACHABLE`, and `fetched_at` otherwise; it renders as `2:14 today`, `yesterday at 9:30`,
`on Saturday at 9:30` or `on Sep 20 at 9:30` in the line-holder's zone (`tz`, IANA name). "Today" is
relative to `now`, which defaults to `facts.fetched_at` because this package has no clock. `alias` is
the name chosen at consent time (`{Name}`); `None` renders as "That person". A `changed` outcome with
two codes joins the two sentences with a space; every SMS form, including the two-code join, fits in 160 characters.

## Thresholds

`src/tower_policy/thresholds.yaml` — data, not code. Durations are `<number><s|m|h|d>`.

| Name | Value | Used by |
|---|---|---|
| `SWAP_WINDOW` | 72h | engine — a SIM change inside this window is `SIM_SWAPPED_RECENT` |
| `STALE` | 10m | engine — older facts are `STALE_DATA` |
| `FRESH` | 10m | Tower — a result younger than this may be reused without re-fetching |
| `UNREACHABLE_ALERT` | transplant 20m, care 4h | Alerts — continuous-unreachable window per profile |
| `ESCALATE_NEXT` | 15m | Alerts — no ack within this → next in the chain |
| `RATE_LIMIT` | 6h | Alerts — one alert per line per reason per window |
| `ACK_DISTRUST` | 24h | Alerts — replies from a line are ignored this long after its own SIM swap |

The loader raises `ThresholdError` for a missing file, a malformed value, an unknown or missing key,
`SWAP_WINDOW > 2400h` (the SIM Swap API's `maxAge` cap) or `STALE > 1h`.

## Run

```sh
uv run pytest packages/tower-policy -q      # 82 tests, < 2 s
uv run mypy --strict packages/tower-policy
make policy-table                            # → artifacts/policy-table.md (960 rows, grouped by outcome)
```

## How to showcase

`make policy-table` and open `artifacts/policy-table.md`: the summary counts, then the leak-prevention
row (`bound=False, sim_swapped=true (inside)` → `NOT_BOUND`, the swap never mentioned), then every row
grouped by outcome. That file is every decision the system can make, readable without running anything.
