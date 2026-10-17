"""In-memory tar.gz archives for resources tests."""

from __future__ import annotations

import io
import tarfile

MTIME = 1_758_000_000


def make_tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = MTIME
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
