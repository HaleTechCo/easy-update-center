"""status.json on the data volume, served at /status.json (docs/reference/status-json.md)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

STATUS_FILE = 'status.json'


def read_status(data_dir: Path) -> dict[str, Any]:
    """Return status.json as a dict; empty when it is missing, unreadable JSON or not an object."""
    try:
        value = json.loads((data_dir / STATUS_FILE).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_status(data_dir: Path, status: dict[str, Any]) -> None:
    """Replace status.json atomically (write a world-readable temporary file, then rename it)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / '.status.json.tmp'
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.chmod(0o644)
    tmp.replace(data_dir / STATUS_FILE)


def update_status(data_dir: Path, **fields: Any) -> dict[str, Any]:
    """Merge fields into status.json at the top level and return the result."""
    status = read_status(data_dir)
    status.update(fields)
    write_status(data_dir, status)
    return status
