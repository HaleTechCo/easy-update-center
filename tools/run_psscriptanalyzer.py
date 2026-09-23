"""pre-commit shim: lint staged .ps1 files with PSScriptAnalyzer, skipping cleanly.

Not every environment has PowerShell (let alone the PSScriptAnalyzer module), so this
shim never blocks a commit on a machine that lacks them:

  - No ``pwsh`` (PowerShell 7+) or ``powershell`` (Windows PS 5.1) on PATH -> print a
    notice and exit 0.
  - Otherwise delegate to ``tools/run_psscriptanalyzer.ps1``, which itself exits 0 when
    the PSScriptAnalyzer module isn't installed.

pre-commit passes the staged .ps1 paths as argv.
"""

import shutil
import subprocess
import sys
from pathlib import Path

_WRAPPER = Path(__file__).with_name('run_psscriptanalyzer.ps1')


def main(argv: list[str]) -> int:
    """Lint the .ps1 paths in argv, skipping cleanly when PowerShell is absent."""
    files = argv[1:]
    if not files:
        return 0
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if shell is None:
        sys.stdout.write('[skip] No PowerShell (pwsh/powershell) found; skipping .ps1 lint.\n')
        return 0
    cmd = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(_WRAPPER), *files]
    # Inputs are trusted: shell is a resolved absolute path from shutil.which, the wrapper
    # is a repo-local file, and files are the staged paths pre-commit hands us.
    return subprocess.run(cmd, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
