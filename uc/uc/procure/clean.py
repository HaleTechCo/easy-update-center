"""uc procure clean: delete the staged files the repository already holds with the same content (docs/how-to/procure-missing-files.md#6-clean-up)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TextIO

from uc.repo_adapter.backends import Http

from . import ProcureError
from .push import Target, push_headers
from .staging import Staging, in_layout, prune_empty, sha1_of

if TYPE_CHECKING:
    from pathlib import Path

    from uc.config import Settings

GONE = 'already gone from the folder'


def clean(settings: Settings, staging_dir: Path, *, dry_run: bool, out: TextIO = sys.stdout, http: Http | None = None) -> int:
    """Delete every staged file the repository holds with the same bytes, listed or placed by hand; return 0 unless one could not be checked or deleted, or differs."""
    if settings.public_repository:
        msg = 'clean compares the staging folder with your own repository, not the public repo.jenkins-ci.org: set REPO_URL and REPO_KEY to it'
        raise ProcureError(msg)
    staging = Staging.open(staging_dir, settings.procurement_source_url)
    target = Target(settings.maven_url, http or Http(settings.repo_ca_file), push_headers(settings))
    removed = would_remove = kept = problems = 0
    forgotten = False

    def say(label: str, path: str, detail: str = '') -> None:
        print(f'{label:<12} {path}' + (f'  ({detail})' if detail else ''), file=out)

    root = staging_dir.resolve()
    by_hand = [path for path in staging.unrecorded() if in_layout(path)]
    for path in sorted([*staging.files, *by_hand]):
        file = staging_dir / path
        recorded = staging.files.get(path)
        here = file.is_file() and file.resolve().is_relative_to(root)
        if here:
            try:
                local = sha1_of(file)
            except OSError as exc:
                problems += 1
                say('ERROR', path, f'cannot read it: {exc}')
                continue
        elif recorded is not None:
            local = recorded.sha1  # the file is gone: once the repository holds what was recorded, the entry is done
        else:
            continue  # a link out of the folder: not a staged file
        try:
            have = target.sha1(path)
            held = have is None and target.exists(path)  # Artifactory publishes no .sha1 for a file uploaded without one
        except ProcureError as exc:
            problems += 1
            say('ERROR', path, str(exc))
            continue
        if held and settings.repo_type == 'artifact-keeper':  # stored without a record: not in the repository for anyone else
            kept += 1
            say('kept', path, 'Artifact Keeper stored it without recording it; push checks it and records it')
        elif held:
            problems += 1
            say('ERROR', path, 'the repository holds this path but publishes no checksum for it; check it by hand')
        elif have is None:
            kept += 1
            say('kept', path, 'not in the repository yet' if here else 'missing from the folder and not in the repository; download it again')
        elif have != local:
            problems += 1
            say('DIFFERENT', path, f'the repository holds another file (sha1 {have})')
        elif dry_run:
            would_remove += 1
            say('would remove', path, '' if here else GONE)
        else:
            try:
                staging.remove(path)
            except OSError as exc:
                problems += 1
                say('ERROR', path, f'cannot delete it: {exc}')
                continue
            prune_empty(file.parent, staging_dir)
            removed += 1
            forgotten = forgotten or recorded is not None
            say('removed', path, '' if here else GONE)
    if forgotten:
        staging.save()
    planned = f'{would_remove} would remove, ' if dry_run else ''
    print(f'{removed} removed, {planned}{kept} kept, {problems} problem(s) -> {settings.maven_url}', file=out)
    return 1 if problems else 0
