"""uc config: every setting, its value and where it came from (docs/reference/cli.md#uc-config)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO
from urllib.parse import urlsplit, urlunsplit

from .config import make_resolver, read_secret, settings_digest, settings_from
from .errors import ConfigError
from .settings import GROUPS, REGISTRY, SETTINGS, toml_label

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .config import Resolver, Settings
    from .settings import Setting

SCHEDULER_STATE = 'scheduler.json'
"""Written to the data directory by uc schedule when it starts: when, and with which settings (settings_digest)."""
RELOAD = 'docker compose up -d --force-recreate (make reload)'
SOURCE_NAMES = {'env': 'environment'}


@dataclass(frozen=True)
class Row:
    """One setting as uc config shows it."""

    env: str
    key: str  # its uc.toml key, '' for the settings only the environment holds
    value: str
    source: str  # env, file, mode, derived or default
    note: str = ''  # for secret files: present, empty, missing or unreadable


def rows(r: Resolver, settings: Settings | None) -> list[Row]:
    """Return every setting in registry order: its effective value when settings could be built, else its raw value, and its source."""
    return [_row(setting, r, settings) for setting in SETTINGS]


def _row(setting: Setting, r: Resolver, settings: Settings | None) -> Row:
    if setting.env_only:
        given = r.env.get(setting.env, '').strip()
        return Row(setting.env, '', _redact(setting, given or setting.default), 'env' if given else 'default')
    source = r.source(setting.env)
    if source == 'default' and setting.derived:
        source = 'derived'
    elif source == 'default' and (setting.online is not None or setting.airgapped is not None):
        source = 'mode'
    value = _show(getattr(settings, setting.attribute)) if settings is not None and setting.attribute else r.raw(setting.env)
    note = _presence(Path(value)) if setting.secret_file and value else ''
    return Row(setting.env, toml_label(setting.env), _redact(setting, value), source, note)


def _redact(setting: Setting, value: str) -> str:
    """Return a URL setting's value with any username and password replaced by ***, so credentials never reach a terminal or a log."""
    if setting.type != 'url':
        return value
    try:
        parts = urlsplit(value)
        if parts.username is None and parts.password is None:
            return value
        host = parts.netloc.rpartition('@')[2]
        return urlunsplit(parts._replace(netloc=f'***@{host}'))
    except ValueError:
        return value


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


def format_rows(shown: list[Row], config_file: Path) -> str:
    """Return the text uc config prints: the settings file, then each group of settings with its value and source."""
    found = 'read' if config_file.is_file() else 'not found: only the environment and the defaults apply'
    lines = [f'Settings file: {config_file} ({found})']
    for group in GROUPS:
        members = [row for row in shown if REGISTRY[row.env].group == group]
        if not members:
            continue
        lines += ['', group]
        for row in members:
            where = f'uc.toml: {row.key}' if row.source == 'file' else SOURCE_NAMES.get(row.source, row.source)
            note = f'; {row.note}' if row.note else ''
            lines.append(f'  {row.env} = {row.value}  ({where}{note})')
    return '\n'.join(lines) + '\n'


def reload_hint(settings: Settings) -> str | None:
    """Return a note when the scheduler started with other settings than these, else None."""
    try:
        state = json.loads((settings.data_dir / SCHEDULER_STATE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get('settings_digest') in {None, settings_digest(settings)}:
        return None
    return f'note: the scheduler started at {state.get("started_at", "an unknown time")} with other settings; to apply these, run: {RELOAD}'


def run(
    *,
    as_json: bool = False,
    check: bool = False,
    env: Mapping[str, str] | None = None,
    config_file: Path | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print every setting (as text or JSON), or only check them; return 0, or 1 when they are invalid."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        r = make_resolver(env, config_file=config_file)
    except ConfigError as exc:
        err.write(f'uc: error: {exc}\n')
        return 1
    try:
        settings = settings_from(r)
    except ConfigError as exc:
        if not (as_json or check):
            out.write(format_rows(rows(r, None), r.config_file))
        err.write(f'uc: error: {exc}\n')
        return 1
    if check:
        out.write('configuration ok\n')
        return 0
    if as_json:
        out.write(json.dumps([asdict(row) for row in rows(r, settings)], indent=2) + '\n')
        return 0
    out.write(format_rows(rows(r, settings), r.config_file))
    hint = reload_hint(settings)
    if hint:
        out.write(f'\n{hint}\n')
    return 0
