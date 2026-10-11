#!/usr/bin/env python3
"""`make showcase-alerts` — testing-and-showcase.md §2.6, locally, in one process.

The mock carrier (scenario `demo`, then `transplant`), the Alerts app, the consent/audit tables (moto, or
DynamoDB Local when TOWER_DYNAMODB_ENDPOINT is set) and the log "phone" all run in-process; the mock's
CloudEvents reach Alerts over an ASGI transport, exactly as they would over HTTPS.

    uv run python services/alerts/scripts/showcase.py [--env local] [--fast] [--out artifacts/transcripts/alerts-showcase.log]

Pacing: one simulated minute takes 60 / ALERTS_CLOCK_SCALE real seconds (default scale 60 → 1 s), so the
transplant story's "20 minutes dark" takes 20 s on camera. `--fast` skips the sleeps. With `--env aws` the
same story runs against SNS and a real phone — not exercised here; the script prints what to run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LINES: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


class _Transcript(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.name in ("alerts.sms",) or msg.startswith(("audit ", "hook ", "subscribed", "escalate")):
            say(f"    [{record.name}] {msg}")


async def run(fast: bool, scale: float) -> int:
    import boto3
    import httpx
    from alerts import escalation
    from alerts.clock import MockCarrierClock
    from alerts.local import create_app
    from alerts.runner import poll
    from alerts.send_backends import LogSender
    from alerts.state import ensure_alerts_table
    from alerts.testing import (
        ASISH,
        BEARER,
        LINE_KEY,
        MOM,
        MSISDN_KEY,
        T0,
        LazyASGI,
        World,
        audit_rows,
        make_service,
    )
    from camara_client import BreakerRegistry, make_client
    from camara_client.testing import mock_config
    from mock_carrier.app import create_app as create_mock
    from mock_carrier.settings import Settings as MockSettings
    from mock_carrier.testing import BASE
    from tower_audit import HmacMarkerSigner, verify
    from tower_consent import (
        LocalLineIdHasher,
        LocalMsisdnCipher,
        Store,
        revoke,
        set_watch_settings,
        upsert_watch,
    )

    endpoint = os.environ.get("TOWER_DYNAMODB_ENDPOINT")
    store = Store(
        boto3.client("dynamodb", region_name="us-east-1", **({"endpoint_url": endpoint} if endpoint else {})),
        prefix=f"showcase{int(time.time())}-",
    )
    store.ensure_tables()
    ensure_alerts_table(store)

    lazy = LazyASGI()
    mock_app = create_mock(
        MockSettings(admin=True, base_url=BASE, webhook_backoff_s=0.0),
        sink_transport=httpx.ASGITransport(app=lazy),
    )
    mock_t = httpx.ASGITransport(app=mock_app)
    carrier = make_client(
        mock_config(BASE, client_id="alerts", profile="proactive"),
        secret="local-dev-alerts",
        transport=mock_t,
        breakers=BreakerRegistry(),
    )
    sender = LogSender()
    svc = make_service(store, carrier, MockCarrierClock(BASE, transport=mock_t), sender=sender)
    lazy.app = create_app(svc)
    w = World(
        svc=svc, store=store, hasher=LocalLineIdHasher(LINE_KEY), cipher=LocalMsisdnCipher(MSISDN_KEY), now=T0
    )
    auth = {"Authorization": f"Bearer {BEARER}"}
    step_s = 0.0 if fast else 60.0 / scale

    async with (
        httpx.AsyncClient(transport=mock_t, base_url=BASE) as mock,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=lazy.app), base_url="https://alerts.local"
        ) as alerts,
    ):

        async def clock() -> str:
            return (await mock.get("/_admin/clock")).json()["clock"][11:16]

        async def fire(e164: str, event: str) -> float:
            t = time.perf_counter()
            r = await mock.post(f"/_admin/lines/{e164}/events", json={"type": event})
            r.raise_for_status()
            return time.perf_counter() - t

        # --- part 1: demo scenario, care watch on Mom ------------------------------------------------------
        say("== 1. watch_line('mom', enable=true) — Asish watches Mom's line (profile care)")
        w.standard()
        w.watch(MOM, "user-asish", "care")
        mom = w.lines[MOM]
        r = await alerts.post("/internal/watch", json={"line_id": mom, "enable": True}, headers=auth)
        say(f"   internal API → {r.status_code} {r.json()}")

        say(f"\n== 2. mock {await clock()}: admin sim_swap on Mom's line → the watcher's phone buzzes")
        n = len(sender.sent)
        took = await fire(MOM, "sim_swap")
        texts = sender.sent[n:]
        say(f"   alert logged {took:.2f} s after the carrier event (budget 5 s): {[s.label for s in texts]}")
        say(
            "   Mom's own number was not texted (it may now be the attacker's): "
            + str(all(s.to_e164 != MOM for s in texts))
        )
        ok_latency = took < 5.0 and len(texts) == 1

        await mock.post("/_admin/clock", json={"advance_s": 600})
        say(f"\n== 3. mock {await clock()}: sim_swap again within 6 h → audited, not sent")
        n = len(sender.sent)
        await fire(MOM, "sim_swap")
        say(f"   texts sent: {len(sender.sent) - n}")

        say("\n== 4. Mom revokes Asish's grant on the binding page; sim_swap again → SUPPRESSED_REVOKED")
        revoke(store, mom, "user-asish", "watch", revoked_by="user-mom", now=T0 + timedelta(minutes=11))
        n = len(sender.sent)
        await fire(MOM, "sim_swap")
        say(f"   texts sent: {len(sender.sent) - n}")

        # --- part 2: transplant scenario ------------------------------------------------------------------
        say(
            "\n== 5. transplant: Asish saves 'must stay reachable' on the binding page, contacts in order "
            "[partner (ack), neighbour]; then 'Alexa, watch my line' (04 §9, 06 §11)"
        )
        await mock.post("/_admin/scenarios/load", json={"name": "transplant"})
        asish = w.lines[ASISH]
        for contact in ("user-partner", "user-neighbour"):
            w.grant_watch(ASISH, "user-asish", contact, "asish")  # each contact's consent: their watch grant
        saved = set_watch_settings(
            store, asish, "user-asish", "transplant", ["user-partner", "user-neighbour"], T0
        )
        upsert_watch(store, saved.model_copy(update={"enabled": True}))  # what watch_line(self, true) writes
        r = await alerts.post(
            "/internal/watch",
            json={"line_id": asish, "watcher_user_id": "user-asish", "enable": True, "profile": "transplant"},
            headers=auth,
        )
        say(f"   internal API → {r.status_code} {r.json()}")
        say(f"   scheduler: transplant poll every simulated minute; 1 simulated minute = {step_s:.2f} s real")
        first_text = second_text = None
        for minute in range(1, 41):
            await asyncio.sleep(step_s)
            await mock.post("/_admin/clock", json={"advance_s": 60})
            n = len(sender.sent)
            await poll(svc, "transplant")
            await escalation.tick(svc, await svc.clock.now())
            new = [s.label for s in sender.sent[n:]]
            if minute in (20, 21, 35, 36) or new:
                dark = minute - 1
                say(f"   mock {await clock()} (+{minute:02d} min, {dark} min dark): texts {new or 'none'}")
            if "chain:user-partner" in new and first_text is None:
                first_text = dark
            if "chain:user-neighbour" in new and second_text is None:
                second_text = dark
        ok_transplant = first_text == 20 and second_text == 35

        say("\n== audit rows (actor system:alerts) and chain verification")
        for name, line in (("mom", mom), ("asish", asish)):
            for row in audit_rows(store, line):
                ref = row.message_ref or "-"
                codes = ",".join(c.value for c in row.reason_codes)
                say(
                    f"   {name:5} {row.ts:%H:%M} {row.actor_user_id} trigger={row.trigger} outcome={row.outcome} codes={codes} ref={ref}"
                )
            say(f"   {name:5} verify → {verify(store, line, HmacMarkerSigner(LINE_KEY)).model_dump()}")
        say("\n== SMS bodies (the 'phone' is the log locally)")
        for s in sender.sent:
            say(f"   to={s.label}: {s.body}")
    await carrier.aclose()
    ok = ok_latency and ok_transplant
    say(
        f"\nRESULT: {'PASS' if ok else 'FAIL'} (first text after {first_text} min dark, second after {second_text})"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="local", choices=["local", "eks", "aws"])
    ap.add_argument("--fast", action="store_true", help="no pacing sleeps")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "transcripts" / "alerts-showcase.log"))
    args = ap.parse_args(argv)
    if args.env != "local":
        print(
            "showcase-alerts --env aws/eks is not exercised in the autonomous build.\n"
            "TODO(human): with Alerts deployed and a phone registered as the watcher's alert phone:\n"
            "  ALERTS_MODE=lambda ALERTS_SENDER=sns make showcase-alerts ENV=aws"
        )
        return 0
    logging.basicConfig(level=logging.WARNING)
    handler = _Transcript()
    for name in ("alerts",):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(handler)
        lg.propagate = False
    scale = float(os.environ.get("ALERTS_CLOCK_SCALE", "60") or 60)
    with ExitStack() as stack:
        if not os.environ.get("TOWER_DYNAMODB_ENDPOINT"):
            from moto import mock_aws

            for k, v in {
                "AWS_ACCESS_KEY_ID": "showcase",
                "AWS_SECRET_ACCESS_KEY": "showcase",
                "AWS_DEFAULT_REGION": "us-east-1",
            }.items():
                os.environ.setdefault(k, v)
            stack.enter_context(mock_aws())
        code = asyncio.run(run(args.fast, scale))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    print(f"\ntranscript → {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
