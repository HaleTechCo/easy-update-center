"""Release helper: the release pull request and the Release workflow's checks.

See docs/development/releasing.md.

    python3 tools/release.py prepare VERSION
        Make the release pull request's change: move CHANGELOG.md's Unreleased entries
        under "## [VERSION] - <UTC date>", below an empty "## Unreleased", and set
        compose.yaml's uc and web images to VERSION's, so a checkout of the release
        runs its own images. Everything else stays byte for byte. Refuses a VERSION
        that is not above the highest vX.Y.Z tag.
    python3 tools/release.py check VERSION [--republish]
        Refuse a tag vVERSION the Release workflow must not publish, then print
        version=, latest=true|false and minor_latest=true|false for $GITHUB_OUTPUT:
        whether VERSION is the highest release, and the highest in its X.Y, so the
        workflow moves the latest and X.Y image tags only then. --republish: the tag
        is published again, so it need not be the highest.
    python3 tools/release.py notes VERSION
        Print that version's CHANGELOG.md section without its heading: the GitHub
        Release's notes, with relative links to repository files pointed at that file in
        the version's tag.
    python3 tools/release.py latest
        Print the highest released version (from the vX.Y.Z tags), or nothing.

Run from the repository root. A refusal prints `release.py: error: ...` and exits 1.
Standard library only, and nothing newer than Python 3.12: the workflow runs it on the
runner's python3.
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
COMPOSE = Path('compose.yaml')
IMAGES = (
    'ghcr.io/haletechco/easy-update-center-uc',
    'ghcr.io/haletechco/easy-update-center-web',
)
IMAGE_REFERENCE = re.compile(
    rf'({"|".join(re.escape(image) for image in IMAGES)}):[^}}\s]+'
)
"""An image reference in compose.yaml.

