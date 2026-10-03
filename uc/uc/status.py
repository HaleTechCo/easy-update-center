"""status.json on the data volume, served at /status.json (docs/reference/status-json.md).

Every write holds DATA/.status.lock, so a generation and a recorded doctor report never lose each other's fields.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

STATUS_FILE = 'status.json'
LOCK_FILE = '.status.lock'


def read_status(data_dir: Path) -> dict[str, Any]:
    """Return status.json as a dict; empty when it is missing, unreadable JSON or not an object."""
    try:
        value = json.loads((data_dir / STATUS_FILE).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@contextlib.contextmanager
def locked(data_dir: Path) -> Iterator[None]:
    """Hold DATA/.status.lock, waiting while another writer holds it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def write_status(data_dir: Path, status: dict[str, Any], *, keep: Iterable[str] = ()) -> None:
    """Replace status.json atomically; a field named in keep that status leaves None keeps its current value.

    A name in keep can also be block.field, a field inside a block: when both documents have the block and status's
    leaves the field None (or out), the field keeps its current value. uc generate keeps doctor.missing_cores and
    doctor.recorded_at so, which only uc doctor --record works out.
    """
    with locked(data_dir):
        current = read_status(data_dir) if keep else {}
        merged = dict(status)
        for name in keep:
            block, _, field = name.partition('.')
            if not field:
                if merged.get(name) is None and current.get(name) is not None:
                    merged[name] = current[name]
                continue
            mine, theirs = merged.get(block), current.get(block)
            if isinstance(mine, dict) and isinstance(theirs, dict) and mine.get(field) is None and theirs.get(field) is not None:
                merged[block] = {**mine, field: theirs[field]}
        _write(data_dir, merged)


def update_status(data_dir: Path, **fields: Any) -> dict[str, Any]:
    """Merge fields into status.json at the top level and return the result."""
    with locked(data_dir):
        status = read_status(data_dir)
        status.update(fields)
        _write(data_dir, status)
    return status


def _write(data_dir: Path, status: dict[str, Any]) -> None:
    """Write a world-readable temporary file, then rename it over status.json."""
    tmp = data_dir / '.status.json.tmp'
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.chmod(0o644)
    tmp.replace(data_dir / STATUS_FILE)
