"""Unit tests for uc.doctor.

Manifest and dependency parsing, repository analysis and the report.
"""

import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import queue
import stat
import threading
from pathlib import Path

import pytest

from httpstub import StubServer
from uc import status
from uc.aql import Gav, RepoFile, classify
from uc.doctor import (
    RECORD_LOCK_FILE,
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
    record_locked,
    required_cores,
)
from uc.errors import UsageError
from uc.model import Credentials
from uc.status import read_status
from uc.versions import VersionTool
from ucsupport import make_settings

CREDS = Credentials('uc', 'secret', 'uc', 'uc', github_placeholder=True)
GIT = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')


def versions():
    return VersionTool(Path('/opt/uc/generator'), Path('/opt/uc/tools'))


def plugin(artifact, version, *extensions, group='org/jenkins-ci/plugins'):
    return [
        RepoFile(f'{group}/{artifact}/{version}', f'{artifact}-{version}.{ext}')
        for ext in extensions
    ]


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
    text = (
        'Manifest-Version: 1.0\r\nShort-Name: git\r\n'
        'Plugin-Dependencies: credentials:2.6.1,scm-api:600.v\r\n'
        ' 1;resolution:=optional\r\n\r\n'
        'Name: extra\r\nShort-Name: other\r\n'
    )
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
    assert not is_stub_pom(
        b'<project xmlns="http://maven.apache.org/POM/4.0.0">'
        b'<name>Git plugin</name></project>'
    )
    assert is_stub_pom(b'<project><artifactId>stub</artifactId></project>')
    assert is_stub_pom(b'<project><name>  </name></project>')
    assert is_stub_pom(b'not xml')
    assert is_stub_pom(
        b'<!DOCTYPE p [<!ENTITY x "y">]><project><name>&x;</name></project>'
    )


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
        poms={
            'git': b'<project><name>Git</name></project>',
            'credentials': b'<project><name>C</name></project>',
            'stub': b'<project/>',
        },
        manifests={
            'git': (
                'Plugin-Dependencies: credentials:2.6.1,'
                'scm-api:1;resolution:=optional,workflow-step-api:1.0\n'
            ),
            'stub': 'Short-Name: stub\n',
        },
    )
    report = analyze(listing, versions(), source)
    assert report.plugins == 3
    assert report.cores == ['2.528.1']
    assert report.hpi_without_pom == [
        'org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi'
    ]
    assert report.war_without_pom == [
        'org/jenkins-ci/main/jenkins-war/2.540/jenkins-war-2.540.war'
    ]
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
    source = ArtifactSource(
        'http://artifactory:8081/artifactory/',
        'jenkins',
        'uc',
        'secret',
        cache_dir=tmp_path / 'cache',
        allow_network=False,
    )
    manifest_path = (
        '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi'
        '!/META-INF/MANIFEST.MF'
    )
    digest = hashlib.sha256(manifest_path.encode()).hexdigest()
    cached = tmp_path / 'cache' / digest[:2] / digest
    cached.parent.mkdir(parents=True)
    cached.write_text('Short-Name: git\n')
    assert source.cache_file('http://artifactory:8081' + manifest_path) == cached
    assert source.manifest(GIT) == 'Short-Name: git\n'
    assert source.pom(GIT) is None  # not cached and network not allowed


def test_a_failed_download_update_center2_cached_is_not_fetched_again(tmp_path):
    # update-center2 records a failed download as a directory in its cache
    pom_path = '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    digest = hashlib.sha256(pom_path.encode()).hexdigest()
    (tmp_path / 'cache' / digest[:2] / digest).mkdir(parents=True)
    with StubServer({('GET', pom_path): (200, {}, b'<project/>')}) as stub:
        source = ArtifactSource(
            stub.url + '/artifactory/',
            'jenkins',
            'uc',
            'secret',
            cache_dir=tmp_path / 'cache',
            allow_network=True,
        )
        assert source.pom(GIT) is None
    assert stub.requests == []


