"""uc doctor: what the generator will and won't see in the source repository.

See docs/reference/cli.md#uc-doctor.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from . import aql, http, status
from .config import PUBLIC_RELEASES_URL
from .errors import UcError, UsageError
from .pom import safe_root
from .settings import CORE_VERSION
from .timeutil import iso

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from .aql import Gav, Listing
    from .config import Settings
    from .model import Credentials
    from .versions import VersionTool

REPORT_FILE = Path('doctor') / 'report.json'
"""Where uc doctor --record saves the full report, under the data directory.

Web does not serve it.
"""


class DoctorError(UcError):
    """Fetching a POM or manifest from the repository failed."""


@dataclass
class DoctorReport:
    """What the generator will publish from the source repository.

    Also what it drops, and why.
    """

    plugins: int = 0
    cores: list[str] = field(default_factory=list)
    hpi_without_pom: list[str] = field(default_factory=list)
    war_without_pom: list[str] = field(default_factory=list)
    non_maven: list[str] = field(default_factory=list)
    stub_poms: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    unchecked: int = 0  # newest releases whose POM or manifest was unavailable
    tiers: dict | None = None
    # From the repo-adapter's report.json (docs/architecture/repo-adapter.md#report);
    # None when the adapter is off.
    repo_adapter: dict | None = None
    # Each required dependency that is absent or too old, as data
    # (uc procure stage --dependencies).
    missing_dependency_details: list[dict] = field(default_factory=list)
    # Core versions plugin releases require, ascending, with the releases requiring
    # each; None when not checked (the public repository). See
    # docs/architecture/modes.md#core-age.
    required_cores: dict[str, list[str]] | None = None
    core_floor: str | None = None
    missing_cores: list[dict] = field(default_factory=list)
    missing_cores_below_floor: int = 0
    # the oldest of those: the --since that stages them all
    oldest_core_below_floor: str | None = None
    # plugin releases whose manifest was unavailable for the core check
    cores_unchecked: int = 0

    def _adapter(self, name: str) -> list:
        return (self.repo_adapter or {}).get(name) or []

    @property
    def dropped(self) -> int:
        """Number of files in the source that the generator will not publish."""
        # needs_pom archives already count as hpi_without_pom: no POM reaches the
        # listing for them.
        return (
            len(self.hpi_without_pom)
            + len(self.war_without_pom)
            + len(self.non_maven)
            + len(self._adapter('misplaced'))
            + len(self._adapter('skipped'))
        )

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
            'missing_cores': len(self.missing_cores)
            if self.required_cores is not None
            else None,
            'cores_unchecked': self.cores_unchecked
            if self.required_cores is not None
            else None,
            'repo_adapter': (
                {name: len(self._adapter(name)) for name in _report_lists()}
                if self.repo_adapter is not None
                else None
            ),
        }

    def to_dict(self) -> dict:
        """Return the full report as a JSON-serializable dict (uc doctor --json)."""
        return asdict(self)


def _report_lists() -> tuple[str, ...]:
    # circular: repo_adapter.archive imports parse_manifest from this module
    from .repo_adapter.listing import REPORT_LISTS  # noqa: PLC0415

    return REPORT_LISTS


# the keys of each section that uc doctor and uc procure read, as strings
_SECTION_KEYS = {
    'derived_poms': ('gav', 'url'),
    'replaced_poms': ('path', 'reason', 'url'),
    'needs_pom': ('gav', 'url'),
    'misplaced': ('path', 'correct_path'),
    'skipped': ('path', 'reason'),
    'dates_from_repository': ('path', 'reason'),
}


GAV_PARTS = 3  # group:artifact:version


def _well_formed(name: str, section: object) -> bool:
    # procure unpacks a gav into its three parts
    return isinstance(section, list) and all(
        isinstance(item, dict)
        and all(isinstance(item.get(key), str) for key in _SECTION_KEYS[name])
        and ('gav' not in item or len(item['gav'].split(':')) == GAV_PARTS)
        for item in section
    )


def attach_adapter_report(report: DoctorReport, settings: Settings) -> DoctorReport:
    """Add the repo-adapter's report of what it filled in and what it dropped.

    Only when the adapter is on.
    """
    if settings.repo_adapter:
        from .repo_adapter.listing import read_report  # noqa: PLC0415

        data = read_report(settings.repo_adapter_report_file) or {}
        # the volume is the adapter's to write: a malformed section counts as missing
        report.repo_adapter = {
            name: data[name] if _well_formed(name, data.get(name)) else []
            for name in _report_lists()
        }
    return report


class Source(Protocol):
    """Where analyze reads a plugin release's POM and manifest from."""

    def pom(self, gav: Gav) -> bytes | None:
        """Return the release's POM, or None when it is unavailable."""

    def manifest(self, gav: Gav) -> str | None:
        """Return the text of the archive's META-INF/MANIFEST.MF.

        None when it is unavailable.
        """


