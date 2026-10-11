"""Call profiles (05 §5, 02 §4, 06 §8): the request path gets 300 ms and no retry — a retry would blow
the voice budget; the proactive path (Alerts) gets 5 s and one retry."""

from __future__ import annotations

from typing import Literal, NamedTuple

ProfileName = Literal["request", "proactive"]


class Profile(NamedTuple):
    timeout_s: float
    retries: int


PROFILES: dict[str, Profile] = {
    "request": Profile(timeout_s=0.300, retries=0),
    "proactive": Profile(timeout_s=5.0, retries=1),
}


def profile_for(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown carrier profile {name!r}; expected one of {sorted(PROFILES)}") from None
