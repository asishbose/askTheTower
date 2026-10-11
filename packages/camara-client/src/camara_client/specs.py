"""Paths, versions and scopes for every CAMARA operation, read from `fixtures/operations.json`.

That file is generated from `specs/camara/*.yaml` by `scripts/gen_fixtures.py` using the mock
carrier's own spec loader, and `tests/test_fixtures.py` fails if it is stale — so the client and the
mock always agree on a path, and the Tower image needs neither the YAML nor a YAML parser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class Operation:
    api: str
    operation_id: str
    method: str
    path: str  # versioned, e.g. /sim-swap/v2/check; may contain {subscriptionId}
    path_params: tuple[str, ...]
    scopes: tuple[tuple[str, ...], ...]  # alternatives
    statuses: tuple[int, ...]
    request_schema: dict[str, Any] | None

    @property
    def key(self) -> str:
        return f"{self.api}/{self.operation_id}"

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    def render_path(self, params: dict[str, str] | None = None) -> str:
        path = self.path
        for name in self.path_params:
            value = (params or {})[name]
            if not value or "/" in value or "?" in value or "#" in value:
                raise ValueError(f"invalid {name}")
            path = path.replace("{" + name + "}", value)
        return path

    def scope_for(self, granted: tuple[str, ...] = ()) -> str:
        """The scope string to ask the token endpoint for: the first alternative the configured scopes
        cover (`sim-swap` covers `sim-swap:check`), else the spec's first alternative."""
        for alt in self.scopes:
            if granted and all(any(s == g or s.startswith(g + ":") for g in granted) for s in alt):
                return " ".join(alt)
        return " ".join(self.scopes[0]) if self.scopes else ""


def fixtures_dir() -> resources.abc.Traversable:
    return resources.files("camara_client") / "fixtures"


@cache
def index() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((fixtures_dir() / "operations.json").read_text("utf-8"))
    return data


@cache
def operation(api: str, operation_id: str) -> Operation:
    raw = index()["operations"][f"{api}/{operation_id}"]
    return Operation(
        api=raw["api"],
        operation_id=raw["operation_id"],
        method=raw["method"],
        path=raw["path"],
        path_params=tuple(raw["path_params"]),
        scopes=tuple(tuple(a) for a in raw["scopes"]),
        statuses=tuple(raw["statuses"]),
        request_schema=raw["request_schema"],
    )


def operations() -> list[Operation]:
    return [operation(*key.split("/", 1)) for key in index()["operations"]]


def api_versions() -> dict[str, str]:
    return {api: info["version"] for api, info in index()["apis"].items()}