def test_artifact_source_fetches_with_basic_auth():
    routes = {
        (
            'GET',
            '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom',
        ): (200, {}, b'<project/>')
    }
    with StubServer(routes) as stub:
        source = ArtifactSource(
            stub.url + '/artifactory/',
            'jenkins',
            'uc',
            'secret',
            cache_dir=None,
            allow_network=True,
        )
        assert source.pom(GIT) == b'<project/>'
        assert source.manifest(GIT) is None  # 404
    assert (
        stub.requests[0][2]['Authorization']
        == 'Basic ' + base64.b64encode(b'uc:secret').decode()
    )


def test_inspect_repository_includes_live_tiers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    release = settings.data_dir / 'releases' / '20260922T000000Z'
    release.mkdir(parents=True)
    (release / 'tiers.json').write_text(
        '{"weeklyCores": [], "stableCores": ["2.528.1"]}'
    )
    (settings.data_dir / 'live').symlink_to('releases/20260922T000000Z')
    files = plugin('git', '5.2.0', 'hpi', 'pom') + plugin(
        'jenkins-war', '2.528.1', 'war', 'pom', group='org/jenkins-ci/main'
    )
    report = inspect_repository(
        settings,
        CREDS,
        fetch=False,
        versions=VersionTool.from_settings(settings),
        query=lambda *_args: files,
    ).report
    assert report.tiers == {'weeklyCores': [], 'stableCores': ['2.528.1']}
    assert report.unchecked == 1
    assert report.exit_code() == 0


@pytest.mark.parametrize('content', ['[]', '"weekly"', 'null', '{bad'])
def test_inspect_repository_ignores_a_tiers_file_that_is_not_an_object(
    tmp_path, secrets_dir, content
):
    settings = make_settings(tmp_path, secrets_dir)
    release = settings.data_dir / 'releases' / '20260922T000000Z'
    release.mkdir(parents=True)
    (release / 'tiers.json').write_text(content)
    (settings.data_dir / 'live').symlink_to('releases/20260922T000000Z')
    report = inspect_repository(
        settings,
        CREDS,
        fetch=False,
        versions=VersionTool.from_settings(settings),
        query=lambda *_args: plugin('git', '5.2.0', 'hpi', 'pom'),
    ).report
    assert report.tiers is None


ADAPTER_REPORT = {
    'derived_poms': [
        {
            'gav': 'io.jenkins.plugins:caffeine-api:3.2.4',
            'url': 'https://repo.jenkins-ci.org/releases/io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4.pom',
        }
    ],
    'replaced_poms': [
        {
            'path': 'org/x/ad/1/ad-1.pom',
            'reason': 'not XML',
            'url': 'https://repo.jenkins-ci.org/releases/org/x/ad/1/ad-1.pom',
        }
    ],
    'needs_pom': [
        {
            'gav': 'org.jenkins-ci.plugins:polaris:1.4.0',
            'url': 'https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/polaris/1.4.0/polaris-1.4.0.pom',
        }
    ],
    'misplaced': [
        {
            'path': 'org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi',
            'correct_path': 'io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi',
        }
    ],
    'skipped': [{'path': 'org/x/big/1/big-1.hpi', 'reason': 'larger than 512 MiB'}],
    'dates_from_repository': [
        {
            'path': 'org/x/cas/1/cas-1.hpi',
            'reason': 'archive date 1980-02-01 is before 2004',
        }
    ],
}


