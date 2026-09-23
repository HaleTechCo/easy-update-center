"""Everything update-center2 does not write itself (spec §6 step 8), as upstream site/generate.sh:135-166."""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from . import http
from .errors import UcError

if TYPE_CHECKING:
    from pathlib import Path

    from .model import Tiers

TOP_LEVEL_LINKS = (
    'latest',
    'latestCore.txt',
    'plugin-documentation-urls.json',
    'release-history.json',
    'plugin-versions.json',
    'update-center.json',
    'update-center.actual.json',
)
_INSTALLER_HREF = re.compile(r'href="([A-Za-z0-9._-]+\.json(?:\.html)?)"')
_JSONP_PREFIX = b'downloadService.post('


class ToolInstallersError(UcError):
    """The tool-installer index lists nothing, or a listed file is not tool installer metadata."""


def site_dirs(root: Path, tiers: Tiers) -> list[Path]:
    """Return every update site directory under root: one per weekly and stable tier, experimental, current."""
    return [root / f'dynamic-{v}' for v in tiers.weekly] + [root / f'dynamic-stable-{v}' for v in tiers.stable] + [root / 'experimental', root / 'current']


def _symlink(target: str, link: Path) -> None:
    if link.is_symlink() or link.is_file():
        link.unlink()
    link.symlink_to(target)


def finalize_layout(staging: Path, tiers: Tiers, templates: Path) -> None:
    """Add the shared updates/ directory, the stable and top-level symlinks, uctest.json and index.html."""
    (staging / 'updates').mkdir(exist_ok=True)
    for site in site_dirs(staging, tiers):
        _symlink('../updates', site / 'updates')
    _symlink(f'dynamic-stable-{tiers.stable[-1]}' if tiers.stable else 'current', staging / 'stable')
    for name in TOP_LEVEL_LINKS:
        _symlink(f'current/{name}', staging / name)
    (staging / 'uctest.json').write_text('{}\n')
    shutil.copyfile(templates / 'index.html', staging / 'index.html')


def _fetch_tool_installers(index_url: str, get: http.Getter) -> dict[str, bytes]:
    """Return every file the index links to, by name; raise ToolInstallersError if it lists none or a .json is not JSONP."""
    names = sorted(set(_INSTALLER_HREF.findall(get(index_url).decode('utf-8', 'replace'))))
    if not names:
        msg = f'{index_url} lists no tool installer files'
        raise ToolInstallersError(msg)
    fetched: dict[str, bytes] = {}
    for name in names:
        body = get(urljoin(index_url, name))
        if name.endswith('.json') and not body.lstrip(b'\xef\xbb\xbf \r\n\t').startswith(_JSONP_PREFIX):
            msg = f'{name} does not look like tool installer metadata'
            raise ToolInstallersError(msg)
        fetched[name] = body
    return fetched


def mirror_tool_installers(index_url: str, dest: Path, previous: Path | None, get: http.Getter = http.get) -> list[str]:
    """Copy upstream's signed tool-installer metadata unmodified; fall back to the previous copy."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        fetched = _fetch_tool_installers(index_url, get)
    except (http.HttpError, ToolInstallersError) as exc:
        if previous is not None and previous.is_dir():
            for item in previous.iterdir():
                if item.is_file():
                    shutil.copyfile(item, dest / item.name)
            return [f'tool installers not refreshed ({exc}); kept the previous copy']
        return [f'tool installers not mirrored ({exc})']
    for name, body in fetched.items():
        (dest / name).write_bytes(body)
    return []
