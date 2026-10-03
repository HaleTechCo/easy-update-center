"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path.

The repo-adapter is off unless a test turns it on, so tests read the repository directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uc.config import Settings, load_settings

if TYPE_CHECKING:
    from pathlib import Path


def make_settings(tmp_path: Path, secrets: Path, **env: str) -> Settings:
    base = {
        'MODE': 'airgapped',
        'PUBLIC_URL': 'https://uc.example.com',
        'REPO_URL': 'http://artifactory:8081/artifactory/',
        'REPO_KEY': 'jenkins',
        'REPO_ADAPTER': 'off',
        'SECRETS_DIR': str(secrets),
        'UC_DATA_DIR': str(tmp_path / 'data'),
        'UC_OVERLAY_DIR': str(tmp_path / 'overlay'),
        'UC_CONFIG_FILE': str(tmp_path / 'uc.toml'),
    }
    base.update(env)
    return load_settings(base)
