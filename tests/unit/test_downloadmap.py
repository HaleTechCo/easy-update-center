"""Unit tests for uc.downloadmap.

Building, writing and reading the .htdownload-map RewriteMap.
"""

from uc.aql import RepoFile, classify
from uc.downloadmap import (
    build_download_map,
    preferred_groups,
    read_download_map,
    write_download_map,
)


def files(group_path, artifact, version, *extensions):
    return [
        RepoFile(f'{group_path}/{artifact}/{version}', f'{artifact}-{version}.{ext}')
        for ext in extensions
    ]


def test_map_covers_plugins_and_wars_with_poms():
    listing = classify(
        files('org/jenkins-ci/plugins', 'git', '5.2.0', 'hpi', 'pom')
        + files('org/jenkins-ci/main', 'jenkins-war', '2.528.1', 'war', 'pom')
        + files('org/jenkins-ci/plugins', 'lonely', '1.0', 'hpi')
    )
    mapping, warnings = build_download_map(listing, {})
    assert mapping == {
        'plugins/git/5.2.0': 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi',
        'war/2.528.1': (
            'org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war'
        ),
    }
    assert warnings == []


def test_group_conflict_prefers_the_offered_group():
    listing = classify(
        files('org/jenkins-ci/plugins', 'foo', '1.0', 'hpi', 'pom')
        + files('io/jenkins/plugins', 'foo', '1.0', 'hpi', 'pom')
    )
    mapping, warnings = build_download_map(listing, {'foo': 'io.jenkins.plugins'})
    assert mapping['plugins/foo/1.0'] == 'io/jenkins/plugins/foo/1.0/foo-1.0.hpi'
    assert warnings == [
        (
            'plugins/foo/1.0 exists under several groupIds '
            '(io.jenkins.plugins, org.jenkins-ci.plugins); using io.jenkins.plugins'
        )
    ]


def test_hpi_is_preferred_over_jpi():
    listing = classify(
        files('org/jenkins-ci/plugins', 'foo', '1.0', 'jpi', 'hpi', 'pom')
    )
    assert build_download_map(listing, {})[0]['plugins/foo/1.0'].endswith('foo-1.0.hpi')


def test_jpi_only_plugin_maps_to_jpi():
    listing = classify(files('org/jenkins-ci/plugins', 'old', '1.0', 'jpi', 'pom'))
    assert (
        build_download_map(listing, {})[0]['plugins/old/1.0']
        == 'org/jenkins-ci/plugins/old/1.0/old-1.0.jpi'
    )


def test_preferred_groups():
    doc = {
        'plugins': {
            'git': {'gav': 'org.jenkins-ci.plugins:git:5.2.0'},
            'odd': {'gav': 'broken'},
            'none': {},
        }
    }
    assert preferred_groups(doc) == {'git': 'org.jenkins-ci.plugins'}


def test_write_and_read_roundtrip(tmp_path):
    path = tmp_path / '.htdownload-map'
    write_download_map({'war/2.528.1': 'w.war', 'plugins/git/5.2.0': 'g.hpi'}, path)
    assert path.read_text() == 'plugins/git/5.2.0 g.hpi\nwar/2.528.1 w.war\n'
    assert read_download_map(path) == {
        'war/2.528.1': 'w.war',
        'plugins/git/5.2.0': 'g.hpi',
    }
    write_download_map({}, path)
    assert path.read_text() == ''
