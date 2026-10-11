"""Number Verification. Both operations need a three-legged token from `/oauth2/authorize` that the
network attributed to a line (simulated: `X-Mock-Client-Id` matched the line's
`mobile_data_client_ids`). Anything else — a two-legged token, an auth code issued without the header
or with an id no line knows — is 403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`,
the Fall25 code for "the request was not made over the line's mobile data"."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import Response

from mock_carrier import errors
from mock_carrier.routers.common import Call, register
from mock_carrier.state import Line

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "number-verification"


def _network_line(call: Call) -> Line:
    tok = call.token
    line = call.rt.state.line_by_ref(tok.line) if tok.three_legged and tok.line else None
    if line is None or tok.attributed_client_id is None:
        raise errors.UserNotAuthenticatedByMobileNetwork(
            "Client must authenticate via the mobile network to use this service."
        )
    return line


async def verify(call: Call) -> Response:
    line = _network_line(call)
    if "phoneNumber" in call.body:
        ok = hmac.compare_digest(str(call.body["phoneNumber"]), line.msisdn)
    else:
        digest = hashlib.sha256(line.msisdn.encode()).hexdigest()
        ok = hmac.compare_digest(str(call.body["hashedPhoneNumber"]).lower(), digest)
    return call.json({"devicePhoneNumberVerified": ok})


async def device_phone_number(call: Call) -> Response:
    line = _network_line(call)
    return call.json({"devicePhoneNumber": line.msisdn})


def mount(app: FastAPI, rt: Runtime) -> None:
    register(app, rt, API, "phoneNumberVerify", verify)
    register(app, rt, API, "phoneNumberShare", device_phone_number)
