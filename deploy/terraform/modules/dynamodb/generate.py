#!/usr/bin/env python3
"""Write deploy/terraform/tables.auto.tfvars.json from the Python table definitions (the single source).

    uv run python deploy/terraform/modules/dynamodb/generate.py          # (re)write
    uv run python deploy/terraform/modules/dynamodb/generate.py --check  # exit 1 if the file is stale

Sources: `tower_consent.tables.terraform_definitions()` (Users, Lines, Grants, Watches, Audit, BindTokens — the
output of `python -m tower_consent.tables --terraform`) plus `alerts.state.terraform_definition()` (AlertsState):
seven tables, 10 §2. The prefix is applied by Terraform (`<name_prefix>-<environment>-`), so names here are
logical. `tests/aws/test_terraform_static.py` runs `--check`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[2] / "tables.auto.tfvars.json"


def definitions() -> dict[str, Any]:
    from alerts.state import ALERTS_STATE, terraform_definition
    from tower_consent.tables import terraform_definitions

    tables: dict[str, Any] = dict(terraform_definitions(prefix="")["tables"])
    tables[ALERTS_STATE.name] = terraform_definition(prefix="")
    return {"dynamodb_tables": dict(sorted(tables.items()))}


def render() -> str:
    return json.dumps(definitions(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = args.out.read_text("utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale: run uv run python {Path(__file__).as_posix()}", file=sys.stderr)
            return 1
        print(f"{args.out.name}: up to date ({len(definitions()['dynamodb_tables'])} tables)")
        return 0
    args.out.write_text(text, "utf-8")
    print(f"wrote {args.out} ({len(definitions()['dynamodb_tables'])} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
