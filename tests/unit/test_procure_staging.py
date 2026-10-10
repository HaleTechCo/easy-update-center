"""Unit tests for uc.procure.staging: the staging folder, its procurement.json and path checks."""

import hashlib
import json
import re
import stat
from pathlib import Path

import pytest

from procurefixture import SOURCE, stage
from uc.procure import ProcureError
from uc.procure.staging import MANIFEST, Staging, check_path, review_line, sha1_of

POM = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.pom'


@pytest.mark.parametrize('path', [POM, 'io/jenkins/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.hpi', 'org/jenkins-ci/main/jenkins-war/2.479.1/jenkins-war-2.479.1.war'])
def test_maven_paths_are_accepted(path):
    assert check_path(path) == path


@pytest.mark.parametrize('path', ['/etc/passwd', '../x/1/x-1.pom', 'org/../x/1/x-1.pom', 'org/x/1/x-1.jar', 'org/x/1/y-1.pom', 'x-1.pom', 'org\\x\\1\\x-1.pom', 'org//x/1/x-1.pom'])
def test_anything_else_is_refused(path):
    with pytest.raises(ProcureError, match='not a Maven path'):
        check_path(path)


def test_committed_files_are_recorded_and_saved(tmp_path):
    staging = Staging.open(tmp_path / 'stage', SOURCE)
    stage(staging, POM, b'<project/>')
    staging.save()
    saved = json.loads((tmp_path / 'stage' / MANIFEST).read_text())
    assert saved['format'] == 1
    assert saved['source'] == SOURCE
    assert saved['files'][POM] == {
        'url': SOURCE + POM,
        'sha1': hashlib.sha1(b'<project/>', usedforsecurity=False).hexdigest(),
        'size': 10,
        'kind': 'pom',
        'reason': 'test',
        'staged_at': '2026-09-27T00:00:00Z',
        'required_core': None,
        'replaces_unusable': False,
    }
    assert (tmp_path / 'stage' / POM).read_bytes() == b'<project/>'
    assert not list((tmp_path / 'stage').glob('.part-*'))
    assert Staging.open(tmp_path / 'stage', SOURCE).files == staging.files


def test_review_line_shows_the_host_folder_for_the_default_staging_dir():
    assert review_line(Path('/procurement'), 3) == 'next: review ./procurement (procurement.json lists 3 files), then uc procure push'
    assert review_line(Path('/procurement'), 1) == 'next: review ./procurement (procurement.json lists 1 file), then uc procure push'
    assert review_line(Path('/srv/stage'), 2) == 'next: review /srv/stage (procurement.json lists 2 files), then uc procure push'


def test_staged_files_and_procurement_json_are_readable_by_everyone(tmp_path):
    # The containers run as the operator (UC_UID), who owns what uc stages; mkstemp alone would leave it 0600.
    staging = Staging.open(tmp_path / 'stage', SOURCE)
    stage(staging, POM, b'<project/>')
    staging.save()
    assert stat.S_IMODE((tmp_path / 'stage' / MANIFEST).stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / 'stage' / POM).stat().st_mode) == 0o644


def test_intact_means_recorded_and_unchanged(tmp_path):
    staging = Staging.open(tmp_path, SOURCE)
    assert not staging.intact(POM)
    stage(staging, POM, b'<project/>')
    assert staging.intact(POM)
    (tmp_path / POM).write_bytes(b'<changed/>')
    assert not staging.intact(POM)


def test_remove_forgets_and_deletes(tmp_path):
    staging = Staging.open(tmp_path, SOURCE)
    stage(staging, POM, b'<project/>')
    staging.remove(POM)
    assert POM not in staging.files
    assert not (tmp_path / POM).exists()


def test_sha1_of_reads_the_whole_file(tmp_path):
    data = b'x' * 3_000_000
    (tmp_path / 'f').write_bytes(data)
    assert sha1_of(tmp_path / 'f') == hashlib.sha1(data, usedforsecurity=False).hexdigest()


@pytest.mark.parametrize('content', ['not json', '{"format": 2, "files": {}}', '[]', '{"format": 1, "files": {"org/x/1/x-1.pom": {"unexpected": 1}}}'])
def test_a_hand_edited_manifest_is_refused_with_its_name(tmp_path, content):
    (tmp_path / MANIFEST).write_text(content)
    with pytest.raises(ProcureError, match=re.escape('procurement.json is not a procurement.json this version of uc wrote')):
        Staging.open(tmp_path, SOURCE)


def test_a_manifest_naming_a_path_outside_the_layout_is_refused(tmp_path):
    entry = {'url': 'u', 'sha1': '0' * 40, 'size': 1, 'kind': 'pom', 'reason': 'r', 'staged_at': 't'}
    (tmp_path / MANIFEST).write_text(json.dumps({'format': 1, 'source': SOURCE, 'files': {'../../etc/x-1.pom': entry}}))
    with pytest.raises(ProcureError, match='not a Maven path'):
        Staging.open(tmp_path, SOURCE)


def test_an_unknown_kind_or_a_bad_sha1_is_refused(tmp_path):
    entry = {'url': 'u', 'sha1': 'nope', 'size': 1, 'kind': 'plugin', 'reason': 'r', 'staged_at': 't'}
    (tmp_path / MANIFEST).write_text(json.dumps({'format': 1, 'source': SOURCE, 'files': {POM: entry}}))
    with pytest.raises(ProcureError, match="has kind 'plugin' and sha1 'nope'"):
        Staging.open(tmp_path, SOURCE)


def test_an_unwritable_folder_says_how_to_fix_it(tmp_path):
    root = tmp_path / 'stage'
    root.mkdir()
    root.chmod(0o555)
    with pytest.raises(ProcureError, match=r'^cannot write to .*: .*\. Check that UC_UID and UC_GID in \.env are the owner of \./procurement \(id -u, id -g\)$'):
        Staging.open(root, SOURCE).temporary()
