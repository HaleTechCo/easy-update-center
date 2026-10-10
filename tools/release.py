"""Release helper for the Release workflow (docs/development/releasing.md).

    python3 tools/release.py prepare VERSION
        Move CHANGELOG.md's Unreleased entries under "## [VERSION] - <UTC date>", below an empty
        "## Unreleased". Everything else stays byte for byte.
    python3 tools/release.py check VERSION [--publish-only]
        Refuse a version the Release workflow must not publish, then print latest=true|false and
        minor_latest=true|false for $GITHUB_OUTPUT: whether VERSION is the highest release, and the
        highest in its X.Y, so the workflow moves the latest and X.Y image tags only then.
    python3 tools/release.py notes VERSION
        Print that version's CHANGELOG.md section without its heading: the GitHub Release's notes.
    python3 tools/release.py latest
        Print the highest released version (from the vX.Y.Z tags), or nothing.

Run from the repository root. A refusal prints `release.py: error: ...` and exits 1. Standard library
only, and nothing newer than Python 3.12: the workflow runs it on the runner's python3.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

CHANGELOG = Path('CHANGELOG.md')
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
TAG = re.compile(r'v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))')
UNRELEASED = re.compile(r'^## Unreleased[ \t]*\n', re.MULTILINE)
NEXT_SECTION = re.compile(r'^## ', re.MULTILINE)


class ReleaseError(Exception):
    """A refusal; the message says what to fix."""


def parse_version(text: str) -> tuple[int, int, int]:
    """Return X.Y.Z as three integers; refuse anything else (a v prefix, a pre-release, a leading zero)."""
    match = VERSION.fullmatch(text)
    if match is None:
        msg = f'{text!r} is not a version: give X.Y.Z, such as 0.1.0'
        raise ReleaseError(msg)
    return int(match[1]), int(match[2]), int(match[3])


def highest(tags: Iterable[str]) -> str | None:
    """Return the highest version among the vX.Y.Z tags, without the v; other tags are ignored."""
    versions = [match[1] for tag in tags if (match := TAG.fullmatch(tag))]
    return max(versions, key=parse_version, default=None)


def _version_heading(version: str) -> re.Pattern[str]:
    return re.compile(rf'^## \[{re.escape(version)}\] - [^\n]*\n', re.MULTILINE)


def _body(text: str, heading: re.Pattern[str]) -> tuple[int, int] | None:
    """Return where the body under the first level-2 heading matching heading starts and ends: up to the next level-2 heading, or the end."""
    match = heading.search(text)
    if match is None:
        return None
    following = NEXT_SECTION.search(text, match.end())
    return match.end(), following.start() if following else len(text)


def _has_entries(body: str) -> bool:
    """Check whether body holds anything besides blank lines and subsection headings."""
    return any(line.strip() and not line.startswith('#') for line in body.splitlines())


def prepare(text: str, version: str, date: str) -> str:
    """Return the CHANGELOG text with the Unreleased entries moved under "## [version] - date"."""
    parse_version(version)
    if _version_heading(version).search(text):
        msg = f'CHANGELOG.md already has a section for {version}'
        raise ReleaseError(msg)
    body = _body(text, UNRELEASED)
    if body is None:
        msg = 'CHANGELOG.md has no "## Unreleased" section'
        raise ReleaseError(msg)
    start, end = body
    if not _has_entries(text[start:end]):
        msg = 'CHANGELOG.md has no entries under "## Unreleased": there is nothing to release'
        raise ReleaseError(msg)
    return f'{text[:start]}\n## [{version}] - {date}\n{text[start:]}'


def notes(text: str, version: str) -> str:
    """Return the body of version's section, without its heading or the blank lines around it."""
    parse_version(version)
    body = _body(text, _version_heading(version))
    if body is None:
        msg = f'CHANGELOG.md has no section for {version}'
        raise ReleaseError(msg)
    start, end = body
    return text[start:end].strip('\n') + '\n'


def check(version: str, tags: Sequence[str], text: str, *, publish_only: bool) -> dict[str, bool]:
    """Refuse what the Release workflow must not publish; return whether version is the highest release overall (latest) and within its X.Y (minor_latest)."""
    parsed = parse_version(version)
    tag = f'v{version}'
    if publish_only:
        if tag not in tags:
            msg = f'{tag} does not exist: publish-only republishes a release; run without it to release {version}'
            raise ReleaseError(msg)
    else:
        if tag in tags:
            msg = f'{tag} already exists: release a higher version, or republish this one with publish-only'
            raise ReleaseError(msg)
        top = highest(tags)
        if top is not None and parsed <= parse_version(top):
            msg = f'{version} is not above the highest release, {top}'
            raise ReleaseError(msg)
        prepare(text, version, '0000-00-00')  # the refusals the prepare job would meet, before CI runs
    others = [other for other in tags if other != tag]
    top = highest(others)
    minor_top = highest(other for other in others if (match := TAG.fullmatch(other)) and parse_version(match[1])[:2] == parsed[:2])
    return {
        'latest': top is None or parsed > parse_version(top),
        'minor_latest': minor_top is None or parsed > parse_version(minor_top),
    }


def git_tags() -> list[str]:
    """Return every tag of the repository in the working directory."""
    return subprocess.run(['git', 'tag', '--list'], capture_output=True, text=True, check=True).stdout.split()


def main(argv: Sequence[str] | None = None) -> int:
    """Run one release.py command; return its exit status."""
    parser = argparse.ArgumentParser(prog='release.py', description='Release helper for the Release workflow (docs/development/releasing.md).')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare', help="move CHANGELOG.md's Unreleased entries under the version").add_argument('version')
    check_command = commands.add_parser('check', help='refuse a version the workflow must not publish; print latest= and minor_latest=')
    check_command.add_argument('version')
    check_command.add_argument('--publish-only', action='store_true', help='the version is an existing release to publish again')
    commands.add_parser('notes', help="print the version's CHANGELOG.md section").add_argument('version')
    commands.add_parser('latest', help='print the highest released version')
    args = parser.parse_args(argv)
    try:
        if args.command == 'latest':
            top = highest(git_tags())
            sys.stdout.write(f'{top}\n' if top else '')
            return 0
        text = CHANGELOG.read_bytes().decode('utf-8')
        if args.command == 'prepare':
            today = dt.datetime.now(tz=dt.UTC).date().isoformat()
            CHANGELOG.write_bytes(prepare(text, args.version, today).encode('utf-8'))
        elif args.command == 'check':
            flags = check(args.version, git_tags(), text, publish_only=args.publish_only)
            sys.stdout.write(''.join(f'{key}={str(value).lower()}\n' for key, value in flags.items()))
        else:
            sys.stdout.write(notes(text, args.version))
    except ReleaseError as exc:
        sys.stderr.write(f'release.py: error: {exc}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
