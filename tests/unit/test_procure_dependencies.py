"""Unit tests for uc.procure.dependencies.

Missing required dependencies, staged transitively.
"""

import io
from pathlib import Path

import pytest

from archivefixture import make_hpi, pom_xml
from procurefixture import publish
from uc.aql import RepoFile, classify
from uc.doctor import DoctorReport, Inspection
from uc.http import Http
from uc.procure.dependencies import resolve_dependencies
from uc.procure.stager import Stager
from uc.procure.staging import Staging
from uc.procure.upstream import Upstream
from uc.versions import VersionTool

PLUGINS = 'org.jenkins-ci.plugins'


def versions():
    return VersionTool(Path('/opt/uc/generator'), Path('/opt/uc/tools'))


def path(group, artifact, version, ext):
    return f'{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}.{ext}'


def release(
    root,
    group,
    artifact,
    version,
    *,
    requires='',
    core='2.479.1',
    ext='hpi',
    pom_dependencies=(),
):
    pom = pom_xml(group, artifact, version, dependencies=pom_dependencies)
    publish(
        root,
        path(group, artifact, version, ext),
        make_hpi(
            group,
            artifact,
            version,
            pom=pom,
            jenkins_version=core,
            dependencies=requires,
        ),
    )
    publish(root, path(group, artifact, version, 'pom'), pom)


class Poms:
    """The repository's POMs, by artifactId, as ArtifactSource returns them."""

    def __init__(self, poms):
        """Serve the given POM bytes, keyed by artifactId."""
        self.poms = poms

    def pom(self, gav):
        """Return the POM for gav, or None."""
        return self.poms.get(gav.artifact_id)

    def manifest(self, gav):
        """Return no manifest: resolution reads the staged archives' manifests."""
        return self.poms.get(('manifest', gav.artifact_id))


def repository(*releases_in_repo):
    files = []
    for group, artifact, version in releases_in_repo:
        directory = f'{group.replace(".", "/")}/{artifact}/{version}'
        files += [
            RepoFile(directory, f'{artifact}-{version}.hpi'),
            RepoFile(directory, f'{artifact}-{version}.pom'),
        ]
    return classify(files)


def missing(plugin, version, dependency, required, group=PLUGINS):
    return {
        'plugin': plugin,
        'plugin_group': group,
        'plugin_version': version,
        'dependency': dependency,
        'required': required,
    }


class RecordingHttp(Http):
    """An Http that records the URL of every request and download."""

    def __init__(self):
        """Verify TLS against the system bundle; nothing recorded yet."""
        super().__init__(None)
        self.urls = []

    def request(self, url, **kwargs):
        """Record the URL, then make the request."""
        self.urls.append(url)
        return super().request(url, **kwargs)

    def download(self, url, **kwargs):
        """Record the URL, then download it."""
        self.urls.append(url)
        return super().download(url, **kwargs)


def run(tmp_path, url, *, listing, details, poms=None, http=None):
    out = io.StringIO()
    stager = Stager(
        Staging.open(tmp_path / 'stage', url),
        Upstream(url, http or Http(None), limit=1 << 20),
        out,
    )
    resolve_dependencies(
        Inspection(
            DoctorReport(missing_dependency_details=details), listing, Poms(poms or {})
        ),
        stager,
        versions(),
    )
    return stager, out


AD_POM = pom_xml(
    PLUGINS, 'active-directory', '2.0', dependencies=[(PLUGINS, 'mailer', '534.v1')]
)


def test_a_missing_dependency_and_its_own_are_staged(releases, tmp_path):
    root, url = releases
    release(
        root,
        PLUGINS,
        'mailer',
        '534.v1',
        requires='display-url-api:2.0',
        pom_dependencies=[(PLUGINS, 'display-url-api', '2.0')],
    )
    release(root, PLUGINS, 'display-url-api', '2.0', core='2.462.1')
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
        poms={'active-directory': AD_POM},
    )
    files = stager.staging.files
    mailer, display = (
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'display-url-api', '2.0', 'hpi'),
    )
    assert set(files) == {
        mailer,
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
        display,
        path(PLUGINS, 'display-url-api', '2.0', 'pom'),
    }
    assert files[mailer].reason == 'active-directory 2.0 requires mailer 534.v1'
    assert files[display].reason == 'mailer 534.v1 requires display-url-api 2.0'
    assert (files[mailer].required_core, files[display].required_core) == (
        '2.479.1',
        '2.462.1',
    )
    assert {staged.kind for staged in files.values()} == {'dependency'}
    assert stager.problems == 0


