"""Unit tests for uc.repo_adapter.backends.

Artifactory and Artifact Keeper listings, reads, redirects and TLS trust.
"""

import base64
import datetime as dt
import hashlib
import io
import json
import socket
import threading
import zipfile

import pytest

from archivefixture import make_hpi, make_jar
from httpstub import StubServer, serve_bytes
from uc.aql import AQL_TEMPLATE
from uc.http import Http, HttpError, forwards_credentials
from uc.repo_adapter.backends import (
    ArtifactKeeperBackend,
    ArtifactoryBackend,
    BackendError,
    RepoEntry,
    artifactory_time,
)

UTC = dt.UTC
GIT = ('org.jenkins-ci.plugins', 'git', '5.2.0')
HPI_PATH = 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi'
BASIC = 'Basic ' + base64.b64encode(b'svc:pw').decode()


def artifactory(stub, max_archive=1024 * 1024):
    return ArtifactoryBackend(
        Http(None),
        stub.url + '/artifactory/',
        stub.url + '/artifactory/api/',
        'jenkins',
        username='svc',
        password='pw',
        max_archive_bytes=max_archive,
    )


def keeper(stub, tmp_path, max_archive=1024 * 1024):
    return ArtifactKeeperBackend(
        Http(None),
        stub.url + '/',
        'jenkins-prod-local',
        username='zac',
        token='tok',
        tmp_dir=tmp_path / 'tmp',
        max_archive_bytes=max_archive,
    )


def entry(data, path=HPI_PATH):
    directory, _, name = path.rpartition('/')
    return RepoEntry(
        directory,
        name,
        hashlib.sha256(data).hexdigest(),
        None,
        len(data),
        '2026-09-22T23:09:48.946Z',
        '2026-09-22T23:09:48.946Z',
    )


def test_artifactory_time():
    assert (
        artifactory_time(dt.datetime(2026, 9, 22, 23, 9, 48, 946941, tzinfo=UTC))
        == '2026-09-22T23:09:48.946Z'
    )


def test_artifactory_listing_sends_the_upstream_query_with_basic_auth():
    results = [
        {
            'repo': 'jenkins',
            'path': 'org/jenkins-ci/plugins/git/5.2.0',
            'name': 'git-5.2.0.hpi',
            'created': '2026-09-01T00:00:00.000Z',
            'modified': '2026-09-02T00:00:00.000Z',
            'sha256': 'a' * 64,
            'actual_sha1': 'b' * 40,
            'size': 12,
        }
    ]
    routes = {
        ('POST', '/artifactory/api/search/aql'): (
            200,
            {},
            json.dumps({'results': results}).encode(),
        )
    }
    with StubServer(routes) as stub:
        listed = artifactory(stub).list()
    assert listed == [
        RepoEntry(
            'org/jenkins-ci/plugins/git/5.2.0',
            'git-5.2.0.hpi',
            'a' * 64,
            'b' * 40,
            12,
            '2026-09-01T00:00:00.000Z',
            '2026-09-02T00:00:00.000Z',
        )
    ]
    _method, _path, headers, body = stub.requests[0]
    assert body == (AQL_TEMPLATE % 'jenkins').encode()
    assert headers['Authorization'] == BASIC


def test_artifactory_listing_failures_are_backend_errors():
    with (
        StubServer(
            {('POST', '/artifactory/api/search/aql'): (403, {}, b'denied')}
        ) as stub,
        pytest.raises(BackendError, match='HTTP 403'),
    ):
        artifactory(stub).list()


def test_artifactory_files_entries_and_sha1():
    routes = {
        ('GET', '/artifactory/jenkins/org/x/1/x-1.pom'): (200, {}, b'<project/>'),
        ('GET', f'/artifactory/jenkins/{HPI_PATH}!/META-INF/MANIFEST.MF'): (
            200,
            {},
            b'Manifest-Version: 1.0',
        ),
    }
    with StubServer(routes) as stub:
        backend = artifactory(stub)
        assert backend.get('org/x/1/x-1.pom') == b'<project/>'
        assert backend.get('org/x/1/missing.pom') is None
        assert (
            backend.entry(HPI_PATH, 'META-INF/MANIFEST.MF', 100)
            == b'Manifest-Version: 1.0'
        )
        assert backend.entry(HPI_PATH, 'index.jelly', 100) is None
        assert (
            backend.sha1(RepoEntry('d', 'n.hpi', 'a' * 64, 'c' * 40, 1, '', ''))
            == 'c' * 40
        )
        with pytest.raises(BackendError, match='no sha1'):
            backend.sha1(RepoEntry('d', 'n.hpi', 'a' * 64, None, 1, '', ''))


