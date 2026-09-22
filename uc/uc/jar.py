"""Running the update-center2 jar (spec §6 steps 3, 4, 7)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from .config import Settings
from .errors import UcError
from .model import Credentials, Tiers

JarRunner = Callable[[list[str], Mapping[str, str], Path], None]


class JarError(UcError):
    pass


def jar_environment(settings: Settings, creds: Credentials, base_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(base_env)
    env.update({
        "DOWNLOADS_ROOT_URL": settings.downloads_root_url,
        "ARTIFACTORY_URL": settings.artifactory_url_with_port,
        "ARTIFACTORY_API_URL": settings.artifactory_api_url,
        "ARTIFACTORY_REPOSITORY": settings.artifactory_repository,
        "ARTIFACTORY_USERNAME": creds.artifactory_username,
        "ARTIFACTORY_PASSWORD": creds.artifactory_password,
        "ARTIFACTORY_CACHEDIR": str(settings.cache_dir),
        "GITHUB_USERNAME": creds.github_username,
        "GITHUB_PASSWORD": creds.github_password,
        "POPULARITY_JSON_URL": settings.popularity_json_url,
        "CERTIFICATE_MINIMUM_VALID_DAYS": str(settings.certificate_minimum_valid_days),
    })
    return env


def java_command(settings: Settings, *args: str) -> list[str]:
    return [
        "java", "-Dfile.encoding=UTF-8",
        f"-DCERTIFICATE_MINIMUM_VALID_DAYS={settings.certificate_minimum_valid_days}",
        "-jar", str(settings.jar), *args,
    ]


def run_jar(cmd: list[str], env: Mapping[str, str], log_path: Path) -> None:
    """Run the jar, streaming its output to stdout and appending it to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log, subprocess.Popen(
        cmd, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
    if proc.returncode != 0:
        raise JarError(f"update-center2 exited with status {proc.returncode}; see {log_path}")


def generate_tiers(
    settings: Settings, env: Mapping[str, str], resources: Path, out: Path, log: Path,
    run: JarRunner = run_jar,
) -> Tiers:
    run(java_command(settings, "--resources-dir", str(resources), *settings.extra_args,
                     "--dynamic-tier-list-file", str(out)), env, log)
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JarError(f"tier list {out} is missing or invalid: {exc}") from exc
    return Tiers(tuple(data.get("weeklyCores") or ()), tuple(data.get("stableCores") or ()))


def generate_sites(
    settings: Settings, env: Mapping[str, str], resources: Path, arguments_file: Path, log: Path,
    run: JarRunner = run_jar,
) -> None:
    run(java_command(settings, "--resources-dir", str(resources), "--arguments-file",
                     str(arguments_file)), env, log)
