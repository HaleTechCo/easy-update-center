"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path.

The repo-adapter is off unless a test turns it on, so tests read the repository directly.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from uc.config import Settings, load_settings

if TYPE_CHECKING:
    from pathlib import Path


def make_settings(tmp_path: Path, secrets: Path, **env: str) -> Settings:
    """Load the settings from an environment: UPPER_CASE keywords add to it; lower-case ones replace that field of the result."""
    base = {
        'MODE': 'airgapped',
        'PUBLIC_URL': 'https://uc.example.com',
        'REPO_URL': 'http://artifactory:8081/artifactory/',
        'REPO_KEY': 'jenkins',
        'DOWNLOAD_BACKEND_URL': 'http://artifactory:8081/artifactory/jenkins',
        'REPO_ADAPTER': 'off',
        'SECRETS_DIR': str(secrets),
        'UC_DATA_DIR': str(tmp_path / 'data'),
        'UC_OVERLAY_DIR': str(tmp_path / 'overlay'),
    }
    base.update({name: value for name, value in env.items() if name.isupper()})
    return dataclasses.replace(load_settings(base), **{name: value for name, value in env.items() if not name.isupper()})
