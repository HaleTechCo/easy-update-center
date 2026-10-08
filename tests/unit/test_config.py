"""Unit tests for uc.config: per-mode defaults, overrides and the rejection of invalid settings."""

import urllib.request
from pathlib import Path
from urllib.parse import unquote

import pytest

from uc.config import _proxy_hostport, load_settings, load_web_settings, read_secret, settings_digest, split_userinfo, with_explicit_port
from uc.errors import ConfigError
from uc.settings import SETTINGS

AIRGAPPED = {
    'MODE': 'airgapped',
    'PUBLIC_URL': 'https://uc.example.com',
    'REPO_URL': 'https://artifactory.corp/artifactory',
    'REPO_KEY': 'jenkins-local',
}
KEEPER = {**AIRGAPPED, 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'https://ak.corp', 'REPO_KEY': 'jenkins-prod-local'}


def test_online_defaults():
    s = load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com/'})
    assert s.public_url == 'https://uc.example.com'
    assert s.downloads_root_url == 'https://uc.example.com/download'
    assert s.repo_type == 'artifactory'
    assert s.repo_url == 'https://repo.jenkins-ci.org/'
    assert s.repo_url_with_port == 'https://repo.jenkins-ci.org:443/'
    assert s.repo_api_url == 'https://repo.jenkins-ci.org:443/api/'
    assert s.repo_key == 'releases'
    assert s.repo_adapter is False
    assert (s.source_url, s.source_api_url) == ('https://repo.jenkins-ci.org:443/', 'https://repo.jenkins-ci.org:443/api/')
    assert s.core_age_days == 400
    assert s.repo_max_archive_mb == 512
    assert s.repo_ca_file is None
    assert s.download_backend == 'mirror'
    assert s.download_backend_url == 'https://get.jenkins.io'
    assert s.download_access == 'redirect'
    assert s.resources_source == 'github'
    assert s.resources_ref == 'master'
    assert s.popularity_json_url.startswith('https://raw.githubusercontent.com/')
    assert s.tool_installers_url == 'https://updates.jenkins.io/updates/'
    assert (s.retain_releases, s.min_plugins, s.min_plugin_ratio) == (5, 1, 0.9)
    assert (s.certificate_minimum_valid_days, s.pki_signing_days, s.stale_after_days) == (30, 365, 30)
    assert s.generate_schedule == '0 */6 * * *'
    assert s.uc_id == 'default'
    assert s.extra_args == ()
    assert s.signing_key_file == Path('/run/secrets/update-center.key')
    assert s.root_ca_file == Path('/run/secrets/root-ca.crt')
    assert s.data_dir == Path('/data')
    assert s.jar == Path('/opt/uc/generator/update-center2.jar')
    assert s.cache_dir == Path('/data/cache/artifactory')
    assert s.repo_adapter_dir == Path('/data/repo-adapter')
    assert s.repo_adapter_token_file == Path('/data/repo-adapter/token')
    assert s.repo_adapter_report_file == Path('/data/repo-adapter/report.json')
    assert s.repo_username_file == Path('/run/secrets/repo_username')


def test_airgapped_defaults():
    s = load_settings(AIRGAPPED)
    assert s.repo_url == 'https://artifactory.corp/artifactory/'
    assert s.repo_url_with_port == 'https://artifactory.corp:443/artifactory/'
    assert s.repo_adapter is True
    assert s.repo_adapter_url == 'http://repo-adapter:8081/artifactory/'
    assert (s.source_url, s.source_api_url) == ('http://repo-adapter:8081/artifactory/', 'http://repo-adapter:8081/artifactory/api/')
    assert s.core_age_days == 0
    assert s.download_backend == 'artifactory'
    assert s.download_backend_url == 'https://artifactory.corp/artifactory/jenkins-local'
    assert s.resources_source == 'bundled'
    assert s.popularity_json_url == ''
    assert s.tool_installers_url == ''


def test_airgapped_requires_a_repository():
    with pytest.raises(ConfigError, match='REPO_URL and REPO_KEY must be set'):
        load_settings({'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com'})


def test_artifact_keeper_defaults():
    s = load_settings(KEEPER)
    assert s.repo_type == 'artifact-keeper'
    assert s.repo_url == 'https://ak.corp/'
    assert s.repo_adapter is True
    assert s.download_backend_url == 'https://ak.corp/maven/jenkins-prod-local'


def test_artifact_keeper_online_needs_its_own_url_and_forces_the_adapter():
    with pytest.raises(ConfigError, match='REPO_URL and REPO_KEY must be set'):
        load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'REPO_TYPE': 'artifact-keeper'})
    s = load_settings({**KEEPER, 'MODE': 'online'})
    assert s.repo_adapter is True
    assert s.core_age_days == 400


def test_artifact_keeper_refuses_the_adapter_off():
    with pytest.raises(ConfigError, match='REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on'):
        load_settings({**KEEPER, 'REPO_ADAPTER': 'off'})


