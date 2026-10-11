"""Consent and line-binding library: DynamoDB tables, HMAC/encryption helpers, resolve().

Hot path: `resolve(store, user_id, line)` — one GetItem/Query, no cache. Everything else is for the binding page
(09), Tower's `watch_line` (08) and Alerts (10).
"""

from tower_policy.types import ConsentView

from tower_consent import crypto, errors, tables
from tower_consent.bind import (
    BIND_TOKEN_TTL,
    bind_line,
    consume_bind_token,
    create_bind_token,
    get_bind_token,
    get_line,
    list_lines,
)
from tower_consent.crypto import (
    KmsLineIdHasher,
    KmsMsisdnCipher,
    LineIdHasher,
    LocalLineIdHasher,
    LocalMsisdnCipher,
    MsisdnCipher,
    crypto_from_env,
)
from tower_consent.grants import grant, list_granted_to, list_grants, revoke
from tower_consent.models import (
    BindToken,
    EscalationStep,
    Grant,
    LastState,
    Line,
    User,
    Watch,
    validate_alias,
)
from tower_consent.resolve import ResolvedConsent, resolve
from tower_consent.store import Store
from tower_consent.users import ensure_user, get_user, set_alert_phone
from tower_consent.watch_settings import MAX_CONTACTS, set_watch_settings
from tower_consent.watches import (
    chain_of,
    delete_watch,
    disable_watch,
    get_watch,
    list_watches_by_profile,
    list_watches_for_line,
    remove_contact,
    update_last_state,
    upsert_watch,
)

__all__ = [
    "BIND_TOKEN_TTL",
    "BindToken",
    "ConsentView",
    "EscalationStep",
    "Grant",
    "KmsLineIdHasher",
    "KmsMsisdnCipher",
    "LastState",
    "Line",
    "LineIdHasher",
    "LocalLineIdHasher",
    "LocalMsisdnCipher",
    "MAX_CONTACTS",
    "MsisdnCipher",
    "ResolvedConsent",
    "Store",
    "User",
    "Watch",
    "bind_line",
    "chain_of",
    "consume_bind_token",
    "create_bind_token",
    "crypto",
    "crypto_from_env",
    "delete_watch",
    "disable_watch",
    "ensure_user",
    "errors",
    "get_bind_token",
    "get_line",
    "get_user",
    "get_watch",
    "grant",
    "list_granted_to",
    "list_grants",
    "list_lines",
    "list_watches_by_profile",
    "list_watches_for_line",
    "remove_contact",
    "resolve",
    "revoke",
    "set_alert_phone",
    "set_watch_settings",
    "tables",
    "update_last_state",
    "upsert_watch",
    "validate_alias",
]
