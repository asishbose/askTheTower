"""tests/fixtures is data the other layers rely on: every CloudEvents sample is valid for the vendored schemas
Alerts validates against, and every scenario file is one the mock can load."""

from __future__ import annotations

import pytest
from alerts.hooks import validate_event
from mock_carrier.scenarios import list_scenarios

from tests.fixtures import CLOUDEVENTS, SCENARIOS, cloudevent, cloudevents, scenario
from tests.helpers.patterns import phone_hits

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", sorted(p.stem for p in CLOUDEVENTS.glob("*.json")))
def test_cloudevent_samples_validate(name: str) -> None:
    kind = name.split(".", 1)[0]
    event = cloudevent(name)
    assert validate_event(kind, event) == event["type"]
    assert event["type"].endswith("." + name.split(".", 1)[1])
    assert not phone_hits((CLOUDEVENTS / f"{name}.json").read_text(encoding="utf-8"))


def test_samples_cover_the_event_types_alerts_handles() -> None:
    names = set(cloudevents())
    assert {
        "sim-swap.swapped",
        "reachability.reachability-disconnected",
        "reachability.reachability-data",
    } <= names
    assert {"sim-swap.subscription-ended", "reachability.subscription-ended"} <= names


def test_scenarios_are_the_mocks() -> None:
    assert scenario("demo").parent == SCENARIOS
    assert {"demo", "transplant"} <= set(list_scenarios(SCENARIOS))
    with pytest.raises(KeyError):
        scenario("no-such-scenario")
