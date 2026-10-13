"""Unit tests for uc.preflight.

PUBLIC_URL checks, signing material checks and credential loading.
"""

import datetime as dt
import os
import re
import secrets
import shutil
import threading

import pytest

from uc.pki import init_pki
from uc.preflight import (
    ADAPTER_ENTRIES,
    PreflightError,
    check_data_dir,
    check_public_url,
    check_signing,
    load_credentials,
    load_repository_credentials,
)
from ucsupport import make_settings

NOW = dt.datetime.now(dt.UTC)


@pytest.mark.parametrize(
    'url', ['https://uc.example.com', 'https://uc.example.com:8443', 'http://web:8080']
)
def test_bare_origins_are_accepted(url):
    check_public_url(url, allow_local=False)


def test_plain_http_warns():
    (warning,) = check_public_url('http://web:8080', allow_local=False)
    assert warning.startswith('PUBLIC_URL uses plain http: ')
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


@pytest.mark.parametrize(
    'url',
    [
        'ftp://u:S3cretPW@host',  # pragma: allowlist secret
        'https://u:S3cretPW@host/path',  # pragma: allowlist secret
        'https://S3cretPW#x@host/',
    ],
)
def test_credentials_in_the_public_url_are_refused_before_a_message_could_quote_them(
    url,
):
    with pytest.raises(
        PreflightError, match=r'^PUBLIC_URL must not contain credentials or an @$'
    ) as error:
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
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == (
        'uc',
        'uc',
        True,
    )


def test_load_credentials_with_github_token(tmp_path, secrets_dir):
    (secrets_dir / 'github_username').write_text('bot\n')
    (secrets_dir / 'github_token').write_text('ghp_x\n')
    creds = load_credentials(make_settings(tmp_path, secrets_dir))
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == (
        'bot',
        'ghp_x',
        False,
    )


def test_load_credentials_requires_the_repository_account(tmp_path, secrets_dir):
    (secrets_dir / 'repo_password').write_text('')
    with pytest.raises(PreflightError, match='Repository credentials missing'):
        load_credentials(make_settings(tmp_path, secrets_dir))


ADAPTER_TOKEN = secrets.token_hex(32)


