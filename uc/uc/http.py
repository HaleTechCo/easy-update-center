"""Minimal HTTP GET used for resources and tool installers.

urllib follows 301/302/303/307/308.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from .errors import UcError
from .version import uc_version

USER_AGENT = f'easy-update-center/{uc_version()} (+https://github.com/HaleTechCo/easy-update-center)'
Getter = Callable[..., bytes]


class HttpError(UcError):
    """A GET failed: connection error, timeout, invalid URL or an HTTP error status."""


def get(
    url: str, headers: Mapping[str, str] | None = None, timeout: float = 60
) -> bytes:
    """Return the body of url, sent with the uc User-Agent and any extra headers."""
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    # URLError and HTTPError are OSError subclasses
    except (OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()  # it holds the response
        msg = f'GET {url} failed: {exc}'
        raise HttpError(msg) from exc
