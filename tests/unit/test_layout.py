"""Unit tests for uc.layout.

The site layout around the generator output and tool installer mirroring.
"""

from pathlib import Path

from sitefixture import write_generator_output
from uc.http import HttpError
from uc.layout import finalize_layout, mirror_tool_installers, site_dirs
from uc.model import Tiers

TEMPLATES = Path('/opt/uc/templates')
URL = 'https://uc.example.com'
INDEX = 'https://updates.jenkins.io/updates/'
MAVEN = 'hudson.tasks.Maven.MavenInstaller.json'


def test_finalize_layout_matches_upstream(tmp_path):
    tiers = Tiers(('2.540',), ('2.528.1', '2.528.3'))
    write_generator_output(tmp_path, URL, tiers)
    finalize_layout(tmp_path, tiers, TEMPLATES)
    assert [p.name for p in site_dirs(tmp_path, tiers)] == [
        'dynamic-2.540',
        'dynamic-stable-2.528.1',
        'dynamic-stable-2.528.3',
        'experimental',
        'current',
    ]
    for site in site_dirs(tmp_path, tiers):
        assert (site / 'updates').readlink() == Path('../updates')
    assert (tmp_path / 'updates').is_dir()
    assert (tmp_path / 'stable').readlink() == Path('dynamic-stable-2.528.3')
    assert (tmp_path / 'update-center.json').readlink() == Path(
        'current/update-center.json'
    )
    assert (tmp_path / 'latest').readlink() == Path('current/latest')
    assert (tmp_path / 'latestCore.txt').read_text() == '2.528.1'
    assert (tmp_path / 'uctest.json').read_bytes() == b'{}\n'
    assert 'Easy Update Center for Jenkins' in (tmp_path / 'index.html').read_text()


def test_without_stable_tiers_stable_is_current(tmp_path):
    write_generator_output(tmp_path, URL, Tiers())
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    assert (tmp_path / 'stable').readlink() == Path('current')


def test_finalize_layout_is_idempotent(tmp_path):
    write_generator_output(tmp_path, URL, Tiers())
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    assert (tmp_path / 'current' / 'updates').readlink() == Path('../updates')


def getter(files):
    def get(url, _headers=None, _timeout=60):
        if url not in files:
            msg = f'GET {url} failed: HTTP Error 404'
            raise HttpError(msg)
        return files[url]

    return get


LISTING = (
    b'<a href="?C=N;O=A">Name</a><a href="/">Parent</a>'
    b'<a href="hudson.tasks.Maven.MavenInstaller.json">x</a>'
    b'<a href="hudson.tasks.Maven.MavenInstaller.json.html">y</a>'
)


def test_tool_installers_are_copied_verbatim(tmp_path):
    files = {
        INDEX: LISTING,
        INDEX + MAVEN: (
            b'downloadService.post(\'hudson.tasks.Maven.MavenInstaller\',{"list":[]})'
        ),
        INDEX + MAVEN + '.html': b'<html>postMessage</html>',
    }
    assert (
        mirror_tool_installers(INDEX, tmp_path / 'updates', None, get=getter(files))
        == []
    )
    assert (tmp_path / 'updates' / MAVEN).read_bytes() == files[INDEX + MAVEN]
    assert (tmp_path / 'updates' / (MAVEN + '.html')).is_file()


def test_bad_tool_installer_keeps_previous_copy(tmp_path):
    previous = tmp_path / 'previous'
    previous.mkdir()
    (previous / MAVEN).write_text("downloadService.post('old',{})")
    files = {
        INDEX: LISTING,
        INDEX + MAVEN: b'<html>error page</html>',
        INDEX + MAVEN + '.html': b'x',
    }
    warnings = mirror_tool_installers(
        INDEX, tmp_path / 'updates', previous, get=getter(files)
    )
    assert 'kept the previous copy' in warnings[0]
    assert (
        tmp_path / 'updates' / MAVEN
    ).read_text() == "downloadService.post('old',{})"


def test_unreachable_tool_installers_without_previous(tmp_path):
    warnings = mirror_tool_installers(INDEX, tmp_path / 'updates', None, get=getter({}))
    assert warnings[0].startswith('tool installers not mirrored')


LOGIN_PAGE = b'<html><body><a href="/login">Sign in</a></body></html>'


def test_an_index_that_lists_no_installers_is_not_mirrored(tmp_path):
    # a login or error page answers 200 with links, none of them installers
    warnings = mirror_tool_installers(
        INDEX, tmp_path / 'updates', None, get=getter({INDEX: LOGIN_PAGE})
    )
    assert warnings == [
        f'tool installers not mirrored ({INDEX} lists no tool installer files)'
    ]
    assert not list((tmp_path / 'updates').iterdir())


def test_an_index_that_lists_no_installers_keeps_the_previous_copy(tmp_path):
    previous = tmp_path / 'previous'
    previous.mkdir()
    (previous / MAVEN).write_text("downloadService.post('old',{})")
    warnings = mirror_tool_installers(
        INDEX, tmp_path / 'updates', previous, get=getter({INDEX: LOGIN_PAGE})
    )
    assert warnings == [
        (
            f'tool installers not refreshed ({INDEX} lists no tool installer files); '
            'kept the previous copy'
        )
    ]
    assert (
        tmp_path / 'updates' / MAVEN
    ).read_text() == "downloadService.post('old',{})"