class ArtifactSource:
    """Reads POMs and MANIFEST.MF the way update-center2 does, from its cache first."""

    def __init__(
        self,
        artifactory_url: str,
        repository: str,
        username: str,
        password: str,
        *,
        cache_dir: Path | None,
        allow_network: bool,
        ca_file: Path | None = None,
        timeout: float = 60,
    ) -> None:
        """Read from repository at artifactory_url.

        With allow_network False, only what cache_dir already holds.
        """
        self.base = f'{artifactory_url}{repository}/'
        self.cache_dir = cache_dir
        self.allow_network = allow_network
        self.ca_file = ca_file
        self.timeout = timeout
        self._http: http.Http | None = None
        self._auth = (
            'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, creds: Credentials, *, allow_network: bool
    ) -> ArtifactSource:
        """Build a source reading the repository and cache the generator uses."""
        # Same URL the jar uses, so cache keys (sha256 of the URL path) match.
        return cls(
            settings.source_url,
            settings.repo_key,
            creds.repo_username,
            creds.repo_password,
            cache_dir=settings.cache_dir,
            allow_network=allow_network,
            ca_file=settings.source_ca_file,
        )

    def pom(self, gav: Gav) -> bytes | None:
        """Return the .pom beside gav.

        None when it is neither cached nor fetchable.
        """
        return self._get(self.base + gav.with_extension('pom').maven_path)

    def manifest(self, gav: Gav) -> str | None:
        """Return the archive's META-INF/MANIFEST.MF as text.

        None when it is neither cached nor fetchable.
        """
        data = self._get(self.base + gav.maven_path + '!/META-INF/MANIFEST.MF')
        return data.decode('utf-8', 'replace') if data is not None else None

    def cache_file(self, url: str) -> Path | None:
        """Return where update-center2's cache keeps url.

        Keyed by the sha256 of its path; None with no cache.
        """
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(urlsplit(url).path.encode('utf-8')).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _get(self, url: str) -> bytes | None:
        cached = self.cache_file(url)
        if cached is not None:
            if cached.is_file():
                return cached.read_bytes()
            # update-center2 records failed downloads as directories
            if cached.is_dir():
                return None
        if not self.allow_network:
            return None
        # built on first use, so a source that only reads the cache never reads
        # REPO_CA_FILE
        if self._http is None:
            self._http = http.Http(self.ca_file)
        try:
            response = self._http.request(
                url,
                headers={'Authorization': self._auth},
                limit=http.SMALL_FILE_LIMIT,
                timeout=self.timeout,
            )
        except http.HttpError as exc:
            raise DoctorError(str(exc)) from exc
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'GET {url} failed with HTTP {response.status}'
            raise DoctorError(msg)
        return response.body


def parse_manifest(text: str) -> dict[str, str]:
    """Return the main section of a JAR manifest.

    Joins 72-column continuation lines.
    """
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
    return all(
        not (child.tag.rsplit('}', 1)[-1] == 'name' and (child.text or '').strip())
        for child in root
    )


