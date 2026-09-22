"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path."""
from __future__ import annotations

from pathlib import Path

from uc.config import Settings, load_settings


def make_settings(tmp_path: Path, secrets: Path, **env: str) -> Settings:
    base = {
        "MODE": "airgapped",
        "PUBLIC_URL": "https://uc.example.com",
        "ARTIFACTORY_URL": "http://artifactory:8081/artifactory/",
        "ARTIFACTORY_REPOSITORY": "jenkins",
        "SECRETS_DIR": str(secrets),
        "UC_DATA_DIR": str(tmp_path / "data"),
        "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
    }
    base.update(env)
    return load_settings(base)
