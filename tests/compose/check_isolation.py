"""compose-check: the isolation the repo-adapter's unauthenticated GETs rely on (docs/architecture/repo-adapter.md#security).

Reads `docker compose config --format json` on stdin; the argument names the mode.
"""

import json
import sys

REPO_SECRETS = {'repo_username', 'repo_password'}


def networks(service: dict) -> set[str]:
    return set(service.get('networks') or {})


def secrets(service: dict) -> set[str]:
    return {s['source'] for s in service.get('secrets') or []}


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
    for problem in problems:
        print(f'compose-check ({mode}): {problem}')
    if problems:
        sys.exit(1)
    print(f'compose-check ({mode}): isolation ok')


if __name__ == '__main__':
    main()
