# 03 — Policy Engine

**Role:** turn carrier facts and consent state into one outcome, deterministically.
**Runs on:** inside Tower (02) and inside the Alerts service (06) — the same package, so both paths decide identically.
**Owns:** reason codes, thresholds, and the phrasing templates keyed on them.

---

## 1. The property

```python
evaluate_line(facts: Facts, consent: ConsentView, now: datetime) -> Outcome
evaluate_reachability(facts: Facts, consent: ConsentView, now: datetime) -> Outcome
```

Pure: no I/O, no clock read inside, no randomness. Same inputs → same outcome, always. This is why the whole consent and safety story is testable as a table and why the deck can say "the model never decides".

## 2. Inputs

```python
class Facts(TypedDict):
    sim_swapped: bool | None  # None = carrier didn't answer
    latest_sim_change: datetime | None
    call_forwarding: Literal["none", "unconditional", "conditional", "unknown"]
    reachable: bool | None
    connectivity: Literal["DATA", "SMS", "NONE", "UNKNOWN"]
    last_status_time: datetime | None
    fetched_at: datetime


class ConsentView(TypedDict):
    bound: bool  # the line is bound to a verified number
    grant: Literal["owner", "watch", "reachability", "none"]
    revoked_at: datetime | None
```

## 3. Rules (in order)

```python
def evaluate_line(f, c, now):
    if not c.bound:
        return refuse("NOT_BOUND")
    if c.grant == "none" or c.revoked_at:
        return refuse("NO_CONSENT")
    codes = []
    if f.sim_swapped and within(f.latest_sim_change, now, SWAP_WINDOW):
        codes.append("SIM_SWAPPED_RECENT")
    if f.call_forwarding == "unconditional":
        codes.append("CALL_FORWARDING_SET")
    if (f.sim_swapped is None or f.call_forwarding == "unknown") and not codes:
        return refuse("CARRIER_ERROR")
    if (now - f.fetched_at) > STALE:
        return refuse("STALE_DATA")
    return ok() if not codes else changed(codes)


def evaluate_reachability(f, c, now):
    # same consent gates as above; the carrier-error gate for this tool is `reachable is None`,
    # and it comes before staleness for the same reason as in evaluate_line
    if not c.bound:
        return refuse("NOT_BOUND")
    if c.grant == "none" or c.revoked_at:
        return refuse("NO_CONSENT")
    if f.reachable is None:
        return refuse("CARRIER_ERROR")
    if (now - f.fetched_at) > STALE:
        return refuse("STALE_DATA")
    return ok() if f.reachable else changed(["UNREACHABLE"])
```

**One unknown fact is enough to refuse.** If *either* call went unanswered (`sim_swapped is None` or `call_forwarding == "unknown"`), the answer is `CARRIER_ERROR`, never `OK`: a half answer is not "your line is as it was", and `sim_swapped` is the fraud signal. The one exception never hides an alarm: if the fact that *is* known alarms (forwarding `unconditional`, or a swap inside the window), the outcome is `changed` with that code — e.g. swap unknown + forwarding unconditional → `changed · CALL_FORWARDING_SET`. The swap stays unknown in the result (`sim_swapped_recently: null`), never "no". Tower turns a `CARRIER_ERROR` caused by a *timeout* into `STALE_DATA` with the Watch's last-known facts when a Watch exists (02 §6); the engine itself never sees a Watch. (Was: refuse only when *both* facts were unknown — code-vs-docs D4.)

`within(latest_sim_change, now, SWAP_WINDOW)` treats `latest_sim_change = None` as inside the window: the SIM Swap API may say "swapped" without a date, and the safe reading of "swapped, date unknown" is "recent". `evaluate_reachability` does not consult `sim_swapped` / `call_forwarding` at all — `is_reachable` may be served from the Device Status call alone.

The *window* for `UNREACHABLE` (20 min / 4 h continuous) is applied by the Alerts service (06) over successive outcomes; the engine reports the instant fact.

Order matters for the same reason it did in the previous design: consent before facts, so an unbound or unconsented line never leaks whether a swap happened; carrier errors before staleness, so an outage is reported as an outage.

### Thresholds

