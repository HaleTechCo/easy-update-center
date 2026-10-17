"""Exception types shared by the uc CLI.

Every expected failure is a UcError subclass.
"""

import re
from collections.abc import Iterable

_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s)\]\'"]+')
_DOCS = re.compile(r'\(docs/[\w./#-]+\)')
"""A pointer to this project's documentation, which a public warning may keep."""
LOGGED = '(see the generator log)'
"""What a public warning says in place of a parenthesised detail."""


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


def _closing(text: str, start: int) -> int:
    """Return the index just past the ')' that closes the '(' at start.

    Nested pairs count; len(text) when it is never closed.
    """
    depth = 0
    for index in range(start, len(text)):
        if text[index] == '(':
            depth += 1
        elif text[index] == ')':
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def _without_details(warning: str) -> str:
    """Return warning with each parenthesised detail after a space as LOGGED.

    Such as an exception's text, which can name an internal host without a URL. A
    pointer to the docs stays, and so do plurals such as 'artifact(s)'.
    """
    parts, done = [], 0
    while (start := warning.find(' (', done)) != -1:
        end = _closing(warning, start + 1)
        group = warning[start + 1 : end]
        parts += [
            warning[done : start + 1],
            group if _DOCS.fullmatch(group) else LOGGED,
        ]
        done = end
    return ''.join([*parts, warning[done:]])


def public_warnings(warnings: Iterable[str]) -> list[str]:
    """Return what /status.json may say of the warnings: what failed, not the details.

    Each parenthesised detail becomes '(see the generator log)' and any other URL
    <url>: either can name an internal host or path, and the generator log keeps the
    full text (docs/reference/status-json.md).
    """
    return [_URL.sub('<url>', _without_details(warning)) for warning in warnings]