def analyze(listing: Listing, versions: VersionTool, source: Source) -> DoctorReport:
    """Report what the listing drops, and check each plugin's newest release.

    The check is for a stub POM and unmet required dependencies.
    """
    report = DoctorReport()
    report.non_maven = sorted(f'{f.path}/{f.name}' for f in listing.rejected)
    report.hpi_without_pom = sorted(g.maven_path for g in listing.plugins_without_pom)
    report.war_without_pom = sorted(g.maven_path for g in listing.wars_without_pom)
    report.cores = versions.sort({g.version for g in listing.wars})
    newest = versions.max_by_key((g.artifact_id, g.version) for g in listing.plugins)
    report.plugins = len(newest)
    latest: dict[str, Gav] = {}
    for gav in listing.plugins:
        if newest.get(gav.artifact_id) == gav.version and (
            gav.artifact_id not in latest or gav.extension == 'hpi'
        ):
            latest[gav.artifact_id] = gav

    # plugin release, dependency, required, available
    checks: list[tuple[Gav, str, str, str]] = []
    for artifact_id, gav in sorted(latest.items()):
        pom, manifest = source.pom(gav), source.manifest(gav)
        if pom is None or manifest is None:
            report.unchecked += 1
        if pom is not None and is_stub_pom(pom):
            report.stub_poms.append(f'{artifact_id}:{gav.version}')
        if manifest is None:
            continue
        for name, required, optional in parse_dependencies(
            parse_manifest(manifest).get('Plugin-Dependencies', '')
        ):
            if optional:
                continue
            if name not in latest:
                report.missing_dependencies.append(
                    f'{artifact_id}:{gav.version} requires {name} {required} '
                    '(not in the repository)'
                )
                report.missing_dependency_details.append(
                    dependency_detail(gav, name, required)
                )
            else:
                checks.append((gav, name, required, latest[name].version))
    results = versions.compare_many(
        [(available, required) for _, _, required, available in checks]
    )
    for (gav, name, required, available), cmp in zip(checks, results, strict=False):
        if cmp < 0:
            report.missing_dependencies.append(
                f'{gav.artifact_id}:{gav.version} requires {name} >= {required} '
                f'(newest available: {available})'
            )
            report.missing_dependency_details.append(
                dependency_detail(gav, name, required)
            )
    return report


def dependency_detail(gav: Gav, dependency: str, required: str) -> dict[str, str]:
    """Return one missing or too-old required dependency as data.

    Names the plugin release that requires it.
    """
    return {
        'plugin': gav.artifact_id,
        'plugin_group': gav.group_id,
        'plugin_version': gav.version,
        'dependency': dependency,
        'required': required,
    }


def required_jenkins_version(attributes: Mapping[str, str]) -> str | None:
    """Return the core a plugin release requires.

    Jenkins-Version, or Hudson-Version for old builds; None unless a plain version.
    """
    value = (
        attributes.get('Jenkins-Version') or attributes.get('Hudson-Version') or ''
    ).strip()
    return value if CORE_VERSION.match(value) else None


def required_cores(
    listing: Listing, source: Source
) -> tuple[dict[str, list[str]], int]:
    """Map each core the repository's plugin releases require to those releases.

    Also count releases whose manifest is unavailable. Every release counts, as
    update-center2's TieredUpdateSitesGenerator counts them when it picks tiers.
    """
    required: dict[str, list[str]] = {}
    unchecked = 0
    seen: set[tuple[str, str, str]] = set()
    for gav in listing.plugins:
        release = (gav.group_id, gav.artifact_id, gav.version)
        if release in seen:
            continue
        seen.add(release)
        manifest = source.manifest(gav)
        if manifest is None:
            unchecked += 1
            continue
        version = required_jenkins_version(parse_manifest(manifest))
        if version is not None:
            required.setdefault(version, []).append(':'.join(release))
    return required, unchecked


def check_since(since: str | None) -> None:
    """Refuse a --since that is not a Jenkins version."""
    if since is not None and not CORE_VERSION.match(since):
        msg = f'--since takes a Jenkins version such as 2.462.1 (got {since!r})'
        raise UsageError(msg)


def find_missing_cores(
    required: Mapping[str, list[str]],
    stocked: Sequence[str],
    since: str | None,
    versions: VersionTool,
) -> tuple[str | None, list[dict], int, str | None]:
    """Return the floor and the required cores the repository lacks at or above it.

    The floor is since, else the oldest stocked core. Also return how many it lacks
    below it and the oldest of those.
    """
    check_since(since)
    floor = since or (stocked[0] if stocked else None)
    if floor is None:
        return None, [], 0, None
    have = set(stocked)
    lacking = versions.sort(version for version in required if version not in have)
    order = versions.compare_many([(version, floor) for version in lacking])
    missing = [
        {
            'version': version,
            'url': PUBLIC_RELEASES_URL
            + aql.Gav(aql.WAR_GROUP, aql.WAR_ARTIFACT, version, 'war').maven_path,
            'required_by': len(required[version]),
            'example': min(required[version]),
        }
        for version, cmp in zip(lacking, order, strict=True)
        if cmp >= 0
    ]
    below = sum(1 for cmp in order if cmp < 0)
    # lacking is ascending: those below the floor come first
    return floor, missing, below, lacking[0] if below else None


