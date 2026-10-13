"""tools/release.py: the release pull request's change, the tag checks and the notes.

prepare moves CHANGELOG.md's Unreleased entries under the version and sets
compose.yaml's images to it; check refuses a tag the Release workflow must not publish.
"""

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

NOTHING_UNRELEASED = CHANGELOG.replace('- A change.\n\n', '').replace(
    '- A new thing.\n\n', ''
)
"""Unreleased with its subsection headings but no entries."""

HISTORY = CHANGELOG.replace(
    '## [0.1.0] - 2026-10-10',
    '## [0.2.0] - 2026-10-30\n\n- Two.\n\n'
    '## [0.1.1] - 2026-10-20\n\n- One.\n\n'
    '## [0.1.0] - 2026-10-10',
)
"""A CHANGELOG with a section for each of the releases in TAGS."""

COMPOSE = """x-uc: &uc
  image: ${UC_IMAGE:-ghcr.io/haletechco/easy-update-center-uc:0.2.0}
services:
  web:
    image: ${WEB_IMAGE:-ghcr.io/haletechco/easy-update-center-web:0.2.0}
  setup:
    image: ${UC_IMAGE:-ghcr.io/haletechco/easy-update-center-uc:0.2.0}
"""

TAGS = [
    'v0.1.0',
    'v0.1.1',
    'v0.2.0',
    'backup/settings-file-pre-squash',
    'v1.0.0-rc1',
    'update-center2-3.18.4',
]


def released(version: str) -> tuple[str, str]:
    """Return CHANGELOG.md and compose.yaml as the release pull request left them."""
    return (
        release.prepare(CHANGELOG, version, '2026-11-02'),
        release.pin_images(COMPOSE, version),
    )


def test_prepare_moves_the_unreleased_entries_under_the_version():
    assert release.prepare(CHANGELOG, '0.2.0', '2026-11-02') == PREPARED


def test_prepare_changes_nothing_else():
    prepared = release.prepare(CHANGELOG, '0.2.0', '2026-11-02')
    assert prepared.replace('\n## [0.2.0] - 2026-11-02\n', '', 1) == CHANGELOG


def test_prepare_refuses_an_unreleased_section_with_only_headings():
    with pytest.raises(release.ReleaseError, match='nothing to release'):
        release.prepare(NOTHING_UNRELEASED, '0.2.0', '2026-11-02')


def test_prepare_refuses_a_version_the_changelog_already_has():
    message = r'already has a section for 0\.1\.0'
    with pytest.raises(release.ReleaseError, match=message):
        release.prepare(CHANGELOG, '0.1.0', '2026-11-02')


@pytest.mark.parametrize(
    'version',
    [
        '1.0',
        'v1.0.0',
        '01.0.0',
        '1.0.0-rc1',
        '1.0.0 ',
        '1.0.0-rc.0',
        '1.0.0-rc.01',
        '1.0.0-beta.1',
        '1.0.0-rc',
    ],
)
def test_a_version_is_x_y_z(version):
    with pytest.raises(release.ReleaseError, match='is not a version'):
        release.prepare(CHANGELOG, version, '2026-11-02')


def test_notes_are_one_version_section_without_its_heading():
    notes = release.notes(PREPARED, '0.2.0')
    assert notes == '### Changed\n\n- A change.\n\n### Added\n\n- A new thing.\n'
    assert release.notes(PREPARED, '0.1.0') == '### Added\n\n- The first release.\n'


BASE = 'https://github.com/HaleTechCo/easy-update-center/blob/v0.2.0/'


def linked(text: str) -> str:
    """Return the 0.2.0 notes of a CHANGELOG whose only entry is text."""
    changelog = f'## Unreleased\n\n## [0.2.0] - 2026-11-02\n\n{text}\n'
    return release.notes(changelog, '0.2.0')


def test_notes_point_a_relative_link_at_the_files_of_the_release():
    assert (
        linked('- See [the guide](docs/how-to/verify-a-release.md).')
        == f'- See [the guide]({BASE}docs/how-to/verify-a-release.md).\n'
    )


def test_notes_keep_the_anchor_of_a_relative_link():
    assert (
        linked('- [Upgrading](docs/how-to/upgrade.md#upgrade-from-source) now.')
        == f'- [Upgrading]({BASE}docs/how-to/upgrade.md#upgrade-from-source) now.\n'
    )


@pytest.mark.parametrize(
    'text',
    [
        '- [site](https://example.org/a.md#b)',
        '- [mail](mailto:a@example.org)',
        '- [here](#anchor)',
        '- [plain](http://example.org)',
        '- no link (README.md)',
    ],
)
def test_notes_leave_other_links_alone(text):
    assert linked(text) == text + '\n'


