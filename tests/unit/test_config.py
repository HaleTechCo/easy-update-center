from pathlib import Path

import pytest

from uc.config import load_settings, read_secret, with_explicit_port
from uc.errors import ConfigError

AIRGAPPED = {
    "MODE": "airgapped",
    "PUBLIC_URL": "https://uc.example.com",
    "REPO_URL": "https://artifactory.corp/artifactory",
    "REPO_KEY": "jenkins-local",
}
KEEPER = {**AIRGAPPED, "REPO_TYPE": "artifact-keeper", "REPO_URL": "https://ak.corp", "REPO_KEY": "jenkins-prod-local"}


def test_online_defaults():
    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc.example.com/"})
    assert s.public_url == "https://uc.example.com"
    assert s.downloads_root_url == "https://uc.example.com/download"
    assert s.repo_type == "artifactory"
    assert s.repo_url == "https://repo.jenkins-ci.org/"
    assert s.repo_url_with_port == "https://repo.jenkins-ci.org:443/"
    assert s.repo_api_url == "https://repo.jenkins-ci.org:443/api/"
    assert s.repo_key == "releases"
    assert s.repo_adapter is False
    assert (s.source_url, s.source_api_url) == ("https://repo.jenkins-ci.org:443/", "https://repo.jenkins-ci.org:443/api/")
    assert s.core_age_days == 400
    assert s.repo_max_archive_mb == 512
    assert s.repo_ca_file is None
    assert s.download_backend == "mirror"
    assert s.download_backend_url == "https://get.jenkins.io"
    assert s.download_access == "redirect"
    assert s.resources_source == "github"
    assert s.resources_ref == "master"
    assert s.popularity_json_url.startswith("https://raw.githubusercontent.com/")
    assert s.tool_installers_url == "https://updates.jenkins.io/updates/"
    assert (s.retain_releases, s.min_plugins, s.min_plugin_ratio) == (5, 1, 0.9)
    assert (s.certificate_minimum_valid_days, s.signing_cert_days, s.stale_after_days) == (30, 365, 30)
    assert s.generate_schedule == "0 */6 * * *"
    assert s.uc_id == "default"
    assert s.extra_args == ()
    assert s.signing_key_file == Path("/run/secrets/update-center.key")
    assert s.root_ca_file == Path("/run/secrets/root-ca.crt")
    assert s.data_dir == Path("/data")
    assert s.jar == Path("/opt/uc/generator/update-center2.jar")
    assert s.cache_dir == Path("/data/cache/artifactory")
    assert s.repo_adapter_dir == Path("/data/repo-adapter")
    assert s.repo_adapter_token_file == Path("/data/repo-adapter/token")
    assert s.repo_adapter_report_file == Path("/data/repo-adapter/report.json")
    assert s.repo_username_file == Path("/run/secrets/repo_username")


def test_airgapped_defaults():
    s = load_settings(AIRGAPPED)
    assert s.repo_url == "https://artifactory.corp/artifactory/"
    assert s.repo_url_with_port == "https://artifactory.corp:443/artifactory/"
    assert s.repo_adapter is True
    assert s.repo_adapter_url == "http://repo-adapter:8081/artifactory/"
    assert (s.source_url, s.source_api_url) == (
        "http://repo-adapter:8081/artifactory/", "http://repo-adapter:8081/artifactory/api/")
    assert s.core_age_days == 0
    assert s.download_backend == "artifactory"
    assert s.download_backend_url == "https://artifactory.corp/artifactory/jenkins-local"
    assert s.resources_source == "bundled"
    assert s.popularity_json_url == ""
    assert s.tool_installers_url == ""


def test_airgapped_requires_a_repository():
    with pytest.raises(ConfigError, match="REPO_URL and REPO_KEY must be set"):
        load_settings({"MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com"})


def test_artifact_keeper_defaults():
    s = load_settings(KEEPER)
    assert s.repo_type == "artifact-keeper"
    assert s.repo_url == "https://ak.corp/"
    assert s.repo_adapter is True
    assert s.download_backend_url == "https://ak.corp/maven/jenkins-prod-local"


def test_artifact_keeper_online_needs_its_own_url_and_forces_the_adapter():
    with pytest.raises(ConfigError, match="REPO_URL and REPO_KEY must be set"):
        load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "REPO_TYPE": "artifact-keeper"})
    s = load_settings({**KEEPER, "MODE": "online"})
    assert s.repo_adapter is True
    assert s.core_age_days == 400


def test_artifact_keeper_refuses_the_adapter_off():
    with pytest.raises(ConfigError, match="REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on"):
        load_settings({**KEEPER, "REPO_ADAPTER": "off"})


def test_the_generator_cache_is_separate_with_the_adapter():
    """Cache keys are URL paths, and /artifactory/<key>/... is the same with the adapter on or off."""
    assert load_settings(AIRGAPPED).cache_dir == Path("/data/cache/repo-adapter")
    assert load_settings({**AIRGAPPED, "REPO_ADAPTER": "off"}).cache_dir == Path("/data/cache/artifactory")


