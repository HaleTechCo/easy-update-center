"""Upstream resources/ (security warnings, suspensions, labels) plus the operator overlay (spec §6 step 2)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import shutil
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import http
from .config import Settings
from .errors import UcError
from .timeutil import iso, parse_iso

GITHUB_REPO = 'jenkins-infra/update-center2'
REQUIRED_FILES = ('warnings.json', 'artifact-ignores.properties', 'index-template.html')
INFO_FILE = '.uc-resources.json'
_SHA = re.compile(r'^[0-9a-f]{40}$')


class ResourcesError(UcError):
    pass


@dataclass(frozen=True)
class ResourcesInfo:
    source: str
    ref: str
    data_date: str  # when upstream last changed this data (ISO 8601 UTC)
    fetched_at: str

    def age_days(self, now: dt.datetime) -> int:
        return (now - parse_iso(self.data_date)).days


@dataclass(frozen=True)
class PreparedResources:
    path: Path  # merged copy passed to --resources-dir
    upstream: Path  # upstream copy before the overlay; saved as last-good after publish
    info: ResourcesInfo
    overlay: list[str]
    warnings: list[str]


def _resources_prefix(members: list[tarfile.TarInfo]) -> str:
    candidates = [m.name[: -len('warnings.json')] for m in members if m.isfile() and PurePosixPath(m.name).name == 'warnings.json' and PurePosixPath(m.name).parent.name == 'resources']
    if not candidates:
        raise ResourcesError('archive does not contain resources/warnings.json')
    return min(candidates, key=len)  # the top-level resources/, not src/test/resources/


def _extract_resources(archive: bytes, dest: Path) -> dt.datetime:
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode='r:*')
    except tarfile.TarError as exc:
        raise ResourcesError(f'resources archive is not a tar file: {exc}') from exc
    with tar:
        members = tar.getmembers()
        prefix = _resources_prefix(members)
        dest.mkdir(parents=True)
        newest = 0.0
        for member in members:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name[len(prefix) :])
            if relative.is_absolute() or '..' in relative.parts:
                raise ResourcesError(f'unsafe path in resources archive: {member.name}')
            target = dest.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            assert extracted is not None
            target.write_bytes(extracted.read())
            newest = max(newest, float(member.mtime))
    return dt.datetime.fromtimestamp(newest, dt.timezone.utc)


def fetch_upstream(settings: Settings, dest: Path, now: dt.datetime, get: http.Getter = http.get) -> ResourcesInfo:
    if settings.resources_source == 'bundled':
        shutil.copytree(settings.bundled_resources, dest)
        meta = json.loads((dest / INFO_FILE).read_text(encoding='utf-8'))
        info = ResourcesInfo('bundled', meta['ref'], iso(parse_iso(meta['data_date'])), iso(now))
    elif settings.resources_source == 'github':
        commit = json.loads(
            get(
                f'https://api.github.com/repos/{GITHUB_REPO}/commits/{settings.resources_ref}',
                {'Accept': 'application/vnd.github+json'},
            )
        )
        sha = commit.get('sha', '')
        if not _SHA.match(sha):
            raise ResourcesError(f'unexpected GitHub response for {settings.resources_ref!r}')
        _extract_resources(get(f'https://codeload.github.com/{GITHUB_REPO}/tar.gz/{sha}'), dest)
        info = ResourcesInfo('github', sha, iso(parse_iso(commit['commit']['committer']['date'])), iso(now))
    else:
        archive = get(settings.resources_url)
        digest = hashlib.sha256(archive).hexdigest()
        if settings.resources_sha256 and digest != settings.resources_sha256:
            raise ResourcesError(f'RESOURCES_URL checksum mismatch: expected {settings.resources_sha256}, got {digest}')
        newest = _extract_resources(archive, dest)
        info = ResourcesInfo('url', f'sha256:{digest}', iso(newest), iso(now))
    missing = [name for name in REQUIRED_FILES if not (dest / name).is_file()]
    if missing:
        raise ResourcesError(f'resources are missing {", ".join(missing)}')
    (dest / INFO_FILE).write_text(json.dumps(asdict(info)) + '\n', encoding='utf-8')
    return info


def _merge_warnings(base: object, extra: object) -> list:
    if not isinstance(base, list) or not isinstance(extra, list):
        raise ResourcesError('warnings.json must be a JSON array')
    if any(not isinstance(w, dict) or 'id' not in w for w in extra):
        raise ResourcesError("every overlay warning needs an 'id'")
    ids = [w['id'] for w in extra]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ResourcesError(f'duplicate warning id(s) in the overlay: {", ".join(duplicates)}')
    by_id = {w['id']: w for w in extra}
    merged = [by_id.pop(w.get('id'), w) if isinstance(w, dict) else w for w in base]
    merged.extend(by_id.values())
    return merged


def merge_overlay(upstream: Path, overlay: Path, dest: Path) -> list[str]:
    """Copy upstream to dest and apply the overlay: .properties append (later keys win),
    warnings.json merges by id, any other file replaces upstream's."""
    shutil.copytree(upstream, dest)
    applied: list[str] = []
    if not overlay.is_dir():
        return applied
    for item in sorted(overlay.iterdir()):
        if not item.is_file() or item.name.startswith('.') or item.name.lower().startswith('readme'):
            continue
        target = dest / item.name
        if item.suffix == '.properties':
            base = target.read_bytes() if target.exists() else b''
            if base and not base.endswith(b'\n'):
                base += b'\n'
            banner = f'\n# ---- overlay: {item.name} (later keys win) ----\n'.encode('ascii')
            target.write_bytes(base + banner + item.read_bytes())
        elif item.name == 'warnings.json':
            merged = _merge_warnings(
                json.loads(target.read_text(encoding='utf-8')),
                json.loads(item.read_text(encoding='utf-8')),
            )
            target.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        else:
            shutil.copyfile(item, target)
        applied.append(item.name)
    return applied


def prepare_resources(settings: Settings, work: Path, now: dt.datetime, get: http.Getter = http.get) -> PreparedResources:
    upstream = work / 'upstream-resources'
    last_good = settings.data_dir / 'resources' / 'last-good'
    warnings: list[str] = []
    try:
        info = fetch_upstream(settings, upstream, now, get)
    except (ResourcesError, http.HttpError, OSError, ValueError, KeyError) as exc:
        if not (last_good / INFO_FILE).is_file():
            raise ResourcesError(f'could not obtain resources ({exc}) and no last-good copy exists') from exc
        shutil.rmtree(upstream, ignore_errors=True)
        shutil.copytree(last_good, upstream)
        info = ResourcesInfo(**json.loads((last_good / INFO_FILE).read_text(encoding='utf-8')))
        warnings.append(f'resources fetch failed ({exc}); using last-good copy from {info.fetched_at}')
    merged = work / 'resources'
    applied = merge_overlay(upstream, settings.overlay_dir, merged)
    return PreparedResources(merged, upstream, info, applied, warnings)


def save_last_good(prepared: PreparedResources, data_dir: Path) -> None:
    """Called only after a successful publish, so a bad upstream change never becomes the fallback."""
    target = data_dir / 'resources' / 'last-good'
    staging = target.with_name('last-good.tmp')
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(prepared.upstream, staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
