"""Helpers for the procurement tests.

Files published at a fake source, staged records, and the settings and HTTP
client the push and clean tests share.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from uc.http import Http
from uc.procure.staging import StagedFile, Staging
from ucsupport import make_settings

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = 'https://repo.jenkins-ci.org/releases/'


def publish(root: Path, path: str, data: bytes) -> None:
    """Put data at path under root, as a repository in Maven layout holds it."""
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(data)


def sha1(data: bytes) -> str:
    """Return the SHA-1 of data as hex."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def record(path: str, data: bytes, *, kind: str = 'pom', **fields) -> StagedFile:
    """Return a procurement.json entry for data staged at path."""
    return StagedFile(
        path=path,
        url=SOURCE + path,
        sha1=sha1(data),
        size=len(data),
        kind=kind,
        reason='test',
        staged_at='2026-09-27T00:00:00Z',
        **fields,
    )


def stage(staging: Staging, path: str, data: bytes, **fields) -> None:
    """Commit data at path into staging, as a download would."""
    part = staging.temporary()
    part.write_bytes(data)
    staging.commit(record(path, data, **fields), part)


def staged(stage_dir: Path, files: dict[str, bytes], **fields) -> None:
    """Stage every file of files in stage_dir and save the manifest."""
    staging = Staging.open(stage_dir, SOURCE)
    for path, data in files.items():
        stage(staging, path, data, **fields)
    staging.save()


def keeper_settings(tmp_path: Path, secrets_dir: Path, url: str, auth='uc:tok'):
    """Return settings for an Artifact Keeper at url, with push credentials."""
    (secrets_dir / 'repo_push_auth').write_text(auth + '\n')
    return make_settings(
        tmp_path,
        secrets_dir,
        REPO_TYPE='artifact-keeper',
        REPO_URL=url,
        REPO_KEY='jenkins',
        REPO_ADAPTER='on',
    )


def artifactory_settings(tmp_path: Path, secrets_dir: Path, url: str):
    """Return settings for an Artifactory at url, with push credentials."""
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    return make_settings(tmp_path, secrets_dir, REPO_URL=url, REPO_KEY='jenkins')


class RecordingHttp(Http):
    """An Http that records what it is asked for.

    `calls` holds the method and URL of each request; `urls` the URL of each
    request and each download.
    """

    def __init__(self):
        """Verify TLS against the system bundle; nothing recorded yet."""
        super().__init__(None)
        self.calls = []
        self.urls = []

    def request(self, url, **kwargs):
        """Record the request, then make it."""
        self.calls.append((kwargs.get('method') or 'GET', url))
        self.urls.append(url)
        return super().request(url, **kwargs)

    def download(self, url, **kwargs):
        """Record the URL, then download it."""
        self.urls.append(url)
        return super().download(url, **kwargs)