def test_the_generator_cache_is_separate_with_the_adapter():
    """Cache keys are URL paths, and /artifactory/<key>/... is the same with the adapter on or off."""
    assert load_settings(AIRGAPPED).cache_dir == Path('/data/cache/repo-adapter')
    assert load_settings({**AIRGAPPED, 'REPO_ADAPTER': 'off'}).cache_dir == Path('/data/cache/artifactory')


def test_adapter_settings_can_be_overridden():
    s = load_settings(
        {
            **AIRGAPPED,
            'REPO_ADAPTER_URL': 'http://127.0.0.1:9999/artifactory',
            'REPO_ADAPTER_DIR': '/state',
            'REPO_CA_FILE': '/ca/corp.pem',
            'REPO_MAX_ARCHIVE_MB': '64',
            'CORE_AGE_DAYS': '400',
        }
    )
    assert s.source_url == 'http://127.0.0.1:9999/artifactory/'
    assert s.repo_adapter_token_file == Path('/state/token')
    assert s.repo_ca_file == Path('/ca/corp.pem')
    assert (s.repo_max_archive_mb, s.core_age_days) == (64, 400)


def test_online_private_artifactory_can_turn_the_adapter_on():
    s = load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'https://art.corp/artifactory/', 'REPO_KEY': 'jenkins', 'REPO_ADAPTER': 'on'})
    assert s.repo_adapter is True
    assert s.source_url == 'http://repo-adapter:8081/artifactory/'


@pytest.mark.parametrize('mode', ['', 'offline'])
def test_mode_is_required_and_validated(mode):
    with pytest.raises(ConfigError, match='MODE must be one of online, airgapped'):
        load_settings({'MODE': mode, 'PUBLIC_URL': 'https://uc.example.com'})


def test_public_url_is_required():
    with pytest.raises(ConfigError, match='PUBLIC_URL must be set'):
        load_settings({'MODE': 'online'})


def test_explicit_empty_popularity_url_disables_it_online():
    s = load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc', 'POPULARITY_JSON_URL': ''})
    assert s.popularity_json_url == ''


def test_resources_url_required_for_url_source():
    with pytest.raises(ConfigError, match='RESOURCES_URL must be set'):
        load_settings({**AIRGAPPED, 'RESOURCES_SOURCE': 'url'})


@pytest.mark.parametrize(
    ('name', 'value', 'message'),
    [
        ('MIN_PLUGIN_RATIO', '1.5', 'between 0 and 1'),
        ('RETAIN_RELEASES', 'abc', 'must be an integer'),
        ('RETAIN_RELEASES', '0', 'must be >= 1'),
        ('DOWNLOAD_BACKEND', 's3', 'DOWNLOAD_BACKEND must be one of'),
        ('DOWNLOAD_ACCESS', 'cache', 'DOWNLOAD_ACCESS must be one of'),
        ('ALLOW_LOCAL_PUBLIC_URL', 'maybe', 'true or false'),
        ('REPO_KEY', 'a"b', 'unsupported characters'),
        ('REPO_TYPE', 'nexus', 'REPO_TYPE must be one of artifactory, artifact-keeper'),
        ('REPO_ADAPTER', 'sometimes', 'true or false'),
        ('CORE_AGE_DAYS', '-1', 'must be >= 0'),
        ('CORE_AGE_DAYS', 'ten', 'must be an integer'),
        ('REPO_MAX_ARCHIVE_MB', '0', 'must be >= 1'),
    ],
)
def test_invalid_values_are_rejected(name, value, message):
    with pytest.raises(ConfigError, match=message):
        load_settings({**AIRGAPPED, name: value})


def test_backend_url_override_and_extra_args():
    s = load_settings(
        {
            **AIRGAPPED,
            'DOWNLOAD_BACKEND_URL': 'https://dl.corp/jenkins/',
            'UC_EXTRA_ARGS': '--max-plugins 20',
        }
    )
    assert s.download_backend_url == 'https://dl.corp/jenkins'
    assert s.extra_args == ('--max-plugins', '20')


def test_secret_paths_follow_secrets_dir_and_overrides():
    s = load_settings({**AIRGAPPED, 'SECRETS_DIR': '/pki', 'GITHUB_TOKEN_FILE': '/other/token'})
    assert s.signing_cert_file == Path('/pki/update-center.cert')
    assert s.repo_password_file == Path('/pki/repo_password')
    assert s.github_token_file == Path('/other/token')


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        ('https://a.b/x/', 'https://a.b:443/x/'),
        ('http://a.b/', 'http://a.b:80/'),
        ('https://a.b:8443/', 'https://a.b:8443/'),
        ('http://[::1]/', 'http://[::1]:80/'),
    ],
)
def test_with_explicit_port(url, expected):
    assert with_explicit_port(url) == expected


def test_read_secret(tmp_path):
    assert read_secret(tmp_path / 'missing') is None
    (tmp_path / 'empty').write_text('\n')
    assert read_secret(tmp_path / 'empty') is None
    (tmp_path / 'value').write_text('s3cret\n')
    assert read_secret(tmp_path / 'value') == 's3cret'


