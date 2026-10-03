"""Settings from config/uc.toml: under the environment, over the defaults, and the file's own mistakes (docs/reference/configuration.md#how-settings-are-read)."""

import os
from pathlib import Path

import pytest

from uc.config import PkiSettings, apply_process_env, load_pki_settings, load_settings
from uc.errors import ConfigError
from uc.settings import SETTINGS

ONLINE = {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}
AIRGAPPED = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://artifactory.corp/artifactory', 'REPO_KEY': 'jenkins-local'}
NO_FILE = Path('/nonexistent/uc.toml')


def toml(tmp_path, text):
    path = tmp_path / 'uc.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test_the_file_sets_what_the_environment_does_not(tmp_path):
    path = toml(tmp_path, 'mode = "airgapped"\npublic_url = "https://uc.example.com"\nretain_releases = 7\n[repo]\ntype = "artifact-keeper"\nurl = "https://ak.corp/"\nkey = "jenkins"\n')
    s = load_settings({}, config_file=path)
    assert (s.mode, s.retain_releases, s.repo_type, s.repo_url, s.repo_key) == ('airgapped', 7, 'artifact-keeper', 'https://ak.corp/', 'jenkins')
    assert s.download_backend_url == 'https://ak.corp/maven/jenkins'
    assert s.config_file == path


def test_the_environment_wins_over_the_file(tmp_path):
    assert load_settings({**ONLINE, 'RETAIN_RELEASES': '3'}, config_file=toml(tmp_path, 'retain_releases = 7\n')).retain_releases == 3


def test_an_empty_variable_falls_through_to_the_file(tmp_path):
    assert load_settings({**ONLINE, 'RETAIN_RELEASES': '  '}, config_file=toml(tmp_path, 'retain_releases = 7\n')).retain_releases == 7


def test_an_empty_popularity_url_turns_it_off_from_either_source(tmp_path):
    assert load_settings({**ONLINE, 'POPULARITY_JSON_URL': ''}, config_file=toml(tmp_path, 'popularity_json_url = "https://x/p.json"\n')).popularity_json_url == ''
    assert load_settings(ONLINE, config_file=toml(tmp_path, 'popularity_json_url = ""\n')).popularity_json_url == ''


def test_mode_defaults_follow_a_mode_set_in_the_file(tmp_path):
    s = load_settings({'PUBLIC_URL': 'https://uc', 'REPO_URL': 'https://a/', 'REPO_KEY': 'k'}, config_file=toml(tmp_path, 'mode = "airgapped"\n'))
    assert (s.core_age_days, s.download_backend, s.repo_adapter) == (0, 'artifactory', True)


def test_a_missing_file_is_fine(tmp_path):
    assert load_settings(ONLINE, config_file=tmp_path / 'absent.toml').retain_releases == 5


def test_uc_config_file_names_the_file(tmp_path):
    path = toml(tmp_path, 'retain_releases = 9\n')
    assert load_settings({**ONLINE, 'UC_CONFIG_FILE': str(path)}).retain_releases == 9


def test_the_web_and_jvm_settings_have_fields(tmp_path):
    path = toml(tmp_path, 'java_tool_options = "-Xmx6g"\n[tls]\ncert_file = "/tls/c.pem"\nkey_file = "/tls/k.pem"\n')
    s = load_settings({**ONLINE, 'SECRETS_DIR': '/pki'}, config_file=path)
    assert (s.java_tool_options, s.tls_cert_file, s.tls_key_file) == ('-Xmx6g', '/tls/c.pem', '/tls/k.pem')
    assert s.download_proxy_auth_file == Path('/pki/download_proxy_auth')
    assert s.download_proxy_ca_file == '/etc/ssl/certs/ca-certificates.crt'
    assert s.secrets_dir == Path('/pki')


