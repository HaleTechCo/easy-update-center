"""Unit tests for uc.preflight: PUBLIC_URL checks, signing material checks and credential loading."""

import datetime as dt
import shutil

import pytest

from uc.pki import init_pki
from uc.preflight import (
    PreflightError,
    check_public_url,
    check_signing,
    load_credentials,
    load_repository_credentials,
)
from ucsupport import make_settings

NOW = dt.datetime.now(dt.UTC)


@pytest.mark.parametrize('url', ['https://uc.example.com', 'https://uc.example.com:8443', 'http://web:8080'])
def test_bare_origins_are_accepted(url):
    check_public_url(url, allow_local=False)


def test_plain_http_warns():
    assert check_public_url('http://web:8080', allow_local=False) == ['PUBLIC_URL uses plain http; signatures protect integrity, but TLS is recommended']
    assert check_public_url('https://uc.example.com', allow_local=False) == []


@pytest.mark.parametrize(
    ('url', 'message'),
    [
        ('https://host.example.com/jenkins', 'bare origin'),
        ('https://host.example.com/?x=1', 'bare origin'),
        ('ftp://host', 'http\\(s\\) URL'),
        ('https://user:pw@host', 'credentials'),  # pragma: allowlist secret
        ('http://localhost:8080', 'cannot reach'),
        ('http://127.0.0.1:8080', 'cannot reach'),
    ],
)
def test_bad_public_urls_are_rejected(url, message):
    with pytest.raises(PreflightError, match=message):
        check_public_url(url, allow_local=False)


@pytest.mark.parametrize('url', ['ftp://u:S3cretPW@host', 'https://u:S3cretPW@host/path', 'https://S3cretPW#x@host/'])  # pragma: allowlist secret
def test_credentials_in_the_public_url_are_refused_before_a_message_could_quote_them(url):
    with pytest.raises(PreflightError, match=r'^PUBLIC_URL must not contain credentials or an @$') as error:
        check_public_url(url, allow_local=False)
    assert 'S3cretPW' not in str(error.value)


def test_local_urls_allowed_for_tests():
    check_public_url('http://localhost:8080', allow_local=True)


def test_check_signing_ok(tmp_path, secrets_dir):
    info = check_signing(make_settings(tmp_path, secrets_dir), NOW)
    assert 'Signing' in info.subject


def test_check_signing_missing_file(tmp_path, secrets_dir):
    (secrets_dir / 'root-ca.crt').unlink()
    with pytest.raises(PreflightError, match='missing signing material'):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_check_signing_expiring_cert(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, CERTIFICATE_MINIMUM_VALID_DAYS='50')
    with pytest.raises(PreflightError, match=r'expires in 4[45] days \(minimum 50\)'):
        check_signing(settings, NOW)


def test_check_signing_key_mismatch(tmp_path, secrets_dir):
    other = tmp_path / 'other-pki'
    init_pki(other, root_days=10, signing_days=5)
    shutil.copy(other / 'update-center.key', secrets_dir / 'update-center.key')
    with pytest.raises(PreflightError, match='does not match'):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_check_signing_wrong_root(tmp_path, secrets_dir):
    other = tmp_path / 'other-pki'
    init_pki(other, root_days=10, signing_days=5)
    shutil.copy(other / 'root-ca.crt', secrets_dir / 'root-ca.crt')
    with pytest.raises(PreflightError, match='is not issued by'):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_load_credentials_with_github_placeholder(tmp_path, secrets_dir):
    creds = load_credentials(make_settings(tmp_path, secrets_dir))
    assert (creds.repo_username, creds.repo_password) == ('uc', 'secret')
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == ('uc', 'uc', True)


def test_load_credentials_with_github_token(tmp_path, secrets_dir):
    (secrets_dir / 'github_username').write_text('bot\n')
    (secrets_dir / 'github_token').write_text('ghp_x\n')
    creds = load_credentials(make_settings(tmp_path, secrets_dir))
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == ('bot', 'ghp_x', False)


def test_load_credentials_requires_the_repository_account(tmp_path, secrets_dir):
    (secrets_dir / 'repo_password').write_text('')
    with pytest.raises(PreflightError, match='Repository credentials missing'):
        load_credentials(make_settings(tmp_path, secrets_dir))


def test_load_credentials_uses_the_adapter_token_when_the_adapter_is_on(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    (secrets_dir / 'repo_password').write_text('')  # the adapter holds these, not uc
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_token_file.write_text('adapter-token\n')
    creds = load_credentials(settings)
    assert (creds.repo_username, creds.repo_password) == ('uc', 'adapter-token')


def test_load_credentials_explains_a_missing_adapter_token(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    with pytest.raises(PreflightError, match=r'repo-adapter token missing: .*repo-adapter.token'):
        load_credentials(settings)


def test_load_repository_credentials(tmp_path, secrets_dir):
    assert load_repository_credentials(make_settings(tmp_path, secrets_dir)) == ('uc', 'secret')
