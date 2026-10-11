"""Fixtures for the mock-carrier tests: an in-process app (httpx.ASGITransport) and a sink that
records CloudEvents inside the test process. Helpers live in `mock_carrier.testing`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

import httpx
import pytest
from mock_carrier.app import create_app
from mock_carrier.settings import Settings
from mock_carrier.testing import BASE, Sink


@pytest.fixture
def settings() -> Settings:
    return Settings(admin=True, ciba=True, base_url=BASE, webhook_backoff_s=0.0, fault_timeout_s=0.5)


@pytest.fixture
def sink() -> Sink:
    return Sink()


@pytest.fixture
def make_client(settings: Settings, sink: Sink) -> Callable[..., httpx.AsyncClient]:
    def _make(**overrides: Any) -> httpx.AsyncClient:
        app = create_app(replace(settings, **overrides), sink_transport=sink.transport)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)

    return _make


@pytest.fixture
async def client(make_client: Callable[..., httpx.AsyncClient]) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c
