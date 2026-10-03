"""web.env: the web container's settings, worked out by uc (docs/reference/cli.md#uc-config)."""

from pathlib import Path

import pytest

from uc.config import load_settings
from uc.errors import ConfigError
from uc.webenv import web_env, write_web_env

NO_FILE = Path('/nonexistent/uc.toml')


def test_online_web_settings():
    text = web_env(load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'}, config_file=NO_FILE))
    assert text == ('PUBLIC_URL=https://uc.example.com\nDOWNLOAD_BACKEND=mirror\nDOWNLOAD_BACKEND_URL=https://get.jenkins.io\nDOWNLOAD_ACCESS=redirect\nDOWNLOAD_PROXY_AUTH_FILE=/run/secrets/download_proxy_auth\nDOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt\nTLS_CERT_FILE=\nTLS_KEY_FILE=\n')


def test_the_artifact_keeper_backend_url_is_worked_out_for_web():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'http://keeper.internal/', 'REPO_KEY': 'jenkins'}
    assert 'DOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=http://keeper.internal/maven/jenkins\n' in web_env(load_settings(env, config_file=NO_FILE))


def test_a_value_on_two_lines_is_refused(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[tls]\ncert_file = "a\\nb"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='TLS_CERT_FILE must be on one line'):
        web_env(load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc'}, config_file=path))


def test_write_web_env_is_atomic_and_readable_by_web(tmp_path):
    out = tmp_path / 'uc-web' / 'web.env'
    write_web_env('PUBLIC_URL=https://uc\n', out)
    assert out.read_text(encoding='utf-8') == 'PUBLIC_URL=https://uc\n'
    assert out.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in out.parent.iterdir()) == ['web.env']