| Name | Value | Why |
|---|---|---|
| `SWAP_WINDOW` | 72 h | long enough to catch a swap over a weekend; the SIM Swap API itself caps `maxAge` at 2400 h |
| `STALE` | 10 min | a result older than this is not a fact, it's a memory |
| `UNREACHABLE_ALERT` (06) | 20 min for `transplant` profile; 4 h for `care` profile | matches the care slides; profile chosen by the line-holder on the binding page (04 §9), or `care` for a grantee's own watch |
| `ESCALATE_NEXT` (06) | 15 min without acknowledgement | the second person in the chain |
| `ACK_DISTRUST` (06) | 24 h after a line's own SIM swap | a reply from that number may be the attacker's |

Thresholds are data (`policy/thresholds.yaml`), not code, but the engine refuses to load a file that widens `SWAP_WINDOW` past the API's maximum or sets `STALE` above an hour.

## 4. Outcomes and reason codes

| Code | Outcome | Spoken template (Tower `phrasing.py`) |
|---|---|---|
| `OK` | ok | "Your line is as it was." |
| `SIM_SWAPPED_RECENT` | changed | "Your SIM was moved to another device at {time}. If that wasn't you, call your carrier now." |
| `CALL_FORWARDING_SET` | changed | "All your calls have been forwarding since {time}. If you didn't set that, call your carrier." |
| `UNREACHABLE` | changed | "{Name}'s phone has been off the network since {time} — that's the network, nothing more." |
| `NOT_BOUND` | refuse | "I need to connect your line first — I'll send you a link." |
| `NO_CONSENT` | refuse | "{Name} hasn't shared that with you." |
| `STALE_DATA` | refuse | "I can't reach your carrier right now. The last I heard was at {time}." — neutral on purpose: the last-known state may itself show a swap or forwarding, so the sentence says *when*, never *what* (the result's facts carry the state, `stale=true`). SMS: "Can't reach your carrier right now. Last heard at {time}." (Was "…your line was fine." whatever the state — D5.) |
| `CARRIER_ERROR` | refuse | "I can't reach your carrier right now. Try again in a minute." |
| `SERVICE_UNAVAILABLE` | refuse | "Something on my side isn't available. Try again in a minute." |
| `SUPPRESSED_REVOKED` | audit only (06) | — no message; the grant was pulled before the alert went out |
| `ALERT_FAILED` | audit only (06) | — no message; delivery failed after retry |
| `ACK_IGNORED_SWAPPED_LINE` | audit only (06) | — an acknowledgement or cancel arrived from a line within 24 h of its own SIM swap; ignored, escalation continues, the watcher is told it was ignored |

Templates never include a phone number or a health word (`unwell` included — the privacy regex in `tests/privacy/patterns.py` bans it, so the slide's "unreachable, not unwell" is the deck's line, not the template's). "{Name}" is the alias the user chose at consent time. `{time}` is `latest_sim_change` for `SIM_SWAPPED_RECENT`, `last_status_time` for `UNREACHABLE`, and `fetched_at` otherwise (the Call Forwarding API reports no start time, so "since {time}" is when the forwarding was observed).

## 5. What the engine never does

- Call anything.
- Consult a model.
- Return a location, a position, or a free-text carrier message.
- Decide *who* gets alerted — that's a Watch record (04) read by the Alerts service (06). The engine says *whether*, not *to whom*.
- Trust an acknowledgement. Whether an "OK" or "cancel" counts is decided in Alerts (06 §3) by the rule: nothing from a line counts for 24 h after that line's own SIM swap.

## 6. Tests — the table

| Dimension | Values |
|---|---|
| bound | true, false |
| grant | owner, watch, reachability, none, revoked |
| sim_swapped | true (inside window), true (outside), false, None |
| call_forwarding | none, unconditional, conditional, unknown |
| reachable | true, false, None |
| fetched_at | fresh, stale |

Full cross-product is a few hundred cases and runs in under a second. Plus named cases: thresholds file that exceeds API max is rejected; consent checked before facts (a NOT_BOUND line with a swap returns NOT_BOUND, never the swap); one unknown fact with no known alarm is `CARRIER_ERROR`, never `OK`, and a known alarm next to an unknown fact is still `changed` (D4); every `STALE_DATA` row phrases the same neutral sentence whatever its last-known facts held (D5, `test_d5_stale_phrasing.py`; the policy table prints it in its "STALE_DATA wording" section); template output contains no digits that look like a phone number (regex assertion).

## 7. Showcase on its own

`make policy-table` prints the decision table as markdown — the same artefact goes in the README and is what a judge can read without running anything. See `testing-and-showcase.md` §2.3.
