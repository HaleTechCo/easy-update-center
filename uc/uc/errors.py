"""Exception types shared by the uc CLI.

Every expected failure is a UcError subclass.
"""

import re
from collections.abc import Iterable

_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s)\]\'"]+')


class UcError(Exception):
    """An expected failure: the CLI prints the message and exits 1."""


class UsageError(UcError):
    """The command line asks for nothing, or for something that cannot be done.

    uc exits 2, as for a bad argument.
    """


class ConfigError(UcError):
    """The environment does not describe a valid configuration."""


def public_summary(exc: BaseException) -> str:
    """Return what /status.json may say about a failure.

    The class, never the message (docs/reference/status-json.md).

    The full text reaches the container log, since generate() re-raises.
    """
    return f'{type(exc).__name__}: see the generator log'


def public_warnings(warnings: Iterable[str]) -> list[str]:
    """Return what /status.json may say of the warnings: each URL replaced by <url>.

    A URL can name an internal host or path; the generator log keeps the full text.
    """
    return [_URL.sub('<url>', warning) for warning in warnings]
