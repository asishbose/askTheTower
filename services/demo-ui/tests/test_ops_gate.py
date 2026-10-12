"""Doc 11 §2 "The gate" (T4), §11: G1–G6 each alone off → off with its reason verbatim, all on → on; a host other
than exactly 127.0.0.1 → off even with the flag; container mode → off; a flag other than exactly `1` → off;
config-check exit 1 → off with its `error:` line masked; config-check cached until `.env`'s mtime changes (or
re-check); `ENV=aws` does not enter the gate; a flipped condition is seen at the next call, without a restart."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import stat
from pathlib import Path

import pytest
from demo_ui.ops import gate as g
from demo_ui.ops.gate import Gate, GateInputs, decide
from demo_ui.ops.probes import ProbeResult, Probes

pytestmark = pytest.mark.unit

ON = GateInputs(published_loopback=False, host="127.0.0.1", flag="1", make_found=True, checkout_found=True,
                dotenv=True)  # fmt: skip
PASS: ProbeResult[None] = ProbeResult(True)


def test_all_on_is_on_and_each_condition_alone_is_its_reason() -> None:
    assert decide(ON, PASS) is None
    cases = [
        (dataclasses.replace(ON, published_loopback=True), g.G1),
        (
            dataclasses.replace(ON, host="0.0.0.0"),  # noqa: S104 - a value under test, nothing binds
            "AWS runs need the UI on 127.0.0.1 (this one listens on 0.0.0.0)",
        ),
        (dataclasses.replace(ON, flag=""), g.G3),
        (dataclasses.replace(ON, make_found=False), "make not found"),
        (dataclasses.replace(ON, checkout_found=False), "checkout not found"),
        (
            dataclasses.replace(ON, dotenv=False),
            "root .env not found: cp .env.example .env, then make config-check",
        ),
    ]
    for inputs, reason in cases:
        assert decide(inputs, PASS) == reason
    assert decide(ON, ProbeResult(False, reason="error: line 3: not KEY=value")) == (
        "make config-check failed: run it in a terminal (error: line 3: not KEY=value)"
    )
    assert decide(ON, ProbeResult(False)) == "make config-check failed: run it in a terminal"


def test_the_first_failing_condition_wins() -> None:
    everything_off = GateInputs(True, "0.0.0.0", "", False, False, False)  # noqa: S104 - a value under test
    assert decide(everything_off, ProbeResult(False)) == g.G1
    assert decide(dataclasses.replace(everything_off, published_loopback=False), None).startswith(
        "AWS runs need"
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::1", "localhost", "192.168.1.20", "127.0.0.2"])  # noqa: S104 - values under test
def test_any_other_host_is_off_even_with_the_flag(host: str) -> None:
    assert (
        decide(dataclasses.replace(ON, host=host), PASS)
        == f"AWS runs need the UI on 127.0.0.1 (this one listens on {host})"
    )


@pytest.mark.parametrize("flag", ["true", "yes", "0", " 1", "1 ", "on", ""])
def test_the_flag_is_exactly_one(flag: str) -> None:
    assert decide(dataclasses.replace(ON, flag=flag), PASS) == g.G3


def _checkout(tmp_path: Path) -> Path:
    (tmp_path / "mk").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Makefile").write_text("all:\n")
    (tmp_path / ".env").write_text("AWS_REGION=us-east-1\n")
    return tmp_path


class Counting:
    def __init__(self, answer: ProbeResult[None] = PASS) -> None:
        self.calls = 0
        self.answer = answer

    async def __call__(self) -> ProbeResult[None]:
        self.calls += 1
        return self.answer


def _gate(root: Path, check: Counting, **kw: object) -> Gate:
    args: dict[str, object] = {"published_loopback": False, "host": "127.0.0.1", "flag": "1", "checkout": root,
                               "which": lambda _: "/usr/bin/make", "config_check": check} | kw  # fmt: skip
    return Gate(**args)  # type: ignore[arg-type]


def test_config_check_is_cached_until_the_env_mtime_changes(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    check = Counting()
    gate = _gate(root, check)

    async def go() -> None:
        assert await gate.reason() is None and check.calls == 1
        assert await gate.reason() is None and check.calls == 1  # cached
        os.utime(root / ".env", (5_000, 5_000))
        assert await gate.reason() is None and check.calls == 2  # .env changed
        assert await gate.reason(fresh=True) is None and check.calls == 3  # re-check
        check.answer = ProbeResult(
            False, reason="error: line 2 (AWS_ACCESS_KEY_ID): '$' and '#' are not allowed"
        )
        os.utime(root / ".env", (6_000, 6_000))
        reason = await gate.reason()
        assert reason is not None and reason.startswith(g.G6) and gate.last == reason

    asyncio.run(go())


def test_a_flipped_condition_is_seen_at_the_next_call(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    make: list[str | None] = ["/usr/bin/make"]
    gate = _gate(root, Counting(), which=lambda _: make[0])

    async def go() -> None:
        assert await gate.reason() is None
        (root / ".env").unlink()
        assert await gate.reason() == g.G5
        (root / ".env").write_text("A=1\n")
        make[0] = None
        assert await gate.reason() == "make not found"

    asyncio.run(go())
    assert asyncio.run(_gate(None, Counting()).reason()) == "checkout not found"  # type: ignore[arg-type]
    container = _gate(root, Counting(), published_loopback=True)
    assert asyncio.run(container.reason()) == g.G1


def test_env_aws_is_not_a_gate_condition() -> None:
    assert "env" not in {f.name for f in dataclasses.fields(GateInputs)}


def _fake_make(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "make"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_config_check_probe_masks_its_error_line_and_discards_stdout(tmp_path: Path) -> None:
    root = _checkout(tmp_path / "co")
    bin_dir = tmp_path / "bin"
    _fake_make(
        bin_dir,
        "echo 'AWS_SECRET_ACCESS_KEY  wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'\n"
        "echo 'warning: keys and a profile' >&2\n"
        "echo 'error: line 4 (AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY) call +16135550101' >&2\n"
        "echo 'error: second' >&2\nexit 1",
    )
    probes = Probes(root, {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    result = asyncio.run(probes.config_check())
    assert not result.ok and result.reason.startswith("error: line 4 (AWS_SECRET_ACCESS_KEY=••••")
    assert (
        "wJalr" not in result.reason and "6135550101" not in result.reason and "second" not in result.reason
    )
    _fake_make(bin_dir, "echo all good; exit 0")
    assert asyncio.run(probes.config_check()) == ProbeResult(True)
