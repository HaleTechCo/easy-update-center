"""Unit tests for uc.cli: exit codes, status output, rollback, the run lock and the pki subcommands."""

import errno
import io
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sitefixture import uc_document, write_site
from uc import configview, pki
from uc.cli import EXIT_LOCKED, StageOptions, build_parser, doctor_options, main, procure_options
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
    assert f'move {out / "root" / "root-ca.key"} to offline storage' in capsys.readouterr().out
    assert main(['pki', 'show', '--dir', str(out)]) == 0
    shown = capsys.readouterr().out
    assert 'update-center.cert: CN=Jenkins Update Center Signing' in shown
    assert 'root-ca.crt: CN=Jenkins Update Center Root CA' in shown


MOVED = [
    (['--download-poms'], 'uc procure stage --poms'),
    (['--download-dependencies'], 'uc procure stage --dependencies'),
    (['--download-war', 'required'], 'uc procure stage --war'),
    (['--staging', '/x'], 'uc procure stage|record|push|clean --staging'),
    (['--push'], 'uc procure push'),
    (['--dry-run'], 'uc procure push --dry-run, or uc procure clean --dry-run'),
    (['--stage-local'], 'uc procure record'),
    (['--fix'], 'uc procure record --fix'),
    (['--clean'], 'uc procure clean'),
]


@pytest.mark.parametrize(('argv', 'replacement'), MOVED, ids=[argv[0] for argv, _ in MOVED])
def test_a_removed_doctor_flag_names_its_command(argv, replacement, capsys):
    with pytest.raises(SystemExit) as exited:
        main(['doctor', *argv])
    assert exited.value.code == 2
    assert f'{argv[0]} moved: run {replacement}' in capsys.readouterr().err


def test_procure_stage_with_nothing_selected_exits_2(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert main(['procure', 'stage']) == 2
    err = capsys.readouterr().err
    assert err.startswith('uc: error: nothing to stage:')
    for name in ('--poms', '--dependencies', '--war', 'PROCUREMENT_POMS', 'PROCUREMENT_DEPENDENCIES', 'PROCUREMENT_WARS'):
        assert name in err


def test_procure_options_come_from_the_settings_unless_given(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, procurement_poms=True, procurement_wars=('required',), procurement_fetch=True, doctor_since='2.462.1')
    parse = build_parser().parse_args
    assert procure_options(parse(['procure', 'stage']), settings) == StageOptions(fetch=True, poms=True, dependencies=False, wars=('required',), since='2.462.1', staging=settings.procurement_staging_dir)
    assert procure_options(parse(['procure', 'stage', '--no-poms', '--no-fetch']), settings).poms is False  # Review Focus 5
    assert procure_options(parse(['procure', 'stage', '--war', '2.479.1', '--war', '2.462.3']), settings).wars == ('2.479.1', '2.462.3')  # replaces the setting
    assert procure_options(parse(['procure', 'stage', '--no-wars']), settings).wars == ()
    assert procure_options(parse(['procure', 'stage', '--staging', '/elsewhere']), settings).staging == Path('/elsewhere')


def test_procure_stage_refuses_war_and_no_wars_together():
    with pytest.raises(SystemExit) as exited:
        main(['procure', 'stage', '--war', 'required', '--no-wars'])
    assert exited.value.code == 2


def test_procure_stage_downloads_are_refused_air_gapped_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['procure', 'stage', '--poms', '--staging', str(tmp_path / 'stage')]) == 1
    assert 'uc: error: downloads need repo.jenkins-ci.org' in capsys.readouterr().err
    assert not (tmp_path / 'stage').exists()


def test_procure_stage_refuses_a_bad_war_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert main(['procure', 'stage', '--war', '2.479.x']) == 1
    assert '--war takes a Jenkins version such as 2.479.1, or required' in capsys.readouterr().err


