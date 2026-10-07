"""compose-check: the isolation the repo-adapter's unauthenticated GETs rely on, and that only the one-shot uc service can write to the repository (docs/architecture/overview.md#networks-and-trust-boundaries).

Reads `docker compose config --format json` on stdin; the argument names the mode.
"""

import json
import sys

REPO_SECRETS = {'repo_username', 'repo_password'}
PUSH_SECRET = 'repo_push_auth'
WEB_SECRETS = {'download_proxy_auth'}
UC_SERVICES = ('uc', 'scheduler', 'repo-adapter', 'web-config')
UC_IMAGE_SERVICES = (*UC_SERVICES, 'setup')
STAGING = '/procurement'
# What compose.yaml mounts into setup: target -> (source, relative to the project directory; read-only).
SETUP_MOUNTS = {
    '/setup/secrets': ('secrets', False),
    '/setup/procurement': ('procurement', False),
    '/setup/config': ('config', True),
}
SETUP_ENTRYPOINT = ['uc-setup']


def networks(service: dict) -> set[str]:
    return set(service.get('networks') or {})


def secrets(service: dict) -> set[str]:
    return {s['source'] for s in service.get('secrets') or []}


def mounts(service: dict) -> set[str]:
    return {volume.get('target') for volume in service.get('volumes') or []}


def context_dir(build: dict) -> str:
    # Compose gives the absolute build context, with backslashes on Windows.
    return str(build.get('context', '')).replace('\\', '/').rsplit('/', 1)[-1]


def setup_mount_problems(setup: dict) -> list[str]:
    volumes = {volume.get('target'): volume for volume in setup.get('volumes') or []}
    if set(volumes) != set(SETUP_MOUNTS):
        return [f'setup mounts {sorted(volumes, key=str)}, not {sorted(SETUP_MOUNTS)}']
    problems, projects = [], set()
    for target, (source, read_only) in SETUP_MOUNTS.items():
        volume = volumes[target]
        # Compose gives the absolute host path, with backslashes on Windows.
        path = str(volume.get('source', '')).replace('\\', '/')
        if volume.get('type') != 'bind' or not path.endswith(f'/{source}'):
            problems.append(f'setup mounts {volume.get("source")!r} at {target}, not ./{source}')
        else:
            projects.add(path.removesuffix(f'/{source}'))
        if bool(volume.get('read_only')) != read_only:
            problems.append(f'setup must mount {target} {"read-only" if read_only else "read-write"}')
    if len(projects) > 1:
        problems.append(f'setup mounts its files from more than one directory: {sorted(projects)}')
    return problems


def main() -> None:
    mode = sys.argv[1]
    config = json.load(sys.stdin)
    services, problems = config['services'], []
    web, adapter = services['web'], services['repo-adapter']
    if not config['networks']['repo'].get('internal'):
        problems.append('the repo network must be internal')
    if networks(web) & networks(adapter):
        problems.append(f'web and repo-adapter share {sorted(networks(web) & networks(adapter))}')
    for name in ('scheduler',) + (('uc',) if 'uc' in services else ()):
        if 'repo' not in networks(services[name]):
            problems.append(f'{name} is not on the repo network')
        if mode == 'airgapped' and secrets(services[name]) & REPO_SECRETS:
            problems.append(f'{name} receives the repository credentials in air-gapped mode')
    if secrets(adapter) != REPO_SECRETS:
        problems.append(f'repo-adapter secrets are {sorted(secrets(adapter))}')
    if REPO_SECRETS & secrets(web):
        problems.append('web receives the repository credentials')
    for name, service in sorted(services.items()):
        if name == 'uc':
            continue
        if PUSH_SECRET in secrets(service):
            problems.append(f'{name} receives {PUSH_SECRET}, the repository write credential')
        if STAGING in mounts(service):
            problems.append(f'{name} mounts the staging folder {STAGING}')
    if 'uc' in services and (PUSH_SECRET not in secrets(services['uc']) or STAGING not in mounts(services['uc'])):
        problems.append(f'uc must receive {PUSH_SECRET} and mount {STAGING}')
    setup = services.get('setup')
    if setup is None:
        problems.append('setup is missing: make pki-init and the secrets targets run it')
    else:
        if setup.get('network_mode') != 'none' or networks(setup) - {'none'}:
            problems.append('setup must have no network')
        if setup.get('user') != '0:0':
            problems.append(f'setup must run as 0:0, not {setup.get("user")!r}')
        if secrets(setup):
            problems.append(f'setup receives secrets {sorted(secrets(setup))}')
        if setup.get('entrypoint') != SETUP_ENTRYPOINT:
            problems.append(f'setup must run uc-setup as its entrypoint, not {setup.get("entrypoint")!r}')
        problems += setup_mount_problems(setup)
    web_config = services.get('web-config')
    if web_config is None:
        problems.append('web-config is missing: web reads its settings from it')
    else:
        if secrets(web_config):
            problems.append(f'web-config receives secrets {sorted(secrets(web_config))}')
        if web_config.get('network_mode') != 'none':
            problems.append('web-config must have no network')
    if web.get('environment'):
        problems.append(f'web reads settings from its own environment ({sorted(web["environment"])}); web-config writes them')
    if (web.get('depends_on') or {}).get('web-config', {}).get('condition') != 'service_completed_successfully':
        problems.append('web must start after web-config has written its settings')
    if not any(volume.get('target') == '/run/uc-web' and volume.get('read_only') for volume in web.get('volumes') or []):
        problems.append('web must mount the web-config volume read-only at /run/uc-web')
    if web.get('env_file'):
        problems.append('web must have no env_file: .env holds settings and credentials web does not need')
    if secrets(web) != WEB_SECRETS:
        problems.append(f'web secrets are {sorted(secrets(web))}, not {sorted(WEB_SECRETS)}')
    for name in UC_SERVICES:
        # The image's own user is non-root; only setup overrides it.
        if name in services and services[name].get('user'):
            problems.append(f"{name} sets user {services[name]['user']!r}; it must run as the image's non-root user")
    for name in UC_IMAGE_SERVICES:
        build = services.get(name, {}).get('build') or {}
        if (context_dir(build), build.get('target')) != ('uc', 'runtime'):
            problems.append(f'{name} needs build: {{context: uc, target: runtime}} so docker compose build builds its image')
    if context_dir(web.get('build') or {}) != 'web':
        problems.append('web needs build: {context: web} so docker compose build builds its image')
    for problem in problems:
        print(f'compose-check ({mode}): {problem}')
    if problems:
        sys.exit(1)
    print(f'compose-check ({mode}): isolation ok')


if __name__ == '__main__':
    main()
