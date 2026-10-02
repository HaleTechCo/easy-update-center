"""Unit tests for uc.procure.local: recording files placed in the staging folder by hand."""

import hashlib
import io
import zipfile

import pytest

from archivefixture import make_hpi, make_war, pom_xml
from procurefixture import SOURCE, publish, stage
from uc.procure.local import stage_local
from uc.procure.staging import Staging

PLUGINS = 'org.jenkins-ci.plugins'
AD = 'org/jenkins-ci/plugins/active-directory/2.967.v1/active-directory-2.967.v1'
WAR = 'org/jenkins-ci/main/jenkins-war/2.479.1/jenkins-war-2.479.1'
FIX = '; --fix moves it there'


def run(root, *, fix=False):
    out = io.StringIO()
    return stage_local(root, SOURCE, fix=fix, out=out), out.getvalue().splitlines()


def summary(root, recorded, refused, moved=None):
    return ('' if moved is None else f'{moved} moved, ') + f'{recorded} recorded, {refused} refused; review {root}, then run uc doctor --push'


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
    assert f'REFUSED   {wrong}  (its manifest names org.jenkins-ci.plugins:active-directory:2.967.v1: it belongs at {AD}.hpi{FIX})' in lines
    assert Staging.open(tmp_path, SOURCE).files == {}
    assert lines[-1] == summary(tmp_path, 0, 1)


def test_a_plugin_whose_manifest_names_no_coordinates_is_recorded_by_its_path(tmp_path):
    publish(tmp_path, AD + '.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1', manifest_group=None, manifest_artifact=None))
    code, _ = run(tmp_path)
    assert code == 0
    assert AD + '.hpi' in Staging.open(tmp_path, SOURCE).files


def test_a_core_whose_manifest_names_another_version_is_refused(tmp_path):
    publish(tmp_path, WAR + '.war', make_war('2.479.2'))
    code, lines = run(tmp_path)
    assert code == 1
    other = 'org/jenkins-ci/main/jenkins-war/2.479.2/jenkins-war-2.479.2.war'
    assert f'REFUSED   {WAR}.war  (its manifest names org.jenkins-ci.main:jenkins-war:2.479.2: it belongs at {other}{FIX})' in lines


def test_a_core_whose_manifest_names_no_version_is_recorded_by_its_path(tmp_path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as war:
        war.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nMain-Class: executable.Main\r\n\r\n')
    publish(tmp_path, WAR + '.war', archive.getvalue())
    code, _ = run(tmp_path)
    assert code == 0
    assert WAR + '.war' in Staging.open(tmp_path, SOURCE).files


@pytest.mark.parametrize(
    ('path', 'data', 'detail'),
    [
        (AD + '.pom', b'<html><head><title>404 Not Found</title></head></html>', 'not a usable POM for its path: not a POM (root element <html>)'),
        (AD + '.pom', b'<html>\r\n<body>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n', 'not a usable POM for its path: not XML'),
        (AD + '.pom', pom_xml(PLUGINS, 'git', '5.2.0'), f'its POM names {PLUGINS}:git:5.2.0: it belongs at org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom{FIX}'),
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


@pytest.mark.parametrize(
    ('name', 'data', 'detail'),
    [
        ('active-directory-2.967.v1.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1'), f'its manifest names {PLUGINS}:active-directory:2.967.v1: it belongs at {AD}.hpi'),
        ('jenkins.war', make_war('2.479.1'), f'its manifest names org.jenkins-ci.main:jenkins-war:2.479.1: it belongs at {WAR}.war'),
        ('active-directory.pom', pom_xml(PLUGINS, 'active-directory', '2.967.v1'), f'its POM names {PLUGINS}:active-directory:2.967.v1: it belongs at {AD}.pom'),
    ],
    ids=['plugin', 'core', 'pom'],
)
def test_a_file_outside_the_maven_layout_is_told_where_it_belongs(tmp_path, name, data, detail):
    publish(tmp_path, f'downloads/{name}', data)
    code, lines = run(tmp_path)
    assert code == 1
    assert f'REFUSED   downloads/{name}  (not at a Maven path; {detail}{FIX})' in lines
    assert Staging.open(tmp_path, SOURCE).files == {}


@pytest.mark.parametrize('fix', [False, True])
def test_a_plugin_whose_manifest_names_no_group_cannot_be_placed_for_you(tmp_path, fix):
    publish(tmp_path, 'active-directory.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1', manifest_group=None))
    code, lines = run(tmp_path, fix=fix)
    assert code == 1
    assert "REFUSED   active-directory.hpi  (not a Maven path to a .hpi, .jpi, .war or .pom: 'active-directory.hpi')" in lines
    assert (tmp_path / 'active-directory.hpi').is_file()


def test_fix_moves_each_file_to_the_path_it_declares_and_records_it(tmp_path):
    publish(tmp_path, 'active-directory-2.967.v1.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1'))
    publish(tmp_path, 'downloads/jenkins.war', make_war('2.479.1'))
    publish(tmp_path, 'downloads/poms/active-directory.pom', pom_xml(PLUGINS, 'active-directory', '2.967.v1'))
    code, lines = run(tmp_path, fix=True)
    assert code == 0
    assert set(Staging.open(tmp_path, SOURCE).files) == {AD + '.hpi', AD + '.pom', WAR + '.war'}
    assert f'moved     active-directory-2.967.v1.hpi  (to {AD}.hpi)' in lines
    assert f'moved     downloads/jenkins.war  (to {WAR}.war)' in lines
    assert f'recorded  {AD}.hpi  (added by hand)' in lines
    assert not (tmp_path / 'active-directory-2.967.v1.hpi').exists()
    assert not (tmp_path / 'downloads').exists()  # emptied folders go
    assert lines[-1] == summary(tmp_path, 3, 0, moved=3)


