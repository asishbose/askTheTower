"""The quiet probes (doc 11 §2 "The quiet probes", T6): read-only, allowlisted, filtered inside, never raising.

Each probe is a fixed argv run with `asyncio.create_subprocess_exec` (`shell=False`, stdin closed, its own process
group, the scrubbed base env), a 5 s timeout (then the group is killed: they only read), stdout capped at 256 KB and
stderr discarded (it can carry ARNs; `config-check` keeps its stderr only to find its first `error:` line, which
names a key or a line number, never a value). `facts`, `sts`, `ecr` and `tf` go through `envload.loader_argv`, so
their credentials come from the root `.env` through make, as in a terminal. The output is parsed and filtered here;
the raw text is never logged, cached, rendered or written. Log lines carry the probe name and the reason only.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from demo_ui.ops.allowlist import env_from_sets
from demo_ui.ops.envload import FACTS_RECIPE, Facts, LoaderEnv, loader_argv, parse_facts
from demo_ui.redact import mask, mask_secrets

log = logging.getLogger("demo_ui.ops.probes")

TIMEOUT_S = 5.0
CONFIG_CHECK_TIMEOUT_S = 20.0  # it starts `uv`
STDOUT_CAP = 256 * 1024
STDERR_CAP = 16 * 1024
# Inside the `make --eval` wrapper a missing binary is bash's 127, but make then exits 2 for any failed recipe, so
# the recipe says so itself: a guard prints this marker (and exits 0) when its binary is not on PATH.
MISSING_MARK = "__ops_missing__"


def guarded(binary: str, command: str) -> str:
    return f"command -v {binary} >/dev/null 2>&1 || {{ echo {MISSING_MARK}; exit 0; }}; {command}"


STS_RECIPE = guarded("aws", "aws sts get-caller-identity --query Account --output text")
ECR_RECIPE = guarded(
    "aws", "aws ecr describe-repositories --query 'repositories[].repositoryName' --output json"
)
TF_RECIPE = guarded("terraform", "terraform -chdir=deploy/terraform output -json")
CONFIG_CHECK_ARGV = ("make", "-s", "--no-print-directory", "config-check", "ENV=local")
CHECKIP_ARGV = ("curl", "-fsS", "--max-time", "5", "https://checkip.amazonaws.com")
TF_DIR = Path("deploy") / "terraform"

REPO_NAME = re.compile(r"[a-z0-9][a-z0-9._/-]{0,255}")
ACCOUNT_DIGITS = 12
TF_KEYS = ("region", "name", "binding_url", "web_chat_url", "carrier_backend")
AWS_MISSING = "aws CLI not found (runbook 0.8)"
TF_MISSING = "terraform not found"
TF_UNINITIALISED = "terraform not initialised: run plan"
CURL_MISSING = "curl not found: type your address"
NOT_A_32 = "not a public IPv4 /32"


@dataclass(frozen=True)
class ProbeResult[T]:
    """`ok` with a filtered `value`, or not `ok` with a `reason` (shown verbatim). Never an exception."""

    ok: bool
    value: T | None = None
    reason: str = ""


@dataclass(frozen=True)
class Exec:
    code: int | None  # None: it did not start, timed out or answered too much
    out: bytes = b""
    err: bytes = b""
    reason: str = ""


def public_slash32(value: str) -> str | None:
    """The admin path's address rule (doc 11 §2 "Admin path /32"): an IPv4 `/32` (a bare address gets `/32`) whose
    address is global; the normalised `a.b.c.d/32`, or None. IPv6, a wider prefix or a private address: None."""
    text = value.strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "/" not in text:
        text += "/32"
    try:
        net = ipaddress.IPv4Network(text, strict=True)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return None
    if net.prefixlen != 32 or not net.network_address.is_global:
        return None
    return str(net)


@dataclass(frozen=True)
class TfState:
    """`in_state`: the main root has outputs (`{}` = no stack in state); `outputs`: only the kept keys."""

    in_state: bool
    outputs: Mapping[str, str]


@dataclass(frozen=True)
class ProbeSnapshot:
    """The quiet probes' last answers; None = not run (the gate is off, or only a tick asked)."""

    facts: ProbeResult[Facts] | None = None
    sts: ProbeResult[str] | None = None
    ecr: ProbeResult[tuple[str, ...]] | None = None
    tf: ProbeResult[TfState] | None = None
    checkip: ProbeResult[str] | None = None


def is_account(text: str) -> bool:
    """Exactly twelve ASCII digits (an AWS account id). Plain string checks: the privacy regexes have one home."""
    return len(text) == ACCOUNT_DIGITS and text.isascii() and text.isdigit()


