"""The release this uc reports (uc --version, status.json uc_version, the HTTP User-Agent): the image's UC_VERSION build argument, dev for a build from source."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

DEV = 'dev'


def uc_version(env: Mapping[str, str] | None = None) -> str:
    """Return UC_VERSION from env (by default the process environment), or dev when it is unset or empty."""
    return (os.environ if env is None else env).get('UC_VERSION', '') or DEV
