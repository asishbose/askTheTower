#!/usr/bin/env python3
"""Call one tool on the deployed AgentCore Gateway and print the raw answer — the diagnostic for
`CARRIER_ERROR (malformed)`, which is what the client reports when the Gateway answers with its own error
(Identity could not get a token, the target is unreachable, the spec and the mock disagree, the arguments
have the wrong shape) rather than a CAMARA error body. The clients never log that text (it may echo
arguments); this script prints it, with any phone number masked. Arguments are sent flat, as the Gateway
expects (`--schema` shows what it lists; `--wrapped` reproduces the first-apply failure).

    uv run python scripts/gateway_probe.py                       # sim-swap___checkSimSwap on the first demo line
    uv run python scripts/gateway_probe.py --tool call-forwarding-signal___retrieveCallForwarding
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml
from ref_client.privacy import mask  # the one shared number rule (tests/privacy single source)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402


async def probe(url: str, region: str, tool: str, args: dict, *, schema: bool) -> None:
    from camara_client.aws import SigV4Auth
    from fastmcp import Client

    async with Client(url, auth=SigV4Auth(region)) as client:
        if schema:
            for t in await client.list_tools():
                if t.name == tool:
                    print("inputSchema:", json.dumps(getattr(t, "inputSchema", None) or getattr(t, "input_schema", None), indent=2))
        print("arguments sent:", mask(json.dumps(args)))
        result = await client.call_tool(tool, args, raise_on_error=False)
        print(f"tool: {tool}\nis_error: {result.is_error}")
        for c in result.content:
            text = getattr(c, "text", None)
            print(mask(text if text is not None else repr(c)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", default="sim-swap___checkSimSwap")
    ap.add_argument("--max-age", type=int, default=240)
    ap.add_argument("--schema", action="store_true", help="print the tool's inputSchema as the Gateway lists it")
    ap.add_argument("--wrapped", action="store_true", help="send the body under a 'body' key (the pre-2026-10-13 client shape; the Gateway answers 'internal error')")
    args = ap.parse_args(argv)
    o = load_outputs()
    scenario = yaml.safe_load((ROOT / "scenarios" / "demo.yaml").read_text("utf-8"))
    number = next(iter(scenario["lines"]))
    if "checkSimSwap" in args.tool:
        body = {"phoneNumber": number, "maxAge": args.max_age}
    elif "retrieveSimSwapDate" in args.tool:
        body = {"phoneNumber": number}
    else:
        body = {"device": {"phoneNumber": number}}
    asyncio.run(probe(str(o["gateway_url"]), str(o["region"]), args.tool, {"body": body} if args.wrapped else body, schema=args.schema))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