def adapter_settings(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    (secrets_dir / 'repo_password').write_text('')  # the adapter holds these, not uc
    settings.repo_adapter_dir.mkdir(parents=True)
    return settings


def test_load_credentials_uses_the_adapter_token_when_the_adapter_is_on(
    tmp_path, secrets_dir
):
    settings = adapter_settings(tmp_path, secrets_dir)
    settings.repo_adapter_token_file.write_text(ADAPTER_TOKEN + '\n')
    creds = load_credentials(settings)
    assert (creds.repo_username, creds.repo_password) == ('uc', ADAPTER_TOKEN)


def test_load_credentials_explains_a_missing_adapter_token(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    with pytest.raises(
        PreflightError, match=r'repo-adapter token missing: .*repo-adapter.token'
    ):
        load_credentials(settings)


def test_a_symlinked_adapter_token_is_refused_even_when_it_looks_valid(
    tmp_path, secrets_dir
):
    # The adapter can write its volume: a link there could point at uc's own secrets,
    # which uc would then send the adapter as the repository password.
    settings = adapter_settings(tmp_path, secrets_dir)
    target = tmp_path / 'elsewhere'
    target.write_text(ADAPTER_TOKEN + '\n')
    settings.repo_adapter_token_file.symlink_to(target)
    with pytest.raises(PreflightError, match=r'repo-adapter token refused: .*link'):
        load_credentials(settings)


@pytest.mark.parametrize(
    'content',
    [
        'hunter2-not-a-token',
        ADAPTER_TOKEN.upper(),
        ADAPTER_TOKEN[:-1],
        ADAPTER_TOKEN + 'a',
        'g' * 64,
        ADAPTER_TOKEN + '\n' + ADAPTER_TOKEN,
        'a' * 200,
    ],
    ids=['words', 'upper-case', 'short', 'long', 'not-hex', 'two-lines', 'oversized'],
)
def test_an_adapter_token_that_is_not_64_hex_digits_is_refused_unquoted(
    tmp_path, secrets_dir, content
):
    settings = adapter_settings(tmp_path, secrets_dir)
    settings.repo_adapter_token_file.write_text(content + '\n')
    with pytest.raises(PreflightError, match='repo-adapter token refused') as caught:
        load_credentials(settings)
    assert content[:16] not in str(caught.value)


def test_a_fifo_adapter_token_is_refused_without_blocking(tmp_path, secrets_dir):
    settings = adapter_settings(tmp_path, secrets_dir)
    os.mkfifo(settings.repo_adapter_token_file)
    outcome = []

    def load():
        try:
            load_credentials(settings)
        except PreflightError as exc:
            outcome.append(str(exc))

    worker = threading.Thread(target=load, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), 'reading the token blocked on a FIFO'
    assert len(outcome) == 1
    assert 'not a regular file' in outcome[0]


def test_load_repository_credentials(tmp_path, secrets_dir):
    assert load_repository_credentials(make_settings(tmp_path, secrets_dir)) == (
        'uc',
        'secret',
    )


@pytest.mark.skipif(os.geteuid() == 0, reason='root can write anywhere')
def test_a_data_directory_this_uid_cannot_write_is_refused_naming_uc_uid(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    data.chmod(0o555)
    try:
        with pytest.raises(
            PreflightError,
            match=(
                rf'^{re.escape(str(data))} is not writable by uid {os.getuid()}: '
                r'.*UC_UID and UC_GID'
            ),
        ):
            check_data_dir(data)
    finally:
        data.chmod(0o755)


def test_a_writable_or_missing_data_directory_passes(tmp_path):
    check_data_dir(tmp_path)
    check_data_dir(tmp_path / 'not-yet')


def test_a_data_volume_whose_contents_belong_to_another_uid_is_refused(
    tmp_path, monkeypatch
):
    # A volume this release creates has a world-writable, sticky /data, so what the
    # first UC_UID owns is its contents.
    data = tmp_path / 'data'
    (data / 'releases').mkdir(parents=True)
    (data / 'status.json').write_text('{}')
    other = os.getuid() + 1
    monkeypatch.setattr(os, 'getuid', lambda: other)
    with pytest.raises(
        PreflightError,
        match=(
            rf'^{re.escape(str(data))} is not writable by uid {other}: '
            r'.*UC_UID and UC_GID'
        ),
    ):
        check_data_dir(data)


def test_entries_uc_never_writes_do_not_count(tmp_path, monkeypatch):
    # Such as the lost+found of a filesystem mounted at /data.
    data = tmp_path / 'data'
    (data / 'lost+found').mkdir(parents=True)
    other = os.getuid() + 1
    monkeypatch.setattr(os, 'getuid', lambda: other)
    check_data_dir(data)


def test_the_adapter_mount_point_on_the_data_volume_does_not_count(
    tmp_path, monkeypatch
):
    # Docker creates /data/repo-adapter, the mount point of the adapter's own volume,
    # as root inside uc-data: uc never owns it.
    data = tmp_path / 'data'
    (data / 'repo-adapter').mkdir(parents=True)
    other = os.getuid() + 1
    monkeypatch.setattr(os, 'getuid', lambda: other)
    check_data_dir(data)


def test_the_adapter_directory_is_checked_for_the_adapter_files(tmp_path, monkeypatch):
    adapter = tmp_path / 'repo-adapter'
    adapter.mkdir()
    (adapter / 'token').write_text('t\n')
    other = os.getuid() + 1
    monkeypatch.setattr(os, 'getuid', lambda: other)
    with pytest.raises(PreflightError, match='not writable by uid'):
        check_data_dir(adapter, ADAPTER_ENTRIES)
