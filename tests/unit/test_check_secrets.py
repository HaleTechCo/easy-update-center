"""The committed-secret checker.

What it flags, what it lets through as a placeholder, and that it never echoes a secret.
"""

import importlib.util
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location(
    'check_secrets', TOOLS / 'check_secrets.py'
)
secrets_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secrets_tool)

# built from parts so this file holds no credential-shaped text of its own
REAL_PASSWORD = 'Xk9fQ2mZ7pLrTw4v'
# the scheme apart from the rest, so no line here is itself a credentialed URL
URL = 'https' + '://'
API_KEY = 'AKCp' + 'a1B2c3D4e5' * 7


def scan(tmp_path, text):
    path = tmp_path / 'sample.txt'
    path.write_text(text, encoding='utf-8')
    return list(secrets_tool.scan_file(str(path)))


def test_a_credentialed_url_is_caught_and_its_password_masked(tmp_path):
    found = scan(tmp_path, f'REPO_URL={URL}svc:{REAL_PASSWORD}@repo.example.com/\n')
    assert found == [(1, 'credentialed URL', f'{URL}svc:***@')]


@pytest.mark.parametrize(
    'password', ['<token>', '{SECRET}', '***', 'token', 'S3CRET', 'v3ndorS3cret']
)
def test_a_placeholder_password_is_not_a_finding(tmp_path, password):
    assert scan(tmp_path, f'see {URL}user:{password}@host/x\n') == []


def test_a_real_secret_that_merely_starts_with_a_placeholder_word_is_caught(tmp_path):
    found = scan(tmp_path, f'{URL}svc:passwXk9fQ2mZ7pLr@host/\n')
    assert [label for _, label, _ in found] == ['credentialed URL']


def test_a_placeholder_does_not_hide_a_real_credential_on_the_same_line(tmp_path):
    found = scan(
        tmp_path,
        f'{URL}user:<token>@a/ then {URL}svc:{REAL_PASSWORD}@b/\n',
    )
    assert [label for _, label, _ in found] == ['credentialed URL']


def test_a_url_without_a_password_is_not_a_finding(tmp_path):
    assert scan(tmp_path, f'{URL}user@host/ and {URL}host:8443/x\n') == []


def test_the_allowlist_pragma_skips_its_line(tmp_path):
    line = f'{URL}svc:{REAL_PASSWORD}@host/  # pragma: allowlist secret\n'
    assert scan(tmp_path, line) == []


def test_a_vendor_token_is_caught_and_cut_short(tmp_path):
    found = scan(tmp_path, f'key = {API_KEY}\n')
    assert found == [(1, 'Artifactory API key', API_KEY[:12] + '...')]


def test_main_exits_1_with_the_location_and_never_prints_the_secret(tmp_path, capsys):
    path = tmp_path / 'compose.yaml'
    path.write_text(f'a: 1\nurl: {URL}svc:{REAL_PASSWORD}@host/\n', encoding='utf-8')
    assert secrets_tool.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert f'{path}:2: possible credentialed URL: {URL}svc:***@' in out
    assert REAL_PASSWORD not in out


def test_main_exits_0_for_a_clean_file(tmp_path, capsys):
    path = tmp_path / 'clean.txt'
    path.write_text('nothing to see\n', encoding='utf-8')
    assert secrets_tool.main([str(path)]) == 0
    assert capsys.readouterr().out == ''
