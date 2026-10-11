"""D7 (code-vs-docs.md) through the page: Mom's one-tap revoke must also turn off Asish's Watch on her line.

Design rule 4 (revocable), 04 §3 (revocation is one tap on this page), 06 §4. Today the revoke route
(grants.py:141-163) only sets `revoked_at`; the grantee's Watch stays `enabled`, so Alerts keeps its
subscriptions live. Mom's own Watch on her line, and Asish's Watch on his own line, are not touched.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from tower_consent import get_watch, list_lines, upsert_watch
from tower_consent.models import Watch

pytestmark = pytest.mark.integration


async def _bind(page: Any, h: Any, user: str, device: str) -> httpx.AsyncClient:
    browser, r = await h.bind(page, user, device)
    assert r.status_code == 200 and "Line connected" in r.text
    return browser


async def test_d7_revoke_on_page_disables_the_grantees_watch(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    code = await h.invite_code(asish)
    r = await mom.post(
        "/grants",
        data={"csrf": await h.csrf_of(mom), "invite_code": code, "kind": "watch", "alias": "mom"},
    )
    assert r.status_code == 200
    mom_line = list_lines(page.store, "user-mom")[0].line_id
    asish_line = list_lines(page.store, "user-asish")[0].line_id
    # "Alexa, watch mom's line" (watch_line writes this), Mom's own watch, Asish's own watch
    upsert_watch(page.store, Watch(line_id=mom_line, watcher_user_id="user-asish", profile="care"))
    upsert_watch(page.store, Watch(line_id=mom_line, watcher_user_id="user-mom", profile="self"))
    upsert_watch(page.store, Watch(line_id=asish_line, watcher_user_id="user-asish", profile="self"))

    (gid,) = re.findall(r'action="/grants/([^"]+)/revoke"', (await mom.get("/me")).text)
    r = await mom.post(f"/grants/{gid}/revoke", data={"csrf": await h.csrf_of(mom)})
    assert r.status_code == 200

    w = get_watch(page.store, mom_line, "user-asish")
    assert w is None or not w.enabled, f"revoked grantee's Watch still enabled: enabled={w.enabled}"
    own = get_watch(page.store, mom_line, "user-mom")
    assert own is not None and own.enabled
    his = get_watch(page.store, asish_line, "user-asish")
    assert his is not None and his.enabled
    await mom.aclose()
    await asish.aclose()