def test_a_dependency_the_repository_has_new_enough_is_not_staged(releases, tmp_path):
    root, url = releases
    release(
        root,
        PLUGINS,
        'mailer',
        '534.v1',
        requires='display-url-api:2.0',
        pom_dependencies=[(PLUGINS, 'display-url-api', '2.0')],
    )
    # the source has the asked-for version, so only the listing keeps it out
    release(root, PLUGINS, 'display-url-api', '2.0')
    listing = repository(
        (PLUGINS, 'active-directory', '2.0'), (PLUGINS, 'display-url-api', '3.0')
    )
    stager, _ = run(
        tmp_path,
        url,
        listing=listing,
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
        poms={'active-directory': AD_POM},
    )
    assert set(stager.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
    }
    assert stager.problems == 0


def test_the_highest_minimum_wins(releases, tmp_path):
    root, url = releases
    release(root, PLUGINS, 'mailer', '500.v1')
    release(root, PLUGINS, 'mailer', '534.v1')
    details = [
        missing('active-directory', '2.0', 'mailer', '500.v1'),
        missing('email-ext', '3.0', 'mailer', '534.v1'),
    ]
    stager, _ = run(
        tmp_path,
        url,
        listing=repository(
            (PLUGINS, 'active-directory', '2.0'), (PLUGINS, 'email-ext', '3.0')
        ),
        details=details,
    )
    assert set(stager.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
    }
    assert (
        stager.staging.files[path(PLUGINS, 'mailer', '534.v1', 'hpi')].reason
        == 'email-ext 3.0 requires mailer 534.v1'
    )


def test_a_higher_minimum_replaces_a_version_an_earlier_run_staged(releases, tmp_path):
    root, url = releases
    release(root, PLUGINS, 'mailer', '500.v1')
    release(root, PLUGINS, 'mailer', '534.v1')
    listing = repository((PLUGINS, 'active-directory', '2.0'))
    first, _ = run(
        tmp_path,
        url,
        listing=listing,
        details=[missing('active-directory', '2.0', 'mailer', '500.v1')],
    )
    first.staging.save()
    second, _ = run(
        tmp_path,
        url,
        listing=listing,
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
    )
    assert set(second.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
    }
    assert not (tmp_path / 'stage' / path(PLUGINS, 'mailer', '500.v1', 'hpi')).exists()
    assert not (tmp_path / 'stage' / path(PLUGINS, 'mailer', '500.v1', 'pom')).exists()


def test_the_group_comes_from_the_fallbacks_and_jpi_is_tried(releases, tmp_path):
    root, url = releases
    release(root, 'io.jenkins.plugins', 'caffeine-api', '3.2.4', ext='jpi')
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'caffeine-api', '3.2.4')],
    )
    assert set(stager.staging.files) == {
        path('io.jenkins.plugins', 'caffeine-api', '3.2.4', 'jpi'),
        path('io.jenkins.plugins', 'caffeine-api', '3.2.4', 'pom'),
    }


def test_the_group_comes_from_the_requiring_plugins_pom(releases, tmp_path):
    root, url = releases
    release(root, 'com.example.jenkins', 'corp-lib', '1.0')
    ad_pom = pom_xml(
        PLUGINS,
        'active-directory',
        '2.0',
        dependencies=[('com.example.jenkins', 'corp-lib', '1.0')],
    )
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'corp-lib', '1.0')],
        poms={'active-directory': ad_pom},
    )
    assert path('com.example.jenkins', 'corp-lib', '1.0', 'hpi') in stager.staging.files


def test_an_unresolvable_dependency_is_a_problem_and_the_rest_continue(
    releases, tmp_path
):
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1')
    details = [
        missing('active-directory', '2.0', 'nowhere', '1.0'),
        missing('active-directory', '2.0', 'mailer', '534.v1'),
    ]
    stager, out = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=details,
    )
    assert stager.problems == 1
    groups = (
        'org.jenkins-ci.plugins, io.jenkins.plugins, org.jenkins-ci.plugins.workflow, '
        'org.jenkinsci.plugins, io.jenkins.blueocean'
    )
    assert (
        f'FAILED    nowhere 1.0  (not found at the source under {groups}; '
        'required by active-directory 2.0)'
    ) in out.getvalue().splitlines()
    assert path(PLUGINS, 'mailer', '534.v1', 'hpi') in stager.staging.files


def test_optional_and_already_satisfied_dependencies_end_the_walk(releases, tmp_path):
    root, url = releases
    release(
        root,
        PLUGINS,
        'mailer',
        '534.v1',
        requires='active-directory:1.0,matrix-auth:3.0;resolution:=optional',
    )
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
    )
    assert set(stager.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
    }
    assert stager.problems == 0


