"""uc doctor: what the generator will and won't see in the source repository (spec §10)."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from . import aql, http
from .errors import UcError
from .pom import safe_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .aql import Gav, Listing
    from .config import Settings
    from .model import Credentials
    from .versions import VersionTool

HTTP_NOT_FOUND = 404


class DoctorError(UcError):
    """Fetching a POM or manifest from the repository failed."""


@dataclass
class DoctorReport:
    """What the generator will publish from the source repository, what it drops, and why."""

    plugins: int = 0
    cores: list[str] = field(default_factory=list)
    hpi_without_pom: list[str] = field(default_factory=list)
    war_without_pom: list[str] = field(default_factory=list)
    non_maven: list[str] = field(default_factory=list)
    stub_poms: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    unchecked: int = 0  # newest releases whose POM or manifest was unavailable
    tiers: dict | None = None
    # From the repo-adapter's report.json (repo-adapter spec §7); None when the adapter is off.
    repo_adapter: dict | None = None

    def _adapter(self, name: str) -> list:
        return (self.repo_adapter or {}).get(name) or []

    @property
    def dropped(self) -> int:
        """Number of files in the source that the generator will not publish."""
        # needs_pom archives already count as hpi_without_pom: no POM reaches the listing for them.
        return len(self.hpi_without_pom) + len(self.war_without_pom) + len(self.non_maven) + len(self._adapter('misplaced')) + len(self._adapter('skipped'))

    def exit_code(self) -> int:
        """Return 1 when anything is dropped or a dependency is missing, else 0."""
        return 1 if self.dropped or self.missing_dependencies else 0

    def summary(self) -> dict:
        """For the public status.json: counts only, never paths or URLs."""
        return {
            'plugins': self.plugins,
            'cores': self.cores,
            'hpi_without_pom': len(self.hpi_without_pom),
            'war_without_pom': len(self.war_without_pom),
            'non_maven': len(self.non_maven),
            'stub_poms': len(self.stub_poms),
            'missing_dependencies': len(self.missing_dependencies),
            'unchecked': self.unchecked,
            'repo_adapter': ({name: len(self._adapter(name)) for name in ADAPTER_SECTIONS} if self.repo_adapter is not None else None),
        }

    def to_dict(self) -> dict:
        """Return the full report as a JSON-serializable dict (uc doctor --json)."""
        return asdict(self)


ADAPTER_SECTIONS = ('derived_poms', 'replaced_poms', 'needs_pom', 'misplaced', 'skipped', 'dates_from_repository')


def attach_adapter_report(report: DoctorReport, settings: Settings) -> DoctorReport:
    """Add the repo-adapter's report of what it filled in and what it dropped, when the adapter is on."""
    if settings.repo_adapter:
        from .repo_adapter.listing import read_report  # noqa: PLC0415 - circular: repo_adapter.archive imports parse_manifest from this module

        data = read_report(settings.repo_adapter_report_file) or {}
        report.repo_adapter = {name: data.get(name) or [] for name in ADAPTER_SECTIONS}
    return report


class Source(Protocol):
    """Where analyze reads a plugin release's POM and manifest from."""

    def pom(self, gav: Gav) -> bytes | None:
        """Return the release's POM, or None when it is unavailable."""

    def manifest(self, gav: Gav) -> str | None:
        """Return the text of the archive's META-INF/MANIFEST.MF, or None when it is unavailable."""


