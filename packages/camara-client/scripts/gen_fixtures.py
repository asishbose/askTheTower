#!/usr/bin/env python3
"""Generate the carrier client's spec index and conformance fixtures from `specs/camara/*.yaml`.

Writes into `packages/camara-client/src/camara_client/fixtures/`:

- `operations.json` — every operation in the vendored specs: method, versioned path, scopes, path
  parameters, declared statuses, the error (status, code) pairs, and the request schema. The client
  reads paths and versions from this file at runtime, so the Tower image needs no YAML.
- `<api>__<operationId>.json` — one file per operation the client calls: request/response pairs for
  every success example and every error code the spec declares for that operation, plus a few
  synthetic out-of-spec cases (500, an unknown code, a non-JSON 502). Both `DirectClient` and
  `GatewayClient` replay them (`tests/test_fixtures.py`).

The specs are parsed with the mock carrier's own loader (`mock_carrier.specs`), so the client and the
mock can never disagree on a path. Run after any change to `specs/camara/`:

    uv run python packages/camara-client/scripts/gen_fixtures.py          # write
    uv run python packages/camara-client/scripts/gen_fixtures.py --check  # exit 1 if stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mock_carrier.specs import SPEC_FILES, SpecSet, load_spec_file

ROOT = Path(__file__).resolve().parents[3]
SPECS_DIR = ROOT / "specs" / "camara"
OUT_DIR = ROOT / "packages" / "camara-client" / "src" / "camara_client" / "fixtures"

# Fixture inputs. The number is 555-01xx fiction (never a real line).
LINE_ID = "line-fixture-a"
E164 = "+16135550199"
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
TTL_H = 24
SINK = "https://sink.test/hooks/fixture-line-a"
AUTH_CODE = "fixture-auth-code"
REDIRECT = "http://localhost:8081/callback"
SUB_ID = "sub-fixture-1"
SWAPPED = "org.camaraproject.sim-swap-subscriptions.v0.swapped"
DISCONNECTED = "org.camaraproject.device-reachability-status-subscriptions.v0.reachability-disconnected"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ref(doc: dict[str, Any], node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        parts = node["$ref"].removeprefix("#/").split("/")
        target: Any = doc
        for p in parts:
            target = target[p]
        node = target
    return node


def _codes_of(doc: dict[str, Any], response: dict[str, Any]) -> list[str]:
    schema = _ref(doc, response.get("content", {}).get("application/json", {}).get("schema", {}))
    codes: list[str] = []
    for part in schema.get("allOf", []):
        part = _ref(doc, part)
        enum = part.get("properties", {}).get("code", {}).get("enum")
        if enum:
            codes.extend(str(c) for c in enum)
    return codes


def _examples_of(doc: dict[str, Any], response: dict[str, Any]) -> list[tuple[str, Any]]:
    media = response.get("content", {}).get("application/json", {})
    out = []
    for name, ex in (media.get("examples") or {}).items():
        ex = _ref(doc, ex)
        if "value" in ex:
            out.append((name, ex["value"]))
    if not out and "example" in media:
        out.append(("example", media["example"]))
    return out


def spec_error_codes() -> dict[str, list[tuple[int, str]]]:
    """Every (status, code) pair declared by every operation's error responses, per `api/operationId`."""
    result: dict[str, list[tuple[int, str]]] = {}
    for api, filename in SPEC_FILES.items():
        doc = load_spec_file(SPECS_DIR / filename)
        for item in doc["paths"].values():
            for method, op in item.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                pairs: list[tuple[int, str]] = []
                for status, resp in op["responses"].items():
                    if not str(status).isdigit() or int(status) < 400:
                        continue
                    for code in _codes_of(doc, _ref(doc, resp)):
                        pairs.append((int(status), code))
                result[f"{api}/{op['operationId']}"] = pairs
    return result


def component_error_codes() -> set[tuple[int, str]]:
    """Every (status, code) in every error response schema under `components/responses` of every
    spec — including ones only callbacks use (e.g. 410 GONE)."""
    pairs: set[tuple[int, str]] = set()
    for filename in SPEC_FILES.values():
        doc = load_spec_file(SPECS_DIR / filename)
        for resp in doc.get("components", {}).get("responses", {}).values():
            schema = _ref(doc, resp.get("content", {}).get("application/json", {}).get("schema", {}))
            statuses: list[int] = []
            for part in schema.get("allOf", []):
                statuses += [
                    int(s) for s in _ref(doc, part).get("properties", {}).get("status", {}).get("enum", [])
                ]
            for code in _codes_of(doc, resp):
                pairs.update((st, code) for st in statuses)
    return pairs


