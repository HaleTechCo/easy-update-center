"""status.json on the data volume, served at /status.json (spec §8.4)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATUS_FILE = 'status.json'


def read_status(data_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((data_dir / STATUS_FILE).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_status(data_dir: Path, status: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / '.status.json.tmp'
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o644)
    os.replace(tmp, data_dir / STATUS_FILE)


def update_status(data_dir: Path, **fields: Any) -> dict[str, Any]:
    status = read_status(data_dir)
    status.update(fields)
    write_status(data_dir, status)
    return status
