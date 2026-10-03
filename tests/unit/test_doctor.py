"""Unit tests for uc.doctor: manifest and dependency parsing, repository analysis and the report."""

import base64
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from httpstub import StubServer
from uc.aql import Gav, RepoFile, classify
from uc.doctor import (
    ArtifactSource,
    DoctorError,
    DoctorReport,
    analyze,
    attach_adapter_report,
    attach_core_check,
    check_since,
    find_missing_cores,
    format_report,
    inspect_repository,
    is_stub_pom,
    parse_dependencies,
    parse_manifest,
    record,
    required_cores,
    run_doctor,
)
from uc.model import Credentials
from uc.status import read_status
from uc.versions import VersionTool
from ucsupport import make_settings

CREDS = Credentials('uc', 'secret', 'uc', 'uc', github_placeholder=True)
GIT = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')


def versions():
    return VersionTool(Path('/opt/uc/generator'), Path('/opt/uc/tools'))


def plugin(artifact, version, *extensions, group='org/jenkins-ci/plugins'):
    return [RepoFile(f'{group}/{artifact}/{version}', f'{artifact}-{version}.{ext}') for ext in extensions]


class FakeSource:
    """Stands in for ArtifactSource: POMs and manifests looked up by artifactId."""

    def __init__(self, poms, manifests):
        """Serve the given POM bytes and manifest texts, keyed by artifactId."""
        self.poms, self.manifests = poms, manifests

    def pom(self, gav):
        """Return the POM for gav, or None."""
        return self.poms.get(gav.artifact_id)

    def manifest(self, gav):
        """Return the manifest text for gav, or None."""
        return self.manifests.get(gav.artifact_id)


def test_parse_manifest_joins_continuation_lines_and_stops_at_first_section():
    text = 'Manifest-Version: 1.0\r\nShort-Name: git\r\nPlugin-Dependencies: credentials:2.6.1,scm-api:600.v\r\n 1;resolution:=optional\r\n\r\nName: extra\r\nShort-Name: other\r\n'
    assert parse_manifest(text) == {
        'Manifest-Version': '1.0',
        'Short-Name': 'git',
        'Plugin-Dependencies': 'credentials:2.6.1,scm-api:600.v1;resolution:=optional',
    }


def test_parse_dependencies():
    assert parse_dependencies('a:1.0,b:2.0;resolution:=optional, c:3') == [
        ('a', '1.0', False),
        ('b', '2.0', True),
        ('c', '3', False),
    ]
    assert parse_dependencies('') == []


def test_is_stub_pom():
    assert not is_stub_pom(b'<project xmlns="http://maven.apache.org/POM/4.0.0"><name>Git plugin</name></project>')
    assert is_stub_pom(b'<project><artifactId>stub</artifactId></project>')
    assert is_stub_pom(b'<project><name>  </name></project>')
    assert is_stub_pom(b'not xml')
    assert is_stub_pom(b'<!DOCTYPE p [<!ENTITY x "y">]><project><name>&x;</name></project>')


def test_analyze_reports_every_defect():
    listing = classify(
        plugin('git', '5.2.0', 'hpi', 'pom')
        + plugin('git', '5.10.0', 'hpi', 'pom')
        + plugin('credentials', '2.0', 'hpi', 'pom')
        + plugin('orphan', '1.0', 'hpi')
        + plugin('stub', '1.0', 'hpi', 'pom')
        + plugin('jenkins-war', '2.528.1', 'war', 'pom', group='org/jenkins-ci/main')
        + plugin('jenkins-war', '2.540', 'war', group='org/jenkins-ci/main')
        + [RepoFile('plugins/x/1.0', 'x.hpi')]
    )
    source = FakeSource(
        poms={'git': b'<project><name>Git</name></project>', 'credentials': b'<project><name>C</name></project>', 'stub': b'<project/>'},
        manifests={'git': 'Plugin-Dependencies: credentials:2.6.1,scm-api:1;resolution:=optional,workflow-step-api:1.0\n', 'stub': 'Short-Name: stub\n'},
    )
    report = analyze(listing, versions(), source)
    assert report.plugins == 3
    assert report.cores == ['2.528.1']
    assert report.hpi_without_pom == ['org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi']
    assert report.war_without_pom == ['org/jenkins-ci/main/jenkins-war/2.540/jenkins-war-2.540.war']
    assert report.non_maven == ['plugins/x/1.0/x.hpi']
    assert report.stub_poms == ['stub:1.0']
    assert report.missing_dependencies == [
        'git:5.10.0 requires workflow-step-api 1.0 (not in the repository)',
        'git:5.10.0 requires credentials >= 2.6.1 (newest available: 2.0)',
    ]
    assert report.unchecked == 1  # credentials has no manifest
    assert report.dropped == 3
    assert report.exit_code() == 1
    assert report.summary()['missing_dependencies'] == 2


