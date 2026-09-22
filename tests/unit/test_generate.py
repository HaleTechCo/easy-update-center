import datetime as dt
import os

import pytest
from fakejar import FakeJar, SimpleVersions
from sitefixture import DEFAULT_PLUGINS
from ucsupport import make_settings

from uc.aql import RepoFile
from uc.generate import Toolchain, generate
from uc.htaccess import absolute_targets
from uc.jar import JarError
from uc.model import Tiers
from uc.pki import cert_der_b64
from uc.preflight import PreflightError
from uc.publish import LockBusy, RunLock, list_releases, live_release
from uc.status import read_status
from uc.validate import ValidationError

T1 = dt.datetime(2026, 9, 22, 3, 0, tzinfo=dt.timezone.utc)
T2 = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.timezone.utc)
TIERS = Tiers(("2.540",), ("2.528.1",))


def repo_files():
    files = []
    for plugin_id, version in DEFAULT_PLUGINS.items():
        for ext in ("hpi", "pom"):
            files.append(RepoFile(f"org/jenkins-ci/plugins/{plugin_id}/{version}", f"{plugin_id}-{version}.{ext}"))
    for ext in ("war", "pom"):
        files.append(RepoFile("org/jenkins-ci/main/jenkins-war/2.528.1", f"jenkins-war-2.528.1.{ext}"))
    return files


def no_network(url, headers=None, timeout=60):
    raise AssertionError(f"unexpected network access: {url}")


def no_query(*args):
    raise AssertionError("AQL must not be queried with the mirror backend")


def tools_for(settings, **jar_options):
    return Toolchain(
        run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file), **jar_options),
        aql_query=lambda *args: repo_files(),
        http_get=no_network,
        versions=SimpleVersions(),
    )


def test_airgapped_generation_publishes(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    rid = generate(settings, tools=tools_for(settings), now=T1, base_env={"UC2_VERSION": "3.18.4+popularity-patch"})
    data = settings.data_dir
    live = data / "live"
    assert rid == "20260922T030000Z"
    assert live_release(data) == rid
    assert (live / "current" / "update-center.json").is_file()
    assert os.readlink(live / "stable") == "dynamic-stable-2.528.1"
    assert absolute_targets((live / ".htaccess").read_text()) == []
    mapping = (live / ".htdownload-map").read_text()
    assert "plugins/git/5.2.0 org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi\n" in mapping
    assert "war/2.528.1 org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war\n" in mapping
    status = read_status(data)
    assert status["live_release"] == rid
    assert status["last_run"]["result"] == "success"
    assert status["update_center2"] == "3.18.4+popularity-patch"
    assert status["resources"]["source"] == "bundled"
    assert status["tiers"] == {"weekly": ["2.540"], "stable": ["2.528.1"]}
    assert status["plugin_count"] == 2
    assert status["doctor"]["plugins"] == 2
    assert any("GitHub credentials not set" in w for w in status["warnings"])
    assert (data / "resources" / "last-good" / "warnings.json").is_file()
    assert not list((data / "releases").glob("*.staging"))


def test_jar_receives_download_root_and_credentials(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    tools = tools_for(settings)
    generate(settings, tools=tools, now=T1, base_env={})
    env = tools.run_jar.envs[-1]
    assert env["DOWNLOADS_ROOT_URL"] == "https://uc.example.com/download"
    assert env["ARTIFACTORY_PASSWORD"] == "secret"
    assert env["POPULARITY_JSON_URL"] == ""
    assert "--arguments-file" in tools.run_jar.calls[-1]


def test_failed_validation_keeps_the_live_release(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    first = generate(settings, tools=tools_for(settings), now=T1, base_env={})
    with pytest.raises(ValidationError, match="download URLs outside"):
        generate(settings, tools=tools_for(settings, url_root="https://updates.jenkins.io"), now=T2, base_env={})
    data = settings.data_dir
    assert live_release(data) == first
    status = read_status(data)
    assert status["live_release"] == first
    assert status["last_run"]["result"] == "failed"
    assert "download URLs outside" in status["last_run"]["error"]
    assert (data / "releases" / ".failed-20260922T090000Z").is_dir()


def test_jar_failure_is_recorded(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(JarError):
        generate(settings, tools=tools_for(settings, fail_sites=True), now=T1, base_env={})
    assert read_status(settings.data_dir)["last_run"]["result"] == "failed"
    assert live_release(settings.data_dir) is None


def test_preflight_failure_creates_no_staging(tmp_path, secrets_dir):
    (secrets_dir / "update-center.key").unlink()
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(PreflightError):
        generate(settings, tools=tools_for(settings), now=T1, base_env={})
    releases = settings.data_dir / "releases"
    assert not releases.exists() or list(releases.iterdir()) == []


def test_plugin_count_drop_needs_force(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    generate(settings, tools=tools_for(settings), now=T1, base_env={})
    shrunk = {"plugins": {"git": "5.2.0"}}
    with pytest.raises(ValidationError, match="plugin count 1 < 0.9"):
        generate(settings, tools=tools_for(settings, **shrunk), now=T2, base_env={})
    rid = generate(settings, force=True, tools=tools_for(settings, **shrunk),
                   now=T2 + dt.timedelta(minutes=1), base_env={})
    assert live_release(settings.data_dir) == rid


def test_online_generation_uses_mirror_and_tool_installers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, MODE="online", ARTIFACTORY_URL="", ARTIFACTORY_REPOSITORY="",
                             RESOURCES_SOURCE="bundled", TOOL_INSTALLERS_URL="https://updates.example/updates/")
    files = {
        "https://updates.example/updates/": b'<a href="hudson.tasks.Maven.MavenInstaller.json">m</a>',
        "https://updates.example/updates/hudson.tasks.Maven.MavenInstaller.json": b"downloadService.post('m',{})",
    }
    tools = Toolchain(run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file)), aql_query=no_query,
                      http_get=lambda url, headers=None, timeout=60: files[url])
    generate(settings, tools=tools, now=T1, base_env={})
    live = settings.data_dir / "live"
    assert (live / ".htdownload-map").read_text() == ""
    assert (live / "current" / "updates" / "hudson.tasks.Maven.MavenInstaller.json").is_file()
    status = read_status(settings.data_dir)
    assert status["download_backend"] == "mirror"
    assert status["doctor"] is None
    assert tools.run_jar.envs[-1]["POPULARITY_JSON_URL"].startswith("https://raw.githubusercontent.com/")
    assert tools.run_jar.envs[-1]["ARTIFACTORY_URL"] == "https://repo.jenkins-ci.org:443/"


def test_lock_prevents_concurrent_runs(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    settings.data_dir.mkdir(parents=True)
    with RunLock(settings.data_dir):
        with pytest.raises(LockBusy):
            generate(settings, tools=tools_for(settings), now=T1, base_env={})


def test_old_releases_are_pruned(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RETAIN_RELEASES="2")
    for hour in range(4):
        generate(settings, tools=tools_for(settings), now=T1 + dt.timedelta(hours=hour), base_env={})
    assert list_releases(settings.data_dir) == ["20260922T050000Z", "20260922T060000Z"]
