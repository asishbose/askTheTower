"""The one tap (04 §2, §7): link → page → Verify → carrier → "Line connected"; Wi-Fi on → refused, nothing stored."""

from __future__ import annotations

import re
from typing import Any

import pytest
from mock_carrier.testing import ASISH
from tower_consent import list_lines, resolve

pytestmark = pytest.mark.integration

FREE_TEXT = re.compile(r"<input(?![^>]*type=\"hidden\")[^>]*>|<textarea|<select", re.I)


async def test_right_client_id_binds_the_line(page: Any, h: Any) -> None:
    browser, r = await h.bind(page, "user-asish", "phone-asish")
    assert r.status_code == 200
    assert "Line connected" in r.text
    assert "•••• 0101" in r.text  # masked last four, the only form of the number the page ever shows
    assert ASISH not in r.text and ASISH[1:] not in r.text
    lines = list_lines(page.store, "user-asish")
    assert len(lines) == 1 and lines[0].binding_method == "auth_code"
    assert page.deps.cipher.decrypt(lines[0].msisdn_enc) == ASISH
    assert resolve(page.store, "user-asish", "self").view.bound is True
    # the flow went through the simulated header, server-side, exactly once
    sent = [q for q in page.carrier_transport.requests if q.url.path == "/oauth2/authorize"]
    assert [q.headers.get("X-Mock-Client-Id") for q in sent] == ["phone-asish"]
    # a page session now exists
    assert (await browser.get("/me")).status_code == 200
    await browser.aclose()


async def test_wifi_on_is_refused_and_nothing_is_stored(page: Any, h: Any) -> None:
    before = h.table_counts(page.store)
    browser, r = await h.bind(page, "user-asish", None)  # no client id: the "Wi-Fi on" simulation
    assert r.status_code == 403
    assert "mobile data" in r.text and "Wi-Fi off" in r.text
    after = h.table_counts(page.store)
    assert after == {**before, "BindTokens": 1}  # no line, no user — only the unspent token, to retry with
    assert list_lines(page.store, "user-asish") == []
    assert resolve(page.store, "user-asish", "self").view.bound is False
    assert (await browser.get("/me")).status_code == 401  # no session either
    await browser.aclose()


async def test_wifi_on_then_off_retries_with_the_same_link(page: Any) -> None:
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.post(f"/bind/{token}/verify")
    assert r.status_code == 403
    r = await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"})
    assert r.status_code == 200 and "Line connected" in r.text
    await browser.aclose()


async def test_another_lines_device_binds_that_line_not_a_typed_one(page: Any, h: Any) -> None:
    """The carrier asserts the number: Mom's phone binds Mom's line, whoever's link it is."""
    browser, r = await h.bind(page, "user-mom", "phone-mom")
    assert r.status_code == 200 and "•••• 0102" in r.text
    await browser.aclose()


async def test_line_bound_to_someone_else_is_refused(page: Any, h: Any) -> None:
    b1, r = await h.bind(page, "user-asish", "phone-asish")
    assert r.status_code == 200
    b2, r = await h.bind(page, "user-intruder", "phone-asish")
    assert r.status_code == 409 and "another account" in r.text
    assert list_lines(page.store, "user-intruder") == []
    await b1.aclose()
    await b2.aclose()


