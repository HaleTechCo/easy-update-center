"""Unit tests for uc.aql.

Maven path parsing, listing classification and the AQL query sent to Artifactory.
"""

import base64
import json

import pytest

from aqlfixture import UPSTREAM_QUERY
from httpstub import StubServer
from uc import aql
from uc.aql import (
    AqlError,
    Gav,
    RepoFile,
    classify,
    query_repository,
    to_gav,
)


@pytest.mark.parametrize(
    ('path', 'name', 'expected'),
    [
        (
            'org/jenkins-ci/plugins/git/5.2.0',
            'git-5.2.0.hpi',
            Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi'),
        ),
        (
            'org/jenkins-ci/main/jenkins-war/2.528.1',
            'jenkins-war-2.528.1.war',
            Gav('org.jenkins-ci.main', 'jenkins-war', '2.528.1', 'war'),
        ),
        (
            'io/jenkins/plugins/foo/1.0',
            'foo-1.0.pom',
            Gav('io.jenkins.plugins', 'foo', '1.0', 'pom'),
        ),
        ('plugins/git/5.2.0', 'git.hpi', None),
        # the name matches the path: only the path is too shallow for a group
        ('git/5.2.0', 'git-5.2.0.hpi', None),
        ('org/x/git/5.2.0', 'git-5.2.0-sources.hpi', None),
        # the name matches the path: only the space is not allowed
        ('org/x/g it/5.2.0', 'g it-5.2.0.hpi', None),
    ],
)
def test_to_gav(path, name, expected):
    assert to_gav(RepoFile(path, name)) == expected


def test_maven_path_and_with_extension():
    gav = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')
    assert gav.maven_path == 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi'
    assert (
        gav.with_extension('pom').maven_path
        == 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    )


def test_classify():
    listing = classify(
        [
            RepoFile('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.hpi'),
            RepoFile('org/jenkins-ci/plugins/git/5.2.0', 'git-5.2.0.pom'),
            RepoFile('org/jenkins-ci/plugins/lonely/1.0', 'lonely-1.0.hpi'),
            RepoFile(
                'org/jenkins-ci/main/jenkins-war/2.528.1', 'jenkins-war-2.528.1.war'
            ),
            RepoFile(
                'org/jenkins-ci/main/jenkins-war/2.528.1', 'jenkins-war-2.528.1.pom'
            ),
            RepoFile('org/jenkins-ci/main/jenkins-war/2.540', 'jenkins-war-2.540.war'),
            # right artifact in another group, and right group, another artifact
            RepoFile('org/other/jenkins-war/1.0', 'jenkins-war-1.0.war'),
            RepoFile('org/other/jenkins-war/1.0', 'jenkins-war-1.0.pom'),
            RepoFile('org/jenkins-ci/main/other-war/1.0', 'other-war-1.0.war'),
            RepoFile('org/jenkins-ci/main/other-war/1.0', 'other-war-1.0.pom'),
            RepoFile('plugins/git/5.2.0', 'git.hpi'),
        ]
    )
    assert [g.artifact_id for g in listing.plugins] == ['git']
    assert [g.maven_path for g in listing.plugins_without_pom] == [
        'org/jenkins-ci/plugins/lonely/1.0/lonely-1.0.hpi'
    ]
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
    routes = {
        ('POST', '/artifactory/api/search/aql'): (
            200,
            {'Content-Type': 'application/json'},
            json.dumps(payload).encode(),
        )
    }
    with StubServer(routes) as stub:
        files = query_repository(
            stub.url + '/artifactory/api/', 'jenkins', 'uc', 'secret'
        )
    assert files == [
        RepoFile(
            'org/jenkins-ci/plugins/git/5.2.0',
            'git-5.2.0.hpi',
            'ab',
            'cd',
            10,
            '2026-09-01T00:00:00.000Z',
        )
    ]
    _, _, headers, body = stub.requests[0]
    assert (
        headers['Authorization'] == 'Basic ' + base64.b64encode(b'uc:secret').decode()
    )
    assert body.decode() == UPSTREAM_QUERY


