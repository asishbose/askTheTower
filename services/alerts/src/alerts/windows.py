"""Pure window arithmetic over `last_state` + `now` (06 §2, §5). No I/O, no clock.

- **Continuous-false:** the unreachable clock (`last_state.unreachable_since`) starts at the first `false`
  observation and is cleared by any `true`. When the carrier reports when the status changed
  (`last_status_time`), the clock starts there instead — but never before our own last `true` observation.
- **UNREACHABLE_ALERT:** `transplant` 20 min, `care` 4 h (thresholds.yaml). `self` has no reachability alert.
- **Daytime window (care):** reachability alerts for `care` are released only 08:00–22:00 line-holder local
  time; a window that completes at night is released by the first evaluation after 08:00. Dark time at night
  still counts towards the 4 h. Fraud alerts (SIM swap, forwarding) have no quiet hours.
- **One alert per dark spell:** once `last_alert_at[UNREACHABLE]` is at or after `unreachable_since`, the
  spell has been reported.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from tower_consent import LastState
from tower_policy import ReasonCode, Thresholds

DAY_START = time(8, 0)
DAY_END = time(22, 0)


def next_unreachable_since(
    prev: LastState | None, reachable: bool, last_status_time: datetime | None, now: datetime
) -> datetime | None:
    """The new value of `last_state.unreachable_since` after one observation."""
    if reachable:
        return None
    if prev is not None and prev.reachable is False and prev.unreachable_since is not None:
        kept: datetime = prev.unreachable_since
        return kept
    start: datetime = now
    if last_status_time is not None and last_status_time <= now:
        start = last_status_time
    if prev is not None and prev.reachable is True and prev.at > start:
        start = prev.at  # we saw it reachable then; the dark spell cannot have started earlier
    return start


def unreachable_window(profile: str, thresholds: Thresholds) -> timedelta | None:
    windows: dict[str, timedelta] = {
        "transplant": thresholds.UNREACHABLE_ALERT.transplant,
        "care": thresholds.UNREACHABLE_ALERT.care,
    }
    return windows.get(profile)


def is_daytime(now: datetime, tz: str) -> bool:
    local = now.astimezone(ZoneInfo(tz)).time()
    return DAY_START <= local < DAY_END


def unreachable_due(
    profile: str,
    since: datetime | None,
    now: datetime,
    thresholds: Thresholds,
    tz: str,
    last_alert_at: datetime | None = None,
) -> bool:
    """True when the dark spell has lasted the profile's window, inside its delivery window, unreported."""
    window = unreachable_window(profile, thresholds)
    if window is None or since is None:
        return False
    if now - since < window:
        return False
    if profile == "care" and not is_daytime(now, tz):
        return False
    return not (last_alert_at is not None and last_alert_at >= since)


def rate_limited(state: LastState | None, code: ReasonCode, now: datetime, thresholds: Thresholds) -> bool:
    """06 §5: at most one alert per line per reason per RATE_LIMIT (6 h)."""
    if state is None:
        return False
    last = state.last_alert_at.get(code.value)
    return last is not None and now - last < thresholds.RATE_LIMIT


def swap_distrusted(sim_change_at: datetime | None, now: datetime, thresholds: Thresholds) -> bool:
    """ACK_DISTRUST: a line is untrusted for 24 h after its own SIM swap."""
    return sim_change_at is not None and timedelta(0) <= now - sim_change_at <= thresholds.ACK_DISTRUST