def test_procurement_defaults():
    s = load_settings(AIRGAPPED)
    assert s.procurement_source_url == 'https://repo.jenkins-ci.org/releases/'
    assert s.procurement_source_set is False
    assert s.repo_push_auth_file == Path('/run/secrets/repo_push_auth')


def test_procurement_source_can_point_at_a_proxy_repository():
    s = load_settings({**AIRGAPPED, 'PROCUREMENT_SOURCE_URL': 'https://artifacts.corp/maven/jenkins-remote'})
    assert s.procurement_source_url == 'https://artifacts.corp/maven/jenkins-remote/'
    assert s.procurement_source_set is True


def test_maven_url_follows_the_repository_type():
    assert load_settings(AIRGAPPED).maven_url == 'https://artifactory.corp/artifactory/jenkins-local/'
    assert load_settings(KEEPER).maven_url == 'https://ak.corp/maven/jenkins-prod-local/'


def test_the_public_repository_is_recognised_by_its_host():
    assert load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}).public_repository is True
    assert load_settings(AIRGAPPED).public_repository is False


def test_the_adapter_url_follows_the_adapter_port():
    s = load_settings({**AIRGAPPED, 'REPO_ADAPTER_PORT': '9091'})
    assert s.repo_adapter_url == 'http://repo-adapter:9091/artifactory/'
    assert s.source_api_url == 'http://repo-adapter:9091/artifactory/api/'


def test_an_explicit_adapter_url_wins_over_the_port():
    s = load_settings({**AIRGAPPED, 'REPO_ADAPTER_PORT': '9091', 'REPO_ADAPTER_URL': 'http://adapter.corp:8081/artifactory'})
    assert s.repo_adapter_url == 'http://adapter.corp:8081/artifactory/'


