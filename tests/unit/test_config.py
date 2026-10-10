"""Unit tests for uc.config: per-mode defaults, overrides and the rejection of invalid settings."""

import urllib.request
from pathlib import Path
from urllib.parse import unquote

import pytest

from uc.config import _proxy_hostport, load_settings, read_secret, split_userinfo, with_explicit_port
from uc.errors import ConfigError
from uc.settings import REGISTRY, SETTINGS

AIRGAPPED = {
    'MODE': 'airgapped',
    'PUBLIC_URL': 'https://uc.example.com',
    'REPO_URL': 'https://artifactory.corp/artifactory',
    'REPO_KEY': 'jenkins-local',
    'DOWNLOAD_BACKEND_URL': 'https://artifactory.corp/artifactory/jenkins-local',
}
KEEPER = {**AIRGAPPED, 'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'https://ak.corp', 'REPO_KEY': 'jenkins-prod-local', 'DOWNLOAD_BACKEND_URL': 'https://ak.corp/maven/jenkins-prod-local'}
ONLINE = {'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}


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
    assert (s.retain_releases, s.min_plugin_ratio) == (5, 0.9)
    assert (s.certificate_minimum_valid_days, s.stale_after_days) == (30, 30)
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


def test_repository_settings_can_be_set():
    s = load_settings({**AIRGAPPED, 'REPO_CA_FILE': '/ca/corp.pem', 'REPO_MAX_ARCHIVE_MB': '64', 'CORE_AGE_DAYS': '400'})
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


@pytest.mark.parametrize('value', ['2.462', '2.462.1', '2.440'])
def test_doctor_since_accepts_jenkins_versions(value):
    assert load_settings({**AIRGAPPED, 'DOCTOR_SINCE': value}).doctor_since == value


def test_doctor_since_empty_means_unset():
    assert load_settings({**AIRGAPPED, 'DOCTOR_SINCE': ''}).doctor_since == ''


@pytest.mark.parametrize('value', ['latest', '2', '2.462.1.4', ' 2.x', '2.462-rc'])
def test_doctor_since_must_be_a_jenkins_version(value):
    with pytest.raises(ConfigError, match=rf"DOCTOR_SINCE must be a Jenkins version such as 2\.462\.1 \(got '{value.strip()}'\)"):
        load_settings({**AIRGAPPED, 'DOCTOR_SINCE': value})


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


URL_SETTINGS = ['REPO_API_URL', 'RESOURCES_URL', 'POPULARITY_JSON_URL', 'TOOL_INSTALLERS_URL', 'HTTPS_PROXY', 'HTTP_PROXY']


@pytest.mark.parametrize('bad', ['https://[bad', 'http://host:abc'])
@pytest.mark.parametrize('name', URL_SETTINGS)
def test_every_url_setting_is_parsed_when_the_settings_load(name, bad):
    with pytest.raises(ConfigError, match=f'^{name} is not a valid URL: '):
        load_settings({**AIRGAPPED, name: bad})


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
    assert load_settings({**AIRGAPPED, name: value})


PASSWORD_DELIMITERS = ['#', '?', '/', '[', ']']
PASSWORD_FORMS = ['http://proxyuser:{}@proxy.corp:3128', 'proxyuser:{}@proxy.corp:3128']  # pragma: allowlist secret


@pytest.mark.parametrize('delimiter', PASSWORD_DELIMITERS)
@pytest.mark.parametrize('form', PASSWORD_FORMS, ids=['scheme', 'no-scheme'])
def test_a_proxy_password_may_hold_what_ends_a_url_host(delimiter, form):
    # urllib's proxy handler takes the user info up to the last @, so these work there and must not stop uc.
    value = form.format(f'S3cret{delimiter}Tail9z')
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}).https_proxy == value


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
        load_settings({**AIRGAPPED, 'HTTPS_PROXY': value})
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
        load_settings({**AIRGAPPED, name: value})
    for part in ('svc', 'S3cretPW', 'proxy.corp'):
        assert part not in str(error.value)


AT_AFTER_AUTHORITY_PROXIES = ['http://proxyuser:S3cretPW@Tail/9z@proxy.corp:3128', 'http://proxyuser:S3cretPW@proxy.corp:3128/x@other.corp', '//proxyuser:S3cretPW@Tail/9z@proxy.corp:3128']  # pragma: allowlist secret


