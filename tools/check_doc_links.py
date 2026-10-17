"""Verify that every relative documentation link in this tree resolves.

The documentation is a tree of cross-linked pages, and nothing else in the gate would
notice a link going stale: a renamed heading breaks every reference to it silently, and
the references that rot most quietly are the ones in comments - code, compose files,
Makefiles, Dockerfiles - which nobody re-reads. This hook is why `docs/` can be
reorganised without that happening.

Two kinds of reference are checked, because the tree contains both:

* a Markdown link in a `.md` file, whose target is resolved relative to the linking
  file;
* a bare `path/to/page.md#anchor` token in a comment of any other file type, whose
  target is resolved relative to the REPOSITORY ROOT, which is how those comments are
  written.

Neither kind is read out of a fenced (``` / ~~~) code block: a link or a heading inside
one is illustrating something, not making a live reference - the docs are full of shell
transcripts, compose snippets and worked examples that legitimately contain link-shaped
and heading-shaped text. `_unfenced_lines` is the one place that decides "inside a
fence", shared by the heading/anchor scanner and the link scanner so the two cannot
drift apart. The accepted trade: a genuinely broken reference that appears only inside a
fence goes unreported, because it was never live to begin with.

`heading_slug` reproduces GitHub's algorithm, because GitHub is where these pages are
read. It gives each space its own hyphen and does NOT collapse runs, so a heading like
`Online / air-gapped` slugs to `online--air-gapped` with TWO hyphens, and keeps accented
letters.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

MARKDOWN_LINK = re.compile(r'\[[^\]]*\]\((?:<([^>]*)>|([^)\s]+))(?:\s+"[^"]*")?\)')
"""A Markdown inline link, capturing the target and discarding any title.

The target is either bare (group 2) or wrapped in CommonMark's angle brackets (group 1),
the form that lets a destination contain spaces or parentheses. Reading only the bare
form would take the brackets as part of the path and report a live
`[text](<https://...>)` link as a missing local file.
"""

BARE_REFERENCE = re.compile(r'(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.md)(#[\w-]+)?')
"""A `path/to/page.md#anchor` token as it appears in a comment.

The anchor is optional.