def test_notes_leave_links_in_inline_code_and_fenced_blocks_alone():
    text = '- Write `[a](README.md)` as [b](CHANGELOG.md).\n\n```md\n[c](LICENSE)\n```'
    assert linked(text) == (
        f'- Write `[a](README.md)` as [b]({BASE}CHANGELOG.md).\n\n'
        '```md\n[c](LICENSE)\n```\n'
    )


def test_notes_refuse_a_version_the_changelog_lacks():
    with pytest.raises(release.ReleaseError, match=r'no section for 0\.3\.0'):
        release.notes(PREPARED, '0.3.0')


def test_highest_compares_versions_as_numbers_and_ignores_other_tags():
    tags = ['v0.9.1', 'v0.10.0', 'v0.2.0', 'backup/x', 'v1.0.0-beta.1']
    assert release.highest(tags) == '0.10.0'
    assert release.highest(['backup/settings-file-pre-squash']) is None


@pytest.mark.parametrize('version', ['0.2.1', '0.3.0', '1.0.0'])
def test_check_a_pushed_release(version):
    changelog, compose = released(version)
    tags = [*TAGS, f'v{version}']
    flags = release.check(version, tags, changelog, compose)
    assert flags == {'prerelease': False, 'latest': True, 'minor_latest': True}


def test_check_the_first_release():
    compose = release.pin_images(COMPOSE, '0.1.0')
    tags = ['backup/x', 'v0.1.0']
    flags = release.check('0.1.0', tags, CHANGELOG, compose)
    assert flags == {'prerelease': False, 'latest': True, 'minor_latest': True}


def test_check_refuses_a_tag_never_pushed():
    changelog, compose = released('0.2.1')
    message = r'v0\.2\.1 does not exist: push the tag first'
    with pytest.raises(release.ReleaseError, match=message):
        release.check('0.2.1', TAGS, changelog, compose)


def test_check_refuses_a_new_tag_not_above_the_highest_release():
    changelog, compose = released('0.1.2')
    message = r'0\.1\.2 is not above the highest release, 0\.2\.0'
    with pytest.raises(release.ReleaseError, match=message):
        release.check('0.1.2', [*TAGS, 'v0.1.2'], changelog, compose)


def test_check_refuses_a_tag_without_its_changelog_section():
    compose = release.pin_images(COMPOSE, '0.2.1')
    message = r'CHANGELOG\.md has no section for 0\.2\.1: tag the merged release'
    with pytest.raises(release.ReleaseError, match=message):
        release.check('0.2.1', [*TAGS, 'v0.2.1'], CHANGELOG, compose)


def test_check_refuses_a_tag_whose_compose_file_names_other_images():
    changelog, _ = released('0.2.1')
    message = r'compose\.yaml names other images than 0\.2\.1'
    with pytest.raises(release.ReleaseError, match=message):
        release.check('0.2.1', [*TAGS, 'v0.2.1'], changelog, COMPOSE)


def test_pin_images_sets_every_uc_and_web_image_to_the_version():
    assert release.pin_images(COMPOSE, '0.2.1') == COMPOSE.replace(':0.2.0}', ':0.2.1}')


def test_pin_images_refuses_a_compose_file_without_one_of_the_images():
    message = (
        r'compose\.yaml names no ghcr\.io/haletechco/easy-update-center-web image '
        r'to set to 0\.2\.1'
    )
    with pytest.raises(release.ReleaseError, match=message):
        release.pin_images(COMPOSE.replace('easy-update-center-web', 'web'), '0.2.1')


def test_the_command_line_prepares_checks_and_prints_notes(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    (tmp_path / 'compose.yaml').write_bytes(COMPOSE.encode('utf-8'))
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['prepare', '0.2.1']) == 0
    changelog = (tmp_path / 'CHANGELOG.md').read_text(encoding='utf-8')
    heading = re.compile(r'^## \[0\.2\.1\] - \d{4}-\d{2}-\d{2}\n', re.MULTILINE)
    assert heading.search(changelog)
    compose = (tmp_path / 'compose.yaml').read_text(encoding='utf-8')
    assert compose == COMPOSE.replace(':0.2.0}', ':0.2.1}')
    monkeypatch.setattr(release, 'git_tags', lambda: [*TAGS, 'v0.2.1'])
    assert release.main(['check', '0.2.1']) == 0
    output = capsys.readouterr().out
    assert output == 'version=0.2.1\nprerelease=false\nlatest=true\nminor_latest=true\n'
    assert release.main(['notes', '0.2.1']) == 0
    notes = capsys.readouterr().out
    assert notes == '### Changed\n\n- A change.\n\n### Added\n\n- A new thing.\n'
    assert release.main(['latest']) == 0
    assert capsys.readouterr().out == '0.2.1\n'


