"""Staging one file.

Keep it when already staged intact, else download it, check it and record it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

from uc.timeutil import iso, utcnow

from . import ProcureError
from .staging import StagedFile, check_path

if TYPE_CHECKING:
    from .staging import Staging
    from .upstream import Upstream


@dataclass(frozen=True)
class Wanted:
    """A file to stage.

    Its Maven path, its kind (pom, dependency, core) and why, for procurement.json.
    """

    path: str
    kind: str
    reason: str
    replaces_unusable: bool = False


class Stager:
    """Stages files into a Staging from an Upstream.

    Printing one line per file and counting problems.
    """

    def __init__(self, staging: Staging, upstream: Upstream, out: TextIO) -> None:
        """Stage into staging from upstream, reporting to out."""
        self.staging, self.upstream, self.out = staging, upstream, out
        self.staged = self.kept = self.problems = 0

    def say(self, label: str, path: str, detail: str = '') -> None:
        """Print one result line: a label, the path, and a detail in parentheses."""
        print(f'{label:<9} {path}' + (f'  ({detail})' if detail else ''), file=self.out)

    def problem(self, path: str, detail: str) -> None:
        """Report a file that could not be staged."""
        self.problems += 1
        self.say('FAILED', path, detail)

    def stage(self, wanted: Wanted) -> bool:
        """Stage one file; return whether it is in the folder, intact, afterwards."""
        try:
            check_path(wanted.path)
        except ProcureError as exc:
            self.problem(wanted.path, str(exc))
            return False
        if self.staging.intact(wanted.path):
            existing = self.staging.files[wanted.path]
            self.staging.files[wanted.path] = dataclasses.replace(
                existing,
                kind=wanted.kind,
                reason=wanted.reason,
                replaces_unusable=wanted.replaces_unusable,
            )
            self.kept += 1
            self.say('kept', wanted.path, 'already staged')
            return True
        part = self.staging.temporary()
        try:
            sha1, size = self.upstream.fetch(wanted.path, part)
        except ProcureError as exc:
            part.unlink(missing_ok=True)
            self.problem(wanted.path, str(exc))
            return False
        staged = StagedFile(
            path=wanted.path,
            url=self.upstream.url(wanted.path),
            sha1=sha1,
            size=size,
            kind=wanted.kind,
            reason=wanted.reason,
            staged_at=iso(utcnow()),
            replaces_unusable=wanted.replaces_unusable,
        )
        try:
            self.staging.commit(staged, part)
        except ProcureError as exc:
            part.unlink(missing_ok=True)  # commit leaves it when it cannot move it
            self.problem(wanted.path, str(exc))
            return False
        self.staged += 1
        self.say('staged', wanted.path, wanted.reason)
        return True
