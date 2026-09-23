import base64
import hashlib
import json
import os
from pathlib import Path

from httpstub import StubServer
from ucsupport import make_settings

from uc.aql import Gav, RepoFile, classify
from uc.doctor import (
    ArtifactSource,
    analyze,
    attach_adapter_report,
    format_report,
    is_stub_pom,
    parse_dependencies,
    parse_manifest,
    run_doctor,
)
from uc.model import Credentials
from uc.versions import VersionTool

CREDS = Credentials("uc", "secret", "uc", "uc", True)
GIT = Gav("org.jenkins-ci.plugins", "git", "5.2.0", "hpi")


def versions():
    return VersionTool(Path("/opt/uc/generator"), Path("/opt/uc/tools"))


def plugin(artifact, version, *extensions, group="org/jenkins-ci/plugins"):
    return [RepoFile(f"{group}/{artifact}/{version}", f"{artifact}-{version}.{ext}") for ext in extensions]


class FakeSource:
    def __init__(self, poms, manifests):
        self.poms, self.manifests = poms, manifests

    def pom(self, gav):
        return self.poms.get(gav.artifact_id)

    def manifest(self, gav):
        return self.manifests.get(gav.artifact_id)


def test_parse_manifest_joins_continuation_lines_and_stops_at_first_section():
    text = ("Manifest-Version: 1.0\r\nShort-Name: git\r\nPlugin-Dependencies: credentials:2.6.1,scm-api:600.v\r\n"
            " 1;resolution:=optional\r\n\r\nName: extra\r\nShort-Name: other\r\n")
    assert parse_manifest(text) == {
        "Manifest-Version": "1.0",
        "Short-Name": "git",
        "Plugin-Dependencies": "credentials:2.6.1,scm-api:600.v1;resolution:=optional",
    }


def test_parse_dependencies():
    assert parse_dependencies("a:1.0,b:2.0;resolution:=optional, c:3") == [
        ("a", "1.0", False), ("b", "2.0", True), ("c", "3", False),
    ]
    assert parse_dependencies("") == []


def test_is_stub_pom():
    assert not is_stub_pom(b'<project xmlns="http://maven.apache.org/POM/4.0.0"><name>Git plugin</name></project>')
    assert is_stub_pom(b"<project><artifactId>stub</artifactId></project>")
    assert is_stub_pom(b"<project><name>  </name></project>")
    assert is_stub_pom(b"not xml")
    assert is_stub_pom(b'<!DOCTYPE p [<!ENTITY x "y">]><project><name>&x;</name></project>')


def test_analyze_reports_every_defect():
    listing = classify(
        plugin("git", "5.2.0", "hpi", "pom")
        + plugin("git", "5.10.0", "hpi", "pom")
        + plugin("credentials", "2.0", "hpi", "pom")
        + plugin("orphan", "1.0", "hpi")
        + plugin("stub", "1.0", "hpi", "pom")
        + plugin("jenkins-war", "2.528.1", "war", "pom", group="org/jenkins-ci/main")
        + plugin("jenkins-war", "2.540", "war", group="org/jenkins-ci/main")
        + [RepoFile("plugins/x/1.0", "x.hpi")]
    )
    source = FakeSource(
        poms={"git": b"<project><name>Git</name></project>", "credentials": b"<project><name>C</name></project>",
              "stub": b"<project/>"},
        manifests={"git": "Plugin-Dependencies: credentials:2.6.1,scm-api:1;resolution:=optional,workflow-step-api:1.0\n",
                   "stub": "Short-Name: stub\n"},
    )
    report = analyze(listing, versions(), source)
    assert report.plugins == 3
    assert report.cores == ["2.528.1"]
    assert report.hpi_without_pom == ["org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi"]
    assert report.war_without_pom == ["org/jenkins-ci/main/jenkins-war/2.540/jenkins-war-2.540.war"]
    assert report.non_maven == ["plugins/x/1.0/x.hpi"]
    assert report.stub_poms == ["stub:1.0"]
    assert report.missing_dependencies == [
        "git:5.10.0 requires workflow-step-api 1.0 (not in the repository)",
        "git:5.10.0 requires credentials >= 2.6.1 (newest available: 2.0)",
    ]
    assert report.unchecked == 1  # credentials has no manifest
    assert report.dropped == 3
    assert report.exit_code() == 1
    assert report.summary()["missing_dependencies"] == 2


