"""Unit tests for uc.jar.

The update-center2 environment, its java command line and the jar runner.
"""

import os
import sys
from pathlib import Path

import pytest

from uc.jar import (
    JarError,
    generate_sites,
    generate_tiers,
    jar_environment,
    java_command,
    run_jar,
)
from uc.model import Credentials, Tiers
from ucsupport import make_settings

CREDS = Credentials('uc', 'secret', 'uc', 'uc', github_placeholder=True)


def test_jar_environment(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    env = jar_environment(
        settings, CREDS, {'PATH': '/usr/bin', 'ARTIFACTORY_URL': 'ignored'}
    )
    assert env['PATH'] == '/usr/bin'
    assert env['DOWNLOADS_ROOT_URL'] == 'https://uc.example.com/download'
    assert env['ARTIFACTORY_URL'] == 'http://artifactory:8081/artifactory/'
    assert env['ARTIFACTORY_API_URL'] == 'http://artifactory:8081/artifactory/api/'
    assert env['ARTIFACTORY_REPOSITORY'] == 'jenkins'
    assert (env['ARTIFACTORY_USERNAME'], env['ARTIFACTORY_PASSWORD']) == (
        'uc',
        'secret',
    )
    assert env['ARTIFACTORY_CACHEDIR'] == str(
        tmp_path / 'data' / 'cache' / 'artifactory'
    )
    assert (env['GITHUB_USERNAME'], env['GITHUB_PASSWORD']) == ('uc', 'uc')
    assert env['POPULARITY_JSON_URL'] == ''
    assert env['CERTIFICATE_MINIMUM_VALID_DAYS'] == '30'
    assert env['CORE_AGE_DAYS'] == '0'


def test_jar_environment_spells_out_the_default_port(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path, secrets_dir, REPO_URL='https://art.corp/artifactory'
    )
    assert (
        jar_environment(settings, CREDS, {})['ARTIFACTORY_URL']
        == 'https://art.corp:443/artifactory/'
    )


def test_jar_environment_points_at_the_adapter_when_it_is_on(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path, secrets_dir, REPO_ADAPTER='on', CORE_AGE_DAYS='400'
    )
    env = jar_environment(
        settings, Credentials('uc', 'token', 'uc', 'uc', github_placeholder=True), {}
    )
    assert env['ARTIFACTORY_URL'] == 'http://repo-adapter:8081/artifactory/'
    assert env['ARTIFACTORY_API_URL'] == 'http://repo-adapter:8081/artifactory/api/'
    assert env['CORE_AGE_DAYS'] == '400'


def test_java_command(tmp_path, secrets_dir):
    assert java_command(make_settings(tmp_path, secrets_dir), '--x') == [
        'java',
        '-Dfile.encoding=UTF-8',
        '-DCERTIFICATE_MINIMUM_VALID_DAYS=30',
        '-Dsun.net.client.defaultConnectTimeout=10000',
        '-jar',
        '/opt/uc/generator/update-center2.jar',
        '--x',
    ]


def test_java_command_leaves_a_connect_timeout_the_operator_set(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path,
        secrets_dir,
        JAVA_TOOL_OPTIONS='-Xmx4g -Dsun.net.client.defaultConnectTimeout=30000',
    )
    assert not any('defaultConnectTimeout' in arg for arg in java_command(settings))


def test_run_jar_streams_and_logs(tmp_path, capfd):
    log = tmp_path / 'logs' / 'uc2.log'
    run_jar([sys.executable, '-c', "print('hello from jar')"], dict(os.environ), log)
    assert 'hello from jar' in log.read_text()
    assert 'hello from jar' in capfd.readouterr().out


def test_run_jar_failure(tmp_path):
    with pytest.raises(JarError, match='exited with status 4'):
        run_jar(
            [sys.executable, '-c', 'import sys; sys.exit(4)'],
            dict(os.environ),
            tmp_path / 'log',
        )


def test_generate_tiers_reads_upstream_json(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = []

    def fake(cmd, _env, _log):
        calls.append(cmd)
        out = Path(cmd[cmd.index('--dynamic-tier-list-file') + 1])
        out.write_text(
            '{"stableCores": ["2.528.1"], "weeklyCores": ["2.540", "2.541"]}'
        )

    tiers = generate_tiers(
        settings,
        {},
        tmp_path / 'res',
        tmp_path / 'tiers.json',
        tmp_path / 'log',
        run=fake,
    )
    assert tiers == Tiers(('2.540', '2.541'), ('2.528.1',))
    assert calls[0][-4:] == [
        '--resources-dir',
        str(tmp_path / 'res'),
        '--dynamic-tier-list-file',
        str(tmp_path / 'tiers.json'),
    ]


def test_generate_tiers_without_output_fails(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(JarError, match='tier list'):
        generate_tiers(
            settings,
            {},
            tmp_path / 'res',
            tmp_path / 'tiers.json',
            tmp_path / 'log',
            run=lambda _cmd, _env, _log: None,
        )


def test_generate_sites_command(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = []
    generate_sites(
        settings,
        {'A': '1'},
        tmp_path / 'res',
        tmp_path / 'args.lst',
        tmp_path / 'log',
        run=lambda cmd, env, _log: calls.append((cmd, env)),
    )
    cmd, env = calls[0]
    assert cmd[-4:] == [
        '--resources-dir',
        str(tmp_path / 'res'),
        '--arguments-file',
        str(tmp_path / 'args.lst'),
    ]
    assert env == {'A': '1'}
