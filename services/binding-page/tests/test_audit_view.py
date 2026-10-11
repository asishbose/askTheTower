"""The owner's audit view (07 §4): rows + chain status for the line-holder; 403 for a watcher of that line."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from tower_audit import AuditRecord, append
from tower_consent import grant, list_lines
from tower_policy import ReasonCode, policy_version

pytestmark = pytest.mark.integration


def _seed_rows(page: Any, line_id: str, n: int = 3) -> None:
    for i in range(n):
        append(
            page.store,
            AuditRecord(
                line_id=line_id,
                ts=page.clock() - timedelta(hours=n - i),
                actor_user_id="user-asish" if i % 2 else "system:alerts",
                tool="line_is_ok" if i % 2 else "alert",
                trigger="voice" if i % 2 else "poll",
                source="carrier" if i % 2 else None,
                outcome="ok",
                reason_codes=(ReasonCode.OK,),
                policy_version=policy_version(),
            ),
        )


async def _bind(page: Any, h: Any, user: str, device: str) -> httpx.AsyncClient:
    browser, r = await h.bind(page, user, device)
    assert r.status_code == 200
    return browser


async def test_owner_sees_rows_and_chain_ok(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    (line,) = list_lines(page.store, "user-mom")
    _seed_rows(page, line.line_id)
    r = await mom.get(f"/me/lines/{line.line_id}/audit")
    assert r.status_code == 200
    assert "Log verified" in r.text and "3 entries" in r.text
    assert r.text.count("<tr>") == 4  # header + 3 rows
    assert "line_is_ok (voice)" in r.text and "alert (poll)" in r.text
    # /me links to it
    assert f"/me/lines/{line.line_id}/audit" in (await mom.get("/me")).text
    await mom.aclose()


async def test_tampered_chain_shows_failed(page: Any, h: Any) -> None:
    from tower_consent import tables

    mom = await _bind(page, h, "user-mom", "phone-mom")
    (line,) = list_lines(page.store, "user-mom")
    _seed_rows(page, line.line_id)
    rows = [r for r in page.store.scan_all(tables.AUDIT) if r["ts_seq"] != "~head"]
    victim = sorted(rows, key=lambda r: r["ts_seq"])[1]
    page.store.put(tables.AUDIT, {**victim, "outcome": "changed"})
    r = await mom.get(f"/me/lines/{line.line_id}/audit")
    assert r.status_code == 200 and "Log check failed" in r.text
    await mom.aclose()


async def test_watcher_gets_403_for_the_watched_line(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    (line,) = list_lines(page.store, "user-mom")
    grant(page.store, line.line_id, "user-asish", "watch", "mom", granted_by="user-mom", now=page.clock())
    _seed_rows(page, line.line_id)
    r = await asish.get(f"/me/lines/{line.line_id}/audit")
    assert r.status_code == 403
    assert "line_is_ok" not in r.text
    # unknown line: the same answer (no oracle)
    assert (await asish.get("/me/lines/ln_" + "a" * 64 + "/audit")).status_code == 403
    async with page.browser() as anonymous:
        assert (await anonymous.get(f"/me/lines/{line.line_id}/audit")).status_code == 401
    await mom.aclose()
    await asish.aclose()


async def test_empty_log(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    (line,) = list_lines(page.store, "user-mom")
    r = await mom.get(f"/me/lines/{line.line_id}/audit")
    assert r.status_code == 200 and "No activity yet" in r.text and "Log verified" in r.text
    await mom.aclose()
