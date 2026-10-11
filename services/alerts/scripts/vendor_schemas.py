#!/usr/bin/env python3
"""Extract the CloudEvents notification schemas from the vendored CAMARA specs (specs/camara/).

Writes `services/alerts/src/alerts/schemas/<api>.cloudevents.json`: the `CloudEvent` schema, every
schema it (transitively) references, and the spec's `type` → event-schema discriminator mapping, with
`#/components/schemas/X` rewritten to `#/$defs/X`. The Alerts image then validates webhooks without
YAML or the full specs. `--check` exits 1 when the files are out of date (a test runs it).

    uv run python services/alerts/scripts/vendor_schemas.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mock_carrier.specs import load_spec_file

ROOT = Path(__file__).resolve().parents[3]
SPECS = ROOT / "specs" / "camara"
OUT = ROOT / "services" / "alerts" / "src" / "alerts" / "schemas"
APIS = ("sim-swap-subscriptions", "device-reachability-status-subscriptions")
PREFIX = "#/components/schemas/"


def _refs(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(PREFIX):
            out.add(ref[len(PREFIX) :])
        for v in node.values():
            out |= _refs(v)
    elif isinstance(node, list):
        for v in node:
            out |= _refs(v)
    return out


def _rewrite(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: (
                "#/$defs/" + v[len(PREFIX) :]
                if k == "$ref" and isinstance(v, str) and v.startswith(PREFIX)
                else _rewrite(v)
            )
            for k, v in node.items()
            if k not in ("example", "examples", "discriminator")
        }
    if isinstance(node, list):
        return [_rewrite(v) for v in node]
    return node


def build(api: str) -> dict[str, Any]:
    spec = load_spec_file(SPECS / f"{api}.yaml")
    schemas: dict[str, Any] = spec["components"]["schemas"]
    cloud_event = schemas["CloudEvent"]
    mapping = {t: ref[len(PREFIX) :] for t, ref in cloud_event["discriminator"]["mapping"].items()}
    todo = ["CloudEvent", *mapping.values()]
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(_refs(schemas[name]) - seen)
    return {
        "$comment": f"Generated from specs/camara/{api}.yaml by services/alerts/scripts/vendor_schemas.py",
        "api": api,
        "info_version": spec["info"]["version"],
        "mapping": dict(sorted(mapping.items())),
        "$defs": {name: _rewrite(schemas[name]) for name in sorted(seen)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    stale = []
    for api in APIS:
        text = json.dumps(build(api), indent=1, sort_keys=True) + "\n"
        path = OUT / f"{api}.cloudevents.json"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print("out of date:", ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
