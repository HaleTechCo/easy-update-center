"""Online end to end (CONTRIBUTING.md#tests): generate from repo.jenkins-ci.org, serve, redirect to get.jenkins.io."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netutil import fetch, get, wait_for
from uc.timeutil import iso, utcnow

WEB = 'http://web:8080'


def tree_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())


@pytest.fixture(scope='module')
def release() -> dict:
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-m', 'uc', 'generate'], check=False)
    duration = round(time.monotonic() - started)
    assert result.returncode == 0
    wait_for(WEB + '/uctest.json')
    status = json.loads(Path('/data/status.json').read_text())
    measurements = {
        'finished_at': iso(utcnow()),
        'duration_seconds': duration,
        'uc_extra_args': os.environ.get('UC_EXTRA_ARGS', ''),
        'java_tool_options': os.environ.get('JAVA_TOOL_OPTIONS', ''),
        'plugin_count': status['plugin_count'],
        'tiers': status['tiers'],
        'cache_bytes': tree_size(Path('/data/cache')),
        'release_bytes': tree_size(Path('/data/live').resolve()),
    }
    out = Path('/results') / f'online-{measurements["finished_at"].replace(":", "")}.json'
    out.write_text(json.dumps(measurements, indent=2) + '\n')
    print(json.dumps(measurements, indent=2))
    return status


def test_published_from_the_public_repository(release):
    assert release['last_run']['result'] == 'success'
    assert release['download_backend'] == 'mirror'
    assert release['resources']['source'] == 'github'
    assert len(release['resources']['ref']) == 40
    if '--max-plugins' in os.environ.get('UC_EXTRA_ARGS', ''):
        assert 1 <= release['plugin_count'] <= 25


@pytest.mark.usefixtures('release')
def test_metadata_points_at_the_update_center():
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    assert all(p['url'].startswith(WEB + '/download/plugins/') for p in doc['plugins'].values())
    assert doc['core']['url'].startswith(WEB + '/download/war/')


@pytest.mark.usefixtures('release')
def test_downloads_redirect_to_get_jenkins_io():
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    plugin_id, plugin = min(doc['plugins'].items())
    response = fetch(plugin['url'])
    assert response.status == 302
    assert response.location == f'https://get.jenkins.io/plugins/{plugin_id}/{plugin["version"]}/{plugin_id}.hpi'
    assert fetch(response.location).status in (200, 302)


@pytest.mark.usefixtures('release')
def test_popularity_is_included_online():
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    assert any(p.get('popularity', 0) > 0 for p in doc['plugins'].values())


@pytest.mark.usefixtures('release')
def test_tool_installers_are_mirrored():
    body = get(WEB + '/updates/hudson.tasks.Maven.MavenInstaller.json')
    assert body.lstrip(b'\xef\xbb\xbf').startswith(b'downloadService.post(')


def test_version_routing_uses_generated_tiers(release):
    stable = release['tiers']['stable']
    version = stable[-1] if stable else '2.999.1'
    response = fetch(f'{WEB}/update-center.json?version={version}')
    expected = f'{WEB}/dynamic-stable-{version}/update-center.json' if stable else f'{WEB}/current/update-center.json'
    assert response.location == expected


def test_github_labels_when_a_token_is_given(release):
    if release['warnings'] and any('GitHub' in w for w in release['warnings']):
        pytest.skip('no GitHub token provided (the label test needs one)')
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    assert any(p.get('labels') for p in doc['plugins'].values())