def test_artifactory_facts_use_entries_and_a_ranged_central_directory():
    data = make_hpi(*GIT, date=(2023, 11, 18, 20, 14, 0))
    prefix = f'/artifactory/jenkins/{HPI_PATH}'
    routes = {('GET', prefix): serve_bytes(data)}
    for name in (
        'META-INF/MANIFEST.MF',
        'META-INF/maven/org.jenkins-ci.plugins/git/pom.xml',
    ):
        routes[('GET', f'{prefix}!/{name}')] = (
            200,
            {},
            zipfile.ZipFile(io.BytesIO(data)).read(name),
        )
    routes[('GET', f'{prefix}!/WEB-INF/lib/git.jar')] = (
        200,
        {},
        make_jar(b'<div>From the jar.</div>'),
    )
    with StubServer(routes) as stub:
        facts = artifactory(stub).facts(entry(data), GIT, plugin=True)
    assert facts.coordinates == GIT
    assert facts.description == b'<div>From the jar.</div>'
    assert facts.released == dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC)
    ranged = [r for r in stub.requests if r[1] == prefix]
    assert all('Range' in headers for _, _, headers, _ in ranged)
    assert len(ranged) <= 4  # a probe and a block or two, never the whole archive


def test_artifactory_without_range_support_gives_no_date():
    data = make_hpi(*GIT)
    prefix = f'/artifactory/jenkins/{HPI_PATH}'
    with StubServer({('GET', prefix): serve_bytes(data, ranges=False)}) as stub:
        assert artifactory(stub).archive_released(entry(data)) is None


def test_protocol_errors_are_backend_errors():
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)

    def reply():
        connection, _ = listener.accept()
        connection.recv(65536)
        connection.sendall(
            b'garbage\r\n\r\n'
        )  # http.client.BadStatusLine, which is not an OSError
        connection.close()

    threading.Thread(target=reply, daemon=True).start()
    try:
        with pytest.raises(HttpError, match='failed'):
            Http(None).request(
                f'http://127.0.0.1:{listener.getsockname()[1]}/x',
                headers={},
                limit=1000,
            )
    finally:
        listener.close()


def test_a_private_ca_is_added_to_the_system_bundle(pki_material):
    """An Artifactory behind a private CA may still redirect to object storage.

    That object storage has a public certificate.
    """
    system = len(Http(None).context.get_ca_certs())
    assert system > 0
    assert len(Http(pki_material / 'root-ca.crt').context.get_ca_certs()) == system + 1


@pytest.mark.parametrize(
    ('old', 'new', 'expected'),
    [
        ('https://art.corp/a', 'https://art.corp/b', True),
        ('http://art.corp/a', 'https://art.corp/b', True),  # an upgrade
        ('https://art.corp/a', 'https://s3.example.com/b', False),  # another host
        (
            'https://art.corp/a',
            'http://art.corp/b',
            False,
        ),  # a downgrade would send them in the clear
    ],
)
def test_which_redirects_keep_the_credentials(old, new, expected):
    assert forwards_credentials(old, new) is expected


def test_redirects_to_another_host_drop_the_credentials():
    with StubServer({('GET', '/blob'): (200, {}, b'<project/>')}) as storage:
        other_host = storage.url.replace('127.0.0.1', 'localhost') + '/blob'
        with StubServer(
            {
                ('GET', '/artifactory/jenkins/x/1/x-1.pom'): (
                    302,
                    {'Location': other_host},
                    b'',
                )
            }
        ) as stub:
            assert artifactory(stub).get('x/1/x-1.pom') == b'<project/>'
    assert stub.requests[0][2]['Authorization'] == BASIC
    assert 'Authorization' not in storage.requests[0][2]


def keeper_item(path, sha, size=10, created='2026-09-22T23:09:48.946941Z'):
    return {
        'id': 'x',
        'repository_key': 'jenkins-prod-local',
        'path': path,
        'name': 'org',
        'version': None,
        'size_bytes': size,
        'checksum_sha256': sha,
        'created_at': created,
    }


