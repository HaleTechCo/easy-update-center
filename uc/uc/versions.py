"""Jenkins version comparisons through the generator's own hudson.util.VersionNumber (VersionTool.java)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from .errors import UcError

if TYPE_CHECKING:
    from .config import Settings


class VersionToolError(UcError):
    pass


class VersionTool:
    """Runs the VersionTool class compiled at image build time (tool_dir) against the generator jars."""

    def __init__(self, jar_dir: Path, tool_dir: Path, java: str = 'java') -> None:
        self._cmd = [java, '-cp', f'{jar_dir}/*:{tool_dir}', 'VersionTool']

    def _run(self, mode: str, lines: Sequence[str]) -> list[str]:
        if not lines:
            return []
        for line in lines:
            if '\n' in line:
                raise VersionToolError(f'version input contains a newline: {line!r}')
        proc = subprocess.run([*self._cmd, mode], input='\n'.join(lines) + '\n', capture_output=True, text=True)
        if proc.returncode != 0:
            raise VersionToolError(f'VersionTool {mode} failed: {proc.stderr.strip()}')
        return proc.stdout.splitlines()

    def compare_many(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        return [int(x) for x in self._run('compare', [f'{a}\t{b}' for a, b in pairs])]

    def sort(self, versions: Iterable[str]) -> list[str]:
        return self._run('sort', list(versions))

    def max_by_key(self, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in self._run('max', [f'{k}\t{v}' for k, v in items]):
            key, _, value = line.partition('\t')
            result[key] = value
        return result

    @classmethod
    def from_settings(cls, settings: 'Settings') -> 'VersionTool':
        return cls(settings.jar_dir, settings.version_tool_dir)
