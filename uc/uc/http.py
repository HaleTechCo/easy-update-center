"""Minimal HTTP GET used for resources and tool installers (urllib follows 301/302/303/307/308)."""

from __future__ import annotations

import urllib.request
from typing import Callable, Mapping

from .errors import UcError

USER_AGENT = 'jenkins-uc/1'
Getter = Callable[..., bytes]


class HttpError(UcError):
    pass


def get(url: str, headers: Mapping[str, str] | None = None, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, ValueError) as exc:  # URLError and HTTPError are OSError subclasses
        raise HttpError(f'GET {url} failed: {exc}') from exc