def test_the_command_line_refuses_to_prepare_a_version_not_above_the_highest(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    (tmp_path / 'compose.yaml').write_bytes(COMPOSE.encode('utf-8'))
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['prepare', '0.2.0']) == 1
    expected = 'release.py: error: 0.2.0 is not above the highest release, 0.2.0\n'
    assert capsys.readouterr().err == expected
    assert (tmp_path / 'CHANGELOG.md').read_bytes() == CHANGELOG.encode('utf-8')


def test_the_command_line_prepares_nothing_when_compose_names_no_image(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    (tmp_path / 'compose.yaml').write_bytes(b'services: {}\n')
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['prepare', '0.2.1']) == 1
    error = capsys.readouterr().err
    assert 'compose.yaml names no ghcr.io/haletechco/easy-update-center-uc or' in error
    assert (tmp_path / 'CHANGELOG.md').read_bytes() == CHANGELOG.encode('utf-8')
    assert (tmp_path / 'compose.yaml').read_bytes() == b'services: {}\n'


def test_git_tags_lists_the_repository_tags(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git('init', '-q')
    git('commit', '-q', '--allow-empty', '-m', 'start')
    git('tag', 'v0.1.0')
    git('tag', 'backup/x')
    monkeypatch.chdir(tmp_path)
    assert sorted(release.git_tags()) == ['backup/x', 'v0.1.0']


COMPOSE_RC = (
    'services:\n'
    '  uc: {image: "${UC_IMAGE:-ghcr.io/haletechco/easy-update-center-uc'
    ':0.2.0-rc.1}"}\n'
    '  web: {image: "${WEB_IMAGE:-ghcr.io/haletechco/easy-update-center-web'
    ':0.2.0-rc.1}"}\n'
)


def test_candidates_sort_below_their_release_and_by_number():
    order = ['0.1.0', '0.2.0-rc.1', '0.2.0-rc.2', '0.2.0-rc.10', '0.2.0', '0.2.1-rc.1']
    assert sorted(sorted(order, key=str), key=release.parse_version) == order


def test_highest_can_ignore_candidates():
    tags = ['v0.1.0', 'v0.2.0-rc.1']
    assert release.highest(tags) == '0.2.0-rc.1'
    assert release.highest(tags, releases_only=True) == '0.1.0'


def test_check_a_candidate_moves_no_floating_tag():
    flags = release.check(
        '0.2.0-rc.1', ['v0.1.0', 'v0.2.0-rc.1'], CHANGELOG, COMPOSE_RC
    )
    assert flags == {'prerelease': True, 'latest': False, 'minor_latest': False}


def test_check_refuses_a_candidate_below_its_release():
    with pytest.raises(
        release.ReleaseError, match=r'not above the highest release, 0\.2\.0'
    ):
        release.check('0.2.0-rc.3', ['v0.2.0', 'v0.2.0-rc.3'], CHANGELOG, COMPOSE_RC)


def test_check_refuses_a_candidate_with_nothing_unreleased():
    with pytest.raises(release.ReleaseError, match='no entries under "## Unreleased"'):
        release.check('0.2.0-rc.1', ['v0.1.0', 'v0.2.0-rc.1'], PREPARED, COMPOSE_RC)


def test_a_release_after_its_candidates_is_the_latest():
    compose = COMPOSE_RC.replace('0.2.0-rc.1', '0.2.0')
    flags = release.check(
        '0.2.0', ['v0.1.0', 'v0.2.0-rc.1', 'v0.2.0'], PREPARED, compose
    )
    assert flags == {'prerelease': False, 'latest': True, 'minor_latest': True}


def test_candidate_notes_are_the_unreleased_entries():
    text = release.notes(CHANGELOG, '0.2.0-rc.1')
    assert text.startswith('### Changed\n\n- A change.')
    assert '[0.1.0]' not in text


def test_the_command_line_prepares_and_checks_a_candidate(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'CHANGELOG.md').write_bytes(CHANGELOG.encode('utf-8'))
    (tmp_path / 'compose.yaml').write_bytes(COMPOSE.encode('utf-8'))
    monkeypatch.setattr(release, 'git_tags', lambda: TAGS)
    assert release.main(['prepare', '0.3.0-rc.1']) == 0
    assert (tmp_path / 'CHANGELOG.md').read_bytes() == CHANGELOG.encode('utf-8')
    compose = (tmp_path / 'compose.yaml').read_text(encoding='utf-8')
    assert compose == COMPOSE.replace(':0.2.0}', ':0.3.0-rc.1}')
    monkeypatch.setattr(release, 'git_tags', lambda: [*TAGS, 'v0.3.0-rc.1'])
    assert release.main(['check', '0.3.0-rc.1']) == 0
    expected = 'version=0.3.0-rc.1\nprerelease=true\nlatest=false\nminor_latest=false\n'
    assert capsys.readouterr().out == expected