def test_the_adapter_url_follows_the_adapter_port_from_uc_toml(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[repo]\nadapter_port = 9092\n', encoding='utf-8')
    assert load_settings(AIRGAPPED, config_file=path).repo_adapter_url == 'http://repo-adapter:9092/artifactory/'


def test_the_adapter_url_from_uc_toml_wins_over_the_one_its_port_gives(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[repo]\nadapter_port = 9092\nadapter_url = "http://adapter.corp:9093/artifactory"\n', encoding='utf-8')
    assert load_settings(AIRGAPPED, config_file=path).repo_adapter_url == 'http://adapter.corp:9093/artifactory/'


def test_the_adapter_url_from_the_environment_wins_over_uc_toml(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[repo]\nadapter_url = "http://adapter.corp:9093/artifactory"\n', encoding='utf-8')
    s = load_settings({**AIRGAPPED, 'REPO_ADAPTER_URL': 'http://adapter.env:9094/artifactory'}, config_file=path)
    assert s.repo_adapter_url == 'http://adapter.env:9094/artifactory/'


def test_procurement_staging_defaults():
    settings = load_settings(AIRGAPPED)
    assert settings.procurement_staging_dir == Path('/procurement')
    assert settings.procurement_fetch is True
    assert (settings.procurement_poms, settings.procurement_dependencies, settings.procurement_fix) == (False, False, False)
    assert settings.procurement_wars == ()


@pytest.mark.parametrize(('data', 'staging'), [('/data', '/data'), ('/data', '/data/'), ('/data', '/data/procurement'), ('/data', '/data/../data/stage'), ('/srv/uc', '/srv/uc/stage')])
def test_a_staging_folder_in_the_data_volume_fails_when_the_settings_load(data, staging):
    with pytest.raises(ConfigError, match=rf"^PROCUREMENT_STAGING_DIR must be outside UC_DATA_DIR \({data}\), which the other services mount too: keep it on the \./procurement mount, /procurement by default \(got '{staging}'\)$"):
        load_settings({**AIRGAPPED, 'UC_DATA_DIR': data, 'PROCUREMENT_STAGING_DIR': staging}, config_file=Path('/nonexistent/uc.toml'))


def test_a_staging_folder_in_the_data_volume_from_uc_toml_names_its_key(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[procurement]\nstaging_dir = "/data/stage"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=r'^PROCUREMENT_STAGING_DIR \(\[procurement\] staging_dir in .*uc\.toml\) must be outside UC_DATA_DIR \(/data\)'):
        load_settings(AIRGAPPED, config_file=path)


@pytest.mark.parametrize('staging', ['/procurement', '/procurement/batch', '/database', '/srv/data', '/data2/stage'])
def test_a_staging_folder_outside_the_data_volume_loads(staging):
    assert load_settings({**AIRGAPPED, 'PROCUREMENT_STAGING_DIR': staging}, config_file=Path('/nonexistent/uc.toml')).procurement_staging_dir == Path(staging)


@pytest.mark.parametrize(('raw', 'wars'), [('required', ('required',)), ('required 2.479.1', ('required', '2.479.1')), ('2.462.3,2.479.1', ('2.462.3', '2.479.1')), (' required , 2.479.1 ', ('required', '2.479.1'))])
def test_procurement_wars_from_the_environment(raw, wars):
    assert load_settings({**AIRGAPPED, 'PROCUREMENT_WARS': raw}).procurement_wars == wars


def test_procurement_wars_from_uc_toml_as_an_array_or_a_string(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[procurement]\nwars = ["required", "2.479.1"]\n', encoding='utf-8')
    assert load_settings(AIRGAPPED, config_file=path).procurement_wars == ('required', '2.479.1')
    path.write_text('[procurement]\nwars = "required 2.479.1"\n', encoding='utf-8')
    assert load_settings(AIRGAPPED, config_file=path).procurement_wars == ('required', '2.479.1')


@pytest.mark.parametrize('raw', ['latest', '2.x', 'required,latest'])
def test_a_bad_procurement_war_fails_when_the_settings_load(raw):
    with pytest.raises(ConfigError, match=r'^PROCUREMENT_WARS takes Jenkins versions such as 2\.479\.1, or required'):
        load_settings({**AIRGAPPED, 'PROCUREMENT_WARS': raw})


def test_a_list_in_uc_toml_must_hold_strings(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[procurement]\nwars = [2]\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='must be a list of strings'):
        load_settings(AIRGAPPED, config_file=path)


@pytest.mark.parametrize('value', ['2.462', '2.462.1', '2.440'])
def test_doctor_since_accepts_jenkins_versions(value):
    assert load_settings({**AIRGAPPED, 'DOCTOR_SINCE': value}).doctor_since == value


def test_doctor_since_empty_means_unset():
    assert load_settings({**AIRGAPPED, 'DOCTOR_SINCE': ''}).doctor_since == ''


@pytest.mark.parametrize('value', ['latest', '2', '2.462.1.4', ' 2.x', '2.462-rc'])
def test_doctor_since_must_be_a_jenkins_version(value):
    with pytest.raises(ConfigError, match=rf"DOCTOR_SINCE must be a Jenkins version such as 2\.462\.1 \(got '{value.strip()}'\)"):
        load_settings({**AIRGAPPED, 'DOCTOR_SINCE': value})


def test_doctor_since_from_uc_toml_names_its_key(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[doctor]\nsince = "newest"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=r"DOCTOR_SINCE \(\[doctor\] since in .*uc\.toml\) must be a Jenkins version such as 2\.462\.1 \(got 'newest'\)"):
        load_settings(AIRGAPPED, config_file=path)


@pytest.mark.parametrize(
    ('name', 'extra'),
    [
        ('REPO_URL', {}),
        ('REPO_URL', {'REPO_ADAPTER': 'off'}),
        ('PROCUREMENT_SOURCE_URL', {}),
        ('REPO_URL', {'REPO_URL': 'http://host:notaport/'}),
    ],
)
def test_a_malformed_url_is_a_configuration_error(name, extra):
    env = {**AIRGAPPED, name: 'http://[bad/', **extra}
    with pytest.raises(ConfigError, match=f'{name} is not a valid URL: '):
        load_settings(env)


URL_SETTINGS = [
    ('REPO_API_URL', '[repo]\napi_url'),
    ('REPO_ADAPTER_URL', '[repo]\nadapter_url'),
    ('RESOURCES_URL', '[resources]\nurl'),
    ('POPULARITY_JSON_URL', 'popularity_json_url'),
    ('TOOL_INSTALLERS_URL', 'tool_installers_url'),
    ('HTTPS_PROXY', 'https_proxy'),
    ('HTTP_PROXY', 'http_proxy'),
]


@pytest.mark.parametrize('bad', ['https://[bad', 'http://host:abc'])
@pytest.mark.parametrize('name', [name for name, _ in URL_SETTINGS])
def test_every_url_setting_is_parsed_when_the_settings_load(name, bad):
    with pytest.raises(ConfigError, match=f'^{name} is not a valid URL: '):
        load_settings({**AIRGAPPED, name: bad}, config_file=Path('/nonexistent/uc.toml'))


@pytest.mark.parametrize(('name', 'toml'), URL_SETTINGS)
def test_a_malformed_url_setting_from_uc_toml_names_its_key(tmp_path, name, toml):
    path = tmp_path / 'uc.toml'
    path.write_text(f'{toml} = "http://[bad"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=rf'^{name} \(.* in .*uc\.toml\) is not a valid URL: '):
        load_settings(AIRGAPPED, config_file=path)


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('HTTPS_PROXY', 'proxy.corp:3128'),
        ('HTTP_PROXY', 'http://user:pw@proxy.corp:3128'),  # pragma: allowlist secret
        ('HTTPS_PROXY', 'svc:S3cretPW@proxy.corp:3128'),  # urllib reads a scheme-less svc: with no / after it as the user name # pragma: allowlist secret
        ('REPO_API_URL', 'https://artifactory.corp/artifactory/api/'),
        ('RESOURCES_URL', 'https://mirror.corp/resources.zip'),
    ],
)
def test_valid_url_settings_still_load(name, value):
    assert load_settings({**AIRGAPPED, name: value}, config_file=Path('/nonexistent/uc.toml'))


PASSWORD_DELIMITERS = ['#', '?', '/', '[', ']']
PASSWORD_FORMS = ['http://proxyuser:{}@proxy.corp:3128', 'proxyuser:{}@proxy.corp:3128']  # pragma: allowlist secret


@pytest.mark.parametrize('delimiter', PASSWORD_DELIMITERS)
@pytest.mark.parametrize('form', PASSWORD_FORMS, ids=['scheme', 'no-scheme'])
def test_a_proxy_password_may_hold_what_ends_a_url_host(tmp_path, delimiter, form):
    # urllib's proxy handler takes the user info up to the last @, so these work there and must not stop uc.
    value = form.format(f'S3cret{delimiter}Tail9z')
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}, config_file=Path('/nonexistent/uc.toml')).https_proxy == value
    path = tmp_path / 'uc.toml'
    path.write_text(f'http_proxy = "{value}"\n', encoding='utf-8')
    assert load_settings(AIRGAPPED, config_file=path).http_proxy == value


UNREADABLE_PROXIES = [
    'http://proxy.corp:abc',
    'http://proxyuser:S3cretPW@proxy.corp:abc',  # pragma: allowlist secret
    'proxyuser:S3cretPW@proxy.corp:abc',
    'http://proxyuser:S3cretPW@proxy.corp:99999',  # pragma: allowlist secret
    'http://proxyuser:S3cretPW@[proxy.corp:3128',  # pragma: allowlist secret
]


@pytest.mark.parametrize('value', UNREADABLE_PROXIES)
def test_a_proxy_whose_host_or_port_cannot_be_read_is_refused_without_quoting_it(value):
    with pytest.raises(ConfigError, match=r'^HTTPS_PROXY is not a valid URL: ') as error:
        load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}, config_file=Path('/nonexistent/uc.toml'))
    for part in ('abc', '99999', 'proxyuser', 'S3cretPW'):
        assert part not in str(error.value)


