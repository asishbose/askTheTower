#!/usr/bin/env python3
"""`make admin-open` / `make admin-close`: the one `demo_admin_cidrs` line of `deploy/terraform/envs/aws.tfvars`.

Doc 11 §2 "admin-open / admin-close", doc 10 §5:

- The file must exist (absent → exit 2; the `.example` is never edited).
- The one line `demo_admin_cidrs = …` becomes `demo_admin_cidrs = ["a.b.c.d/32"] # managed by make admin-open /
  admin-close` (open) or `demo_admin_cidrs = [] # managed …` (close); no such line → it is appended. More than one,
  or a list spread over several lines → exit 2, nothing written. Every other byte is kept; the write is atomic
  (temp file in the same directory, `os.replace`) and keeps the file's mode.
- open takes `--cidr` (`ADMIN_CIDR`), or, when empty, the body of `curl -fsS --max-time 5
  https://checkip.amazonaws.com` with `/32`; it must be a public IPv4 `/32` (`is_global`), else exit 2.
- `plan-gate` reads the summary line of `artifacts/terraform-plan.txt` (the regex of the demo UI's `ops/plan.py`):
  a plan that destroys anything, or whose summary cannot be read, stops with exit 3 unless `--force`. The one
  expected roll is let through: when every destroyed or replaced address is one of `ROLL` (the bearer password, the
  mock task definition and its secret version, which changing `demo_admin_cidrs` always replaces), the gate prints
  "rolls the demo admin bearer and the mock task" and goes on to `make deploy`.

The address is never printed: only "demo_admin_cidrs: 1 address" / "demo_admin_cidrs: []".

    uv run python scripts/admin_cidr.py open [--cidr a.b.c.d[/32]] [--file F]
    uv run python scripts/admin_cidr.py close [--file F]
    uv run python scripts/admin_cidr.py plan-gate --name admin-open [--force] [--plan F]
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TFVARS = ROOT / "deploy" / "terraform" / "envs" / "aws.tfvars"
PLAN = ROOT / "artifacts" / "terraform-plan.txt"
MARKER = "# managed by make admin-open / admin-close"
LINE = re.compile(r"^\s*demo_admin_cidrs\s*=")
SUMMARY = re.compile(
    r"^(?:Plan: (?:(\d+) to import, )?(\d+) to add, (\d+) to change, (\d+) to destroy\.|No changes\.)", re.M
)
# What changing demo_admin_cidrs always replaces (doc 10 §2.1): the gate lets a plan through whose destroys are
# exactly these, so `make admin-open` deploys in one go. Anything else destroyed still stops it.
ROLL = frozenset(
    {
        "random_password.demo_admin_bearer[0]",
        "module.mock_carrier[0].aws_ecs_task_definition.this",
        "module.mock_carrier[0].aws_secretsmanager_secret_version.registry",
    }
)
ACTION = re.compile(r"^\s*# (?P<addr>\S+) (?:must be replaced|will be destroyed)$", re.M)
CHECKIP = ("curl", "-fsS", "--max-time", "5", "https://checkip.amazonaws.com")
MISSING = "deploy/terraform/envs/aws.tfvars not found: copy envs/aws.tfvars.example first"
BY_HAND = "edit demo_admin_cidrs by hand to one line"
NOT_A_32 = "ADMIN_CIDR is not a public IPv4 /32"


class Refused(Exception):
    """Exit 2 with this message; nothing was written."""


def public_slash32(value: str) -> str | None:
    """The normalised `a.b.c.d/32` of a public IPv4 address (a bare address gets `/32`), or None."""
    text = value.strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "/" not in text:
        text += "/32"
    try:
        net = ipaddress.IPv4Network(text, strict=True)
    except ValueError:
        return None
    if net.prefixlen != 32 or not net.network_address.is_global:
        return None
    return str(net)


def checkip() -> str:
    """The caller's address as seen from AWS; "" when curl fails (the caller then refuses)."""
    try:
        out = subprocess.run(CHECKIP, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603 - fixed argv
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def rewrite(text: str, value: str) -> str:
    """`text` with its one `demo_admin_cidrs` line set to `value` (`[]` or `["a.b.c.d/32"]`), or appended."""
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if LINE.match(line)]
    new = f"demo_admin_cidrs = {value} {MARKER}"
    if not hits:
        sep = "" if not text or text.endswith("\n") else "\n"
        return f"{text}{sep}{new}\n"
    if len(hits) > 1:
        raise Refused(BY_HAND)
    i = hits[0]
    body = lines[i].rstrip("\r\n")
    rhs = body.split("=", 1)[1].split("#", 1)[0].strip()
    if not (rhs.startswith("[") and rhs.endswith("]")):
        raise Refused(BY_HAND)
    lines[i] = new + lines[i][len(body) :]  # keep the line's own ending
    return "".join(lines)


