import base64
import json

import pytest

from httpstub import StubServer
from uc.aql import AQL_TEMPLATE, AqlError, Gav, RepoFile, classify, query_repository, to_gav


@pytest.mark.parametrize(
    ('path', 'name', 'expected'),
    [
        ('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.hpi', Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')),
        ('org/jenkins-ci/main/jenkins-war/2.528.1', 'jenkins-war-2.528.1.war', Gav('org.jenkins-ci.main', 'jenkins-war', '2.528.1', 'war')),
        ('io/jenkins/plugins/foo/1.0', 'foo-1.0.pom', Gav('io.jenkins.plugins', 'foo', '1.0', 'pom')),
        ('plugins/git/5.2.0', 'git.hpi', None),
        ('git', 'git-5.2.0.hpi', None),
        ('org/x/git/5.2.0', 'git-5.2.0-sources.hpi', None),
        ('org/x/git/5.2.0', 'git 5.2.0.hpi', None),
    ],
)
def test_to_gav(path, name, expected):
    assert to_gav(RepoFile(path, name)) == expected


def test_maven_path_and_with_extension():
    gav = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')
    assert gav.maven_path == 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi'
    assert gav.with_extension('pom').maven_path == 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'


def test_classify():
    listing = classify(
        [
            RepoFile('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.hpi'),
            RepoFile('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.pom'),
            RepoFile('org/jenkins-ci/plugins/lonely/1.0', 'lonely-1.0.hpi'),
            RepoFile('org/jenkins-ci/main/jenkins-war/2.528.1', 'jenkins-war-2.528.1.war'),
            RepoFile('org/jenkins-ci/main/jenkins-war/2.528.1', 'jenkins-war-2.528.1.pom'),
            RepoFile('org/jenkins-ci/main/jenkins-war/2.540', 'jenkins-war-2.540.war'),
            RepoFile('org/other/something-war/1.0', 'something-war-1.0.war'),
            RepoFile('org/other/something-war/1.0', 'something-war-1.0.pom'),
            RepoFile('plugins/git/5.2.0', 'git.hpi'),
        ]
    )
    assert [g.artifact_id for g in listing.plugins] == ['git']
    assert [g.maven_path for g in listing.plugins_without_pom] == ['org/jenkins-ci/plugins/lonely/1.0/lonely-1.0.hpi']
    assert [g.version for g in listing.wars] == ['2.528.1']
    assert [g.version for g in listing.wars_without_pom] == ['2.540']
    assert listing.rejected == (RepoFile('plugins/git/5.2.0', 'git.hpi'),)


def test_query_repository_sends_the_upstream_query():
    payload = {
        'results': [
            {
                'repo': 'jenkins',
                'path': 'org/jenkins-ci/plugins/git/5.2.0',
                'name': 'git-5.2.0.hpi',
                'sha256': 'ab',
                'actual_sha1': 'cd',
                'size': 10,
                'modified': '2026-09-01T00:00:00.000Z',
            }
        ]
    }
    routes = {('POST', '/artifactory/api/search/aql'): (200, {'Content-Type': 'application/json'}, json.dumps(payload).encode())}
    with StubServer(routes) as stub:
        files = query_repository(stub.url + '/artifactory/api/', 'jenkins', 'uc', 'secret')
    assert files == [RepoFile('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.hpi', 'ab', 'cd', 10, '2026-09-01T00:00:00.000Z')]
    _, _, headers, body = stub.requests[0]
    assert headers['Authorization'] == 'Basic ' + base64.b64encode(b'uc:secret').decode()
    assert body.decode() == AQL_TEMPLATE % 'jenkins'


def test_query_repository_http_error():
    routes = {('POST', '/api/search/aql'): (403, {}, b'forbidden')}
    with StubServer(routes) as stub, pytest.raises(AqlError, match='HTTP 403: forbidden'):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')


def test_query_repository_rejects_odd_repository_names():
    with pytest.raises(AqlError, match='repository name'):
        query_repository('http://127.0.0.1:1/api/', 'a"b', 'u', 'p')
