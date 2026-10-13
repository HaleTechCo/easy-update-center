"""Unit tests for uc.procure.stager.

Staging one file, keeping intact ones, failing cleanly.
"""

import io

from httpstub import StubServer
from procurefixture import publish
from uc.procure.stager import Stager, Wanted
from uc.procure.staging import Staging
from uc.procure.upstream import Upstream
from uc.repo_adapter.backends import Http

POM = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.pom'


def stager_for(stage_dir, url):
    out = io.StringIO()
    return Stager(
        Staging.open(stage_dir, url), Upstream(url, Http(None), limit=1 << 20), out
    ), out


def test_a_file_is_downloaded_recorded_and_reported(releases, tmp_path):
    root, url = releases
    publish(root, POM, b'<project/>')
    stager, out = stager_for(tmp_path / 'stage', url)
    assert stager.stage(Wanted(POM, 'pom', 'real POM for mailer'))
    staged = stager.staging.files[POM]
    assert (staged.url, staged.size, staged.kind, staged.reason) == (
        url + POM,
        10,
        'pom',
        'real POM for mailer',
    )
    assert (tmp_path / 'stage' / POM).read_bytes() == b'<project/>'
    assert out.getvalue() == f'staged    {POM}  (real POM for mailer)\n'
    assert (stager.staged, stager.kept, stager.problems) == (1, 0, 0)


def test_an_intact_file_is_kept_without_the_source(releases, tmp_path):
    root, url = releases
    publish(root, POM, b'<project/>')
    first, _ = stager_for(tmp_path / 'stage', url)
    first.stage(Wanted(POM, 'pom', 'first'))
    first.staging.save()
    (root / POM).unlink()  # keeping it must not need the source
    again, out = stager_for(tmp_path / 'stage', url)
    assert again.stage(
        Wanted(POM, 'pom', 'replaces an unusable .pom', replaces_unusable=True)
    )
    assert out.getvalue() == f'kept      {POM}  (already staged)\n'
    assert again.staging.files[POM].replaces_unusable is True
    assert again.kept == 1


def test_a_changed_file_is_downloaded_again(releases, tmp_path):
    root, url = releases
    publish(root, POM, b'<project/>')
    first, _ = stager_for(tmp_path / 'stage', url)
    first.stage(Wanted(POM, 'pom', 'first'))
    first.staging.save()
    (tmp_path / 'stage' / POM).write_bytes(b'<tampered/>')
    again, _ = stager_for(tmp_path / 'stage', url)
    assert again.stage(Wanted(POM, 'pom', 'first'))
    assert (tmp_path / 'stage' / POM).read_bytes() == b'<project/>'
    assert again.staged == 1


def test_a_failed_download_leaves_nothing_behind(tmp_path):
    routes = {
        ('GET', '/r/' + POM + '.sha1'): (200, {}, b'0' * 40),
        ('GET', '/r/' + POM): (200, {}, b'<project/>'),
    }
    with StubServer(routes) as stub:
        stager, out = stager_for(tmp_path / 'stage', stub.url + '/r/')
        assert not stager.stage(Wanted(POM, 'pom', 'x'))
    assert POM not in stager.staging.files
    assert not (tmp_path / 'stage' / POM).exists()
    assert not list((tmp_path / 'stage').glob('.part-*'))
    assert out.getvalue().startswith(f'FAILED    {POM}  (')
    assert stager.problems == 1


def test_a_path_outside_the_layout_is_a_problem_not_a_crash(releases, tmp_path):
    _, url = releases
    stager, out = stager_for(tmp_path / 'stage', url)
    assert not stager.stage(Wanted('../evil-1.pom', 'pom', 'x'))
    assert stager.problems == 1
    assert 'not a Maven path' in out.getvalue()
