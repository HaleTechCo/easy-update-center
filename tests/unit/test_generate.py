"""Unit tests for uc.generate.

Whole generate runs against a fake jar, their failure handling and pruning.
"""

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from fakejar import FakeJar, SimpleVersions
from sitefixture import DEFAULT_PLUGINS
from uc.aql import AqlError, RepoFile
from uc.generate import Toolchain, generate
from uc.htaccess import absolute_targets
from uc.jar import JarError
from uc.model import Tiers
from uc.pki import cert_der_b64
from uc.preflight import PreflightError
from uc.publish import (
    LockBusyError,
    RunLock,
    create_staging,
    list_releases,
    live_release,
)
from uc.status import read_status, write_status
from uc.validate import ValidationError
from ucsupport import make_settings

T1 = dt.datetime(2026, 9, 22, 3, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.UTC)
TIERS = Tiers(('2.540',), ('2.528.1',))


def repo_files():
    plugins = [
        RepoFile(
            f'org/jenkins-ci/plugins/{plugin_id}/{version}',
            f'{plugin_id}-{version}.{ext}',
        )
        for plugin_id, version in DEFAULT_PLUGINS.items()
        for ext in ('hpi', 'pom')
    ]
    wars = [
        RepoFile(
            'org/jenkins-ci/main/jenkins-war/2.528.1', f'jenkins-war-2.528.1.{ext}'
        )
        for ext in ('war', 'pom')
    ]
    return plugins + wars


def no_network(url, _headers=None, _timeout=60):
    msg = f'unexpected network access: {url}'
    raise AssertionError(msg)


def no_query(*_args):
    msg = 'AQL must not be queried with the mirror backend'
    raise AssertionError(msg)


def tools_for(settings, **jar_options):
    return Toolchain(
        run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file), **jar_options),
        aql_query=lambda *_args: repo_files(),
        http_get=no_network,
        versions=SimpleVersions(),
    )


