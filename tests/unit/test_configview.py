"""uc config: each setting's value and source, secret files only as present or not, and the reload hint (docs/reference/cli.md#uc-config)."""

import io
import json
import os

import pytest

from uc.config import load_settings, settings_digest
from uc.configview import run


def env_for(tmp_path, secrets_dir, **extra):
    return {
        'MODE': 'airgapped',
        'PUBLIC_URL': 'https://uc.example.com',
        'REPO_URL': 'http://artifactory:8081/artifactory/',
        'REPO_KEY': 'jenkins',
        'SECRETS_DIR': str(secrets_dir),
        'UC_DATA_DIR': str(tmp_path / 'data'),
        **extra,
    }


def show(env, **options):
    out, err = io.StringIO(), io.StringIO()
    code = run(env=env, out=out, err=err, **options)
    return code, out.getvalue(), err.getvalue()


def test_json_rows_give_each_value_and_its_source(tmp_path, secrets_dir):
    code, out, _ = show(env_for(tmp_path, secrets_dir, RETAIN_RELEASES='7'), as_json=True)
    assert code == 0
    rows = {row['env']: row for row in json.loads(out)}
    assert rows['MODE'] == {'env': 'MODE', 'value': 'airgapped', 'source': 'env', 'note': '', 'restart': True}
    assert rows['RETAIN_RELEASES'] == {'env': 'RETAIN_RELEASES', 'value': '7', 'source': 'env', 'note': '', 'restart': False}
    assert (rows['CORE_AGE_DAYS']['value'], rows['CORE_AGE_DAYS']['source']) == ('0', 'mode')
    assert (rows['REPO_ADAPTER']['value'], rows['REPO_ADAPTER']['source']) == ('true', 'derived')
    assert (rows['UC_ID']['value'], rows['UC_ID']['source']) == ('default', 'default')
    assert (rows['SIGNING_KEY_FILE']['source'], rows['SIGNING_KEY_FILE']['note']) == ('derived', 'present')
    assert rows['GITHUB_TOKEN_FILE']['note'] == 'missing'
    assert rows['UC_IMAGE'] == {'env': 'UC_IMAGE', 'value': 'easy-update-center/uc:local', 'source': 'default', 'note': '', 'restart': False}


def test_text_groups_the_settings(tmp_path, secrets_dir):
    code, out, _ = show(env_for(tmp_path, secrets_dir, RETAIN_RELEASES='7'))
    assert code == 0
    assert 'Settings file' not in out
    assert '\nGeneration\n' in out
    assert '  RETAIN_RELEASES = 7  (environment' in out
    assert '  MODE = airgapped  (environment; restart)\n' in out
    assert f'  SIGNING_KEY_FILE = {secrets_dir}/update-center.key  (derived; present)\n' in out


def test_secret_contents_are_never_shown(tmp_path, secrets_dir):
    (secrets_dir / 'repo_password').write_text('hunter2-not-shown\n')
    _, text, _ = show(env_for(tmp_path, secrets_dir))
    _, as_json, _ = show(env_for(tmp_path, secrets_dir), as_json=True)
    assert 'hunter2-not-shown' not in text + as_json


def test_check(tmp_path, secrets_dir):
    assert show(env_for(tmp_path, secrets_dir), check=True) == (0, 'configuration ok\n', '')


def test_an_invalid_configuration_still_shows_what_it_can(tmp_path, secrets_dir):
    code, out, err = show(env_for(tmp_path, secrets_dir, RETAIN_RELEASES='0'))
    assert code == 1
    assert '  RETAIN_RELEASES = 0  (environment)\n' in out
    assert 'uc: error: RETAIN_RELEASES must be >= 1 (got 0)' in err


def test_the_reload_hint(tmp_path, secrets_dir):
    env = env_for(tmp_path, secrets_dir)
    state = tmp_path / 'data' / 'scheduler.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'started_at': '2026-10-03T08:00:00Z', 'settings_digest': 'older'}), encoding='utf-8')
    _, out, _ = show(env)
    assert 'note: the scheduler started at 2026-10-03T08:00:00Z with different settings (a change in .env, or in uc.toml to a setting marked restart); to apply them, run: docker compose up -d --force-recreate (make reload)' in out
    state.write_text(json.dumps({'started_at': 'x', 'settings_digest': settings_digest(load_settings(env), env)}), encoding='utf-8')
    _, out, _ = show(env)
    assert 'note:' not in out


def test_credentials_in_an_invalid_url_are_not_shown(tmp_path, secrets_dir):
    env = env_for(tmp_path, secrets_dir, REPO_URL='https://admin:hunter2@artifactory:8081/artifactory/')
    code, out, _ = show(env)
    assert code == 1
    assert 'REPO_URL = https://***@artifactory:8081/artifactory/' in out
    assert 'hunter2' not in out


