"""uc doctor --push: upload the files procurement.json lists to the repository, never overwriting (docs/how-to/procure-missing-files.md#3-push)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from http import HTTPStatus
from typing import TYPE_CHECKING, TextIO

from uc.config import read_secret
from uc.repo_adapter.backends import LONG_TIMEOUT, TIMEOUT, BackendError, Http

from . import ProcureError
from .staging import MANIFEST, Staging, check_path
from .upstream import SIDE_FILE_LIMIT, parse_sha1

if TYPE_CHECKING:
    from pathlib import Path

    from uc.config import Settings

UPLOADED = (HTTPStatus.OK, HTTPStatus.CREATED)
REASON_LIMIT = 200
# Deleting a release in Artifact Keeper leaves a tombstone with its checksum; other bytes at that path are
# refused with 409 until its storage garbage collection reaps the tombstone (cleanup_soft_deleted_artifact_checked).
TOMBSTONE = 'Artifact Keeper keeps a deleted release as a tombstone until its storage garbage collection runs'


def repository_reason(body: bytes) -> str:
    """Return the reason a repository gives for refusing a request: its JSON error's message, or its plain text; empty for HTML."""
    text = body.decode('utf-8', 'replace').strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        errors = data.get('errors')  # Artifactory: {"errors": [{"status": 409, "message": "..."}]}
        error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else data
        text = str(error.get('message') or '')
    elif text.startswith('<'):
        text = ''
    return ' '.join(''.join(ch if ch.isprintable() else ' ' for ch in text).split())[:REASON_LIMIT]


def _in_layout(path: str) -> bool:
    try:
        check_path(path)
    except ProcureError:
        return False
    return True


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

    def put(self, path: str, data: bytes, sha1: str) -> tuple[int, str]:
        """Upload data to path with its sha1, which Artifactory records and verifies (Artifact Keeper ignores it); return the HTTP status and, when refused, the repository's reason."""
        headers = {**self.headers, 'Content-Type': 'application/octet-stream', 'X-Checksum-Sha1': sha1}
        try:
            response = self.http.request(self.base + path, headers=headers, limit=SIDE_FILE_LIMIT, data=data, method='PUT', timeout=LONG_TIMEOUT, truncate=True)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        return response.status, '' if response.status in UPLOADED else repository_reason(response.body)


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
    uploaded = would_put = present = problems = 0
    artifact_keeper = settings.repo_type == 'artifact-keeper'

    def say(label: str, path: str, detail: str = '') -> None:
        print(f'{label:<9} {path}' + (f'  ({detail})' if detail else ''), file=out)

    root = staging_dir.resolve()
    for path, staged in sorted(staging.files.items()):
        file = staging_dir / path
        try:
            data = file.read_bytes() if file.is_file() and file.resolve().is_relative_to(root) else None
        except OSError as exc:
            problems += 1
            say('TAMPERED', path, f'cannot read it: {exc}')
            continue
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
            hint = ''
            if staged.replaces_unusable:
                hint = '; delete it in the repository first' + (", then run Artifact Keeper's storage garbage collection" if artifact_keeper else '')
            say('DIFFERENT', path, f'the repository holds another file (sha1 {have}){hint}')
            continue
        if held:
            problems += 1
            say('ERROR', path, 'the repository holds this path but publishes no checksum for it; check it by hand')
            continue
        if dry_run:
            would_put += 1
            say('would put', path)
            continue
        try:
            status, reason = target.put(path, data, staged.sha1)
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
            detail = f'HTTP {status}' + (f': {reason}' if reason else '') + f', repository sha1 {after or "none"}'
            if artifact_keeper and status == HTTPStatus.CONFLICT and after is None:
                detail += f'; {TOMBSTONE}'
            say('FAILED', path, detail)
    unlisted = staging.unrecorded()
    for path in unlisted:
        hint = 'record it with uc doctor --stage-local' if _in_layout(path) else 'not a Maven path'
        say('unlisted', path, f'not in procurement.json, so not pushed; {hint}')
    planned = f'{would_put} would put, ' if dry_run else ''
    ignored = f', {len(unlisted)} not in procurement.json' if unlisted else ''
    print(f'{uploaded} uploaded, {planned}{present} already present, {problems} problem(s){ignored} -> {settings.maven_url}', file=out)
    print('run uc doctor --fetch to confirm what the generator will see', file=out)
    return 1 if problems else 0
