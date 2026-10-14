"""The Ops tab's routes (doc 11 §2 "Routes"). All behind the app's guard (token, and `HX-Request` on POSTs).

- `POST /ops/run/{name}` (form fields `confirm`, and `cidr` for `aws-admin-open`; anything else is ignored): a present
  `Origin` must be this page's own (403); then `OpsRunner.request` checks name (404), runner off or gate off (403),
  busy (409), order (403 "after <label>"), confirm word (400 "type <word> to confirm") and `/32` (400); else 202.
- `POST /ops/stop`: same header and origin rules; 200, also when nothing runs.
- Fragments: `/fragment/ops` (Run here + the shared log), `/fragment/ops-header` (profile, region, account, gate,
  banner), `/fragment/ops-checklist` (the stepper and the admin box) and `/fragment/ops-teardown`. `?fresh=1` (the
  re-check button) skips the caches and re-runs the probes and config-check; `?tick=1` (the 15 s poll, set by
  `ops.js`) re-reads files and repeats the GETs but runs no probe, so an idle tab makes no AWS call.

The log lines travel on the existing `/events` stream (`ops`, `ops-status`); there is no `/ops/stream` (O6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from demo_ui import views
from demo_ui.ops import plan
from demo_ui.ops.allowlist import GROUPS, OpsTarget, by_name
from demo_ui.ops.aws_status import RESTART, Pipeline, account, run_block
from demo_ui.ops.explain import explain
from demo_ui.ops.probes import ProbeSnapshot
from demo_ui.ops.runner import RunRefused

if TYPE_CHECKING:
    from demo_ui.deps import Deps

WAIT_FOR_STACK = "waiting for the stack: run up"
# The stepper, the admin box and teardown always show the allowlist's own aws rows (tests may swap the runner's).
ALLOWLIST = by_name()


def _status(text: str, level: str, code: int) -> HTMLResponse:
    return HTMLResponse(views.render("status.html", text=text, level=level), status_code=code)


def same_origin(request: Request) -> bool:
    """A browser sends `Origin` on every POST; when present it must be this page's own scheme and host."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin == f"{request.url.scheme}://{request.headers.get('host', '')}"


def command(target: OpsTarget) -> str:
    """The exact terminal command (the tab and the terminal are interchangeable): the argv as text."""
    return " ".join(target.argv)


def _field(form: Any, name: str) -> str | None:
    value = form.get(name)
    return value if isinstance(value, str) else None