PROXY_REFUSAL = r'is not a valid URL: write it as scheme://host:port; percent-encode # \? / \[ \] @ in a user name or password$'
UNREADABLE_PROXY = r'is not a valid URL: its host or port cannot be read; percent-encode # \? / \[ \] @ in a user name or password$'
SINGLE_SLASH_PROXIES = ['svc:/S3cretPW@proxy.corp:3128', 'http:/proxy.corp:3128', '/S3cretPW@proxy.corp:3128', '/proxy.corp:3128']  # pragma: allowlist secret


@pytest.mark.parametrize('name', ['HTTPS_PROXY', 'HTTP_PROXY'])
@pytest.mark.parametrize('value', SINGLE_SLASH_PROXIES)
def test_a_proxy_with_a_single_slash_where_two_belong_is_refused_as_urllib_refuses_it(name, value):
    # urllib takes what precedes the first : as the scheme, if anything does, and refuses a rest that starts with / but not //.
    with pytest.raises(ConfigError, match=f'^{name} {PROXY_REFUSAL}') as error:
        load_settings({**AIRGAPPED, name: value}, config_file=Path('/nonexistent/uc.toml'))
    for part in ('svc', 'S3cretPW', 'proxy.corp'):
        assert part not in str(error.value)


AT_AFTER_AUTHORITY_PROXIES = ['http://proxyuser:S3cretPW@Tail/9z@proxy.corp:3128', 'http://proxyuser:S3cretPW@proxy.corp:3128/x@other.corp', '//proxyuser:S3cretPW@Tail/9z@proxy.corp:3128']  # pragma: allowlist secret


@pytest.mark.parametrize('name', ['HTTPS_PROXY', 'HTTP_PROXY'])
@pytest.mark.parametrize('value', AT_AFTER_AUTHORITY_PROXIES)
def test_a_proxy_with_an_at_after_the_end_of_urllibs_authority_is_refused(name, value):
    # urllib ends the authority at the first / after the first @ and takes the host from before that /; uc reads it after the last @.
    with pytest.raises(ConfigError, match=f'^{name} {PROXY_REFUSAL}') as error:
        load_settings({**AIRGAPPED, name: value}, config_file=Path('/nonexistent/uc.toml'))
    for part in ('proxyuser', 'S3cretPW', 'Tail', 'proxy.corp', 'other.corp'):
        assert part not in str(error.value)


UNUSABLE_HOSTPORT_PROXIES = [
    'http://proxy.corp:3128?x',
    'http://proxy.corp:3128#x',
    'proxy.corp:3128/',
    'proxy.corp:3128/x',
    '//proxy.corp:abc',
    'http://proxyuser:S3cretPW@proxy.corp:3128?x',  # pragma: allowlist secret
    'proxyuser:S3cretPW@proxy.corp:3128/x',
    'http://proxyuser:S3cretPW@:3128',  # pragma: allowlist secret
]


@pytest.mark.parametrize('name', ['HTTPS_PROXY', 'HTTP_PROXY'])
@pytest.mark.parametrize('value', UNUSABLE_HOSTPORT_PROXIES)
def test_a_proxy_whose_host_or_port_urllib_cannot_use_is_refused(name, value):
    # urllib hands http.client everything up to the end of its authority, which a ? or # does not end, nor a / without a // before it.
    with pytest.raises(ConfigError, match=f'^{name} {UNREADABLE_PROXY}') as error:
        load_settings({**AIRGAPPED, name: value}, config_file=Path('/nonexistent/uc.toml'))
    for part in ('proxyuser', 'S3cretPW', 'proxy.corp', '3128', 'abc'):
        assert part not in str(error.value)


