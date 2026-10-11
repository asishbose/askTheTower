#!/usr/bin/env python3
"""`make config-check`: validate the repo-root `.env` (the one settings file; template `.env.example`).

Checks, without ever printing a secret value:

- the file is gitignored and not tracked (a tracked `.env` is an error even if it holds nothing yet);
- every line is `KEY=value` with a valid key; no `$` or `#` in a value (make expands or cuts them), no quotes
  (make keeps them, compose strips them — the two would disagree), no leading/trailing whitespace, no trailing
  backslash (a make line continuation);
- duplicate keys (the last one wins in make; flagged so it isn't a surprise);
- keys not in `.env.example` (usually a typo) — a warning, since any TF_VAR_* or tool variable may be added;
- AWS: a key id without its secret (or the reverse) is an error; keys and a profile both set is a warning
  (the keys win in botocore and the CLI).

Then it prints what is set, per section of `.env.example`, with secrets masked. Exit 1 on any error.

    uv run python scripts/config_check.py [--file .env] [--example .env.example]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Masked in the listing: secrets, and phone numbers (personal data; never printed — 00 privacy invariants).
SECRET = re.compile(r"(SECRET|TOKEN|BEARER|PASSWORD|_KEY$|_KEY_ID$|^TOWER_JWT$|_numbers$)", re.IGNORECASE)
SECTION = re.compile(r"^# --- (.+?) -*$")


def parse(text: str) -> tuple[list[tuple[int, str, str]], list[str]]:
    """(line number, key, value) for every assignment line, and the format errors (no values in messages)."""
    rows: list[tuple[int, str, str]] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not KEY.match(key):
            errors.append(f"line {n}: not KEY=value (spaces around '=' or an invalid key name?)")
            continue
        if "$" in value or "#" in value:
            errors.append(f"line {n} ({key}): '$' and '#' are not allowed in values")
        if value and value[0] in "\"'":
            errors.append(f"line {n} ({key}): no quotes — write the value bare")
        if value != value.strip():
            errors.append(f"line {n} ({key}): leading or trailing whitespace in the value")
        if value.endswith("\\"):
            errors.append(f"line {n} ({key}): trailing backslash (make would join the next line)")
        rows.append((n, key, value))
    return rows, errors


def sections(example: str) -> list[tuple[str, list[str]]]:
    """Section titles of `.env.example` with the keys under each, in order."""
    out: list[tuple[str, list[str]]] = [("other", [])]
    for raw in example.splitlines():
        m = SECTION.match(raw.strip())
        if m:
            out.append((m.group(1).strip(), []))
            continue
        key, sep, _ = raw.partition("=")
        if sep and KEY.match(key):
            out[-1][1].append(key)
    return [s for s in out if s[1]]


def git_status(path: Path) -> list[str]:
    """Errors if the file is tracked or not ignored. Silent outside a git checkout."""
    rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    try:
        tracked = subprocess.run(  # noqa: S603
            ["git", "ls-files", "--error-unmatch", rel],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
        )
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, capture_output=True)  # noqa: S603,S607
    except FileNotFoundError:
        return []
    errors = []
    if tracked.returncode == 0:
        errors.append(f"{rel} is tracked by git — `git rm --cached {rel}` and rotate every secret in it")
    elif ignored.returncode == 1:
        errors.append(f"{rel} is not gitignored — add it to .gitignore before putting secrets in it")
    return errors


def show(key: str, value: str) -> str:
    if not value:
        return "·"
    return "set (hidden)" if SECRET.search(key) else value


def check(env_path: Path, example_path: Path) -> int:
    if not env_path.exists():
        print(f"{env_path.name}: not found — nothing overrides the defaults. To start: cp .env.example .env")
        return 0
    text = env_path.read_bytes().decode("utf-8")  # bytes: read_text would hide CRLF
    rows, errors = parse(text)
    warnings: list[str] = []
    if "\r\n" in text:
        warnings.append("CRLF line endings (fine: make and `make up` strip them)")
    errors += git_status(env_path)

    values: dict[str, str] = {}
    seen: dict[str, int] = {}
    for n, key, value in rows:
        if key in seen:
            warnings.append(f"line {n} ({key}): duplicate of line {seen[key]} — the last one wins")
        seen[key] = n
        values[key] = value
    set_ = {k: v for k, v in values.items() if v}

    example = example_path.read_text("utf-8") if example_path.exists() else ""
    known = {k for _, keys in sections(example) for k in keys}
    for key in sorted(set(values) - known):
        warnings.append(
            f"{key}: not in {example_path.name} (a typo? fine if it is a TF_VAR_ or tool variable)"
        )

    if bool(set_.get("AWS_ACCESS_KEY_ID")) != bool(set_.get("AWS_SECRET_ACCESS_KEY")):
        errors.append("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together")
    if set_.get("AWS_SESSION_TOKEN") and not set_.get("AWS_ACCESS_KEY_ID"):
        errors.append("AWS_SESSION_TOKEN without AWS_ACCESS_KEY_ID")
    if set_.get("AWS_PROFILE") and set_.get("AWS_ACCESS_KEY_ID"):
        warnings.append("AWS_PROFILE and access keys both set: the keys win; leave one empty")

    print(f"{env_path.name}: {len(set_)} setting(s) set of {len(values)} line(s)")
    for title, keys in sections(example):
        present = [k for k in keys if k in values]
        if not present:
            continue
        print(f"\n  {title}")
        for k in present:
            print(f"    {k:<34} {show(k, values[k])}")
    extra = [k for k in values if k not in known]
    if extra:
        print("\n  not in the template")
        for k in extra:
            print(f"    {k:<34} {show(k, values[k])}")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=ROOT / ".env")
    ap.add_argument("--example", type=Path, default=ROOT / ".env.example")
    args = ap.parse_args(argv)
    return check(args.file.resolve(), args.example.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
