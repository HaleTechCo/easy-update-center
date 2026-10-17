"""Artifactory listing through the same AQL query update-center2 runs.

See ArtifactoryRepositoryImpl.java.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

from . import http
from .errors import UcError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

AQL_TEMPLATE = (
    'items.find({"repo":{"$eq":"%s"},"$or":['
    '{"name":{"$match":"*.hpi"}},{"name":{"$match":"*.jpi"}},'
    '{"name":{"$match":"*.war"}},{"name":{"$match":"*.pom"}}]})'
    '.include("repo", "path", "name", "modified", "created", '
    '"sha256", "actual_sha1", "size")'
)
WAR_GROUP = 'org.jenkins-ci.main'
WAR_ARTIFACT = 'jenkins-war'
LISTING_LIMIT = 256 * 1024 * 1024
"""The largest AQL answer read: about a million files."""
_ALLOWED = re.compile(r'^[0-9A-Za-z+\-./_]*$')
_REPOSITORY = re.compile(r'^[A-Za-z0-9._-]+$')


class AqlError(UcError):
    """The AQL query failed or returned something other than a results list."""


@dataclass(frozen=True)
class RepoFile:
    """One file from the AQL listing.

    Its repository path, name, checksums, size and modification time.
    """

    path: str
    name: str
    sha256: str | None = None
    actual_sha1: str | None = None
    size: int = 0
    modified: str = ''


@dataclass(frozen=True)
class Gav:
    """Maven coordinates of one artifact file.

    groupId, artifactId, version and extension.
    """

    group_id: str
    artifact_id: str
    version: str
    extension: str

    @property
    def maven_path(self) -> str:
        """Return the file's path in Maven repository layout."""
        return (
            f'{self.group_id.replace(".", "/")}/{self.artifact_id}/{self.version}/'
            f'{self.artifact_id}-{self.version}.{self.extension}'
        )

    def with_extension(self, extension: str) -> Gav:
        """Return the same coordinates with another extension.

        The .pom beside an .hpi, say.
        """
        return Gav(self.group_id, self.artifact_id, self.version, extension)


def to_gav(f: RepoFile) -> Gav | None:
    """Port of ArtifactoryRepositoryImpl.toGav.

    None when the file is not in Maven layout.
    """
    if not _ALLOWED.match(f.name) or not _ALLOWED.match(f.path):
        return None
    cut = f.path.rfind('/')
    if cut <= 0:
        return None
    version, ga = f.path[cut + 1 :], f.path[:cut]
    cut = ga.rfind('/')
    if cut <= 0:
        return None
    artifact, group = ga[cut + 1 :], ga[:cut].replace('/', '.')
    dot = f.name.rfind('.')
    if dot <= 0:
        return None
    extension = f.name[dot + 1 :]
    if f.name != f'{artifact}-{version}.{extension}':
        return None
    return Gav(group, artifact, version, extension)


def query_repository(  # noqa: PLR0917
    api_url: str,
    repository: str,
    username: str,
    password: str,
    ca_file: Path | None = None,
    timeout: float = http.LONG_TIMEOUT,
) -> list[RepoFile]:
    """Run the AQL query against api_url with basic auth.

    Return every .hpi, .jpi, .war and .pom in repository. The credentials never follow a
    redirect to another host (uc.http.Http); ca_file is REPO_CA_FILE.
    """
    if not _REPOSITORY.match(repository):
        msg = f'unsupported repository name {repository!r}'
        raise AqlError(msg)
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    try:
        response = http.Http(ca_file).request(
            api_url + 'search/aql',
            method='POST',
            data=(AQL_TEMPLATE % repository).encode(),
            headers={
                'Content-Type': 'text/plain',
                'Accept': 'application/json',
                'Authorization': f'Basic {token}',
            },
            limit=LISTING_LIMIT,
            timeout=timeout,
        )
    except http.HttpError as exc:
        msg = f'AQL query to {api_url} failed: {exc}'
        raise AqlError(msg) from exc
    if response.status != HTTPStatus.OK:
        detail = response.body[:300].decode('utf-8', 'replace')
        msg = f'AQL query to {api_url} failed with HTTP {response.status}: {detail}'
        raise AqlError(msg)
    try:
        payload = json.loads(response.body)
    except ValueError as exc:
        msg = f'AQL query to {api_url} failed: {exc}'
        raise AqlError(msg) from exc
    results = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(results, list):
        msg = 'AQL response has no results list'
        raise AqlError(msg)
    if not all(isinstance(r, dict) for r in results):
        msg = 'AQL response has a result that is not an object'
        raise AqlError(msg)
    return [
        RepoFile(
            r.get('path', ''),
            r.get('name', ''),
            r.get('sha256'),
            r.get('actual_sha1'),
            int(r.get('size') or 0),
            r.get('modified') or '',
        )
        for r in results
    ]


@dataclass(frozen=True)
class Listing:
    """The repository listing sorted into what the generator uses and what it drops."""

    plugins: tuple[Gav, ...]  # .hpi/.jpi with a matching .pom
    plugins_without_pom: tuple[Gav, ...]
    wars: tuple[Gav, ...]  # org.jenkins-ci.main:jenkins-war with a matching .pom
    wars_without_pom: tuple[Gav, ...]
    rejected: tuple[RepoFile, ...]  # not in Maven layout


def classify(files: Iterable[RepoFile]) -> Listing:
    """Split files into plugins and jenkins-war cores.

    Each with or without a matching .pom, plus the files not in Maven layout.
    """
    gavs: list[Gav] = []
    rejected: list[RepoFile] = []
    for f in files:
        gav = to_gav(f)
        if gav is None:
            rejected.append(f)
        else:
            gavs.append(gav)
    poms = {
        (g.group_id, g.artifact_id, g.version) for g in gavs if g.extension == 'pom'
    }

    def has_pom(g: Gav) -> bool:
        return (g.group_id, g.artifact_id, g.version) in poms

    def ordered(items: Iterable[Gav]) -> tuple[Gav, ...]:
        return tuple(
            sorted(
                items, key=lambda g: (g.artifact_id, g.version, g.group_id, g.extension)
            )
        )

    plugins = [g for g in gavs if g.extension in ('hpi', 'jpi')]
    wars = [
        g
        for g in gavs
        if g.extension == 'war'
        and g.group_id == WAR_GROUP
        and g.artifact_id == WAR_ARTIFACT
    ]
    return Listing(
        ordered(g for g in plugins if has_pom(g)),
        ordered(g for g in plugins if not has_pom(g)),
        ordered(g for g in wars if has_pom(g)),
        ordered(g for g in wars if not has_pom(g)),
        tuple(sorted(rejected, key=lambda f: (f.path, f.name))),
    )
