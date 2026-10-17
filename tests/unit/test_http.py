"""Unit tests for uc.http.

Redirects, the User-Agent and error wrapping of the GET helper.
"""

import socket
import threading

import pytest

from httpstub import StubServer
from uc import http
from uc.http import HttpError, get


def test_get_follows_redirects():
    routes = {
        ('GET', '/start'): (307, {'Location': '/final'}, b''),
        ('GET', '/final'): (200, {}, b'payload'),
    }
    with StubServer(routes) as stub:
        assert get(stub.url + '/start') == b'payload'
        assert stub.requests[0][2]['User-Agent'].startswith('easy-update-center/')


def test_get_wraps_errors():
    with (
        StubServer({}) as stub,
        pytest.raises(HttpError, match=r'GET .*/missing failed: HTTP Error 404'),
    ):
        get(stub.url + '/missing')


def test_get_keeps_an_authorization_header_off_another_host():
    with StubServer({('GET', '/x'): (200, {}, b'ok')}) as other:
        elsewhere = other.url.replace('127.0.0.1', 'localhost') + '/x'
        with StubServer(
            {('GET', '/start'): (302, {'Location': elsewhere}, b'')}
        ) as first:
            assert http.get(first.url + '/start', {'Authorization': 'token t'}) == b'ok'
    assert 'Authorization' not in other.requests[0][2]


def test_an_unreadable_ca_file_is_an_http_error(tmp_path):
    with pytest.raises(http.HttpError, match='cannot be read'):
        http.Http(tmp_path / 'missing.crt')


@pytest.mark.parametrize(
    ('old', 'new', 'expected'),
    [
        ('https://art.corp/a', 'https://art.corp/b', True),
        ('https://art.corp:8443/a', 'https://art.corp:8443/b', True),
        ('http://art.corp/a', 'https://art.corp/b', True),  # an upgrade, default ports
        ('https://art.corp/a', 'https://art.corp:9443/b', False),  # another port
        ('http://art.corp:8081/a', 'https://art.corp:8443/b', False),
        ('https://art.corp/a', 'https://s3.example.com/b', False),  # another host
        ('https://art.corp/a', 'http://art.corp/b', False),  # in the clear
    ],
)
def test_credentials_follow_a_redirect_only_within_one_origin(old, new, expected):
    assert http.forwards_credentials(old, new) is expected


def test_get_turns_a_non_http_answer_into_an_http_error():
    server = socket.create_server(('127.0.0.1', 0))

    def answer():
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b'garbage')

    thread = threading.Thread(target=answer)
    thread.start()
    try:
        with pytest.raises(HttpError, match=r'GET .* failed'):
            get(f'http://127.0.0.1:{server.getsockname()[1]}/x')
    finally:
        thread.join()
        server.close()