def test_artifact_source_reads_the_update_center2_cache(tmp_path):
    source = ArtifactSource("http://artifactory:8081/artifactory/", "jenkins", "uc", "secret",
                            tmp_path / "cache", allow_network=False)
    manifest_path = "/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi!/META-INF/MANIFEST.MF"
    digest = hashlib.sha256(manifest_path.encode()).hexdigest()
    cached = tmp_path / "cache" / digest[:2] / digest
    cached.parent.mkdir(parents=True)
    cached.write_text("Short-Name: git\n")
    assert source.cache_file("http://artifactory:8081" + manifest_path) == cached
    assert source.manifest(GIT) == "Short-Name: git\n"
    assert source.pom(GIT) is None  # not cached and network not allowed
    pom_digest = hashlib.sha256(b"/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom").hexdigest()
    (tmp_path / "cache" / pom_digest[:2] / pom_digest).mkdir(parents=True)  # update-center2's cached failure
    assert source.pom(GIT) is None


def test_artifact_source_fetches_with_basic_auth():
    routes = {("GET", "/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"): (200, {}, b"<project/>")}
    with StubServer(routes) as stub:
        source = ArtifactSource(stub.url + "/artifactory/", "jenkins", "uc", "secret", None, allow_network=True)
        assert source.pom(GIT) == b"<project/>"
        assert source.manifest(GIT) is None  # 404
    assert stub.requests[0][2]["Authorization"] == "Basic " + base64.b64encode(b"uc:secret").decode()


def test_run_doctor_includes_live_tiers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    release = settings.data_dir / "releases" / "20260922T000000Z"
    release.mkdir(parents=True)
    (release / "tiers.json").write_text('{"weeklyCores": [], "stableCores": ["2.528.1"]}')
    os.symlink("releases/20260922T000000Z", settings.data_dir / "live")
    files = plugin("git", "5.2.0", "hpi", "pom") + plugin("jenkins-war", "2.528.1", "war", "pom", group="org/jenkins-ci/main")
    report = run_doctor(settings, CREDS, fetch=False, versions=VersionTool.from_settings(settings),
                        query=lambda *args: files)
    assert report.tiers == {"weeklyCores": [], "stableCores": ["2.528.1"]}
    assert report.unchecked == 1
    assert report.exit_code() == 0


ADAPTER_REPORT = {
    "derived_poms": ["io.jenkins.plugins:caffeine-api:3.2.4"],
    "replaced_poms": [{"path": "org/x/ad/1/ad-1.pom", "reason": "not XML"}],
    "needs_pom": [{"gav": "org.jenkins-ci.plugins:polaris:1.4.0",
                   "url": "https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/polaris/1.4.0/polaris-1.4.0.pom"}],
    "misplaced": [{"path": "org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi",
                   "correct_path": "io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi"}],
    "skipped": [{"path": "org/x/big/1/big-1.hpi", "reason": "larger than 512 MiB"}],
    "dates_from_repository": [{"path": "org/x/cas/1/cas-1.hpi", "reason": "archive date 1980-02-01 is before 2004"}],
}


def test_the_adapter_report_adds_sections_and_counts_its_drops(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
    settings.repo_adapter_dir.mkdir(parents=True)
    settings.repo_adapter_report_file.write_text(json.dumps({"archives": 5, **ADAPTER_REPORT}))
    listing = classify(plugin("polaris", "1.4.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
    report = attach_adapter_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert report.dropped == 3  # polaris (no POM at all), the misplaced and the skipped archive
    assert report.summary()["repo_adapter"] == {name: 1 for name in ADAPTER_REPORT}
    text = format_report(report, settings)
    assert "(through repo-adapter)" in text
    assert "procure the real .pom (1):" in text and ADAPTER_REPORT["needs_pom"][0]["url"] in text
    assert "-> " + "io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi" in text.replace("  ->  ", " -> ")
    assert "POMs filled in from archives (1):" in text
    assert "org/x/ad/1/ad-1.pom: not XML" in text
    assert "archive date 1980-02-01 is before 2004" in text


def test_without_the_adapter_there_is_no_adapter_report(tmp_path, secrets_dir):
    report = attach_adapter_report(analyze(classify([]), versions(), FakeSource({}, {})),
                                   make_settings(tmp_path, secrets_dir))
    assert report.repo_adapter is None and report.summary()["repo_adapter"] is None


def test_format_report(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    listing = classify(plugin("orphan", "1.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
    text = format_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert "Source: artifactory repository jenkins at http://artifactory:8081/artifactory/" in text
    assert "through repo-adapter" not in text
    assert "Jenkins cores with a POM: none" in text
    assert "Dropped by the generator: 1" in text
    assert "org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi" in text
    assert "Not checked (POM or manifest unavailable; run with --fetch): 1" in text