def build_index(specs: SpecSet) -> dict[str, Any]:
    errors = spec_error_codes()
    apis = {}
    for api, spec in specs.specs.items():
        apis[api] = {
            "file": spec.file.name,
            "sha256": hashlib.sha256(spec.file.read_bytes()).hexdigest(),
            "version": spec.version,
            "base_path": spec.base_path,
        }
    operations = {}
    for op in specs.operations():
        key = f"{op.api}/{op.operation_id}"
        operations[key] = {
            "api": op.api,
            "operation_id": op.operation_id,
            "method": op.method,
            "path": op.full_path,
            "path_params": re.findall(r"{(\w+)}", op.full_path),
            "scopes": [list(alt) for alt in op.scopes],
            "statuses": sorted(op.responses),
            "errors": [list(p) for p in errors[key]],
            "request_schema": op.request_schema,
        }
    return {
        "generated_by": "packages/camara-client/scripts/gen_fixtures.py",
        "source": "specs/camara (CAMARA Fall25)",
        "apis": apis,
        "operations": dict(sorted(operations.items())),
    }


# --- fixtures ----------------------------------------------------------------------------------------
# Which client method drives each operation, with which arguments, and what request it must send.
_LINE = {"line_id": LINE_ID, "e164": E164}
_SUB_CALL = {"ttl_h": TTL_H, "now": _iso(NOW), "sink_url": SINK}
_EXPIRES = _iso(NOW + timedelta(hours=TTL_H))

DRIVERS: dict[str, dict[str, Any]] = {
    "sim-swap/checkSimSwap": {
        "call": {"method": "sim_swap_check", "args": {"line": _LINE, "max_age_h": 72}},
        "json": {"phoneNumber": E164, "maxAge": 72},
    },
    "sim-swap/retrieveSimSwapDate": {
        "call": {"method": "sim_swap_date", "args": {"line": _LINE}},
        "json": {"phoneNumber": E164},
    },
    "call-forwarding-signal/retrieveCallForwarding": {
        "call": {"method": "call_forwarding", "args": {"line": _LINE}},
        "json": {"phoneNumber": E164},
    },
    "number-verification/phoneNumberVerify": {
        "call": {
            "method": "number_verify",
            "args": {"auth_code": AUTH_CODE, "redirect_uri": REDIRECT, "e164": E164},
        },
        "json": {"phoneNumber": E164},
    },
    "number-verification/phoneNumberShare": {
        "call": {"method": "number_verify", "args": {"auth_code": AUTH_CODE, "redirect_uri": REDIRECT}},
        "json": None,
    },
    "device-reachability-status/getReachabilityStatus": {
        "call": {"method": "reachability", "args": {"line": _LINE}},
        "json": {"device": {"phoneNumber": E164}},
    },
    "sim-swap-subscriptions/createSimSwapSubscription": {
        "call": {"method": "subscribe", "args": {"kind": "sim-swap", "line": _LINE, **_SUB_CALL}},
        "json": {
            "protocol": "HTTP",
            "sink": SINK,
            "types": [SWAPPED],
            "config": {"subscriptionDetail": {"phoneNumber": E164}, "subscriptionExpireTime": _EXPIRES},
        },
    },
    "sim-swap-subscriptions/deleteSubscription": {
        "call": {"method": "unsubscribe", "args": {"subscription_id": f"sim-swap-subscriptions/{SUB_ID}"}},
        "json": None,
        "path_params": {"subscriptionId": SUB_ID},
    },
    "device-reachability-status-subscriptions/createDeviceReachabilityStatusSubscription": {
        "call": {
            "method": "subscribe",
            "args": {"kind": "reachability-disconnected", "line": _LINE, **_SUB_CALL},
        },
        "json": {
            "protocol": "HTTP",
            "sink": SINK,
            "types": [DISCONNECTED],
            "config": {
                "subscriptionDetail": {"device": {"phoneNumber": E164}},
                "subscriptionExpireTime": _EXPIRES,
            },
        },
    },
    "device-reachability-status-subscriptions/deleteDeviceReachabilityStatusSubscription": {
        "call": {
            "method": "unsubscribe",
            "args": {"subscription_id": f"device-reachability-status-subscriptions/{SUB_ID}"},
        },
        "json": None,
        "path_params": {"subscriptionId": SUB_ID},
    },
}