@pytest.mark.parametrize(
    ('text', 'message'),
    [
        ('retain_releases = "abc"\n', r'RETAIN_RELEASES \(retain_releases in .*uc\.toml\) must be an integer'),
        ('retain_releases = true\n', r'retain_releases in .*uc\.toml must be an integer'),
        ('[repo]\nadapter = 1\n', r'\[repo\] adapter in .*uc\.toml must be true or false'),
        ('[uc]\nid = 5\n', r'\[uc\] id in .*uc\.toml must be a string'),
        ('[procurment]\nsource_url = "x"\n', r'unknown setting \[procurment\] in .*; did you mean \[procurement\]\?'),
        ('[generate]\nretain_releases = 3\n', r'unknown setting \[generate\] retain_releases in .*; did you mean retain_releases\?'),
        ('MODE = "online"\n', r'unknown setting MODE in .*; did you mean mode\?'),
        ('repo_url = "https://a/"\n', r'repo_url in .*: write REPO_URL as \[repo\] url'),
        ('[uc]\nimage = "x"\n', r'UC_IMAGE can only be set in the environment'),
        ('mode = \n', r'is not valid TOML'),
    ],
)
def test_file_errors_name_the_key_and_the_file(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(ONLINE, config_file=toml(tmp_path, text))


def test_a_byte_order_mark_and_crlf_are_fine(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_bytes(b'\xef\xbb\xbfretain_releases = 4\r\n[repo]\r\nmax_archive_mb = 64\r\n')
    s = load_settings(ONLINE, config_file=path)
    assert (s.retain_releases, s.repo_max_archive_mb) == (4, 64)


def test_quoted_numbers_and_booleans_read_like_the_environment(tmp_path):
    s = load_settings(ONLINE, config_file=toml(tmp_path, 'retain_releases = "6"\nallow_local_public_url = "yes"\nmin_plugin_ratio = 1\n'))
    assert (s.retain_releases, s.allow_local_public_url, s.min_plugin_ratio) == (6, True, 1.0)


def test_a_directory_in_place_of_the_file_is_explained(tmp_path):
    (tmp_path / 'uc.toml').mkdir()
    with pytest.raises(ConfigError, match='is a directory, not a settings file'):
        load_settings(ONLINE, config_file=tmp_path / 'uc.toml')


def test_a_file_that_is_not_utf8_is_explained(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('retain_releases = 4\n', encoding='utf-16')
    with pytest.raises(ConfigError, match='is not UTF-8 text: save it as UTF-8'):
        load_settings(ONLINE, config_file=path)


def test_a_path_under_a_regular_file_is_explained(tmp_path):
    (tmp_path / 'afile').write_text('x', encoding='utf-8')
    with pytest.raises(ConfigError, match=r'uc.toml cannot be read: '):
        load_settings(ONLINE, config_file=tmp_path / 'afile' / 'uc.toml')


@pytest.mark.skipif(os.getuid() == 0, reason='root reads any file')
def test_an_unreadable_file_is_explained(tmp_path):
    path = toml(tmp_path, 'retain_releases = 4\n')
    path.chmod(0)
    with pytest.raises(ConfigError, match=r'is not readable by uid \d+: make it readable by everyone \(chmod 644\)'):
        load_settings(ONLINE, config_file=path)


def test_every_setting_has_a_settings_field():
    s = load_settings(ONLINE, config_file=NO_FILE)
    for setting in SETTINGS:
        if setting.attribute is not None:
            assert hasattr(s, setting.attribute), setting.env


@pytest.mark.parametrize('env', [ONLINE, AIRGAPPED])
def test_the_registry_defaults_are_the_defaults(env):
    s = load_settings(env, config_file=NO_FILE)
    for setting in SETTINGS:
        default = setting.default_for(env['MODE'])
        if setting.attribute is None or not default:
            continue
        value = getattr(s, setting.attribute)
        assert (str(value).lower() if isinstance(value, bool) else str(value)) == default, setting.env


def test_jvm_options_and_proxies_from_the_file_reach_the_process_environment(tmp_path):
    settings = load_settings(ONLINE, config_file=toml(tmp_path, 'java_tool_options = "-Xmx6g"\nhttps_proxy = "http://proxy:3128"\nno_proxy = "repo-adapter"\n'))
    environ = {'HTTPS_PROXY': 'http://other:8080', 'HTTP_PROXY': ''}
    assert apply_process_env(settings, environ) == ('JAVA_TOOL_OPTIONS', 'NO_PROXY')
    assert environ == {'HTTPS_PROXY': 'http://other:8080', 'HTTP_PROXY': '', 'JAVA_TOOL_OPTIONS': '-Xmx6g', 'NO_PROXY': 'repo-adapter'}


def test_pki_settings_need_no_mode(tmp_path):
    path = toml(tmp_path, '[pki]\norg = "Example Corp"\n')
    assert load_pki_settings({'PKI_ROOT_DAYS': '100'}, config_file=path) == PkiSettings('Example Corp', 100, 365)


def test_pki_days_must_be_positive(tmp_path):
    with pytest.raises(ConfigError, match='PKI_SIGNING_DAYS must be >= 1'):
        load_pki_settings({'PKI_SIGNING_DAYS': '0'}, config_file=tmp_path / 'none.toml')
