"""Unit tests for uc.repo_adapter.listing: the gap-filled listing (derived and replaced POMs, dates) and its report."""

import datetime as dt
import hashlib
import json
import stat

from archivefixture import make_hpi, make_war, pom_xml
from fakebackend import FakeBackend
from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import build_index, read_report, write_report

NOW = dt.datetime(2026, 9, 23, 0, 0, tzinfo=dt.UTC)
AD = 'org/jenkins-ci/plugins/active-directory/2.967.vf4e254546a_b_3/active-directory-2.967.vf4e254546a_b_3'
CAFFEINE = 'io/jenkins/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_'
AD_GAV = ('org.jenkins-ci.plugins', 'active-directory', '2.967.vf4e254546a_b_3')
CAFFEINE_GAV = ('io.jenkins.plugins', 'caffeine-api', '3.2.4-208.v7e2da_a_7db_82b_')
NGINX_404 = b'<html>\r\n<head><title>404 Not Found</title></head>\r\n<body><hr><center>nginx</center></body></html>\r\n'


def index_of(files, tmp_path, **backend_options):
    backend = FakeBackend(files, **backend_options)
    return backend, build_index(backend, Cache(tmp_path / 'cache'), key='jenkins-prod-local', now=NOW)


def rows_by_name(index):
    return {row['name']: row for row in index.rows}


def test_the_operators_test_repository(tmp_path):
    """Both plugins publish: one POM derived, one HTML "POM" replaced by the embedded one."""
    files = {f'{AD}.hpi': make_hpi(*AD_GAV), f'{AD}.pom': NGINX_404, f'{CAFFEINE}.hpi': make_hpi(*CAFFEINE_GAV)}
    _, index = index_of(files, tmp_path)
    rows = rows_by_name(index)
    assert set(rows) == {n.rsplit('/', 1)[1] + ext for n in (AD, CAFFEINE) for ext in ('.hpi', '.pom')}
    assert index.poms[f'{AD}.pom'] == pom_xml(*AD_GAV)
    assert index.poms[f'{CAFFEINE}.pom'] == pom_xml(*CAFFEINE_GAV)
    report = index.report
    assert report.derived_poms == [':'.join(CAFFEINE_GAV), ':'.join(AD_GAV)]  # in path order
    assert report.replaced_poms == [{'path': f'{AD}.pom', 'reason': 'not XML'}]
    assert report.counts()['needs_pom'] == 0
    derived = rows[AD.rsplit('/', 1)[1] + '.pom']
    assert derived['sha256'] == hashlib.sha256(pom_xml(*AD_GAV)).hexdigest()
    assert derived['actual_sha1'] == hashlib.sha1(pom_xml(*AD_GAV), usedforsecurity=False).hexdigest()
    assert derived['size'] == len(pom_xml(*AD_GAV))


def test_a_usable_repository_pom_wins(tmp_path):
    own = pom_xml(*AD_GAV, name="The repository's own POM")
    _, index = index_of({f'{AD}.hpi': make_hpi(*AD_GAV), f'{AD}.pom': own}, tmp_path)
    assert index.poms[f'{AD}.pom'] is None  # forwarded to the repository, not derived
    assert index.report.derived_poms == []
    assert index.report.replaced_poms == []


def test_a_pom_for_another_artifact_is_replaced(tmp_path):
    files = {f'{AD}.hpi': make_hpi(*AD_GAV), f'{AD}.pom': pom_xml('org.jenkins-ci.plugins', 'git', '5.2.0')}
    _, index = index_of(files, tmp_path)
    assert index.report.replaced_poms[0]['reason'].startswith('declares org.jenkins-ci.plugins:git:5.2.0')
    assert index.poms[f'{AD}.pom'] == pom_xml(*AD_GAV)


