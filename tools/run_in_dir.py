"""pre-commit shim: run a tool with the working directory set to a subdirectory.

pre-commit always runs hooks from the repo root, but a tool can need a different working
directory - a Go module in a subdirectory is the case this was written for. Most Go commands
take `go -C DIR ...` and need no help; golangci-lint has no equivalent flag and fails with
"directory prefix ... does not contain main module" when invoked from the root, so it needs a
real chdir. Nothing in this tree needs it today; it is kept ready for the hooks that would.

The shell spelling would be ``bash -c 'cd DIR && exec TOOL ...'``. That is not portable: on
Windows the ``bash`` on PATH can be WSL bash, with its own PATH and ``/mnt/c`` paths, so the
Windows toolchain is invisible to it.

Usage (from a hook entry):
  python tools/run_in_dir.py DIR TOOL [ARG ...]

Exits 127 with a hint when TOOL is not installed, so a missing linter is an obvious failure
rather than a silent pass.
"""

import shutil
import subprocess
import sys
from pathlib import Path

_NOT_FOUND = 127

# argv[0] (this script), argv[1] (DIR) and argv[2] (TOOL) are all required.
_MIN_ARGS = 3


def main(argv: list[str]) -> int:
    """Run argv[2] with argv[3:] from the directory argv[1]."""
    if len(argv) < _MIN_ARGS:
        sys.stderr.write('usage: run_in_dir.py DIR TOOL [ARG ...]\n')
        return 2
    workdir, tool, args = Path(argv[1]), argv[2], argv[3:]
    if not workdir.is_dir():
        sys.stderr.write(f'{workdir}: not a directory\n')
        return 2
    resolved = shutil.which(tool)
    if resolved is None:
        sys.stderr.write(f'{tool} is not on PATH - install it, or skip this hook with SKIP=<hook-id>.\n')
        return _NOT_FOUND
    # Inputs are trusted: `resolved` is an absolute path from shutil.which, and the
    # remaining args come from the hook entry in .pre-commit-config.yaml.
    return subprocess.run([resolved, *args], cwd=workdir, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
