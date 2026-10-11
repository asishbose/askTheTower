"""Environment configuration for the Alerts service (README "Config" lists every variable).

| Variable | Default | Meaning |
|---|---|---|
| `ALERTS_MODE` | `local` | `lambda` (SNS sender, system clock), `local` (log sender, FastAPI + in-process scheduler) or `k8s` (SNS sender, FastAPI without a scheduler: CronJobs run `python -m alerts.job`) |
| `HOOKS_BASE_URL` | `https://alerts.local` | public base of `/hooks/...`; CAMARA sinks must be `https://` |
| `INTERNAL_BEARER` | — | bearer for `/internal/*` and `/inbound/sms`; unset → those routes refuse (fail closed) |
| `SNS_TOPIC_ARN` | — | the two-way-SMS inbound topic (ack replies) — recorded for Terraform; outbound SMS publishes by number |
| `ALERTS_SENDER` | `sns` (lambda, k8s) / `log` (local) | `sns`, `log` or `webhook` |
| `SMS_GATEWAY_URL` | — | local only: also POST each SMS to this URL (`{"to", "body"}`) — e.g. an Android SMS gateway |
| `ALERTS_CLOCK` | `system` | `system` or `mock` (read `GET <CARRIER_BASE_URL>/_admin/clock` so windows follow the mock clock) |
| `ALERTS_CLOCK_SCALE` | `1` | local scheduler: cadences are divided by this (60 → a 5-min poll every 5 s) |
| `ALERTS_TRANSPLANT_POLL_S` / `ALERTS_CARE_POLL_S` / `ALERTS_DAILY_POLL_S` / `ALERTS_TICK_S` | 300 / 1800 / 86400 / 60 | local scheduler cadences in *simulated* seconds (10 §3) |
| `ALERTS_DEFAULT_TZ` | `America/Toronto` | line-holder zone when the Users row has no `tz` attribute |
| `ALERTS_SUBSCRIPTION_TTL_H` | `720` | CAMARA subscription lifetime (30 days); watchdog re-subscribes after TTL/2 without events |
| `ALERTS_PORT` | `8082` | local HTTP port |
| `CARRIER_*` | see camara_client | `CARRIER_PROFILE` defaults to `proactive`, `CARRIER_CLIENT_ID` to `alerts` here |
| `TOWER_DYNAMODB_ENDPOINT` (alias `DYNAMO_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | — | tower_consent `Store.from_env` |
| `TOWER_ENV`, `TOWER_LINE_ID_KEY`, `TOWER_MSISDN_KEY` / KMS ids | — | tower_consent `crypto_from_env` |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

Mode = Literal["lambda", "local", "k8s"]
SenderKind = Literal["sns", "log", "webhook"]
ClockKind = Literal["system", "mock"]


@dataclass(frozen=True)
class Settings:
    mode: Mode = "local"
    hooks_base_url: str = "https://alerts.local"
    internal_bearer: str | None = None
    sns_topic_arn: str | None = None
    sender: SenderKind = "log"
    sms_gateway_url: str | None = None
    clock: ClockKind = "system"
    clock_scale: float = 1.0
    transplant_poll_s: float = 300.0
    care_poll_s: float = 1800.0
    daily_poll_s: float = 86400.0
    tick_s: float = 60.0
    default_tz: str = "America/Toronto"
    subscription_ttl: timedelta = timedelta(days=30)
    port: int = 8082

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        raw_mode = e.get("ALERTS_MODE", "local")
        mode: Mode = raw_mode if raw_mode in ("lambda", "k8s") else "local"  # type: ignore[assignment]
        default_sender: SenderKind = "log" if mode == "local" else "sns"
        sender = e.get("ALERTS_SENDER") or default_sender
        if sender not in ("sns", "log", "webhook"):
            raise ValueError(f"ALERTS_SENDER must be sns|log|webhook, got {sender!r}")
        clock = e.get("ALERTS_CLOCK", "system")
        if clock not in ("system", "mock"):
            raise ValueError(f"ALERTS_CLOCK must be system|mock, got {clock!r}")
        scale = float(e.get("ALERTS_CLOCK_SCALE", "1") or 1)
        if scale <= 0:
            raise ValueError("ALERTS_CLOCK_SCALE must be > 0")
        return cls(
            mode=mode,
            hooks_base_url=e.get("HOOKS_BASE_URL", "https://alerts.local").rstrip("/"),
            internal_bearer=e.get("INTERNAL_BEARER") or None,
            sns_topic_arn=e.get("SNS_TOPIC_ARN") or None,
            sender=sender,  # type: ignore[arg-type]
            sms_gateway_url=e.get("SMS_GATEWAY_URL") or None,
            clock=clock,  # type: ignore[arg-type]
            clock_scale=scale,
            transplant_poll_s=float(e.get("ALERTS_TRANSPLANT_POLL_S", "300")),
            care_poll_s=float(e.get("ALERTS_CARE_POLL_S", "1800")),
            daily_poll_s=float(e.get("ALERTS_DAILY_POLL_S", "86400")),
            tick_s=float(e.get("ALERTS_TICK_S", "60")),
            default_tz=e.get("ALERTS_DEFAULT_TZ", "America/Toronto"),
            subscription_ttl=timedelta(hours=float(e.get("ALERTS_SUBSCRIPTION_TTL_H", "720"))),
            port=int(e.get("ALERTS_PORT", "8082")),
        )


def carrier_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The process environment with Alerts' carrier defaults: proactive profile, `alerts` client id."""
    e = dict(os.environ if env is None else env)
    e.setdefault("CARRIER_PROFILE", "proactive")
    e.setdefault("CARRIER_CLIENT_ID", "alerts")
    if "DYNAMO_ENDPOINT" in e and "TOWER_DYNAMODB_ENDPOINT" not in e:
        e["TOWER_DYNAMODB_ENDPOINT"] = e["DYNAMO_ENDPOINT"]
    return e
