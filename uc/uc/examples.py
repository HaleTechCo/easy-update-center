"""The example settings files, generated from the settings registry: uc config --example env|toml (docs/reference/configuration.md)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .settings import GROUPS, SECTIONS, SETTINGS, toml_key

if TYPE_CHECKING:
    from .settings import Setting

ENV_HEADER = """\
# Settings for the update center in {mode} mode, one variable per setting. Copy this file to .env and
# edit the copy; a line starting with # is at its default, so remove the # to set it.
{note}# Generated from the settings registry: `uc config --example env --mode {mode}` (make examples).
# Any of these can go in config/uc.toml instead (config/uc.example.toml); a variable here wins over it.
# Every setting: docs/reference/configuration.md
"""

TOML_HEADER = """\
# Settings for the update center in {mode} mode. Copy this file to config/uc.toml and edit the copy;
# a line starting with # is at its default, so remove the # to set it.
{note}# Generated from the settings registry: `uc config --example toml --mode {mode}` (make examples).
# A variable in .env wins over this file: [section] key is SECTION_KEY, and a top-level key is KEY.
# Every setting: docs/reference/configuration.md
"""

MODE_NOTES = {
    'online': (
        'Online, the generator reads repo.jenkins-ci.org with a free Jenkins community account',
        '(secrets/repo_*), and controllers download from get.jenkins.io, unless you set your own',
        'repository below.',
    ),
    'airgapped': (
        'Air-gapped, the generator reads your repository through the repo-adapter',
        '(docs/how-to/stock-airgapped-repository.md), and controllers download from it.',
    ),
}
"""For each mode's header: where the generator reads plugins from and where controllers download them."""


def shown(setting: Setting, mode: str) -> tuple[bool, str]:
    """Return whether the example for mode sets setting (an uncommented line), and the value it shows."""
    if setting.env == 'MODE':
        return True, mode
    return mode in setting.active, setting.sample or setting.default_for(mode)


def example_env(mode: str) -> str:
    """Return the example .env for mode: every setting, grouped as docs/reference/configuration.md groups them."""
    parts = [ENV_HEADER.format(mode=mode, note=_note(mode))]
    for group in GROUPS:
        members = [setting for setting in SETTINGS if setting.group == group]
        if not members:
            continue
        parts.append(f'\n# ---- {group} ----\n')
        for setting in members:
            active, value = shown(setting, mode)
            parts.append(_comments(setting))
            parts.append(f'{"" if active else "# "}{setting.env}={value}\n')
    return ''.join(parts)


def example_toml(mode: str) -> str:
    """Return the example uc.toml for mode: every setting the file can hold, the top-level keys first, then each section."""
    parts = [TOML_HEADER.format(mode=mode, note=_note(mode))]
    for section in (None, *SECTIONS):
        members = [setting for setting in SETTINGS if not setting.env_only and toml_key(setting.env)[0] == section]
        if not members:
            continue
        parts.append(f'\n[{section}]\n' if section else '\n')
        for setting in members:
            active, value = shown(setting, mode)
            parts.append(_comments(setting, f' ({setting.env})'))
            parts.append(f'{"" if active else "# "}{toml_key(setting.env)[1]} = {_toml_value(setting, value)}\n')
    return ''.join(parts)


def _note(mode: str) -> str:
    return ''.join(f'# {line}\n' for line in MODE_NOTES[mode])


def _comments(setting: Setting, suffix: str = '') -> str:
    return ''.join(f'# {line}\n' for line in (setting.doc + suffix, *setting.hint))


def _toml_value(setting: Setting, value: str) -> str:
    if value and setting.type in {'int', 'ratio'}:
        return value
    if value and setting.type == 'bool':
        return value.lower()
    return json.dumps(value)
