"""Running the update-center2 jar (docs/architecture/generation-pipeline.md#step-7-run-the-generator)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import UcError
from .model import Credentials, Tiers

if TYPE_CHECKING:
    from .config import Settings

JarRunner = Callable[[list[str], Mapping[str, str], Path], None]


class JarError(UcError):
    """update-center2 exited non-zero or did not write the tier list."""


def jar_environment(settings: Settings, creds: Credentials, base_env: Mapping[str, str]) -> dict[str, str]:
    """Return base_env plus the variables update-center2 reads: repository, credentials, GitHub, popularity and limits."""
    env = dict(base_env)
    env.update(
        {
            'DOWNLOADS_ROOT_URL': settings.downloads_root_url,
            'ARTIFACTORY_URL': settings.source_url,
            'ARTIFACTORY_API_URL': settings.source_api_url,
            'ARTIFACTORY_REPOSITORY': settings.repo_key,
            'ARTIFACTORY_USERNAME': creds.repo_username,
            'ARTIFACTORY_PASSWORD': creds.repo_password,
            'ARTIFACTORY_CACHEDIR': str(settings.cache_dir),
            'GITHUB_USERNAME': creds.github_username,
            'GITHUB_PASSWORD': creds.github_password,
            'POPULARITY_JSON_URL': settings.popularity_json_url,
            'CERTIFICATE_MINIMUM_VALID_DAYS': str(settings.certificate_minimum_valid_days),
            'CORE_AGE_DAYS': str(settings.core_age_days),
        }
    )
    return env


CONNECT_TIMEOUT_MS = 10000
"""How long the generator's HttpURLConnection clients wait for a connection: a host the air-gapped extra_hosts list misses stalls seconds, not minutes (docs/architecture/modes.md#fast-failure-for-blocked-hosts)."""


def java_command(settings: Settings, *args: str) -> list[str]:
    """Return the java command line that runs the update-center2 jar with args."""
    timeout = [] if 'sun.net.client.defaultConnectTimeout' in settings.java_tool_options else [f'-Dsun.net.client.defaultConnectTimeout={CONNECT_TIMEOUT_MS}']
    return [
        'java',
        '-Dfile.encoding=UTF-8',
        f'-DCERTIFICATE_MINIMUM_VALID_DAYS={settings.certificate_minimum_valid_days}',
        *timeout,
        '-jar',
        str(settings.jar),
        *args,
    ]


def run_jar(cmd: list[str], env: Mapping[str, str], log_path: Path) -> None:
    """Run the jar, streaming its output to stdout and appending it to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        log_path.open('a', encoding='utf-8') as log,
        subprocess.Popen(
            cmd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors='replace',
        ) as proc,
    ):
        if proc.stdout is None:
            msg = 'update-center2 was started without a stdout pipe'
            raise RuntimeError(msg)
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
    if proc.returncode != 0:
        msg = f'update-center2 exited with status {proc.returncode}; see {log_path}'
        raise JarError(msg)


def generate_tiers(
    settings: Settings,
    env: Mapping[str, str],
    resources: Path,
    out: Path,
    log: Path,
    *,
    run: JarRunner = run_jar,
) -> Tiers:
    """Run update-center2 to write the dynamic tier list to out, and return the tiers it lists."""
    run(java_command(settings, '--resources-dir', str(resources), *settings.extra_args, '--dynamic-tier-list-file', str(out)), env, log)
    try:
        data = json.loads(out.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        msg = f'tier list {out} is missing or invalid: {exc}'
        raise JarError(msg) from exc
    return Tiers(tuple(data.get('weeklyCores') or ()), tuple(data.get('stableCores') or ()))


def generate_sites(
    settings: Settings,
    env: Mapping[str, str],
    resources: Path,
    arguments_file: Path,
    log: Path,
    *,
    run: JarRunner = run_jar,
) -> None:
    """Run update-center2 once in batch mode, generating every update site listed in arguments_file."""
    run(java_command(settings, '--resources-dir', str(resources), '--arguments-file', str(arguments_file)), env, log)