def test_artifact_source_reads_the_update_center2_cache(tmp_path):
    source = ArtifactSource('http://artifactory:8081/artifactory/', 'jenkins', 'uc', 'secret', cache_dir=tmp_path / 'cache', allow_network=False)
    manifest_path = '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi!/META-INF/MANIFEST.MF'
    digest = hashlib.sha256(manifest_path.encode()).hexdigest()
    cached = tmp_path / 'cache' / digest[:2] / digest
    cached.parent.mkdir(parents=True)
    cached.write_text('Short-Name: git\n')
    assert source.cache_file('http://artifactory:8081' + manifest_path) == cached
    assert source.manifest(GIT) == 'Short-Name: git\n'
    assert source.pom(GIT) is None  # not cached and network not allowed
    pom_digest = hashlib.sha256(b'/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom').hexdigest()
    (tmp_path / 'cache' / pom_digest[:2] / pom_digest).mkdir(parents=True)  # update-center2's cached failure
    assert source.pom(GIT) is None


def test_artifact_source_fetches_with_basic_auth():
    routes = {('GET', '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'): (200, {}, b'<project/>')}
    with StubServer(routes) as stub:
        source = ArtifactSource(stub.url + '/artifactory/', 'jenkins', 'uc', 'secret', cache_dir=None, allow_network=True)
        assert source.pom(GIT) == b'<project/>'
        assert source.manifest(GIT) is None  # 404
    assert stub.requests[0][2]['Authorization'] == 'Basic ' + base64.b64encode(b'uc:secret').decode()


def test_run_doctor_includes_live_tiers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    release = settings.data_dir / 'releases' / '20260922T000000Z'
    release.mkdir(parents=True)
    (release / 'tiers.json').write_text('{"weeklyCores": [], "stableCores": ["2.528.1"]}')
    (settings.data_dir / 'live').symlink_to('releases/20260922T000000Z')
    files = plugin('git', '5.2.0', 'hpi', 'pom') + plugin('jenkins-war', '2.528.1', 'war', 'pom', group='org/jenkins-ci/main')
    report = run_doctor(settings, CREDS, fetch=False, versions=VersionTool.from_settings(settings), query=lambda *_args: files)
    assert report.tiers == {'weeklyCores': [], 'stableCores': ['2.528.1']}
    assert report.unchecked == 1
    assert report.exit_code() == 0


ADAPTER_REPORT = {
    'derived_poms': [{'gav': 'io.jenkins.plugins:caffeine-api:3.2.4', 'url': 'https://repo.jenkins-ci.org/releases/io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4.pom'}],
    'replaced_poms': [{'path': 'org/x/ad/1/ad-1.pom', 'reason': 'not XML', 'url': 'https://repo.jenkins-ci.org/releases/org/x/ad/1/ad-1.pom'}],
    'needs_pom': [{'gav': 'org.jenkins-ci.plugins:polaris:1.4.0', 'url': 'https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/polaris/1.4.0/polaris-1.4.0.pom'}],
    'misplaced': [{'path': 'org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi', 'correct_path': 'io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi'}],
    'skipped': [{'path': 'org/x/big/1/big-1.hpi', 'reason': 'larger than 512 MiB'}],
    'dates_from_repository': [{'path': 'org/x/cas/1/cas-1.hpi', 'reason': 'archive date 1980-02-01 is before 2004'}],
}