BRACKETED_NOT_IPV6_PROXIES = [
    'http://[v1.x]:3128',
    'http://%5Bv1.x%5D:3128',
    '%5Bv1.x%5D:3128',
    '[v1.x[::1]',
    'http://[v1.x[::1]:3128',
    'http://proxyuser:S3cretPW@[v1.x]:3128',  # pragma: allowlist secret
]


@pytest.mark.parametrize('value', BRACKETED_NOT_IPV6_PROXIES)
def test_a_proxy_host_in_brackets_must_be_an_ipv6_address(value):
    # urlsplit also takes an IPvFuture literal ([v1.x], even [v1.x[::1]), which http.client cannot connect to.
    with pytest.raises(ConfigError, match=f'^HTTPS_PROXY {UNREADABLE_PROXY}') as error:
        load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}, config_file=Path('/nonexistent/uc.toml'))
    for part in ('proxyuser', 'S3cretPW', 'v1.x', '3128'):
        assert part not in str(error.value)


USABLE_PROXIES = [
    'http://proxy.corp:3128',
    'http://proxyuser:S3cretPW@proxy.corp:3128',  # pragma: allowlist secret
    'proxy.corp:3128',
    'proxyuser:S3cretPW@proxy.corp:3128',
    'http://proxyuser:S3cretPW@proxy.corp:3128/',  # pragma: allowlist secret
    'http://[::1]:3128',
    'http://[fe80::1%25eth0]:3128',  # a scoped IPv6 address, percent-encoded
    'socks5h://proxyuser:S3cretPW@proxy.corp:1080',  # pragma: allowlist secret
    'http://proxy.corp:31%32%38',  # urllib percent-decodes the host and port
]


@pytest.mark.parametrize('value', USABLE_PROXIES)
def test_a_proxy_urllib_can_use_still_loads(value):
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}, config_file=Path('/nonexistent/uc.toml')).https_proxy == value


ATS_IN_USER_INFO_PROXIES = ['proxyuser:S3cretPW@Tail/9z@proxy.corp:3128', 'http://proxyuser:S3cretPW/Tail@9z@proxy.corp:3128', 'http://proxyuser:S3cretPW@Tail9z@proxy.corp:3128/']  # pragma: allowlist secret


@pytest.mark.parametrize('value', ATS_IN_USER_INFO_PROXIES)
def test_a_proxy_whose_ats_are_all_in_urllibs_user_info_still_loads(value):
    # Without // urllib reads the whole value as the authority; with it, a / before the first @ or after the last one ends nothing early.
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}, config_file=Path('/nonexistent/uc.toml')).https_proxy == value


def urllib_reading(value):
    """Return (user info, host:port) as urllib itself reads a proxy URL (_parse_proxy, then proxy_open's unquote); None when it refuses it."""
    try:
        _, user, password, hostport = urllib.request._parse_proxy(value)  # noqa: SLF001 - the point is to compare with urllib itself
    except ValueError:
        return None
    return (None if user is None else user + ('' if password is None else ':' + password)), unquote(hostport)


DRIFT_PROXIES = [
    'proxy.corp:3128',
    'http://user:pw@proxy.corp:3128',  # pragma: allowlist secret
    'svc:S3cretPW@proxy.corp:3128',
    *(form.format(f'S3cret{delimiter}Tail9z') for form in PASSWORD_FORMS for delimiter in PASSWORD_DELIMITERS),
    *UNREADABLE_PROXIES,
    *SINGLE_SLASH_PROXIES,
    *AT_AFTER_AUTHORITY_PROXIES,
    *UNUSABLE_HOSTPORT_PROXIES,
    *BRACKETED_NOT_IPV6_PROXIES,
    *USABLE_PROXIES,
    *ATS_IN_USER_INFO_PROXIES,
    '//proxy.corp:3128',
    '//proxyuser:S3cretPW@proxy.corp:3128',
    'http:///proxy.corp:3128',
    'HTTP://Proxy.Corp:3128',
    'http://proxy.corp',
    ':@proxy.corp:3128',
    'http://proxy%40corp:3128',
    'http://proxyuser:S3cret%40Tail9z@proxy.corp:3128',  # pragma: allowlist secret
    'socks5h://proxy.corp:1080/',
    'u:a@b/c@proxy.corp:3128',
    'a:b:c@proxy.corp:3128',
]


@pytest.mark.parametrize('value', DRIFT_PROXIES)
def test_the_proxy_host_and_port_follow_urllibs_own_parser(value):
    # Pins the mirror to urllib itself, so a Python update that changes how urllib reads a proxy URL fails here.
    ours, theirs = _proxy_hostport(value), urllib_reading(value)
    if ours is None:
        assert theirs is None or theirs[0] != split_userinfo(value)[1]  # urllib refuses it, or reads other user info
    else:
        assert theirs is not None
        assert ours == theirs[1]