def test_keeper_listing_follows_cursors_with_a_bearer_token(tmp_path):
    base = '/api/v1/repositories/jenkins-prod-local/artifacts?per_page=100'
    first = {
        'items': [
            keeper_item(HPI_PATH, 'a' * 64),
            keeper_item('org/x/1/x-1.jar', 'b' * 64),
            keeper_item('org/x/1/x-1.txt', 'c' * 64),
        ],
        'pagination': {'page': 1, 'per_page': 100, 'total': 4, 'total_pages': 2},
        'next_cursor': 'abc',
        'has_more': True,
    }
    second = {
        'items': [keeper_item('org/x/1/x-1.pom', 'd' * 64)],
        'pagination': {'page': 2, 'per_page': 100, 'total': 4, 'total_pages': 2},
        'has_more': False,
    }
    routes = {
        ('GET', base + '&page=1'): (200, {}, json.dumps(first).encode()),
        ('GET', base + '&cursor=abc'): (200, {}, json.dumps(second).encode()),
    }
    with StubServer(routes) as stub:
        listed = keeper(stub, tmp_path).list()
    assert [e.path for e in listed] == [HPI_PATH, 'org/x/1/x-1.jar', 'org/x/1/x-1.pom']
    assert listed[0].created == listed[0].modified == '2026-09-22T23:09:48.946Z'
    assert listed[0].sha1 is None
    assert all(
        headers['Authorization'] == 'Bearer tok' for _, _, headers, _ in stub.requests
    )


def test_keeper_listing_falls_back_to_page_numbers(tmp_path):
    base = '/api/v1/repositories/jenkins-prod-local/artifacts?per_page=100'
    pages = {
        1: [keeper_item(HPI_PATH, 'a' * 64)],
        2: [keeper_item('org/x/1/x-1.pom', 'd' * 64)],
    }
    routes = {
        ('GET', f'{base}&page={n}'): (
            200,
            {},
            json.dumps(
                {
                    'items': items,
                    'pagination': {
                        'page': n,
                        'per_page': 100,
                        'total': 2,
                        'total_pages': 2,
                    },
                }
            ).encode(),
        )
        for n, items in pages.items()
    }
    with StubServer(routes) as stub:
        assert len(keeper(stub, tmp_path).list()) == 2


def test_keeper_files_use_basic_auth_and_sha1_side_files(tmp_path):
    routes = {
        ('GET', '/maven/jenkins-prod-local/org/x/1/x-1.pom'): (200, {}, b'<project/>'),
        ('GET', f'/maven/jenkins-prod-local/{HPI_PATH}.sha1'): (
            200,
            {},
            b'C' * 40 + b'  git-5.2.0.hpi\n',
        ),
        ('GET', '/maven/jenkins-prod-local/org/x/1/x-1.hpi.sha1'): (
            200,
            {},
            b'<html>404</html>',
        ),
    }
    with StubServer(routes) as stub:
        backend = keeper(stub, tmp_path)
        assert backend.get('org/x/1/x-1.pom') == b'<project/>'
        assert backend.sha1(entry(b'x')) == 'c' * 40
        with pytest.raises(BackendError, match='no valid sha1'):
            backend.sha1(entry(b'x', 'org/x/1/x-1.hpi'))
    assert (
        stub.requests[0][2]['Authorization']
        == 'Basic ' + base64.b64encode(b'zac:tok').decode()
    )


def test_keeper_facts_download_the_archive_once(tmp_path):
    data = make_hpi(*GIT, layout='classes', date=(2019, 12, 19, 10, 8, 0))
    with StubServer(
        {('GET', f'/maven/jenkins-prod-local/{HPI_PATH}'): (200, {}, data)}
    ) as stub:
        facts = keeper(stub, tmp_path).facts(entry(data), GIT, plugin=True)
    assert facts.description == b'<div>An excellent plugin.</div>'
    assert facts.released == dt.datetime(2019, 12, 19, 10, 8, tzinfo=UTC)
    assert len(stub.requests) == 1
    assert list((tmp_path / 'tmp').iterdir()) == []  # the archive itself is not kept


def test_keeper_refuses_archives_over_the_cap(tmp_path):
    data = make_hpi(*GIT)
    with (
        StubServer(
            {('GET', f'/maven/jenkins-prod-local/{HPI_PATH}'): (200, {}, data)}
        ) as stub,
        pytest.raises(HttpError, match='larger than 100 bytes'),
    ):
        keeper(stub, tmp_path, max_archive=100).facts(entry(data), GIT, plugin=True)
