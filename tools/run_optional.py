"""pre-commit shim: run a tool if it is on PATH, otherwise skip cleanly.

The shell spelling would be ``bash -c 'command -v TOOL || exit 0; exec TOOL ...'``.
That is not portable: on Windows the ``bash`` on PATH can be WSL bash, which has its own PATH
and its own ``/mnt/c`` view of the filesystem, so the Windows toolchain is invisible to it and
the staged paths do not resolve. This shim does the same job with the venv's Python, which
pre-commit already requires.

Usage (from a hook entry):
  python tools/run_optional.py TOOL [ARG ...]

pre-commit appends the staged paths after ARGs, so they are forwarded as-is. Exits 0 when
TOOL is absent, otherwise returns TOOL's exit code.
"""

import shutil
import subprocess
import sys

# argv[0] (this script) and argv[1] (TOOL) are both required.
_MIN_ARGS = 2


def main(argv: list[str]) -> int:
    """Run argv[1] with argv[2:], or exit 0 if it is not installed."""
    if len(argv) < _MIN_ARGS:
        sys.stderr.write('usage: run_optional.py TOOL [ARG ...]\n')
        return 2
    tool, args = argv[1], argv[2:]
    resolved = shutil.which(tool)
    if resolved is None:
        sys.stdout.write(f'[skip] {tool} is not on PATH; skipping.\n')
        return 0
    # Inputs are trusted: `resolved` is an absolute path from shutil.which, and the
    # remaining args come from the hook entry plus the paths pre-commit hands us.
    return subprocess.run([resolved, *args], check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
