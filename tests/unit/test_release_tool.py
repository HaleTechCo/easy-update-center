"""tools/release.py: CHANGELOG.md's Unreleased entries become a version, what the Release workflow refuses, and the release notes."""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location('release', TOOLS / 'release.py')
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

Intro.

## Unreleased

### Changed

- A change.

### Added

- A new thing.

## [0.1.0] - 2026-10-10

### Added

- The first release.
"""

PREPARED = """# Changelog

Intro.

## Unreleased

## [0.2.0] - 2026-11-02

### Changed

- A change.

### Added

- A new thing.

## [0.1.0] - 2026-10-10

### Added

- The first release.
"""

NOTHING_UNRELEASED = CHANGELOG.replace('- A change.\n\n', '').replace('- A new thing.\n\n', '')
"""Unreleased with its subsection headings but no entries."""

TAGS = ['v0.1.0', 'v0.1.1', 'v0.2.0', 'backup/settings-file-pre-squash', 'v1.0.0-rc1', 'update-center2-3.18.4']


def test_prepare_moves_the_unreleased_entries_under_the_version():
    assert release.prepare(CHANGELOG, '0.2.0', '2026-11-02') == PREPARED


def test_prepare_changes_nothing_else():
    assert release.prepare(CHANGELOG, '0.2.0', '2026-11-02').replace('\n## [0.2.0] - 2026-11-02\n', '', 1) == CHANGELOG


def test_prepare_refuses_an_unreleased_section_with_only_headings():
    with pytest.raises(release.ReleaseError, match='nothing to release'):
        release.prepare(NOTHING_UNRELEASED, '0.2.0', '2026-11-02')


def test_prepare_refuses_a_version_the_changelog_already_has():
    with pytest.raises(release.ReleaseError, match=r'already has a section for 0\.1\.0'):
        release.prepare(CHANGELOG, '0.1.0', '2026-11-02')


@pytest.mark.parametrize('version', ['1.0', 'v1.0.0', '01.0.0', '1.0.0-rc1', '1.0.0 '])
def test_a_version_is_x_y_z(version):
    with pytest.raises(release.ReleaseError, match='is not a version'):
        release.prepare(CHANGELOG, version, '2026-11-02')


def test_notes_are_one_version_section_without_its_heading():
    assert release.notes(PREPARED, '0.2.0') == '### Changed\n\n- A change.\n\n### Added\n\n- A new thing.\n'
    assert release.notes(PREPARED, '0.1.0') == '### Added\n\n- The first release.\n'


def test_notes_refuse_a_version_the_changelog_lacks():
    with pytest.raises(release.ReleaseError, match=r'no section for 0\.3\.0'):
        release.notes(PREPARED, '0.3.0')


def test_highest_compares_versions_as_numbers_and_ignores_other_tags():
    assert release.highest(['v0.9.1', 'v0.10.0', 'v0.2.0', 'backup/x', 'v1.0.0-rc1']) == '0.10.0'
    assert release.highest(['backup/settings-file-pre-squash']) is None


@pytest.mark.parametrize('version', ['0.2.1', '0.3.0', '1.0.0'])
def test_check_a_new_release(version):
    assert release.check(version, TAGS, CHANGELOG, publish_only=False) == {'latest': True, 'minor_latest': True}


def test_check_the_first_release():
    assert release.check('0.1.0', ['backup/x'], CHANGELOG.replace('## [0.1.0]', '## [0.0.9]'), publish_only=False) == {'latest': True, 'minor_latest': True}


@pytest.mark.parametrize(('version', 'message'), [('0.2.0', r'v0\.2\.0 already exists'), ('0.1.2', r'0\.1\.2 is not above the highest release, 0\.2\.0')])
def test_check_refuses_a_version_that_is_not_new(version, message):
    with pytest.raises(release.ReleaseError, match=message):
        release.check(version, TAGS, CHANGELOG, publish_only=False)


def test_check_refuses_a_release_with_nothing_unreleased():
    with pytest.raises(release.ReleaseError, match='nothing to release'):
        release.check('0.2.1', TAGS, NOTHING_UNRELEASED, publish_only=False)


@pytest.mark.parametrize(('version', 'flags'), [('0.2.0', {'latest': True, 'minor_latest': True}), ('0.1.1', {'latest': False, 'minor_latest': True}), ('0.1.0', {'latest': False, 'minor_latest': False})])
def test_republishing_moves_latest_and_the_minor_tag_only_for_the_highest(version, flags):
    assert release.check(version, TAGS, NOTHING_UNRELEASED, publish_only=True) == flags


def test_publish_only_refuses_a_version_never_released():
    with pytest.raises(release.ReleaseError, match=r'v0\.3\.0 does not exist'):
        release.check('0.3.0', TAGS, CHANGELOG, publish_only=True)


def test_the_command_line_checks_prepares_and_prints_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['check', '0.2.1']) == 0
    assert capsys.readouterr().out == 'latest=true\nminor_latest=true\n'
    assert release.main(['prepare', '0.2.1']) == 0
    assert re.search(r'^## \[0\.2\.1\] - \d{4}-\d{2}-\d{2}\n', (tmp_path / 'CHANGELOG.md').read_text(encoding='utf-8'), re.MULTILINE)
    assert release.main(['notes', '0.2.1']) == 0
    assert capsys.readouterr().out == '### Changed\n\n- A change.\n\n### Added\n\n- A new thing.\n'
    assert release.main(['latest']) == 0
    assert capsys.readouterr().out == '0.2.0\n'


def test_the_command_line_reports_a_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['check', '0.2.0']) == 1
    assert capsys.readouterr().err == 'release.py: error: v0.2.0 already exists: release a higher version, or republish this one with publish-only\n'


def test_git_tags_lists_the_repository_tags(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=tmp_path, check=True, capture_output=True)

    git('init', '-q')
    git('commit', '-q', '--allow-empty', '-m', 'start')
    git('tag', 'v0.1.0')
    git('tag', 'backup/x')
    monkeypatch.chdir(tmp_path)
    assert sorted(release.git_tags()) == ['backup/x', 'v0.1.0']
