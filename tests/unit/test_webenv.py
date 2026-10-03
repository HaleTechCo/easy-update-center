"""web.env: the web container's settings, worked out by uc (docs/reference/cli.md#uc-config)."""

from pathlib import Path

import pytest

from uc.config import load_settings, load_web_settings
from uc.errors import ConfigError
from uc.webenv import web_env, write_web_env

NO_FILE = Path('/nonexistent/uc.toml')
WEB_FIELDS = ('public_url', 'download_backend', 'download_backend_url', 'download_access', 'download_proxy_auth_file', 'download_proxy_ca_file', 'tls_cert_file', 'tls_key_file')


def test_online_web_settings():
    text = web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'}, config_file=NO_FILE))
    assert text == ('PUBLIC_URL=https://uc.example.com\nDOWNLOAD_BACKEND=mirror\nDOWNLOAD_BACKEND_URL=https://get.jenkins.io\nDOWNLOAD_ACCESS=redirect\nDOWNLOAD_PROXY_AUTH_FILE=/run/secrets/download_proxy_auth\nDOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt\nTLS_CERT_FILE=\nTLS_KEY_FILE=\n')


def test_the_artifact_keeper_backend_url_is_worked_out_for_web():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'http://keeper.internal/', 'REPO_KEY': 'jenkins'}
    assert 'DOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=http://keeper.internal/maven/jenkins\n' in web_env(load_web_settings(env, config_file=NO_FILE))


def test_a_value_on_two_lines_is_refused(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[tls]\ncert_file = "a\\nb"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='TLS_CERT_FILE must be on one line'):
        web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc'}, config_file=path))


def test_write_web_env_is_atomic_and_readable_by_web(tmp_path):
    out = tmp_path / 'uc-web' / 'web.env'
    write_web_env('PUBLIC_URL=https://uc\n', out)
    assert out.read_text(encoding='utf-8') == 'PUBLIC_URL=https://uc\n'
    assert out.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in out.parent.iterdir()) == ['web.env']


def test_invalid_generator_only_settings_do_not_stop_web():
    env = {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/', 'RESOURCES_SOURCE': 'gihub', 'RETAIN_RELEASES': '0'}
    assert web_env(load_web_settings(env, config_file=NO_FILE)) == web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}, config_file=NO_FILE))
    with pytest.raises(ConfigError, match='RESOURCES_SOURCE'):
        load_settings(env, config_file=NO_FILE)


def test_an_explicit_backend_url_needs_no_repository_settings():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'DOWNLOAD_BACKEND_URL': 'http://backend/repo/'}
    assert 'DOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=http://backend/repo\n' in web_env(load_web_settings(env, config_file=NO_FILE))


def test_airgapped_without_a_backend_url_or_repository_is_refused():
    with pytest.raises(ConfigError, match='REPO_URL and REPO_KEY must be set'):
        load_web_settings({'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc'}, config_file=NO_FILE)


@pytest.mark.parametrize('load', [load_web_settings, load_settings])
def test_credentials_in_the_repository_url_are_refused(load):
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'https://u:p@artifactory.corp/artifactory/', 'REPO_KEY': 'jenkins'}  # pragma: allowlist secret
    with pytest.raises(ConfigError, match=r'^URLs must not contain credentials; use the secret files instead$'):
        load(env, config_file=NO_FILE)


def test_an_invalid_download_access_still_fails():
    with pytest.raises(ConfigError, match='DOWNLOAD_ACCESS must be one of'):
        load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'DOWNLOAD_ACCESS': 'tunnel'}, config_file=NO_FILE)


AGREEMENT_ENVS = {
    'online': {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'},
    'airgapped artifactory': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'http://art.internal/artifactory', 'REPO_KEY': 'jenkins'},
    'airgapped artifact keeper': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'http://keeper.internal', 'REPO_KEY': 'jenkins'},
    'proxy and tls': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'http://art.internal/artifactory/', 'REPO_KEY': 'jenkins', 'DOWNLOAD_ACCESS': 'proxy', 'SECRETS_DIR': '/pki', 'DOWNLOAD_PROXY_CA_FILE': '/tls/root.crt', 'TLS_CERT_FILE': '/tls/c.pem', 'TLS_KEY_FILE': '/tls/k.pem'},
}


@pytest.mark.parametrize('env', AGREEMENT_ENVS.values(), ids=AGREEMENT_ENVS.keys())
def test_web_settings_agree_with_the_full_settings(env):
    web, full = load_web_settings(env, config_file=NO_FILE), load_settings(env, config_file=NO_FILE)
    assert {name: getattr(web, name) for name in WEB_FIELDS} == {name: getattr(full, name) for name in WEB_FIELDS}
