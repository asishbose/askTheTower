"""Jinja2 templates (plain HTML, phone width, no JavaScript)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _minute(v: datetime | None) -> str:
    return v.strftime("%Y-%m-%d %H:%M UTC") if v else ""


def _short_line(line_id: str) -> str:
    """`ln_abcdefgh…` — enough to tell two lines apart; never the number."""
    return f"{line_id[:11]}…"


templates.env.filters["minute"] = _minute
templates.env.filters["short_line"] = _short_line


def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