def write_atomic(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_cidrs(path: Path, cidr: str | None) -> str:
    """Open (`cidr`) or close (None); the line printed afterwards. Raises `Refused`."""
    if not path.is_file():
        raise Refused(MISSING)
    text = path.read_bytes().decode("utf-8")  # bytes: read_text would turn \r\n into \n
    value = "[]" if cidr is None else f'["{cidr}"]'
    write_atomic(path, rewrite(text, value))
    return "demo_admin_cidrs: []" if cidr is None else "demo_admin_cidrs: 1 address"


def plan_destroys(text: str) -> int | None:
    """The destroy count of the last summary, 0 for `No changes.`, None when there is none."""
    matches = list(SUMMARY.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    return 0 if last.group(0) == "No changes." else int(last[4])


def plan_rolls_only(text: str, destroys: int) -> bool:
    """True when the plan's destroyed/replaced addresses are all in ROLL and account for every destroy."""
    addrs = [m["addr"] for m in ACTION.finditer(text)]
    return len(addrs) == destroys and bool(addrs) and all(a in ROLL for a in addrs)


def plan_gate(plan: Path, name: str, force: bool) -> str | None:
    """None to go on to `make deploy`; else the exit-3 message."""
    try:
        text = plan.read_text(encoding="utf-8", errors="replace")
        destroys = plan_destroys(text)
    except OSError:
        text, destroys = "", None
    if destroys == 0 or force:
        return None
    if destroys and plan_rolls_only(text, destroys):
        print(f"{name}: the plan rolls the demo admin bearer and the mock task ({destroys} replaced); deploying")
        return None
    count = "an unknown number of" if destroys is None else str(destroys)
    return (
        f"{name}: the plan destroys {count} resource(s) (changing demo_admin_cidrs replaces DEMO_ADMIN_BEARER and "
        "rolls the mock task): read artifacts/terraform-plan.txt, then `make deploy`"
    )


def main(argv: list[str] | None = None, *, fetch: Callable[[], str] = checkip) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    op = sub.add_parser("open")
    op.add_argument("--cidr", default="")
    op.add_argument("--file", type=Path, default=TFVARS)
    cl = sub.add_parser("close")
    cl.add_argument("--file", type=Path, default=TFVARS)
    pg = sub.add_parser("plan-gate")
    pg.add_argument("--name", required=True)
    pg.add_argument("--force", action="store_true")
    pg.add_argument("--plan", type=Path, default=PLAN)
    args = ap.parse_args(argv)
    if args.cmd == "plan-gate":
        stop = plan_gate(args.plan, args.name, args.force)
        if stop:
            print(stop, file=sys.stderr)
            return 3
        return 0
    try:
        if args.cmd == "close":
            print(set_cidrs(args.file, None))
            return 0
        if not args.file.is_file():
            raise Refused(MISSING)
        cidr = public_slash32(args.cidr or fetch())
        if cidr is None:
            raise Refused(NOT_A_32)
        print(set_cidrs(args.file, cidr))
    except Refused as e:
        print(f"admin_cidr: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
