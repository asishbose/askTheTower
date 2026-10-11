#!/usr/bin/env python3
"""Publish the web chat page (`make web-chat-sync`, deployment-agentcore.md step 10) and print its URL
(`make web-chat-url`).

1. Render `config.js` from the Terraform outputs (`artifacts/tf-outputs.json`): `COGNITO_DOMAIN` =
   `cognito_hosted_ui_url`, `CLIENT_ID` = `cognito_client_id`, `REDIRECT_URI` = `web_chat_url`, `AGENT_URL` =
   `agent_url`, `BINDING_BASE_URL` = `binding_url` (09 §6.4). None of them is a secret.
2. Upload `index.html`, `app.js`, `styles.css` and the rendered `config.js` to `web_chat_bucket` (no-cache on
   `config.js` and `index.html`, so a re-deploy is picked up at once).
3. Invalidate `/*` on `web_chat_distribution_id`.

    uv run python scripts/web_chat_sync.py              # render, upload, invalidate
    uv run python scripts/web_chat_sync.py --dry-run    # print config.js and what would be uploaded; touches nothing
    uv run python scripts/web_chat_sync.py --url        # print the page URL
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402

PAGE = ROOT / "services" / "web-chat"
FILES = {  # file → (content type, cache control)
    "index.html": ("text/html; charset=utf-8", "no-cache"),
    "app.js": ("text/javascript; charset=utf-8", "max-age=300"),
    "styles.css": ("text/css; charset=utf-8", "max-age=300"),
}
CONFIG_KEYS = {
    "COGNITO_DOMAIN": "cognito_hosted_ui_url",
    "CLIENT_ID": "cognito_client_id",
    "REDIRECT_URI": "web_chat_url",
    "AGENT_URL": "agent_url",
    "BINDING_BASE_URL": "binding_url",
}


def render_config(outputs: dict[str, Any]) -> str:
    missing = [o for o in CONFIG_KEYS.values() if not outputs.get(o)]
    if missing:
        raise SystemExit(
            f"outputs missing {missing}: deploy with enable_web_chat = true, then `make outputs`"
        )
    cfg = {
        key: str(outputs[out]).rstrip("/") if key == "BINDING_BASE_URL" else str(outputs[out])
        for key, out in CONFIG_KEYS.items()
    }
    return f"window.WEB_CHAT_CONFIG = {json.dumps(cfg, indent=2)};\n"


def objects(config_js: str) -> list[tuple[str, bytes, str, str]]:
    """(key, body, content type, cache control) for every object the page needs."""
    out = [(name, (PAGE / name).read_bytes(), ctype, cache) for name, (ctype, cache) in FILES.items()]
    out.append(("config.js", config_js.encode("utf-8"), "text/javascript; charset=utf-8", "no-cache"))
    return out


def sync(outputs: dict[str, Any], *, dry_run: bool, s3: Any = None, cloudfront: Any = None) -> None:
    bucket, distribution = outputs.get("web_chat_bucket"), outputs.get("web_chat_distribution_id")
    if not bucket or not distribution:
        raise SystemExit(
            "no web_chat_bucket / web_chat_distribution_id in the outputs (enable_web_chat = false?)"
        )
    config_js = render_config(outputs)
    if dry_run:
        print(config_js, end="")
    for key, body, ctype, cache in objects(config_js):
        print(f"{'would upload' if dry_run else 'upload'} s3://{bucket}/{key} ({len(body)} bytes, {cache})")
        if not dry_run:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype, CacheControl=cache)
    print(f"{'would invalidate' if dry_run else 'invalidate'} /* on {distribution}")
    if not dry_run:
        cloudfront.create_invalidation(
            DistributionId=distribution,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/*"]},
                "CallerReference": f"web-chat-{time.time_ns()}",
            },
        )
    print(f"web chat: {outputs['web_chat_url']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url", action="store_true", help="only print the page URL")
    args = ap.parse_args(argv)
    outputs = load_outputs(args.outputs)
    if args.url:
        url = outputs.get("web_chat_url")
        if not url:
            raise SystemExit(
                "no web_chat_url in the outputs (enable_web_chat = false, or run `make outputs`)"
            )
        print(url)
        return 0
    if args.dry_run:
        sync(outputs, dry_run=True)
        return 0
    import boto3

    region = str(outputs["region"])
    sync(
        outputs,
        dry_run=False,
        s3=boto3.client("s3", region_name=region),
        cloudfront=boto3.client("cloudfront"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