async def test_bind_path_has_nothing_to_type(page: Any) -> None:
    """Acceptance: no <input type="tel">, no free-text input anywhere on the bind path."""
    browser = page.browser()
    token = page.bind_token("user-asish")
    pages = [await browser.get(f"/bind/{token}?as=phone-asish"), await browser.get(f"/bind/{token}")]
    pages.append(await browser.post(f"/bind/{token}/verify"))  # refused page
    pages.append(await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"}))  # connected page
    pages.append(await browser.get(f"/bind/{token}"))  # expired page
    assert [p.status_code for p in pages] == [200, 200, 403, 200, 404]
    for p in pages:
        assert 'type="tel"' not in p.text
        assert not FREE_TEXT.search(p.text), FREE_TEXT.search(p.text)
    await browser.aclose()


async def test_malformed_simulation_param_is_rejected_locally(page: Any) -> None:
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.get(f"/bind/{token}?as=%2B16135550101")
    assert r.status_code == 400
    r = await browser.post(f"/bind/{token}/verify", data={"as": "Phone Asish!"})
    assert r.status_code == 400
    assert not [q for q in page.carrier_transport.requests if q.headers.get("X-Mock-Client-Id")]
    await browser.aclose()


async def test_on_aws_the_simulation_param_is_ignored(make_page: Any, h: Any) -> None:
    """`mobile_data` is inert outside TOWER_ENV=local: `?as=` is neither used nor echoed, no header is ever
    sent, the browser goes to the carrier itself — and without real network attribution the carrier refuses."""
    page = make_page(tower_env="aws")
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.get(f"/bind/{token}?as=phone-asish")
    assert r.status_code == 200 and "phone-asish" not in r.text and 'name="as"' not in r.text
    r = await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"}, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("http://mock.test/oauth2/authorize?")
    assert page.carrier_transport.requests == []  # the page did not call the carrier on the phone's behalf
    # The phone's browser follows the redirect itself (no header: a browser can't add one).
    import httpx

    async with httpx.AsyncClient(transport=page.carrier_transport.inner) as phone_to_carrier:
        back = await phone_to_carrier.get(location)
    assert back.status_code == 302
    callback = httpx.URL(back.headers["location"])
    r = await browser.get(f"/bind/callback?{callback.query.decode()}")
    assert r.status_code == 403
    assert not [q for q in page.carrier_transport.requests if q.headers.get("X-Mock-Client-Id")]
    assert list_lines(page.store, "user-asish") == []
    await browser.aclose()


async def test_aws_mode_with_network_attribution_binds(make_page: Any) -> None:
    """Same AWS-mode page; the 'network' (here: the mock's header, added by the carrier side, not the page)
    attributes the phone — the page code path is unchanged and the line binds."""
    import httpx

    page = make_page(tower_env="aws")
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.post(f"/bind/{token}/verify", follow_redirects=False)
    async with httpx.AsyncClient(transport=page.carrier_transport.inner) as network:
        back = await network.get(r.headers["location"], headers={"X-Mock-Client-Id": "phone-asish"})
    callback = httpx.URL(back.headers["location"])
    r = await browser.get(f"/bind/callback?{callback.query.decode()}")
    assert r.status_code == 200 and "Line connected" in r.text
    await browser.aclose()


async def test_healthz(page: Any) -> None:
    async with page.browser() as b:
        r = await b.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


async def test_aws_shape_through_the_gateway_client(make_page: Any) -> None:
    """On AWS the code exchange and Number Verification go through AgentCore Gateway + Identity
    (`GatewayClient`; here the in-process `FakeGateway` from camara_client.testing). Same page code."""
    import httpx
    from camara_client import BreakerRegistry, GatewayClient
    from camara_client.testing import FakeGateway, mock_config
    from mock_carrier.testing import BASE

    page = make_page(tower_env="aws")
    fake = FakeGateway(base_url=BASE, transport=page.carrier_transport.inner)
    gw = GatewayClient(
        mock_config(BASE, client="gateway", profile="proactive", client_id="binding-page"),
        client_factory=fake.client,
        breakers=BreakerRegistry(),
    )
    assert await gw.check_tools() == []  # warm-up, as the service does at start
    page.deps.carrier = gw
    browser = page.browser()
    r = await browser.post(f"/bind/{page.bind_token('user-asish')}/verify", follow_redirects=False)
    async with httpx.AsyncClient(transport=page.carrier_transport.inner) as network:
        back = await network.get(r.headers["location"], headers={"X-Mock-Client-Id": "phone-asish"})
    r = await browser.get(f"/bind/callback?{httpx.URL(back.headers['location']).query.decode()}")
    assert r.status_code == 200 and "•••• 0101" in r.text
    assert any("number-verification" in c for c in fake.calls)
    assert len(list_lines(page.store, "user-asish")) == 1
    await browser.aclose()
    await fake.aclose()
