"""Unit tests for uc.procure.local: recording files placed in the staging folder by hand."""

import hashlib
import io

import pytest

from archivefixture import make_hpi, make_war, pom_xml
from procurefixture import SOURCE, publish, stage
from uc.procure.local import stage_local
from uc.procure.staging import Staging

PLUGINS = 'org.jenkins-ci.plugins'
AD = 'org/jenkins-ci/plugins/active-directory/2.967.v1/active-directory-2.967.v1'
WAR = 'org/jenkins-ci/main/jenkins-war/2.479.1/jenkins-war-2.479.1'


def run(root):
    out = io.StringIO()
    return stage_local(root, SOURCE, out=out), out.getvalue().splitlines()


def summary(root, recorded, refused):
    return f'{recorded} recorded, {refused} refused; review {root}, then run uc doctor --push'


def test_files_placed_by_hand_are_recorded(tmp_path):
    hpi = make_hpi(PLUGINS, 'active-directory', '2.967.v1', jenkins_version='2.541.3')
    publish(tmp_path, AD + '.hpi', hpi)
    publish(tmp_path, AD + '.pom', pom_xml(PLUGINS, 'active-directory', '2.967.v1'))
    publish(tmp_path, WAR + '.war', make_war('2.479.1'))
    publish(tmp_path, WAR + '.pom', pom_xml('org.jenkins-ci.main', 'jenkins-war', '2.479.1'))
    code, lines = run(tmp_path)
    assert code == 0
    files = Staging.open(tmp_path, SOURCE).files
    assert set(files) == {AD + '.hpi', AD + '.pom', WAR + '.war', WAR + '.pom'}
    assert {(staged.kind, staged.reason, staged.url) for staged in files.values()} == {('local', 'added by hand', '')}
    assert files[AD + '.hpi'].sha1 == hashlib.sha1(hpi, usedforsecurity=False).hexdigest()
    assert files[AD + '.hpi'].size == len(hpi)
    assert files[AD + '.hpi'].required_core == '2.541.3'
    assert files[WAR + '.war'].required_core is None
    assert f'recorded  {AD}.hpi  (added by hand)' in lines
    assert lines[-1] == summary(tmp_path, 4, 0)


def test_files_already_recorded_are_left_alone(tmp_path):
    staging = Staging.open(tmp_path, SOURCE)
    stage(staging, AD + '.pom', pom_xml(PLUGINS, 'active-directory', '2.967.v1'))
    staging.save()
    code, lines = run(tmp_path)
    assert code == 0
    assert Staging.open(tmp_path, SOURCE).files[AD + '.pom'].kind == 'pom'
    assert lines == [summary(tmp_path, 0, 0)]


def test_a_plugin_under_the_wrong_group_is_refused(tmp_path):
    wrong = 'io/jenkins/plugins/active-directory/2.967.v1/active-directory-2.967.v1.hpi'
    publish(tmp_path, wrong, make_hpi(PLUGINS, 'active-directory', '2.967.v1'))
    code, lines = run(tmp_path)
    assert code == 1
    assert f'REFUSED   {wrong}  (its manifest names org.jenkins-ci.plugins:active-directory:2.967.v1: it belongs at {AD}.hpi)' in lines
    assert Staging.open(tmp_path, SOURCE).files == {}
    assert lines[-1] == summary(tmp_path, 0, 1)


def test_a_plugin_whose_manifest_names_no_coordinates_is_recorded_by_its_path(tmp_path):
    publish(tmp_path, AD + '.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1', manifest_group=None, manifest_artifact=None))
    code, _ = run(tmp_path)
    assert code == 0
    assert AD + '.hpi' in Staging.open(tmp_path, SOURCE).files


@pytest.mark.parametrize(
    ('path', 'data', 'detail'),
    [
        (AD + '.pom', b'<html><head><title>404 Not Found</title></head></html>', 'not a usable POM for its path: not a POM (root element <html>)'),
        (AD + '.pom', b'<html>\r\n<body>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n', 'not a usable POM for its path: not XML'),
        (AD + '.pom', pom_xml(PLUGINS, 'git', '5.2.0'), 'not a usable POM for its path: declares org.jenkins-ci.plugins:git:5.2.0'),
        (WAR + '.war', b'not a zip', 'not a readable archive'),
        (AD + '.hpi', b'not a zip either', 'not a readable archive'),
        ('org/x/y-1.hpi', b'x', 'not a Maven path'),
    ],
)
def test_files_that_fail_the_download_checks_are_refused(tmp_path, path, data, detail):
    publish(tmp_path, path, data)
    code, lines = run(tmp_path)
    assert code == 1
    assert [line for line in lines if line.startswith(f'REFUSED   {path}  ({detail}')], lines
    assert Staging.open(tmp_path, SOURCE).files == {}


def test_a_pom_over_the_size_limit_is_refused(tmp_path):
    publish(tmp_path, AD + '.pom', b'<' * ((1 << 20) + 1))
    code, lines = run(tmp_path)
    assert code == 1
    assert [line for line in lines if line.startswith(f'REFUSED   {AD}.pom  (larger than 1048576 bytes')], lines


def test_other_files_are_not_touched(tmp_path):
    (tmp_path / 'notes.txt').write_text('reviewed')
    (tmp_path / '.part-abc123').write_bytes(b'')
    code, lines = run(tmp_path)
    assert code == 0
    assert lines == [summary(tmp_path, 0, 0)]


def test_one_refusal_does_not_stop_the_rest(tmp_path):
    publish(tmp_path, AD + '.pom', b'<html>404</html>')
    publish(tmp_path, WAR + '.war', make_war('2.479.1'))
    code, lines = run(tmp_path)
    assert code == 1
    assert set(Staging.open(tmp_path, SOURCE).files) == {WAR + '.war'}
    assert lines[-1] == summary(tmp_path, 1, 1)
