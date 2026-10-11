"""The reference client's in-process stack (mock carrier + Tower over Streamable HTTP + DynamoDB), reused, not
copied: `services/ref-client/tests/conftest.py` is loaded by path and its `ref_stack` fixture re-exported by the
test modules that need it (doc 11 §11: "the ref-client test seams"). Its `store` dependency resolves to this
service's moto `store`.

Plus `Wire`: an httpx transport in front of another client that records every request the UI sends and every
response body it reads, so a test can grep both (doc 11 §8.3: the UI never sends or receives a number).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import httpx

REF_CONFTEST = Path(__file__).resolve().parents[2] / "ref-client" / "tests" / "conftest.py"


def load_ref_conftest() -> ModuleType:
    name = "demo_ui_tests_ref_stack"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REF_CONFTEST)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look their module up while the class is built
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Exchange:
    method: str
    url: str
    headers: dict[str, str]
    body: str
    status: int
    response: str

    def text(self) -> str:
        return f"{self.method} {self.url} {self.body} -> {self.status} {self.response}"


@dataclass
class Wire(httpx.AsyncBaseTransport):
    """Sends through `inner` (an ASGI transport or a client) and keeps both directions."""

    inner: httpx.AsyncBaseTransport | httpx.AsyncClient
    log: list[Exchange] = field(default_factory=list)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = (await request.aread()).decode()
        if isinstance(self.inner, httpx.AsyncClient):
            got = await self.inner.send(request)
        else:
            got = await self.inner.handle_async_request(request)
        content = await got.aread()
        self.log.append(
            Exchange(
                request.method,
                str(request.url),
                dict(request.headers),
                body,
                got.status_code,
                content.decode(errors="replace"),
            )
        )
        headers = {
            k: v for k, v in got.headers.items() if k.lower() not in {"content-length", "content-encoding"}
        }
        return httpx.Response(got.status_code, headers=headers, content=content, request=request)

    def dump(self) -> str:
        return "\n".join(x.text() for x in self.log)
