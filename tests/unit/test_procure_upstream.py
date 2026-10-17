"""Unit tests for uc.procure.upstream.

Downloads by Maven path, each checked against its .sha1.
"""

import hashlib

import pytest

from httpstub import StubServer, free_port
from procurefixture import publish
from uc.http import Http
from uc.procure import ProcureError
from uc.procure.upstream import NotFoundError, Upstream, parse_sha1

POM = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.pom'
HPI = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.hpi'


def test_fetch_downloads_and_verifies(releases, tmp_path):
    root, url = releases
    publish(root, POM, b'<project/>')
    upstream = Upstream(url, Http(None), limit=1 << 20)
    assert upstream.fetch(POM, tmp_path / 'part') == (
        hashlib.sha1(b'<project/>', usedforsecurity=False).hexdigest(),
        10,
    )
    assert (tmp_path / 'part').read_bytes() == b'<project/>'
    assert upstream.url(POM) == url + POM


def test_a_file_the_source_lacks_is_not_found(releases, tmp_path):
    _, url = releases
    upstream = Upstream(url, Http(None), limit=1 << 20)
    assert upstream.sha1(POM) is None
    with pytest.raises(NotFoundError, match='not found'):
        upstream.fetch(POM, tmp_path / 'part')


def test_a_body_that_does_not_match_its_sha1_is_refused(tmp_path):
    routes = {
        ('GET', '/r/' + POM + '.sha1'): (200, {}, b'0' * 40),
        ('GET', '/r/' + POM): (200, {}, b'<project/>'),
    }
    with (
        StubServer(routes) as stub,
        pytest.raises(ProcureError, match=r'does not match its \.sha1'),
    ):
        Upstream(stub.url + '/r/', Http(None), limit=1 << 20).fetch(
            POM, tmp_path / 'part'
        )


def test_archives_over_the_limit_are_refused(releases, tmp_path):
    root, url = releases
    publish(root, HPI, b'x' * 2048)
    with pytest.raises(ProcureError, match='larger than 1024 bytes'):
        Upstream(url, Http(None), limit=1024).fetch(HPI, tmp_path / 'part')


def test_poms_are_capped_at_one_mebibyte_whatever_the_archive_limit(releases, tmp_path):
    root, url = releases
    publish(root, POM, b'<' * ((1 << 20) + 1))
    with pytest.raises(ProcureError, match='larger than 1048576 bytes'):
        Upstream(url, Http(None), limit=1 << 30).fetch(POM, tmp_path / 'part')


def test_a_server_error_is_not_read_as_absent():
    with (
        StubServer({('GET', '/r/' + POM + '.sha1'): (503, {}, b'')}) as stub,
        pytest.raises(ProcureError, match='HTTP 503'),
    ):
        Upstream(stub.url + '/r/', Http(None), limit=1 << 20).sha1(POM)


def test_a_source_that_cannot_be_reached_is_a_message_not_a_traceback():
    url = f'http://127.0.0.1:{free_port()}/r/'
    with pytest.raises(ProcureError, match=r'GET .*\.sha1 failed'):
        Upstream(url, Http(None), limit=1 << 20).sha1(POM)


def test_a_page_answering_200_for_a_sha1_is_named_as_not_a_sha1_file():
    # a login or error page behind a proxy answers 200 with HTML
    with (
        StubServer(
            {('GET', '/r/' + POM + '.sha1'): (200, {}, b'<html>sign in</html>')}
        ) as stub,
        pytest.raises(ProcureError) as caught,
    ):
        Upstream(stub.url + '/r/', Http(None), limit=1 << 20).sha1(POM)
    assert str(caught.value) == f'{stub.url}/r/{POM}.sha1 is not a sha1 file'


@pytest.mark.parametrize(
    'body',
    [
        b'3f786850e387550fdab836ed7e6dc881de23001b',
        b'3F786850E387550FDAB836ED7E6DC881DE23001B\n',
        b'3f786850e387550fdab836ed7e6dc881de23001b  mailer-534.v1.pom\n',
    ],
)
def test_sha1_files_in_every_common_form_are_read(body):
    assert parse_sha1(body) == '3f786850e387550fdab836ed7e6dc881de23001b'


@pytest.mark.parametrize('body', [b'', b'<html>404</html>', b'3f78'])
def test_anything_else_is_not_a_sha1_file(body):
    with pytest.raises(ProcureError, match='not a sha1 file'):
        parse_sha1(body)
