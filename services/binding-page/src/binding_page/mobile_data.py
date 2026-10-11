"""LOCAL SIMULATION OF MOBILE-DATA ATTRIBUTION — the one place it lives.

Real Number Verification works because the carrier sees the phone on its own cellular data session: the
browser is sent to the carrier's authorize URL and the *network* attributes the request to a line.

Locally there is no network to do that. The mock carrier stands in with a header, `X-Mock-Client-Id`
(docs/architecture/components/08-mock-carrier.md §3), and a browser cannot add a header to a redirect. So,
**only when `TOWER_ENV=local`**, the page itself makes the authorize request on the phone's behalf, adding
`X-Mock-Client-Id: <as>` when the bind link carried `?as=phone-asish`, and then sends the browser straight to
its own callback with the carrier's code. Without `?as=` the header is absent — that is the "Wi-Fi on"
simulation, and the carrier refuses exactly as a real one would.

On AWS (any `TOWER_ENV` other than `local`) this module is inert: `?as=` is ignored, no header is ever sent, the
browser goes to the carrier itself, and the carrier's own network attribution does the work. The slide note
says so.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from binding_page.config import Settings

HEADER = "X-Mock-Client-Id"
_AS_RE = re.compile(r"^[a-z][a-z-]{0,39}$")  # letters and hyphens only, like `phone-asish`


class SimulationParamRejected(ValueError):
    """`?as=` is malformed (local only; on AWS the parameter is ignored, never rejected or used)."""


def simulated_client_id(settings: Settings, as_param: str | None) -> str | None:
    """The simulated device id to attribute, or None. Inert outside `TOWER_ENV=local`."""
    if not settings.local or not as_param:
        return None
    if not _AS_RE.match(as_param):
        raise SimulationParamRejected("the simulation parameter is malformed")
    return as_param


class CarrierStartFailed(Exception):
    """The (simulated) carrier did not answer the authorize request with a redirect."""


async def start(settings: Settings, http: httpx.AsyncClient, authorize_url: str, as_param: str | None) -> str:
    """Where to send the browser to begin the carrier's auth-code flow.

    AWS: the carrier's authorize URL itself. Local: the page performs the authorize request (with the simulated
    header when `as` is set) and returns its own callback URL carrying the carrier's `code` and `state`.
    """
    if not settings.local:
        return authorize_url
    client_id = simulated_client_id(settings, as_param)
    headers = {HEADER: client_id} if client_id else {}
    r = await http.get(authorize_url, headers=headers)
    location = r.headers.get("location")
    if r.status_code not in (301, 302, 303, 307) or not location:
        raise CarrierStartFailed(f"authorize answered {r.status_code}")
    query = urlsplit(location).query
    return f"/bind/callback?{query}"
