"""compose-check: the isolation the repo-adapter's unauthenticated GETs rely on.

It also checks that only the one-shot uc service can write to the repository
(docs/architecture/overview.md#networks-and-trust-boundaries).

Reads `docker compose config --format json` on stdin; the first argument names the mode,
and a second, `build`, says compose.build.yaml is merged in.
"""

import json
import sys

REPO_SECRETS = {'repo_username', 'repo_password'}
PUSH_SECRET = 'repo_push_auth'
WEB_SECRETS = {'download_proxy_auth'}
UC_SERVICES = ('uc', 'scheduler', 'repo-adapter')
WEB_ENVIRONMENT = {
    'PUBLIC_URL',
    'MODE',
    'DOWNLOAD_BACKEND',
    'DOWNLOAD_BACKEND_URL',
    'DOWNLOAD_ACCESS',
    'DOWNLOAD_PROXY_CA_FILE',
    'TLS_CERT_FILE',
    'TLS_KEY_FILE',
}
"""web's variables, interpolated by Compose from .env.

With no env_file, proxy credentials and the rest of .env never reach it.
"""
UC_IMAGE_SERVICES = (*UC_SERVICES, 'setup')
STAGING = '/procurement'
# What compose.yaml mounts into setup: target -> (source, relative to the project
# directory; read-only).
SETUP_MOUNTS = {
    '/setup/secrets': ('secrets', False),
}
SETUP_ENTRYPOINT = ['uc-setup']
ADAPTER_VOLUME = 'repo-adapter-data'
ADAPTER_DIR = '/data/repo-adapter'


def volume_mounts(service: dict) -> dict[str, tuple[str, bool]]:
    """Return the service's named-volume mounts: target -> (volume, read-only)."""
    return {
        str(volume.get('target')): (
            str(volume.get('source')),
            bool(volume.get('read_only')),
        )
        for volume in service.get('volumes') or []
        if volume.get('type') == 'volume'
    }


def adapter_volume_problems(services: dict) -> list[str]:
    """List where the adapter, scheduler, uc and web break the adapter-volume split.

    The adapter writes only its own volume; scheduler and uc read it; web never sees it.
    """
    problems = []
    adapter = volume_mounts(services['repo-adapter'])
    if any(source == 'uc-data' for source, _ in adapter.values()):
        problems.append('repo-adapter mounts uc-data; it gets only its own volume')
    if adapter.get(ADAPTER_DIR) != (ADAPTER_VOLUME, False):
        problems.append(
            f'repo-adapter must mount {ADAPTER_VOLUME} at {ADAPTER_DIR} read-write'
        )
    problems.extend(
        f'{name} must mount {ADAPTER_VOLUME} at {ADAPTER_DIR} read-only'
        for name in ('scheduler', 'uc')
        if name in services
        and volume_mounts(services[name]).get(ADAPTER_DIR) != (ADAPTER_VOLUME, True)
    )
    if any(
        source == ADAPTER_VOLUME
        for source, _ in volume_mounts(services['web']).values()
    ):
        problems.append(
            f'web mounts {ADAPTER_VOLUME}: the adapter token would reach it'
        )
    return problems


def networks(service: dict) -> set[str]:
    return set(service.get('networks') or {})


def secrets(service: dict) -> set[str]:
    return {s['source'] for s in service.get('secrets') or []}


def mounts(service: dict) -> set[str]:
    return {volume.get('target') for volume in service.get('volumes') or []}


def procurement_binds(service: dict) -> list[str]:
    # The targets at which a service bind-mounts a host folder named procurement,
    # wherever it mounts it.
    # Compose gives the absolute host path, with backslashes on Windows.
    return [
        str(volume.get('target'))
        for volume in service.get('volumes') or []
        if volume.get('type') == 'bind'
        and str(volume.get('source', ''))
        .replace('\\', '/')
        .rstrip('/')
        .endswith('/procurement')
    ]


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
            problems.append(
                f'setup mounts {volume.get("source")!r} at {target}, not ./{source}'
            )
        else:
            projects.add(path.removesuffix(f'/{source}'))
        if bool(volume.get('read_only')) != read_only:
            problems.append(
                f'setup must mount {target} '
                f'{"read-only" if read_only else "read-write"}'
            )
    if len(projects) > 1:
        problems.append(
            f'setup mounts its files from more than one directory: {sorted(projects)}'
        )
    return problems


