#!/usr/bin/env python3
"""`make seed` (run by `make up`): put the local stack in the demo's starting state. Idempotent.

1. `ensure_tables` on DynamoDB Local (the six consent/audit tables).
2. Load `scenarios/demo.yaml` on the mock carrier (resets its clock, lines, subscriptions), and drop what the
   previous run left that would outlive that reset: every Watch (its `last_state` was observed on the old
   timeline, which Tower's stored-state path would trust) and Alerts' `AlertsState` rows (sink tokens and
   subscription ids the reset mock no longer knows, rate-limit claims). Users, Lines, Grants and the Audit log are
   kept; the audit is append-only even locally.
3. Create the users `user-asish`, `user-mom`, `user-partner` and `user-neighbour`.
4. Bind all four lines through the binding page's real one-tap flow: a bind link from the page's local admin,
   then `POST /bind/<token>/verify` with the simulated client id (`phone-asish` / `phone-mom` / `phone-partner` /
   `phone-neighbour`), which makes the page run the mock's OAuth auth-code flow + Number Verification exactly as
   a phone on mobile data would. The contacts' alert phone is their own bound line (06 §3), so nothing is typed.
5. Grants through the page's local admin (the same `tower_consent.grant`): Mom grants Asish `watch` as "mom";
   Asish grants the partner and the neighbour `watch` as "asish" — the transplant story's contacts (06 §11.4).
   The profile is **not** set here: the demo's Settings step saves it, as the line-holder would on `/me`.
6. Print the state: who resolves to what, table counts, the mock clock. No number is printed (the store holds
   none; the mock's numbers are never read here).

Runs in the `seed` compose service (the tower-mcp image, `docker compose --profile tools run --rm seed`), or on
the host with `MOCK_URL`, `BINDING_URL` and `DYNAMO_ENDPOINT` pointing at the published ports. `MOCK_ADMIN_TOKEN`,
when set, goes on the mock's admin calls (08 §3, G1).

The demo UI's Reset (doc 11 §5, decision 5) is `reset(mock, page, store)`: steps 1–5 with the UI's own clients and
store, no printing of the state table and no change to `os.environ`.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from tower_consent import Store

MOCK_URL = os.environ.get("MOCK_URL", "http://localhost:8443").rstrip("/")
BINDING_URL = os.environ.get("BINDING_URL", "http://localhost:8081").rstrip("/")
PEOPLE = (
    ("user-asish", "phone-asish"),
    ("user-mom", "phone-mom"),
    ("user-partner", "phone-partner"),
    ("user-neighbour", "phone-neighbour"),
)
GRANTS = (  # (owner, grantee, alias): all `watch`
    ("user-mom", "user-asish", "mom"),
    ("user-asish", "user-partner", "asish"),
    ("user-asish", "user-neighbour", "asish"),
)


class SeedError(RuntimeError):
    pass


def say(msg: str) -> None:
    print(f"seed: {msg}", flush=True)


Echo = Callable[[str], None]


def mock_admin_headers() -> dict[str, str]:
    token = os.environ.get("MOCK_ADMIN_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def wait_for(client: httpx.Client, url: str, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise SeedError(f"{url} did not answer 200 within {timeout_s:.0f}s")
        time.sleep(1)


def local_store() -> Store:
    """The CLI's store: DynamoDB Local from `DYNAMO_ENDPOINT`, with its placeholder credentials in the env."""
    from tower_consent import Store

    env = dict(os.environ)
    if not env.get("TOWER_DYNAMODB_ENDPOINT") and env.get("DYNAMO_ENDPOINT"):
        env["TOWER_DYNAMODB_ENDPOINT"] = env["DYNAMO_ENDPOINT"]
    env.setdefault("AWS_ACCESS_KEY_ID", "dynamodblocal")  # DynamoDB Local accepts any credentials
    env.setdefault("AWS_SECRET_ACCESS_KEY", "dynamodblocal")
    os.environ.update({k: env[k] for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")})
    return Store.from_env(env)


def ensure_tables_and_users(store: Store, echo: Echo = say) -> None:
    from tower_consent import ensure_user, tables

    for attempt in range(30):
        try:
            created = store.ensure_tables()
            break
        except Exception:  # noqa: BLE001 - DynamoDB Local's JVM may still be starting
            if attempt == 29:
                raise
            time.sleep(1)
    echo(f"tables ready ({len(created)} created)" if created else "tables ready (all existed)")
    watches = store.scan_all(tables.WATCHES)
    for w in watches:
        store.delete(tables.WATCHES, {"line_id": w["line_id"], "watcher_user_id": w["watcher_user_id"]})
    # Alerts' own table (services/alerts/src/alerts/state.py: PK `pk`); absent until Alerts has started once.
    alerts_state = tables.Table(name="AlertsState", hash_key=tables.Key("pk"), ttl_attribute="expires_at")
    try:
        rows = store.scan_all(alerts_state)
    except store.client.exceptions.ResourceNotFoundException:
        rows = []
    for row in rows:
        store.delete(alerts_state, {"pk": row["pk"]})
    echo(f"previous run cleared: {len(watches)} watch(es), {len(rows)} AlertsState row(s)")
    now = datetime.now(UTC)
    for user, _ in PEOPLE:
        ensure_user(store, user, now=now)
    echo("users: " + ", ".join(u for u, _ in PEOPLE))


def load_scenario(mock: httpx.Client, echo: Echo = say) -> None:
    r = mock.post("/_admin/scenarios/load", json={"name": "demo"})
    if r.status_code == 401:
        raise SeedError("the mock wants its admin token: set MOCK_ADMIN_TOKEN (deploy/compose/.env)")
    r.raise_for_status()
    echo("mock: scenarios/demo.yaml loaded (clock and lines reset)")


def bind(page: httpx.Client, user_id: str, client_id: str, echo: Echo = say) -> None:
    """The one tap, driven over HTTP. The callback URL the carrier redirects to is the page's public base
    (BASE_URL, e.g. localhost:8081 for the phone); inside the network the same path is on BINDING_URL."""
    r = page.post("/_admin/bind-tokens", json={"user_id": user_id})
    if r.status_code == 404:
        raise SeedError("the binding page's admin is off (needs TOWER_ENV=local and BIND_ADMIN=1)")
    r.raise_for_status()
    path = urlsplit(r.json()["url"]).path  # /bind/<token>
    if page.get(path, params={"as": client_id}).status_code != 200:
        raise SeedError(f"bind link for {user_id} did not open")
    r = page.post(f"{path}/verify", data={"as": client_id})
    if r.status_code != 303:
        raise SeedError(f"verify for {user_id} answered {r.status_code}, expected a redirect to the callback")
    target = urlsplit(r.headers["location"])
    r = page.get(target.path + (f"?{target.query}" if target.query else ""))
    if r.status_code != 200 or "Line connected" not in r.text:
        raise SeedError(f"bind callback for {user_id} answered {r.status_code}")
    page.cookies.clear()
    echo(f"bound {user_id}'s line via the mock's auth-code flow (simulated client id {client_id})")


def grant(page: httpx.Client, owner: str, grantee: str, alias: str, echo: Echo = say) -> None:
    body = {
        "owner_user_id": owner,
        "grantee_user_id": grantee,
        "grant": "watch",
        "alias": alias,
        "action": "grant",
    }
    r = page.post("/_admin/grants", json=body)
    if r.status_code == 404:
        raise SeedError("POST /_admin/grants is missing on the binding page (BIND_ADMIN=1, TOWER_ENV=local)")
    r.raise_for_status()
    state = "granted" if r.json()["changed"] else "already granted"
    echo(f"{owner} → {grantee}: watch as '{alias}' ({state})")


def reset(mock: httpx.Client, page: httpx.Client, store: Store, echo: Echo = say) -> None:
    """Steps 1–5: the demo's starting state. `make seed` and the demo UI's Reset both run exactly this."""
    ensure_tables_and_users(store, echo)
    load_scenario(mock, echo)
    for user, client_id in PEOPLE:
        bind(page, user, client_id, echo)
    for owner, grantee, alias in GRANTS:
        grant(page, owner, grantee, alias, echo)


def print_state(page: httpx.Client, mock: httpx.Client) -> None:
    def resolve(user: str, line: str) -> dict[str, Any]:
        r = page.get("/_admin/resolve", params={"user": user, "line": line})
        r.raise_for_status()
        return dict(r.json())

    print("\n  who          asks about   bound  grant")
    asks = (
        ("user-asish", "self"),
        ("user-mom", "self"),
        ("user-asish", "mom"),
        ("user-partner", "asish"),
        ("user-neighbour", "asish"),
    )
    for user, line in asks:
        v = resolve(user, line)
        print(f"  {user:<12} {line:<12} {v.get('bound')!s:<6} {v.get('grant')}")
    tables = page.get("/_admin/tables", params={"format": "json"}).json()
    print("  tables: " + ", ".join(f"{name} {len(rows)}" for name, rows in tables.items()))
    clock = mock.get("/_admin/clock").json().get("clock")
    print(f"  mock clock: {clock}   (Tower and Alerts follow it)\n", flush=True)
    watching = all(resolve(u, a).get("grant") == "watch" for u, a in asks[2:])
    if not (resolve("user-asish", "self").get("bound") and watching):
        raise SeedError("seeded state is not the demo's starting state")


def main() -> int:
    try:
        with (
            httpx.Client(base_url=MOCK_URL, timeout=15.0, headers=mock_admin_headers()) as mock,
            httpx.Client(base_url=BINDING_URL, timeout=30.0, follow_redirects=False) as page,
        ):
            wait_for(mock, "/healthz")
            wait_for(page, "/healthz")
            reset(mock, page, local_store())
            print_state(page, mock)
    except (SeedError, httpx.HTTPError) as e:
        print(f"seed: FAILED: {e}", file=sys.stderr)
        return 1
    say("done — `make demo` next")
    return 0


if __name__ == "__main__":
    sys.exit(main())
