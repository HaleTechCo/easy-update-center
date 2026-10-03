"""Unit tests for uc.cli: exit codes, status output, rollback, the run lock and the pki subcommands."""

import io
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sitefixture import uc_document, write_site
from uc import configview
from uc.cli import EXIT_LOCKED, build_parser, doctor_options, main
from uc.pki import cert_info
from uc.publish import RunLock
from uc.repo_adapter.server import record_port
from uc.schedule import run_schedule
from uc.settings import REGISTRY
from uc.status import read_status, write_status
from uc.timeutil import utcnow
from ucsupport import make_settings


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
        'UC_CONFIG_FILE': str(tmp_path / 'uc.toml'),
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


def test_commands_export_jvm_options_from_the_settings_file(monkeypatch, tmp_path, secrets_dir):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (tmp_path / 'uc.toml').write_text('java_tool_options = "-Xmx6g"\n', encoding='utf-8')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '')  # monkeypatch restores it after the test
    assert main(['status']) == 0
    assert os.environ['JAVA_TOOL_OPTIONS'] == '-Xmx6g'


def test_scheduled_jobs_read_jvm_options_and_proxies_from_the_settings_file_afresh(monkeypatch, tmp_path, secrets_dir):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (tmp_path / 'uc.toml').write_text('java_tool_options = "-Xmx6g"\n', encoding='utf-8')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '')  # monkeypatch restores it after the test
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.corp:3128')
    first_run, handed = {}, {}

    def schedule(settings, **options):
        run_schedule(settings, **options, generate_once=lambda: first_run.update(os.environ), exec_=lambda _file, _argv, env: handed.update(env), crontab=tmp_path / 'crontab')

    monkeypatch.setattr('uc.cli.run_schedule', schedule)
    assert main(['schedule']) == 0
    assert first_run['JAVA_TOOL_OPTIONS'] == '-Xmx6g'  # the scheduler's own first generation
    assert 'JAVA_TOOL_OPTIONS' not in handed  # supercronic's jobs read uc.toml again on each run
    assert handed['HTTPS_PROXY'] == 'http://proxy.corp:3128'


def test_the_scheduler_records_the_environment_it_received_not_what_uc_toml_exports(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    (tmp_path / 'uc.toml').write_text('java_tool_options = "-Xmx6g"\nhttps_proxy = "http://proxy.corp:3128"\n', encoding='utf-8')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '')  # monkeypatch restores both after apply_process_env exports the file's values
    monkeypatch.setenv('HTTPS_PROXY', '')
    environment = {name: value for name, value in os.environ.items() if name in REGISTRY}  # what the container was given
    monkeypatch.setattr('uc.cli.run_schedule', lambda settings, **options: run_schedule(settings, **options, generate_once=lambda: None, exec_=lambda *_: None, crontab=tmp_path / 'crontab'))
    assert main(['schedule']) == 0
    assert os.environ['JAVA_TOOL_OPTIONS'] == '-Xmx6g'  # exported, so a digest of os.environ taken now would differ and the note would fire forever
    assert (data / 'scheduler.json').is_file()
    out = io.StringIO()
    assert configview.run(env=environment, out=out, err=io.StringIO()) == 0
    assert 'note:' not in out.getvalue()


def test_config_check(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['config', '--check']) == 0
    assert capsys.readouterr().out == 'configuration ok\n'


def test_config_json_and_check_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['config', '--json', '--check'])
    assert exit_info.value.code == 2
    assert 'not allowed with argument' in capsys.readouterr().err