def test_the_adapter_report_adds_sections_and_counts_its_drops(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_report_file.write_text(json.dumps({'archives': 5, **ADAPTER_REPORT}))
    listing = classify(plugin('polaris', '1.4.0', 'hpi') + plugin('git', '5.2.0', 'hpi', 'pom'))
    report = attach_adapter_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert report.dropped == 3  # polaris (no POM at all), the misplaced and the skipped archive
    assert report.summary()['repo_adapter'] == dict.fromkeys(ADAPTER_REPORT, 1)
    text = format_report(report, settings)
    assert '(through repo-adapter)' in text
    assert 'procure the real .pom (1):' in text
    assert ADAPTER_REPORT['needs_pom'][0]['url'] in text
    assert '-> ' + 'io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi' in text.replace('  ->  ', ' -> ')
    lines = text.splitlines()
    assert 'POMs filled in from archives; the real .pom is optional (1):' in lines
    assert '  io.jenkins.plugins:caffeine-api:3.2.4  https://repo.jenkins-ci.org/releases/io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4.pom' in lines
    assert '.pom files ignored (not usable); replace with the real .pom (1):' in lines
    assert '  org/x/ad/1/ad-1.pom: not XML  https://repo.jenkins-ci.org/releases/org/x/ad/1/ad-1.pom' in lines
    assert 'archive date 1980-02-01 is before 2004' in text


def test_without_the_adapter_there_is_no_adapter_report(tmp_path, secrets_dir):
    report = attach_adapter_report(analyze(classify([]), versions(), FakeSource({}, {})), make_settings(tmp_path, secrets_dir))
    assert report.repo_adapter is None
    assert report.summary()['repo_adapter'] is None


def test_format_report(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    listing = classify(plugin('orphan', '1.0', 'hpi') + plugin('git', '5.2.0', 'hpi', 'pom'))
    text = format_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert 'Source: artifactory repository jenkins at http://artifactory:8081/artifactory/' in text
    assert 'through repo-adapter' not in text
    assert 'Jenkins cores with a POM: none' in text
    assert 'Dropped by the generator: 1' in text
    assert 'org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi' in text
    assert 'Not checked (POM or manifest unavailable; run with --fetch): 1' in text


class ReleaseSource:
    """Stands in for ArtifactSource: manifests looked up by (artifactId, version)."""

    def __init__(self, manifests):
        """Serve the given manifest texts, keyed by (artifactId, version)."""
        self.manifests = manifests

    def pom(self, gav):
        """Return no POM: the core check reads manifests only."""
        return self.manifests.get(('pom', gav.artifact_id))

    def manifest(self, gav):
        """Return the manifest for gav's release, or None."""
        return self.manifests.get((gav.artifact_id, gav.version))


def core_manifest(version, key='Jenkins-Version'):
    return f'Manifest-Version: 1.0\r\n{key}: {version}\r\n\r\n'


def test_each_missing_dependency_is_recorded_as_data():
    listing = classify(plugin('ad', '2.0', 'hpi', 'pom') + plugin('mailer', '400.v1', 'hpi', 'pom'))
    manifests = {
        'ad': 'Manifest-Version: 1.0\r\nPlugin-Dependencies: mailer:534.v1,ionicons-api:94.v1,matrix:3.0;resolution:=optional\r\n\r\n',
        'mailer': 'Manifest-Version: 1.0\r\n\r\n',
    }
    report = analyze(listing, versions(), FakeSource({}, manifests))
    assert report.missing_dependency_details == [
        {'plugin': 'ad', 'plugin_group': 'org.jenkins-ci.plugins', 'plugin_version': '2.0', 'dependency': 'ionicons-api', 'required': '94.v1'},
        {'plugin': 'ad', 'plugin_group': 'org.jenkins-ci.plugins', 'plugin_version': '2.0', 'dependency': 'mailer', 'required': '534.v1'},
    ]
    assert report.missing_dependencies == ['ad:2.0 requires ionicons-api 94.v1 (not in the repository)', 'ad:2.0 requires mailer >= 534.v1 (newest available: 400.v1)']


def test_required_cores_cover_every_release_once():
    listing = classify(plugin('ad', '1.0', 'hpi', 'jpi', 'pom') + plugin('ad', '2.0', 'hpi', 'pom') + plugin('old', '1.0', 'hpi', 'pom') + plugin('gone', '1.0', 'hpi', 'pom') + plugin('odd', '1.0', 'hpi', 'pom'))
    source = ReleaseSource(
        {
            ('ad', '1.0'): core_manifest('2.479.1'),
            ('ad', '2.0'): core_manifest('2.541.3'),
            ('old', '1.0'): core_manifest('1.625.3', key='Hudson-Version'),
            ('odd', '1.0'): core_manifest('2.60-SNAPSHOT'),
        }
    )
    required, unchecked = required_cores(listing, source)
    assert required == {'2.479.1': ['org.jenkins-ci.plugins:ad:1.0'], '2.541.3': ['org.jenkins-ci.plugins:ad:2.0'], '1.625.3': ['org.jenkins-ci.plugins:old:1.0']}
    assert unchecked == 1  # gone: no manifest


REQUIRED = {'1.625.3': ['g:old:1'], '2.479.1': ['g:a:1', 'g:b:1'], '2.541.3': ['g:c:1'], '2.555.1': ['g:d:1']}


def test_missing_cores_start_at_the_oldest_stocked_core():
    floor, missing, below, oldest = find_missing_cores(REQUIRED, ['2.479.1', '2.528.3'], None, versions())
    assert floor == '2.479.1'
    assert [core['version'] for core in missing] == ['2.541.3', '2.555.1']
    assert missing[0] == {
        'version': '2.541.3',
        'url': 'https://repo.jenkins-ci.org/releases/org/jenkins-ci/main/jenkins-war/2.541.3/jenkins-war-2.541.3.war',
        'required_by': 1,
        'example': 'g:c:1',
    }
    assert (below, oldest) == (1, '1.625.3')


def test_since_sets_the_floor():
    floor, missing, below, oldest = find_missing_cores(REQUIRED, [], '2.500.1', versions())
    assert floor == '2.500.1'
    assert [core['version'] for core in missing] == ['2.541.3', '2.555.1']
    assert (below, oldest) == (2, '1.625.3')


def test_without_a_core_or_since_there_is_no_floor():
    assert find_missing_cores(REQUIRED, [], None, versions()) == (None, [], 0, None)


@pytest.mark.parametrize('since', ['2.x', 'latest', '2.462.1\n2.479.1', ''])
def test_since_must_be_a_jenkins_version(since):
    with pytest.raises(DoctorError, match='--since takes a Jenkins version'):
        check_since(since)


def test_the_core_check_fills_the_report():
    listing = classify(plugin('ad', '2.0', 'hpi', 'pom') + plugin('jenkins-war', '2.462.3', 'war', 'pom', group='org/jenkins-ci/main'))
    report = analyze(listing, versions(), ReleaseSource({}))
    attach_core_check(report, listing, ReleaseSource({('ad', '2.0'): core_manifest('2.479.1')}), versions(), since=None)
    assert report.required_cores == {'2.479.1': ['org.jenkins-ci.plugins:ad:2.0']}
    assert report.core_floor == '2.462.3'
    assert [core['version'] for core in report.missing_cores] == ['2.479.1']


def test_the_report_lists_missing_cores(tmp_path, secrets_dir):
    report = DoctorReport(
        cores=['2.479.1'],
        required_cores={'2.479.1': ['g:a:1'], '2.541.3': ['g:c:1']},
        core_floor='2.479.1',
        missing_cores=[{'version': '2.541.3', 'url': 'U', 'required_by': 1, 'example': 'g:c:1'}],
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    assert 'Cores plugins require that the repository lacks, at or above 2.479.1 (1):' in lines
    assert '  2.541.3  U  (required by 1 release, e.g. g:c:1)' in lines
    assert not [line for line in lines if 'older required core' in line]


WARNING = '  Warning: once pushed, 2.361.4 is the oldest core in the repository, and so the floor: every later --download-war required also stages the missing cores plugins require from 2.361.4 up, for plugins you add later too'


def test_the_report_names_the_older_core_it_leaves_out_and_how_to_stage_it(tmp_path, secrets_dir):
    report = DoctorReport(cores=['2.462.1', '2.479.1'], required_cores={'2.361.4': ['g:j:1']}, core_floor='2.462.1', missing_cores_below_floor=1, oldest_core_below_floor='2.361.4')
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    start = lines.index('1 older required core below 2.462.1 not listed: 2.361.4')
    assert lines[start + 1 : start + 3] == ['  To stage it: uc doctor --download-war required --since 2.361.4', WARNING]


def test_several_older_cores_are_staged_from_the_oldest(tmp_path, secrets_dir):
    report = DoctorReport(cores=['2.462.1'], required_cores={'2.361.4': ['g:j:1'], '2.401.3': ['g:k:1']}, core_floor='2.462.1', missing_cores_below_floor=2, oldest_core_below_floor='2.361.4')
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    start = lines.index('2 older required cores below 2.462.1 not listed, the oldest 2.361.4')
    assert lines[start + 1 : start + 3] == ['  To stage them: uc doctor --download-war required --since 2.361.4 (a later version stages only those from it up)', WARNING]


def test_a_floor_set_with_since_gets_no_floor_warning(tmp_path, secrets_dir):
    report = DoctorReport(cores=['2.462.1'], required_cores={'2.479.1': ['g:a:1']}, core_floor='2.500.1', missing_cores_below_floor=1, oldest_core_below_floor='2.479.1')
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    assert '  To stage it: uc doctor --download-war required --since 2.479.1' in lines
    assert not [line for line in lines if 'Warning' in line]


def test_without_any_core_the_report_says_how_to_choose_a_floor(tmp_path, secrets_dir):
    report = DoctorReport(required_cores={'2.479.1': ['g:a:1'], '2.541.3': ['g:c:1']})
    text = format_report(report, make_settings(tmp_path, secrets_dir))
    assert 'No Jenkins core in the repository: plugins require 2 cores, from 2.479.1 to 2.541.3.' in text
    assert 'uc doctor --download-war required --since <oldest version your controllers run>' in text


def test_the_public_repository_is_not_checked_for_cores(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, MODE='online', REPO_URL='https://repo.jenkins-ci.org/', REPO_KEY='releases')
    report = run_doctor(settings, CREDS, fetch=False, versions=versions(), query=lambda *_args: plugin('git', '5.2.0', 'hpi', 'pom'))
    assert report.required_cores is None
    assert 'Cores plugins require' not in format_report(report, settings)


def test_inspect_repository_returns_the_listing_and_source_it_used(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    inspection = inspect_repository(settings, CREDS, fetch=False, versions=versions(), query=lambda *_args: plugin('git', '5.2.0', 'hpi', 'pom'))
    assert [g.artifact_id for g in inspection.listing.plugins] == ['git']
    assert isinstance(inspection.source, ArtifactSource)
    assert inspection.report.plugins == 1
    assert inspection.report.required_cores == {}
    assert inspection.report.cores_unchecked == 1  # no cache, no --fetch


def test_record_saves_the_report_and_its_counts(tmp_path):
    report = DoctorReport(plugins=3, cores=['2.462.1'], hpi_without_pom=['a'], required_cores={'2.479.1': ['x']}, missing_cores=[{'version': '2.479.1'}])
    path = record(report, tmp_path, dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC))
    assert path == tmp_path / 'doctor' / 'report.json'
    assert json.loads(path.read_text(encoding='utf-8'))['plugins'] == 3
    assert path.stat().st_mode & 0o777 == 0o644
    block = read_status(tmp_path)['doctor']
    assert (block['plugins'], block['hpi_without_pom'], block['missing_cores'], block['checked_at'], block['recorded_at']) == (3, 1, 1, '2026-10-03T05:30:00Z', '2026-10-03T05:30:00Z')


def test_missing_cores_is_null_when_cores_were_not_checked():
    assert DoctorReport().summary()['missing_cores'] is None


def test_summary_counts_the_releases_whose_manifest_was_unavailable_for_the_core_check():
    assert DoctorReport(required_cores={}, cores_unchecked=4).summary()['cores_unchecked'] == 4
    assert DoctorReport(required_cores={}).summary()['cores_unchecked'] == 0


def test_cores_unchecked_is_null_when_cores_were_not_checked():
    assert DoctorReport(cores_unchecked=0).summary()['cores_unchecked'] is None


def test_record_saves_cores_unchecked_in_the_doctor_block(tmp_path):
    record(DoctorReport(required_cores={}, cores_unchecked=2), tmp_path, dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC))
    assert read_status(tmp_path)['doctor']['cores_unchecked'] == 2