def test_airgapped_generation_publishes(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    rid = generate(
        settings,
        tools=tools_for(settings),
        now=T1,
        base_env={'UC2_VERSION': '3.18.4+patches'},
    )
    data = settings.data_dir
    live = data / 'live'
    assert rid == '20260922T030000Z'
    assert live_release(data) == rid
    assert (live / 'current' / 'update-center.json').is_file()
    assert (live / 'stable').readlink() == Path('dynamic-stable-2.528.1')
    assert absolute_targets((live / '.htaccess').read_text()) == []
    mapping = (live / '.htdownload-map').read_text()
    assert (
        'plugins/git/5.2.0 org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi\n' in mapping
    )
    assert (
        'war/2.528.1 org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war\n'
        in mapping
    )
    status = read_status(data)
    assert status['live_release'] == rid
    assert status['last_run']['result'] == 'success'
    assert status['update_center2'] == '3.18.4+patches'
    assert status['uc_version'] == 'dev'
    assert status['resources']['source'] == 'bundled'
    assert status['tiers'] == {'weekly': ['2.540'], 'stable': ['2.528.1']}
    assert status['plugin_count'] == 2
    assert status['doctor']['plugins'] == 2
    assert status['doctor']['missing_cores'] is None
    assert status['doctor']['cores_unchecked'] is None
    assert 'recorded_at' not in status['doctor']
    assert status['doctor']['checked_at'] == '2026-09-22T03:00:00Z'
    assert any('GitHub credentials not set' in w for w in status['warnings'])
    assert (data / 'resources' / 'last-good' / 'warnings.json').is_file()
    assert not list((data / 'releases').glob('*.staging'))


def test_status_reports_the_uc_version(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    generate(
        settings,
        tools=tools_for(settings),
        now=T1,
        base_env={'UC2_VERSION': '3.18.4+patches', 'UC_VERSION': '0.1.0'},
    )
    assert read_status(settings.data_dir)['uc_version'] == '0.1.0'


def test_jar_receives_download_root_and_credentials(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    tools = tools_for(settings)
    generate(settings, tools=tools, now=T1, base_env={})
    env = tools.run_jar.envs[-1]
    assert env['DOWNLOADS_ROOT_URL'] == 'https://uc.example.com/download'
    assert env['ARTIFACTORY_PASSWORD'] == 'secret'
    assert env['POPULARITY_JSON_URL'] == ''
    assert '--arguments-file' in tools.run_jar.calls[-1]


def test_failed_validation_keeps_the_live_release(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    first = generate(settings, tools=tools_for(settings), now=T1, base_env={})
    with pytest.raises(ValidationError, match='download URLs outside'):
        generate(
            settings,
            tools=tools_for(settings, url_root='https://updates.jenkins.io'),
            now=T2,
            base_env={},
        )
    data = settings.data_dir
    assert live_release(data) == first
    status = read_status(data)
    assert status['live_release'] == first
    assert status['last_run']['result'] == 'failed'
    assert status['last_run']['error'] == 'ValidationError: see the generator log'
    assert (data / 'releases' / '.failed-20260922T090000Z').is_dir()


def test_jar_failure_is_recorded(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(JarError):
        generate(
            settings, tools=tools_for(settings, fail_sites=True), now=T1, base_env={}
        )
    assert read_status(settings.data_dir)['last_run']['result'] == 'failed'
    assert live_release(settings.data_dir) is None


def test_preflight_failure_creates_no_staging(tmp_path, secrets_dir):
    (secrets_dir / 'update-center.key').unlink()
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(PreflightError):
        generate(settings, tools=tools_for(settings), now=T1, base_env={})
    releases = settings.data_dir / 'releases'
    assert not releases.exists() or list(releases.iterdir()) == []


def test_plugin_count_drop_needs_force(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    generate(settings, tools=tools_for(settings), now=T1, base_env={})
    shrunk = {'plugins': {'git': '5.2.0'}}
    with pytest.raises(ValidationError, match=re.escape('plugin count 1 < 0.9')):
        generate(settings, tools=tools_for(settings, **shrunk), now=T2, base_env={})
    rid = generate(
        settings,
        force=True,
        tools=tools_for(settings, **shrunk),
        now=T2 + dt.timedelta(minutes=1),
        base_env={},
    )
    assert live_release(settings.data_dir) == rid


def test_online_generation_uses_mirror_and_tool_installers(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path,
        secrets_dir,
        MODE='online',
        REPO_URL='',
        REPO_KEY='',
        REPO_ADAPTER='',
        RESOURCES_SOURCE='bundled',
        TOOL_INSTALLERS_URL='https://updates.example/updates/',
    )
    files = {
        'https://updates.example/updates/': (
            b'<a href="hudson.tasks.Maven.MavenInstaller.json">m</a>'
        ),
        'https://updates.example/updates/hudson.tasks.Maven.MavenInstaller.json': (
            b"downloadService.post('m',{})"
        ),
    }
    tools = Toolchain(
        run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file)),
        aql_query=no_query,
        http_get=lambda url, _headers=None, _timeout=60: files[url],
    )
    generate(settings, tools=tools, now=T1, base_env={})
    live = settings.data_dir / 'live'
    assert (live / '.htdownload-map').read_text() == ''
    assert (
        live / 'current' / 'updates' / 'hudson.tasks.Maven.MavenInstaller.json'
    ).is_file()
    status = read_status(settings.data_dir)
    assert status['download_backend'] == 'mirror'
    assert status['doctor'] is None
    assert tools.run_jar.envs[-1]['POPULARITY_JSON_URL'].startswith(
        'https://raw.githubusercontent.com/'
    )
    assert (
        tools.run_jar.envs[-1]['ARTIFACTORY_URL'] == 'https://repo.jenkins-ci.org:443/'
    )


def test_lock_prevents_concurrent_runs(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    settings.data_dir.mkdir(parents=True)
    with RunLock(settings.data_dir), pytest.raises(LockBusyError):
        generate(settings, tools=tools_for(settings), now=T1, base_env={})


def test_old_releases_are_pruned(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RETAIN_RELEASES='2')
    for hour in range(4):
        generate(
            settings,
            tools=tools_for(settings),
            now=T1 + dt.timedelta(hours=hour),
            base_env={},
        )
    assert list_releases(settings.data_dir) == ['20260922T050000Z', '20260922T060000Z']


def test_generate_reclaims_orphaned_staging_and_work(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    data = settings.data_dir
    stale = '20260922T020000Z'
    create_staging(data, stale)
    (data / 'work' / stale).mkdir(parents=True)

    rid = generate(settings, tools=tools_for(settings), now=T1, base_env={})

    assert not (data / 'releases' / f'{stale}.staging').exists()
    assert not (data / 'work' / stale).exists()
    status = read_status(data)
    warning = next(w for w in status['warnings'] if 'leftover director' in w)
    assert f'releases/{stale}.staging' in warning
    assert f'work/{stale}' in warning
    assert live_release(data) == rid


def test_generation_through_the_adapter(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_token_file.write_text('adapter-token\n')
    settings.repo_adapter_report_file.write_text(
        json.dumps(
            {
                'derived_poms': [
                    {
                        'gav': 'org.jenkins-ci.plugins:git:5.2.0',
                        'url': 'https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom',
                    }
                ],
                'misplaced': [
                    {
                        'path': (
                            'org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi'
                        ),
                        'correct_path': (
                            'io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi'
                        ),
                    }
                ],
            }
        )
    )
    queries = []
    tools = tools_for(settings)
    tools.aql_query = lambda *args: queries.append(args) or repo_files()
    generate(settings, tools=tools, now=T1, base_env={})
    env = tools.run_jar.envs[-1]
    assert env['ARTIFACTORY_URL'] == 'http://repo-adapter:8081/artifactory/'
    assert (env['ARTIFACTORY_USERNAME'], env['ARTIFACTORY_PASSWORD']) == (
        'uc',
        'adapter-token',
    )
    assert queries[0] == (
        'http://repo-adapter:8081/artifactory/api/',
        'jenkins',
        'uc',
        'adapter-token',
    )
    status = read_status(settings.data_dir)
    assert status['doctor']['repo_adapter']['derived_poms'] == 1
    assert status['doctor']['repo_adapter']['misplaced'] == 1
    assert any('dropped by the generator' in w for w in status['warnings'])
    assert (
        'caffeine-api' not in (settings.data_dir / 'status.json').read_text()
    )  # counts only: it is public


def test_status_error_omits_raw_exception_text(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    secret = (
        'AQL query to https://artifactory.corp.internal/artifactory/api/ '
        'failed with HTTP 401: topsecret-body'
    )

    def boom(*_args):
        raise AqlError(secret)

    tools = tools_for(settings)
    tools.aql_query = boom

    with pytest.raises(AqlError):
        generate(settings, tools=tools, now=T1, base_env={})

    error = read_status(settings.data_dir)['last_run']['error']
    assert 'topsecret-body' not in error
    assert 'artifactory.corp.internal' not in error
    assert error == 'AqlError: see the generator log'


def test_post_publish_failure_still_reports_the_new_live_release(
    tmp_path, secrets_dir, monkeypatch
):
    settings = make_settings(tmp_path, secrets_dir)

    def boom(*_args, **_kwargs):
        msg = 'prune blew up'
        raise RuntimeError(msg)

    monkeypatch.setattr('uc.generate.publish.prune', boom)
    with pytest.raises(RuntimeError):
        generate(settings, tools=tools_for(settings), now=T1, base_env={})

    data = settings.data_dir
    assert live_release(data) == '20260922T030000Z'
    status = read_status(data)
    assert status['live_release'] == '20260922T030000Z'
    assert status['last_run']['result'] == 'failed'


def test_a_generation_without_doctor_counts_keeps_the_recorded_ones(
    tmp_path, secrets_dir
):
    settings = make_settings(
        tmp_path,
        secrets_dir,
        MODE='online',
        REPO_URL='',
        REPO_KEY='',
        REPO_ADAPTER='',
        RESOURCES_SOURCE='bundled',
        TOOL_INSTALLERS_URL='',
    )
    recorded = {'plugins': 9, 'checked_at': '2026-10-03T05:30:00Z'}
    write_status(settings.data_dir, {'doctor': recorded})
    tools = Toolchain(
        run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file)),
        aql_query=no_query,
        http_get=no_network,
    )
    generate(settings, tools=tools, now=T1, base_env={})
    assert read_status(settings.data_dir)['doctor'] == recorded


def test_a_generation_with_doctor_counts_keeps_the_recorded_core_check(
    tmp_path, secrets_dir
):
    settings = make_settings(
        tmp_path, secrets_dir
    )  # DOWNLOAD_BACKEND=artifactory, the air-gapped default
    recorded = {
        'plugins': 9,
        'missing_cores': 3,
        'cores_unchecked': 5,
        'checked_at': '2026-01-01T05:30:00Z',
        'recorded_at': '2026-01-01T05:30:00Z',
    }
    write_status(settings.data_dir, {'doctor': recorded})
    generate(settings, tools=tools_for(settings), now=T1, base_env={})
    block = read_status(settings.data_dir)['doctor']
    assert (block['missing_cores'], block['cores_unchecked'], block['recorded_at']) == (
        3,
        5,
        '2026-01-01T05:30:00Z',
    )
    assert block['plugins'] == 2  # the generation's own counts
    assert (
        block['checked_at'] == '2026-09-22T03:00:00Z'
    )  # the injected start time, not the wall clock
