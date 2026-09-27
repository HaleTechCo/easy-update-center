"""uc doctor --push: upload the files procurement.json lists to the repository, never overwriting (docs/how-to/procure-missing-files.md#3-push)."""

from __future__ import annotations

import base64
import hashlib
import sys
from http import HTTPStatus
from typing import TYPE_CHECKING, TextIO

from uc.config import read_secret
from uc.repo_adapter.backends import LONG_TIMEOUT, TIMEOUT, BackendError, Http

from . import ProcureError
from .staging import MANIFEST, Staging
from .upstream import SIDE_FILE_LIMIT, parse_sha1

if TYPE_CHECKING:
    from pathlib import Path

    from uc.config import Settings

UPLOADED = (HTTPStatus.OK, HTTPStatus.CREATED)


def push_headers(settings: Settings) -> dict[str, str]:
    """Return the Authorization header for secrets/repo_push_auth (user:token); raise ProcureError when it holds anything else."""
    user, sep, token = (read_secret(settings.repo_push_auth_file) or '').partition(':')
    if not (user and sep and token):
        msg = f'{settings.repo_push_auth_file} must hold user:token for an account that can write to the repository'
        raise ProcureError(msg)
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{token}'.encode()).decode()}


class Target:
    """The repository's Maven layout, read and written by path with the push account."""

    def __init__(self, base_url: str, http: Http, headers: dict[str, str]) -> None:
        """Use base_url (ending in /) through http, sending headers (the push account) with every request."""
        self.base, self.http, self.headers = base_url, http, headers

    def sha1(self, path: str) -> str | None:
        """Return the repository's sha1 of path, None when it has no such file; raise ProcureError on any other answer."""
        try:
            response = self.http.request(self.base + path + '.sha1', headers=self.headers, limit=SIDE_FILE_LIMIT, timeout=TIMEOUT)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'the repository answered HTTP {response.status}'
            raise ProcureError(msg)
        return parse_sha1(response.body)

    def exists(self, path: str) -> bool:
        """Return whether the repository holds path (HEAD answers 200) or not (404); raise ProcureError on any other answer."""
        try:
            response = self.http.request(self.base + path, headers=self.headers, limit=SIDE_FILE_LIMIT, method='HEAD', timeout=TIMEOUT, truncate=True)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        if response.status not in {HTTPStatus.OK, HTTPStatus.NOT_FOUND}:
            msg = f'the repository answered HTTP {response.status}'
            raise ProcureError(msg)
        return response.status == HTTPStatus.OK

    def put(self, path: str, data: bytes, sha1: str) -> int:
        """Upload data to path with its sha1, which Artifactory records and verifies (Artifact Keeper ignores it), and return the HTTP status."""
        headers = {**self.headers, 'Content-Type': 'application/octet-stream', 'X-Checksum-Sha1': sha1}
        try:
            response = self.http.request(self.base + path, headers=headers, limit=SIDE_FILE_LIMIT, data=data, method='PUT', timeout=LONG_TIMEOUT, truncate=True)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        return response.status


def push(settings: Settings, staging_dir: Path, *, dry_run: bool, out: TextIO = sys.stdout, http: Http | None = None) -> int:
    """Upload every file procurement.json lists that the repository lacks; return 0 when each ends up there with the recorded content."""
    if settings.public_repository:
        msg = 'push does not write to the public repo.jenkins-ci.org: set REPO_URL and REPO_KEY to your repository'
        raise ProcureError(msg)
    if not (staging_dir / MANIFEST).is_file():
        msg = f'nothing to push: no {MANIFEST} in {staging_dir}; stage files with uc doctor --download-poms, --download-dependencies or --download-war first'
        raise ProcureError(msg)
    staging = Staging.open(staging_dir, settings.procurement_source_url)
    target = Target(settings.maven_url, http or Http(settings.repo_ca_file), push_headers(settings))
    uploaded = present = problems = 0

    def say(label: str, path: str, detail: str = '') -> None:
        print(f'{label:<9} {path}' + (f'  ({detail})' if detail else ''), file=out)

    root = staging_dir.resolve()
    for path, staged in sorted(staging.files.items()):
        file = staging_dir / path
        data = file.read_bytes() if file.is_file() and file.resolve().is_relative_to(root) else None
        if data is None or hashlib.sha1(data, usedforsecurity=False).hexdigest() != staged.sha1:
            problems += 1
            say('TAMPERED', path, 'changed or missing since it was downloaded; download it again')
            continue
        try:
            have = target.sha1(path)
            held = have is None and target.exists(path)  # Artifactory publishes no .sha1 for a file uploaded without one
        except ProcureError as exc:
            problems += 1
            say('ERROR', path, str(exc))
            continue
        if have == staged.sha1:
            present += 1
            say('present', path)
            continue
        if have is not None:
            problems += 1
            hint = '; delete it in the repository first' if staged.replaces_unusable else ''
            say('DIFFERENT', path, f'the repository holds another file (sha1 {have}){hint}')
            continue
        if held:
            problems += 1
            say('ERROR', path, 'the repository holds this path but publishes no checksum for it; check it by hand')
            continue
        if dry_run:
            say('would put', path)
            continue
        try:
            status = target.put(path, data, staged.sha1)
            after = target.sha1(path)
        except ProcureError as exc:
            problems += 1
            say('FAILED', path, str(exc))
            continue
        if after == staged.sha1 and status in UPLOADED:
            uploaded += 1
            say('uploaded', path)
        elif after == staged.sha1 and status == HTTPStatus.CONFLICT:
            present += 1
            say('present', path, 'the same file arrived while pushing')
        else:
            problems += 1
            say('FAILED', path, f'HTTP {status}, repository sha1 {after or "none"}')
    print(f'{uploaded} uploaded, {present} already present, {problems} problem(s) -> {settings.maven_url}', file=out)
    print('run uc doctor --fetch to confirm what the generator will see', file=out)
    return 1 if problems else 0