def holds_account(text: str) -> bool:
    """A run of twelve or more digits anywhere (a repository URI holds the account id)."""
    run = 0
    for c in text:
        run = run + 1 if c.isascii() and c.isdigit() else 0
        if run >= ACCOUNT_DIGITS:
            return True
    return False


def filter_tf(text: str) -> TfState | None:
    """`terraform output -json`, reduced to the non-sensitive string outputs of `TF_KEYS` (it prints sensitive
    outputs in clear); None when it is not a JSON object."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, str] = {}
    for key in TF_KEYS:
        entry = data.get(key)
        if (
            isinstance(entry, dict)
            and entry.get("sensitive") is False
            and isinstance(entry.get("value"), str)
        ):
            out[key] = entry["value"]
    return TfState(bool(data), out)


def filter_ecr(text: str) -> tuple[str, ...] | None:
    """A JSON list of repository names (no URI: a URI holds the account id); None for anything else."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or not all(
        isinstance(n, str) and REPO_NAME.fullmatch(n) and "amazonaws" not in n and not holds_account(n)
        for n in data
    ):
        return None
    return tuple(data)


def dashed(account: str) -> str:
    """`123456789012` → `1234-5678-9012`, as the console shows it (a bare 12-digit run is a phone-number hit)."""
    return f"{account[:4]}-{account[4:8]}-{account[8:]}"


class ProbeExec:
    """Runs one fixed argv quietly. Separate from `Probes` so tests can call it with a recipe of their own."""

    def __init__(self, cwd: Path | None, parent_env: Mapping[str, str]) -> None:
        self.cwd = cwd
        self.env = env_from_sets(("base",), parent_env)

    async def run(self, argv: Sequence[str], *, timeout_s: float, keep_stderr: bool = False) -> Exec:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE if keep_stderr else asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return Exec(None, reason=f"{argv[0]} not found")
        except OSError as e:
            return Exec(None, reason=f"could not start {argv[0]} ({type(e).__name__})")
        try:
            out, err = await asyncio.wait_for(_collect(proc), timeout_s)
        except TimeoutError:
            await _kill(proc)
            return Exec(None, reason=f"timeout ({timeout_s:g} s)")
        if out is None:
            await _kill(proc)
            return Exec(None, reason="answer too long")
        return Exec(proc.returncode, out, err or b"")


async def _read(stream: asyncio.StreamReader | None, cap: int) -> bytes | None:
    """Up to `cap` bytes; None when there is more (the probe's answer is never that long)."""
    if stream is None:
        return b""
    buf = bytearray()
    while chunk := await stream.read(65_536):
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


async def _collect(proc: asyncio.subprocess.Process) -> tuple[bytes | None, bytes | None]:
    out, err = await asyncio.gather(_read(proc.stdout, STDOUT_CAP), _read(proc.stderr, STDERR_CAP))
    if out is not None:
        await proc.wait()
    return out, err


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), 1.0)


