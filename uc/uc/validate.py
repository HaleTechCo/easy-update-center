"""Checks a staged release must pass before it becomes live (spec §6 step 9)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import UcError
from .htaccess import absolute_targets
from .layout import site_dirs
from .model import Tiers

JSONP_PREFIX = 'updateCenter.post('
SITE_FILES = ('update-center.json', 'update-center.actual.json')
CURRENT_FILES = SITE_FILES + (
    'plugin-versions.json',
    'release-history.json',
    'plugin-documentation-urls.json',
    'pluginCount.txt',
    'timestamp.txt',
)  # and latestCore.txt, when the site offers a core
ROOT_FILES = ('.htaccess', '.htdownload-map', 'tiers.json', 'uctest.json', 'index.html')
MAX_LISTED = 5


class ValidationError(UcError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__('release failed validation: ' + '; '.join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ValidationResult:
    plugin_count: int
    offered: frozenset[str]


def load_site_json(path: Path) -> Any:
    text = path.read_text(encoding='utf-8').strip()
    if text.startswith(JSONP_PREFIX):
        text = text[len(JSONP_PREFIX) :].rstrip()
        if text.endswith(');'):
            text = text[:-2]
    return json.loads(text)


def count_plugins(site: Path) -> int | None:
    try:
        return len(load_site_json(site / 'update-center.actual.json').get('plugins') or {})
    except (OSError, ValueError, AttributeError):
        return None


def download_key(url: str, downloads_root: str) -> str | None:
    prefix = downloads_root.rstrip('/') + '/'
    if not url.startswith(prefix):
        return None
    parts = url[len(prefix) :].split('/')
    if len(parts) == 4 and parts[0] == 'plugins' and parts[3] == f'{parts[1]}.hpi':
        return f'plugins/{parts[1]}/{parts[2]}'
    if len(parts) == 3 and parts[0] == 'war' and parts[2] == 'jenkins.war':
        return f'war/{parts[1]}'
    return None


def offers_core(doc: Any) -> bool:
    """With no jenkins-war in the repository, upstream still writes `core`, holding only its defaults
    (name and size): the site then offers plugins only, which is valid (spec §6 step 9)."""
    core = doc.get('core') if isinstance(doc, dict) else None
    return isinstance(core, dict) and bool(core.get('url') or core.get('version'))


def _listed(items: Iterable[str]) -> str:
    ordered = sorted(items)
    more = len(ordered) - MAX_LISTED
    return ', '.join(ordered[:MAX_LISTED]) + (f' (+{more} more)' if more > 0 else '')


def validate_release(
    staging: Path,
    *,
    tiers: Tiers,
    downloads_root: str,
    signing_cert_b64: str,
    previous_plugin_count: int | None,
    min_plugins: int,
    min_ratio: float,
    force: bool,
    download_map: Mapping[str, str] | None,
) -> ValidationResult:
    problems: list[str] = []
    offered: set[str] = set()
    bad_urls: set[str] = set()

    for name in ROOT_FILES:
        if not (staging / name).is_file():
            problems.append(f'missing {name}')

    def check_signature(doc: Any, label: str) -> None:
        signature = doc.get('signature') if isinstance(doc, dict) else None
        certificates = signature.get('certificates') if isinstance(signature, dict) else None
        if not certificates or certificates[0] != signing_cert_b64:
            problems.append(f'{label} is not signed with the configured signing certificate')

    def check_url(url: Any, label: str, collect: bool) -> None:
        key = download_key(url, downloads_root) if isinstance(url, str) else None
        if key is None:
            bad_urls.add(f'{label} {url}')
        elif collect:
            offered.add(key)

    for site in site_dirs(staging, tiers):
        required = CURRENT_FILES if site.name == 'current' else SITE_FILES
        missing = [name for name in required if not (site / name).is_file()]
        if missing:
            problems.append(f'{site.name}/ is missing {", ".join(missing)}')
            continue
        try:
            doc = load_site_json(site / 'update-center.actual.json')
            load_site_json(site / 'update-center.json')
        except ValueError as exc:
            problems.append(f'{site.name}/ update-center JSON does not parse: {exc}')
            continue
        check_signature(doc, f'{site.name}/update-center.actual.json')
        if offers_core(doc):
            check_url(doc['core'].get('url'), f'{site.name} core', collect=True)
            if site.name == 'current' and not (site / 'latestCore.txt').is_file():
                problems.append('current/ is missing latestCore.txt')
        for plugin_id, plugin in (doc.get('plugins') or {}).items():
            url = plugin.get('url') if isinstance(plugin, dict) else None
            check_url(url, f'{site.name} {plugin_id}', collect=True)

    versions_file = staging / 'current' / 'plugin-versions.json'
    if versions_file.is_file():
        try:
            versions = load_site_json(versions_file)
        except ValueError as exc:
            problems.append(f'current/plugin-versions.json does not parse: {exc}')
        else:
            check_signature(versions, 'current/plugin-versions.json')
            for plugin_id, by_version in (versions.get('plugins') or {}).items():
                for version, entry in (by_version or {}).items():
                    url = entry.get('url') if isinstance(entry, dict) else None
                    check_url(url, f'plugin-versions {plugin_id}:{version}', collect=False)

    if bad_urls:
        problems.append(f'download URLs outside {downloads_root}/: {_listed(bad_urls)}')

    htaccess = staging / '.htaccess'
    if htaccess.is_file():
        for line in absolute_targets(htaccess.read_text(encoding='utf-8')):
            problems.append(f'.htaccess redirects to an absolute URL: {line}')

    count = count_plugins(staging / 'current') or 0
    if count < min_plugins:
        problems.append(f'plugin count {count} is below MIN_PLUGINS={min_plugins}')
    if previous_plugin_count and not force and count < min_ratio * previous_plugin_count:
        problems.append(f"plugin count {count} < {min_ratio} × {previous_plugin_count} of the live release (use 'uc generate --force' if the shrinkage is intended)")
    if download_map is not None:
        missing_keys = offered - set(download_map)
        if missing_keys:
            problems.append(f'download map has no entry for {_listed(missing_keys)}')

    if problems:
        raise ValidationError(problems)
    return ValidationResult(count, frozenset(offered))
