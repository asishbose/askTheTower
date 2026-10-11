#!/usr/bin/env python3
"""Check the Gateway registration and write gateway-tools.json (`make register-gateway`, part of `make deploy`).

Terraform registers the specs: modules/agentcore_gateway creates one Gateway target per specs/camara/*.yaml
(OpenAPI → MCP tools, servers rewritten to the carrier URL). This script then asks the deployed Gateway for its
tools over MCP (SigV4, the same path Tower uses), maps them onto the CAMARA operations with
`camara_client.gateway.resolve_tool_names`, prints the list (the §2.5 showcase: CAMARA operations as MCP tools)
and writes the manifest `GatewayClient` reads (`CARRIER_GATEWAY_TOOLS`). Services on AWS use
`CARRIER_GATEWAY_TOOLS=discover` and resolve the same names at warm-up; the file is the record and the input for
local runs against the AWS Gateway.

    uv run python scripts/register_gateway.py                    # outputs from artifacts/tf-outputs.json
    uv run python scripts/register_gateway.py --check            # fail if the deployed tools differ from the file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402

DEFAULT_OUT = ROOT / "artifacts" / "gateway-tools.json"


async def list_tools(gateway_url: str, region: str) -> list[str]:
    from camara_client.aws import SigV4Auth
    from fastmcp import Client

    async with Client(gateway_url, auth=SigV4Auth(region)) as client:
        return sorted(t.name for t in await client.list_tools())


def main(argv: list[str] | None = None) -> int:
    from camara_client import resolve_tool_names, tools_manifest

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="compare with --out instead of writing it")
    args = ap.parse_args(argv)

    o = load_outputs(args.outputs)
    url, region = str(o["gateway_url"]), str(o["region"])
    listed = asyncio.run(list_tools(url, region))
    print(f"Gateway {url}: {len(listed)} tools")
    for name in listed:
        print(f"  {name}")
    try:
        names = resolve_tool_names(listed)
    except ValueError as e:
        print(f"registration incomplete: {e}", file=sys.stderr)
        return 1
    manifest = tools_manifest(names, gateway=url)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.check:
        current = args.out.read_text("utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} differs from the deployed Gateway: re-run without --check", file=sys.stderr)
            return 1
        print(f"{args.out.name}: matches the deployed Gateway")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, "utf-8")
    print(f"wrote {args.out} ({len(names)} operations mapped; backend {o.get('carrier_backend')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
