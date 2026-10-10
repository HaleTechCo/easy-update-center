"""uc config: every setting, its value and where it came from (docs/reference/cli.md#uc-config)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from .config import make_resolver, read_secret, settings_from, split_userinfo
from .errors import ConfigError
from .settings import GROUPS, REGISTRY, SETTINGS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .config import Resolver, Settings
    from .settings import Setting

SOURCE_NAMES = {'env': 'environment'}


@dataclass(frozen=True)
class Row:
    """One setting as uc config shows it."""

    env: str
    value: str
    source: str  # env, mode, derived or default
    note: str = ''  # for secret files: present, empty, missing or unreadable


def rows(r: Resolver, settings: Settings | None) -> list[Row]:
    """Return every setting in registry order: its effective value when settings could be built, else its raw value, and its source."""
    return [_row(setting, r, settings) for setting in SETTINGS]


def _row(setting: Setting, r: Resolver, settings: Settings | None) -> Row:
    if setting.env_only:
        given = r.env.get(setting.env, '').strip()
        return Row(setting.env, _redact(setting, given or setting.default), 'env' if given else 'default')
    source = r.source(setting.env)
    if source == 'default' and setting.derived:
        source = 'derived'
    elif source == 'default' and (setting.online is not None or setting.airgapped is not None):
        source = 'mode'
    value = _show(getattr(settings, setting.attribute)) if settings is not None and setting.attribute else r.raw(setting.env)
    note = _presence(Path(value)) if setting.secret_file and value else ''
    return Row(setting.env, _redact(setting, value), source, note)


def _redact(setting: Setting, value: str) -> str:
    """Return a URL setting's value with its user info, everything before the last '@' (split_userinfo), shown as ***, so credentials never reach a terminal or a log."""
    if setting.type != 'url':
        return value
    scheme, userinfo, rest = split_userinfo(value)
    return value if userinfo is None else f'{scheme}***@{rest}'


def _show(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, tuple):
        return ' '.join(value)
    return '' if value is None else str(value)


def _presence(path: Path) -> str:
    try:
        if not path.exists():
            return 'missing'
        return 'present' if read_secret(path) else 'empty'
    except OSError:
        return 'unreadable'


def format_rows(shown: list[Row]) -> str:
    """Return the text uc config prints: each group of settings with its value and source."""
    lines: list[str] = []
    for group in GROUPS:
        members = [row for row in shown if REGISTRY[row.env].group == group]
        if not members:
            continue
        lines += ['', group]
        for row in members:
            where = SOURCE_NAMES.get(row.source, row.source)
            notes = f'; {row.note}' if row.note else ''
            lines.append(f'  {row.env} = {row.value}  ({where}{notes})')
    return '\n'.join(lines).lstrip('\n') + '\n'


def run(
    *,
    as_json: bool = False,
    check: bool = False,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print every setting (as text or JSON), or only check them; return 0, or 1 when they are invalid."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    r = make_resolver(env)
    try:
        settings = settings_from(r)
    except ConfigError as exc:
        if not (as_json or check):
            out.write(format_rows(rows(r, None)))
        err.write(f'uc: error: {exc}\n')
        return 1
    if check:
        out.write('configuration ok\n')
        return 0
    if as_json:
        out.write(json.dumps([asdict(row) for row in rows(r, settings)], indent=2) + '\n')
        return 0
    out.write(format_rows(rows(r, settings)))
    return 0
