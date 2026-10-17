"""Run CI scripts in tests the way the runners do.

Stub commands on PATH, and bash with -e and pipefail.
"""

import subprocess
from pathlib import Path

STEP_TIMEOUT = 30


def stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable stub command into tmp_path/bin; return that directory."""
    stubs = tmp_path / 'bin'
    stubs.mkdir(exist_ok=True)
    (stubs / name).write_text(body, encoding='utf-8')
    (stubs / name).chmod(0o755)
    return stubs


def run_bash(
    tmp_path: Path, script: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Run a script as a GitHub Actions bash step runs it.

    That is bash with -e and pipefail, and only env.
    """
    (tmp_path / 'step.sh').write_text(script, encoding='utf-8')
    return subprocess.run(
        ['bash', '--noprofile', '--norc', '-eo', 'pipefail', str(tmp_path / 'step.sh')],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=STEP_TIMEOUT,
    )
