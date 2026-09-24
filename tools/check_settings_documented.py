"""Fail when the settings the code reads and the settings the configuration reference documents differ.

A setting nobody documented is invisible to operators, and a documented setting that nothing reads
is a trap. Both appear silently when the code changes and the reference page does not, so this hook
compares the two sets on every commit, in both directions.

Where the code reads settings:

* `uc/uc/config.py`: every name passed to its env readers (`_get(env, 'NAME'` and the rest of that
  family), to `env.get('NAME'` and to `secret('NAME'`.
* `web/entrypoint.sh`: every `${NAME:-...}` expansion, with or without the colon and with any of
  `-`, `=`, `?`, `+`. The script runs under `set -u`, so an optional environment variable can only
  be read through one of those forms. A name the script sets itself and then reads bare
  (`DOWNLOAD_PROXY_AUTH_HEADER`) is a derived value, not a setting.
* `compose.yaml` and `compose.airgapped.yaml`: every `${NAME...}` interpolation.

What the page documents: every backticked upper-case name in the first cell of a table row, outside
fenced code blocks. Names in the tables under the "Read by bundled software" heading
(`JAVA_TOOL_OPTIONS`, read by the JVM) do not need a reader in this repository.

Run from the repository root, with no arguments.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

PAGE = Path('docs/reference/configuration.md')
BUNDLED_HEADING = 'Read by bundled software'

PYTHON_READ = re.compile(r"(?:\(env,\s*|env\.get\(|secret\()'([A-Z][A-Z0-9_]*)'")
SHELL_READ = re.compile(r'\$\{([A-Z][A-Z0-9_]*):?[-=?+]')
COMPOSE_READ = re.compile(r'\$\{([A-Z][A-Z0-9_]*)')

SOURCES: tuple[tuple[Path, re.Pattern[str]], ...] = (
    (Path('uc/uc/config.py'), PYTHON_READ),
    (Path('web/entrypoint.sh'), SHELL_READ),
    (Path('compose.yaml'), COMPOSE_READ),
    (Path('compose.airgapped.yaml'), COMPOSE_READ),
)
"""Each file that reads settings, with the pattern that finds them in it. Order sets the reader named in reports."""

DOCUMENTED_NAME = re.compile(r'`([A-Z][A-Z0-9_]*)`')
HEADING = re.compile(r'^#{1,6}\s+(.*?)\s*#*$')
FENCE = re.compile(r'^\s*(```|~~~)')
TABLE_SEPARATOR = re.compile(r'^\|[\s:|-]+$')

USAGE = 'usage: check_settings_documented.py - run from the repository root; it takes no arguments'


def names_read(text: str, pattern: re.Pattern[str]) -> set[str]:
    """Return every setting name `pattern` finds in `text`."""
    return {match.group(1) for match in pattern.finditer(text)}


def documented(text: str) -> tuple[set[str], set[str]]:
    """Return the names the page documents, and the subset listed under the bundled-software heading."""
    names: set[str] = set()
    bundled: set[str] = set()
    in_fence = False
    section = ''
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = HEADING.match(line)
        if heading:
            section = heading.group(1)
            continue
        row = line.strip()
        if not row.startswith('|') or TABLE_SEPARATOR.match(row):
            continue
        found = set(DOCUMENTED_NAME.findall(row.split('|')[1]))
        names |= found
        if section == BUNDLED_HEADING:
            bundled |= found
    return names, bundled


def code_settings(root: Path) -> dict[str, str]:
    """Return every setting the code under `root` reads, mapped to the first source file that reads it."""
    readers: dict[str, str] = {}
    for source, pattern in SOURCES:
        for name in sorted(names_read((root / source).read_text(encoding='utf-8'), pattern)):
            readers.setdefault(name, source.as_posix())
    return readers


def problems(readers: dict[str, str], names: set[str], bundled: set[str]) -> list[str]:
    """Return one line per setting that is read but undocumented, or documented but read by nothing."""
    page = PAGE.as_posix()
    found = [f'{name} is read by {readers[name]} but not documented in {page}' for name in sorted(set(readers) - names)]
    found += [f'{name} is documented in {page} but nothing reads it' for name in sorted(names - set(readers) - bundled)]
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Compare the two sets. Return 1 if they differ, 2 on a usage error or when either side is empty, 0 otherwise.

    An empty side is refused rather than passed: a pattern that stopped matching after a refactor
    would otherwise report "nothing out of step" forever - a false green that looks exactly like a
    clean run.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        sys.stderr.write(USAGE + '\n')
        return 2
    root = Path.cwd()
    try:
        readers = code_settings(root)
        names, bundled = documented((root / PAGE).read_text(encoding='utf-8'))
    except OSError as error:
        sys.stderr.write(f'check_settings_documented.py: {error}\n')
        return 2
    if not readers or not names:
        sys.stderr.write('check_settings_documented.py: found no settings in the code, or none documented; a pattern no longer matches\n')
        return 2
    lines = problems(readers, names, bundled)
    for line in lines:
        sys.stdout.write(line + '\n')
    if lines:
        sys.stdout.write(f'\n{len(lines)} setting(s) out of step with {PAGE.as_posix()}.\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