ALL_URL_SETTINGS = [setting.env for setting in SETTINGS if setting.type == 'url']
WEB_URL_SETTINGS = ['PUBLIC_URL', 'REPO_URL', 'DOWNLOAD_BACKEND_URL']
NO_AT_SETTINGS = {'PUBLIC_URL': 'public_url', 'REPO_URL': '[repo]\nurl', 'DOWNLOAD_BACKEND_URL': '[download]\nbackend_url', 'PROCUREMENT_SOURCE_URL': '[procurement]\nsource_url'}
"""The URL settings that never carry credentials, so any @ in them is refused, with their uc.toml keys."""


def refused(name):
    """Return how the message refusing a URL setting's malformed value starts: an @ in a setting that never carries credentials is refused before the URL is parsed."""
    return f'^{name} must not contain credentials or an @' if name in NO_AT_SETTINGS else f'^{name} is not a valid URL: '


@pytest.mark.parametrize(('load', 'name'), [*((load_settings, name) for name in ALL_URL_SETTINGS), *((load_web_settings, name) for name in WEB_URL_SETTINGS)])
def test_a_malformed_url_message_does_not_quote_the_value(load, name):
    with pytest.raises(ConfigError, match=refused(name)) as error:
        load({**AIRGAPPED, name: 'https://admin:S3cretPW@host.corp:wxyz/'}, config_file=Path('/nonexistent/uc.toml'))  # pragma: allowlist secret
    for part in ('admin', 'S3cretPW', 'wxyz'):
        assert part not in str(error.value)


@pytest.mark.parametrize('name', [name for name in ALL_URL_SETTINGS if not name.endswith('_PROXY')])
def test_a_password_that_ends_the_host_early_is_not_quoted(name):
    # Only the proxies take the user info up to the last @; elsewhere urlsplit reads S3cretPW as the port.
    with pytest.raises(ConfigError, match=refused(name)) as error:
        load_settings({**AIRGAPPED, name: 'https://admin:S3cretPW#x@host.corp/'}, config_file=Path('/nonexistent/uc.toml'))  # pragma: allowlist secret
    assert 'S3cretPW' not in str(error.value)


TOKEN = 'Tok3nQx7'
AT_VALUES = {
    'user info': f'https://admin:{TOKEN}@host.corp/',  # pragma: allowlist secret
    'user only': f'https://{TOKEN}@host.corp/',
    'user info then #': f'https://admin:{TOKEN}#x@host.corp/',  # pragma: allowlist secret
    'token then #': f'https://{TOKEN}#x@host.corp/',
    'token then ?': f'https://{TOKEN}?x@host.corp/',
    'token then /': f'https://{TOKEN}/x@host.corp/',
    'path': f'https://host.corp/{TOKEN}@b',
    'query': f'https://host.corp?x={TOKEN}@b',
}
NO_AT_LOADS = [*((load_settings, name) for name in NO_AT_SETTINGS), *((load_web_settings, name) for name in WEB_URL_SETTINGS)]