def test_an_aql_row_that_is_not_an_object_is_refused():
    routes = {('POST', '/artifactory/api/search/aql'): (200, {}, b'{"results": [1]}')}
    with StubServer(routes) as stub, pytest.raises(AqlError, match='not an object'):
        query_repository(stub.url + '/artifactory/api/', 'jenkins', 'uc', 'secret')


def test_an_aql_answer_that_is_not_json_is_refused():
    # a login page or proxy error behind a 200
    routes = {('POST', '/api/search/aql'): (200, {}, b'<html>sign in</html>')}
    with (
        StubServer(routes) as stub,
        pytest.raises(AqlError, match=r'^AQL query to .* failed: ') as caught,
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize('size', ['"abc"', '[1]', '1e999'])
def test_an_aql_row_with_a_size_that_is_not_a_number_is_refused(size):
    row = {'path': 'p', 'name': 'n', 'size': json.loads(size)}
    body = json.dumps({'results': [row]}).encode()
    routes = {('POST', '/api/search/aql'): (200, {}, body)}
    with (
        StubServer(routes) as stub,
        pytest.raises(AqlError, match=r'^AQL response has a result whose size is '),
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')


@pytest.mark.parametrize(
    ('field', 'value'),
    [('path', None), ('path', 1), ('name', None), ('name', [])],
)
def test_an_aql_row_with_a_path_or_name_that_is_not_text_is_refused(field, value):
    row = {'path': 'p', 'name': 'n', field: value}
    body = json.dumps({'results': [row]}).encode()
    routes = {('POST', '/api/search/aql'): (200, {}, body)}
    with (
        StubServer(routes) as stub,
        pytest.raises(AqlError, match=f'whose {field} is not text'),
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')


@pytest.mark.parametrize('body', [b'{}', b'[]', b'{"results": null}', b'"x"'])
def test_an_aql_answer_without_a_results_list_is_refused(body):
    routes = {('POST', '/api/search/aql'): (200, {}, body)}
    with (
        StubServer(routes) as stub,
        pytest.raises(AqlError, match=r'^AQL response has no results list$'),
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')


def test_query_repository_http_error():
    routes = {('POST', '/api/search/aql'): (403, {}, b'forbidden')}
    with (
        StubServer(routes) as stub,
        pytest.raises(AqlError, match='HTTP 403: forbidden'),
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'uc', 'secret')


def test_query_repository_rejects_odd_repository_names():
    with pytest.raises(AqlError, match='repository name'):
        query_repository('http://127.0.0.1:1/api/', 'a"b', 'u', 'p')


def test_the_aql_query_keeps_the_credentials_off_another_host():
    results = {'results': [{'path': 'org/x/1', 'name': 'x-1.pom', 'size': 3}]}
    with StubServer(
        {('GET', '/api/search/aql'): (200, {}, json.dumps(results).encode())}
    ) as other:
        elsewhere = other.url.replace('127.0.0.1', 'localhost') + '/api/search/aql'
        with StubServer(
            {('POST', '/api/search/aql'): (302, {'Location': elsewhere}, b'')}
        ) as repository:
            files = query_repository(repository.url + '/api/', 'jenkins', 'svc', 'pw')
    assert [f.name for f in files] == ['x-1.pom']
    _method, _path, headers, _body = other.requests[0]
    assert 'Authorization' not in headers


def test_an_aql_answer_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(aql, 'LISTING_LIMIT', 10)
    with (
        StubServer(
            {('POST', '/api/search/aql'): (200, {}, b'{"results": []}' * 2)}
        ) as stub,
        pytest.raises(aql.AqlError, match='larger than 10 bytes'),
    ):
        query_repository(stub.url + '/api/', 'jenkins', 'svc', 'pw')


@pytest.mark.parametrize('pattern', [aql._ALLOWED, aql._REPOSITORY])  # noqa: SLF001
def test_the_aql_name_patterns_refuse_a_trailing_newline(pattern):
    assert pattern.match('plugins-release')
    assert not pattern.match('plugins-release\n')
