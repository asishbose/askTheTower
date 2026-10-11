"""Page → Alerts: re-subscribe a line after its watch settings change (04 §9.2, 06 §1).

Same call Tower's `watch_line` makes: `POST {ALERTS_INTERNAL_URL}/internal/watch {line_id, watcher_user_id,
enable, profile}`, bearer-protected. Only made when the line-holder's Watch is enabled; a failure is logged by
the caller and swallowed (the profile's poll schedule finds the Watch). Built once in `app.deps_from_env`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from tower_consent.models import Profile

log = logging.getLogger("binding_page.alerts")


class AlertsWatch(Protocol):
    def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None: ...


@dataclass
class RecordingAlerts:
    """Stand-in when `ALERTS_INTERNAL_URL` is unset (and in tests): records and logs. `fail` simulates an outage."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None:
        if self.fail:
            raise httpx.ConnectError("alerts unavailable (test)")
        self.calls.append(
            {"line_id": line_id, "watcher_user_id": watcher_user_id, "enable": enable, "profile": profile}
        )
        log.info("alerts stub: watch line_id=%s enable=%s profile=%s", line_id, enable, profile)


class HttpAlerts:
    """The real call. 2 s timeout: a person is waiting on the page, and the polls cover a miss."""

    def __init__(self, base_url: str, bearer: str | None, *, transport: httpx.BaseTransport | None = None):
        self.url = base_url.rstrip("/") + "/internal/watch"
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self._http = httpx.Client(transport=transport, timeout=2.0, headers=headers)

    def __repr__(self) -> str:
        return f"HttpAlerts(url={self.url!r})"

    def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None:
        r = self._http.post(
            self.url,
            json={
                "line_id": line_id,
                "watcher_user_id": watcher_user_id,
                "enable": enable,
                "profile": profile,
            },
        )
        r.raise_for_status()

    def close(self) -> None:
        self._http.close()