def test_fix_moves_a_plugin_off_the_wrong_maven_path(tmp_path):
    wrong = 'io/jenkins/plugins/active-directory/2.967.v1/active-directory-2.967.v1.hpi'
    publish(tmp_path, wrong, make_hpi(PLUGINS, 'active-directory', '2.967.v1'))
    code, _ = run(tmp_path, fix=True)
    assert code == 0
    assert (tmp_path / (AD + '.hpi')).is_file()
    assert not (tmp_path / 'io').exists()


@pytest.mark.parametrize(('there', 'detail'), [(b'same', 'the same file is already there; delete this copy'), (b'other', 'another file is already there')])
def test_fix_never_moves_a_file_over_another(tmp_path, there, detail):
    hpi = make_hpi(PLUGINS, 'active-directory', '2.967.v1')
    publish(tmp_path, 'copy.hpi', hpi)
    publish(tmp_path, AD + '.hpi', hpi if there == b'same' else make_hpi(PLUGINS, 'active-directory', '2.967.v1', jenkins_version='2.541.3'))
    before = (tmp_path / (AD + '.hpi')).read_bytes()
    code, lines = run(tmp_path, fix=True)
    assert code == 1
    assert f'REFUSED   copy.hpi  (cannot move it to {AD}.hpi: {detail})' in lines
    assert (tmp_path / 'copy.hpi').read_bytes() == hpi
    assert (tmp_path / (AD + '.hpi')).read_bytes() == before


def test_fix_never_moves_a_file_onto_a_listed_path(tmp_path):
    hpi = make_hpi(PLUGINS, 'active-directory', '2.967.v1')
    staging = Staging.open(tmp_path, SOURCE)
    stage(staging, AD + '.hpi', hpi, kind='dependency')
    staging.save()
    (tmp_path / (AD + '.hpi')).unlink()
    publish(tmp_path, 'copy.hpi', hpi)
    code, lines = run(tmp_path, fix=True)
    assert code == 1
    assert f'REFUSED   copy.hpi  (cannot move it to {AD}.hpi: procurement.json already lists that path)' in lines
    assert (tmp_path / 'copy.hpi').is_file()


def test_fix_never_moves_a_file_out_of_the_staging_folder(tmp_path):
    root, outside = tmp_path / 'stage', tmp_path / 'outside'
    outside.mkdir()
    publish(root, 'copy.hpi', make_hpi(PLUGINS, 'active-directory', '2.967.v1'))
    (root / 'org').symlink_to(outside, target_is_directory=True)
    code, lines = run(root, fix=True)
    assert code == 1
    assert f'REFUSED   copy.hpi  (cannot move it to {AD}.hpi: that path leads out of the staging folder)' in lines
    assert list(outside.iterdir()) == []
    assert (root / 'copy.hpi').is_file()