def test_adapter_settings_can_be_overridden():
    s = load_settings({
        **AIRGAPPED, "REPO_ADAPTER_URL": "http://127.0.0.1:9999/artifactory", "REPO_ADAPTER_DIR": "/state",
        "REPO_CA_FILE": "/ca/corp.pem", "REPO_MAX_ARCHIVE_MB": "64", "CORE_AGE_DAYS": "400",
    })
    assert s.source_url == "http://127.0.0.1:9999/artifactory/"
    assert s.repo_adapter_token_file == Path("/state/token")
    assert s.repo_ca_file == Path("/ca/corp.pem")
    assert (s.repo_max_archive_mb, s.core_age_days) == (64, 400)


def test_online_private_artifactory_can_turn_the_adapter_on():
    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "REPO_URL": "https://art.corp/artifactory/",
                       "REPO_KEY": "jenkins", "REPO_ADAPTER": "on"})
    assert s.repo_adapter is True
    assert s.source_url == "http://repo-adapter:8081/artifactory/"


@pytest.mark.parametrize("mode", ["", "offline"])
def test_mode_is_required_and_validated(mode):
    with pytest.raises(ConfigError, match="MODE must be one of online, airgapped"):
        load_settings({"MODE": mode, "PUBLIC_URL": "https://uc.example.com"})


def test_public_url_is_required():
    with pytest.raises(ConfigError, match="PUBLIC_URL must be set"):
        load_settings({"MODE": "online"})


def test_explicit_empty_popularity_url_disables_it_online():
    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "POPULARITY_JSON_URL": ""})
    assert s.popularity_json_url == ""


def test_resources_url_required_for_url_source():
    with pytest.raises(ConfigError, match="RESOURCES_URL must be set"):
        load_settings({**AIRGAPPED, "RESOURCES_SOURCE": "url"})


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("MIN_PLUGIN_RATIO", "1.5", "between 0 and 1"),
        ("RETAIN_RELEASES", "abc", "must be an integer"),
        ("RETAIN_RELEASES", "0", "must be >= 1"),
        ("DOWNLOAD_BACKEND", "s3", "DOWNLOAD_BACKEND must be one of"),
        ("DOWNLOAD_ACCESS", "cache", "DOWNLOAD_ACCESS must be one of"),
        ("ALLOW_LOCAL_PUBLIC_URL", "maybe", "true or false"),
        ("REPO_KEY", 'a"b', "unsupported characters"),
        ("REPO_TYPE", "nexus", "REPO_TYPE must be one of artifactory, artifact-keeper"),
        ("REPO_ADAPTER", "sometimes", "true or false"),
        ("CORE_AGE_DAYS", "-1", "must be >= 0"),
        ("CORE_AGE_DAYS", "ten", "must be an integer"),
        ("REPO_MAX_ARCHIVE_MB", "0", "must be >= 1"),
    ],
)
def test_invalid_values_are_rejected(name, value, message):
    with pytest.raises(ConfigError, match=message):
        load_settings({**AIRGAPPED, name: value})


def test_backend_url_override_and_extra_args():
    s = load_settings({
        **AIRGAPPED,
        "DOWNLOAD_BACKEND_URL": "https://dl.corp/jenkins/",
        "UC_EXTRA_ARGS": "--max-plugins 20",
    })
    assert s.download_backend_url == "https://dl.corp/jenkins"
    assert s.extra_args == ("--max-plugins", "20")


def test_secret_paths_follow_secrets_dir_and_overrides():
    s = load_settings({**AIRGAPPED, "SECRETS_DIR": "/pki", "GITHUB_TOKEN_FILE": "/other/token"})
    assert s.signing_cert_file == Path("/pki/update-center.cert")
    assert s.repo_password_file == Path("/pki/repo_password")
    assert s.github_token_file == Path("/other/token")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://a.b/x/", "https://a.b:443/x/"),
        ("http://a.b/", "http://a.b:80/"),
        ("https://a.b:8443/", "https://a.b:8443/"),
        ("http://[::1]/", "http://[::1]:80/"),
    ],
)
def test_with_explicit_port(url, expected):
    assert with_explicit_port(url) == expected


def test_credentials_in_repo_url_are_rejected():
    with pytest.raises(ConfigError, match="credentials"):
        load_settings({**AIRGAPPED, "REPO_URL": "https://u:p@artifactory.corp/"})  # pragma: allowlist secret


def test_read_secret(tmp_path):
    assert read_secret(tmp_path / "missing") is None
    (tmp_path / "empty").write_text("\n")
    assert read_secret(tmp_path / "empty") is None
    (tmp_path / "value").write_text("s3cret\n")
    assert read_secret(tmp_path / "value") == "s3cret"
