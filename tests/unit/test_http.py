import pytest
from httpstub import StubServer

from uc.http import HttpError, get


def test_get_follows_redirects():
    routes = {
        ('GET', '/start'): (307, {'Location': '/final'}, b''),
        ('GET', '/final'): (200, {}, b'payload'),
    }
    with StubServer(routes) as stub:
        assert get(stub.url + '/start') == b'payload'
        assert stub.requests[0][2]['User-Agent'].startswith('jenkins-uc/')


def test_get_wraps_errors():
    with StubServer({}) as stub:
        with pytest.raises(HttpError, match='GET .*/missing failed: HTTP Error 404'):
            get(stub.url + '/missing')
