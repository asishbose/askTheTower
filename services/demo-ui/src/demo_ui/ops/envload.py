"""The make loader (doc 11 §2 "Env modes", T3, reading R1): how a probe's child gets the root `.env`.

The root `.env` is loaded by `mk/vars.mk` (it rejects `$` and `#`, strips CR, skips empty values and exports every
non-empty `KEY=value` to the recipe). A probe is not a make target, so its recipe is wrapped in a rule that exists
only on make's command line:

    make -s --no-print-directory ENV=<local|aws> --eval '__ops_probe: ; @<recipe>' __ops_probe

This module never sources `.env`, never parses it and never reads a value from it: the only thing it asks of the
file is whether it exists (gate G5). Recipes are constants (`FACTS_RECIPE` here, the rest in `ops/probes.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT_ENV = ".env"
PROBE_GOAL = "__ops_probe"
LoaderEnv = Literal["local", "aws"]

# The shell's `${VAR:+1}` turns a secret into a `1`: no value is ever printed (doc 11 §2, probe `facts`).
FACTS_RECIPE = (
    'echo "profile=$${AWS_PROFILE} region=$${AWS_REGION} keys=$${AWS_ACCESS_KEY_ID:+1} '
    'cognito=$${COGNITO_PASSWORD_ASISH:+1}$${COGNITO_PASSWORD_MOM:+1}"'
)
FACTS_LINE = re.compile(
    r"profile=(?P<profile>[A-Za-z0-9_+=,.@-]{0,64}) region=(?P<region>(?:[a-z]{2}(?:-[a-z]+)+-\d)?) "
    r"keys=(?P<keys>1?) cognito=(?P<cognito>1{0,2})"
)


@dataclass(frozen=True)
class Facts:
    """What the `facts` probe may say about the root `.env`: names and booleans, never a secret."""

    profile: str
    region: str
    keys: bool
    cognito: int  # how many of the two Cognito passwords are set

    @property
    def credentials(self) -> str:
        """The header's credential source (doc 11 §2 "Layout")."""
        if self.profile:
            return f"profile {self.profile}"
        return "key pair from .env" if self.keys else "default chain"


def loader_argv(env: LoaderEnv, recipe: str) -> tuple[str, ...]:
    """The `make --eval` argv for one probe recipe: one rule, goal `__ops_probe`, `ENV` fixed, no shell of ours."""
    return (
        "make", "-s", "--no-print-directory", f"ENV={env}", "--eval", f"{PROBE_GOAL}: ; @{recipe}", PROBE_GOAL,
    )  # fmt: skip


def dotenv_present(checkout: Path | None) -> bool:
    """G5: the root `.env` exists. Existence only; the file is never opened here."""
    return checkout is not None and (checkout / ROOT_ENV).exists()


def dotenv_mtime(checkout: Path | None) -> float | None:
    """The cache key for gate G6 (config-check is re-run when `.env` changes). Metadata only."""
    if checkout is None:
        return None
    try:
        return (checkout / ROOT_ENV).stat().st_mtime
    except OSError:
        return None


def parse_facts(text: str) -> Facts | None:
    """The one line the `facts` recipe prints, or None when it is anything else."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    m = FACTS_LINE.fullmatch(lines[0].strip())
    if m is None:
        return None
    return Facts(m["profile"], m["region"], m["keys"] == "1", len(m["cognito"]))
