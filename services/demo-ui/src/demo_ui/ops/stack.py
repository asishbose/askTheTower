"""Is the local stack up? (doc 11 §2 "Pane 5", Buttons): the mock, the binding page and Alerts each answer
`GET /healthz` 200 within 2 s, checked when the "Run here" column renders. Unauthenticated, concurrent."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx

TIMEOUT_S = 2.0


class StackProbe:
    def __init__(
        self, http: httpx.AsyncClient, urls: Mapping[str, str], *, timeout_s: float = TIMEOUT_S
    ) -> None:
        self.http = http
        self.urls = {name: url for name, url in urls.items() if url}
        self.timeout_s = timeout_s

    async def down(self) -> list[str]:
        """The services that did not answer 200 (empty: the stack is up)."""
        names = list(self.urls)
        ups = await asyncio.gather(*(self._up(self.urls[n]) for n in names))
        return [n for n, up in zip(names, ups, strict=True) if not up]

    async def _up(self, base: str) -> bool:
        try:
            r = await self.http.get(f"{base.rstrip('/')}/healthz", timeout=self.timeout_s)
        except (httpx.HTTPError, ValueError):
            return False
        return r.status_code == 200
