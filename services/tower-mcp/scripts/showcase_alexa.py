#!/usr/bin/env python3
"""`make showcase-alexa` — the Alexa+ surface (testing-and-showcase §2.1 and §4 steps 4–6; prompt 15).

Alexa+ is the client here, so nothing in this script talks to Alexa+. It does the three things the person at
the keyboard needs while they speak into the web simulator:

1. **Prints the script**: five phrasings per tool, two off-topic, one ambiguous (from the reference client's
   corpus, `services/ref-client/corpus/phrasings.yaml`), then the three demo moments as the deck phrases them,
   with the mock admin curl lines to paste between utterances (second pane).
2. **Tails Tower's log** (`docker compose logs -f tower-mcp alerts`) and shows, per tool call, what Tower
   returned — and Alerts' `SMS to=` lines (the phone buzz).
3. **Captures Tower-side transcripts**: every `tower_mcp.calls` line (Tower with `TOWER_CALL_LOG=1`, local mode
   only — `deploy/compose/docker-compose.alexa.yml` sets it) becomes `artifacts/transcripts/alexa-NN-<tool>.json`.
   The utterance is not visible to Tower: fill `utterance` from `docs/architecture/alexa/simulator-run.md`.

Subcommands:

    uv run python services/tower-mcp/scripts/showcase_alexa.py [--print-only]     # script + tail + capture
    uv run python services/tower-mcp/scripts/showcase_alexa.py link --sub <SUB>   # Alexa-linked user = Asish
    uv run python services/tower-mcp/scripts/showcase_alexa.py collect <log>      # transcripts from a saved log

`link` makes the account-linked identity (the JWT `sub`, mapped by `tower_mcp.auth.safe_user_id` exactly as
Tower maps it) the holder of Asish's demo line and Mom's `watch` grantee under the alias "mom", re-using the
compose seed's one-tap binding flow. It moves Asish's line off `user-asish`: `make down && make up` restores the
reference-client demo. No number is printed anywhere; the curl lines resolve Mom's line from the mock's state.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.privacy.patterns import E164_STRICT  # noqa: E402 — the one home of the privacy regexes

CORPUS = ROOT / "services" / "ref-client" / "corpus" / "phrasings.yaml"
TRANSCRIPTS = ROOT / "artifacts" / "transcripts"
COMPOSE_FILES = ("deploy/compose/docker-compose.yml", "deploy/compose/docker-compose.alexa.yml")
CALL_LINE = re.compile(r"tower_mcp\.calls call (\{.*\})\s*$")
TOOLS = ("line_is_ok", "is_reachable", "watch_line")


# --- the script -------------------------------------------------------------------------------------------
def pick_utterances() -> dict[str, list[str]]:
    """§2.1: five per tool (demo-tagged first), two off-topic (one near-miss), one ambiguous."""
    import yaml

    entries: list[dict[str, Any]] = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))

    def tagged(tag: str) -> list[dict[str, Any]]:
        return [e for e in entries if tag in e.get("tags", [])]

    out: dict[str, list[str]] = {}
    for tool in TOOLS:
        rows = sorted(tagged(tool), key=lambda e: "demo" not in e["tags"])
        out[tool] = [e["say"] for e in rows[:5]]
    off = tagged("off-topic")
    out["off-topic"] = [off[0]["say"], next(e["say"] for e in off if "near-miss" in e["tags"])]
    out["ambiguous"] = [tagged("ambiguous")[0]["say"]]
    return out


def script(mock: str, binding: str, asish_user: str) -> str:
    u = pick_utterances()
    post = "curl -s -X POST -H 'content-type: application/json'"
    admin = f'{post} -H "Authorization: Bearer $MOCK_ADMIN_TOKEN"'  # G1: the mock's mutations need it
    mom_line = (
        f"MOM=$(curl -s '{mock}/_admin/state?view=refs' | python3 -c 'import json,sys; "
        'print(next(m for m, l in json.load(sys.stdin)["lines"].items() '
        'if "phone-mom" in l.get("mobile_data_client_ids", [])))\')'
    )
    grant = (
        f'{post} {binding}/_admin/grants -d \'{{"owner_user_id": "user-mom", '
        f'"grantee_user_id": "{asish_user}", "grant": "watch", "alias": "mom", "action": "%s"}}\''
    )
    lines = [
        "=== Alexa+ surface (testing-and-showcase §2.1, §4 steps 4–6) ===================================",
        "Simulator: the Alexa+ MCP Toolkit web simulator, signed in as the account linked in registration.md.",
        "Log pane  : this terminal (Tower's tool calls + Alerts' SMS lines). Admin pane: a second terminal.",
        "Record    : docs/architecture/alexa/simulator-run.md — what Alexa+ said, verbatim or paraphrased.",
        "",
        "A. Tool selection (§2.1). Before each: reset with",
        f'     {admin} {mock}/_admin/scenarios/load -d \'{{"name": "demo"}}\'',
    ]
    n = 0
    for group in (*TOOLS, "off-topic", "ambiguous"):
        expect = {"off-topic": "no tool call", "ambiguous": "a clarifying question (yours or Mom's?)"}.get(
            group, group
        )
        lines.append(f"   {group}  → expect {expect}")
        for say in u[group]:
            n += 1
            lines.append(f'     {n:>2}. "{say}"')
    lines += [
        "",
        "B. The three moments, as the deck phrases them (reset first, as above).",
        "   Moment 1",
        '     say  "Is my line OK?"                                   → OK',
        f"     admin {admin} {mock}/_admin/clock -d '{{\"advance_s\": 720}}'   # timeline: SIM swap",
        '     say  "My phone just lost signal. Alexa, is my line OK?"  → SIM_SWAPPED_RECENT',
        "   Moment 2",
        f"     admin {admin} {mock}/_admin/clock -d '{{\"advance_s\": 480}}'   # timeline: cf_set",
        '     say  "Is anything forwarding my calls?"                  → SIM_SWAPPED_RECENT, CALL_FORWARDING_SET',
        "   Moment 3",
        '     say  "Is Mom\'s line OK?"                                 → OK ("as it was")',
        '     say  "Watch Mom\'s line for me."                          → watch on',
        f"     admin {mom_line}",
        f'     admin {admin} {mock}/_admin/lines/$MOM/events -d \'{{"event": "sim_swap"}}\'   # SMS line below',
        f"     admin {grant % 'revoke'}",
        f'     admin {admin} {mock}/_admin/lines/$MOM/events -d \'{{"event": "sim_swap"}}\'   # no SMS',
        '     say  "Is Mom\'s line OK?"                                 → NO_CONSENT',
        f"     admin {grant % 'grant'}   # reset",
        "",
        "Transcripts: each Tower call below is written to artifacts/transcripts/alexa-NN-<tool>.json.",
        "==================================================================================================",
    ]
    return "\n".join(lines)


# --- capture ----------------------------------------------------------------------------------------------
def next_index() -> int:
    found = [
        int(m.group(1)) for p in TRANSCRIPTS.glob("alexa-*.json") if (m := re.match(r"alexa-(\d+)-", p.name))
    ]
    return max(found, default=0) + 1


def capture(line: str, index: int) -> Path | None:
    """One `tower_mcp.calls` log line → one transcript file. Refuses (prints) anything number-shaped."""
    m = CALL_LINE.search(line)
    if not m:
        return None
    entry = json.loads(m.group(1))
    if E164_STRICT.search(m.group(1)):
        print("showcase-alexa: a call line matched the number pattern; not written", file=sys.stderr)
        return None
    record = {
        "source": "tower",  # captured from Tower's side; Alexa+'s words are recorded in simulator-run.md
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "utterance": None,
        "spoken": None,
        "user_id": entry["user_id"],
        "tool_calls": [{"name": entry["tool"], "args": entry["args"]}],
        "result": entry["result"],
        "reason_codes": entry["result"].get("reason_codes"),
    }
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPTS / f"alexa-{index:02d}-{entry['tool']}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    result = entry["result"]
    print(
        f"  ↳ {entry['tool']} {json.dumps(entry['args'])} → {','.join(result.get('reason_codes') or ['error'])}: "
        f"{result.get('summary', '')}   [{path.relative_to(ROOT)}]",
        flush=True,
    )
    return path


def tail() -> int:
    if not shutil.which("docker"):
        print("showcase-alexa: docker not found; start the stack (make up) and re-run", file=sys.stderr)
        return 2
    cmd = ["docker", "compose"]
    for f in COMPOSE_FILES:
        cmd += ["-f", str(ROOT / f)]
    cmd += ["logs", "-f", "--since", "0s", "--no-color", "tower-mcp", "alerts"]
    print("tailing: " + " ".join(cmd[cmd.index("logs") :]) + "   (Ctrl-C stops)\n", flush=True)
    index = next_index()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # noqa: S603
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if capture(line, index):
                index += 1
            elif "SMS to=" in line or " refused " in line or "failed" in line:
                print(E164_STRICT.sub("<redacted>", line.rstrip()), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    return 0


def collect(log: Path) -> int:
    index = next_index()
    for line in log.read_text(encoding="utf-8").splitlines():
        if capture(line, index):
            index += 1
    return 0


# --- link -------------------------------------------------------------------------------------------------
def load_compose_seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "compose_seed", ROOT / "deploy" / "compose" / "seed" / "seed.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def link(sub: str) -> int:
    import httpx
    from tower_consent import list_lines, tables
    from tower_mcp.auth import safe_user_id

    os.environ.setdefault("MOCK_URL", "http://localhost:8443")
    os.environ.setdefault("BINDING_URL", "http://localhost:8081")
    os.environ.setdefault("DYNAMO_ENDPOINT", "http://localhost:8000")
    os.environ.setdefault("AWS_REGION", "us-east-1")
    user = safe_user_id(sub)
    seed = load_compose_seed()
    seed.PEOPLE = ((user, "phone-asish"), ("user-mom", "phone-mom"))
    try:
        with (
            httpx.Client(base_url=seed.MOCK_URL, timeout=15.0, headers=seed.mock_admin_headers()) as mock,
            httpx.Client(base_url=seed.BINDING_URL, timeout=30.0, follow_redirects=False) as page,
        ):
            seed.wait_for(mock, "/healthz")
            seed.wait_for(page, "/healthz")
            store = seed.local_store()
            seed.ensure_tables_and_users(store)  # also creates the users in PEOPLE
            moved = [ln.line_id for ln in list_lines(store, "user-asish")] if user != "user-asish" else []
            for line_id in moved:  # Asish's demo line changes holder: the linked Alexa identity is Asish now
                store.delete(tables.LINES, {"line_id": line_id})
            seed.load_scenario(mock)
            for who, client_id in seed.PEOPLE:
                seed.bind(page, who, client_id)
            seed.grant(page, "user-mom", user, "mom")
            for line in ("self", "mom"):
                r = page.get("/_admin/resolve", params={"user": user, "line": line})
                r.raise_for_status()
                v = r.json()
                print(f"  {user} asks about {line:<5} bound={v.get('bound')} grant={v.get('grant')}")
    except (seed.SeedError, httpx.HTTPError) as e:
        print(f"showcase-alexa link: FAILED: {e}", file=sys.stderr)
        return 1
    print(f"linked: Tower user_id {user!r} holds Asish's demo line and Mom's watch grant ('mom').")
    print("Revoke/grant curl lines: run this script with --user " + user)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--print-only", action="store_true", help="print the script; no log tail")
    ap.add_argument(
        "--user", default=os.environ.get("ALEXA_TOWER_USER", "user-asish"), help="Asish's user_id"
    )
    lk = sub.add_parser("link", help="make the account-linked identity Asish (bind + Mom's grant)")
    lk.add_argument("--sub", required=True, help="the `sub` claim of the linked account's token")
    co = sub.add_parser("collect", help="turn `tower_mcp.calls` lines in a saved log into transcripts")
    co.add_argument("log", type=Path)
    args = ap.parse_args()
    if args.cmd == "link":
        return link(args.sub)
    if args.cmd == "collect":
        return collect(args.log)
    mock = os.environ.get("MOCK_URL", "http://localhost:8443")
    binding = os.environ.get("BINDING_URL", "http://localhost:8081")
    print(script(mock, binding, args.user), flush=True)
    if args.print_only:
        return 0
    return tail()


if __name__ == "__main__":
    sys.exit(main())