def test_doctor_refuses_a_bad_since_before_listing(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['doctor', '--since', 'two']) == 1
    assert '--since takes a Jenkins version' in capsys.readouterr().err


def test_clean_dry_run_on_an_empty_folder(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['procure', 'clean', '--dry-run', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == ['0 removed, 0 would remove, 0 kept, 0 problem(s) -> http://artifactory:8081/artifactory/jenkins/']


def test_procure_record_needs_neither_a_source_nor_the_repository(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)  # air-gapped, no PROCUREMENT_SOURCE_URL, an unreachable repository
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['procure', 'record', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == [f'0 recorded, 0 refused; review {staging}, then run uc doctor --push']


def test_doctor_record_keeps_json_output_valid_and_reports_the_saved_path_on_stderr(artifactory, monkeypatch, tmp_path, secrets_dir, capsys):
    _, url = artifactory
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_URL', url)
    (secrets_dir / 'repo_password').write_text('tok')
    assert main(['doctor', '--record', '--json', '--no-fetch']) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['plugins'] == 0
    assert f'saved the report as {data / "doctor" / "report.json"}' in captured.err
    assert 'saved the report' not in captured.out
    assert (data / 'doctor' / 'report.json').is_file()
    assert read_status(data)['doctor']['recorded_at'].endswith('Z')


def test_push_with_nothing_staged_exits_1(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['procure', 'push', '--staging', str(tmp_path / 'empty')]) == 1
    assert 'uc: error: nothing to push' in capsys.readouterr().err


def test_commands_export_jvm_options_from_the_settings_file(monkeypatch, tmp_path, secrets_dir):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (tmp_path / 'uc.toml').write_text('java_tool_options = "-Xmx6g"\n', encoding='utf-8')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '')  # monkeypatch restores it after the test
    assert main(['status']) == 0
    assert os.environ['JAVA_TOOL_OPTIONS'] == '-Xmx6g'


def test_commands_export_proxy_settings_from_the_settings_file(monkeypatch, tmp_path, secrets_dir):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (tmp_path / 'uc.toml').write_text('https_proxy = "http://proxy.corp:3128"\nhttp_proxy = "http://proxy.corp:3129"\nno_proxy = "repo-adapter,artifactory"\n', encoding='utf-8')
    for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY'):
        monkeypatch.setenv(name, '')  # monkeypatch restores them after the test
    assert main(['status']) == 0
    assert os.environ['HTTPS_PROXY'] == 'http://proxy.corp:3128'
    assert os.environ['HTTP_PROXY'] == 'http://proxy.corp:3129'
    assert os.environ['NO_PROXY'] == 'repo-adapter,artifactory'


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


@pytest.mark.parametrize('value', ['https://[uc.example.com', 'https://uc:abc'], ids=['bracket', 'port'])
def test_config_reports_a_malformed_public_url(monkeypatch, tmp_path, secrets_dir, capsys, value):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PUBLIC_URL', value)
    for argv in (['config', '--check'], ['config', '--web-env']):  # every setting, and only the web container's
        assert main(argv) == 1, argv
        captured = capsys.readouterr()
        assert captured.out == '', argv
        assert captured.err.startswith('uc: error: PUBLIC_URL is not a valid URL: '), argv


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


def test_pki_init_rejects_an_invalid_setting(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    monkeypatch.setenv('PKI_ROOT_DAYS', '0')
    assert main(['pki', 'init', '--out', str(tmp_path / 'pki')]) == 1
    assert 'uc: error: PKI_ROOT_DAYS must be >= 1' in capsys.readouterr().err
    assert not (tmp_path / 'pki').exists()


def test_pki_init_names_the_files_under_the_display_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    assert main(['pki', 'init', '--out', str(tmp_path / 'pki'), '--display-dir', 'new pki', '--root-days', '30', '--signing-days', '10']) == 0
    assert capsys.readouterr().out.splitlines() == [
        'created new pki/root-ca.crt: install it in $JENKINS_HOME/update-center-rootCAs/ on every controller',
        'created new pki/update-center.key and new pki/update-center.cert: provide them as secrets',
        "move new pki/root/root-ca.key to offline storage; only 'uc pki rotate' needs it",
    ]


@pytest.mark.parametrize(
    ('display', 'files'),
    [
        ([], '{out}/root/root-ca.key, {out}/root-ca.crt, {out}/update-center.key, {out}/update-center.cert'),
        (['--display-dir', 'secrets'], 'secrets/root/root-ca.key, secrets/root-ca.crt, secrets/update-center.key, secrets/update-center.cert'),
    ],
)
def test_pki_init_names_the_existing_files_it_refuses_under_the_display_dir(monkeypatch, tmp_path, capsys, display, files):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    out = tmp_path / 'pki'
    (out / 'root').mkdir(parents=True)
    for name in ('root/root-ca.key', 'root-ca.crt', 'update-center.key', 'update-center.cert'):
        (out / name).write_text('kept\n', encoding='utf-8')
    assert main(['pki', 'init', '--out', str(out), *display]) == 1
    assert capsys.readouterr().err == f'uc: error: refusing to overwrite existing files: {files.format(out=out)}\n'


def test_pki_rotate_names_the_certificate_under_the_display_dir(monkeypatch, tmp_path, capsys, pki_material):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 0
    assert capsys.readouterr().out == 'issued a new secrets/update-center.cert; the previous pair is kept as *.old\n'
    assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    assert capsys.readouterr().out == f'issued a new {work / "update-center.cert"}; the previous pair is kept as *.old\n'


def tree_state(directory):
    """Return every path under directory, with its mode and each file's bytes (None for a directory)."""
    return {path.relative_to(directory).as_posix(): (stat.S_IMODE(path.lstat().st_mode), path.read_bytes() if path.is_file() else None) for path in directory.rglob('*')}


def fail_at_call(monkeypatch, owner, name, failing, error):
    """Make the failing-th call of owner.name (or each one, given several) raise error; the others run as usual. Return the calls' arguments."""
    original = getattr(owner, name)
    failing = {failing} if isinstance(failing, int) else set(failing)
    calls = []

    def counting(*args):
        calls.append(args)
        if len(calls) in failing:
            raise error
        return original(*args)

    monkeypatch.setattr(owner, name, counting)
    return calls


@pytest.fixture
def small_keys(monkeypatch):
    """Make uc pki generate 2048-bit keys, which are quicker: the tests that interrupt it check what it moves, not the keys."""
    openssl = pki._openssl  # noqa: SLF001 - every call still runs openssl

    def smaller(*args):
        return openssl(*('rsa:2048' if arg == 'rsa:4096' else arg for arg in args))

    monkeypatch.setattr(pki, '_openssl', smaller)


def denied():
    return PermissionError(errno.EACCES, 'Permission denied')


DUMMY_PASSPHRASE = 'pass:dummy-passphrase'  # pragma: allowlist secret


def encrypt_key(key, *options):
    """Replace the private key at key with a copy that openssl, given options, encrypts under a dummy passphrase."""
    encrypted = key.with_name('encrypted')
    subprocess.run(['openssl', *options, '-in', str(key), '-passout', DUMMY_PASSPHRASE, '-out', str(encrypted)], check=True, capture_output=True)
    encrypted.replace(key)


ENCRYPTED_REFUSAL = 'the root CA key {work}/root/root-ca.key is encrypted; decrypt it to a temporary file for the rotation (docs/how-to/rotate-signing-certificate.md)'

ROTATE_REFUSALS = {
    'no root key': 'cannot read the root CA key {work}/root/root-ca.key: No such file or directory',
    'no root certificate': 'cannot read the root CA certificate {work}/root-ca.crt: No such file or directory',
    'a root key openssl cannot read': 'the root CA key {work}/root/root-ca.key is not a private key openssl can read',
    'a root certificate openssl cannot read': 'the root CA certificate {work}/root-ca.crt is not a certificate openssl can read',
    "another root CA's key": 'the root CA key {work}/root/root-ca.key is not the key of the root CA certificate {work}/root-ca.crt',
    'an encrypted root key': ENCRYPTED_REFUSAL,
}
"""How uc pki rotate refuses each unusable root, before it changes anything."""


def break_root(work, case):
    root_key = work / 'root' / 'root-ca.key'
    if case == 'no root key':
        root_key.unlink()
    elif case == 'no root certificate':
        (work / 'root-ca.crt').unlink()
    elif case == 'a root key openssl cannot read':
        root_key.write_text('not a key\n', encoding='utf-8')
    elif case == 'a root certificate openssl cannot read':
        (work / 'root-ca.crt').write_text('not a certificate\n', encoding='utf-8')
    elif case == 'an encrypted root key':
        encrypt_key(root_key, 'pkey', '-aes256')
    else:
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(root_key)], check=True, capture_output=True)


@pytest.mark.parametrize('case', list(ROTATE_REFUSALS))
def test_pki_rotate_refuses_an_unusable_root_and_changes_nothing(monkeypatch, tmp_path, capsys, pki_material, case):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    break_root(work, case)
    before = tree_state(work)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 1
    assert tree_state(work) == before
    assert capsys.readouterr().err == f'uc: error: {ROTATE_REFUSALS[case].format(work=work)}\n'


ENCRYPTED_ROOT_KEYS = {
    'PKCS#8 PEM': (('pkey', '-aes256'), ENCRYPTED_REFUSAL),
    'traditional PEM': (('pkey', '-traditional', '-aes256'), ENCRYPTED_REFUSAL),
    'PKCS#8 DER': (('pkcs8', '-topk8', '-outform', 'DER', '-v2', 'aes-256-cbc'), ROTATE_REFUSALS['a root key openssl cannot read']),  # no PEM header to tell
}


def run_without_a_terminal(args, tmp_path):
    """Run python3 args with stdin open and silent, in a new session with no terminal: anything that asked for a passphrase would wait for it."""
    reader, writer = os.pipe()
    try:
        return subprocess.run(
            [sys.executable, *args],
            stdin=reader,
            capture_output=True,
            text=True,
            timeout=30,
            start_new_session=True,
            env={**os.environ, 'UC_CONFIG_FILE': str(tmp_path / 'uc.toml')},
            check=False,
        )
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize('form', list(ENCRYPTED_ROOT_KEYS))
def test_pki_rotate_never_asks_for_a_passphrase(tmp_path, pki_material, form):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    options, refusal = ENCRYPTED_ROOT_KEYS[form]
    encrypt_key(work / 'root' / 'root-ca.key', *options)
    before = tree_state(work)
    proc = run_without_a_terminal(['-m', 'uc', 'pki', 'rotate', '--dir', str(work)], tmp_path)
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, '', f'uc: error: {refusal.format(work=work)}\n')
    assert tree_state(work) == before


