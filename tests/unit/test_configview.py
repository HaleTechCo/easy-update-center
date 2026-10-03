"""uc config: each setting's value and source, secret files only as present or not, and the reload hint (docs/reference/cli.md#uc-config)."""

import io
import json

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


def show(env, config_file, **options):
    out, err = io.StringIO(), io.StringIO()
    code = run(env=env, config_file=config_file, out=out, err=err, **options)
    return code, out.getvalue(), err.getvalue()


def settings_file(tmp_path, text='retain_releases = 7\n'):
    path = tmp_path / 'uc.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test_json_rows_give_each_value_and_its_source(tmp_path, secrets_dir):
    code, out, _ = show(env_for(tmp_path, secrets_dir), settings_file(tmp_path), as_json=True)
    assert code == 0
    rows = {row['env']: row for row in json.loads(out)}
    assert rows['MODE'] == {'env': 'MODE', 'key': 'mode', 'value': 'airgapped', 'source': 'env', 'note': ''}
    assert rows['RETAIN_RELEASES'] == {'env': 'RETAIN_RELEASES', 'key': 'retain_releases', 'value': '7', 'source': 'file', 'note': ''}
    assert (rows['CORE_AGE_DAYS']['value'], rows['CORE_AGE_DAYS']['source']) == ('0', 'mode')
    assert (rows['REPO_ADAPTER']['value'], rows['REPO_ADAPTER']['source']) == ('true', 'derived')
    assert (rows['UC_ID']['value'], rows['UC_ID']['source']) == ('default', 'default')
    assert (rows['SIGNING_KEY_FILE']['source'], rows['SIGNING_KEY_FILE']['note']) == ('derived', 'present')
    assert rows['GITHUB_TOKEN_FILE']['note'] == 'missing'
    assert rows['UC_IMAGE'] == {'env': 'UC_IMAGE', 'key': '', 'value': 'jenkins-uc/uc:local', 'source': 'default', 'note': ''}


def test_text_groups_the_settings_and_names_the_file(tmp_path, secrets_dir):
    path = settings_file(tmp_path)
    code, out, _ = show(env_for(tmp_path, secrets_dir), path)
    assert code == 0
    assert out.startswith(f'Settings file: {path} (read)\n')
    assert '\nGeneration\n' in out
    assert '  RETAIN_RELEASES = 7  (uc.toml: retain_releases)\n' in out
    assert '  MODE = airgapped  (environment)\n' in out
    assert f'  SIGNING_KEY_FILE = {secrets_dir}/update-center.key  (derived; present)\n' in out


def test_a_missing_file_is_named(tmp_path, secrets_dir):
    _, out, _ = show(env_for(tmp_path, secrets_dir), tmp_path / 'none.toml')
    assert out.startswith(f'Settings file: {tmp_path / "none.toml"} (not found: only the environment and the defaults apply)\n')


def test_secret_contents_are_never_shown(tmp_path, secrets_dir):
    (secrets_dir / 'repo_password').write_text('hunter2-not-shown\n')
    _, text, _ = show(env_for(tmp_path, secrets_dir), tmp_path / 'none.toml')
    _, as_json, _ = show(env_for(tmp_path, secrets_dir), tmp_path / 'none.toml', as_json=True)
    assert 'hunter2-not-shown' not in text + as_json


def test_check(tmp_path, secrets_dir):
    assert show(env_for(tmp_path, secrets_dir), tmp_path / 'none.toml', check=True) == (0, 'configuration ok\n', '')


def test_an_invalid_configuration_still_shows_what_it_can(tmp_path, secrets_dir):
    code, out, err = show(env_for(tmp_path, secrets_dir, RETAIN_RELEASES='0'), tmp_path / 'none.toml')
    assert code == 1
    assert '  RETAIN_RELEASES = 0  (environment)\n' in out
    assert 'uc: error: RETAIN_RELEASES must be >= 1 (got 0)' in err


def test_a_broken_file_is_reported(tmp_path, secrets_dir):
    code, out, err = show(env_for(tmp_path, secrets_dir), settings_file(tmp_path, 'mode = \n'))
    assert (code, out) == (1, '')
    assert 'is not valid TOML' in err


def test_the_reload_hint(tmp_path, secrets_dir):
    env, path = env_for(tmp_path, secrets_dir), tmp_path / 'none.toml'
    state = tmp_path / 'data' / 'scheduler.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'started_at': '2026-10-03T08:00:00Z', 'settings_digest': 'older'}), encoding='utf-8')
    _, out, _ = show(env, path)
    assert 'note: the scheduler started at 2026-10-03T08:00:00Z with other settings; to apply these, run: docker compose up -d --force-recreate (make reload)' in out
    state.write_text(json.dumps({'started_at': 'x', 'settings_digest': settings_digest(load_settings(env, config_file=path))}), encoding='utf-8')
    _, out, _ = show(env, path)
    assert 'note:' not in out
