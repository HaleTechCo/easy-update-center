"""web.env: the web container's settings, worked out by uc (docs/reference/cli.md#uc-config)."""

import os
import re
import stat
from pathlib import Path

import pytest

from uc.cli import main
from uc.config import NO_CREDENTIALS, load_settings, load_web_settings
from uc.errors import ConfigError
from uc.webenv import WEB_SETTINGS, WebEnvWriteError, web_env, write_web_env

WEB_FIELDS = ('public_url', 'download_backend', 'download_backend_url', 'download_access', 'download_proxy_auth_file', 'download_proxy_ca_file', 'tls_cert_file', 'tls_key_file')


def test_online_web_settings():
    text = web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'}))
    assert text == ('PUBLIC_URL=https://uc.example.com\nDOWNLOAD_BACKEND=mirror\nDOWNLOAD_BACKEND_URL=https://get.jenkins.io\nDOWNLOAD_ACCESS=redirect\nDOWNLOAD_PROXY_AUTH_FILE=/run/secrets/download_proxy_auth\nDOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt\nTLS_CERT_FILE=\nTLS_KEY_FILE=\n')


def test_the_artifact_keeper_backend_url_is_worked_out_for_web():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'http://keeper.internal/', 'REPO_KEY': 'jenkins'}
    assert 'DOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=http://keeper.internal/maven/jenkins\n' in web_env(load_web_settings(env))


def test_a_value_on_two_lines_is_refused():
    with pytest.raises(ConfigError, match='TLS_CERT_FILE must be on one line'):
        web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'TLS_CERT_FILE': 'a\nb'}))


def test_write_web_env_is_atomic_and_readable_by_web(tmp_path):
    out = tmp_path / 'uc-web' / 'web.env'
    write_web_env('PUBLIC_URL=https://uc\n', out)
    assert out.read_text(encoding='utf-8') == 'PUBLIC_URL=https://uc\n'
    assert out.stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in out.parent.iterdir()) == ['web.env']


def test_web_env_is_world_readable_whatever_the_umask_and_the_file_it_replaces(tmp_path):
    out = tmp_path / 'web.env'
    out.write_text('PUBLIC_URL=https://old\n', encoding='utf-8')
    out.chmod(0o600)
    umask = os.umask(0o077)
    try:
        write_web_env('PUBLIC_URL=https://uc\n', out)
    finally:
        os.umask(umask)
    assert out.read_text(encoding='utf-8') == 'PUBLIC_URL=https://uc\n'
    assert stat.S_IMODE(out.stat().st_mode) == 0o644


def test_a_failed_replace_is_reported_and_removes_the_temporary_file(tmp_path, monkeypatch):
    out = tmp_path / 'web.env'
    out.mkdir()  # the temporary file is written beside it, then cannot replace a directory
    replace = Path.replace
    written = []

    def watched_replace(self, target):
        written.append((self.parent, self.read_text(encoding='utf-8')))
        return replace(self, target)

    monkeypatch.setattr(Path, 'replace', watched_replace)
    with pytest.raises(WebEnvWriteError, match=rf'^cannot write {re.escape(str(out))}: Is a directory$'):
        write_web_env('PUBLIC_URL=https://uc\n', out)
    assert written == [(tmp_path, 'PUBLIC_URL=https://uc\n')]
    assert [path.name for path in tmp_path.iterdir()] == ['web.env']
    assert out.is_dir()


def test_invalid_generator_only_settings_do_not_stop_web():
    env = {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/', 'RESOURCES_SOURCE': 'gihub', 'RETAIN_RELEASES': '0'}
    assert web_env(load_web_settings(env)) == web_env(load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}))
    with pytest.raises(ConfigError, match='RESOURCES_SOURCE'):
        load_settings(env)


def test_an_explicit_backend_url_needs_no_repository_settings():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'DOWNLOAD_BACKEND_URL': 'http://backend/repo/'}
    assert 'DOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=http://backend/repo\n' in web_env(load_web_settings(env))


def test_airgapped_without_a_backend_url_or_repository_is_refused():
    with pytest.raises(ConfigError, match='REPO_URL and REPO_KEY must be set'):
        load_web_settings({'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc'})


