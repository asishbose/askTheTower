"""Scenario files (`scenarios/*.yaml`) → initial clock, lines and timeline.

Format (see `scenarios/demo.yaml`, which is `docs/architecture/components/08` §2 verbatim):

    clock: "2026-10-05T14:00:00Z"        # start of the mock clock (or the word `now`)
    lines:
      "+16135550101":
        sim_change_at: "2026-09-01T10:00:00Z"   # or null
        call_forwarding: none                  # none | unconditional | conditional | [spec enum values]
        reachable: true
        connectivity: DATA                     # DATA | SMS | [DATA, SMS] | none
        mobile_data_client_ids: [phone-asish]  # simulation aid: who may network-authenticate as this line
        last_status_at: "…"                    # optional; defaults to `clock`
    timeline:                                  # optional, fires as the clock advances
      - at: "+00:12:00"                        # relative to `clock`, or an absolute RFC 3339 time
        line: "+16135550101"
        event: sim_swap                        # sim_swap | cf_set | cf_clear | reachable | unreachable
        connectivity: [DATA]                   # optional, for `reachable`
    variants:                                  # optional named timeline replacements
      recovered:
        timeline: [...]

`load_scenario(dir, "transplant", variant="recovered")` or `load_scenario(dir, "transplant:recovered")`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from mock_carrier.clock import Clock, parse_iso, parse_offset
from mock_carrier.state import EVENT_NAMES, Line, TimelineEvent


class ScenarioError(ValueError):
    pass


@dataclass
class Scenario:
    name: str
    variant: str | None
    clock: datetime
    lines: dict[str, Line]
    timeline: list[TimelineEvent]


def list_scenarios(scenarios_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(scenarios_dir).glob("*.yaml"))


def _connectivity(value: Any) -> list[str]:
    if value is None or value is False or value == "none":
        return []
    if isinstance(value, str):
        return [value.upper()]
    if isinstance(value, list):
        return [str(v).upper() for v in value]
    raise ScenarioError(f"connectivity must be DATA, SMS, a list or none; got {value!r}")


def _call_forwarding(value: Any) -> Any:
    if value is None:
        return "none"
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value).lower()
    if text not in ("none", "unconditional", "conditional"):
        raise ScenarioError(
            f"call_forwarding must be none|unconditional|conditional or a list; got {value!r}"
        )
    return text


def _parse_lines(raw_lines: dict[str, Any], clock: datetime) -> dict[str, Line]:
    lines: dict[str, Line] = {}
    for msisdn, attrs in (raw_lines or {}).items():
        attrs = attrs or {}
        msisdn = str(msisdn)
        if not msisdn.startswith("+"):
            raise ScenarioError(f"line keys must be E.164 with a leading '+': {msisdn!r}")
        sim_change = attrs.get("sim_change_at")
        last_status = attrs.get("last_status_at")
        lines[msisdn] = Line(
            msisdn=msisdn,
            sim_change_at=parse_iso(str(sim_change)) if sim_change else None,
            call_forwarding=_call_forwarding(attrs.get("call_forwarding")),
            reachable=bool(attrs.get("reachable", True)),
            connectivity=_connectivity(attrs.get("connectivity", "DATA")),
            mobile_data_client_ids=[str(c) for c in attrs.get("mobile_data_client_ids", []) or []],
            last_status_at=parse_iso(str(last_status)) if last_status else clock,
        )
    return lines


def _parse_timeline(
    raw: list[dict[str, Any]] | None, clock: datetime, lines: dict[str, Line]
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for entry in raw or []:
        at_raw = str(entry["at"])
        at = clock + parse_offset(at_raw) if at_raw.startswith("+") else parse_iso(at_raw)
        line = str(entry["line"])
        if line not in lines:
            raise ScenarioError(f"timeline references unknown line {line!r}")
        event = str(entry["event"])
        if event not in EVENT_NAMES:
            raise ScenarioError(f"unknown timeline event {event!r}; expected one of {EVENT_NAMES}")
        conn = entry.get("connectivity")
        events.append(
            TimelineEvent(at=at, line=line, event=event, connectivity=_connectivity(conn) if conn else None)
        )
    events.sort(key=lambda e: e.at)
    return events


def load_scenario(scenarios_dir: Path, name: str, variant: str | None = None) -> Scenario:
    if ":" in name and variant is None:
        name, variant = name.split(":", 1)
    if "/" in name or name.startswith("."):
        raise ScenarioError(f"invalid scenario name {name!r}")
    path = Path(scenarios_dir) / f"{name}.yaml"
    if not path.exists():
        raise ScenarioError(f"scenario {name!r} not found in {scenarios_dir}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    clock_raw = raw.get("clock")
    if clock_raw is None:
        raise ScenarioError("scenario needs a `clock`")
    clock = Clock.wall_now() if str(clock_raw).strip().lower() == "now" else parse_iso(str(clock_raw))
    lines = _parse_lines(raw.get("lines") or {}, clock)
    timeline_raw = raw.get("timeline")
    if variant:
        variants = raw.get("variants") or {}
        if variant not in variants:
            raise ScenarioError(f"scenario {name!r} has no variant {variant!r}")
        timeline_raw = (variants[variant] or {}).get("timeline", timeline_raw)
    return Scenario(
        name=name,
        variant=variant,
        clock=clock,
        lines=lines,
        timeline=_parse_timeline(timeline_raw, clock, lines),
    )