class Probes:
    """The six probes of doc 11 §2. Each returns a `ProbeResult` and logs only its name and reason."""

    def __init__(
        self,
        checkout: Path | None,
        parent_env: Mapping[str, str],
        *,
        timeout_s: float = TIMEOUT_S,
        config_timeout_s: float = CONFIG_CHECK_TIMEOUT_S,
    ) -> None:
        self.checkout = checkout
        self.exec = ProbeExec(checkout, parent_env)
        self.timeout_s = timeout_s
        self.config_timeout_s = config_timeout_s

    async def _wrapped(self, env: LoaderEnv, recipe: str) -> Exec:
        return await self.exec.run(loader_argv(env, recipe), timeout_s=self.timeout_s)

    async def facts(self) -> ProbeResult[Facts]:
        ran = await self._wrapped("local", FACTS_RECIPE)
        facts = parse_facts(ran.out.decode("utf-8", "replace")) if ran.code == 0 else None
        return _done("facts", ProbeResult(True, facts) if facts else _failed(ran))

    async def sts(self) -> ProbeResult[str]:
        ran = await self._wrapped("aws", STS_RECIPE)
        text = ran.out.decode("utf-8", "replace").strip() if ran.code == 0 else ""
        if is_account(text):
            return _done("sts", ProbeResult(True, text))
        return _done("sts", _failed(ran, missing=AWS_MISSING, on_exit=f"exit {ran.code}: no credentials?"))

    async def ecr(self) -> ProbeResult[tuple[str, ...]]:
        ran = await self._wrapped("aws", ECR_RECIPE)
        names = filter_ecr(ran.out.decode("utf-8", "replace")) if ran.code == 0 else None
        if names is not None:
            return _done("ecr", ProbeResult(True, names))
        return _done("ecr", _failed(ran, missing=AWS_MISSING))

    async def tf(self) -> ProbeResult[TfState]:
        ran = await self._wrapped("aws", TF_RECIPE)
        kept = filter_tf(ran.out.decode("utf-8", "replace")) if ran.code == 0 else None
        if kept is not None:
            return _done("tf", ProbeResult(True, kept))
        initialised = self.checkout is not None and (self.checkout / TF_DIR / ".terraform").is_dir()
        return _done(
            "tf", _failed(ran, missing=TF_MISSING, on_exit=None if initialised else TF_UNINITIALISED)
        )

    async def config_check(self) -> ProbeResult[None]:
        ran = await self.exec.run(CONFIG_CHECK_ARGV, timeout_s=self.config_timeout_s, keep_stderr=True)
        if ran.code == 0:
            return _done("config-check", ProbeResult(True))
        first = next(
            (
                ln.strip()
                for ln in ran.err.decode("utf-8", "replace").splitlines()
                if ln.strip().startswith("error:")
            ),
            "",
        )
        return _done("config-check", ProbeResult(False, reason=mask(mask_secrets(first)) or ran.reason))

    async def checkip(self) -> ProbeResult[str]:
        ran = await self.exec.run(CHECKIP_ARGV, timeout_s=self.timeout_s)
        if ran.code is None and ran.reason == "curl not found":
            return _done("checkip", ProbeResult(False, reason=CURL_MISSING))
        lines = ran.out.decode("utf-8", "replace").split() if ran.code == 0 else []
        cidr = public_slash32(lines[0]) if len(lines) == 1 and "/" not in lines[0] else None
        if cidr is None:
            return _done("checkip", ProbeResult(False, reason=NOT_A_32) if ran.code == 0 else _failed(ran))
        return _done("checkip", ProbeResult(True, cidr))


def _failed[T](ran: Exec, *, missing: str | None = None, on_exit: str | None = None) -> ProbeResult[T]:
    """Why a probe gave nothing: it did not run (its own reason), a missing binary, a non-zero exit, or output
    that is not what the probe accepts."""
    if ran.code is None:
        return ProbeResult(False, reason=ran.reason)
    if missing and ran.code == 0 and ran.out.strip() == MISSING_MARK.encode():
        return ProbeResult(False, reason=missing)
    if ran.code != 0:
        return ProbeResult(False, reason=on_exit or f"exit {ran.code}")
    return ProbeResult(False, reason="unexpected answer")


def _done[T](name: str, result: ProbeResult[T]) -> ProbeResult[T]:
    log.info("ops probe=%s ok=%s reason=%s", name, result.ok, result.reason or "-")
    return result


class QuietProbes:
    """The probes the stepper and the header use, cached (doc 11 §2 "Recomputed"): `sts`, `ecr`, `tf` and `facts`
    60 s, `checkip` 10 min. They run only with the gate on and only when asked to (tab open, after a job,
    re-check); a 15 s tick reads the last snapshot. Concurrent askers share one run."""

    def __init__(
        self,
        probes: Probes,
        *,
        cache_s: float = 60.0,
        checkip_cache_s: float = 600.0,
        monotonic: Callable[[], float],
    ) -> None:
        self.probes = probes
        self.cache_s = cache_s
        self.checkip_cache_s = checkip_cache_s
        self.monotonic = monotonic
        self._last: tuple[float, ProbeSnapshot] | None = None
        self._checkip: tuple[float, ProbeResult[str]] | None = None
        self._lock = asyncio.Lock()

    async def snapshot(self, *, gate_on: bool, run: bool = True, fresh: bool = False) -> ProbeSnapshot:
        if not gate_on:
            return ProbeSnapshot()
        async with self._lock:
            now = self.monotonic()
            stale = self._last is None or now - self._last[0] >= self.cache_s
            if run and (fresh or stale):
                facts, sts, ecr, tf, checkip = await asyncio.gather(
                    self.probes.facts(), self.probes.sts(), self.probes.ecr(), self.probes.tf(),
                    self._checkip_cached(now, fresh),
                )  # fmt: skip
                self._last = (now, ProbeSnapshot(facts, sts, ecr, tf, checkip))
            return self._last[1] if self._last is not None else ProbeSnapshot()

    async def _checkip_cached(self, now: float, fresh: bool) -> ProbeResult[str]:
        if fresh or self._checkip is None or now - self._checkip[0] >= self.checkip_cache_s:
            self._checkip = (now, await self.probes.checkip())
        return self._checkip[1]