CHECK_SIGNING_KEY = """
import sys
from pathlib import Path
from uc.pki import PkiError, key_matches_cert
try:
    key_matches_cert(Path(sys.argv[1]), Path(sys.argv[2]))
except PkiError as exc:
    sys.exit(f'refused: {exc}')
"""


def test_checking_an_encrypted_signing_key_never_asks_for_a_passphrase(tmp_path, pki_material):
    key = tmp_path / 'update-center.key'
    shutil.copy(pki_material / 'update-center.key', key)
    encrypt_key(key, 'pkey', '-aes256')
    proc = run_without_a_terminal(['-c', CHECK_SIGNING_KEY, str(key), str(pki_material / 'update-center.cert')], tmp_path)
    assert proc.returncode == 1
    assert proc.stderr.startswith('refused: openssl pkey failed: ')
    assert 'pass phrase' not in proc.stderr


@pytest.mark.parametrize('missing', ['update-center.key', 'update-center.cert'])
def test_pki_rotate_refuses_half_a_signing_pair_and_changes_nothing(monkeypatch, tmp_path, capsys, pki_material, missing):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    (work / missing).unlink()
    left = 'update-center.cert' if missing == 'update-center.key' else 'update-center.key'
    before = tree_state(work)
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    assert tree_state(work) == before
    assert capsys.readouterr().err == f'uc: error: found secrets/{left} without secrets/{missing}: put {missing} back, or move {left} away, then rotate again\n'


