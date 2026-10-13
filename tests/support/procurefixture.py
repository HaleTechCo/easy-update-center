"""Helpers for the procurement tests.

Files published at a fake source, and staged records.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from uc.procure.staging import StagedFile

if TYPE_CHECKING:
    from pathlib import Path

    from uc.procure.staging import Staging

SOURCE = 'https://repo.jenkins-ci.org/releases/'


def publish(root: Path, path: str, data: bytes) -> None:
    """Put data at path under root, as a repository in Maven layout holds it."""
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(data)


def record(path: str, data: bytes, *, kind: str = 'pom', **fields) -> StagedFile:
    """Return a procurement.json entry for data staged at path."""
    return StagedFile(
        path=path,
        url=SOURCE + path,
        sha1=hashlib.sha1(data, usedforsecurity=False).hexdigest(),
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
