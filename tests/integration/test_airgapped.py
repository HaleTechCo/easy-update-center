"""Air-gapped end to end (CONTRIBUTING.md#tests).

Doctor, generate through the repo-adapter, serve, and real Jenkins controllers installing plugins.
`make it-airgapped` runs it once per repository type.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jenkins import Jenkins
from netutil import fetch, get, wait_for
from uc.repo_adapter.archive import archive_date, plausible
from uc.versions import VersionTool

WEB = 'http://web:8080'
WEB_PROXY = 'http://web-proxy:8080'
CONTROLLERS = {
    'jenkins-lts-old': os.environ['LTS_OLD'],
    'jenkins-lts-new': os.environ['LTS_NEW'],
    'jenkins-weekly': os.environ['WEEKLY'],
}
TOP_PLUGIN = os.environ['SEED_TOP_PLUGIN']
DOWNLOAD_PREFIX = os.environ['DOWNLOAD_PREFIX']
SEEDED = json.loads(Path('/seed/repo/seeded.json').read_text())
ADAPTER_DIR = {'REPO_ADAPTER_DIR': '/data/repo-adapter'}
VERSIONS = VersionTool(Path('/opt/uc/generator'), Path('/opt/uc/tools'))

CONFIGURE = """
import hudson.model.UpdateSite
import jenkins.model.Jenkins
def uc = Jenkins.get().updateCenter
uc.sites.replaceBy([new UpdateSite("default", "%(site)s/update-center.json")])
uc.save()
def site = uc.getSite("default")
def result = site.updateDirectlyNow()
println("UPDATE=" + result.kind)
println("MESSAGE=" + (result.message ?: ""))
println("CORE=" + site.data?.core?.version)
println("PLUGINS=" + (site.data?.plugins?.size() ?: 0))
"""

INSTALL = """
import jenkins.model.Jenkins
def futures = Jenkins.get().pluginManager.install(["%(plugin)s"], true)
println("INSTALL=" + futures.collect { it.get().status.getClass().simpleName }.join(","))
"""


def uc(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'uc', *args], env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False)


def parse(output: str) -> dict[str, str]:
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def test_doctor_reports_the_seeded_defects():
    result = uc('doctor', '--fetch', '--json')
    assert result.returncode == 1, result.stderr  # something is dropped, by design
    report = json.loads(result.stdout)
    assert report['hpi_without_pom'] == ['io/jenkins/uctest/uc-test-nopom/1.0/uc-test-nopom-1.0.hpi']
    assert 'uc-test-stubpom:1.0' in report['stub_poms']
    assert report['missing_dependencies'] == []
    assert sorted(report['cores']) == sorted(set(SEEDED['cores']))  # WAR POMs derived where missing
    adapter = report['repo_adapter']
    assert adapter['misplaced'] == [
        {
            'path': 'org/jenkins-ci/plugins/uc-test-misplaced/1.0/uc-test-misplaced-1.0.hpi',
            'correct_path': 'io/jenkins/uctest/uc-test-misplaced/1.0/uc-test-misplaced-1.0.hpi',
        }
    ]
    assert [item['reason'] for item in adapter['replaced_poms']] == ['not XML']  # the saved error page
    derived = {item['gav'].split(':')[1] for item in adapter['derived_poms']}
    assert all(item['url'].startswith('https://repo.jenkins-ci.org/releases/') and item['url'].endswith('.pom') for item in adapter['derived_poms'])
    assert {TOP_PLUGIN, SEEDED['html_pom'], 'uc-test-old-a', 'uc-test-old-b', 'jenkins-war'} <= derived
    assert SEEDED['real_pom'] not in derived  # the repository's own POM wins
    assert [item['gav'] for item in adapter['needs_pom']] == ['io.jenkins.uctest:uc-test-nopom:1.0']
    assert adapter['skipped'] == []


@pytest.fixture(scope='module')
def release() -> dict:
    result = uc('generate')
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB + '/uctest.json')
    return json.loads(Path('/data/status.json').read_text())


def test_generation_succeeds_offline_with_only_warnings(release):
    assert release['last_run']['result'] == 'success'
    assert release['mode'] == 'airgapped'
    assert any('GitHub' in warning for warning in release['warnings'])
    assert release['doctor']['hpi_without_pom'] == 1
    assert release['doctor']['repo_adapter']['misplaced'] == 1
    plugins = json.loads(get(WEB + '/current/update-center.actual.json'))['plugins']
    assert 'uc-test-stubpom' in plugins  # a stub POM degrades metadata but is still published
    assert 'uc-test-nopom' not in plugins  # no POM anywhere: dropped by the generator, as uc doctor predicted
    assert 'uc-test-misplaced' not in plugins  # its manifest names another group
    assert {TOP_PLUGIN, SEEDED['html_pom'], 'uc-test-old-a', 'uc-test-old-b'} <= set(plugins)
    assert release['plugin_count'] == release['doctor']['plugins'] == len(plugins)


@pytest.mark.usefixtures('release')
def test_descriptions_are_read_from_inside_the_archives():
    plugins = json.loads(get(WEB + '/current/update-center.actual.json'))['plugins']
    assert plugins[TOP_PLUGIN]['excerpt']  # no .jar in the repository: index.jelly from the .hpi
    assert 'integration-test plugin' in plugins['uc-test-old-a']['excerpt']  # WEB-INF/classes/index.jelly


@pytest.mark.usefixtures('release')
def test_release_dates_are_read_from_the_archives():
    path = Path('/seed/repo/jenkins') / SEEDED['paths'][TOP_PLUGIN]
    with zipfile.ZipFile(path) as archive:
        built = archive_date(archive)
    version = path.parent.name
    stamp = json.loads(get(WEB + '/current/plugin-versions.json'))['plugins'][TOP_PLUGIN][version]['releaseTimestamp']
    released = dt.datetime.strptime(stamp[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=dt.UTC)
    if plausible(built, dt.datetime.now(dt.UTC)) is None:
        assert abs(released - built) < dt.timedelta(minutes=1)  # the build date, not the upload
    else:
        assert abs(released - dt.datetime.now(dt.UTC)) < dt.timedelta(days=1)  # reported fallback


def test_every_stored_core_that_plugins_require_gets_a_tier(release):
    assert set(SEEDED['old_cores']) <= set(release['tiers']['stable'])  # CORE_AGE_DAYS=0, the default


@pytest.mark.usefixtures('release')
def test_core_age_400_keeps_at_most_one_old_core():
    result = uc('generate', env={'CORE_AGE_DAYS': '400', 'UC_DATA_DIR': '/data-age', **ADAPTER_DIR})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    stable = json.loads(Path('/data-age/status.json').read_text())['tiers']['stable']
    oldest = SEEDED['old_cores'][0]
    assert oldest not in stable  # upstream keeps one core older than 400 days as the floor, then stops


@pytest.mark.usefixtures('release')
def test_signed_metadata_points_at_the_update_center():
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    urls = [doc['core']['url']] + [p['url'] for p in doc['plugins'].values()]
    assert all(url.startswith(WEB + '/download/') for url in urls)
    assert doc['connectionCheckUrl'] == WEB + '/uctest.json'


@pytest.mark.usefixtures('release')
def test_downloads_redirect_into_artifactory():
    doc = json.loads(get(WEB + '/current/update-center.actual.json'))
    plugin = doc['plugins'][TOP_PLUGIN]
    response = fetch(plugin['url'])
    assert response.status == 302
    assert response.location.startswith(DOWNLOAD_PREFIX)
    assert len(get(response.location)) > 0


@pytest.mark.usefixtures('release')
@pytest.mark.parametrize('controller', sorted(CONTROLLERS))
def test_routing_offers_only_compatible_plugins(controller):
    version = CONTROLLERS[controller]
    response = fetch(f'{WEB}/update-center.actual.json?version={version}')
    assert response.status in (301, 302)
    assert response.location.startswith(WEB + '/')
    doc = json.loads(get(response.location))
    required = [p['requiredCore'] for p in doc['plugins'].values()]
    assert all(c <= 0 for c in VERSIONS.compare_many([(r, version) for r in required]))


@pytest.mark.usefixtures('release')
@pytest.mark.parametrize('controller', ['jenkins-lts-old', 'jenkins-lts-new'])
def test_controller_trusts_the_signature_and_installs(controller):
    version = CONTROLLERS[controller]
    jenkins = Jenkins(f'http://{controller}:8080')
    jenkins.wait_ready()
    assert jenkins.version() == version
    configured = parse(jenkins.script(CONFIGURE % {'site': WEB}))
    assert configured['UPDATE'] == 'OK', configured.get('MESSAGE')
    tier = json.loads(get(fetch(f'{WEB}/update-center.actual.json?version={version}').location))
    assert int(configured['PLUGINS']) == len(tier['plugins'])  # Jenkins loaded the tier for its version
    installed = parse(jenkins.script(INSTALL % {'plugin': TOP_PLUGIN}))['INSTALL'].split(',')
    assert installed, installed
    assert all(status.startswith('Success') for status in installed), installed


@pytest.mark.usefixtures('release')
def test_proxy_mode_installs_without_anonymous_artifactory_access():
    result = uc('generate', env={'PUBLIC_URL': WEB_PROXY, 'UC_DATA_DIR': '/data-proxy', **ADAPTER_DIR})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB_PROXY + '/uctest.json')
    jenkins = Jenkins('http://jenkins-weekly:8080')
    jenkins.wait_ready()
    configured = parse(jenkins.script(CONFIGURE % {'site': WEB_PROXY}))
    assert configured['UPDATE'] == 'OK', configured.get('MESSAGE')
    installed = parse(jenkins.script(INSTALL % {'plugin': TOP_PLUGIN}))['INSTALL'].split(',')
    assert installed, installed
    assert all(status.startswith('Success') for status in installed), installed


def test_rollback_restores_the_previous_release(release):
    first = release['live_release']
    result = uc('generate', '--force')
    assert result.returncode == 0, result.stderr[-4000:]
    second = json.loads(Path('/data/status.json').read_text())['live_release']
    assert second != first
    assert uc('rollback').returncode == 0
    assert json.loads(Path('/data/status.json').read_text())['live_release'] == first
    assert Path('/data/live').readlink() == Path(f'releases/{first}')