def main() -> None:
    mode, build = sys.argv[1], sys.argv[2:] == ['build']
    config = json.load(sys.stdin)
    services, problems = config['services'], []
    web, adapter = services['web'], services['repo-adapter']
    if not config['networks']['repo'].get('internal'):
        problems.append('the repo network must be internal')
    if networks(web) & networks(adapter):
        problems.append(
            f'web and repo-adapter share {sorted(networks(web) & networks(adapter))}'
        )
    for name in ('scheduler',) + (('uc',) if 'uc' in services else ()):
        if 'repo' not in networks(services[name]):
            problems.append(f'{name} is not on the repo network')
        if mode == 'airgapped' and secrets(services[name]) & REPO_SECRETS:
            problems.append(
                f'{name} receives the repository credentials in air-gapped mode'
            )
    if secrets(adapter) != REPO_SECRETS:
        problems.append(f'repo-adapter secrets are {sorted(secrets(adapter))}')
    if REPO_SECRETS & secrets(web):
        problems.append('web receives the repository credentials')
    for name, service in sorted(services.items()):
        if name == 'uc':
            continue
        if PUSH_SECRET in secrets(service):
            problems.append(
                f'{name} receives {PUSH_SECRET}, the repository write credential'
            )
        if STAGING in mounts(service):
            problems.append(f'{name} mounts the staging folder {STAGING}')
        if name != 'setup' and procurement_binds(service):
            problems.append(
                f'{name} mounts ./procurement at '
                f'{", ".join(procurement_binds(service))}'
            )
    if 'uc' in services and (
        PUSH_SECRET not in secrets(services['uc'])
        or STAGING not in mounts(services['uc'])
    ):
        problems.append(f'uc must receive {PUSH_SECRET} and mount {STAGING}')
    setup = services.get('setup')
    if setup is None:
        problems.append(
            'setup is missing: docker compose run --rm setup runs first-time setup'
        )
    else:
        if setup.get('network_mode') != 'none' or networks(setup) - {'none'}:
            problems.append('setup must have no network')
        if setup.get('environment'):
            problems.append(
                f'setup reads settings ({sorted(setup["environment"])}); it needs none'
            )
        if secrets(setup):
            problems.append(f'setup receives secrets {sorted(secrets(setup))}')
        if setup.get('entrypoint') != SETUP_ENTRYPOINT:
            problems.append(
                'setup must run uc-setup as its entrypoint, '
                f'not {setup.get("entrypoint")!r}'
            )
        problems += setup_mount_problems(setup)
    # docker compose config folds an env_file into environment, so an env_file on web
    # shows here as extra names.
    if set(web.get('environment') or {}) != WEB_ENVIRONMENT:
        problems.append(
            f'web environment is {sorted(web.get("environment") or {})}, '
            f'not {sorted(WEB_ENVIRONMENT)}'
        )
    if 'web-config' in services:
        problems.append('web-config is back: web takes its variables from Compose')
    if secrets(web) != WEB_SECRETS:
        problems.append(
            f'web secrets are {sorted(secrets(web))}, not {sorted(WEB_SECRETS)}'
        )
    users = {name: service.get('user') for name, service in services.items()}
    if None in users.values() or len(set(users.values())) != 1:
        problems.append(
            f'every service must run as the one UC_UID:UC_GID user: {users}'
        )
    elif next(iter(users.values())).split(':')[0] == '0':
        problems.append('the services must not run as root (UC_UID=0)')
    problems += adapter_volume_problems(services)
    problems += build_problems(services, build=build)
    label = f'{mode}, build' if build else mode
    for problem in problems:
        print(f'compose-check ({label}): {problem}')
    if problems:
        sys.exit(1)
    print(f'compose-check ({label}): isolation ok')


def build_problems(services: dict, *, build: bool) -> list[str]:
    """Check that compose.yaml only runs images.

    Also check that compose.build.yaml builds every one under a name of its own.
    """
    images = {
        services[name].get('image') for name in UC_IMAGE_SERVICES if name in services
    }
    problems = (
        []
        if len(images) == 1
        else [f'the uc-image services run different images: {sorted(images, key=str)}']
    )
    if not build:
        # With a build section, an `up` before the pull would build the checkout under
        # the release's image name.
        return problems + [
            (
                f'{name} has a build section: compose.yaml runs the release images; '
                'building belongs in compose.build.yaml'
            )
            for name, service in sorted(services.items())
            if 'build' in service
        ]
    for name in UC_IMAGE_SERVICES:
        section = services.get(name, {}).get('build') or {}
        if (context_dir(section), section.get('target')) != ('uc', 'runtime'):
            problems.append(
                f'{name} needs build: {{context: uc, target: runtime}} '
                'in compose.build.yaml so docker compose build builds its image'
            )
    if context_dir(services['web'].get('build') or {}) != 'web':
        problems.append(
            'web needs build: {context: web} in compose.build.yaml '
            'so docker compose build builds its image'
        )
    problems += [
        f'{name} builds into {service.get("image")}, a published image name'
        for name, service in sorted(services.items())
        if str(service.get('image', '')).startswith('ghcr.io/')
    ]
    return problems


if __name__ == '__main__':
    main()
