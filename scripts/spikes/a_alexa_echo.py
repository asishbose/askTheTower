#!/usr/bin/env python3
"""Spike A (prompt 02): the smallest MCP server Alexa+ can call, logging every inbound request verbatim.

Throwaway evidence code. Nothing in `packages/` or `services/` imports it (tests/spikes asserts that).

Two tools over Streamable HTTP (stateless, JSON responses — the same transport settings as Tower):
- `echo(text)` → `{"echo": text}`: proves registration and a round trip from the Alexa+ web simulator.
- `summary_probe()` → `{"summary": PROBE_SUMMARY, "facts": {...}}`: does Alexa+ read `summary` verbatim or
  paraphrase it? Compare the spoken answer in the simulator with `PROBE_SUMMARY` word for word.

Every request to `/mcp` is appended to `--capture` (JSONL): method, path, headers, JSON body. Secrets are
redacted before anything is written: the `Authorization` value is replaced by its *shape* (scheme, and for a
JWT the header and claim names plus a few non-identifying claim facts), cookies and API-key-like headers by
`<redacted>`, and any 10-15 digit run by `<digits>` (tests/privacy/patterns.py). `--analyse` reads a capture
back and says whether Tower's coded identity assumption (01 §4, `tower_mcp/auth.py`) holds.

    uv run python scripts/spikes/a_alexa_echo.py serve --port 8765                  # then tunnel it
    uv run python scripts/spikes/a_alexa_echo.py analyse artifacts/spikes/A-capture.jsonl
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.privacy.patterns import E164_STRICT  # noqa: E402 — the one home of privacy regexes

DEFAULT_CAPTURE = ROOT / "artifacts" / "spikes" / "A-capture.jsonl"
PROBE_SUMMARY = "Your line looks fine. Nothing has changed on it in the last seven days."
SECRET_HEADERS = {"cookie", "set-cookie", "x-api-key", "x-amz-security-token", "proxy-authorization"}
# Claim facts safe to keep: they describe the token, not the person.
SAFE_CLAIMS = {"iss", "aud", "exp", "iat", "nbf", "token_use", "scope", "azp", "client_id"}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


# --- redaction ----------------------------------------------------------------------------------------
def _b64json(part: str) -> dict[str, Any] | None:
    try:
        raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        out = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    return out if isinstance(out, dict) else None


def token_shape(value: str) -> dict[str, Any]:
    """Describe an Authorization header value without keeping the secret."""
    scheme, _, cred = value.partition(" ")
    shape: dict[str, Any] = {"scheme": scheme or None, "length": len(cred)}
    parts = cred.split(".")
    header = _b64json(parts[0]) if len(parts) == 3 else None
    claims = _b64json(parts[1]) if len(parts) == 3 else None
    if header is None or claims is None:
        shape["jwt"] = False
        return shape
    sub = claims.get("sub")
    shape |= {
        "jwt": True,
        "header": {k: header[k] for k in ("alg", "typ", "kid") if k in header},
        "claim_names": sorted(claims),
        "claims": {k: redact_text(json.dumps(claims[k])) for k in sorted(SAFE_CLAIMS & set(claims))},
        "sub": None
        if sub is None
        else {"type": type(sub).__name__, "length": len(str(sub)), "prefix": str(sub)[:12]},
    }
    return shape


def redact_text(text: str) -> str:
    return E164_STRICT.sub("<digits>", text)


def redact_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in headers:
        name, value = k.decode("latin-1").lower(), v.decode("latin-1")
        if name == "authorization":
            out[name] = token_shape(value)
        elif name in SECRET_HEADERS or "secret" in name or "token" in name:
            out[name] = "<redacted>"
        else:
            out[name] = redact_text(value)
    return out


# --- capture middleware -------------------------------------------------------------------------------
class CaptureMiddleware:
    """Pure ASGI: buffer the request body, record it, then replay it to the app unchanged."""

    def __init__(self, app: Any, capture: Path, protected: str = "/mcp") -> None:
        self.app, self.capture, self.protected = app, capture, protected
        capture.parent.mkdir(parents=True, exist_ok=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.protected):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        try:
            parsed: Any = json.loads(redact_text(body.decode("utf-8"))) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"unparsed_bytes": len(body)}
        record = {
            "at": datetime.now(UTC).isoformat(),
            "method": scope["method"],
            "path": scope["path"],
            "query": redact_text(scope.get("query_string", b"").decode("latin-1")),
            "headers": redact_headers(scope["headers"]),
            "body": parsed,
        }
        with self.capture.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        sent = False

        async def replay() -> MutableMapping[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(capture: Path = DEFAULT_CAPTURE) -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP("spike-a-echo")

    @mcp.tool(name="echo", description="Repeat back what the user said. Use for 'say back <words>'.")
    def echo(text: str) -> dict[str, str]:
        return {"echo": text}

    @mcp.tool(
        name="summary_probe",
        description="Check whether my phone line is okay. Use for 'is my line okay', 'check my phone line'.",
    )
    def summary_probe() -> dict[str, Any]:
        return {
            "summary": PROBE_SUMMARY,
            "facts": {"sim_swapped_recently": False, "call_forwarding": "none", "line": "self"},
        }

    app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app.add_middleware(CaptureMiddleware, capture=capture)
    return app


# --- analysis -----------------------------------------------------------------------------------------
def analyse(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Test the identity assumption coded in Tower against what Alexa+ actually sent on `tools/call`."""
    calls = [
        r for r in records if isinstance(r.get("body"), dict) and r["body"].get("method") == "tools/call"
    ]
    auth = [r["headers"].get("authorization") for r in calls]
    meta = [r["body"].get("params", {}).get("_meta") for r in calls]
    jwts = [a for a in auth if isinstance(a, dict) and a.get("jwt")]
    with_sub = [a for a in jwts if a.get("sub")]
    other_ids = sorted(
        {h for r in calls for h in r["headers"] if any(w in h for w in ("user", "account", "amzn", "alexa"))}
    )
    if not calls:
        verdict = "NO DATA: no tools/call captured — invoke a tool from the simulator first"
    elif calls and len(with_sub) == len(calls) and all(a.get("scheme", "").lower() == "bearer" for a in jwts):
        verdict = "HOLDS: every tools/call carried Authorization: Bearer <JWT> with a sub claim"
    elif jwts:
        verdict = "PARTLY: a JWT arrived but not on every call or without sub — see auth shapes"
    else:
        verdict = "REFUTED: no bearer JWT on tools/call — identity is elsewhere (see headers/_meta) or absent"
    return {
        "tools_calls": len(calls),
        "auth_shapes": auth,
        "meta": meta,
        "identity_like_headers": other_ids,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    a = sub.add_parser("analyse")
    a.add_argument("capture", type=Path, nargs="?", default=DEFAULT_CAPTURE)
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        print(
            f"spike A: capturing to {args.capture}; expose with a tunnel, register <url>/mcp", file=sys.stderr
        )
        uvicorn.run(create_app(args.capture), host=args.host, port=args.port)
        return 0
    records = [json.loads(line) for line in args.capture.read_text("utf-8").splitlines() if line.strip()]
    print(json.dumps(analyse(records), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
