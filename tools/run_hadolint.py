"""pre-commit shim: lint a Dockerfile with hadolint, running it as a container.

hadolint ships as a container rather than a binary, so the Dockerfile has to be piped in over
stdin - the shell spelling would be ``docker run --rm -i hadolint/hadolint hadolint - <
Dockerfile``. That redirect needs a shell, and on Windows the ``bash`` on PATH can be WSL bash
(own PATH, ``/mnt/c`` paths), so this shim does the piping from Python instead. Inline
``# hadolint ignore=`` directives in the Dockerfile still apply.

Usage (from a hook entry):
  python tools/run_hadolint.py PATH/TO/Dockerfile

Needs Docker running. Exits non-zero when Docker is unreachable, so a commit is not silently
waved through - use `git commit --no-verify` when Docker is intentionally down.
"""

import subprocess
import sys
from pathlib import Path

_IMAGE = 'hadolint/hadolint'


_LINT_FINDINGS = 1
_INFRA_FAILURE = 2


def main(argv: list[str]) -> int:
    """Pipe each Dockerfile in argv[1:] through hadolint; return the worst exit code."""
    paths = argv[1:]
    if not paths:
        return 0

    worst = 0
    for path in paths:
        try:
            dockerfile = Path(path).read_bytes()
        except OSError as exc:
            sys.stderr.write(f'{path}: cannot read Dockerfile: {exc}\n')
            worst = max(worst, _INFRA_FAILURE)
            continue

        try:
            # Inputs are trusted: a fixed image name and repo-local paths from the hook.
            result = subprocess.run(
                ['docker', 'run', '--rm', '-i', _IMAGE, 'hadolint', '-'],
                input=dockerfile,
                check=False,
            )
        except FileNotFoundError:
            # No docker binary at all. Fail rather than skip - a Dockerfile changed and
            # went unlinted - but say so plainly instead of raising a traceback.
            sys.stderr.write(
                'docker is not on PATH, so the Dockerfile could not be linted.\nInstall Docker, or skip this hook once with SKIP=hadolint git commit ...\n',
            )
            return _INFRA_FAILURE

        # hadolint exits 1 for lint findings; anything else means it never got to run
        # (daemon down, image pull refused). Distinguish them, or the reader is sent to
        # the Dockerfile to hunt for a problem that is actually in their Docker setup.
        if result.returncode == _LINT_FINDINGS:
            sys.stderr.write(f'{path}: hadolint reported findings (above).\n')
        elif result.returncode:
            sys.stderr.write(
                f'{path}: hadolint could not run (docker exit {result.returncode}); is the daemon running?\n',
            )
        worst = max(worst, result.returncode)

    return worst


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
