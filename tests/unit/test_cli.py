"""Unit tests for uc.cli: exit codes, status output, rollback, the run lock and the pki subcommands."""

import json
from pathlib import Path

import pytest

from sitefixture import uc_document, write_site
from uc.cli import EXIT_LOCKED, main
from uc.publish import RunLock
from uc.status import read_status, write_status


def use_env(monkeypatch, tmp_path, secrets_dir):
    values = {
        'MODE': 'airgapped',
        'PUBLIC_URL': 'https://uc.example.com',
        'REPO_URL': 'http://artifactory:8081/artifactory/',
        'REPO_KEY': 'jenkins',
        'REPO_ADAPTER': 'off',
        'SECRETS_DIR': str(secrets_dir),
        'UC_DATA_DIR': str(tmp_path / 'data'),
        'UC_OVERLAY_DIR': str(tmp_path / 'overlay'),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path / 'data'


def test_status_prints_json(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    write_status(data, {'live_release': 'x'})
    assert main(['status']) == 0
    assert json.loads(capsys.readouterr().out) == {'live_release': 'x'}


def test_configuration_errors_exit_1(monkeypatch, capsys):
    monkeypatch.setenv('MODE', 'bogus')
    assert main(['status']) == 1
    assert 'uc: error: MODE must be one of' in capsys.readouterr().err


def test_rollback_updates_status(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ('20260922T010000Z', '20260922T020000Z'):
        write_site(data / 'releases' / rid / 'current', uc_document('https://uc.example.com'))
    (data / 'live').symlink_to('releases/20260922T020000Z')
    assert main(['rollback']) == 0
    assert '20260922T010000Z' in capsys.readouterr().out
    assert read_status(data)['live_release'] == '20260922T010000Z'
    assert read_status(data)['plugin_count'] == 2


def test_generate_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    data.mkdir(parents=True)
    with RunLock(data):
        assert main(['generate']) == EXIT_LOCKED


def test_rollback_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ('20260922T010000Z', '20260922T020000Z'):
        write_site(data / 'releases' / rid / 'current', uc_document('https://uc.example.com'))
    (data / 'live').symlink_to('releases/20260922T020000Z')
    with RunLock(data):
        assert main(['rollback']) == EXIT_LOCKED
    assert read_status(data).get('live_release') != '20260922T010000Z'
    assert (data / 'live').readlink() == Path('releases/20260922T020000Z')


def test_repo_adapter_refuses_to_start_without_repository_credentials(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_ADAPTER', 'on')
    (secrets_dir / 'repo_password').write_text('')
    assert main(['repo-adapter', '--port', '0']) == 1
    assert 'uc: error: Repository credentials missing' in capsys.readouterr().err


def test_pki_init_and_show(tmp_path, capsys):
    out = tmp_path / 'pki'
    assert main(['pki', 'init', '--out', str(out), '--root-days', '30', '--signing-days', '10']) == 0
    assert 'root-ca.crt' in capsys.readouterr().out
    assert main(['pki', 'show', '--dir', str(out)]) == 0
    shown = capsys.readouterr().out
    assert 'update-center.cert: CN=Jenkins Update Center Signing' in shown
    assert 'root-ca.crt: CN=Jenkins Update Center Root CA' in shown


def test_doctor_downloads_are_refused_air_gapped_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['doctor', '--download-poms', '--staging', str(tmp_path / 'stage')]) == 1
    assert 'uc: error: downloads need repo.jenkins-ci.org' in capsys.readouterr().err
    assert not (tmp_path / 'stage').exists()


def test_doctor_refuses_a_bad_war_version_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert main(['doctor', '--download-war', '2.479.x']) == 1
    assert '--download-war takes a Jenkins version' in capsys.readouterr().err


def test_doctor_refuses_a_bad_since_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['doctor', '--since', 'two']) == 1
    assert '--since takes a Jenkins version' in capsys.readouterr().err


@pytest.mark.parametrize('extra', [['--download-poms'], ['--download-war', '2.479.1'], ['--fetch'], ['--json'], ['--since', '2.479.1']])
def test_push_runs_on_its_own(extra, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', '--push', *extra])
    assert exit_info.value.code == 2
    assert '--push runs on its own' in capsys.readouterr().err


@pytest.mark.parametrize('extra', [['--download-poms'], ['--download-war', '2.479.1'], ['--fetch'], ['--json'], ['--since', '2.479.1']])
def test_stage_local_runs_on_its_own(extra, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', '--stage-local', *extra])
    assert exit_info.value.code == 2
    assert '--stage-local runs on its own' in capsys.readouterr().err


@pytest.mark.parametrize('extra', [['--download-poms'], ['--download-war', '2.479.1'], ['--fetch'], ['--json'], ['--since', '2.479.1']])
def test_clean_runs_on_its_own(extra, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', '--clean', *extra])
    assert exit_info.value.code == 2
    assert '--clean runs on its own' in capsys.readouterr().err


@pytest.mark.parametrize(('flags', 'message'), [(['--push', '--stage-local'], '--push runs on its own'), (['--push', '--clean'], '--push runs on its own'), (['--stage-local', '--clean'], '--stage-local runs on its own')])
def test_push_stage_local_and_clean_are_separate_runs(flags, message, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', *flags])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_clean_dry_run_on_an_empty_folder(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['doctor', '--clean', '--dry-run', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == ['0 removed, 0 would remove, 0 kept, 0 problem(s) -> http://artifactory:8081/artifactory/jenkins/']


def test_stage_local_needs_neither_a_source_nor_the_repository(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)  # air-gapped, no PROCUREMENT_SOURCE_URL, an unreachable repository
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['doctor', '--stage-local', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == [f'0 recorded, 0 refused; review {staging}, then run uc doctor --push']


def test_fix_needs_stage_local(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', '--fix'])
    assert exit_info.value.code == 2
    assert '--fix only applies to --stage-local' in capsys.readouterr().err


def test_dry_run_needs_push(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['doctor', '--dry-run'])
    assert exit_info.value.code == 2
    assert '--dry-run only applies to --push and --clean' in capsys.readouterr().err


def test_push_with_nothing_staged_exits_1(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['doctor', '--push', '--staging', str(tmp_path / 'empty')]) == 1
    assert 'uc: error: nothing to push' in capsys.readouterr().err