Such as the default of ${UC_IMAGE:-ghcr.io/haletechco/easy-update-center-uc:0.1.0}.
"""
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
TAG = re.compile(r'v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))')
UNRELEASED = re.compile(r'^## Unreleased[ \t]*\n', re.MULTILINE)
NEXT_SECTION = re.compile(r'^## ', re.MULTILINE)
REPOSITORY_URL = 'https://github.com/HaleTechCo/easy-update-center'
INLINE_CODE = re.compile(r'(`+)(?:(?!\1).)+?\1', re.DOTALL)
LINK_TARGET = re.compile(r'(\]\()([^)\s]+)(\))')
ABSOLUTE_TARGET = re.compile(r'(?:[A-Za-z][A-Za-z0-9+.-]*:|#|//)')
FENCE = re.compile(r'^ {0,3}(```|~~~)')


class ReleaseError(Exception):
    """A refusal; the message says what to fix."""


def parse_version(text: str) -> tuple[int, int, int]:
    """Return X.Y.Z as three integers.

    Refuse anything else (a v prefix, a pre-release, a leading zero).
    """
    match = VERSION.fullmatch(text)
    if match is None:
        msg = f'{text!r} is not a version: give X.Y.Z, such as 0.1.0'
        raise ReleaseError(msg)
    return int(match[1]), int(match[2]), int(match[3])


def highest(tags: Iterable[str]) -> str | None:
    """Return the highest version among the vX.Y.Z tags, without the v.

    Other tags are ignored.
    """
    versions = [match[1] for tag in tags if (match := TAG.fullmatch(tag))]
    return max(versions, key=parse_version, default=None)


def _refuse_not_above_highest(version: str, tags: Iterable[str]) -> None:
    """Refuse version unless it is above every vX.Y.Z tag in tags."""
    top = highest(tags)
    if top is not None and parse_version(version) <= parse_version(top):
        msg = f'{version} is not above the highest release, {top}'
        raise ReleaseError(msg)


def _version_heading(version: str) -> re.Pattern[str]:
    return re.compile(rf'^## \[{re.escape(version)}\] - [^\n]*\n', re.MULTILINE)


def _body(text: str, heading: re.Pattern[str]) -> tuple[int, int] | None:
    """Return where the body under the first level-2 heading matching heading lies.

    That is, where it starts and ends: up to the next level-2 heading, or the end.
    """
    match = heading.search(text)
    if match is None:
        return None
    following = NEXT_SECTION.search(text, match.end())
    return match.end(), following.start() if following else len(text)


def _has_entries(body: str) -> bool:
    """Check whether body holds anything besides blank lines and subsection headings."""
    return any(line.strip() and not line.startswith('#') for line in body.splitlines())


def prepare(text: str, version: str, date: str) -> str:
    """Return the CHANGELOG text with the Unreleased entries moved.

    They move under "## [version] - date".
    """
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
        msg = (
            'CHANGELOG.md has no entries under "## Unreleased": '
            'there is nothing to release'
        )
        raise ReleaseError(msg)
    return f'{text[:start]}\n## [{version}] - {date}\n{text[start:]}'


def pin_images(text: str, version: str) -> str:
    """Return compose.yaml's text with the uc and web images set to version.

    Every reference to either image is set.
    """
    parse_version(version)
    missing = [
        image for image in IMAGES if not re.search(rf'{re.escape(image)}:', text)
    ]
    if missing:
        msg = f'compose.yaml names no {" or ".join(missing)} image to set to {version}'
        raise ReleaseError(msg)
    return IMAGE_REFERENCE.sub(rf'\g<1>:{version}', text)


def _absolute_links(text: str, version: str) -> str:
    """Return text with each relative Markdown link target made absolute.

    Each target points at that file in the version's tag, since a GitHub Release does
    not resolve relative links.

    Links in inline code spans and fenced blocks, and every other byte, stay as they
    are.
    """

    def point(match: re.Match[str]) -> str:
        target = match[2]
        if ABSOLUTE_TARGET.match(target):
            return match[0]
        return (
            f'{match[1]}{REPOSITORY_URL}'
            f'/blob/v{version}/{target.removeprefix("./").lstrip("/")}{match[3]}'
        )

    def prose(chunk: str) -> str:
        parts, last = [], 0
        for code in INLINE_CODE.finditer(chunk):
            parts += [LINK_TARGET.sub(point, chunk[last : code.start()]), code[0]]
            last = code.end()
        return ''.join([*parts, LINK_TARGET.sub(point, chunk[last:])])

    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        opening = FENCE.match(line)
        if fence is None and opening:
            fence = opening[1]
        elif fence is not None and opening and opening[1] == fence:
            fence = None
        elif fence is None:
            line = prose(line)  # noqa: PLW2901
        lines.append(line)
    return ''.join(lines)


def notes(text: str, version: str) -> str:
    """Return the body of version's section.

    Without its heading or the blank lines around it, with relative links made absolute.
    """
    parse_version(version)
    body = _body(text, _version_heading(version))
    if body is None:
        msg = f'CHANGELOG.md has no section for {version}'
        raise ReleaseError(msg)
    start, end = body
    return _absolute_links(text[start:end].strip('\n') + '\n', version)


def check(
    version: str,
    tags: Sequence[str],
    changelog: str,
    compose: str,
    *,
    republish: bool,
) -> dict[str, bool]:
    """Refuse a tag vVERSION the Release workflow must not publish.

    The tag must exist and, unless it is published again (republish), be above every
    other release. CHANGELOG.md must have the version's section and compose.yaml must
    name its images: both come from the release pull request. Return whether version
    is the highest release overall (latest) and within its X.Y (minor_latest).
    """
    parsed = parse_version(version)
    tag = f'v{version}'
    if tag not in tags:
        msg = f'{tag} does not exist: push the tag first'
        raise ReleaseError(msg)
    others = [other for other in tags if other != tag]
    if not republish:
        _refuse_not_above_highest(version, others)
    pull_request = f'tag the merged release pull request (release.py prepare {version})'
    if not _version_heading(version).search(changelog):
        msg = f'CHANGELOG.md has no section for {version}: {pull_request}'
        raise ReleaseError(msg)
    if pin_images(compose, version) != compose:
        msg = f'compose.yaml names other images than {version}: {pull_request}'
        raise ReleaseError(msg)
    top = highest(others)
    minor_top = highest(
        other
        for other in others
        if (match := TAG.fullmatch(other)) and parse_version(match[1])[:2] == parsed[:2]
    )
    return {
        'latest': top is None or parsed > parse_version(top),
        'minor_latest': minor_top is None or parsed > parse_version(minor_top),
    }


def git_tags() -> list[str]:
    """Return every tag of the repository in the working directory."""
    return subprocess.run(
        ['git', 'tag', '--list'], capture_output=True, text=True, check=True
    ).stdout.split()


def main(argv: Sequence[str] | None = None) -> int:
    """Run one release.py command; return its exit status."""
    parser = argparse.ArgumentParser(
        prog='release.py',
        description=(
            'Release helper for the Release workflow (docs/development/releasing.md).'
        ),
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser(
        'prepare',
        help="move CHANGELOG.md's Unreleased entries under the version, set the images",
    ).add_argument('version')
    check_command = commands.add_parser(
        'check',
        help=(
            'refuse a tag the workflow must not publish; '
            'print version=, latest= and minor_latest='
        ),
    )
    check_command.add_argument('version')
    check_command.add_argument(
        '--republish',
        action='store_true',
        help='the tag was published before and is published again',
    )
    commands.add_parser(
        'notes', help="print the version's CHANGELOG.md section"
    ).add_argument('version')
    commands.add_parser('latest', help='print the highest released version')
    args = parser.parse_args(argv)
    try:
        if args.command == 'latest':
            top = highest(git_tags())
            sys.stdout.write(f'{top}\n' if top else '')
            return 0
        text = CHANGELOG.read_bytes().decode('utf-8')
        if args.command != 'notes':
            compose = COMPOSE.read_bytes().decode('utf-8')
        if args.command == 'prepare':
            parse_version(args.version)
            _refuse_not_above_highest(args.version, git_tags())
            today = dt.datetime.now(tz=dt.UTC).date().isoformat()
            changelog = prepare(text, args.version, today)
            COMPOSE.write_bytes(pin_images(compose, args.version).encode('utf-8'))
            CHANGELOG.write_bytes(changelog.encode('utf-8'))
        elif args.command == 'check':
            flags = check(
                args.version, git_tags(), text, compose, republish=args.republish
            )
            lines = [f'version={args.version}\n']
            lines += [f'{key}={str(value).lower()}\n' for key, value in flags.items()]
            sys.stdout.write(''.join(lines))
        else:
            sys.stdout.write(notes(text, args.version))
    except ReleaseError as exc:
        sys.stderr.write(f'release.py: error: {exc}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