Illustrated with `path/to/...` rather than a real top-level directory name on purpose:
once this module is tracked, `_targets` scans its OWN source text for exactly this
shape, and an illustration naming a real directory (`docs/`, `tools/`, ...) would
resolve against this repository and could report the checker's own docstring as a broken
reference to itself.
"""

ATX_HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*$')
EXPLICIT_ANCHOR = re.compile(r'<a\s+id="([^"]+)"')
FENCE = re.compile(r'^\s*(```|~~~)')

EXTERNAL_SCHEMES = ('http://', 'https://', 'mailto:', 'ftp://')
MARKDOWN_SUFFIX = '.md'


def heading_slug(text: str) -> str:
    """Reproduce GitHub's heading-to-anchor algorithm (github-slugger).

    Inline code, links and raw HTML are reduced to their text, and the result is
    lowercased. Every character that is not a letter, mark, number, connector
    punctuation (`_`), hyphen or space is dropped, and each space becomes one hyphen.
    Runs are NOT collapsed: `Online / air-gapped` is `online--air-gapped`. Accented
    letters stay as they are (`Café` is `café`).
    """
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    kept = ''.join(
        char
        for char in text.strip().lower()
        if char in ' -'
        or unicodedata.category(char)[0] in 'LMN'
        or unicodedata.category(char) == 'Pc'
    )
    return kept.replace(' ', '-')


def _unfenced_lines(text: str) -> list[str]:
    """The lines of `text` that fall outside a fenced (``` / ~~~) code block.

    Shared by `anchors_in`, which must not read a heading out of a shell transcript, and
    `_targets`, which must not read a link out of a documentation example: either way, a
    fence marks its contents as illustration rather than live content, and one toggle
    here serves both scanners instead of two copies that could drift apart. Applied to
    every file type alike - a fence inside a `.py` docstring is as much an example as
    one inside a Markdown page - so the one accepted trade is a genuinely broken
    reference that appears only inside a fence, which goes unreported because it was
    never a live reference to begin with.
    """  # noqa: D401 -- describes what is returned; imperative mood would read worse here
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lines.append(line)
    return lines


def anchors_in(path: Path) -> set[str]:
    """Every anchor `path` offers.

    One per ATX heading, plus every explicit `<a id=>` tag.

    Headings inside a fenced code block are not headings - this tree's documents are
    full of shell transcripts whose lines start with `#` - and a heading slug that
    repeats gets the host's `-1`, `-2` suffix so that two sections with the same name
    remain separately addressable.
    """
    found: set[str] = set()
    counts: dict[str, int] = {}
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return found
    for line in _unfenced_lines(text):
        heading = ATX_HEADING.match(line)
        if heading:
            base = heading_slug(heading.group(2))
            seen = counts.get(base, 0)
            counts[base] = seen + 1
            found.add(base if seen == 0 else f'{base}-{seen}')
        found.update(match.group(1) for match in EXPLICIT_ANCHOR.finditer(line))
    return found


def _is_repo_path(token: str, root: Path) -> bool:
    """Is `token` written as a path from the repository root, rather than prose?

    A comment saying "see the overlay README.md" names a file the way a sentence names
    it, not the way a link does: it resolves from neither the repository root nor the
    commenting file's own directory. Checking such a mention would report a permanent,
    unfixable failure. A token counts as a real reference only when its first segment is
    a directory that exists at the root, or when it is a root-level file with no
    directory part at all.
    """  # noqa: D400 -- the first line is a question, not a statement; a forced period would misstate it
    head, slash, _ = token.partition('/')
    if not slash:
        return (root / head).is_file()
    return (root / head).is_dir()


def _targets(path: Path, text: str, root: Path) -> list[str]:
    """The references `path` makes, as raw `target[#anchor]` strings.

    A Markdown file is read for real links only: a bare `foo.md` inside its prose is
    prose, not a reference. Any other file type is read for bare tokens instead, because
    that is the only shape a reference takes in a comment - and those are filtered by
    `_is_repo_path`, so that a sentence mentioning a file is not mistaken for a link to
    one. Either way, a fenced code block is scanned out first by `_unfenced_lines`: a
    link or token inside one is an example, not a live reference, exactly as a heading
    inside one is not a live heading.
    """  # noqa: D401 -- describes what is returned; imperative mood would read worse here
    unfenced = '\n'.join(_unfenced_lines(text))
    if path.suffix == MARKDOWN_SUFFIX:
        return [
            match.group(1) if match.group(1) is not None else match.group(2)
            for match in MARKDOWN_LINK.finditer(unfenced)
        ]
    return [
        match.group(1) + (match.group(2) or '')
        for match in BARE_REFERENCE.finditer(unfenced)
        if _is_repo_path(match.group(1), root)
    ]


def broken_links(path: Path, root: Path) -> list[str]:
    """Every reference in `path` that does not resolve, as a human-readable line each.

    A Markdown file's links resolve relative to the file; every other file's bare tokens
    resolve relative to `root`, which is how the `.py` and `.yaml` comments in this tree
    are written.
    """
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    base = path.parent if path.suffix == MARKDOWN_SUFFIX else root
    problems: list[str] = []
    for target in _targets(path, text, root):
        if target.startswith(EXTERNAL_SCHEMES):
            continue
        file_part, _, anchor = target.partition('#')
        destination = path if not file_part else (base / file_part)
        # A link to a DIRECTORY is legitimate and common - a README pointing at the
        # directory it describes - so existence is the test, not file-ness. Only a file
        # can answer for an anchor.
        if file_part and not destination.exists():
            problems.append(f'{path}: no such file: {target}')
            continue
        if anchor and destination.is_file() and anchor not in anchors_in(destination):
            problems.append(f'{path}: no such anchor: {target}')
    return problems


TRACKED_SUFFIXES = ('.md', '.py', '.yaml', '.conf', '.sh')
TRACKED_NAMES = ('Makefile', 'Dockerfile')
TRACKED_ENV_EXAMPLE = re.compile(r'(?:^|/)\.env\.[^/]+\.example$')
"""The files `--tracked` scans: every type that carries a reference in this tree.

Markdown pages, and comments in Python, compose and pre-commit YAML, Apache `.conf`,
shell scripts, the Makefile, the Dockerfiles and the `.env.*.example` files. Not a
performance filter: `git ls-files` also names patches, `.gitattributes` and
extensionless scripts, and this list is the honest statement of which files can hold a
link at all.
"""


def _scannable(name: str) -> bool:
    """Say whether the tracked file `name` is of a type that can hold a reference."""
    return (
        name.endswith(TRACKED_SUFFIXES)
        or name.rsplit('/', 1)[-1] in TRACKED_NAMES
        or TRACKED_ENV_EXAMPLE.search(name) is not None
    )


TRACKED_EXCLUDE_PREFIXES: tuple[str, ...] = ()
"""Tracked paths `--tracked` skips.

Empty: every tracked document is expected to resolve. Add a prefix here only for a
directory of historical records that deliberately names pages which no longer exist, and
say why next to it.
"""

USAGE = (
    'usage: check_doc_links.py --tracked | PATH [PATH ...] - checks the documentation '
    'references in each path given; it takes no default set'
)


def tracked_paths(root: Path) -> list[Path]:
    """Every tracked file `--tracked` should scan, listed by git itself.

    `git ls-files` rather than an `rglob`, so an untracked scratch file, a build
    directory and an ignored virtualenv are out by construction rather than by a second
    exclusion list to keep in step. A path git still tracks but the worktree no longer
    holds is dropped here rather than in `broken_links`, which returns `[]` for an
    unreadable file and would make a deleted one indistinguishable from a clean one.
    """
    # `git` by name, not an absolute path: pre-commit already requires it on PATH, and
    # pinning a path would break every other checkout. It cannot be the thing that is
    # missing here.
    listing = subprocess.run(
        ['git', 'ls-files', '-z'],
        capture_output=True,
        check=True,
        cwd=root,
        encoding='utf-8',
        errors='replace',
    ).stdout
    names = [name for name in listing.split('\0') if name]
    return [
        root / name
        for name in names
        if _scannable(name)
        and not name.startswith(TRACKED_EXCLUDE_PREFIXES)
        and (root / name).is_file()
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Check every path given.

    Returns 1 if anything is broken, 2 if no path was given, 0 otherwise.

    `--tracked`, given alone, stands in for the whole tree: every tracked file of a type
    that can hold a reference. It is what the pre-commit hook passes, because **checking
    only the OUTGOING links of the files in a commit misses every INBOUND one** - a
    commit that renames a heading in one page touches no other file, so nothing would
    re-check the references aimed at it from elsewhere, which is exactly the rot this
    checker exists to catch.

    **NO PATHS IS A USAGE ERROR, NOT AN EMPTY CLEAN RUN.** The loop below iterates what
    it is given, so a bare invocation would check nothing and report success - a FALSE
    GREEN, and the worst shape of one, since "exit 0, no output" is exactly what a clean
    run looks like. A `--tracked` that matches nothing is refused the same way and for
    the same reason.

    Exit 2 rather than 1, so that a misconfiguration is refused and a finding is not: 1
    means "references are broken", and a caller distinguishing the two must not read
    "you gave me nothing to check" as one broken reference.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(USAGE + '\n')
        return 2
    root = Path.cwd()
    if args == ['--tracked']:
        paths = tracked_paths(root)
        if not paths:
            # The same rule as no paths at all: an empty work-list must never read as a
            # clean run.
            sys.stderr.write(
                'check_doc_links.py --tracked: git listed no file to check\n'
            )
            return 2
    else:
        paths = [Path(name) for name in args]
    problems: list[str] = []
    for path in paths:
        problems.extend(broken_links(path, root))
    for problem in problems:
        sys.stdout.write(problem + '\n')
    if problems:
        sys.stdout.write(f'\n{len(problems)} broken documentation reference(s).\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
