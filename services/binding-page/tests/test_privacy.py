"""Every page, for the seeded state, holds no phone number — only the masked "•••• 0101" on the connected page
(00 privacy invariants; last four allowed). Logs at DEBUG carry no number either."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

import pytest
from mock_carrier.testing import ASISH, MOM
from tower_audit import AuditRecord, append
from tower_consent import list_lines
from tower_policy import ReasonCode, policy_version

from tests.privacy.patterns import E164_STRICT, phone_hits

pytestmark = pytest.mark.integration

MASKED = re.compile(r"•••• \d{4}")


def _numbers_in(text: str) -> list[str]:
    raw = [n for n in (ASISH, MOM) if n in text or n[1:] in text or n[2:] in text]
    return raw + phone_hits(text)


async def test_every_page_is_number_free(make_page: Any, h: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    page = make_page(admin=True)
    pages: dict[str, str] = {}

    # bind page, both simulation banners, refused (Wi-Fi on), connected, expired
    browser = page.browser()
    token = page.bind_token("user-mom")
    pages["bind (sim)"] = (await browser.get(f"/bind/{token}?as=phone-mom")).text
    pages["bind (no sim)"] = (await browser.get(f"/bind/{token}")).text
    pages["refused wifi"] = (await browser.post(f"/bind/{token}/verify")).text
    r = await browser.post(f"/bind/{token}/verify", data={"as": "phone-mom"})
    pages["connected mom"] = r.text
    pages["expired"] = (await browser.get(f"/bind/{token}")).text
    mom = browser

    asish, r = await h.bind(page, "user-asish", "phone-asish")
    pages["connected asish"] = r.text
    pages["taken"] = (await h.bind(page, "user-other", "phone-asish"))[1].text

    code = await h.invite_code(asish)
    r = await mom.post(
        "/grants",
        data={"csrf": await h.csrf_of(mom), "invite_code": code, "kind": "watch", "alias": "mom"},
    )
    pages["me mom (after grant)"] = r.text
    r = await asish.post("/me/invite", data={"csrf": await h.csrf_of(asish)})
    pages["me asish (invite shown)"] = r.text
    r = await mom.post(
        "/grants", data={"csrf": await h.csrf_of(mom), "invite_code": ASISH, "kind": "watch", "alias": "x"}
    )
    pages["me mom (error)"] = r.text

    (line,) = list_lines(page.store, "user-mom")
    for i in range(3):
        append(
            page.store,
            AuditRecord(
                line_id=line.line_id,
                ts=page.clock() - timedelta(minutes=10 * (3 - i)),
                actor_user_id="user-asish",
                tool="line_is_ok",
                trigger="voice",
                source="carrier",
                outcome="ok",
                reason_codes=(ReasonCode.OK,),
                policy_version=policy_version(),
            ),
        )
    pages["audit mom"] = (await mom.get(f"/me/lines/{line.line_id}/audit")).text
    pages["audit forbidden"] = (await asish.get(f"/me/lines/{line.line_id}/audit")).text
    pages["admin tables"] = (await mom.get("/_admin/tables")).text
    pages["admin tables json"] = (await mom.get("/_admin/tables?format=json")).text
    pages["admin resolve"] = (
        await mom.get("/_admin/resolve", params={"user": "user-asish", "line": "mom"})
    ).text
    async with page.browser() as anon:
        pages["no session"] = (await anon.get("/me")).text
        pages["healthz"] = (await anon.get("/healthz")).text

    assert len(pages) == 17
    for name, text in pages.items():
        assert _numbers_in(text) == [], f"{name}: {_numbers_in(text)}"
        masked = MASKED.findall(text)
        if name.startswith("connected"):
            assert len(masked) == 1, name
        else:
            assert masked == [], f"{name} shows a masked number"
    assert "•••• 0102" in pages["connected mom"] and "•••• 0101" in pages["connected asish"]
    assert "Line connected" in pages["connected mom"]
    assert "Log verified" in pages["audit mom"]

    # Logs at DEBUG (ours, httpx, botocore — whose request dumps carry SigV4 hex, so the generic regex would
    # be noise there): neither number appears in any form.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert log_text, "nothing was captured — the check would be vacuous"
    assert [n for n in (ASISH, MOM) if n[2:] in log_text] == []
    assert [m for m in E164_STRICT.findall(log_text) if m.endswith(("6135550101", "6135550102"))] == []
    await mom.aclose()
    await asish.aclose()
