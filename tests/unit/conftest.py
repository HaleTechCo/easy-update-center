"""Shared unit-test fixtures.

A session-wide signing PKI, a per-test secrets directory, and fake repositories.
"""

import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from uc.pki import init_pki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'support'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'integration'))

from fake_artifact_keeper import make_handler as keeper_handler
from fake_artifactory import make_handler as artifactory_handler


def serve(handler):
    """Serve handler on a free local port in a background thread.

    Return the server and its URL.
    """
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(
        target=httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True
    ).start()
    return httpd, f'http://127.0.0.1:{httpd.server_address[1]}/'


def stop(httpd):
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope='session')
def pki_material(tmp_path_factory):
    directory = tmp_path_factory.mktemp('pki')
    init_pki(directory, root_days=60, signing_days=45)
    return directory


@pytest.fixture
def secrets_dir(tmp_path, pki_material):
    directory = tmp_path / 'secrets'
    directory.mkdir()
    for name in ('update-center.key', 'update-center.cert', 'root-ca.crt'):
        shutil.copy(pki_material / name, directory / name)
    (directory / 'repo_username').write_text('uc\n')
    (directory / 'repo_password').write_text('secret\n')
    return directory


@pytest.fixture
def releases(tmp_path):
    """Serve a fake repo.jenkins-ci.org.

    Files put under root at url, with generated .sha1 files.
    """
    base = tmp_path / 'source'
    httpd, url = serve(keeper_handler(base, 'unused', 'unused', anonymous_read=True))
    yield base / 'releases', url + 'maven/releases/'
    stop(httpd)


@pytest.fixture
def keeper(tmp_path):
    """Serve a fake Artifact Keeper that needs the account uc:tok.

    Yield the folder of its repository jenkins and its URL.
    """
    httpd, url = serve(
        keeper_handler(tmp_path / 'repo', 'uc', 'tok', anonymous_read=False)
    )
    yield tmp_path / 'repo' / 'jenkins', url
    stop(httpd)


@pytest.fixture
def artifactory(tmp_path):
    """Serve a fake Artifactory that needs the account uc:tok.

    Yield the folder of its repository jenkins and its URL.
    """
    httpd, url = serve(
        artifactory_handler(tmp_path / 'repo', 'uc:tok', anonymous_read=False)
    )
    yield tmp_path / 'repo' / 'jenkins', url + 'artifactory/'
    stop(httpd)


@pytest.fixture
def client_checksums(tmp_path):
    """Serve a fake Artifactory with the client checksum policy.

    That policy is the default for Artifactory's local repositories.
    """
    httpd, url = serve(
        artifactory_handler(
            tmp_path / 'repo', 'uc:tok', anonymous_read=False, checksum_policy='client'
        )
    )
    yield tmp_path / 'repo' / 'jenkins', url + 'artifactory/'
    stop(httpd)
