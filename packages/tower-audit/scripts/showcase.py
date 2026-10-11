#!/usr/bin/env python3
"""`make showcase-audit` (testing-and-showcase §2.7), the audit part on its own.

ENV=local: against DynamoDB Local when `TOWER_DYNAMODB_ENDPOINT` is set (compose), otherwise in-process moto.
Seeds a week on Mom's line (daily check x7, Asish's two voice checks, an alert, a SUPPRESSED_REVOKED row),
prints what Mom sees (her log, `recent_checks`, chain verified), the canonical JSON of one row, then tampers one
row in the table and shows `verify` naming it. Fixed clock; demo-only HMAC key; no phone number is printed.

ENV=eks|aws: needs the deployed stack and the binding page (prompt 09); prints the steps and exits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

from tower_audit import (
    SYSTEM_ALERTS,
    AuditRecord,
    HmacMarkerSigner,
    append,
    list_for_line,
    recent_checks,
    verify,
)
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, bind_line, grant
from tower_consent import tables as T
from tower_policy import policy_version

NOW = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
DEMO_KEY = hashlib.sha256(b"ask-the-tower showcase-audit demo key, not a secret").digest()
MOM_NUMBER = "+15555550123"  # fictional 555-01xx range; never printed
MOM, ASISH = "user-mom", "user-asish"


def _store(stack: ExitStack) -> Store:
    if os.environ.get("TOWER_DYNAMODB_ENDPOINT"):
        store = Store.from_env()
        store.prefix = f"{store.prefix}showcase-{uuid.uuid4().hex[:6]}-"
    else:
        for k, v in {
            "AWS_ACCESS_KEY_ID": "x",
            "AWS_SECRET_ACCESS_KEY": "x",
            "AWS_DEFAULT_REGION": "us-east-1",
        }.items():
            os.environ.setdefault(k, v)
        import boto3
        from moto import mock_aws

        stack.enter_context(mock_aws())
        store = Store(boto3.client("dynamodb", region_name="us-east-1"), prefix="showcase-")
    store.ensure_tables()
    return store


def _rec(line_id: str, minutes: int, **kw: object) -> AuditRecord:
    data: dict[str, object] = {
        "line_id": line_id,
        "ts": NOW - timedelta(days=6) + timedelta(minutes=minutes),
        "actor_user_id": SYSTEM_ALERTS,
        "tool": "alert",
        "trigger": "poll",
        "outcome": "ok",
        "reason_codes": ["OK"],
        "policy_version": policy_version(),
    }
    data.update(kw)
    return AuditRecord.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="local", choices=["local", "eks", "aws"])
    args = ap.parse_args(argv)
    if args.env != "local":
        print(
            f"showcase-audit ENV={args.env}: open the binding page as Mom on the deployed stack; the log shows "
            "the chain status. Needs prompt 09 and a deployed stack — see docs/submission/build-log/07.md."
        )
        return 0

    with ExitStack() as stack:
        store = _store(stack)
        hasher, cipher, signer = (
            LocalLineIdHasher(DEMO_KEY),
            LocalMsisdnCipher(DEMO_KEY),
            HmacMarkerSigner(DEMO_KEY),
        )
        line = bind_line(store, hasher, cipher, MOM, MOM_NUMBER, "auth_code", now=NOW - timedelta(days=7))
        grant(store, line.line_id, ASISH, "watch", "mom", granted_by=MOM, now=NOW - timedelta(days=7))

        week = [_rec(line.line_id, d * 1440) for d in range(7)]  # the daily check ran seven times
        week += [
            _rec(line.line_id, 600, actor_user_id=ASISH, tool="line_is_ok", trigger="voice", source="watch"),
            _rec(
                line.line_id, 4000, actor_user_id=ASISH, tool="line_is_ok", trigger="voice", source="carrier"
            ),
            _rec(
                line.line_id,
                5000,
                trigger="event",
                outcome="changed",
                reason_codes=["SIM_SWAPPED_RECENT"],
                message_ref="SIM_SWAPPED_RECENT.sms",
            ),
            _rec(
                line.line_id, 6000, trigger="event", outcome="suppressed", reason_codes=["SUPPRESSED_REVOKED"]
            ),
        ]
        rows = [append(store, r) for r in sorted(week, key=lambda r: r.ts)]

        print("== Mom's audit log (binding page view, newest first) ==")
        for r in list_for_line(store, line.line_id, MOM):
            print(
                f"  {r.ts:%a %H:%M}  {r.actor_user_id:<14} {r.tool:<11} {r.trigger:<6} {r.outcome:<10} "
                f"{','.join(c.value for c in r.reason_codes)}"
            )
        print("\n== recent_checks (watch_line(line='self') facts) ==")
        rc = recent_checks(store, line.line_id, NOW - timedelta(days=7), viewer_user_id=MOM)
        print(json.dumps(rc.model_dump(mode="json"), indent=2, sort_keys=True))
        print("\n== canonical JSON of one row ==")
        print(rows[1].canonical())
        print("\n== verify ==")
        print(verify(store, line.line_id, signer).model_dump_json())

        target = rows[2]
        print(f"\n== tamper: set outcome='changed' on the row at {target.ts:%Y-%m-%dT%H:%M:%SZ} ==")
        store.update(
            T.AUDIT,
            {"line_id": target.line_id, "ts_seq": target.ts_seq},
            "SET outcome = :o",
            values={":o": "changed"},
        )
        res = verify(store, line.line_id, signer)
        print(res.model_dump_json())
        return 0 if (not res.ok and res.first_bad_ts == target.ts) else 1


if __name__ == "__main__":
    sys.exit(main())
