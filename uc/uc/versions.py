"""Jenkins version comparisons through the generator's own hudson.util.VersionNumber (VersionTool.java)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .errors import UcError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from .config import Settings


class VersionToolError(UcError):
    """VersionTool exited non-zero, or an input would break its line protocol."""


class VersionTool:
    """Runs the VersionTool class compiled at image build time (tool_dir) against the generator jars."""

    def __init__(self, jar_dir: Path, tool_dir: Path, java: str = 'java') -> None:
        """Put every jar in jar_dir and the compiled class in tool_dir on the classpath."""
        self._cmd = [java, '-cp', f'{jar_dir}/*:{tool_dir}', 'VersionTool']

    def _run(self, mode: str, lines: Sequence[str]) -> list[str]:
        if not lines:
            return []
        for line in lines:
            if '\n' in line:
                msg = f'version input contains a newline: {line!r}'
                raise VersionToolError(msg)
        proc = subprocess.run([*self._cmd, mode], input='\n'.join(lines) + '\n', capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            msg = f'VersionTool {mode} failed: {proc.stderr.strip()}'
            raise VersionToolError(msg)
        return proc.stdout.splitlines()

    def compare_many(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        """Compare each (a, b) pair as VersionNumbers; return -1, 0 or 1 for each, in order."""
        return [int(x) for x in self._run('compare', [f'{a}\t{b}' for a, b in pairs])]

    def sort(self, versions: Iterable[str]) -> list[str]:
        """Return the versions in ascending VersionNumber order."""
        return self._run('sort', list(versions))

    def max_by_key(self, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        """Return the highest version for each key among (key, version) items."""
        result: dict[str, str] = {}
        for line in self._run('max', [f'{k}\t{v}' for k, v in items]):
            key, _, value = line.partition('\t')
            result[key] = value
        return result

    @classmethod
    def from_settings(cls, settings: Settings) -> VersionTool:
        """Build the tool from the image's generator jars and compiled VersionTool class."""
        return cls(settings.jar_dir, settings.version_tool_dir)
