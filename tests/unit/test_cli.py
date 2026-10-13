"""Unit tests for uc.cli.

Exit codes, status output, rollback, the run lock and the pki subcommands.
"""

import errno
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
from types import SimpleNamespace

import pytest

from sitefixture import uc_document, write_site
from uc import cli, pki
from uc.aql import classify
from uc.cli import (
    EXIT_LOCKED,
    StageOptions,
    build_parser,
    doctor_options,
    main,
    procure_options,
)
from uc.doctor import DoctorReport, Inspection
from uc.examples import example_env
from uc.pki import cert_info
from uc.publish import RunLock
from uc.repo_adapter.server import record_port
from uc.status import read_status, write_status
from uc.timeutil import utcnow
from ucsupport import make_settings


def use_env(monkeypatch, tmp_path, secrets_dir):
    values = {
        'MODE': 'airgapped',
        'PUBLIC_URL': 'https://uc.example.com',
        'REPO_URL': 'http://artifactory:8081/artifactory/',
        'REPO_KEY': 'jenkins',
        'DOWNLOAD_BACKEND_URL': 'http://artifactory:8081/artifactory/jenkins',
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


@pytest.mark.parametrize(
    ('value', 'shown'), [(None, 'uc dev\n'), ('', 'uc dev\n'), ('0.1.0', 'uc 0.1.0\n')]
)
def test_version_prints_the_release_the_image_was_built_as(
    monkeypatch, capsys, value, shown
):
    monkeypatch.delenv('UC_VERSION', raising=False)
    if value is not None:
        monkeypatch.setenv('UC_VERSION', value)
    with pytest.raises(SystemExit) as exit_info:
        main(['--version'])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == shown


def test_configuration_errors_exit_1(monkeypatch, capsys):
    monkeypatch.setenv('MODE', 'bogus')
    assert main(['status']) == 1
    assert 'uc: error: MODE must be one of' in capsys.readouterr().err


def test_rollback_updates_status(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ('20260922T010000Z', '20260922T020000Z'):
        write_site(
            data / 'releases' / rid / 'current', uc_document('https://uc.example.com')
        )
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
        write_site(
            data / 'releases' / rid / 'current', uc_document('https://uc.example.com')
        )
    (data / 'live').symlink_to('releases/20260922T020000Z')
    with RunLock(data):
        assert main(['rollback']) == EXIT_LOCKED
    assert read_status(data).get('live_release') != '20260922T010000Z'
    assert (data / 'live').readlink() == Path('releases/20260922T020000Z')


def test_repo_adapter_refuses_to_start_without_repository_credentials(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_ADAPTER', 'on')
    (secrets_dir / 'repo_password').write_text('')
    assert main(['repo-adapter', '--port', '0']) == 1
    assert 'uc: error: Repository credentials missing' in capsys.readouterr().err


def test_pki_init_and_show(tmp_path, capsys):
    out = tmp_path / 'pki'
    assert (
        main(
            [
                'pki',
                'init',
                '--out',
                str(out),
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
        == 0
    )
    assert (
        f'{out / "root" / "root-ca.key"} can sign metadata every controller accepts: '
        'move it to offline storage now' in capsys.readouterr().out
    )
    assert main(['pki', 'show', '--dir', str(out)]) == 0
    shown = capsys.readouterr().out
    assert 'update-center.cert: CN=Easy Update Center Signing' in shown
    assert 'root-ca.crt: CN=Easy Update Center Root CA' in shown


def test_procure_stage_with_nothing_selected_exits_2(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert main(['procure', 'stage']) == 2
    err = capsys.readouterr().err
    assert 'uc: warning: REPO_URL uses plain http: ' in err
    assert 'uc: warning: PROCUREMENT_SOURCE_URL uses plain http: ' in err
    assert err.endswith(
        'uc: error: nothing to stage: choose --poms, --dependencies or --war '
        'VERSION|required\n'
    )


def test_procure_stage_downloads_are_refused_air_gapped_before_listing(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert (
        main(['procure', 'stage', '--poms', '--staging', str(tmp_path / 'stage')]) == 1
    )
    assert 'uc: error: downloads need repo.jenkins-ci.org' in capsys.readouterr().err
    assert not (tmp_path / 'stage').exists()


def test_procure_stage_refuses_a_bad_war_before_listing(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert main(['procure', 'stage', '--war', '2.479.x']) == 1
    assert (
        '--war takes a Jenkins version such as 2.479.1, or required'
        in capsys.readouterr().err
    )


def test_procure_stage_refuses_a_bad_since_before_listing(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert (
        main(
            [
                'procure',
                'stage',
                '--poms',
                '--since',
                'two',
                '--staging',
                str(tmp_path / 'stage'),
            ]
        )
        == 1
    )
    assert '--since takes a Jenkins version' in capsys.readouterr().err
    assert not (tmp_path / 'stage').exists()


def test_doctor_refuses_a_bad_since_before_listing(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['doctor', '--since', 'two']) == 1
    assert '--since takes a Jenkins version' in capsys.readouterr().err


def test_clean_dry_run_on_an_empty_folder(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['procure', 'clean', '--dry-run', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        (
            '0 removed, 0 would remove, 0 kept, 0 problem(s) -> '
            'http://artifactory:8081/artifactory/jenkins/'
        )
    ]


def test_procure_record_needs_neither_a_source_nor_the_repository(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(
        monkeypatch, tmp_path, secrets_dir
    )  # air-gapped, no PROCUREMENT_SOURCE_URL, an unreachable repository
    staging = tmp_path / 'stage'
    staging.mkdir()
    assert main(['procure', 'record', '--staging', str(staging)]) == 0
    assert capsys.readouterr().out.splitlines() == ['0 recorded, 0 refused']


@pytest.mark.parametrize('command', ['push', 'clean'])
def test_staging_on_push_and_clean_reaches_the_command(
    monkeypatch, tmp_path, secrets_dir, command
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    seen = {}
    monkeypatch.setattr(
        f'uc.cli.{command}',
        lambda _settings, staging, **__: seen.update(staging=staging) or 0,
    )
    assert main(['procure', command, '--staging', str(tmp_path / 'elsewhere')]) == 0
    assert seen == {'staging': tmp_path / 'elsewhere'}


@pytest.mark.parametrize(
    'case',
    [
        (command, inside)
        for command in ('stage', 'record', 'push', 'clean')
        for inside in ('', 'x', 'a/../x')
    ],
)
def test_staging_flag_inside_the_data_volume_is_refused_before_anything_runs(
    monkeypatch, tmp_path, secrets_dir, capsys, case
):
    command, inside = case
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    for name in ('stage_local', 'push', 'clean', 'inspect_repository', 'download'):
        monkeypatch.setattr(f'uc.cli.{name}', lambda *_, **__: pytest.fail('ran'))
    staging = data / inside
    argv = ['procure', command, '--staging', str(staging)]
    if command == 'stage':
        argv.append('--poms')
    assert main(argv) == 1
    assert capsys.readouterr().err.splitlines()[-1] == (
        f'uc: error: --staging must not be the data volume ({data}) or a folder '
        'in it, which the other services mount too: keep it on the ./procurement '
        f'mount, /procurement by default (got {str(staging)!r})'
    )
    assert not (data / 'x').exists()


def test_staging_flag_in_the_data_volume_is_refused_before_the_nothing_selected_check(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    assert (
        main(['procure', 'stage', '--staging', str(data / 'x')]) == 1
    )  # not 2, nothing selected
    assert (
        capsys.readouterr()
        .err.splitlines()[-1]
        .startswith('uc: error: --staging must not be the data volume')
    )


@pytest.mark.parametrize('command', ['stage', 'record', 'push', 'clean'])
def test_staging_flag_outside_the_data_volume_is_accepted(
    monkeypatch, tmp_path, secrets_dir, command
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'http://source.invalid/releases/')
    seen = []
    for name in ('stage_local', 'push', 'clean'):
        monkeypatch.setattr(f'uc.cli.{name}', lambda *_, **__: seen.append(1) or 0)
    monkeypatch.setattr(
        'uc.cli.inspect_repository', lambda *_, **__: SimpleNamespace(report=None)
    )
    monkeypatch.setattr('uc.cli.format_report', lambda *_: 'report')
    monkeypatch.setattr('uc.cli.download', lambda *_, **__: seen.append(1) or 0)
    argv = ['procure', command, '--staging', str(tmp_path / 'elsewhere')]
    if command == 'stage':
        argv.append('--poms')
    assert main(argv) == 0
    assert seen


def test_doctor_json_prints_the_next_line_on_stderr_and_keeps_stdout_valid(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'https://mirror.corp/releases/')
    report = DoctorReport(missing_dependencies=['a 1.0 requires b 2.0'])
    monkeypatch.setattr(
        'uc.cli.inspect_repository',
        lambda *_, **__: Inspection(report, classify([]), None),
    )
    main(['doctor', '--json', '--no-fetch'])
    captured = capsys.readouterr()
    assert json.loads(captured.out)['plugins'] == 0
    assert captured.err.splitlines()[-1] == 'next: uc procure stage --dependencies'
    assert 'next:' not in captured.out


def test_doctor_air_gapped_without_a_source_suggests_placing_and_recording_the_files(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    # air-gapped, no PROCUREMENT_SOURCE_URL: uc procure stage would refuse to download
    use_env(monkeypatch, tmp_path, secrets_dir)
    report = DoctorReport(missing_dependencies=['a 1.0 requires b 2.0'])
    monkeypatch.setattr(
        'uc.cli.inspect_repository',
        lambda *_, **__: Inspection(report, classify([]), None),
    )
    main(['doctor', '--no-fetch'])
    expected = (
        'next: place the files the report lists in ./procurement and run uc procure '
        'record, or set PROCUREMENT_SOURCE_URL to a copy of repo.jenkins-ci.org the '
        'update center can reach, then uc procure stage --dependencies'
    )
    assert capsys.readouterr().out.splitlines()[-1] == expected


def test_doctor_carries_its_since_into_the_next_line_when_it_suggests_cores(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PROCUREMENT_SOURCE_URL', 'https://mirror.corp/releases/')
    report = DoctorReport(
        missing_cores=[
            {'version': '2.479.1', 'url': 'u', 'required_by': 1, 'example': 'a:b:1'}
        ]
    )
    seen = {}
    monkeypatch.setattr(
        'uc.cli.inspect_repository',
        lambda *_, since, **__: (
            seen.update(since=since) or Inspection(report, classify([]), None)
        ),
    )
    main(['doctor', '--no-fetch', '--since', '2.462.1'])
    assert seen == {'since': '2.462.1'}
    assert (
        capsys.readouterr().out.splitlines()[-1]
        == 'next: uc procure stage --war required --since 2.462.1'
    )


def test_doctor_record_keeps_json_output_valid_and_reports_the_saved_path_on_stderr(
    artifactory, monkeypatch, tmp_path, secrets_dir, capsys
):
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


def test_config_check(monkeypatch, tmp_path, secrets_dir, capsys):
    use_env(monkeypatch, tmp_path, secrets_dir)
    assert main(['config', '--check']) == 0
    assert capsys.readouterr().out == 'configuration ok\n'


@pytest.mark.parametrize(
    'value', ['https://[uc.example.com', 'https://uc:abc'], ids=['bracket', 'port']
)
def test_config_reports_a_malformed_public_url(
    monkeypatch, tmp_path, secrets_dir, capsys, value
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('PUBLIC_URL', value)
    assert main(['config', '--check']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err.splitlines()[-1].startswith(
        'uc: error: PUBLIC_URL is not a valid URL: '
    )


def test_config_json_and_check_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(['config', '--json', '--check'])
    assert exit_info.value.code == 2
    assert 'not allowed with argument' in capsys.readouterr().err


@pytest.mark.parametrize(
    ('argv', 'message'),
    [(['config', '--mode', 'online'], '--mode only applies to --example')],
)
def test_config_options_that_need_another(argv, message, capsys):
    with pytest.raises(SystemExit):
        main(argv)
    assert message in capsys.readouterr().err


def test_generate_accepts_wait():
    assert build_parser().parse_args(['generate', '--wait']).wait is True


def test_pki_init_names_the_files_under_the_display_dir(tmp_path, capsys):
    assert (
        main(
            [
                'pki',
                'init',
                '--out',
                str(tmp_path / 'pki'),
                '--display-dir',
                'new pki',
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.splitlines() == [
        (
            'created new pki/root-ca.crt: install it in '
            '$JENKINS_HOME/update-center-rootCAs/ on every controller'
        ),
        (
            'created new pki/update-center.key and new pki/update-center.cert: '
            'provide them as secrets'
        ),
        (
            'new pki/root/root-ca.key can sign metadata every controller accepts: '
            "move it to offline storage now; only 'uc pki rotate' needs it"
        ),
    ]


@pytest.mark.parametrize(
    ('display', 'files'),
    [
        (
            [],
            (
                '{out}/root/root-ca.key, {out}/root-ca.crt, {out}/update-center.key, '
                '{out}/update-center.cert'
            ),
        ),
        (
            ['--display-dir', 'secrets'],
            (
                'secrets/root/root-ca.key, secrets/root-ca.crt, '
                'secrets/update-center.key, secrets/update-center.cert'
            ),
        ),
    ],
)
def test_pki_init_names_the_existing_files_it_refuses_under_the_display_dir(
    tmp_path, capsys, display, files
):
    out = tmp_path / 'pki'
    (out / 'root').mkdir(parents=True)
    for name in (
        'root/root-ca.key',
        'root-ca.crt',
        'update-center.key',
        'update-center.cert',
    ):
        (out / name).write_text('kept\n', encoding='utf-8')
    assert main(['pki', 'init', '--out', str(out), *display]) == 1
    assert (
        capsys.readouterr().err
        == f'uc: error: refusing to overwrite existing files: {files.format(out=out)}\n'
    )


def test_pki_rotate_names_the_certificate_under_the_display_dir(
    tmp_path, capsys, pki_material
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 0
    assert capsys.readouterr().out == (
        'issued a new secrets/update-center.cert; the previous pair is kept as *.old\n'
    )
    assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    assert capsys.readouterr().out == (
        f'issued a new {work / "update-center.cert"}; '
        'the previous pair is kept as *.old\n'
    )


def tree_state(directory):
    """Return every path under directory, with its mode and each file's bytes.

    None for a directory.
    """
    return {
        path.relative_to(directory).as_posix(): (
            stat.S_IMODE(path.lstat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in directory.rglob('*')
    }


def fail_at_call(monkeypatch, owner, name, failing, error):
    """Make the failing-th call of owner.name (or each one, given several) raise error.

    The others run as usual. Return the calls' arguments.
    """
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
    """Make uc pki generate 2048-bit keys, which are quicker.

    The tests that interrupt it check what it moves, not the keys.
    """
    openssl = pki._openssl  # noqa: SLF001 - every call still runs openssl

    def smaller(*args):
        return openssl(*('rsa:2048' if arg == 'rsa:4096' else arg for arg in args))

    monkeypatch.setattr(pki, '_openssl', smaller)


def denied():
    return PermissionError(errno.EACCES, 'Permission denied')


DUMMY_PASSPHRASE = 'pass:dummy-passphrase'  # pragma: allowlist secret


def encrypt_key(key, *options):
    """Replace the private key at key with an encrypted copy.

    openssl, given options, encrypts it under a dummy passphrase.
    """
    encrypted = key.with_name('encrypted')
    subprocess.run(
        [
            'openssl',
            *options,
            '-in',
            str(key),
            '-passout',
            DUMMY_PASSPHRASE,
            '-out',
            str(encrypted),
        ],
        check=True,
        capture_output=True,
    )
    encrypted.replace(key)


ENCRYPTED_REFUSAL = (
    'the root CA key {work}/root/root-ca.key is encrypted; decrypt it to a temporary '
    'file for the rotation (docs/how-to/rotate-signing-certificate.md)'
)

ROTATE_REFUSALS = {
    'no root key': (
        'cannot read the root CA key {work}/root/root-ca.key: No such file or directory'
    ),
    'no root certificate': (
        'cannot read the root CA certificate {work}/root-ca.crt: '
        'No such file or directory'
    ),
    'a root key openssl cannot read': (
        'the root CA key {work}/root/root-ca.key is not a private key openssl can read'
    ),
    'a root certificate openssl cannot read': (
        'the root CA certificate {work}/root-ca.crt is not a certificate openssl '
        'can read'
    ),
    "another root CA's key": (
        'the root CA key {work}/root/root-ca.key is not the key of the root CA '
        'certificate {work}/root-ca.crt'
    ),
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
        subprocess.run(
            [
                'openssl',
                'genpkey',
                '-algorithm',
                'RSA',
                '-pkeyopt',
                'rsa_keygen_bits:2048',
                '-out',
                str(root_key),
            ],
            check=True,
            capture_output=True,
        )


@pytest.mark.parametrize('case', list(ROTATE_REFUSALS))
def test_pki_rotate_refuses_an_unusable_root_and_changes_nothing(
    tmp_path, capsys, pki_material, case
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    break_root(work, case)
    before = tree_state(work)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 1
    assert tree_state(work) == before
    assert (
        capsys.readouterr().err
        == f'uc: error: {ROTATE_REFUSALS[case].format(work=work)}\n'
    )


ENCRYPTED_ROOT_KEYS = {
    'PKCS#8 PEM': (('pkey', '-aes256'), ENCRYPTED_REFUSAL),
    'traditional PEM': (('pkey', '-traditional', '-aes256'), ENCRYPTED_REFUSAL),
    'PKCS#8 DER': (
        ('pkcs8', '-topk8', '-outform', 'DER', '-v2', 'aes-256-cbc'),
        ROTATE_REFUSALS['a root key openssl cannot read'],
    ),  # no PEM header to tell
}


def run_without_a_terminal(args):
    """Run python3 args with stdin open and silent, in a new session with no terminal.

    Anything that asked for a passphrase would wait for it.
    """
    reader, writer = os.pipe()
    try:
        return subprocess.run(
            [sys.executable, *args],
            stdin=reader,
            capture_output=True,
            text=True,
            timeout=30,
            start_new_session=True,
            env=dict(os.environ),
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
    proc = run_without_a_terminal(['-m', 'uc', 'pki', 'rotate', '--dir', str(work)])
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        1,
        '',
        f'uc: error: {refusal.format(work=work)}\n',
    )
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


def test_checking_an_encrypted_signing_key_never_asks_for_a_passphrase(
    tmp_path, pki_material
):
    key = tmp_path / 'update-center.key'
    shutil.copy(pki_material / 'update-center.key', key)
    encrypt_key(key, 'pkey', '-aes256')
    proc = run_without_a_terminal(
        ['-c', CHECK_SIGNING_KEY, str(key), str(pki_material / 'update-center.cert')]
    )
    assert proc.returncode == 1
    assert proc.stderr.startswith('refused: openssl pkey failed: ')
    assert 'pass phrase' not in proc.stderr


@pytest.mark.parametrize('missing', ['update-center.key', 'update-center.cert'])
def test_pki_rotate_refuses_half_a_signing_pair_and_changes_nothing(
    tmp_path, capsys, pki_material, missing
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    (work / missing).unlink()
    left = (
        'update-center.cert' if missing == 'update-center.key' else 'update-center.key'
    )
    before = tree_state(work)
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    assert tree_state(work) == before
    assert capsys.readouterr().err == (
        f'uc: error: found secrets/{left} without secrets/{missing}: '
        f'put {missing} back, or move {left} away, then rotate again\n'
    )


def test_pki_rotate_changes_nothing_when_openssl_fails_partway(
    monkeypatch, tmp_path, capsys, pki_material
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    before = tree_state(work)
    # every openssl call but the signing one runs for real
    openssl = pki._openssl  # noqa: SLF001

    def failing_signing(*args):
        if args[:2] == (
            'x509',
            '-req',
        ):  # signing the new certificate, after openssl req wrote the new key
            msg = 'openssl x509 failed: disk full'
            raise pki.PkiError(msg)
        return openssl(*args)

    monkeypatch.setattr(pki, '_openssl', failing_signing)
    assert main(['pki', 'rotate', '--dir', str(work)]) == 1
    assert tree_state(work) == before
    assert capsys.readouterr().err == 'uc: error: openssl x509 failed: disk full\n'


ROTATE_RENAMES = {'first rotation': 4, 'with a previous *.old pair': 6}
"""How many renames move the new pair in.

The current pair to *.old and the new one in, after setting aside any *.old pair.
"""

ROTATE_RENAME_FAILURES = {
    **{
        f'{previous}, rename {failing} fails': (previous, failing, denied)
        for previous, renames in ROTATE_RENAMES.items()
        for failing in range(1, renames + 1)
    },
    **{
        f'{previous}, interrupted at rename {renames}': (
            previous,
            renames,
            KeyboardInterrupt,
        )
        for previous, renames in ROTATE_RENAMES.items()
    },
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('case', list(ROTATE_RENAME_FAILURES))
def test_pki_rotate_puts_back_what_it_moved_when_a_rename_fails(
    monkeypatch, tmp_path, capsys, pki_material, case
):
    previous, failing, error = ROTATE_RENAME_FAILURES[case]
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    if previous == 'with a previous *.old pair':
        assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    before = tree_state(work)
    pair = {
        name: (work / name).read_bytes()
        for name in ('update-center.key', 'update-center.cert')
    }
    calls = fail_at_call(monkeypatch, Path, 'rename', failing, error())
    if error is denied:
        assert (
            main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
        )
        assert (
            capsys.readouterr().err
            == 'uc: error: cannot write in secrets: Permission denied\n'
        )
    else:
        with pytest.raises(KeyboardInterrupt):
            main(['pki', 'rotate', '--dir', str(work)])
    assert tree_state(work) == before
    done = len(calls)
    assert (
        main(['pki', 'rotate', '--dir', str(work)]) == 0
    )  # a rerun keeps the pair from before as *.old
    assert len(calls) - done == ROTATE_RENAMES[previous]
    assert {name: (work / f'{name}.old').read_bytes() for name in pair} == pair


# named for the signal, as KeyboardInterrupt is
class Terminated(Exception):  # noqa: N818
    """What the test's SIGTERM handler raises."""


def on_sigterm(_signum, _frame):
    raise Terminated


SIGNALS = {
    'SIGINT': (signal.SIGINT, KeyboardInterrupt),
    'SIGTERM': (signal.SIGTERM, Terminated),
}


def signal_after_rename(monkeypatch, failing, signum):
    """Deliver signum to this thread right after the failing-th Path.rename has renamed.

    Before its caller can record it.
    """
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
    **{
        f'{previous}, SIGINT after rename {failing}': (previous, failing, 'SIGINT')
        for previous, renames in ROTATE_RENAMES.items()
        for failing in range(1, renames + 1)
    },
    'with a previous *.old pair, SIGTERM after rename 3': (
        'with a previous *.old pair',
        3,
        'SIGTERM',
    ),
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('case', list(SIGNALLED_ROTATIONS))
def test_pki_rotate_finishes_its_renames_before_a_signal_stops_it(
    monkeypatch, tmp_path, pki_material, case
):
    previous, failing, signame = SIGNALLED_ROTATIONS[case]
    signum, raised = SIGNALS[signame]
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    if previous == 'with a previous *.old pair':
        assert main(['pki', 'rotate', '--dir', str(work)]) == 0
    pair = {
        name: (work / name).read_bytes()
        for name in ('update-center.key', 'update-center.cert')
    }
    signal_after_rename(monkeypatch, failing, signum)
    handler = signal.signal(signal.SIGTERM, on_sigterm)
    try:
        with pytest.raises(raised):
            main(['pki', 'rotate', '--dir', str(work)])
    finally:
        signal.signal(signal.SIGTERM, handler)
    assert sorted(path.name for path in work.iterdir()) == [
        'root',
        'root-ca.crt',
        'update-center.cert',
        'update-center.cert.old',
        'update-center.key',
        'update-center.key.old',
    ]
    assert {name: (work / f'{name}.old').read_bytes() for name in pair} == pair
    assert pki.key_matches_cert(work / 'update-center.key', work / 'update-center.cert')
    assert pki.verify_chain(work / 'root-ca.crt', work / 'update-center.cert')


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('failing', [1, 2, 3, 4])
def test_pki_init_finishes_its_renames_before_a_signal_stops_it(
    monkeypatch, tmp_path, failing
):
    out = tmp_path / 'pki'
    signal_after_rename(monkeypatch, failing, signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        main(
            [
                'pki',
                'init',
                '--out',
                str(out),
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
    assert sorted(path.relative_to(out).as_posix() for path in out.rglob('*')) == [
        'root',
        'root-ca.crt',
        'root/root-ca.key',
        'update-center.cert',
        'update-center.key',
    ]
    assert pki.key_matches_cert(out / 'update-center.key', out / 'update-center.cert')


@pytest.mark.usefixtures('small_keys')
def test_pki_rotate_keeps_what_it_set_aside_when_it_cannot_move_everything_back(
    monkeypatch, tmp_path, capsys, pki_material
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    assert (
        main(['pki', 'rotate', '--dir', str(work)]) == 0
    )  # an older *.old pair, which the next rotation sets aside
    names = (
        'update-center.key',
        'update-center.cert',
        'update-center.key.old',
        'update-center.cert.old',
    )
    contents = {(work / name).read_bytes() for name in names}
    # rename 4 (the current certificate to *.old) fails, and so does moving the current
    # key back from *.old (call 5)
    fail_at_call(monkeypatch, Path, 'rename', {4, 5}, denied())
    assert main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    err = capsys.readouterr().err
    kept = re.fullmatch(
        (
            r'uc: error: cannot write in secrets: Permission denied; moving back '
            r'failed too for secrets/update-center\.key\.old to '
            r'secrets/update-center\.key \(Permission denied\), '
            r'secrets/(\.rotate-\w+)/update-center\.key\.old to '
            r'secrets/update-center\.key\.old \(File exists\), '
            r'so secrets is not as it was: what was set aside is kept in secrets/\1\n'
        ),
        err,
    )
    assert kept, err
    assert (work / kept[1] / 'update-center.key.old').is_file()
    assert contents <= {
        path.read_bytes() for path in work.rglob('*') if path.is_file()
    }  # nothing is lost


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize('command', ['rotate', 'init'])
def test_a_target_that_appears_meanwhile_is_not_overwritten(
    monkeypatch, tmp_path, capsys, pki_material, command
):
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
    argv = (
        ['pki', 'rotate', '--dir', str(work)]
        if command == 'rotate'
        else ['pki', 'init', '--out', str(work)]
    )
    assert main([*argv, '--display-dir', 'secrets']) == 1
    assert (
        capsys.readouterr().err
        == f'uc: error: refusing to overwrite existing files: secrets/{intruder}\n'
    )
    after = tree_state(work)
    assert after.pop(intruder)[1] == b'written meanwhile\n'
    assert after == before


def test_pki_rotate_reports_a_directory_it_cannot_write_in(
    tmp_path, capsys, pki_material
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    before = tree_state(work)
    work.chmod(0o555)
    try:
        assert (
            main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
        )
    finally:
        work.chmod(0o755)
    assert (
        capsys.readouterr().err
        == 'uc: error: cannot write in secrets: Permission denied\n'
    )
    assert tree_state(work) == before


INIT_FAILURES = {
    **{
        f'openssl step {step}': ('_openssl', step) for step in (1, 2, 3)
    },  # the root CA, the signing request, the signing certificate
    **{
        f'rename {step}': ('rename', step) for step in (1, 2, 3, 4)
    },  # the root key (or root/), root-ca.crt, update-center.key, update-center.cert
}


@pytest.mark.usefixtures('small_keys')
@pytest.mark.parametrize(
    'existing', ['no directory', 'a directory with root/ and other files']
)
@pytest.mark.parametrize('failure', list(INIT_FAILURES))
def test_pki_init_leaves_nothing_behind_when_a_step_fails(
    monkeypatch, tmp_path, capsys, existing, failure
):
    out = tmp_path / 'new' / 'pki'
    if existing != 'no directory':
        (out / 'root').mkdir(parents=True)
        (out / 'repo_username').write_text('uc\n', encoding='utf-8')
    before = tree_state(tmp_path)
    name, failing = INIT_FAILURES[failure]
    if name == '_openssl':
        fail_at_call(
            monkeypatch,
            pki,
            name,
            failing,
            pki.PkiError('openssl req failed: disk full'),
        )
        message = 'openssl req failed: disk full'
    else:
        fail_at_call(monkeypatch, Path, name, failing, denied())
        message = 'cannot write in new pki: Permission denied'
    assert (
        main(
            [
                'pki',
                'init',
                '--out',
                str(out),
                '--display-dir',
                'new pki',
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == f'uc: error: {message}\n'
    assert tree_state(tmp_path) == before


@pytest.mark.usefixtures('small_keys')
def test_pki_init_puts_back_what_it_moved_when_interrupted(monkeypatch, tmp_path):
    out = tmp_path / 'pki'
    out.mkdir()
    calls = fail_at_call(monkeypatch, Path, 'rename', 4, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        main(
            [
                'pki',
                'init',
                '--out',
                str(out),
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
    assert list(out.iterdir()) == []
    done = len(calls)
    assert (
        main(
            [
                'pki',
                'init',
                '--out',
                str(out),
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
        == 0
    )
    assert len(calls) - done == 4  # the renames INIT_FAILURES interrupts one by one
    assert sorted(path.relative_to(out).as_posix() for path in out.rglob('*')) == [
        'root',
        'root-ca.crt',
        'root/root-ca.key',
        'update-center.cert',
        'update-center.key',
    ]


@pytest.mark.parametrize(
    'missing',
    [False, True],
    ids=['read-only directory', 'missing directory in a read-only one'],
)
def test_pki_init_reports_a_directory_it_cannot_write_in(tmp_path, capsys, missing):
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
    assert capsys.readouterr().err == (
        f'uc: error: cannot {"create" if missing else "write in"} secrets: '
        'Permission denied\n'
    )
    assert [path.relative_to(parent).as_posix() for path in parent.rglob('*')] == (
        [] if missing else ['pki']
    )


def test_repo_adapter_listens_on_8081_unless_given_a_port(
    monkeypatch, tmp_path, secrets_dir
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_ADAPTER_PORT', '9090')  # not a setting: ignored
    ports = []
    monkeypatch.setattr('uc.cli.serve', lambda _settings, port: ports.append(port))
    assert main(['repo-adapter']) == 0
    assert main(['repo-adapter', '--port', '7000']) == 0
    assert ports == [8081, 7000]


def test_a_malformed_url_prints_an_error_not_a_traceback(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('REPO_URL', 'http://[bad/')
    assert main(['status']) == 1
    assert 'uc: error: REPO_URL is not a valid URL: ' in capsys.readouterr().err


def test_config_check_reports_a_malformed_url_setting(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('HTTPS_PROXY', 'http://[bad')
    assert main(['config', '--check']) == 1
    assert (
        capsys.readouterr()
        .err.splitlines()[-1]
        .startswith('uc: error: HTTPS_PROXY is not a valid URL: ')
    )


def test_config_check_takes_a_proxy_password_with_a_hash_and_never_prints_one(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv(
        'HTTPS_PROXY',
        'http://svc:S3cret#x@proxy.corp:3128',  # pragma: allowlist secret
    )
    assert main(['config', '--check']) == 0
    monkeypatch.setenv(
        'HTTPS_PROXY',
        'http://svc:S3cretPW@proxy.corp:abc',  # pragma: allowlist secret
    )
    assert main(['config', '--check']) == 1
    err = capsys.readouterr().err
    assert err.splitlines()[-1].startswith(
        'uc: error: HTTPS_PROXY is not a valid URL: '
    )
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


def test_healthcheck_probes_the_recorded_port_and_reads_no_settings(
    monkeypatch, port_file, healthz_port
):
    monkeypatch.setenv('MODE', 'bogus')
    monkeypatch.setattr(
        'uc.cli.serve',
        lambda *_args, **_kwargs: pytest.fail('--healthcheck must not start a server'),
    )
    record_port(healthz_port)
    assert main(['repo-adapter', '--healthcheck']) == 0
    port_file.write_text(f'{_free_port()}\n', encoding='utf-8')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 0


def test_healthcheck_without_a_recorded_port_is_unhealthy(port_file, capsys):
    assert not port_file.exists()
    assert main(['repo-adapter', '--healthcheck']) == 1
    assert (
        'uc: error: repo-adapter is not healthy: it has not recorded a port yet'
        in capsys.readouterr().err
    )
    port_file.write_text('not a port\n', encoding='utf-8')
    assert main(['repo-adapter', '--healthcheck']) == 1


def test_serving_records_the_port_it_bound(
    monkeypatch, tmp_path, secrets_dir, port_file
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    started = []

    class Stub:
        server_address = ('0.0.0.0', 4321)

        def serve_forever(self):
            started.append(True)

    monkeypatch.setattr(
        'uc.repo_adapter.server.make_server', lambda *_args, **_kwargs: Stub()
    )
    assert main(['repo-adapter', '--port', '0']) == 0
    assert started
    assert port_file.read_text(encoding='utf-8') == '4321\n'
    assert not port_file.with_name('adapter.port.tmp').exists()


def test_serving_stops_with_an_error_when_it_cannot_record_its_port(
    monkeypatch, tmp_path, secrets_dir, capsys
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setattr(
        'uc.repo_adapter.server.PORT_FILE', tmp_path / 'missing' / 'adapter.port'
    )
    closed = []

    class Stub:
        server_address = ('0.0.0.0', 4321)

        def serve_forever(self):
            pytest.fail('serving must not start when the port cannot be recorded')

        def server_close(self):
            closed.append(True)

    monkeypatch.setattr(
        'uc.repo_adapter.server.make_server', lambda *_args, **_kwargs: Stub()
    )
    assert main(['repo-adapter', '--port', '0']) == 1
    err = capsys.readouterr().err
    assert err.startswith('uc: error: repo-adapter cannot record its port in ')
    assert 'Traceback' not in err
    assert closed


def test_healthcheck_ignores_the_proxy_settings(
    monkeypatch, tmp_path, secrets_dir, healthz_port
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 0


@pytest.mark.usefixtures('port_file')
def test_healthcheck_fails_with_a_message_when_nothing_listens(capsys):
    port = _free_port()
    record_port(port)
    assert main(['repo-adapter', '--healthcheck']) == 1
    assert (
        f'uc: error: repo-adapter is not healthy on port {port}: '
        in capsys.readouterr().err
    )


def test_healthcheck_fails_on_an_unexpected_status(
    monkeypatch, tmp_path, secrets_dir, capsys, healthz_port
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    monkeypatch.setattr('uc.repo_adapter.server.HEALTH_PATH', '/other')
    assert main(['repo-adapter', '--healthcheck', '--port', str(healthz_port)]) == 1
    assert (
        f'uc: error: repo-adapter is not healthy on port {healthz_port}: HTTP 404'
        in capsys.readouterr().err
    )


def test_config_example_prints_the_env_example(capsys):
    assert main(['config', '--example', '--mode', 'airgapped']) == 0
    assert capsys.readouterr().out == example_env('airgapped')


def test_config_example_takes_no_format():
    with pytest.raises(SystemExit) as exited:
        main(['config', '--example', 'toml'])
    assert exited.value.code == 2


def test_config_has_no_web_env():
    with pytest.raises(SystemExit) as exited:
        main(['config', '--web-env'])
    assert exited.value.code == 2


def test_procure_stage_options_come_from_the_command_line(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, DOCTOR_SINCE='2.462.1')
    parse = build_parser().parse_args
    assert procure_options(
        parse(['procure', 'stage', '--poms']), settings
    ) == StageOptions(
        fetch=True,
        poms=True,
        dependencies=False,
        wars=(),
        since='2.462.1',
        staging=settings.procurement_staging_dir,
    )
    given = [
        'procure',
        'stage',
        '--dependencies',
        '--war',
        'required',
        '--war',
        '2.479.1',
        '--no-fetch',
        '--since',
        '2.479.1',
    ]
    assert procure_options(parse(given), settings) == StageOptions(
        fetch=False,
        poms=False,
        dependencies=True,
        wars=('required', '2.479.1'),
        since='2.479.1',
        staging=settings.procurement_staging_dir,
    )
    assert procure_options(
        parse(['procure', 'stage', '--staging', '/elsewhere']), settings
    ).staging == Path('/elsewhere')


@pytest.mark.parametrize(
    'argv',
    [
        ['procure', 'stage', '--no-wars'],
        ['procure', 'stage', '--no-poms'],
        ['procure', 'record', '--no-fix'],
        ['doctor', '--no-json'],
    ],
)
def test_the_options_that_turned_a_setting_off_are_gone(argv):
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == 2


@pytest.mark.parametrize(('argv', 'expected'), [([], False), (['--fix'], True)])
def test_procure_record_takes_fix_from_the_command_line(
    monkeypatch, tmp_path, secrets_dir, argv, expected
):
    use_env(monkeypatch, tmp_path, secrets_dir)
    seen = {}
    monkeypatch.setattr(
        'uc.cli.stage_local', lambda *_, fix, **__: seen.update(fix=fix) or 0
    )
    assert main(['procure', 'record', *argv, '--staging', str(tmp_path / 'stage')]) == 0
    assert seen == {'fix': expected}


def test_doctor_options_take_json_from_the_command_line_and_fetch_from_the_setting(
    tmp_path, secrets_dir
):
    parser = build_parser()
    configured = make_settings(
        tmp_path, secrets_dir, DOCTOR_FETCH='true', DOCTOR_SINCE='2.462.1'
    )
    assert doctor_options(parser.parse_args(['doctor']), configured) == (
        True,
        False,
        '2.462.1',
    )
    assert doctor_options(
        parser.parse_args(['doctor', '--json', '--no-fetch', '--since', '2.479.1']),
        configured,
    ) == (False, True, '2.479.1')
    assert doctor_options(
        parser.parse_args(['doctor']), make_settings(tmp_path, secrets_dir)
    ) == (False, False, None)


def test_pki_init_defaults_and_flags(tmp_path):
    out = tmp_path / 'pki'
    assert main(['pki', 'init', '--out', str(out)]) == 0
    root, signing = (
        cert_info(out / 'root-ca.crt'),
        cert_info(out / 'update-center.cert'),
    )
    assert 'Easy Update Center' in root.subject
    assert 3649 <= root.days_left(utcnow()) <= 3650
    assert 179 <= signing.days_left(utcnow()) <= 180
    other = tmp_path / 'other'
    assert (
        main(
            [
                'pki',
                'init',
                '--out',
                str(other),
                '--org',
                'Example Corp',
                '--root-days',
                '30',
                '--signing-days',
                '10',
            ]
        )
        == 0
    )
    signing = cert_info(other / 'update-center.cert')
    assert 'Example Corp' in signing.subject
    assert 9 <= signing.days_left(utcnow()) <= 10
    assert main(['pki', 'rotate', '--dir', str(other), '--days', '20']) == 0
    assert 19 <= cert_info(other / 'update-center.cert').days_left(utcnow()) <= 20


def test_pki_takes_no_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('PKI_ORG', 'Env Corp')
    monkeypatch.setenv('PKI_SIGNING_DAYS', '30')
    monkeypatch.setenv('PKI_ROOT_DAYS', '0')
    assert main(['pki', 'init', '--out', str(tmp_path / 'pki')]) == 0
    signing = cert_info(tmp_path / 'pki' / 'update-center.cert')
    assert 'Env Corp' not in signing.subject
    assert signing.days_left(utcnow()) > 150


def test_pki_rotate_reads_the_root_key_from_root_and_names_it_there(
    tmp_path, capsys, pki_material
):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    offline = tmp_path / 'offline'
    (work / 'root').rename(offline)
    assert main(['pki', 'rotate', '--dir', str(work), '--root', str(offline)]) == 0
    assert (work / 'update-center.key.old').is_file()
    assert not (work / 'root').exists()
    (offline / 'root-ca.key').unlink()
    capsys.readouterr()
    assert (
        main(
            [
                'pki',
                'rotate',
                '--dir',
                str(work),
                '--root',
                str(offline),
                '--display-dir',
                'secrets',
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == (
        f'uc: error: cannot read the root CA key {offline / "root-ca.key"}: '
        'No such file or directory\n'
    )
    assert (
        main(['pki', 'rotate', '--dir', str(work), '--display-dir', 'secrets']) == 1
    )  # without --root: root/ under --display-dir
    assert capsys.readouterr().err == (
        'uc: error: cannot read the root CA key secrets/root/root-ca.key: '
        'No such file or directory\n'
    )


def test_pki_rotate_has_no_root_display_dir():
    with pytest.raises(SystemExit) as exited:
        main(['pki', 'rotate', '--dir', 'x', '--root-display-dir', 'y'])
    assert exited.value.code == 2


@pytest.mark.skipif(os.geteuid() == 0, reason='root can write anywhere')
@pytest.mark.parametrize('argv', [['generate'], ['rollback'], ['doctor', '--record']])
def test_commands_that_write_the_data_volume_stop_when_they_cannot(
    monkeypatch, tmp_path, secrets_dir, capsys, argv
):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    data.mkdir()
    data.chmod(0o555)
    try:
        assert main(argv) == 1
    finally:
        data.chmod(0o755)
    assert (
        f'uc: error: {data} is not writable by uid {os.getuid()}'
        in capsys.readouterr().err
    )


def test_the_repo_adapter_checks_its_own_directory(tmp_path, monkeypatch, secrets_dir):
    checked = []
    monkeypatch.setattr(
        cli, 'check_data_dir', lambda path, *_entries: checked.append(path)
    )
    monkeypatch.setattr(cli, 'serve', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        'load_settings',
        lambda: make_settings(
            tmp_path, secrets_dir, REPO_ADAPTER_DIR=str(tmp_path / 'adapter')
        ),
    )
    cli.main(['repo-adapter'])
    assert checked == [tmp_path / 'adapter']


def test_uc_refuses_to_run_as_root(monkeypatch, capsys):
    monkeypatch.setattr(cli.os, 'getuid', lambda: 0)
    monkeypatch.delenv('UC_ALLOW_ROOT', raising=False)
    monkeypatch.setattr(cli, '_dispatch', lambda _args: 0)
    assert cli.main(['status']) == 1
    assert 'uc must not run as root' in capsys.readouterr().err


def test_the_tests_may_run_uc_as_root(monkeypatch):
    monkeypatch.setattr(cli.os, 'getuid', lambda: 0)
    monkeypatch.setenv('UC_ALLOW_ROOT', '1')
    monkeypatch.setattr(cli, '_dispatch', lambda _args: 0)
    assert cli.main(['status']) == 0
