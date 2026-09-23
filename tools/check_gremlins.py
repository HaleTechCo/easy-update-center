"""Forbid (and optionally fix) Unicode "gremlins" in text files.

Gremlins are characters that look like ordinary ASCII (or have a faithful ASCII
form) but are not -- smart / curly quotes, the typographic dashes and ellipsis,
non-breaking and other exotic spaces, zero-width and bidirectional control
characters, BOMs. They sneak in via copy-paste and cause confusing diffs, broken
string matches, and (for the bidi controls) "Trojan Source" risks.

This deliberately does NOT touch the project's intentional non-ASCII glyphs that
have no faithful ASCII form (box-drawing and status marks, middle dots, arrows,
degree signs): it acts only on the fixed denylist of known-bad code points below.
The denylist is built from hex code points so this file stays ASCII.

Usage:
  python tools/check_gremlins.py [--fix] PATH [PATH ...]

PATH may be a file or a directory; directories are walked recursively (skipping
.git, virtualenvs, cache dirs, and build artifacts such as *.egg-info). Binary /
non-UTF-8 files are skipped. A path named explicitly is always processed, so the
pre-commit hook -- which passes staged paths as args -- is unaffected by the
walk-time skips.

Without --fix: report every gremlin (path:line:col) and exit non-zero.
With --fix:    replace them in place (smart quotes -> ASCII quotes, dashes ->
               hyphens, ellipsis -> "...", exotic spaces -> a normal space,
               invisibles / BOM / bidi -> removed) and exit non-zero if anything
               changed, so the fixed files are re-staged.
"""

import sys
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path

# (code point, replacement). An empty replacement deletes the character.
_GREMLIN_SPEC = [
    # smart / curly quotes -> straight ASCII quotes
    (0x2018, "'"),
    (0x2019, "'"),
    (0x201A, "'"),
    (0x201B, "'"),
    (0x201C, '"'),
    (0x201D, '"'),
    (0x201E, '"'),
    (0x201F, '"'),
    # typographic dashes + ellipsis -> faithful ASCII
    (0x2013, '-'),
    (0x2014, '-'),
    (0x2026, '...'),
    # non-breaking and other exotic spaces -> a normal space
    (0x00A0, ' '),
    (0x1680, ' '),
    (0x2000, ' '),
    (0x2001, ' '),
    (0x2002, ' '),
    (0x2003, ' '),
    (0x2004, ' '),
    (0x2005, ' '),
    (0x2006, ' '),
    (0x2007, ' '),
    (0x2008, ' '),
    (0x2009, ' '),
    (0x200A, ' '),
    (0x202F, ' '),
    (0x205F, ' '),
    (0x3000, ' '),
    # zero-width / invisible / soft hyphen / word joiner / BOM -> removed
    (0x00AD, ''),
    (0x200B, ''),
    (0x200C, ''),
    (0x200D, ''),
    (0x2060, ''),
    (0xFEFF, ''),
    # bidirectional controls (Trojan Source) -> removed
    (0x202A, ''),
    (0x202B, ''),
    (0x202C, ''),
    (0x202D, ''),
    (0x202E, ''),
    (0x2066, ''),
    (0x2067, ''),
    (0x2068, ''),
    (0x2069, ''),
    # Unicode replacement character (usually a decode mistake) -> removed
    (0xFFFD, ''),
]
GREMLINS = {chr(cp): rep for cp, rep in _GREMLIN_SPEC}

# directories skipped when a path argument is walked recursively
SKIP_DIRS = {
    '.git',
    '.hg',
    '.svn',
    'venv',
    'env',
    'node_modules',
    '__pycache__',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    '.tox',
    '.eggs',
}

# Directory-name PREFIXES skipped when walking. Covers the common convention of
# tagging a virtualenv with its Python version (.venv, .venv313, .venv312, ...),
# which exact-name matching can't catch. This is why '.venv' is not in SKIP_DIRS:
# the prefix already covers it. Bare 'venv' / 'env' stay exact - prefix-matching
# those would swallow real directories like 'environments/'.
SKIP_DIR_PREFIXES = ('.venv',)

# Directory-name SUFFIXES skipped when walking. Unlike SKIP_DIRS these can't be
# matched by exact name because the package name is baked in (mypackage.egg-info,
# other_package.egg-info, ...). Deliberately not including 'build' / 'dist': those are
# plausible real source directory names, and skipping them by name would silently
# hide genuine gremlins.
SKIP_DIR_SUFFIXES = ('.egg-info',)


def _is_skipped(path: Path) -> bool:
    """Return True if any component of path is a skipped directory."""
    return any(part in SKIP_DIRS or part.startswith(SKIP_DIR_PREFIXES) or part.endswith(SKIP_DIR_SUFFIXES) for part in path.parts)


def iter_files(paths: Iterable[str]) -> Iterator[Path]:
    """Yield file paths from the args, walking any directory recursively.

    Skips apply only to the recursive walk; a file named explicitly on the
    command line is always yielded.
    """
    for path in paths:
        p = Path(path)
        if p.is_dir():
            for sub in p.rglob('*'):
                if sub.is_file() and not _is_skipped(sub):
                    yield sub
        else:
            yield p


def scan(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (lineno, col, char) for each gremlin (1-based line and column)."""
    for lineno, line in enumerate(text.split('\n'), 1):
        for col, ch in enumerate(line, 1):
            if ch in GREMLINS:
                yield lineno, col, ch


def process(path: Path, *, fix: bool) -> tuple[int, bool]:
    """Check one file; report hits and optionally fix. Returns (num_hits, changed)."""
    try:
        text = path.read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return 0, False  # binary or unreadable; types:[text] should exclude these
    found = list(scan(text))
    if not found:
        return 0, False
    for lineno, col, ch in found:
        replacement = GREMLINS[ch]
        shown = repr(replacement) if replacement else 'removed'
        print(f'{path}:{lineno}:{col}: U+{ord(ch):04X} {unicodedata.name(ch, "?")} -> {shown}')
    if not fix:
        return len(found), False
    for ch, replacement in GREMLINS.items():
        text = text.replace(ch, replacement)
    path.write_bytes(text.encode('utf-8'))
    return len(found), True


def main(argv: list[str]) -> int:
    """Check (or fix) every path in argv; return a shell exit code."""
    fix = '--fix' in argv
    paths = [a for a in argv if not a.startswith('-')]
    total = 0
    changed = 0
    for path in iter_files(paths):
        hits, was_changed = process(path, fix=fix)
        total += hits
        changed += int(was_changed)
    if fix:
        if changed:
            print(f'\nfixed unicode gremlins in {changed} file(s) -- review and re-stage.')
            return 1
        return 0
    if total:
        print(f'\n{total} unicode gremlin(s) found. Re-run with --fix to replace them.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
