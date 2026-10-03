"""web.env: the web container's settings, worked out by uc for the web-config service (docs/reference/cli.md#uc-config).

The web image has no Python, so it cannot read uc.toml or apply the mode defaults; web/entrypoint.sh reads this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

    from .config import WebSettings

WEB_SETTINGS = ('PUBLIC_URL', 'DOWNLOAD_BACKEND', 'DOWNLOAD_BACKEND_URL', 'DOWNLOAD_ACCESS', 'DOWNLOAD_PROXY_AUTH_FILE', 'DOWNLOAD_PROXY_CA_FILE', 'TLS_CERT_FILE', 'TLS_KEY_FILE')
"""What web/entrypoint.sh reads, in the order web.env lists it."""


def web_env(settings: WebSettings) -> str:
    """Return web.env for settings: one NAME=value line per web setting."""
    lines = []
    for name in WEB_SETTINGS:
        value = str(getattr(settings, name.lower()))
        if '\n' in value or '\r' in value:
            msg = f'{name} must be on one line'
            raise ConfigError(msg)
        lines.append(f'{name}={value}\n')
    return ''.join(lines)


def write_web_env(text: str, path: Path) -> None:
    """Write web.env atomically and world-readable: the web container reads it as uid 33."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.chmod(0o644)
    tmp.replace(path)