class ArtifactSource:
    """Reads POMs and MANIFEST.MF the way update-center2 does, from its cache first."""

    def __init__(self, artifactory_url: str, repository: str, username: str, password: str, *, cache_dir: Path | None, allow_network: bool, timeout: float = 60) -> None:
        """Read from repository at artifactory_url; with allow_network False, only what cache_dir already holds."""
        self.base = f'{artifactory_url}{repository}/'
        self.cache_dir = cache_dir
        self.allow_network = allow_network
        self.timeout = timeout
        self._auth = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()

    @classmethod
    def from_settings(cls, settings: Settings, creds: Credentials, *, allow_network: bool) -> ArtifactSource:
        """Build a source reading the repository and cache the generator uses."""
        # Same URL the jar uses, so cache keys (sha256 of the URL path) match.
        return cls(settings.source_url, settings.repo_key, creds.repo_username, creds.repo_password, cache_dir=settings.cache_dir, allow_network=allow_network)

    def pom(self, gav: Gav) -> bytes | None:
        """Return the .pom beside gav, or None when it is neither cached nor fetchable."""
        return self._get(self.base + gav.with_extension('pom').maven_path)

    def manifest(self, gav: Gav) -> str | None:
        """Return the archive's META-INF/MANIFEST.MF as text, or None when it is neither cached nor fetchable."""
        data = self._get(self.base + gav.maven_path + '!/META-INF/MANIFEST.MF')
        return data.decode('utf-8', 'replace') if data is not None else None

    def cache_file(self, url: str) -> Path | None:
        """Return where update-center2's cache keeps url (keyed by the sha256 of its path), or None with no cache."""
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(urlsplit(url).path.encode('utf-8')).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _get(self, url: str) -> bytes | None:
        cached = self.cache_file(url)
        if cached is not None:
            if cached.is_file():
                return cached.read_bytes()
            if cached.is_dir():  # update-center2 records failed downloads as directories
                return None
        if not self.allow_network:
            return None
        request = urllib.request.Request(url, headers={'Authorization': self._auth, 'User-Agent': http.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == HTTP_NOT_FOUND:
                return None
            msg = f'GET {url} failed with HTTP {exc.code}'
            raise DoctorError(msg) from exc
        except OSError as exc:
            msg = f'GET {url} failed: {exc}'
            raise DoctorError(msg) from exc


def parse_manifest(text: str) -> dict[str, str]:
    """Return the main section of a JAR manifest, joining 72-column continuation lines."""
    attributes: dict[str, str] = {}
    key: str | None = None
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if not raw:
            if attributes:
                break
            continue
        if raw.startswith(' ') and key:
            attributes[key] += raw[1:]
            continue
        name, sep, value = raw.partition(': ')
        key = name if sep else None
        if sep:
            attributes[name] = value
    return attributes


def parse_dependencies(value: str) -> list[tuple[str, str, bool]]:
    """Parse a Plugin-Dependencies value into (name, version, optional) tuples."""
    dependencies = []
    for item in filter(None, (part.strip() for part in value.split(','))):
        spec, _, params = item.partition(';')
        name, _, version = spec.partition(':')
        dependencies.append((name, version, 'resolution:=optional' in params))
    return dependencies


def is_stub_pom(data: bytes) -> bool:
    """Return True when the POM does not parse or has no non-empty top-level <name>."""
    root = safe_root(data)
    if root is None:
        return True
    return all(not (child.tag.rsplit('}', 1)[-1] == 'name' and (child.text or '').strip()) for child in root)


def analyze(listing: Listing, versions: VersionTool, source: Source) -> DoctorReport:
    """Report what the listing drops, and check each plugin's newest release for a stub POM and unmet required dependencies."""
    report = DoctorReport()
    report.non_maven = sorted(f'{f.path}/{f.name}' for f in listing.rejected)
    report.hpi_without_pom = sorted(g.maven_path for g in listing.plugins_without_pom)
    report.war_without_pom = sorted(g.maven_path for g in listing.wars_without_pom)
    report.cores = versions.sort({g.version for g in listing.wars})
    newest = versions.max_by_key((g.artifact_id, g.version) for g in listing.plugins)
    report.plugins = len(newest)
    latest: dict[str, Gav] = {}
    for gav in listing.plugins:
        if newest.get(gav.artifact_id) == gav.version and (gav.artifact_id not in latest or gav.extension == 'hpi'):
            latest[gav.artifact_id] = gav

    checks: list[tuple[str, str, str, str, str]] = []  # plugin, version, dependency, required, available
    for artifact_id, gav in sorted(latest.items()):
        pom, manifest = source.pom(gav), source.manifest(gav)
        if pom is None or manifest is None:
            report.unchecked += 1
        if pom is not None and is_stub_pom(pom):
            report.stub_poms.append(f'{artifact_id}:{gav.version}')
        if manifest is None:
            continue
        for name, required, optional in parse_dependencies(parse_manifest(manifest).get('Plugin-Dependencies', '')):
            if optional:
                continue
            if name not in latest:
                report.missing_dependencies.append(f'{artifact_id}:{gav.version} requires {name} {required} (not in the repository)')
            else:
                checks.append((artifact_id, gav.version, name, required, latest[name].version))
    results = versions.compare_many([(available, required) for *_, required, available in checks])
    for (artifact_id, version, name, required, available), cmp in zip(checks, results, strict=False):
        if cmp < 0:
            report.missing_dependencies.append(f'{artifact_id}:{version} requires {name} >= {required} (newest available: {available})')
    return report


def run_doctor(
    settings: Settings,
    creds: Credentials,
    *,
    fetch: bool,
    versions: VersionTool,
    query: Callable[..., list[aql.RepoFile]] = aql.query_repository,
) -> DoctorReport:
    """List the source repository and analyze it, adding the repo-adapter's report and the live release's tiers when present."""
    files = query(settings.source_api_url, settings.repo_key, creds.repo_username, creds.repo_password)
    report = analyze(aql.classify(files), versions, ArtifactSource.from_settings(settings, creds, allow_network=fetch))
    attach_adapter_report(report, settings)
    tiers_file = settings.data_dir / 'live' / 'tiers.json'
    if tiers_file.is_file():
        with contextlib.suppress(ValueError):
            report.tiers = json.loads(tiers_file.read_text(encoding='utf-8'))
    return report


def format_report(report: DoctorReport, settings: Settings) -> str:
    """Render the report as the human-readable text uc doctor prints."""
    lines = [
        f'Source: {settings.repo_type} repository {settings.repo_key} at {settings.repo_url}' + (' (through repo-adapter)' if settings.repo_adapter else ''),
        f'Plugins with a POM: {report.plugins}',
        'Jenkins cores with a POM: ' + (', '.join(report.cores) or 'none (the site will offer no core and no tiers)'),
    ]
    if report.tiers is not None:
        lines.append(f'Tiers in the live release: weekly {report.tiers.get("weeklyCores", [])}, stable {report.tiers.get("stableCores", [])}')

    def section(title: str, items: list[str], indent: str = '') -> None:
        if items:
            lines.append(f'{indent}{title} ({len(items)}):')
            lines.extend(f'{indent}  {item}' for item in items)

    lines.append('')
    adapter = report.repo_adapter
    if report.dropped:
        lines.append(f'Dropped by the generator: {report.dropped}')
        if adapter is not None:
            section('no POM in the repository or the archive: procure the real .pom', [f'{item["gav"]}  {item["url"]}' for item in adapter['needs_pom']], '  ')
            section('misplaced: move to the path its manifest names', [f'{item["path"]}  ->  {item["correct_path"]}' for item in adapter['misplaced']], '  ')
            section('skipped', [f'{item["path"]}: {item["reason"]}' for item in adapter['skipped']], '  ')
        else:
            section('.hpi/.jpi without a .pom', report.hpi_without_pom, '  ')
        section('jenkins-war without a .pom', report.war_without_pom, '  ')
        section('not in Maven layout', report.non_maven, '  ')
    else:
        lines.append('Nothing in the source is dropped by the generator.')
    if adapter is not None:
        section('POMs filled in from archives', adapter['derived_poms'])
        section('.pom files ignored (not usable)', [f'{item["path"]}: {item["reason"]}' for item in adapter['replaced_poms']])
        section('dates from the repository (no usable archive date)', [f'{item["path"]}: {item["reason"]}' for item in adapter['dates_from_repository']])
    section('Stub POMs (degraded names, descriptions, SCM links)', report.stub_poms)
    section('Dependency problems', report.missing_dependencies)
    if report.unchecked:
        lines.append(f'Not checked (POM or manifest unavailable; run with --fetch): {report.unchecked}')
    return '\n'.join(lines)