def test_credentials_in_a_proxy_are_not_shown(tmp_path, secrets_dir):
    env = env_for(tmp_path, secrets_dir, HTTPS_PROXY='http://user:s3cret@proxy:3128')
    for options in ({}, {'as_json': True}):
        code, out, _ = show(env, **options)
        assert code == 0
        assert 's3cret' not in out
        assert 'http://***@proxy:3128' in out


def test_credentials_in_a_proxy_without_a_scheme_are_not_shown(tmp_path, secrets_dir):
    env = env_for(tmp_path, secrets_dir, HTTPS_PROXY='proxyuser:s3cret@proxy:3128')
    for options in ({}, {'as_json': True}):
        code, out, _ = show(env, **options)
        assert code == 0
        assert 'proxyuser' not in out
        assert 's3cret' not in out
        assert '***@proxy:3128' in out


@pytest.mark.parametrize('delimiter', ['#', '?', '/', '[', ']'])
@pytest.mark.parametrize(('form', 'shown'), [('http://proxyuser:{}@proxy.corp:3128', 'http://***@proxy.corp:3128'), ('proxyuser:{}@proxy.corp:3128', '***@proxy.corp:3128')], ids=['scheme', 'no-scheme'])  # pragma: allowlist secret
def test_a_proxy_password_that_holds_a_url_delimiter_loads_and_is_not_shown(tmp_path, secrets_dir, delimiter, form, shown):
    value = form.format(f'S3cret{delimiter}Tail9z')
    env = env_for(tmp_path, secrets_dir, HTTPS_PROXY=value)
    assert show(env, check=True) == (0, 'configuration ok\n', '')
    _, text, _ = show(env)
    _, as_json, _ = show(env, as_json=True)
    assert f'  HTTPS_PROXY = {shown}  (' in text
    assert {row['env']: row['value'] for row in json.loads(as_json)}['HTTPS_PROXY'] == shown
    for part in ('proxyuser', 'S3cret', 'Tail9z'):
        assert part not in text + as_json


@pytest.mark.parametrize('delimiter', ['#', '?', '/'])
def test_credentials_that_end_the_host_early_in_an_invalid_url_are_not_shown(tmp_path, secrets_dir, delimiter):
    env = env_for(tmp_path, secrets_dir, REPO_URL=f'https://admin:S3cret{delimiter}Tail9z@artifactory:8081/artifactory/')  # pragma: allowlist secret
    code, out, err = show(env)
    assert code == 1
    assert 'REPO_URL = https://***@artifactory:8081/artifactory/' in out
    for part in ('S3cret', 'Tail9z'):
        assert part not in out + err


def test_check_fails_on_a_bad_doctor_since(tmp_path, secrets_dir):
    code, _, err = show(env_for(tmp_path, secrets_dir, DOCTOR_SINCE='latest'), check=True)
    assert code == 1
    assert 'DOCTOR_SINCE must be a Jenkins version' in err


def test_restart_settings_are_marked_in_the_text_and_the_json(tmp_path, secrets_dir):
    env = env_for(tmp_path, secrets_dir)
    _, text, _ = show(env)
    assert '  PUBLIC_URL = https://uc.example.com  (environment; restart)\n' in text
    assert '  RETAIN_RELEASES = 5  (default)\n' in text
    _, out, _ = show(env, as_json=True)
    by_name = {row['env']: row for row in json.loads(out)}
    assert by_name['PUBLIC_URL']['restart'] is True
    assert by_name['GENERATE_SCHEDULE']['restart'] is True
    assert by_name['RETAIN_RELEASES']['restart'] is False
    assert by_name['COMPOSE_FILE']['restart'] is False


@pytest.mark.parametrize('options', [{'as_json': True}, {'check': True}])
def test_an_invalid_configuration_prints_nothing_on_stdout_with_json_or_check(tmp_path, secrets_dir, options):
    code, out, err = show(env_for(tmp_path, secrets_dir, RETAIN_RELEASES='0'), **options)
    assert (code, out) == (1, '')
    assert 'uc: error: RETAIN_RELEASES must be >= 1 (got 0)' in err


def test_an_empty_secret_file_is_noted(tmp_path, secrets_dir):
    (secrets_dir / 'repo_password').write_text('\n')
    _, out, _ = show(env_for(tmp_path, secrets_dir))
    assert f'  REPO_PASSWORD_FILE = {secrets_dir / "repo_password"}  (derived; empty; restart)\n' in out


def test_an_unreadable_secret_file_is_noted(tmp_path, secrets_dir):
    if os.geteuid() == 0:
        pytest.skip('root reads any file')
    secret = secrets_dir / 'repo_password'
    secret.chmod(0)
    try:
        _, out, _ = show(env_for(tmp_path, secrets_dir))
    finally:
        secret.chmod(0o600)
    assert f'  REPO_PASSWORD_FILE = {secret}  (derived; unreadable; restart)\n' in out
