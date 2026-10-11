"""Loads `specs/camara/*.yaml` and exposes every operation's path, version segment, security
requirements and request schema, so routers never hard-code a path.

Two load-time patches are applied to the in-memory copy (the files are never edited; see
`specs/camara/README.md`): the `apiRoot` server variable defaults to the mock's base URL, and the
`openIdConnectUrl` points at the mock's own discovery document.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from mock_carrier.errors import InvalidArgument, InvalidSink

SPEC_FILES: dict[str, str] = {
    "sim-swap": "sim-swap.yaml",
    "sim-swap-subscriptions": "sim-swap-subscriptions.yaml",
    "call-forwarding-signal": "call-forwarding-signal.yaml",
    "number-verification": "number-verification.yaml",
    "device-reachability-status": "device-reachability-status.yaml",
    "device-reachability-status-subscriptions": "device-reachability-status-subscriptions.yaml",
}

_METHODS = ("get", "post", "put", "patch", "delete")


_BaseLoader: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _SpecLoader(_BaseLoader):  # type: ignore[valid-type,misc]
    """SafeLoader without the timestamp resolver: unquoted dates in examples stay the strings the
    spec authors wrote (so the served JSON is the file's content, not a re-rendered datetime)."""


_SpecLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_spec_file(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        doc = yaml.load(fh, Loader=_SpecLoader)  # noqa: S506 — SafeLoader subclass
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not an OpenAPI document")
    return doc


_REF_RE = re.compile(r"^#/components/([a-zA-Z]+)/([^/]+)$")


@dataclass(frozen=True)
class Operation:
    api: str
    operation_id: str
    method: str  # upper-case
    spec_path: str  # as written in the spec, e.g. /check
    base_path: str  # version-bearing prefix, e.g. /sim-swap/v2
    scopes: tuple[tuple[str, ...], ...]  # alternatives; any one alternative fully satisfied → allowed
    body_required: bool
    request_schema: dict[str, Any] | None  # $ref-resolved, ready for jsonschema
    responses: tuple[int, ...]

    @property
    def full_path(self) -> str:
        return self.base_path + self.spec_path

    @property
    def label(self) -> str:
        return f"{self.method} {self.full_path}"


@dataclass
class Spec:
    api: str
    file: Path
    raw: dict[str, Any]
    version: str
    base_path: str
    operations: dict[str, Operation] = field(default_factory=dict)


def resolve_schema(components: dict[str, Any], schema: Any) -> Any:
    """Inline every `#/components/...` reference and translate OpenAPI 3.0 `nullable` to JSON Schema."""

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 64:
            raise ValueError("schema reference too deep")
        if isinstance(node, dict):
            if "$ref" in node:
                m = _REF_RE.match(node["$ref"])
                if not m:
                    raise ValueError(f"unsupported $ref {node['$ref']}")
                target = components[m.group(1)][m.group(2)]
                merged = {k: v for k, v in node.items() if k != "$ref"}
                merged.update(target)
                return walk(merged, depth + 1)
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in ("discriminator", "example", "examples", "xml", "externalDocs"):
                    continue
                out[k] = walk(v, depth + 1)
            if out.get("nullable") is True and "type" in out:
                t = out["type"]
                out["type"] = [t, "null"] if isinstance(t, str) else [*t, "null"]
            out.pop("nullable", None)
            return out
        if isinstance(node, list):
            return [walk(v, depth + 1) for v in node]
        return node

    return walk(schema)


class SpecSet:
    """All six vendored specs, parsed once at startup."""

    def __init__(self, specs_dir: Path, *, base_url: str, discovery_url: str) -> None:
        self.specs_dir = Path(specs_dir)
        self.base_url = base_url.rstrip("/")
        self.discovery_url = discovery_url
        self.specs: dict[str, Spec] = {}
        self._validators: dict[tuple[str, str], jsonschema.Draft7Validator] = {}
        for api, filename in SPEC_FILES.items():
            path = self.specs_dir / filename
            raw = load_spec_file(path)
            self.specs[api] = self._parse(api, path, raw)

    # --- parsing -------------------------------------------------------------------------------
    def _parse(self, api: str, path: Path, raw: dict[str, Any]) -> Spec:
        server_url: str = raw["servers"][0]["url"]
        base_path = server_url.replace("{apiRoot}", "")
        spec = Spec(api=api, file=path, raw=raw, version=str(raw["info"]["version"]), base_path=base_path)
        for spec_path, item in raw["paths"].items():
            for method in _METHODS:
                if method not in item:
                    continue
                op = item[method]
                sec = op.get("security", raw.get("security", []))
                alternatives = tuple(tuple(scopes) for req in sec for scopes in req.values())
                body = op.get("requestBody")
                schema = None
                required = False
                if body:
                    required = bool(body.get("required", False))
                    media = body.get("content", {}).get("application/json", {})
                    if "schema" in media:
                        schema = resolve_schema(raw["components"], media["schema"])
                spec.operations[op["operationId"]] = Operation(
                    api=api,
                    operation_id=op["operationId"],
                    method=method.upper(),
                    spec_path=spec_path,
                    base_path=base_path,
                    scopes=alternatives,
                    body_required=required,
                    request_schema=schema,
                    responses=tuple(int(c) for c in op["responses"] if str(c).isdigit()),
                )
        return spec

    # --- lookups -------------------------------------------------------------------------------
    def operation(self, api: str, operation_id: str) -> Operation:
        return self.specs[api].operations[operation_id]

    def path_for(self, api: str, operation_id: str) -> str:
        return self.operation(api, operation_id).full_path

    def operations(self) -> list[Operation]:
        return [op for spec in self.specs.values() for op in spec.operations.values()]

    def methods_for(self, path: str) -> list[str]:
        """Every method the specs define for a concrete request path (templates matched per segment)."""
        want = path.rstrip("/").split("/")
        methods = set()
        for op in self.operations():
            have = op.full_path.split("/")
            if len(have) == len(want) and all(
                h == w or (h.startswith("{") and h.endswith("}")) for h, w in zip(have, want, strict=True)
            ):
                methods.add(op.method)
        return sorted(methods)

    def versions(self) -> dict[str, dict[str, str]]:
        return {
            api: {"version": s.version, "base_path": s.base_path, "file": s.file.name}
            for api, s in self.specs.items()
        }

    # --- request validation --------------------------------------------------------------------
    def validate_body(self, op: Operation, body: Any) -> None:
        """Raise InvalidArgument(400) when `body` does not match the operation's request schema."""
        if op.request_schema is None:
            return
        key = (op.api, op.operation_id)
        validator = self._validators.get(key)
        if validator is None:
            validator = jsonschema.Draft7Validator(
                op.request_schema, format_checker=jsonschema.FormatChecker()
            )
            self._validators[key] = validator
        errors = sorted(validator.iter_errors(body), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            where = "/".join(str(p) for p in first.path) or "body"
            if where == "sink":
                raise InvalidSink(f"sink: {first.message}"[:300])
            raise InvalidArgument(f"{where}: {first.message}"[:300])

    # --- served documents ----------------------------------------------------------------------
    def document(self, api: str) -> dict[str, Any]:
        """The vendored spec, verbatim except for the two load-time patches."""
        doc = copy.deepcopy(self.specs[api].raw)
        self._patch(doc)
        return doc

    def _patch(self, doc: dict[str, Any]) -> None:
        for server in doc.get("servers", []):
            variables = server.setdefault("variables", {})
            variables.setdefault("apiRoot", {})["default"] = self.base_url
        schemes = doc.get("components", {}).get("securitySchemes", {})
        if "openId" in schemes:
            schemes["openId"]["openIdConnectUrl"] = self.discovery_url

    def merged_document(
        self, *, extra_paths: dict[str, Any] | None = None, extra_components: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One OpenAPI document for every API: paths carry their version prefix; components are
        namespaced `<api>__<Name>` so nothing collides; security schemes are shared (identical)."""
        paths: dict[str, Any] = {}
        components: dict[str, dict[str, Any]] = {}
        schemes: dict[str, Any] = {}
        description_lines = [
            "CAMARA mock carrier for Ask the Tower. Every path below comes from a vendored spec:"
        ]
        for api, spec in self.specs.items():
            doc = self.document(api)
            prefix = f"{api}__"

            def rewrite(node: Any, prefix: str = prefix) -> Any:
                if isinstance(node, dict):
                    out: dict[str, Any] = {}
                    for k, v in node.items():
                        if k == "$ref" and isinstance(v, str):
                            m = _REF_RE.match(v)
                            out[k] = f"#/components/{m.group(1)}/{prefix}{m.group(2)}" if m else v
                        elif k == "mapping" and isinstance(v, dict):
                            out[k] = {
                                mk: (
                                    f"#/components/{mm.group(1)}/{prefix}{mm.group(2)}"
                                    if isinstance(mv, str) and (mm := _REF_RE.match(mv))
                                    else mv
                                )
                                for mk, mv in v.items()
                            }
                        else:
                            out[k] = rewrite(v, prefix)
                    return out
                if isinstance(node, list):
                    return [rewrite(v, prefix) for v in node]
                return node

            for p, item in doc["paths"].items():
                paths[spec.base_path + p] = rewrite(item)
            for kind, entries in doc.get("components", {}).items():
                if kind == "securitySchemes":
                    for name, scheme in entries.items():
                        schemes.setdefault(name, scheme)
                    continue
                components.setdefault(kind, {})
                for name, value in entries.items():
                    components[kind][prefix + name] = rewrite(value)
            description_lines.append(f"- `{spec.file.name}` ({api} {spec.version}) → `{spec.base_path}`")
        if extra_paths:
            paths.update(copy.deepcopy(extra_paths))
        if extra_components:
            for kind, entries in extra_components.items():
                components.setdefault(kind, {}).update(copy.deepcopy(entries))
        components["securitySchemes"] = schemes
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "Ask the Tower — mock carrier",
                "version": "fall25",
                "description": "\n".join(description_lines)
                + "\n\n`/oauth2/*` and `/_admin/*` are the mock's own additions (the admin API is a simulation aid, "
                "enabled only with `MOCK_ADMIN=1`). Per-API documents are served verbatim at `/openapi/<api>.json`.",
            },
            "servers": [{"url": "/"}],
            "paths": paths,
            "components": components,
            "x-mock-carrier": {"apis": self.versions()},
        }
