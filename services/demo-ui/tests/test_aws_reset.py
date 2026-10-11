"""Doc 11 §3, §5, §11 (U3): Reset on ENV=aws calls the mock's scenario load, then Alerts' `/internal/reset` with the
two subs, then the re-grant, each with its own bearer, in that order; a 401 at step 2 stops before step 3 and the
status names it; the module never imports the seed module and writes no store."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from demo_ui import aws_reset
from demo_ui.aws_reset import AwsReset, AwsResetFailed
from demo_ui.clients import Unavailable

pytestmark = pytest.mark.integration


class Wire:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict[str, object]]] = []
        self.fail: dict[str, int | Exception] = {}

    def transport(self, name: str) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            self.calls.append(
                (name, r.url.path, r.headers.get("authorization", ""), json.loads(r.content or b"{}"))
            )
            answer = self.fail.get(name)
            if isinstance(answer, Exception):
                raise answer
            return httpx.Response(answer or 200, json={})

        return httpx.MockTransport(handle)


def reset_over(wire: Wire) -> AwsReset:
    def client(name: str, base: str, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {token}"}, transport=wire.transport(name)
        )

    return AwsReset(
        client("mock", "https://api.test/carrier-admin", "mt"),
        client("alerts", "https://api.test", "ab"),
        client("binding", "https://api.test", "bt"),
        asish="sub-asish",
        mom="sub-mom",
    )


async def test_three_steps_in_order_each_with_its_own_bearer() -> None:
    wire = Wire()
    await reset_over(wire)()
    assert wire.calls == [
        ("mock", "/carrier-admin/_admin/scenarios/load", "Bearer mt", {"name": "demo"}),
        ("alerts", "/internal/reset", "Bearer ab", {"user_ids": ["sub-asish", "sub-mom"]}),
        ("binding", "/_admin/grants", "Bearer bt", {"owner_user_id": "sub-mom", "grantee_user_id": "sub-asish",
                                                     "grant": "watch", "alias": "mom", "action": "grant"}),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("step", "answer", "message"),
    [
        ("mock", 401, "mock load: 401"),
        ("alerts", 401, "alerts reset: 401"),
        ("alerts", httpx.ConnectError("x"), "alerts reset: not reachable (ConnectError)"),
        ("binding", 403, "binding re-grant: 403"),
    ],
)
async def test_the_first_failure_stops_the_reset_and_names_the_step(
    step: str, answer: int | Exception, message: str
) -> None:
    wire = Wire()
    wire.fail[step] = answer
    with pytest.raises(AwsResetFailed) as e:
        await reset_over(wire)()
    assert str(e.value) == message
    assert isinstance(e.value, Unavailable)  # the runner shows an Unavailable's text verbatim
    order = ["mock", "alerts", "binding"]
    assert [c[0] for c in wire.calls] == order[: order.index(step) + 1]


def test_no_seed_module_and_no_store() -> None:
    tree = ast.parse(Path(aws_reset.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots & {"tower_consent", "tower_audit", "boto3", "importlib", "seed"} == set()
    assert "seed.py" not in Path(aws_reset.__file__).read_text(encoding="utf-8")