# Out-of-spec answers a real carrier (or a proxy in front of it) can still give.
SYNTHETIC: list[dict[str, Any]] = [
    {
        "status": 500,
        "json": {"status": 500, "code": "INTERNAL", "message": "Synthetic fixture: internal error."},
    },
    {
        "status": 400,
        "json": {
            "status": 400,
            "code": "SOMETHING_NEW",
            "message": "Synthetic fixture: a code no spec declares.",
        },
    },
    {"status": 502, "text": "<html>Bad Gateway</html>"},
]


# Success bodies for responses whose spec carries no example: built from the response schema by hand
# (every enum value / boolean branch the client has to tell apart).
DERIVED: dict[tuple[str, int], list[tuple[str, Any]]] = {
    ("sim-swap/checkSimSwap", 200): [("swapped", {"swapped": True}), ("not swapped", {"swapped": False})],
    ("call-forwarding-signal/retrieveCallForwarding", 200): [
        ("inactive", ["inactive"]),
        ("unconditional", ["unconditional"]),
        ("conditional", ["conditional_busy", "conditional_no_answer"]),
        ("unconditional+conditional", ["conditional_not_reachable", "unconditional"]),
    ],
    ("call-forwarding-signal/retrieveUnconditionalCallForwarding", 200): [
        ("active", {"active": True}),
        ("inactive", {"active": False}),
    ],
    ("number-verification/phoneNumberVerify", 200): [
        ("verified", {"devicePhoneNumberVerified": True}),
        ("not verified", {"devicePhoneNumberVerified": False}),
    ],
    ("number-verification/phoneNumberShare", 200): [("shared", {"devicePhoneNumber": E164})],
    ("sim-swap-subscriptions/createSimSwapSubscription", 202): [("async", {"id": "qs15-h556-rt89-1298"})],
    ("sim-swap-subscriptions/deleteSubscription", 202): [("async", {"id": "qs15-h556-rt89-1298"})],
    (
        "device-reachability-status-subscriptions/createDeviceReachabilityStatusSubscription",
        202,
    ): [("async", {"id": "qs15-h556-rt89-1298"})],
    (
        "device-reachability-status-subscriptions/deleteDeviceReachabilityStatusSubscription",
        202,
    ): [("async", {"id": "qs15-h556-rt89-1298"})],
}