def test_a_gradle_build_needs_a_real_pom(tmp_path):
    base = 'org/jenkins-ci/plugins/synopsys-polaris/1.4.0/synopsys-polaris-1.4.0'
    _, index = index_of({f'{base}.hpi': make_hpi('org.jenkins-ci.plugins', 'synopsys-polaris', '1.4.0', layout='gradle')}, tmp_path)
    assert [r['name'] for r in index.rows] == ['synopsys-polaris-1.4.0.hpi']  # upstream drops it: no .pom
    assert index.report.needs_pom == [{'gav': 'org.jenkins-ci.plugins:synopsys-polaris:1.4.0', 'url': f'https://repo.jenkins-ci.org/releases/{base}.pom'}]
    assert index.descriptions[('org.jenkins-ci.plugins', 'synopsys-polaris', '1.4.0')]  # rule 6 still works


def test_a_misplaced_plugin_is_dropped_with_its_correct_path(tmp_path):
    wrong = 'org/jenkins-ci/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_'
    files = {f'{wrong}.hpi': make_hpi(*CAFFEINE_GAV), f'{wrong}.pom': pom_xml(*CAFFEINE_GAV)}
    _, index = index_of(files, tmp_path)
    assert index.rows == []  # neither the archive nor the POM beside it
    assert index.report.misplaced == [{'path': f'{wrong}.hpi', 'correct_path': f'{CAFFEINE}.hpi'}]


def test_a_manifest_without_coordinates_is_never_misplaced(tmp_path):
    base = 'org/example/old/1.0/old-1.0'
    data = make_hpi('org.example', 'old', '1.0', manifest_group=None, manifest_artifact=None)
    _, index = index_of({f'{base}.hpi': data}, tmp_path)
    assert index.report.misplaced == []
    assert index.report.derived_poms == ['org.example:old:1.0']


def test_archive_dates_replace_upload_times(tmp_path):
    _, index = index_of({f'{AD}.hpi': make_hpi(*AD_GAV, date=(2025, 11, 2, 9, 15, 0))}, tmp_path)
    rows = rows_by_name(index)
    for name in ('active-directory-2.967.vf4e254546a_b_3.hpi', 'active-directory-2.967.vf4e254546a_b_3.pom'):
        assert rows[name]['created'] == rows[name]['modified'] == '2025-11-02T09:15:00.000Z'
    assert index.report.dates_from_repository == []


def test_a_placeholder_date_falls_back_to_the_upload_time(tmp_path):
    _, index = index_of({f'{AD}.hpi': make_hpi(*AD_GAV, date=(1980, 2, 1, 0, 0, 0))}, tmp_path)
    assert rows_by_name(index)['active-directory-2.967.vf4e254546a_b_3.hpi']['modified'] == '2026-09-22T23:09:48.946Z'
    assert index.report.dates_from_repository == [{'path': f'{AD}.hpi', 'reason': 'archive date 1980-02-01 is before 2004'}]


def test_cores_get_derived_poms_and_dates(tmp_path):
    war = 'org/jenkins-ci/main/jenkins-war/2.583/jenkins-war-2.583'
    _, index = index_of({f'{war}.war': make_war('2.583', date=(2026, 9, 22, 4, 32, 0))}, tmp_path)
    assert index.poms[f'{war}.pom'] == pom_xml('org.jenkins-ci.main', 'jenkins-war', '2.583', 'Jenkins war')
    assert rows_by_name(index)['jenkins-war-2.583.war']['modified'] == '2026-09-22T04:32:00.000Z'
    assert f'{war}.war' in index.manifests


def test_an_unreadable_archive_is_skipped_and_the_rest_proceed(tmp_path):
    files = {f'{AD}.hpi': make_hpi(*AD_GAV), f'{CAFFEINE}.hpi': make_hpi(*CAFFEINE_GAV)}
    backend = FakeBackend(files)
    backend.failing.add(f'{AD}.hpi')
    index = build_index(backend, Cache(tmp_path / 'cache'), key='k', now=NOW)
    assert index.report.skipped == [{'path': f'{AD}.hpi', 'reason': f'GET {AD}.hpi failed: connection reset'}]
    assert {r['name'] for r in index.rows} == {'caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.hpi', 'caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.pom'}