def test_a_cycle_between_staged_dependencies_ends_and_each_is_staged_once(
    releases, tmp_path
):
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1', requires='display-url-api:2.0')
    release(root, PLUGINS, 'display-url-api', '2.0', requires='mailer:500.v1')
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
    )
    assert len(stager.staging.files) == 4
    assert (stager.staged, stager.kept, stager.problems) == (4, 0, 0)


def test_a_rerun_downloads_a_staged_dependency_that_went_missing(releases, tmp_path):
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1')
    listing, details = (
        repository((PLUGINS, 'active-directory', '2.0')),
        [missing('active-directory', '2.0', 'mailer', '534.v1')],
    )
    first, _ = run(tmp_path, url, listing=listing, details=details)
    first.staging.save()
    hpi = path(PLUGINS, 'mailer', '534.v1', 'hpi')
    (tmp_path / 'stage' / hpi).unlink()
    second, out = run(tmp_path, url, listing=listing, details=details)
    assert second.staging.intact(hpi)
    assert (second.staged, second.kept, second.problems) == (1, 1, 0)
    assert (
        f'staged    {hpi}  (active-directory 2.0 requires mailer 534.v1)'
        in out.getvalue().splitlines()
    )


def test_a_rerun_stages_what_a_staged_dependency_needs_once_the_source_has_it(
    releases, tmp_path
):
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1', requires='display-url-api:2.0')
    listing, details = (
        repository((PLUGINS, 'active-directory', '2.0')),
        [missing('active-directory', '2.0', 'mailer', '534.v1')],
    )
    first, _ = run(tmp_path, url, listing=listing, details=details)
    first.staging.save()
    assert first.problems == 1
    release(root, PLUGINS, 'display-url-api', '2.0', core='2.462.1')
    second, _ = run(tmp_path, url, listing=listing, details=details)
    display = path(PLUGINS, 'display-url-api', '2.0', 'hpi')
    assert second.problems == 0
    assert set(second.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
        display,
        path(PLUGINS, 'display-url-api', '2.0', 'pom'),
    }
    assert (
        second.staging.files[display].reason
        == 'mailer 534.v1 requires display-url-api 2.0'
    )
    assert second.staging.files[display].required_core == '2.462.1'
    assert (second.staged, second.kept) == (2, 2)


@pytest.mark.parametrize(
    'group', ['evil group', 'org..evil', 'org/evil', '../evil', 'evil.']
)
def test_a_group_that_is_not_a_maven_group_falls_back_to_the_known_groups(
    releases, tmp_path, group
):
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1')
    ad_pom = pom_xml(
        PLUGINS, 'active-directory', '2.0', dependencies=[(group, 'mailer', '534.v1')]
    )
    http = RecordingHttp()
    stager, _ = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
        poms={'active-directory': ad_pom},
        http=http,
    )
    assert set(stager.staging.files) == {
        path(PLUGINS, 'mailer', '534.v1', 'hpi'),
        path(PLUGINS, 'mailer', '534.v1', 'pom'),
    }
    assert stager.problems == 0
    assert http.urls
    assert [
        requested
        for requested in http.urls
        if not requested.startswith(f'{url}org/jenkins-ci/plugins/mailer/')
    ] == []


@pytest.mark.parametrize(
    'entry', ['../evil:1.0', 'evil/name:1.0', 'nothing:1/../2', 'nothing:', ':1.0']
)
def test_a_dependency_entry_that_is_not_a_plugin_name_and_version_is_a_problem(
    releases, tmp_path, entry
):
    # the entry comes from a downloaded archive's manifest, so it is untrusted
    root, url = releases
    release(root, PLUGINS, 'mailer', '534.v1', requires=f'{entry},display-url-api:2.0')
    release(root, PLUGINS, 'display-url-api', '2.0')
    http = RecordingHttp()
    stager, out = run(
        tmp_path,
        url,
        listing=repository((PLUGINS, 'active-directory', '2.0')),
        details=[missing('active-directory', '2.0', 'mailer', '534.v1')],
        http=http,
    )
    name, _, version = entry.partition(':')
    assert stager.problems == 1
    assert (
        f'FAILED    {name} {version}  (not a plugin name and version '
        '(required by mailer 534.v1))'
    ) in out.getvalue().splitlines()
    # the valid entry beside it is still staged, and no request names the bad one
    assert path(PLUGINS, 'display-url-api', '2.0', 'hpi') in stager.staging.files
    assert len(stager.staging.files) == 4
    assert [
        requested for requested in http.urls if 'evil' in requested or '..' in requested
    ] == []
