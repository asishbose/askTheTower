"""The chaos matrix, statically (the e2e layer runs it live, tests/e2e/test_chaos.py): every fault the mock can
inject (`/_admin/faults`: timeout / 500 / 429) becomes, through camara-client's error map, a reason code that
is a *refusal* — STALE_DATA or CARRIER_ERROR — never an outcome about the line (OK, SIM_SWAPPED_RECENT, ...)."""

from __future__ import annotations

import pytest
from camara_client.errors import breaker_open, map_error, timeout_error
from mock_carrier import errors as mock_errors
from mock_carrier.state import FAULT_KINDS
from tower_policy import ReasonCode

pytestmark = pytest.mark.unit

REFUSALS = {ReasonCode.STALE_DATA, ReasonCode.CARRIER_ERROR}
LINE_OUTCOMES = {
    ReasonCode.OK,
    ReasonCode.SIM_SWAPPED_RECENT,
    ReasonCode.CALL_FORWARDING_SET,
    ReasonCode.UNREACHABLE,
}
# what the mock answers for each injected fault (routers/common.py)
MOCK_ANSWER = {"500": mock_errors.Internal, "429": mock_errors.TooManyRequests}


def test_fault_kinds_are_the_three_the_doc_names() -> None:
    assert set(FAULT_KINDS) == {"timeout", "500", "429"}  # testing-and-showcase §3, Chaos row


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_every_fault_maps_to_a_refusal(kind: str) -> None:
    if kind == "timeout":
        err = timeout_error()
    else:
        answer = MOCK_ANSWER[kind]
        err = map_error(answer.status, answer.code)
    assert err.reason_code in REFUSALS
    assert err.reason_code not in LINE_OUTCOMES


def test_429_answers_from_last_known_and_5xx_is_a_carrier_error() -> None:
    assert map_error(429, "TOO_MANY_REQUESTS").reason_code is ReasonCode.STALE_DATA
    assert map_error(500, "INTERNAL").reason_code is ReasonCode.CARRIER_ERROR
    assert breaker_open().reason_code is ReasonCode.STALE_DATA  # five 5xx in a row: still a refusal