@pytest.mark.parametrize('name', ['HTTPS_PROXY', 'HTTP_PROXY'])
@pytest.mark.parametrize('value', AT_AFTER_AUTHORITY_PROXIES)
def test_a_proxy_with_an_at_after_the_end_of_urllibs_authority_is_refused(name, value):
    # urllib ends the authority at the first / after the first @ and takes the host from before that /; uc reads it after the last @.
    with pytest.raises(ConfigError, match=f'^{name} {PROXY_REFUSAL}') as error:
        load_settings({**AIRGAPPED, name: value})
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
        load_settings({**AIRGAPPED, name: value})
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
        load_settings({**AIRGAPPED, 'HTTPS_PROXY': value})
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
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}).https_proxy == value


ATS_IN_USER_INFO_PROXIES = ['proxyuser:S3cretPW@Tail/9z@proxy.corp:3128', 'http://proxyuser:S3cretPW/Tail@9z@proxy.corp:3128', 'http://proxyuser:S3cretPW@Tail9z@proxy.corp:3128/']  # pragma: allowlist secret


@pytest.mark.parametrize('value', ATS_IN_USER_INFO_PROXIES)
def test_a_proxy_whose_ats_are_all_in_urllibs_user_info_still_loads(value):
    # Without // urllib reads the whole value as the authority; with it, a / before the first @ or after the last one ends nothing early.
    assert load_settings({**AIRGAPPED, 'HTTPS_PROXY': value}).https_proxy == value


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
NO_AT_SETTINGS = ('PUBLIC_URL', 'REPO_URL', 'DOWNLOAD_BACKEND_URL', 'PROCUREMENT_SOURCE_URL')
"""The URL settings that never carry credentials, so any @ in them is refused."""


def refused(name):
    """Return how the message refusing a URL setting's malformed value starts: an @ in a setting that never carries credentials is refused before the URL is parsed."""
    return f'^{name} must not contain credentials or an @' if name in NO_AT_SETTINGS else f'^{name} is not a valid URL: '


@pytest.mark.parametrize('name', ALL_URL_SETTINGS)
def test_a_malformed_url_message_does_not_quote_the_value(name):
    with pytest.raises(ConfigError, match=refused(name)) as error:
        load_settings({**AIRGAPPED, name: 'https://admin:S3cretPW@host.corp:wxyz/'})  # pragma: allowlist secret
    for part in ('admin', 'S3cretPW', 'wxyz'):
        assert part not in str(error.value)


@pytest.mark.parametrize('name', [name for name in ALL_URL_SETTINGS if not name.endswith('_PROXY')])
def test_a_password_that_ends_the_host_early_is_not_quoted(name):
    # Only the proxies take the user info up to the last @; elsewhere urlsplit reads S3cretPW as the port.
    with pytest.raises(ConfigError, match=refused(name)) as error:
        load_settings({**AIRGAPPED, name: 'https://admin:S3cretPW#x@host.corp/'})  # pragma: allowlist secret
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


@pytest.mark.parametrize('value', AT_VALUES.values(), ids=AT_VALUES.keys())
@pytest.mark.parametrize('name', NO_AT_SETTINGS)
def test_any_at_is_refused_in_a_url_setting_that_never_carries_credentials(name, value):
    # urlsplit ends the user info at a # ? or /, and would read a token before one as the host.
    with pytest.raises(ConfigError, match=f'^{name} must not contain credentials or an @') as error:
        load_settings({**AIRGAPPED, name: value})
    assert TOKEN not in str(error.value)


ENCODED_AT = 'https://host.corp/jenkins%40corp'  # an encoded @ is not one


@pytest.mark.parametrize(
    ('load', 'name', 'field', 'expected'),
    [
        (load_settings, 'PUBLIC_URL', 'public_url', ENCODED_AT),
        (load_settings, 'REPO_URL', 'repo_url', ENCODED_AT + '/'),
        (load_settings, 'DOWNLOAD_BACKEND_URL', 'download_backend_url', ENCODED_AT),
        (load_settings, 'PROCUREMENT_SOURCE_URL', 'procurement_source_url', ENCODED_AT + '/'),
    ],
)
def test_a_url_setting_that_never_carries_credentials_loads_without_an_at(load, name, field, expected):
    assert getattr(load({**AIRGAPPED, name: ENCODED_AT}), field) == expected


