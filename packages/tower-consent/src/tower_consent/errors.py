"""Errors raised by tower-consent. Messages never carry a phone number, a token or a key."""


class ConsentError(Exception):
    """Base class for every tower-consent error."""


class InvalidE164(ConsentError, ValueError):
    """The value is not an E.164 number (the message never echoes it)."""


class CryptoError(ConsentError):
    """A ciphertext failed to decrypt or authenticate, or a key is missing/invalid."""


class ConditionFailed(ConsentError):
    """A conditional write was rejected by DynamoDB."""


class LineNotFound(ConsentError):
    pass


class LineOwnedByOtherUser(ConsentError):
    """bind_line: the number is already bound to a different user."""


class NotLineOwner(ConsentError):
    """grant/revoke attempted by someone who does not own the line."""


class BindTokenRefused(ConsentError):
    """Token unknown, already used, expired, or issued to a different user. One error for all four on purpose."""


class InvalidAlias(ConsentError, ValueError):
    pass


class NotGrantable(ConsentError, ValueError):
    """Only `watch` and `reachability` can be granted; ownership is binding (04 §3)."""


class GrantToSelf(ConsentError):
    pass


class AliasCollision(ConsentError):
    """The grantee already has an unrevoked grant under this alias."""


class GrantExists(ConsentError):
    """An unrevoked grant of this kind already exists for (line, grantee)."""


class GrantNotFound(ConsentError):
    pass


class WatchNotFound(ConsentError):
    pass


class InvalidProfile(ConsentError, ValueError):
    """Watch settings: the profile is not `self`, `transplant` or `care` (04 §9.2)."""


class InvalidContacts(ConsentError, ValueError):
    """Watch settings: a contact without an active `watch` grant on the line, the owner, a duplicate, or > 3."""


class NoContact(ConsentError, ValueError):
    """Watch settings: `transplant` or `care` needs at least one person to text (04 §9.2)."""
