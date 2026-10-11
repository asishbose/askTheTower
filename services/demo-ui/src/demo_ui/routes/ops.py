"""Pane 5 routes (doc 11 §2 "Pane 5", Routes). All behind the app's guard (token, and `HX-Request` on POSTs).

- `POST /ops/run/{name}`: a present `Origin` must be this page's own (403); a name outside the allowlist is 404;
  runner off → 403 with the reason; busy (a job or a macro) → 409 "running: <name>"; else 202 and the job starts.
- `POST /ops/stop`: same header and origin rules; 200, also when nothing runs.
- `GET /fragment/ops`: the "Run here" column. `GET /fragment/ops-checklist`: the AWS pipeline (`?fresh=1` from the
  re-check button skips the 5 s cache).

The log lines travel on the existing `/events` stream (`ops`, `ops-status`); there is no `/ops/stream` (O6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from demo_ui import views
from demo_ui.ops.allowlist import GROUPS
from demo_ui.ops.runner import RunnerBusy, RunnerOff

if TYPE_CHECKING:
    from demo_ui.deps import Deps

WAIT_FOR_STACK = "waiting for the stack: run up"


def _status(text: str, level: str, code: int) -> HTMLResponse:
    return HTMLResponse(views.render("status.html", text=text, level=level), status_code=code)


def same_origin(request: Request) -> bool:
    """A browser sends `Origin` on every POST; when present it must be this page's own scheme and host."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin == f"{request.url.scheme}://{request.headers.get('host', '')}"


def ops_router(deps: Deps) -> APIRouter:
    router = APIRouter()
    ops = deps.ops

    @router.post("/ops/run/{name}", response_class=HTMLResponse)
    async def run(name: str, request: Request) -> HTMLResponse:
        if not same_origin(request):
            return _status("Ops: foreign Origin", "fail", 403)
        if name not in ops.targets:
            return _status("Ops: not in the allowlist", "fail", 404)
        try:
            ops.start(name)
        except RunnerOff as e:
            return _status(str(e), "fail", 403)
        except RunnerBusy as e:
            return _status(str(e), "fail", 409)
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
        busy = view["busy"] or deps.runner.busy
        down = [] if ops.off or busy else await deps.stack.down()
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for group in GROUPS:
            buttons = []
            for t in (t for t in ops.targets.values() if t.group == group):
                why = "a job is running" if busy else (WAIT_FOR_STACK if down and t.needs_stack else "")
                buttons.append(
                    {"name": t.name, "label": t.label, "why": why, "cta": bool(down) and t.name == "up"}
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

    @router.get("/fragment/ops-checklist", response_class=HTMLResponse)
    async def checklist(fresh: bool = False) -> HTMLResponse:
        return HTMLResponse(
            views.render("ops_checklist.html", c=await deps.aws_status.checklist(fresh=fresh))
        )

    return router
