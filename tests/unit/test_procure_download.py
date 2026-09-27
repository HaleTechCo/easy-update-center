"""Unit tests for uc.procure.download: one download run, from the report to the staging folder."""

import io
import json
from pathlib import Path

import pytest

from archivefixture import make_hpi, make_war, pom_xml
from procurefixture import publish
from uc.aql import classify
from uc.doctor import DoctorReport, Inspection
from uc.procure import ProcureError
from uc.procure.download import check_download_allowed, download
from uc.versions import VersionTool
from ucsupport import make_settings

PLUGINS = 'org.jenkins-ci.plugins'
AD = 'org.jenkins-ci.plugins:active-directory:2.967.v1'
AD_POM = 'org/jenkins-ci/plugins/active-directory/2.967.v1/active-directory-2.967.v1.pom'
MAILER = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1'
WAR = 'org/jenkins-ci/main/jenkins-war/2.479.1/jenkins-war-2.479.1'


class NoPoms:
    """A source with no POMs or manifests."""

    def pom(self, gav):
        """Return nothing for gav."""
        return {}.get(gav)

    def manifest(self, gav):
        """Return nothing for gav."""
        return {}.get(gav)


def versions():
    return VersionTool(Path('/opt/uc/generator'), Path('/opt/uc/tools'))


def inspection(report):
    return Inspection(report, classify([]), NoPoms())


def test_one_run_stages_poms_cores_and_dependencies(releases, tmp_path, secrets_dir):
    root, url = releases
    publish(root, AD_POM, pom_xml(PLUGINS, 'active-directory', '2.967.v1'))
    publish(root, WAR + '.war', make_war('2.479.1'))
    publish(root, WAR + '.pom', pom_xml('org.jenkins-ci.main', 'jenkins-war', '2.479.1'))
    publish(root, MAILER + '.hpi', make_hpi(PLUGINS, 'mailer', '534.v1', jenkins_version='2.541.3'))
    publish(root, MAILER + '.pom', pom_xml(PLUGINS, 'mailer', '534.v1'))
    report = DoctorReport(
        cores=['2.462.3'],
        repo_adapter={'derived_poms': [{'gav': AD, 'url': 'u'}]},
        missing_dependency_details=[{'plugin': 'active-directory', 'plugin_group': PLUGINS, 'plugin_version': '2.967.v1', 'dependency': 'mailer', 'required': '534.v1'}],
    )
    out = io.StringIO()
    settings = make_settings(tmp_path, secrets_dir, PROCUREMENT_SOURCE_URL=url)
    code = download(inspection(report), settings, poms=True, dependencies=True, wars=['2.479.1'], staging_dir=tmp_path / 'stage', versions=versions(), out=out)
    assert code == 0
    files = json.loads((tmp_path / 'stage' / 'procurement.json').read_text())['files']
    assert set(files) == {AD_POM, WAR + '.war', WAR + '.pom', MAILER + '.hpi', MAILER + '.pom'}
    lines = out.getvalue().splitlines()
    assert f'note      {MAILER}.hpi  (requires Jenkins 2.541.3, newer than 2.462.3, the newest core in the repository)' in lines
    assert lines[-1] == f'5 staged, 0 kept, 0 problem(s); review {tmp_path / "stage"}, then run uc doctor --push'


def test_a_failure_exits_1_and_the_rest_is_staged(releases, tmp_path, secrets_dir):
    root, url = releases
    publish(root, AD_POM, pom_xml(PLUGINS, 'active-directory', '2.967.v1'))
    report = DoctorReport(repo_adapter={'derived_poms': [{'gav': AD, 'url': 'u'}, {'gav': 'io.jenkins.plugins:gone:1.0', 'url': 'u'}]})
    settings = make_settings(tmp_path, secrets_dir, PROCUREMENT_SOURCE_URL=url)
    code = download(inspection(report), settings, poms=True, dependencies=False, wars=[], staging_dir=tmp_path / 'stage', versions=versions(), out=io.StringIO())
    assert code == 1
    assert list(json.loads((tmp_path / 'stage' / 'procurement.json').read_text())['files']) == [AD_POM]


def test_downloads_are_refused_air_gapped_without_a_source(tmp_path, secrets_dir):
    with pytest.raises(ProcureError, match='air-gapped mode cannot reach'):
        download(inspection(DoctorReport()), make_settings(tmp_path, secrets_dir), poms=True, dependencies=False, wars=[], staging_dir=tmp_path / 'stage', versions=versions())
    assert not (tmp_path / 'stage').exists()


def test_online_downloads_need_no_source_setting(tmp_path, secrets_dir):
    assert check_download_allowed(make_settings(tmp_path, secrets_dir, MODE='online')) is None