@pytest.mark.parametrize('value', ['https://[uc.example.com', 'https://uc:abc'], ids=['bracket', 'port'])
def test_a_malformed_public_url_is_a_configuration_error(value):
    with pytest.raises(ConfigError, match=r'^PUBLIC_URL is not a valid URL: '):
        load_settings({**AIRGAPPED, 'PUBLIC_URL': value})


def test_an_unset_public_url_is_still_reported_as_unset():
    with pytest.raises(ConfigError, match=r'^PUBLIC_URL must be set$'):
        load_settings({'MODE': 'online', 'PUBLIC_URL': ' '})


def test_every_setting_has_a_settings_field():
    s = load_settings(ONLINE)
    for setting in SETTINGS:
        if setting.attribute is not None:
            assert hasattr(s, setting.attribute), setting.env


@pytest.mark.parametrize('env', [ONLINE, AIRGAPPED])
def test_the_registry_defaults_are_the_defaults(env):
    s = load_settings(env)
    for setting in SETTINGS:
        default = setting.default_for(env['MODE'])
        if setting.attribute is None or not default:
            continue
        value = getattr(s, setting.attribute)
        assert (str(value).lower() if isinstance(value, bool) else str(value)) == default, setting.env


def test_the_jvm_and_tls_settings_have_fields():
    s = load_settings({**ONLINE, 'JAVA_TOOL_OPTIONS': '-Xmx6g', 'TLS_CERT_FILE': '/tls/c.pem', 'TLS_KEY_FILE': '/tls/k.pem'})
    assert (s.java_tool_options, s.tls_cert_file, s.tls_key_file) == ('-Xmx6g', '/tls/c.pem', '/tls/k.pem')


def test_artifactory_downloads_need_a_url():
    with pytest.raises(ConfigError, match=r'^DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL$'):
        load_settings({'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins'})


@pytest.mark.parametrize('mode', ['online', 'airgapped'])
def test_the_download_url_is_taken_as_set_without_its_trailing_slash(mode):
    env = {'MODE': mode, 'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins', 'DOWNLOAD_BACKEND': 'artifactory', 'DOWNLOAD_BACKEND_URL': 'https://repo.corp/artifactory/jenkins/'}
    assert load_settings(env).download_backend_url == 'https://repo.corp/artifactory/jenkins'


def test_the_mirror_defaults_to_get_jenkins_io():
    assert load_settings({'MODE': 'online', 'PUBLIC_URL': 'https://uc.example.com'}).download_backend_url == 'https://get.jenkins.io'


def test_internal_paths_are_fixed_unless_a_test_overrides_them(tmp_path):
    s = load_settings(ONLINE)
    assert (s.data_dir, s.overlay_dir, s.app_dir, s.secrets_dir) == (Path('/data'), Path('/config/overlay'), Path('/opt/uc'), Path('/run/secrets'))
    assert (s.repo_adapter_url, s.repo_adapter_dir, s.procurement_staging_dir) == ('http://repo-adapter:8081/artifactory/', Path('/data/repo-adapter'), Path('/procurement'))
    assert s.signing_key_file == Path('/run/secrets/update-center.key')
    moved = load_settings({**ONLINE, 'UC_DATA_DIR': str(tmp_path / 'd'), 'SECRETS_DIR': str(tmp_path / 's'), 'REPO_ADAPTER_URL': 'http://a:9/artifactory', 'REPO_ADAPTER_DIR': str(tmp_path / 'a')})
    assert (moved.data_dir, moved.repo_adapter_dir, moved.repo_adapter_url) == (tmp_path / 'd', tmp_path / 'a', 'http://a:9/artifactory/')
    assert moved.repo_push_auth_file == tmp_path / 's' / 'repo_push_auth'


def test_the_adapter_directory_follows_the_data_directory(tmp_path):
    assert load_settings({**ONLINE, 'UC_DATA_DIR': str(tmp_path)}).repo_adapter_dir == tmp_path / 'repo-adapter'


def test_internal_paths_are_not_settings():
    for name in ('UC_DATA_DIR', 'UC_OVERLAY_DIR', 'UC_APP_DIR', 'SECRETS_DIR', 'REPO_ADAPTER_URL', 'REPO_ADAPTER_DIR', 'REPO_ADAPTER_PORT', 'PROCUREMENT_STAGING_DIR', 'DOWNLOAD_PROXY_AUTH_FILE', 'SIGNING_KEY_FILE', 'REPO_PUSH_AUTH_FILE'):
        assert name not in REGISTRY, name
