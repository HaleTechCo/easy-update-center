"""The example .env files, generated from the settings registry: uc config --example (docs/reference/configuration.md)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .settings import GROUPS, SETTINGS

if TYPE_CHECKING:
    from .settings import Setting

ENV_HEADER = """\
# Settings for the update center in {mode} mode, one variable per setting. Copy this file to .env and
# edit the copy; a line starting with # is not set: it shows the default or an example value, and the
# comment above it says what applies when it is unset. Remove the # to set it.
{note}# Generated from the settings registry: `uc config --example --mode {mode}` (make examples).
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
    return mode in setting.active, setting.sample_for(mode) or setting.default_for(mode)


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
            parts.append(_comments(setting, mode))
            parts.append(f'{"" if active else "# "}{setting.env}={value}\n')
    return ''.join(parts)


def _note(mode: str) -> str:
    return ''.join(f'# {line}\n' for line in MODE_NOTES[mode])


def _comments(setting: Setting, mode: str) -> str:
    return ''.join(f'# {line}\n' for line in (setting.doc, *setting.hint_for(mode)))
