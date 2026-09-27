"""Shared unit-test fixtures: a session-wide signing PKI and a per-test secrets directory."""

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
    """Serve a fake repo.jenkins-ci.org: files put under root at url, with generated .sha1 files."""
    base = tmp_path / 'source'
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), keeper_handler(base, 'unused', 'unused', anonymous_read=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield base / 'releases', f'http://127.0.0.1:{httpd.server_address[1]}/maven/releases/'
    httpd.shutdown()
    httpd.server_close()
