"""Deterministic policy engine: reason codes, thresholds, phrasing templates. Shared by Tower and Alerts.

Pure: no I/O (except `load_thresholds`), no clock, no randomness, no logging. `now` is an argument.
"""

from tower_policy.codes import AUDIT_ONLY_CODES, ENGINE_CODES, ReasonCode
from tower_policy.engine import evaluate_line, evaluate_reachability, within
from tower_policy.phrasing import TEMPLATES, Form, phrase, phrase_code, render_time
from tower_policy.thresholds import (
    DEFAULT_THRESHOLDS_PATH,
    ThresholdError,
    Thresholds,
    default_thresholds,
    load_thresholds,
    parse_thresholds,
)
from tower_policy.types import ConsentView, Facts, Outcome, OutcomeKind
from tower_policy.version import policy_version

__all__ = [
    "AUDIT_ONLY_CODES",
    "DEFAULT_THRESHOLDS_PATH",
    "ENGINE_CODES",
    "TEMPLATES",
    "ConsentView",
    "Facts",
    "Form",
    "Outcome",
    "OutcomeKind",
    "ReasonCode",
    "ThresholdError",
    "Thresholds",
    "default_thresholds",
    "evaluate_line",
    "evaluate_reachability",
    "load_thresholds",
    "parse_thresholds",
    "phrase",
    "phrase_code",
    "policy_version",
    "render_time",
    "within",
]