def attach_core_check(
    report: DoctorReport,
    listing: Listing,
    source: Source,
    versions: VersionTool,
    *,
    since: str | None,
) -> DoctorReport:
    """Add the cores plugin releases require to the report.

    Also add those the repository lacks at or above the floor.
    """
    required, report.cores_unchecked = required_cores(listing, source)
    report.required_cores = {
        version: required[version] for version in versions.sort(required)
    }
    (
        report.core_floor,
        report.missing_cores,
        report.missing_cores_below_floor,
        report.oldest_core_below_floor,
    ) = find_missing_cores(required, report.cores, since, versions)
    return report


@dataclass(frozen=True)
class Inspection:
    """One doctor run: the report, and the listing and source it was built from.

    uc procure stage uses it.
    """

    report: DoctorReport
    listing: Listing
    source: Source
    fetch: bool = False
    # whether the run could download what the cache lacked: the report then names the
    # repository, not --fetch, as why a POM or manifest was unavailable


def inspect_repository(
    settings: Settings,
    creds: Credentials,
    *,
    fetch: bool,
    versions: VersionTool,
    since: str | None = None,
    query: Callable[..., list[aql.RepoFile]] = aql.query_repository,
) -> Inspection:
    """List the source repository and analyze it.

    Adds the repo-adapter's report, the core check (not for the public repository) and
    the live release's tiers.
    """
    check_since(since)
    files = query(
        settings.source_api_url,
        settings.repo_key,
        creds.repo_username,
        creds.repo_password,
        settings.source_ca_file,
    )
    listing = aql.classify(files)
    source = ArtifactSource.from_settings(settings, creds, allow_network=fetch)
    report = analyze(listing, versions, source)
    attach_adapter_report(report, settings)
    if not settings.public_repository:
        attach_core_check(report, listing, source, versions, since=since)
    tiers_file = settings.data_dir / 'live' / 'tiers.json'
    if tiers_file.is_file():
        with contextlib.suppress(ValueError):
            tiers = json.loads(tiers_file.read_text(encoding='utf-8'))
            if isinstance(tiers, dict):
                report.tiers = tiers
    return Inspection(report, listing, source, fetch)


RECORD_LOCK_FILE = REPORT_FILE.with_name('.record.lock')
"""Held while a recorded report is saved.

Not the status lock, which `record` takes inside it through status.update_status.
"""


@contextlib.contextmanager
def record_locked(data_dir: Path) -> Iterator[None]:
    """Hold data_dir/doctor/.record.lock.

    Waits while another uc doctor --record holds it.
    """
    lock = data_dir / RECORD_LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def record(report: DoctorReport, data_dir: Path, now: dt.datetime) -> Path:
    """Save report as the latest, and return the report's path.

    It is saved in full as data_dir/doctor/report.json, its counts in status.json's
    doctor block.

    The block's recorded_at says when. Generations keep it and the block's
    missing_cores and cores_unchecked, which only a recorded report works out
    (docs/reference/status-json.md).
    Overlapping runs take turns, so report.json always matches the last doctor block.
    """
    path = data_dir / REPORT_FILE
    with record_locked(data_dir):
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.report.', suffix='.tmp')
        tmp = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(report.to_dict(), indent=2) + '\n')
            tmp.chmod(0o644)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        status.update_status(
            data_dir,
            doctor={
                **report.summary(),
                'checked_at': iso(now),
                'recorded_at': iso(now),
            },
        )
    return path


def unavailable(*, fetch: bool) -> str:
    """Return how the report says a POM or manifest was unavailable."""
    return (
        'unavailable from the repository' if fetch else 'unavailable; run with --fetch'
    )