def test_config_web_env_writes_the_file(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    out = tmp_path / 'uc-web' / 'web.env'
    assert main(['config', '--web-env', '--out', str(out)]) == 0
    assert out.read_text(encoding='utf-8').startswith('PUBLIC_URL=https://uc.example.com\nDOWNLOAD_BACKEND=artifactory\n')
    assert capsys.readouterr().out.startswith(f'wrote {out}:\nPUBLIC_URL=')


@pytest.mark.parametrize(('argv', 'message'), [(['config', '--out', 'web.env'], '--out only applies to --web-env'), (['config', '--mode', 'online'], '--mode only applies to --example')])
def test_config_options_that_need_another(argv, message, capsys):
    with pytest.raises(SystemExit):
        main(argv)
    assert message in capsys.readouterr().err


def test_doctor_options_come_from_the_settings_unless_given(tmp_path, secrets_dir):
    parser = build_parser()
    configured = make_settings(tmp_path, secrets_dir, DOCTOR_FETCH='true', DOCTOR_JSON='yes', DOCTOR_SINCE='2.462.1')
    assert doctor_options(parser.parse_args(['doctor']), configured) == (True, True, '2.462.1')
    assert doctor_options(parser.parse_args(['doctor', '--no-fetch', '--no-json', '--since', '2.479.1']), configured) == (False, False, '2.479.1')
    assert doctor_options(parser.parse_args(['doctor']), make_settings(tmp_path, secrets_dir)) == (False, False, None)


def test_record_only_applies_to_a_report_run(capsys):
    with pytest.raises(SystemExit):
        main(['doctor', '--push', '--record'])
    assert '--record only applies to a report run' in capsys.readouterr().err


def test_generate_accepts_wait():
    assert build_parser().parse_args(['generate', '--wait']).wait is True


def test_pki_init_uses_the_pki_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    (tmp_path / 'uc.toml').write_text('[pki]\norg = "Example Corp"\nsigning_days = 30\n', encoding='utf-8')
    assert main(['pki', 'init', '--out', str(tmp_path / 'pki')]) == 0
    signing = cert_info(tmp_path / 'pki' / 'update-center.cert')
    assert 'Example Corp' in signing.subject
    assert signing.days_left(utcnow()) <= 30
    assert main(['pki', 'rotate', '--dir', str(tmp_path / 'pki'), '--days', '20']) == 0
    assert cert_info(tmp_path / 'pki' / 'update-center.cert').days_left(utcnow()) <= 20


def test_repo_adapter_listens_on_the_configured_port(monkeypatch, tmp_path, secrets_dir):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_ADAPTER_PORT', '9090')
    ports = []
    monkeypatch.setattr('uc.cli.serve', lambda _settings, port: ports.append(port))
    assert main(['repo-adapter']) == 0
    assert main(['repo-adapter', '--port', '7000']) == 0
    assert ports == [9090, 7000]


def test_a_malformed_url_prints_an_error_not_a_traceback(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_URL', 'http://[bad/')
    assert main(['status']) == 1
    assert 'uc: error: REPO_URL is not a valid URL: ' in capsys.readouterr().err


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/healthz' else 404)
        self.end_headers()
        self.wfile.write(b'ok\n')

    def log_message(self, *args):
        pass


@pytest.fixture
def healthz_port():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def port_file(monkeypatch, tmp_path):
    path = tmp_path / 'adapter.port'
    monkeypatch.setattr('uc.repo_adapter.server.PORT_FILE', path)
    return path


def test_healthcheck_probes_the_recorded_port_and_reads_no_settings(monkeypatch, tmp_path, port_file, healthz_port):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    (tmp_path / 'uc.toml').write_text('no_such_setting = 1\n[repo]\nadapter_port = 1\n', encoding='utf-8')
    monkeypatch.setenv('MODE', 'bogus')
    monkeypatch.setattr('uc.cli.serve', lambda *_args, **_kwargs: pytest.fail('--healthcheck must not start a server'))
    record_port(healthz_port)
    assert main(['repo-adapter', '--healthcheck']) == 0
    port_file.write_text(f'{_free_port()}\n', encoding='utf-8')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 0


def test_healthcheck_without_a_recorded_port_is_unhealthy(port_file, capsys):
    assert not port_file.exists()
    assert main(['repo-adapter', '--healthcheck']) == 1
    assert 'uc: error: repo-adapter is not healthy: it has not recorded a port yet' in capsys.readouterr().err
    port_file.write_text('not a port\n', encoding='utf-8')
    assert main(['repo-adapter', '--healthcheck']) == 1


def test_serving_records_the_port_it_bound(monkeypatch, tmp_path, secrets_dir, port_file):
    use_env(monkeypatch, tmp_path, secrets_dir)
    started = []

    class Stub:
        server_address = ('0.0.0.0', 4321)

        def serve_forever(self):
            started.append(True)

    monkeypatch.setattr('uc.repo_adapter.server.make_server', lambda *_args, **_kwargs: Stub())
    assert main(['repo-adapter', '--port', '0']) == 0
    assert started
    assert port_file.read_text(encoding='utf-8') == '4321\n'
    assert not port_file.with_name('adapter.port.tmp').exists()


def test_healthcheck_ignores_the_proxy_settings(monkeypatch, tmp_path, secrets_dir, healthz_port):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 0


@pytest.mark.usefixtures('port_file')
def test_healthcheck_fails_with_a_message_when_nothing_listens(capsys):
    port = _free_port()
    record_port(port)
    assert main(['repo-adapter', '--healthcheck']) == 1
    assert f'uc: error: repo-adapter is not healthy on port {port}: ' in capsys.readouterr().err


def test_healthcheck_fails_on_an_unexpected_status(monkeypatch, tmp_path, secrets_dir, capsys, healthz_port):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setattr('uc.repo_adapter.server.HEALTH_PATH', '/other')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 1
    assert f'uc: error: repo-adapter is not healthy on port {healthz_port}: HTTP 404' in capsys.readouterr().err
