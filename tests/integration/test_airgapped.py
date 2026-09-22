"""Air-gapped end to end (spec §13): doctor, generate, serve, and real Jenkins controllers installing plugins."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jenkins import Jenkins  # noqa: E402
from netutil import fetch, get, wait_for  # noqa: E402

from uc.versions import VersionTool  # noqa: E402

WEB = "http://web:8080"
WEB_PROXY = "http://web-proxy:8080"
CONTROLLERS = {
    "jenkins-lts-old": os.environ["LTS_OLD"],
    "jenkins-lts-new": os.environ["LTS_NEW"],
    "jenkins-weekly": os.environ["WEEKLY"],
}
TOP_PLUGIN = os.environ["SEED_TOP_PLUGIN"]
SEEDED = json.loads(Path("/seed/repo/seeded.json").read_text())
VERSIONS = VersionTool(Path("/opt/uc/generator"), Path("/opt/uc/tools"))

CONFIGURE = """
import hudson.model.UpdateSite
import jenkins.model.Jenkins
def uc = Jenkins.get().updateCenter
uc.sites.replaceBy([new UpdateSite("default", "%(site)s/update-center.json")])
uc.save()
def site = uc.getSite("default")
def result = site.updateDirectlyNow()
println("UPDATE=" + result.kind)
println("MESSAGE=" + (result.message ?: ""))
println("CORE=" + site.data?.core?.version)
println("PLUGINS=" + (site.data?.plugins?.size() ?: 0))
"""

INSTALL = """
import jenkins.model.Jenkins
def futures = Jenkins.get().pluginManager.install(["%(plugin)s"], true)
println("INSTALL=" + futures.collect { it.get().status.getClass().simpleName }.join(","))
"""


def uc(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "uc", *args], env={**os.environ, **(env or {})},
                          capture_output=True, text=True)


def parse(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def test_doctor_reports_the_seeded_defects():
    result = uc("doctor", "--fetch", "--json")
    assert result.returncode == 1, result.stderr  # something is dropped, by design
    report = json.loads(result.stdout)
    assert report["hpi_without_pom"] == ["io/jenkins/uctest/uc-test-nopom/1.0/uc-test-nopom-1.0.hpi"]
    assert "uc-test-stubpom:1.0" in report["stub_poms"]
    assert report["missing_dependencies"] == []
    assert sorted(report["cores"]) == sorted(set(SEEDED["cores"]))


@pytest.fixture(scope="module")
def release() -> dict:
    result = uc("generate")
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB + "/uctest.json")
    return json.loads(Path("/data/status.json").read_text())


def test_generation_succeeds_offline_with_only_warnings(release):
    assert release["last_run"]["result"] == "success"
    assert release["mode"] == "airgapped"
    assert any("GitHub" in warning for warning in release["warnings"])
    assert release["doctor"]["hpi_without_pom"] == 1
    plugins = json.loads(get(WEB + "/current/update-center.actual.json"))["plugins"]
    assert "uc-test-stubpom" in plugins  # a stub POM degrades metadata but is still published
    assert "uc-test-nopom" not in plugins  # no POM: dropped by the generator, as uc doctor predicted
    assert TOP_PLUGIN in plugins
    assert release["plugin_count"] == release["doctor"]["plugins"] == len(plugins)


def test_signed_metadata_points_at_the_update_center(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    urls = [doc["core"]["url"]] + [p["url"] for p in doc["plugins"].values()]
    assert all(url.startswith(WEB + "/download/") for url in urls)
    assert doc["connectionCheckUrl"] == WEB + "/uctest.json"


def test_downloads_redirect_into_artifactory(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    plugin = doc["plugins"][TOP_PLUGIN]
    response = fetch(plugin["url"])
    assert response.status == 302
    assert response.location.startswith("http://artifactory:8081/artifactory/jenkins/")
    assert len(get(response.location)) > 0


@pytest.mark.parametrize("controller", sorted(CONTROLLERS))
def test_routing_offers_only_compatible_plugins(release, controller):
    version = CONTROLLERS[controller]
    response = fetch(f"{WEB}/update-center.actual.json?version={version}")
    assert response.status in (301, 302)
    assert response.location.startswith(WEB + "/")
    doc = json.loads(get(response.location))
    required = [p["requiredCore"] for p in doc["plugins"].values()]
    assert all(c <= 0 for c in VERSIONS.compare_many([(r, version) for r in required]))


@pytest.mark.parametrize("controller", ["jenkins-lts-old", "jenkins-lts-new"])
def test_controller_trusts_the_signature_and_installs(release, controller):
    version = CONTROLLERS[controller]
    jenkins = Jenkins(f"http://{controller}:8080")
    jenkins.wait_ready()
    assert jenkins.version() == version
    configured = parse(jenkins.script(CONFIGURE % {"site": WEB}))
    assert configured["UPDATE"] == "OK", configured.get("MESSAGE")
    tier = json.loads(get(fetch(f"{WEB}/update-center.actual.json?version={version}").location))
    assert int(configured["PLUGINS"]) == len(tier["plugins"])  # Jenkins loaded the tier for its version
    installed = parse(jenkins.script(INSTALL % {"plugin": TOP_PLUGIN}))["INSTALL"].split(",")
    assert installed and all(status.startswith("Success") for status in installed), installed


def test_proxy_mode_installs_without_anonymous_artifactory_access(release):
    result = uc("generate", env={"PUBLIC_URL": WEB_PROXY, "UC_DATA_DIR": "/data-proxy"})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB_PROXY + "/uctest.json")
    jenkins = Jenkins("http://jenkins-weekly:8080")
    jenkins.wait_ready()
    configured = parse(jenkins.script(CONFIGURE % {"site": WEB_PROXY}))
    assert configured["UPDATE"] == "OK", configured.get("MESSAGE")
    installed = parse(jenkins.script(INSTALL % {"plugin": TOP_PLUGIN}))["INSTALL"].split(",")
    assert installed and all(status.startswith("Success") for status in installed), installed


def test_rollback_restores_the_previous_release(release):
    first = release["live_release"]
    result = uc("generate", "--force")
    assert result.returncode == 0, result.stderr[-4000:]
    second = json.loads(Path("/data/status.json").read_text())["live_release"]
    assert second != first
    assert uc("rollback").returncode == 0
    assert json.loads(Path("/data/status.json").read_text())["live_release"] == first
    assert os.readlink("/data/live") == f"releases/{first}"
