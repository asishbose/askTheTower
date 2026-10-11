"""08 §3 + D-G: on AWS the Fargate mock runs with `MOCK_ASSUME_MOBILE_DATA=1`, so the binding page in AWS mode
binds with no `X-Mock-Client-Id` anywhere; the page's own code path is unchanged."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mock_carrier.app import create_app as create_mock
from mock_carrier.settings import Settings as MockSettings
from mock_carrier.testing import ASISH, BASE
from tower_consent import list_lines

pytestmark = pytest.mark.integration


@pytest.fixture
def mock_app() -> Any:
    return create_mock(
        MockSettings(admin=True, base_url=BASE, webhook_backoff_s=0.0, assume_mobile_data=True)
    )


async def test_aws_mode_binds_without_any_simulation_header(make_page: Any) -> None:
    page = make_page(tower_env="aws")
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.post(f"/bind/{token}/verify", follow_redirects=False)
    async with httpx.AsyncClient(transport=page.carrier_transport.inner) as phone:
        back = await phone.get(r.headers["location"])  # the phone's own browser: no header
    callback = httpx.URL(back.headers["location"])
    r = await browser.get(f"/bind/callback?{callback.query.decode()}")
    assert r.status_code == 200 and "Line connected" in r.text
    lines = list_lines(page.store, "user-asish")
    assert len(lines) == 1 and page.deps.cipher.decrypt(lines[0].msisdn_enc) == ASISH
    sent = [q for q in page.carrier_transport.requests if q.url.path == "/oauth2/authorize"]
    assert all("X-Mock-Client-Id" not in q.headers for q in sent)
    await browser.aclose()