@pytest.mark.parametrize('value', AT_VALUES.values(), ids=AT_VALUES.keys())
@pytest.mark.parametrize(('load', 'name'), NO_AT_LOADS)
def test_any_at_is_refused_in_a_url_setting_that_never_carries_credentials(load, name, value):
    # urlsplit ends the user info at a # ? or /, and would read a token before one as the host.
    with pytest.raises(ConfigError, match=f'^{name} must not contain credentials or an @') as error:
        load({**AIRGAPPED, name: value}, config_file=Path('/nonexistent/uc.toml'))
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(('name', 'toml'), NO_AT_SETTINGS.items())
def test_an_at_in_a_url_setting_from_uc_toml_names_its_key(tmp_path, name, toml):
    path = tmp_path / 'uc.toml'
    path.write_text(f'{toml} = "https://{TOKEN}#x@host.corp/"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=rf'^{name} \(.* in .*uc\.toml\) must not contain credentials or an @') as error:
        load_settings({key: value for key, value in AIRGAPPED.items() if key != name}, config_file=path)
    assert TOKEN not in str(error.value)


ENCODED_AT = 'https://host.corp/jenkins%40corp'  # an encoded @ is not one


@pytest.mark.parametrize(
    ('load', 'name', 'field', 'expected'),
    [
        (load_settings, 'PUBLIC_URL', 'public_url', ENCODED_AT),
        (load_settings, 'REPO_URL', 'repo_url', ENCODED_AT + '/'),
        (load_settings, 'DOWNLOAD_BACKEND_URL', 'download_backend_url', ENCODED_AT),
        (load_settings, 'PROCUREMENT_SOURCE_URL', 'procurement_source_url', ENCODED_AT + '/'),
        (load_web_settings, 'PUBLIC_URL', 'public_url', ENCODED_AT),
        (load_web_settings, 'REPO_URL', 'download_backend_url', ENCODED_AT + '/jenkins-local'),  # web reads the repository only for the default backend URL
        (load_web_settings, 'DOWNLOAD_BACKEND_URL', 'download_backend_url', ENCODED_AT),
    ],
)
def test_a_url_setting_that_never_carries_credentials_loads_without_an_at(load, name, field, expected):
    assert getattr(load({**AIRGAPPED, name: ENCODED_AT}, config_file=Path('/nonexistent/uc.toml')), field) == expected


def test_a_malformed_url_from_uc_toml_names_its_key(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text('[repo]\nurl = "http://[bad/"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=r'REPO_URL \(\[repo\] url in .*uc\.toml\) is not a valid URL: '):
        load_settings({'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_KEY': 'jenkins'}, config_file=path)


def test_web_settings_refuse_a_malformed_repository_url():
    env = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc', 'REPO_URL': 'http://[bad/', 'REPO_KEY': 'jenkins'}
    with pytest.raises(ConfigError, match='REPO_URL is not a valid URL: '):
        load_web_settings(env, config_file=Path('/nonexistent/uc.toml'))


@pytest.mark.parametrize('load', [load_settings, load_web_settings])
@pytest.mark.parametrize('value', ['https://[uc.example.com', 'https://uc:abc'], ids=['bracket', 'port'])
def test_a_malformed_public_url_is_a_configuration_error(load, value, tmp_path):
    with pytest.raises(ConfigError, match=r'^PUBLIC_URL is not a valid URL: '):
        load({**AIRGAPPED, 'PUBLIC_URL': value}, config_file=Path('/nonexistent/uc.toml'))
    path = tmp_path / 'uc.toml'
    path.write_text(f'public_url = "{value}"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=r'^PUBLIC_URL \(public_url in .*uc\.toml\) is not a valid URL: '):
        load({name: given for name, given in AIRGAPPED.items() if name != 'PUBLIC_URL'}, config_file=path)


@pytest.mark.parametrize('load', [load_settings, load_web_settings])
def test_an_unset_public_url_is_still_reported_as_unset(load):
    with pytest.raises(ConfigError, match=r'^PUBLIC_URL must be set$'):
        load({'MODE': 'online', 'PUBLIC_URL': ' '}, config_file=Path('/nonexistent/uc.toml'))


def digest(env, config_file=None):
    return settings_digest(load_settings(env, config_file=config_file or Path('/nonexistent/uc.toml')), env)


def test_equal_settings_have_equal_digests():
    assert digest(AIRGAPPED) == digest(dict(AIRGAPPED))


@pytest.mark.parametrize(('name', 'value'), [('GENERATE_SCHEDULE', '0 1 * * *'), ('DOWNLOAD_ACCESS', 'proxy'), ('REPO_MAX_ARCHIVE_MB', '64'), ('HTTPS_PROXY', 'http://proxy:3128')])
def test_changing_a_restart_setting_changes_the_digest(name, value):
    assert digest({**AIRGAPPED, name: value}) != digest(AIRGAPPED)


@pytest.mark.parametrize(
    'after',
    ['public_url = "https://uc.example.com"\n[generate]\nschedule = "0 1 * * *"\n', 'public_url = "https://other.example.com"\n'],
    ids=['generate_schedule', 'public_url'],
)
def test_a_restart_setting_changed_only_in_uc_toml_changes_the_digest(tmp_path, after):
    env = {name: value for name, value in AIRGAPPED.items() if name != 'PUBLIC_URL'}  # the same environment both times
    before_file, after_file = tmp_path / 'before.toml', tmp_path / 'after.toml'
    before_file.write_text('public_url = "https://uc.example.com"\n', encoding='utf-8')
    after_file.write_text(after, encoding='utf-8')
    assert digest(env, before_file) != digest(env, after_file)


@pytest.mark.parametrize('blank', ['', '   '])
def test_a_blank_environment_value_leaves_the_digest_as_if_absent(blank):
    assert digest({**AIRGAPPED, 'RETAIN_RELEASES': blank}) == digest(AIRGAPPED)


def test_an_environment_value_is_taken_stripped_in_the_digest():
    assert digest({**AIRGAPPED, 'RETAIN_RELEASES': ' 5'}) == digest({**AIRGAPPED, 'RETAIN_RELEASES': '5'})


@pytest.mark.parametrize('name', ['POPULARITY_JSON_URL', 'TOOL_INSTALLERS_URL'])
def test_an_empty_value_that_turns_a_setting_off_stays_in_the_digest(name):
    assert digest({**AIRGAPPED, name: ''}) != digest(AIRGAPPED)


@pytest.mark.parametrize(('name', 'value', 'toml'), [('RETAIN_RELEASES', '9', 'retain_releases = 9\n'), ('JAVA_TOOL_OPTIONS', '-Xmx1g', 'java_tool_options = "-Xmx1g"\n')])
def test_an_unmarked_setting_changes_the_digest_in_the_environment_but_not_in_uc_toml(tmp_path, name, value, toml):
    assert digest({**AIRGAPPED, name: value}) != digest(AIRGAPPED)  # a container's environment is fixed when it is created
    (tmp_path / 'uc.toml').write_text(toml, encoding='utf-8')
    assert digest(AIRGAPPED, tmp_path / 'uc.toml') == digest(AIRGAPPED)