def test_the_adapter_report_adds_sections_and_counts_its_drops(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_report_file.write_text(
        json.dumps({'archives': 5, **ADAPTER_REPORT})
    )
    listing = classify(
        plugin('polaris', '1.4.0', 'hpi') + plugin('git', '5.2.0', 'hpi', 'pom')
    )
    report = attach_adapter_report(
        analyze(listing, versions(), FakeSource({}, {})), settings
    )
    assert (
        report.dropped == 3
    )  # polaris (no POM at all), the misplaced and the skipped archive
    assert report.summary()['repo_adapter'] == dict.fromkeys(ADAPTER_REPORT, 1)
    text = format_report(report, settings)
    assert '(through repo-adapter)' in text
    assert 'procure the real .pom (1):' in text
    assert ADAPTER_REPORT['needs_pom'][0]['url'] in text
    assert (
        '-> ' + 'io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi'
        in text.replace('  ->  ', ' -> ')
    )
    lines = text.splitlines()
    assert 'POMs filled in from archives; the real .pom is optional (1):' in lines
    assert (
        '  io.jenkins.plugins:caffeine-api:3.2.4  https://repo.jenkins-ci.org/releases/io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4.pom'
        in lines
    )
    assert '.pom files ignored (not usable); replace with the real .pom (1):' in lines
    assert (
        '  org/x/ad/1/ad-1.pom: not XML  https://repo.jenkins-ci.org/releases/org/x/ad/1/ad-1.pom'
        in lines
    )
    assert 'archive date 1980-02-01 is before 2004' in text


def test_a_malformed_adapter_report_keeps_only_well_formed_sections(
    tmp_path, secrets_dir
):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    settings.repo_adapter_dir.mkdir(parents=True)
    valid = ADAPTER_REPORT['dates_from_repository']
    settings.repo_adapter_report_file.write_text(
        json.dumps(
            {'skipped': 5, 'derived_poms': [{'gav': 1}], 'dropped': valid}
            | {'dates_from_repository': valid}
        )
    )
    report = attach_adapter_report(
        analyze(classify([]), versions(), FakeSource({}, {})), settings
    )
    counts = report.summary()['repo_adapter']
    assert counts['skipped'] == counts['derived_poms'] == 0
    assert counts['dates_from_repository'] == 1
    text = format_report(report, settings)
    assert 'archive date 1980-02-01 is before 2004' in text


@pytest.mark.parametrize('gav', ['a:b', 'a:b:c:d', 'no-colons', ''])
@pytest.mark.parametrize('section', ['derived_poms', 'needs_pom'])
def test_an_adapter_report_with_a_malformed_gav_is_ignored(
    tmp_path, secrets_dir, section, gav
):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_report_file.write_text(
        json.dumps({section: [{'gav': gav, 'url': 'https://x/y.pom'}]})
    )
    report = attach_adapter_report(
        analyze(classify([]), versions(), FakeSource({}, {})), settings
    )
    assert report.summary()['repo_adapter'][section] == 0


def test_without_the_adapter_there_is_no_adapter_report(tmp_path, secrets_dir):
    report = attach_adapter_report(
        analyze(classify([]), versions(), FakeSource({}, {})),
        make_settings(tmp_path, secrets_dir),
    )
    assert report.repo_adapter is None
    assert report.summary()['repo_adapter'] is None


def test_format_report(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    listing = classify(
        plugin('orphan', '1.0', 'hpi') + plugin('git', '5.2.0', 'hpi', 'pom')
    )
    text = format_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert (
        'Source: artifactory repository jenkins at http://artifactory:8081/artifactory/'
        in text
    )
    assert 'through repo-adapter' not in text
    assert 'Jenkins cores with a POM: none' in text
    assert 'Dropped by the generator: 1' in text
    assert 'org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi' in text
    assert 'Not checked (POM or manifest unavailable; run with --fetch): 1' in text
    fetched = format_report(
        analyze(listing, versions(), FakeSource({}, {})), settings, fetch=True
    )
    assert 'Not checked (POM or manifest unavailable from the repository): 1' in fetched
    assert '--fetch' not in fetched


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
    listing = classify(
        plugin('ad', '2.0', 'hpi', 'pom') + plugin('mailer', '400.v1', 'hpi', 'pom')
    )
    manifests = {
        'ad': (
            'Manifest-Version: 1.0\r\nPlugin-Dependencies: '
            'mailer:534.v1,ionicons-api:94.v1,matrix:3.0;resolution:=optional\r\n\r\n'
        ),
        'mailer': 'Manifest-Version: 1.0\r\n\r\n',
    }
    report = analyze(listing, versions(), FakeSource({}, manifests))
    assert report.missing_dependency_details == [
        {
            'plugin': 'ad',
            'plugin_group': 'org.jenkins-ci.plugins',
            'plugin_version': '2.0',
            'dependency': 'ionicons-api',
            'required': '94.v1',
        },
        {
            'plugin': 'ad',
            'plugin_group': 'org.jenkins-ci.plugins',
            'plugin_version': '2.0',
            'dependency': 'mailer',
            'required': '534.v1',
        },
    ]
    assert report.missing_dependencies == [
        'ad:2.0 requires ionicons-api 94.v1 (not in the repository)',
        'ad:2.0 requires mailer >= 534.v1 (newest available: 400.v1)',
    ]


def test_required_cores_cover_every_release_once():
    listing = classify(
        plugin('ad', '1.0', 'hpi', 'jpi', 'pom')
        + plugin('ad', '2.0', 'hpi', 'pom')
        + plugin('old', '1.0', 'hpi', 'pom')
        + plugin('gone', '1.0', 'hpi', 'pom')
        + plugin('odd', '1.0', 'hpi', 'pom')
    )
    source = ReleaseSource(
        {
            ('ad', '1.0'): core_manifest('2.479.1'),
            ('ad', '2.0'): core_manifest('2.541.3'),
            ('old', '1.0'): core_manifest('1.625.3', key='Hudson-Version'),
            ('odd', '1.0'): core_manifest('2.60-SNAPSHOT'),
        }
    )
    required, unchecked = required_cores(listing, source)
    assert required == {
        '2.479.1': ['org.jenkins-ci.plugins:ad:1.0'],
        '2.541.3': ['org.jenkins-ci.plugins:ad:2.0'],
        '1.625.3': ['org.jenkins-ci.plugins:old:1.0'],
    }
    assert unchecked == 1  # gone: no manifest


REQUIRED = {
    '1.625.3': ['g:old:1'],
    '2.479.1': ['g:a:1', 'g:b:1'],
    '2.541.3': ['g:c:1'],
    '2.555.1': ['g:d:1'],
}


def test_missing_cores_start_at_the_oldest_stocked_core():
    floor, missing, below, oldest = find_missing_cores(
        REQUIRED, ['2.479.1', '2.528.3'], None, versions()
    )
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
    floor, missing, below, oldest = find_missing_cores(
        REQUIRED, [], '2.500.1', versions()
    )
    assert floor == '2.500.1'
    assert [core['version'] for core in missing] == ['2.541.3', '2.555.1']
    assert (below, oldest) == (2, '1.625.3')


def test_without_a_core_or_since_there_is_no_floor():
    assert find_missing_cores(REQUIRED, [], None, versions()) == (None, [], 0, None)


@pytest.mark.parametrize(
    'since', ['2.x', 'latest', '2.462.1\n2.479.1', '2.462.1\n', '']
)
def test_since_must_be_a_jenkins_version(since):
    with pytest.raises(UsageError, match='--since takes a Jenkins version'):
        check_since(since)


def test_the_core_check_fills_the_report():
    listing = classify(
        plugin('ad', '2.0', 'hpi', 'pom')
        + plugin('jenkins-war', '2.462.3', 'war', 'pom', group='org/jenkins-ci/main')
    )
    report = analyze(listing, versions(), ReleaseSource({}))
    attach_core_check(
        report,
        listing,
        ReleaseSource({('ad', '2.0'): core_manifest('2.479.1')}),
        versions(),
        since=None,
    )
    assert report.required_cores == {'2.479.1': ['org.jenkins-ci.plugins:ad:2.0']}
    assert report.core_floor == '2.462.3'
    assert [core['version'] for core in report.missing_cores] == ['2.479.1']


def test_the_report_lists_missing_cores(tmp_path, secrets_dir):
    report = DoctorReport(
        cores=['2.479.1'],
        required_cores={'2.479.1': ['g:a:1'], '2.541.3': ['g:c:1']},
        core_floor='2.479.1',
        missing_cores=[
            {'version': '2.541.3', 'url': 'U', 'required_by': 1, 'example': 'g:c:1'}
        ],
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    assert (
        'Cores plugins require that the repository lacks, at or above 2.479.1 (1):'
        in lines
    )
    assert '  2.541.3  U  (required by 1 release, e.g. g:c:1)' in lines
    assert not [line for line in lines if 'older required core' in line]


WARNING = (
    '  Warning: once pushed, 2.361.4 is the oldest core in the repository, and so the '
    'floor: every later uc procure stage --war required also stages the missing cores '
    'plugins require from 2.361.4 up, for plugins you add later too'
)


def test_the_report_names_the_older_core_it_leaves_out_and_how_to_stage_it(
    tmp_path, secrets_dir
):
    report = DoctorReport(
        cores=['2.462.1', '2.479.1'],
        required_cores={'2.361.4': ['g:j:1']},
        core_floor='2.462.1',
        missing_cores_below_floor=1,
        oldest_core_below_floor='2.361.4',
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    start = lines.index('1 older required core below 2.462.1 not listed: 2.361.4')
    assert lines[start + 1 : start + 3] == [
        '  To stage it: uc procure stage --war required --since 2.361.4',
        WARNING,
    ]


def test_several_older_cores_are_staged_from_the_oldest(tmp_path, secrets_dir):
    report = DoctorReport(
        cores=['2.462.1'],
        required_cores={'2.361.4': ['g:j:1'], '2.401.3': ['g:k:1']},
        core_floor='2.462.1',
        missing_cores_below_floor=2,
        oldest_core_below_floor='2.361.4',
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    start = lines.index(
        '2 older required cores below 2.462.1 not listed, the oldest 2.361.4'
    )
    assert lines[start + 1 : start + 3] == [
        (
            '  To stage them: uc procure stage --war required --since 2.361.4 '
            '(a later version stages only those from it up)'
        ),
        WARNING,
    ]


def test_a_floor_set_with_since_gets_no_floor_warning(tmp_path, secrets_dir):
    report = DoctorReport(
        cores=['2.462.1'],
        required_cores={'2.479.1': ['g:a:1']},
        core_floor='2.500.1',
        missing_cores_below_floor=1,
        oldest_core_below_floor='2.479.1',
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    assert '  To stage it: uc procure stage --war required --since 2.479.1' in lines
    assert not [line for line in lines if 'Warning' in line]


def test_without_any_core_the_report_says_how_to_choose_a_floor(tmp_path, secrets_dir):
    report = DoctorReport(required_cores={'2.479.1': ['g:a:1'], '2.541.3': ['g:c:1']})
    text = format_report(report, make_settings(tmp_path, secrets_dir))
    assert (
        'No Jenkins core in the repository: plugins require 2 cores, '
        'from 2.479.1 to 2.541.3.'
    ) in text
    assert (
        'uc procure stage --war required --since <oldest version your controllers run>'
        in text
    )


def test_the_public_repository_is_not_checked_for_cores(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path,
        secrets_dir,
        MODE='online',
        REPO_URL='https://repo.jenkins-ci.org/',
        REPO_KEY='releases',
    )
    report = inspect_repository(
        settings,
        CREDS,
        fetch=False,
        versions=versions(),
        query=lambda *_args: plugin('git', '5.2.0', 'hpi', 'pom'),
    ).report
    assert report.required_cores is None
    assert report.cores_unchecked == 0
    assert 'Required cores not checked' not in format_report(report, settings)


def test_inspect_repository_returns_the_listing_and_source_it_used(
    tmp_path, secrets_dir
):
    settings = make_settings(tmp_path, secrets_dir)
    inspection = inspect_repository(
        settings,
        CREDS,
        fetch=False,
        versions=versions(),
        query=lambda *_args: plugin('git', '5.2.0', 'hpi', 'pom'),
    )
    assert [g.artifact_id for g in inspection.listing.plugins] == ['git']
    assert isinstance(inspection.source, ArtifactSource)
    assert inspection.report.plugins == 1
    assert inspection.report.required_cores == {}
    assert inspection.report.cores_unchecked == 1  # no cache, no --fetch


def test_record_saves_the_report_and_its_counts(tmp_path):
    report = DoctorReport(
        plugins=3,
        cores=['2.462.1'],
        hpi_without_pom=['a'],
        required_cores={'2.479.1': ['x']},
        missing_cores=[{'version': '2.479.1'}],
    )
    path = record(report, tmp_path, dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC))
    assert path == tmp_path / 'doctor' / 'report.json'
    assert json.loads(path.read_text(encoding='utf-8'))['plugins'] == 3
    assert path.stat().st_mode & 0o777 == 0o644
    block = read_status(tmp_path)['doctor']
    assert (
        block['plugins'],
        block['hpi_without_pom'],
        block['missing_cores'],
        block['checked_at'],
        block['recorded_at'],
    ) == (3, 1, 1, '2026-10-03T05:30:00Z', '2026-10-03T05:30:00Z')


def test_missing_cores_is_null_when_cores_were_not_checked():
    assert DoctorReport().summary()['missing_cores'] is None


def test_summary_counts_releases_whose_manifest_was_unavailable_for_the_core_check():
    assert (
        DoctorReport(required_cores={}, cores_unchecked=4).summary()['cores_unchecked']
        == 4
    )
    assert DoctorReport(required_cores={}).summary()['cores_unchecked'] == 0


def test_cores_unchecked_is_null_when_cores_were_not_checked():
    assert DoctorReport(cores_unchecked=0).summary()['cores_unchecked'] is None


def test_record_saves_cores_unchecked_in_the_doctor_block(tmp_path):
    record(
        DoctorReport(required_cores={}, cores_unchecked=2),
        tmp_path,
        dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC),
    )
    assert read_status(tmp_path)['doctor']['cores_unchecked'] == 2


def share_at_once(path):
    """Take and drop a shared lock on path through a separate open.

    As another process would; BlockingIOError while an exclusive lock is held.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_the_record_lock_is_exclusive_until_released(tmp_path):
    with record_locked(tmp_path), pytest.raises(BlockingIOError):
        share_at_once(
            tmp_path / RECORD_LOCK_FILE
        )  # only an exclusive lock refuses a shared one
    share_at_once(tmp_path / RECORD_LOCK_FILE)


def test_overlapping_records_take_turns_and_leave_the_last_report_with_its_counts(
    tmp_path, monkeypatch
):
    now = dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC)
    lock = tmp_path / RECORD_LOCK_FILE
    flock = fcntl.flock
    asked = queue.Queue()
    finished = []

    def watched_flock(fd, operation):
        asked.put((os.path.samestat(os.fstat(fd), lock.stat()), operation))
        flock(fd, operation)

    update_status = status.update_status
    held = []

    def probing_update_status(*args, **kwargs):
        # as another process would: a shared lock is refused while this one is held
        try:
            share_at_once(lock)
        except BlockingIOError:
            held.append(True)
        else:
            held.append(False)
        return update_status(*args, **kwargs)

    monkeypatch.setattr(status, 'update_status', probing_update_status)

    def run(report):
        record(report, tmp_path, now)
        finished.append(report.plugins)

    with record_locked(tmp_path):
        monkeypatch.setattr(fcntl, 'flock', watched_flock)
        threads = [
            threading.Thread(target=run, args=(DoctorReport(plugins=plugins),))
            for plugins in (1, 2)
        ]
        for thread in threads:
            thread.start()
        # Each recorder has asked for the lock this test holds, blocking and exclusive,
        # so it waits: no timing involved.
        assert [asked.get(timeout=30) for _ in threads] == [(True, fcntl.LOCK_EX)] * 2
        assert all(thread.is_alive() for thread in threads)
        assert not (tmp_path / 'doctor' / 'report.json').exists()
    for thread in threads:
        thread.join(30)
    assert sorted(finished) == [1, 2]
    assert held == [True, True]  # the status block is written under the lock
    saved = json.loads(
        (tmp_path / 'doctor' / 'report.json').read_text(encoding='utf-8')
    )
    assert read_status(tmp_path)['doctor']['plugins'] == saved['plugins']
    assert sorted(path.name for path in (tmp_path / 'doctor').iterdir()) == [
        '.record.lock',
        'report.json',
    ]


def test_record_renames_a_temporary_file_from_beside_report_json(tmp_path, monkeypatch):
    replace = Path.replace
    renames = []

    def watched_replace(self, target):
        renames.append((self, Path(target), stat.S_IMODE(self.stat().st_mode)))
        return replace(self, target)

    monkeypatch.setattr(Path, 'replace', watched_replace)
    path = record(
        DoctorReport(plugins=3),
        tmp_path,
        dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC),
    )
    [(temporary, mode)] = [
        (source, mode) for source, target, mode in renames if target == path
    ]
    assert (
        temporary.parent == path.parent
    )  # the same directory, so the rename is atomic
    assert temporary.name.startswith('.report.')
    assert temporary.name.endswith('.tmp')
    assert mode == 0o644  # readable before it is report.json


def test_a_failed_record_removes_its_temporary_file(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        msg = 'rename blew up'
        raise OSError(msg)

    monkeypatch.setattr(Path, 'replace', boom)
    with pytest.raises(OSError, match='rename blew up'):
        record(DoctorReport(), tmp_path, dt.datetime(2026, 10, 3, 5, 30, tzinfo=dt.UTC))
    assert sorted(path.name for path in (tmp_path / 'doctor').iterdir()) == [
        '.record.lock'
    ]


def test_a_doctor_fetch_keeps_the_credentials_off_another_host():
    gav = Gav('org.x', 'x', '1', 'hpi')
    with StubServer({('GET', '/blob'): (200, {}, b'<project/>')}) as other:
        elsewhere = other.url.replace('127.0.0.1', 'localhost') + '/blob'
        with StubServer(
            {('GET', '/jenkins/org/x/x/1/x-1.pom'): (302, {'Location': elsewhere}, b'')}
        ) as repository:
            source = ArtifactSource(
                repository.url + '/',
                'jenkins',
                'svc',
                'pw',
                cache_dir=None,
                allow_network=True,
            )
            assert source.pom(gav) == b'<project/>'
    _method, _path, headers, _body = other.requests[0]
    assert 'Authorization' not in headers


@pytest.mark.parametrize(('adapter', 'expected'), [('on', None), ('off', 'ca')])
def test_the_doctor_gives_the_repository_ca_only_without_the_adapter(
    tmp_path, secrets_dir, adapter, expected
):
    ca = tmp_path / 'corp-ca.pem'
    settings = make_settings(
        tmp_path, secrets_dir, REPO_ADAPTER=adapter, REPO_CA_FILE=str(ca)
    )
    queried = []

    def query(*args):
        queried.append(args)
        return plugin('git', '5.2.0', 'hpi', 'pom')

    inspection = inspect_repository(
        settings, CREDS, fetch=False, versions=versions(), query=query
    )
    want = ca if expected else None
    assert queried[0][4] == want  # the query's ca_file argument
    assert inspection.source.ca_file == want
    assert (
        ArtifactSource.from_settings(settings, CREDS, allow_network=False).ca_file
        == want
    )


def test_format_report_prints_each_section_of_the_source_with_its_count(
    tmp_path, secrets_dir
):
    report = DoctorReport(
        plugins=2,
        cores=['2.528.1'],
        hpi_without_pom=['a/orphan-1.0.hpi'],
        war_without_pom=['b/jenkins-war-2.540.war'],
        non_maven=['plugins/x/1.0/x.hpi'],
        stub_poms=['stub:1.0'],
        missing_dependencies=['git:5.10.0 requires nope 1.0 (not in the repository)'],
        tiers={'weeklyCores': ['2.540'], 'stableCores': ['2.528.1']},
    )
    lines = format_report(report, make_settings(tmp_path, secrets_dir)).splitlines()
    assert lines == [
        'Source: artifactory repository jenkins at http://artifactory:8081/artifactory/',
        'Plugins with a POM: 2',
        'Jenkins cores with a POM: 2.528.1',
        "Tiers in the live release: weekly ['2.540'], stable ['2.528.1']",
        '',
        'Dropped by the generator: 3',
        '  .hpi/.jpi without a .pom (1):',
        '    a/orphan-1.0.hpi',
        '  jenkins-war without a .pom (1):',
        '    b/jenkins-war-2.540.war',
        '  not in Maven layout (1):',
        '    plugins/x/1.0/x.hpi',
        'Stub POMs (degraded names, descriptions, SCM links) (1):',
        '  stub:1.0',
        'Dependency problems (1):',
        '  git:5.10.0 requires nope 1.0 (not in the repository)',
    ]


def test_format_report_says_when_nothing_is_dropped_and_omits_empty_sections(
    tmp_path, secrets_dir
):
    text = format_report(DoctorReport(plugins=1), make_settings(tmp_path, secrets_dir))
    assert 'Nothing in the source is dropped by the generator.' in text
    for absent in ('Dropped by', 'Stub POMs', 'Dependency problems', 'Not checked'):
        assert absent not in text


def test_format_report_lists_what_the_adapter_dropped_instead_of_the_plain_sections(
    tmp_path, secrets_dir
):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    report = DoctorReport(
        hpi_without_pom=['a/orphan-1.0.hpi'],
        repo_adapter={
            'derived_poms': [],
            'replaced_poms': [],
            'needs_pom': [],
            'misplaced': [{'path': 'bad/x-1.hpi', 'correct_path': 'good/x-1.hpi'}],
            'skipped': [{'path': 'odd/y-1.hpi', 'reason': 'no manifest'}],
            'dates_from_repository': [{'path': 'z/z-1.hpi', 'reason': 'no date'}],
        },
    )
    lines = format_report(report, settings).splitlines()
    assert 'Dropped by the generator: 3' in lines
    assert '  misplaced: move to the path its manifest names (1):' in lines
    assert '    bad/x-1.hpi  ->  good/x-1.hpi' in lines
    assert '  skipped (1):' in lines
    assert '    odd/y-1.hpi: no manifest' in lines
    assert 'dates from the repository (no usable archive date) (1):' in lines
    assert '  z/z-1.hpi: no date' in lines
    # the adapter reports archives without a POM itself: the plain list is not shown
    assert '.hpi/.jpi without a .pom' not in '\n'.join(lines)


def test_a_fetch_that_cannot_reach_the_repository_is_a_doctor_error():
    with StubServer({}) as stub:
        url = stub.url + '/artifactory/'
    source = ArtifactSource(
        url, 'jenkins', 'uc', 'secret', cache_dir=None, allow_network=True, timeout=2
    )
    with pytest.raises(DoctorError):
        source.pom(GIT)


@pytest.mark.parametrize('status_code', [401, 403, 500])
def test_a_fetch_answered_with_an_error_status_is_a_doctor_error(status_code):
    path = '/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    with StubServer({('GET', path): (status_code, {}, b'no')}) as stub:
        source = ArtifactSource(
            stub.url + '/artifactory/',
            'jenkins',
            'uc',
            'secret',
            cache_dir=None,
            allow_network=True,
        )
        with pytest.raises(DoctorError, match=f'failed with HTTP {status_code}'):
            source.pom(GIT)


def test_a_source_that_may_not_use_the_network_never_fetches(tmp_path):
    with StubServer({}) as stub:
        source = ArtifactSource(
            stub.url + '/artifactory/',
            'jenkins',
            'uc',
            'secret',
            cache_dir=tmp_path / 'cache',
            allow_network=False,
        )
        assert source.pom(GIT) is None
        assert source.manifest(GIT) is None
    assert stub.requests == []