def ops_router(deps: Deps) -> APIRouter:
    router = APIRouter()
    ops = deps.ops

    async def gate_reason(fresh: bool = False) -> str | None:
        return await deps.gate.reason(fresh=fresh)

    async def snapshot(gate: str | None, *, run: bool, fresh: bool) -> ProbeSnapshot:
        return await deps.quiet.snapshot(gate_on=gate is None, run=run, fresh=fresh)

    def busy() -> str | None:
        if deps.runner.busy and not ops.busy:
            return "running: a macro"
        return "a job is running" if ops.busy else None

    @router.post("/ops/run/{name}", response_class=HTMLResponse)
    async def run(name: str, request: Request) -> HTMLResponse:
        if not same_origin(request):
            return _status("Ops: foreign Origin", "fail", 403)
        form = await request.form()
        try:
            await ops.request(name, confirm=_field(form, "confirm"), cidr=_field(form, "cidr"))
        except RunRefused as e:
            return _status(str(e), "fail", e.status)
        return _status(f"{name}: started", "info", 202)

    @router.post("/ops/stop", response_class=HTMLResponse)
    async def stop(request: Request) -> HTMLResponse:
        if not same_origin(request):
            return _status("Ops: foreign Origin", "fail", 403)
        stopped = await ops.stop()
        return _status("stopped" if stopped else "nothing runs", "info", 200)

    @router.get("/fragment/ops", response_class=HTMLResponse)
    async def run_column() -> HTMLResponse:
        view = ops.view()
        is_busy = view["busy"] or deps.runner.busy
        down = [] if ops.off or is_busy else await deps.stack.down()
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for group in GROUPS["local"]:
            buttons = []
            for t in (t for t in ops.targets.values() if t.runner == "local" and t.group == group):
                why = "a job is running" if is_busy else (WAIT_FOR_STACK if down and t.needs_stack else "")
                buttons.append(
                    {
                        "name": t.name,
                        "label": t.label,
                        "why": why,
                        "cta": bool(down) and t.name == "up",
                        "explain": explain(t.name),
                    }
                )
            groups.append((group, buttons))
        return HTMLResponse(
            views.render(
                "ops.html",
                off=ops.off,
                groups=groups,
                stack_down=down,
                busy=view["busy"],
                job=view["job"],
                lines=view["lines"],
            )
        )

    @router.get("/fragment/ops-header", response_class=HTMLResponse)
    async def header(fresh: bool = False) -> HTMLResponse:
        gate = await gate_reason(fresh)
        snap = await snapshot(gate, run=True, fresh=fresh)
        pipeline = await deps.aws_status.pipeline(snap)
        facts = snap.facts.value if snap.facts is not None and snap.facts.ok else None
        return HTMLResponse(
            views.render(
                "ops_header.html",
                gate=gate,
                credentials=facts.credentials if facts else None,
                region=(facts.region or "region not set") if facts else None,
                facts_reason=snap.facts.reason if snap.facts is not None and not snap.facts.ok else "",
                account=account(snap.sts) if gate is None else None,
                banner=pipeline.banner,
            )
        )

    @router.get("/fragment/ops-checklist", response_class=HTMLResponse)
    async def checklist(fresh: bool = False, tick: bool = False) -> HTMLResponse:
        gate = await gate_reason(fresh)
        snap = await snapshot(gate, run=not tick, fresh=fresh)
        pipeline = await deps.aws_status.pipeline(snap, fresh=fresh)
        facts = snap.facts.value if snap.facts is not None and snap.facts.ok else None
        cognito = facts.cognito if facts else None
        deploy_word = plan.deploy_needs_word(deps.aws_status.files.plan)
        return HTMLResponse(
            views.render(
                "ops_checklist.html",
                p=pipeline,
                rows=_rows(pipeline, gate=gate, busy=busy(), cognito=cognito, deploy_word=deploy_word),
                admin=_admin_rows(pipeline, gate=gate, busy=busy()),
                restart=RESTART,
            )
        )

    @router.get("/fragment/ops-teardown", response_class=HTMLResponse)
    async def teardown(fresh: bool = False) -> HTMLResponse:
        gate = await gate_reason(fresh)
        snap = await snapshot(gate, run=True, fresh=fresh)
        pipeline = await deps.aws_status.pipeline(snap)
        why = gate or busy() or ""
        rows = [_row(ALLOWLIST[n], why, word=True) for n in ("aws-down", "aws-down-all")]
        return HTMLResponse(views.render("ops_teardown.html", t=pipeline.teardown, rows=rows))

    return router


def _row(target: OpsTarget, why: str, *, word: bool) -> dict[str, Any]:
    return {
        "name": target.name,
        "label": target.label,
        "command": command(target),
        "explain": explain(target.name),
        "why": why,
        "word": target.confirm if word else None,
    }


def _rows(
    p: Pipeline, *, gate: str | None, busy: str | None, cognito: int | None, deploy_word: bool
) -> list[dict[str, Any]]:
    """One stepper row per step: the step, its Run block reason, its command, and `deploy`'s word when needed."""
    out = []
    for s in p.steps:
        t = ALLOWLIST[s.target]
        why = run_block(t, p, gate=gate, busy=busy, facts_cognito=cognito)
        needs_word = t.confirm is not None and (t.confirm_when == "always" or deploy_word)
        out.append({"s": s, "next": s.n == p.next_id, **_row(t, why, word=needs_word)})
    return out


def _admin_rows(p: Pipeline, *, gate: str | None, busy: str | None) -> list[dict[str, Any]]:
    open_, close = ALLOWLIST["aws-admin-open"], ALLOWLIST["aws-admin-close"]
    return [
        {**_row(open_, run_block(open_, p, gate=gate, busy=busy, facts_cognito=2), word=False), "cidr": True},
        {**_row(close, run_block(close, p, gate=gate, busy=busy, facts_cognito=2), word=True), "cidr": False},
    ]
