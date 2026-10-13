"""Release helper: the release pull request and the Release workflow's checks.

See docs/development/releasing.md.

    python3 tools/release.py prepare VERSION
        VERSION is X.Y.Z or X.Y.Z-rc.N. Make the release pull request's change: move
        CHANGELOG.md's Unreleased entries under "## [VERSION] - <UTC date>", below an
        empty "## Unreleased", and set compose.yaml's uc and web images to VERSION's,
        so a checkout of the release runs its own images. Everything else stays byte
        for byte. For a release candidate only compose.yaml changes, and Unreleased
        must have entries. Refuses a VERSION that is not above the highest version
        tag.
    python3 tools/release.py check VERSION
        Refuse a tag vVERSION the Release workflow must not publish, then print
        version=, prerelease=, latest= and minor_latest= (true|false) for
        $GITHUB_OUTPUT: whether VERSION is a release candidate, the highest release,
        and the highest in its X.Y, so the workflow moves the latest and X.Y image
        tags only for a release that is. A published version is never published again.
    python3 tools/release.py notes VERSION
        Print that version's CHANGELOG.md section without its heading (a release
        candidate's: the Unreleased entries): the GitHub Release's notes, with relative
        links to repository files pointed at that file in the version's tag.
    python3 tools/release.py latest
        Print the highest release (from the vX.Y.Z tags, release candidates left out),
        or nothing.

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
VERSION = re.compile(
    r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?'
)
TAG = re.compile(
    r'v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.[1-9][0-9]*)?)'
)
UNRELEASED = re.compile(r'^## Unreleased[ \t]*\n', re.MULTILINE)
NEXT_SECTION = re.compile(r'^## ', re.MULTILINE)
REPOSITORY_URL = 'https://github.com/HaleTechCo/easy-update-center'
INLINE_CODE = re.compile(r'(`+)(?:(?!\1).)+?\1', re.DOTALL)
LINK_TARGET = re.compile(r'(\]\()([^)\s]+)(\))')
ABSOLUTE_TARGET = re.compile(r'(?:[A-Za-z][A-Za-z0-9+.-]*:|#|//)')
FENCE = re.compile(r'^ {0,3}(```|~~~)')


class ReleaseError(Exception):
    """A refusal; the message says what to fix."""


def parse_version(text: str) -> tuple[int, int, int, int, int]:
    """Return X.Y.Z or X.Y.Z-rc.N as a key that orders versions as SemVer does.

    (X, Y, Z, 0, N) for a release candidate, (X, Y, Z, 1, 0) for the release, so every
    X.Y.Z-rc.N sorts below X.Y.Z. Refuse anything else (a v prefix, another pre-release,
    a leading zero).
    """
    match = VERSION.fullmatch(text)
    if match is None:
        msg = f'{text!r} is not a version: give X.Y.Z, such as 0.1.0, or X.Y.Z-rc.N'
        raise ReleaseError(msg)
    major, minor, patch = int(match[1]), int(match[2]), int(match[3])
    if match[4] is None:
        return major, minor, patch, 1, 0
    return major, minor, patch, 0, int(match[4])


def is_candidate(version: str) -> bool:
    """Return whether version is a release candidate, X.Y.Z-rc.N."""
    return parse_version(version)[3] == 0


def highest(tags: Iterable[str], *, releases_only: bool = False) -> str | None:
    """Return the highest version among the vX.Y.Z and vX.Y.Z-rc.N tags, without the v.

    Release candidates are left out with releases_only. Other tags are ignored.
    """
    versions = [match[1] for tag in tags if (match := TAG.fullmatch(tag))]
    if releases_only:
        versions = [version for version in versions if not is_candidate(version)]
    return max(versions, key=parse_version, default=None)


def _refuse_not_above_highest(version: str, tags: Iterable[str]) -> None:
    """Refuse version unless it is above every version tag in tags."""
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


def unreleased(text: str) -> str:
    """Return the Unreleased section's body.

    ReleaseError when there is no such section, or it holds only headings.
    """
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
    return text[start:end]


def prepare(text: str, version: str, date: str) -> str:
    """Return the CHANGELOG text with the Unreleased entries moved.

    They move under "## [version] - date".
    """
    parse_version(version)
    if _version_heading(version).search(text):
        msg = f'CHANGELOG.md already has a section for {version}'
        raise ReleaseError(msg)
    unreleased(text)
    start = _body(text, UNRELEASED)[0]
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
    """Return the version's release notes, with relative links made absolute.

    A release's CHANGELOG section without its heading or the blank lines around it; a
    release candidate's are the Unreleased entries it was cut from.
    """
    if is_candidate(version):
        body = unreleased(text)
    else:
        span = _body(text, _version_heading(version))
        if span is None:
            msg = f'CHANGELOG.md has no section for {version}'
            raise ReleaseError(msg)
        body = text[span[0] : span[1]]
    return _absolute_links(body.strip('\n') + '\n', version)


def check(
    version: str, tags: Sequence[str], changelog: str, compose: str
) -> dict[str, bool]:
    """Refuse a tag vVERSION the Release workflow must not publish.

    The tag must exist and be above every other release and release candidate: a
    published version is never published again. A release needs its CHANGELOG section,
    a candidate Unreleased entries; compose.yaml must name the version's images. Return
    whether it is a candidate (prerelease), and, for a release, whether it is the
    highest release overall (latest) and within its X.Y (minor_latest).
    """
    parsed = parse_version(version)
    tag = f'v{version}'
    if tag not in tags:
        msg = f'{tag} does not exist: push the tag first'
        raise ReleaseError(msg)
    others = [other for other in tags if other != tag]
    _refuse_not_above_highest(version, others)
    pull_request = f'tag the merged release pull request (release.py prepare {version})'
    candidate = is_candidate(version)
    if candidate:
        try:
            unreleased(changelog)
        except ReleaseError as exc:
            msg = f'{exc}: {pull_request}'
            raise ReleaseError(msg) from None
    elif not _version_heading(version).search(changelog):
        msg = f'CHANGELOG.md has no section for {version}: {pull_request}'
        raise ReleaseError(msg)
    if pin_images(compose, version) != compose:
        msg = f'compose.yaml names other images than {version}: {pull_request}'
        raise ReleaseError(msg)
    if candidate:
        return {'prerelease': True, 'latest': False, 'minor_latest': False}
    top = highest(others, releases_only=True)
    minor_top = highest(
        (
            other
            for other in others
            if (match := TAG.fullmatch(other))
            and parse_version(match[1])[:2] == parsed[:2]
        ),
        releases_only=True,
    )
    return {
        'prerelease': False,
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
            'print version=, prerelease=, latest= and minor_latest='
        ),
    )
    check_command.add_argument('version')
    commands.add_parser(
        'notes', help="print the version's CHANGELOG.md section"
    ).add_argument('version')
    commands.add_parser('latest', help='print the highest released version')
    args = parser.parse_args(argv)
    try:
        if args.command == 'latest':
            top = highest(git_tags(), releases_only=True)
            sys.stdout.write(f'{top}\n' if top else '')
            return 0
        text = CHANGELOG.read_bytes().decode('utf-8')
        if args.command != 'notes':
            compose = COMPOSE.read_bytes().decode('utf-8')
        if args.command == 'prepare':
            parse_version(args.version)
            _refuse_not_above_highest(args.version, git_tags())
            pinned = pin_images(compose, args.version)
            if is_candidate(args.version):
                unreleased(text)  # a candidate of nothing is refused
            else:
                today = dt.datetime.now(tz=dt.UTC).date().isoformat()
                changelog = prepare(text, args.version, today)
                CHANGELOG.write_bytes(changelog.encode('utf-8'))
            COMPOSE.write_bytes(pinned.encode('utf-8'))
        elif args.command == 'check':
            flags = check(args.version, git_tags(), text, compose)
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
