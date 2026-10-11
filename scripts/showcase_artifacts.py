#!/usr/bin/env python3
"""`make showcase-artifacts`: regenerate every generated artefact in one go, then check none is stale.

    uv run python scripts/showcase_artifacts.py [--skip-regen] [--no-write]

Three kinds of artefact (testing-and-showcase.md §6, prompt 16's Deliverables):

* **generated** — rebuilt here from code: the policy table, the conformance report, the test report (from the
  junit files the test layers wrote), the deck-consistency table, and — only when the local stack is up — the
  demo transcripts (`make demo`). The corpus is generated too, but needs Bedrock: without AWS credentials it is
  reported as deferred, not run.
* **measured** — produced by a timed run (latency, clean-machine timing, helm-kind). Not re-run here (they take
  minutes and measure a machine); checked for staleness only.
* **deferred** — needs a human, a phone, or AWS (screenshots, the video, the AWS latency, the EKS run, the plan,
  billing). Reported with the command that produces it; never fails the run.

Staleness: an artefact is stale when its timestamp is more than 24 h older than the code it comes from (the
newest commit, or uncommitted edit, under packages/ services/ specs/ scenarios/ deploy/compose deploy/helm). The
artefact's timestamp is the date written inside it when it has one, else its last commit (if unmodified), else
its mtime.

Every step runs even if an earlier one fails. The deck-check result is spliced into
docs/submission/deck-consistency.md and reported, but open deck rows do not fail this target (`make deck-check`
is that gate; the fixes are deck-side and listed in that file). The status table is spliced into
docs/submission/checklist.md.

Exit status: 1 if a generated step failed or any present artefact is stale; else 0.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
CHECKLIST = ROOT / "docs/submission/checklist.md"
DECK_MD = ROOT / "docs/submission/deck-consistency.md"
CODE_PATHS = ("packages", "services", "specs", "scenarios", "deploy/compose", "deploy/helm")
STALE_AFTER = timedelta(hours=24)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?(?:Z| UTC)")
NOT_RUN_RE = re.compile(r"NOT (?:RUN|MEASURED)")
BEGIN, END = "<!-- showcase-artifacts:begin -->", "<!-- showcase-artifacts:end -->"
DECK_BEGIN, DECK_END = "<!-- deck-check:begin -->", "<!-- deck-check:end -->"


@dataclass(frozen=True)
class Artefact:
    path: str  # relative to ROOT; a trailing /*.json is a glob
    kind: str  # generated | measured | deferred
    command: str  # what produces it


ARTEFACTS: tuple[Artefact, ...] = (
    Artefact("artifacts/policy-table.md", "generated", "make policy-table"),
    Artefact(
        "artifacts/conformance-report.html",
        "generated",
        "uv run pytest services/mock-carrier/tests/test_conformance.py",
    ),
    Artefact(
        "artifacts/test-report.md",
        "generated",
        "make test (or: uv run python scripts/test_report.py --no-gate)",
    ),
    Artefact(
        "docs/submission/deck-consistency.md",
        "generated",
        "make deck-check (table spliced by make showcase-artifacts)",
    ),
    Artefact("artifacts/transcripts/*.json", "generated", "make up && make demo"),
    Artefact(
        "artifacts/corpus.md",
        "deferred",
        "make corpus (needs Bedrock: AWS credentials with Nova Micro access)",
    ),
    Artefact(
        "artifacts/latency.md",
        "measured",
        "uv run python scripts/latency.py --out artifacts/latency.md (keep its hand-added section)",
    ),
    Artefact(
        "artifacts/latency-compose.md",
        "measured",
        "make up && uv run python scripts/latency.py --url http://localhost:8080/mcp … --out artifacts/latency-compose.md",
    ),
    Artefact(
        "artifacts/clean-run.txt",
        "measured",
        "make showcase-infra (scripts/clean_machine.sh) — on a clean machine",
    ),
    Artefact("artifacts/helm-kind.txt", "measured", "make helm-kind"),
    Artefact(
        "artifacts/cost.md",
        "measured",
        "hand-written estimate; replace with Cost Explorer after a real deploy",
    ),
    Artefact("artifacts/latency-aws.md", "deferred", "make deploy ENV=aws && make latency-aws"),
    Artefact(
        "artifacts/eks-run.txt", "deferred", "make deploy-eks && make demo ENV=eks && FORCE=1 make down-eks"
    ),
    Artefact("artifacts/terraform-plan.txt", "deferred", "make plan ENV=aws"),
    Artefact(
        "artifacts/billing-zero.png",
        "deferred",
        "make down ENV=aws, then a Cost Explorer / Billing screenshot",
    ),
    Artefact(
        "artifacts/sms-screenshot.png",
        "deferred",
        "make showcase-alerts ENV=aws on a real phone (number masked)",
    ),
    Artefact(
        "artifacts/audit-screenshot.png",
        "deferred",
        "make showcase-audit (Mom's view, chain verified) — screenshot",
    ),
    Artefact(
        "artifacts/alexa-simulator.mp4",
        "deferred",
        "make showcase-alexa against the Alexa+ web simulator (US)",
    ),
    Artefact("artifacts/video/ask-the-tower-demo.mp4", "deferred", "record per artifacts/video/script.md"),
)


def run(cmd: list[str], env: dict[str, str] | None = None, label: str = "") -> bool:
    print(f"\n── {label or ' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})})  # noqa: S603 (fixed argv)
    print(f"   → exit {r.returncode}", flush=True)
    return r.returncode == 0


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)  # noqa: S603, S607
    return r.stdout.strip() if r.returncode == 0 else ""


def stack_up() -> bool:
    r = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", "deploy/compose/docker-compose.yml", "ps", "--status", "running"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and "tower-mcp" in r.stdout


def aws_credentials() -> bool:
    return (
        any(os.environ.get(k) for k in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID"))
        or (Path.home() / ".aws/credentials").exists()
    )


def splice(path: Path, begin: str, end: str, body: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = f"{begin}\n{body.rstrip()}\n{end}"
    if begin in text and end in text:
        text = text[: text.index(begin)] + block + text[text.index(end) + len(end) :]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


# ── timestamps ──────────────────────────────────────────────────────────────────────────────────────────────


def dirty(paths: tuple[str, ...]) -> list[Path]:
    out = git("status", "--porcelain", "--untracked-files=all", "--", *paths)
    return [ROOT / ln[3:].split(" -> ")[-1].strip('"') for ln in out.splitlines() if ln[3:]]


def code_time() -> datetime:
    ts = [int(t) for t in [git("log", "-1", "--format=%ct", "--", *CODE_PATHS)] if t]
    ts += [int(p.stat().st_mtime) for p in dirty(CODE_PATHS) if p.is_file()]
    return datetime.fromtimestamp(max(ts) if ts else 0, UTC)


def file_time(p: Path) -> tuple[datetime, str]:
    if p.suffix in {".md", ".txt", ".html", ".log"}:
        head = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:20])
        m = DATE_RE.search(head)
        if m:
            return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:00+00:00"), "date inside"
    rel = str(p.relative_to(ROOT))
    if not dirty((rel,)):
        t = git("log", "-1", "--format=%ct", "--", rel)
        if t:
            return datetime.fromtimestamp(int(t), UTC), "last commit"
    return datetime.fromtimestamp(p.stat().st_mtime, UTC), "mtime"


@dataclass
class Status:
    art: Artefact
    state: str  # fresh | STALE | deferred | missing | placeholder
    when: str
    note: str = ""


def assess(a: Artefact, code: datetime, regenerated: frozenset[str] = frozenset()) -> Status:
    """State of one artefact; one this run just rebuilt is fresh even when its bytes (and commit) didn't change."""
    files = sorted(ROOT.glob(a.path)) if "*" in a.path else [ROOT / a.path]
    files = [f for f in files if f.exists()]
    if not files:
        return Status(a, "deferred" if a.kind == "deferred" else "missing", "–")
    if a.kind == "deferred":
        head = (
            files[0].read_text(encoding="utf-8", errors="replace")[:600]
            if files[0].suffix in {".md", ".txt"}
            else ""
        )
        if NOT_RUN_RE.search(head):
            return Status(a, "deferred", "–", "placeholder says NOT RUN / NOT MEASURED")
    if a.path in regenerated:
        return Status(a, "fresh", f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC (regenerated now)")
    times = [file_time(f) for f in files]
    oldest, how = min(times)
    stale = oldest < code - STALE_AFTER
    return Status(a, "STALE" if stale else "fresh", f"{oldest:%Y-%m-%d %H:%M} UTC ({how})")


# ── main ────────────────────────────────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the generated artefacts; check staleness")
    ap.add_argument("--skip-regen", action="store_true", help="only check staleness")
    ap.add_argument("--no-write", action="store_true", help="don't splice tables into docs/submission/")
    args = ap.parse_args()
    py = sys.executable
    failed: list[str] = []
    done: set[str] = set()  # artefact paths regenerated successfully in this run
    notes: list[str] = []
    deck_open = "not run"

    if not args.skip_regen:
        if not run(
            [py, "-m", "pytest", "packages/tower-policy/tests/test_table.py", "-q", "-p", "no:cacheprovider"],
            env={"POLICY_TABLE_OUT": "artifacts/policy-table.md"},
            label="policy table → artifacts/policy-table.md",
        ):
            failed.append("policy-table")
        else:
            done.add("artifacts/policy-table.md")
        if not run(
            [
                py,
                "-m",
                "pytest",
                "services/mock-carrier/tests/test_conformance.py",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            label="conformance (schemathesis, in-process) → artifacts/conformance-report.html",
        ):
            failed.append("conformance")
        else:
            done.add("artifacts/conformance-report.html")
        if stack_up():
            if not run(["make", "--no-print-directory", "demo"], label="make demo → artifacts/transcripts/"):
                failed.append("demo")
            else:
                done.add("artifacts/transcripts/*.json")
        else:
            notes.append("transcripts not regenerated: local stack not running (`make up && make demo`)")
        if aws_credentials() and stack_up():
            if not run(["make", "--no-print-directory", "corpus"], label="make corpus → artifacts/corpus.md"):
                failed.append("corpus")
        else:
            notes.append("corpus not run: needs Bedrock (AWS credentials) and the stack")
        if not run(
            [py, "scripts/test_report.py", "--no-gate"], label="test report → artifacts/test-report.md"
        ):
            failed.append("test-report")
        else:
            done.add("artifacts/test-report.md")

    # deck-check: run it for the table; open rows are reported, never abort the rest
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "deck.md"
        print("\n── deck-check → docs/submission/deck-consistency.md", flush=True)
        r = subprocess.run(  # noqa: S603
            [py, "scripts/deck_check.py", "--out", str(out)], cwd=ROOT, capture_output=True, text=True
        )
        print(r.stdout.rstrip())
        if out.exists():
            table = out.read_text(encoding="utf-8")
            n_open = table.count("| OPEN |")
            deck_open = f"{n_open} open" if n_open else "0 open"
            if not args.no_write:
                stamp = f"Last run: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC — {deck_open}.\n\n"
                splice(DECK_MD, DECK_BEGIN, DECK_END, stamp + table)
        else:
            failed.append("deck-check")
            print(r.stderr.rstrip())

    code = code_time()
    statuses = [assess(a, code, frozenset(done)) for a in ARTEFACTS]
    stale = [s for s in statuses if s.state in {"STALE", "missing"} and s.art.kind != "deferred"]

    rows = [
        f"Generated by `make showcase-artifacts` at {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. "
        f"Code timestamp: {code:%Y-%m-%d %H:%M} UTC (newest change under {', '.join(CODE_PATHS)}); "
        f"stale = more than 24 h older than that. Deck check: {deck_open}.",
        "",
        "| Artefact | Kind | State | Timestamp | Produced by |",
        "|---|---|---|---|---|",
    ]
    for s in statuses:
        state = s.state + (f" ({s.note})" if s.note else "")
        rows.append(f"| `{s.art.path}` | {s.art.kind} | {state} | {s.when} | `{s.art.command}` |")
    if notes:
        rows += ["", *[f"- {n}" for n in notes]]
    table = "\n".join(rows)
    print("\n" + table)
    if not args.no_write and CHECKLIST.exists():
        splice(CHECKLIST, BEGIN, END, table)

    for f in failed:
        print(f"showcase-artifacts: step failed: {f}")
    for s in stale:
        print(f"showcase-artifacts: {s.state}: {s.art.path} — rerun: {s.art.command}")
    print(
        f"showcase-artifacts: {len(failed)} failed step(s), {len(stale)} stale/missing, "
        f"{sum(s.state == 'deferred' for s in statuses)} deferred, deck-check {deck_open}"
        + (
            " (see docs/submission/deck-consistency.md; `make deck-check` is the gate)"
            if deck_open != "0 open"
            else ""
        )
    )
    return 1 if failed or stale else 0


if __name__ == "__main__":
    sys.exit(main())