def format_report(
    report: DoctorReport, settings: Settings, *, fetch: bool = False
) -> str:
    """Render the report as the human-readable text uc doctor prints.

    fetch says whether the run could download POMs and manifests: when it could, a
    missing one is the repository's doing, and --fetch is no advice.
    """
    lines = [
        (
            f'Source: {settings.repo_type} repository {settings.repo_key} '
            f'at {settings.repo_url}'
        )
        + (' (through repo-adapter)' if settings.repo_adapter else ''),
        f'Plugins with a POM: {report.plugins}',
        'Jenkins cores with a POM: '
        + (
            ', '.join(report.cores) or 'none (the site will offer no core and no tiers)'
        ),
    ]
    if report.tiers is not None:
        lines.append(
            f'Tiers in the live release: weekly {report.tiers.get("weeklyCores", [])}, '
            f'stable {report.tiers.get("stableCores", [])}'
        )

    def section(title: str, items: list[str], indent: str = '') -> None:
        if items:
            lines.append(f'{indent}{title} ({len(items)}):')
            lines.extend(f'{indent}  {item}' for item in items)

    lines.append('')
    adapter = report.repo_adapter
    if report.dropped:
        lines.append(f'Dropped by the generator: {report.dropped}')
        if adapter is not None:
            section(
                'no POM in the repository or the archive: procure the real .pom',
                [f'{item["gav"]}  {item["url"]}' for item in adapter['needs_pom']],
                '  ',
            )
            section(
                'misplaced: move to the path its manifest names',
                [
                    f'{item["path"]}  ->  {item["correct_path"]}'
                    for item in adapter['misplaced']
                ],
                '  ',
            )
            section(
                'skipped',
                [f'{item["path"]}: {item["reason"]}' for item in adapter['skipped']],
                '  ',
            )
        else:
            section('.hpi/.jpi without a .pom', report.hpi_without_pom, '  ')
        section('jenkins-war without a .pom', report.war_without_pom, '  ')
        section('not in Maven layout', report.non_maven, '  ')
    else:
        lines.append('Nothing in the source is dropped by the generator.')
    if adapter is not None:
        section(
            'POMs filled in from archives; the real .pom is optional',
            [f'{item["gav"]}  {item["url"]}' for item in adapter['derived_poms']],
        )
        section(
            '.pom files ignored (not usable); replace with the real .pom',
            [
                f'{item["path"]}: {item["reason"]}  {item["url"]}'
                for item in adapter['replaced_poms']
            ],
        )
        section(
            'dates from the repository (no usable archive date)',
            [
                f'{item["path"]}: {item["reason"]}'
                for item in adapter['dates_from_repository']
            ],
        )
    section('Stub POMs (degraded names, descriptions, SCM links)', report.stub_poms)
    section('Dependency problems', report.missing_dependencies)
    if report.required_cores is not None:
        lines.extend(core_lines(report, fetch=fetch))
    if report.unchecked:
        lines.append(
            f'Not checked (POM or manifest {unavailable(fetch=fetch)}): '
            f'{report.unchecked}'
        )
    return '\n'.join(lines)


def core_lines(report: DoctorReport, *, fetch: bool = False) -> list[str]:
    """Return the report's lines on required cores the repository lacks."""
    required = list(report.required_cores or {})
    lines: list[str] = []
    if report.core_floor is None:
        if required:
            lines.append(
                f'No Jenkins core in the repository: plugins require {len(required)} '
                f'cores, from {required[0]} to {required[-1]}. Stage the ones your '
                'controllers need with: uc procure stage --war required --since '
                '<oldest version your controllers run>'
            )
    else:
        if report.missing_cores:
            lines.append(
                'Cores plugins require that the repository lacks, at or above '
                f'{report.core_floor} ({len(report.missing_cores)}):'
            )
            lines.extend(
                (
                    f'  {core["version"]}  {core["url"]}  (required by '
                    f'{core["required_by"]} '
                    f'release{"" if core["required_by"] == 1 else "s"}, '
                    f'e.g. {core["example"]})'
                )
                for core in report.missing_cores
            )
        below, oldest = report.missing_cores_below_floor, report.oldest_core_below_floor
        if below:
            if below == 1:
                lines.append(
                    f'1 older required core below {report.core_floor} '
                    f'not listed: {oldest}'
                )
                lines.append(
                    f'  To stage it: uc procure stage --war required --since {oldest}'
                )
            else:
                lines.append(
                    f'{below} older required cores below {report.core_floor} '
                    f'not listed, the oldest {oldest}'
                )
                lines.append(
                    '  To stage them: uc procure stage --war required --since '
                    f'{oldest} (a later version stages only those from it up)'
                )
            # the floor is the repository's oldest core, which an older one replaces
            if report.cores and report.core_floor == report.cores[0]:
                lines.append(
                    f'  Warning: once pushed, {oldest} is the oldest core in the '
                    'repository, and so the floor: every later uc procure stage --war '
                    'required also stages the missing cores plugins require from '
                    f'{oldest} up, for plugins you add later too'
                )
    if report.cores_unchecked:
        lines.append(
            f'Required cores not checked (manifest {unavailable(fetch=fetch)}): '
            f'{report.cores_unchecked}'
        )
    return lines