@pytest.mark.parametrize('load', [load_web_settings, load_settings])
def test_credentials_in_the_repository_url_are_refused(load):
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'https://u:p@artifactory.corp/artifactory/', 'REPO_KEY': 'jenkins'}  # pragma: allowlist secret
    with pytest.raises(ConfigError, match=r'^REPO_URL must not contain credentials or an @; use the secret files instead$'):
        load(env)


def test_an_invalid_download_access_still_fails():
    with pytest.raises(ConfigError, match='DOWNLOAD_ACCESS must be one of'):
        load_web_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'DOWNLOAD_ACCESS': 'tunnel'})


AGREEMENT_ENVS = {
    'online': {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'},
    'airgapped artifactory': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'http://art.internal/artifactory', 'REPO_KEY': 'jenkins'},
    'airgapped artifact keeper': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'http://keeper.internal', 'REPO_KEY': 'jenkins'},
    'proxy and tls': {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'http://art.internal/artifactory/', 'REPO_KEY': 'jenkins', 'DOWNLOAD_ACCESS': 'proxy', 'SECRETS_DIR': '/pki', 'DOWNLOAD_PROXY_CA_FILE': '/tls/root.crt', 'TLS_CERT_FILE': '/tls/c.pem', 'TLS_KEY_FILE': '/tls/k.pem'},
}


@pytest.mark.parametrize('env', AGREEMENT_ENVS.values(), ids=AGREEMENT_ENVS.keys())
def test_web_settings_agree_with_the_full_settings(env):
    web, full = load_web_settings(env), load_settings(env)
    assert {name: getattr(web, name) for name in WEB_FIELDS} == {name: getattr(full, name) for name in WEB_FIELDS}


def test_the_entrypoint_reads_exactly_the_web_settings():
    script = Path('/web/entrypoint.sh').read_text(encoding='utf-8')
    names = re.search(r'case "\$\{name\}" in\n\s*([A-Z_]+(?: \| [A-Z_]+)*)\)', script)
    assert names, 'the reader loop in web/entrypoint.sh has no case pattern of names'
    assert tuple(names.group(1).split(' | ')) == WEB_SETTINGS


def test_the_entrypoint_refuses_an_at_in_the_backend_url_with_ucs_message():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins', 'DOWNLOAD_BACKEND_URL': 'https://u@backend.example'}
    with pytest.raises(ConfigError) as refused:
        load_web_settings(env)
    message = str(refused.value)
    assert message.startswith(f'DOWNLOAD_BACKEND_URL {NO_CREDENTIALS}; ')
    assert f'die "{message}"' in Path('/web/entrypoint.sh').read_text(encoding='utf-8')


@pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() == 0, reason='root can write to a read-only directory')
def test_an_unwritable_web_env_directory_is_reported_and_leaves_no_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('MODE', 'online')
    monkeypatch.setenv('PUBLIC_URL', 'https://uc.example.com')
    out_dir = tmp_path / 'locked'
    out_dir.mkdir()
    out_dir.chmod(0o555)
    try:
        assert main(['config', '--web-env', '--out', str(out_dir / 'web.env')]) == 1
        assert 'uc: error: cannot write' in capsys.readouterr().err
        assert list(out_dir.iterdir()) == []
    finally:
        out_dir.chmod(0o755)


CREDENTIAL_ERROR = 'DOWNLOAD_BACKEND_URL must not contain credentials'
CREDENTIAL_URL = 'https://user:pw@art/repo'  # pragma: allowlist secret


@pytest.mark.parametrize('load', [load_web_settings, load_settings])
def test_credentials_in_the_backend_url_are_refused(load):
    env = {'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'DOWNLOAD_BACKEND_URL': CREDENTIAL_URL}
    with pytest.raises(ConfigError, match=CREDENTIAL_ERROR):
        load(env)


def test_web_env_refuses_a_credentialed_backend_url_at_the_command_line(monkeypatch, capsys):
    monkeypatch.setenv('MODE', 'online')
    monkeypatch.setenv('PUBLIC_URL', 'https://uc')
    monkeypatch.setenv('DOWNLOAD_BACKEND_URL', CREDENTIAL_URL)
    assert main(['config', '--web-env']) == 1
    assert 'uc: error: ' + CREDENTIAL_ERROR in capsys.readouterr().err