def test_an_unexpected_failure_skips_only_that_archive(tmp_path):
    class Exploding(FakeBackend):
        def facts(self, entry, path_coordinates, *, plugin):
            if entry.path == f'{AD}.hpi':
                msg = 'File is encrypted, password required for extraction'
                raise RuntimeError(msg)
            return super().facts(entry, path_coordinates, plugin=plugin)

    backend = Exploding({f'{AD}.hpi': make_hpi(*AD_GAV), f'{CAFFEINE}.hpi': make_hpi(*CAFFEINE_GAV)})
    index = build_index(backend, Cache(tmp_path / 'cache'), key='k', now=NOW)
    assert index.report.skipped == [{'path': f'{AD}.hpi', 'reason': 'RuntimeError: File is encrypted, password required for extraction'}]
    assert {r['name'] for r in index.rows} == {'caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.hpi', 'caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.pom'}


def test_an_unreadable_repository_pom_is_replaced_not_fatal(tmp_path):
    class Flaky(FakeBackend):
        def get(self, path):
            if path.endswith('.pom'):
                msg = 'connection reset'
                raise RuntimeError(msg)
            return super().get(path)

    backend = Flaky({f'{AD}.hpi': make_hpi(*AD_GAV), f'{AD}.pom': pom_xml(*AD_GAV)})
    index = build_index(backend, Cache(tmp_path / 'cache'), key='k', now=NOW)
    assert index.report.replaced_poms == [{'path': f'{AD}.pom', 'reason': 'unreadable: RuntimeError: connection reset'}]
    assert index.poms[f'{AD}.pom'] == pom_xml(*AD_GAV)  # the embedded copy stands in


def test_not_an_archive_is_skipped(tmp_path):
    _, index = index_of({f'{AD}.hpi': NGINX_404}, tmp_path)
    assert index.report.skipped[0]['reason'] == 'not a zip archive (no end of central directory record)'


def test_files_outside_maven_layout_pass_through_for_upstream_to_reject(tmp_path):
    _, index = index_of({'plugins/git/git.hpi': b'not even a zip'}, tmp_path)
    assert [r['name'] for r in index.rows] == ['git.hpi']


def test_parent_poms_pass_through_and_bogus_ones_are_reported(tmp_path):
    parent = 'org/jenkins-ci/plugins/plugin/4.88/plugin-4.88.pom'
    bogus = 'org/example/x/1.0/x-1.0.pom'
    files = {parent: pom_xml('org.jenkins-ci.plugins', 'plugin', '4.88'), bogus: NGINX_404}
    _, index = index_of(files, tmp_path)
    assert [r['name'] for r in index.rows] == ['plugin-4.88.pom']
    assert index.poms[parent] is None
    assert index.report.replaced_poms == [{'path': bogus, 'reason': 'not XML'}]


def test_the_cache_spares_a_second_download(tmp_path):
    files = {f'{AD}.hpi': make_hpi(*AD_GAV), f'{AD}.pom': NGINX_404}
    backend = FakeBackend(files)
    cache = Cache(tmp_path / 'cache')
    first = build_index(backend, cache, key='k', now=NOW)
    backend.calls.clear()
    second = build_index(backend, cache, key='k', now=NOW)
    assert second.rows == first.rows
    assert [call for call in backend.calls if call[0] in ('facts', 'get', 'sha1')] == []


def test_artifact_keeper_jars_are_indexed_but_not_listed(tmp_path):
    jar = f'{AD}.jar'
    _, index = index_of({f'{AD}.hpi': make_hpi(*AD_GAV), jar: b'jar bytes'}, tmp_path)
    assert jar in index.jars
    assert all(not r['name'].endswith('.jar') for r in index.rows)


def test_report_is_written_privately(tmp_path):
    _, index = index_of({f'{CAFFEINE}.hpi': make_hpi(*CAFFEINE_GAV)}, tmp_path)
    path = tmp_path / 'state' / 'report.json'
    write_report(index.report, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    report = read_report(path)
    assert report['repo'] == {'type': 'artifact-keeper', 'key': 'jenkins-prod-local'}
    assert report['archives'] == 1
    assert report['derived_poms'] == [':'.join(CAFFEINE_GAV)]
    assert json.loads(path.read_text())['generated_at'] == '2026-09-23T00:00:00Z'
    assert read_report(tmp_path / 'missing.json') is None