def test_pki_rotate_changes_nothing_when_openssl_fails_partway(monkeypatch, tmp_path, capsys, pki_material):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    before = tree_state(work)
    openssl = pki._openssl  # noqa: SLF001 - every openssl call but the signing one runs for real

    def failing_signing(*args):
        if args[:2] == ('x509', '-req'):  # signing the new certificate, after openssl req wrote the new key
            msg = 'openssl x509 failed: disk full'
            raise pki.PkiError(msg)
        return openssl(*args)

    monkeypatch.setattr(pki, '_openssl', failing_signing)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 1
    assert tree_state(work) == before
    assert capsys.readouterr().err == 'uc: error: openssl x509 failed: disk full\n'


ROTATE_RENAMES = {'first rotation': 4, 'with a previous *.old pair': 6}
"""How many renames move the new pair in: the current pair to *.old and the new one in, after setting aside any *.old pair."""

ROTATE_RENAME_FAILURES = {
    **{f'{previous}, rename {failing} fails': (previous, failing, denied) for previous, renames in ROTATE_RENAMES.items() for failing in range(1, renames + 1)},
    **{f'{previous}, interrupted at rename {renames}': (previous, renames, KeyboardInterrupt) for previous, renames in ROTATE_RENAMES.items()},
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('case', list(ROTATE_RENAME_FAILURES))
def test_pki_rotate_puts_back_what_it_moved_when_a_rename_fails(monkeypatch, tmp_path, capsys, pki_material, case):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    previous, failing, error = ROTATE_RENAME_FAILURES[case]
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    if previous == 'with a previous *.old pair':
        assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    before = tree_state(work)
    pair = {name: (work / name).read_bytes() for name in ('update-center.key', 'update-center.cert')}
    calls = fail_at_call(monkeypatch, Path, 'rename', failing, error())
    if error is denied:
        assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
        assert capsys.readouterr().err == 'uc: error: cannot write in secrets: Permission denied\n'
    else:
        with pytest.raises(KeyboardInterrupt):
            main(['pki', 'rotate', '--dir', str(work)])
    assert tree_state(work) == before
    done = len(calls)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 0  # a rerun keeps the pair from before as *.old
    assert len(calls) - done == ROTATE_RENAMES[previous]
    assert {name: (work / f'{name}.old').read_bytes() for name in pair} == pair


class Terminated(Exception):  # noqa: N818 - named for the signal, as KeyboardInterrupt is
    """What the test's SIGTERM handler raises."""


def on_sigterm(_signum, _frame):
    raise Terminated


SIGNALS = {'SIGINT': (signal.SIGINT, KeyboardInterrupt), 'SIGTERM': (signal.SIGTERM, Terminated)}


def signal_after_rename(monkeypatch, failing, signum):
    """Deliver signum to this thread right after the failing-th Path.rename has renamed, before its caller can record it."""
    rename = Path.rename
    calls = []

    def renaming(*args):
        calls.append(args)
        renamed = rename(*args)
        if len(calls) == failing:
            signal.raise_signal(signum)
        return renamed

    monkeypatch.setattr(Path, 'rename', renaming)


SIGNALLED_ROTATIONS = {
    **{f'{previous}, SIGINT after rename {failing}': (previous, failing, 'SIGINT') for previous, renames in ROTATE_RENAMES.items() for failing in range(1, renames + 1)},
    'with a previous *.old pair, SIGTERM after rename 3': ('with a previous *.old pair', 3, 'SIGTERM'),
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('case', list(SIGNALLED_ROTATIONS))
def test_pki_rotate_finishes_its_renames_before_a_signal_stops_it(monkeypatch, tmp_path, pki_material, case):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    previous, failing, signame = SIGNALLED_ROTATIONS[case]
    signum, raised = SIGNALS[signame]
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    if previous == 'with a previous *.old pair':
        assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    pair = {name: (work / name).read_bytes() for name in ('update-center.key', 'update-center.cert')}
    signal_after_rename(monkeypatch, failing, signum)
    handler = signal.signal(signal.SIGTERM, on_sigterm)
    try:
        with pytest.raises(raised):
            main(['pki', 'rotate', '--dir', str(work)])
    finally:
        signal.signal(signal.SIGTERM, handler)
    assert sorted(path.name for path in work.iterdir()) == ['root', 'root-ca.crt', 'update-center.cert', 'update-center.cert.old', 'update-center.key', 'update-center.key.old']
    assert {name: (work / f'{name}.old').read_bytes() for name in pair} == pair
    assert pki.key_matches_cert(work / 'update-center.key', work / 'update-center.cert')
    assert pki.verify_chain(work / 'root-ca.crt', work / 'update-center.cert')


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('failing', [1, 2, 3, 4])
def test_pki_init_finishes_its_renames_before_a_signal_stops_it(monkeypatch, tmp_path, failing):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    out = tmp_path / 'pki'
    signal_after_rename(monkeypatch, failing, signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        main(['pki', 'init', '--out', str(out), '--root-days', '30', '--signing-days', '10'])
    assert sorted(path.relative_to(out).as_posix() for path in out.rglob('*')) == ['root', 'root-ca.crt', 'root/root-ca.key', 'update-center.cert', 'update-center.key']
    assert pki.key_matches_cert(out / 'update-center.key', out / 'update-center.cert')


@pytest.mark.usefixtures('small_keys')
def test_pki_rotate_keeps_what_it_set_aside_when_it_cannot_move_everything_back(monkeypatch, tmp_path, capsys, pki_material):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 0  # an older *.old pair, which the next rotation sets aside
    names = ('update-center.key', 'update-center.cert', 'update-center.key.old', 'update-center.cert.old')
    contents = {(work / name).read_bytes() for name in names}
    # rename 4 (the current certificate to *.old) fails, and so does moving the current key back from *.old (call 5)
    fail_at_call(monkeypatch, Path, 'rename', {4, 5}, denied())
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    err = capsys.readouterr().err
    kept = re.fullmatch(
        r'uc: error: cannot write in secrets: Permission denied; moving back failed too for '
        r'secrets/update-center\.key\.old to secrets/update-center\.key \(Permission denied\), '
        r'secrets/(\.rotate-\w+)/update-center\.key\.old to secrets/update-center\.key\.old \(File exists\), '
        r'so secrets is not as it was: what was set aside is kept in secrets/\1\n',
        err,
    )
    assert kept, err
    assert (work / kept[1] / 'update-center.key.old').is_file()
    assert contents <= {path.read_bytes() for path in work.rglob('*') if path.is_file()}  # nothing is lost


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('command', ['rotate', 'init'])
def test_a_target_that_appears_meanwhile_is_not_overwritten(monkeypatch, tmp_path, capsys, pki_material, command):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    if command == 'rotate':
        shutil.copytree(pki_material, work)
        intruder = 'update-center.cert.old'  # the target of rename 2
    else:
        work.mkdir()
        intruder = 'update-center.key'  # the target of rename 3
    before = tree_state(work)
    rename = Path.rename
    calls = []

    def another_process_writes(*args):
        calls.append(args)
        renamed = rename(*args)
        if len(calls) == 1:
            (work / intruder).write_text('written meanwhile\n', encoding='utf-8')
        return renamed

    monkeypatch.setattr(Path, 'rename', another_process_writes)
    argv = ['pki', 'rotate', '--dir', str(work)] if command == 'rotate' else ['pki', 'init', '--out', str(work)]
    assert main([*argv, '--display-dir', 'secrets']) == 1
    assert capsys.readouterr().err == f'uc: error: refusing to overwrite existing files: secrets/{intruder}\n'
    after = tree_state(work)
    assert after.pop(intruder)[1] == b'written meanwhile\n'
    assert after == before


def test_pki_rotate_reports_a_directory_it_cannot_write_in(monkeypatch, tmp_path, capsys, pki_material):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    before = tree_state(work)
    work.chmod(0o555)
    try:
        assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    finally:
        work.chmod(0o755)
    assert capsys.readouterr().err == 'uc: error: cannot write in secrets: Permission denied\n'
    assert tree_state(work) == before


INIT_FAILURES = {
    **{f'openssl step {step}': ('_openssl', step) for step in (1, 2, 3)},  # the root CA, the signing request, the signing certificate
    **{f'rename {step}': ('rename', step) for step in (1, 2, 3, 4)},  # the root key (or root/), root-ca.crt, update-center.key, update-center.cert
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('existing', ['no directory', 'a directory with root/ and other files'])
@pytest.mark.parametrize('failure', list(INIT_FAILURES))
def test_pki_init_leaves_nothing_behind_when_a_step_fails(monkeypatch, tmp_path, capsys, existing, failure):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    out = tmp_path / 'new' / 'pki'
    if existing != 'no directory':
        (out / 'root').mkdir(parents=True)
        (out / 'repo_username').write_text('uc\n', encoding='utf-8')
    before = tree_state(tmp_path)
    name, failing = INIT_FAILURES[failure]
    if name == '_openssl':
        fail_at_call(monkeypatch, pki, name, failing, pki.PkiError('openssl req failed: disk full'))
        message = 'openssl req failed: disk full'
    else:
        fail_at_call(monkeypatch, Path, name, failing, denied())
        message = 'cannot write in new pki: Permission denied'
    assert main(['pki', 'init', '--out', str(out), '--display-dir', 'new pki', '--root-days', '30', '--signing-days', '10']) == 1
    assert capsys.readouterr().err == f'uc: error: {message}\n'
    assert tree_state(tmp_path) == before


@pytest.mark.usefixtures('small_keys')
def test_pki_init_puts_back_what_it_moved_when_interrupted(monkeypatch, tmp_path):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    out = tmp_path / 'pki'
    out.mkdir()
    calls = fail_at_call(monkeypatch, Path, 'rename', 4, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        main(['pki', 'init', '--out', str(out), '--root-days', '30', '--signing-days', '10'])
    assert list(out.iterdir()) == []
    done = len(calls)
    assert main(['pki', 'init', '--out', str(out), '--root-days', '30', '--signing-days', '10']) == 0
    assert len(calls) - done == 4  # the renames INIT_FAILURES interrupts one by one
    assert sorted(path.relative_to(out).as_posix() for path in out.rglob('*')) == ['root', 'root-ca.crt', 'root/root-ca.key', 'update-center.cert', 'update-center.key']


@pytest.mark.parametrize('missing', [False, True], ids=['read-only directory', 'missing directory in a read-only one'])
def test_pki_init_reports_a_directory_it_cannot_write_in(monkeypatch, tmp_path, capsys, missing):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    parent = tmp_path / 'parent'
    out = parent / 'pki'
    out.mkdir(parents=True)
    if missing:
        out.rmdir()
    (parent if missing else out).chmod(0o555)
    try:
        assert main(['pki', 'init', '--out', str(out), '--display-dir', 'secrets']) == 1
    finally:
        (parent if missing else out).chmod(0o755)
    assert capsys.readouterr().err == f'uc: error: cannot {"create" if missing else "write in"} secrets: Permission denied\n'
    assert [path.relative_to(parent).as_posix() for path in parent.rglob('*')] == ([] if missing else ['pki'])


def test_pki_rotate_names_the_root_files_where_they_are_on_the_host(monkeypatch, tmp_path, capsys, pki_material):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    (work / 'root' / 'root-ca.key').unlink()
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets', '--root-display-dir', 'old root']) == 1
    assert capsys.readouterr().err == 'uc: error: cannot read the root CA key old root/root-ca.key: No such file or directory\n'
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1  # without --root-display-dir: root/ under --display-dir
    assert capsys.readouterr().err == 'uc: error: cannot read the root CA key secrets/root/root-ca.key: No such file or directory\n'
    shutil.copy(pki_material / 'root' / 'root-ca.key', work / 'root' / 'root-ca.key')
    (work / 'root-ca.crt').unlink()
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets', '--root-display-dir', 'old root']) == 1
    assert capsys.readouterr().err == 'uc: error: cannot read the root CA certificate secrets/root-ca.crt: No such file or directory\n'


def test_pki_init_flags_override_the_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('UC_CONFIG_FILE', str(tmp_path / 'uc.toml'))
    monkeypatch.setenv('PKI_ORG', 'Env Corp')
    monkeypatch.setenv('PKI_ROOT_DAYS', '900')
    monkeypatch.setenv('PKI_SIGNING_DAYS', '400')
    out = tmp_path / 'pki'
    assert main(['pki', 'init', '--out', str(out), '--org', 'Flag Corp', '--root-days', '50', '--signing-days', '20']) == 0
    root, signing = cert_info(out / 'root-ca.crt'), cert_info(out / 'update-center.cert')
    assert 'Flag Corp' in root.subject
    assert 'Flag Corp' in signing.subject
    assert 'Env Corp' not in signing.subject
    assert 49 <= root.days_left(utcnow()) <= 50
    assert 19 <= signing.days_left(utcnow()) <= 20


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


def test_config_check_reports_a_bad_procurement_war(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_WARS', 'latest')
    assert main(['config', '--check']) == 1
    assert capsys.readouterr().err.startswith('uc: error: PROCUREMENT_WARS takes Jenkins versions')


def test_config_check_reports_a_malformed_url_setting(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('HTTPS_PROXY', 'http://[bad')
    assert main(['config', '--check']) == 1
    assert capsys.readouterr().err.startswith('uc: error: HTTPS_PROXY is not a valid URL: ')


def test_config_check_takes_a_proxy_password_with_a_hash_and_never_prints_one(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('HTTPS_PROXY', 'http://svc:S3cret#x@proxy.corp:3128')  # pragma: allowlist secret
    assert main(['config', '--check']) == 0
    monkeypatch.setenv('HTTPS_PROXY', 'http://svc:S3cretPW@proxy.corp:abc')  # pragma: allowlist secret
    assert main(['config', '--check']) == 1
    err = capsys.readouterr().err
    assert err.startswith('uc: error: HTTPS_PROXY is not a valid URL: ')
    assert 'S3cretPW' not in err
    assert 'abc' not in err


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


def test_serving_stops_with_an_error_when_it_cannot_record_its_port(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setattr('uc.repo_adapter.server.PORT_FILE', tmp_path / 'missing' / 'adapter.port')
    closed = []

    class Stub:
        server_address = ('0.0.0.0', 4321)

        def serve_forever(self):
            pytest.fail('serving must not start when the port cannot be recorded')

        def server_close(self):
            closed.append(True)

    monkeypatch.setattr('uc.repo_adapter.server.make_server', lambda *_args, **_kwargs: Stub())
    assert main(['repo-adapter', '--port', '0']) == 1
    err = capsys.readouterr().err
    assert err.startswith('uc: error: repo-adapter cannot record its port in ')
    assert 'Traceback' not in err
    assert closed


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