def _request(entry: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    path = op["path"]
    for k, v in (entry.get("path_params") or {}).items():
        path = path.replace("{" + k + "}", v)
    return {"method": op["method"], "path": path, "json": entry["json"]}


def _cases_for(key: str, index: dict[str, Any], docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    op = index["operations"][key]
    entry = DRIVERS[key]
    doc = docs[op["api"]]
    spec_op = None
    for item in doc["paths"].values():
        for candidate in item.values():
            if isinstance(candidate, dict) and candidate.get("operationId") == op["operation_id"]:
                spec_op = candidate
    assert spec_op is not None, key
    request = _request(entry, op)
    cases: list[dict[str, Any]] = []

    def add(name: str, exchanges: list[dict[str, Any]], source: str = "spec") -> None:
        cases.append({"case": name, "source": source, "call": entry["call"], "exchanges": exchanges})

    for status, resp in sorted(spec_op["responses"].items(), key=lambda kv: str(kv[0])):
        if not str(status).isdigit():
            continue
        st = int(status)
        resp = _ref(doc, resp)
        if st < 400:
            examples = _examples_of(doc, resp)
            source = "spec"
            if not examples and (key, st) in DERIVED:
                examples, source = DERIVED[(key, st)], "derived"
            if not examples:
                add(f"{st}", [{"request": request, "response": {"status": st, "json": None}}])
            for name, value in examples:
                add(f"{st} {name}", [{"request": request, "response": {"status": st, "json": value}}], source)
            continue
        by_code = {v.get("code"): v for _, v in _examples_of(doc, resp) if isinstance(v, dict)}
        for code in _codes_of(doc, resp):
            body = by_code.get(code) or {"status": st, "code": code, "message": f"Spec-declared {code}."}
            add(f"{st} {code}", [{"request": request, "response": {"status": st, "json": body}}])
    for syn in SYNTHETIC:
        label = syn["json"]["code"] if "json" in syn else "non-JSON body"
        response = {
            "status": syn["status"],
            **({"json": syn["json"]} if "json" in syn else {"text": syn["text"]}),
        }
        add(f"{syn['status']} {label}", [{"request": request, "response": response}], source="synthetic")

    if key == "call-forwarding-signal/retrieveCallForwarding":
        # 501 NOT_IMPLEMENTED on the list → the client falls back to the unconditional operation.
        fb_key = "call-forwarding-signal/retrieveUnconditionalCallForwarding"
        fb_op = index["operations"][fb_key]
        fb_req = {"method": fb_op["method"], "path": fb_op["path"], "json": {"phoneNumber": E164}}
        plain = next(c for c in cases if c["case"] == "501 NOT_IMPLEMENTED")
        cases.remove(plain)  # never the end of the story: every 501 case below carries the fallback
        first = plain["exchanges"][0]
        fb_doc_op = next(
            c
            for item in doc["paths"].values()
            for c in item.values()
            if isinstance(c, dict) and c.get("operationId") == "retrieveUnconditionalCallForwarding"
        )
        for status, resp in sorted(fb_doc_op["responses"].items(), key=lambda kv: str(kv[0])):
            st = int(status)
            resp = _ref(doc, resp)
            if st < 400:
                for name, value in _examples_of(doc, resp) or DERIVED[(fb_key, st)]:
                    add(
                        f"501 → unconditional {st} {name}",
                        [first, {"request": fb_req, "response": {"status": st, "json": value}}],
                        "derived",
                    )
                continue
            by_code = {v.get("code"): v for _, v in _examples_of(doc, resp) if isinstance(v, dict)}
            for code in _codes_of(doc, resp):
                body = by_code.get(code) or {"status": st, "code": code, "message": f"Spec-declared {code}."}
                add(
                    f"501 → unconditional {st} {code}",
                    [first, {"request": fb_req, "response": {"status": st, "json": body}}],
                )
    return cases


def build_fixtures(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    docs = {api: load_spec_file(SPECS_DIR / f) for api, f in SPEC_FILES.items()}
    files: dict[str, dict[str, Any]] = {}
    for key in DRIVERS:
        api, op_id = key.split("/")
        files[f"{api}__{op_id}.json"] = {
            "operation": key,
            "inputs": {"line_id": LINE_ID, "e164": E164, "now": _iso(NOW), "auth_code": AUTH_CODE},
            "cases": _cases_for(key, index, docs),
        }
    return files


def render() -> dict[str, str]:
    specs = SpecSet(SPECS_DIR, base_url="http://carrier.invalid", discovery_url="http://carrier.invalid/d")
    index = build_index(specs)
    out = {"operations.json": json.dumps(index, indent=1, sort_keys=False, ensure_ascii=False) + "\n"}
    for name, content in build_fixtures(index).items():
        out[name] = json.dumps(content, indent=1, ensure_ascii=False) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the files on disk are stale")
    args = ap.parse_args(argv)
    rendered = render()
    stale = [
        n for n, c in rendered.items() if not (OUT_DIR / n).exists() or (OUT_DIR / n).read_text("utf-8") != c
    ]
    extra = sorted(p.name for p in OUT_DIR.glob("*.json") if p.name not in rendered)
    if args.check:
        if stale or extra:
            print(f"stale fixtures: {stale}; unexpected: {extra} — run gen_fixtures.py", file=sys.stderr)
            return 1
        print(f"fixtures up to date ({len(rendered)} files)")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8", newline="\n")
    for name in extra:
        (OUT_DIR / name).unlink()
    cases = {n: len(json.loads(c).get("cases", [])) for n, c in rendered.items() if n != "operations.json"}
    for n, c in cases.items():
        print(f"{c:3d}  {n}")
    print(
        f"{sum(cases.values())} cases in {len(cases)} files; operations.json indexes "
        f"{len(json.loads(rendered['operations.json'])['operations'])} operations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
