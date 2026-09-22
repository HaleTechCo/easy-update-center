import base64
import json

import pytest
from helpers import fetch

ARTIFACTORY = "http://artifactory.internal/artifactory/jenkins"


def test_mirror_plugin_redirect():
    response = fetch("web-full", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 302
    assert response.location == "https://get.jenkins.io/plugins/git/5.2.0/git.hpi"


@pytest.mark.parametrize(
    "version,target",
    [
        ("2.222.1", "https://get.jenkins.io/war-stable/2.222.1/jenkins.war"),
        ("2.240", "https://get.jenkins.io/war/2.240/jenkins.war"),
    ],
)
def test_mirror_war_redirects_split_like_upstream(version, target):
    assert fetch("web-full", f"/download/war/{version}/jenkins.war").location == target


def test_latest_links_resolve_locally():
    assert fetch("web-full", "/download/plugins/git/latest/git.hpi").location == "http://web-full:8080/latest/git.hpi"
    assert fetch("web-full", "/download/war/latest/jenkins.war").location == "http://web-full:8080/latest/jenkins.war"
    response = fetch("web-full", "/latest/git.hpi")
    assert response.status == 302
    assert response.location == "http://web-full:8080/download/plugins/git/5.2.0/git.hpi"


def test_artifactory_map_hit_and_miss():
    response = fetch("web-artifactory", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 302
    assert response.location == f"{ARTIFACTORY}/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
    assert fetch("web-artifactory", "/download/plugins/git/9.9.9/git.hpi").status == 404
    war = fetch("web-artifactory", "/download/war/2.528.1/jenkins.war")
    assert war.location == f"{ARTIFACTORY}/org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war"
    assert fetch("web-artifactory", "/download/war/2.540/jenkins.war").status == 404


def test_proxy_mode_streams_with_credentials():
    response = fetch("web-proxy", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 200
    echoed = json.loads(response.body)
    assert echoed["path"] == "/repo/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
    assert echoed["authorization"] == "Basic " + base64.b64encode(b"routing:secret").decode()
    assert fetch("web-proxy", "/download/plugins/git/9.9.9/git.hpi").status == 404


def test_download_index_pages_are_served():
    assert fetch("web-full", "/download/plugins/git/").status == 200


def test_cache_headers():
    assert fetch("web-full", "/update-center.json?version=2.240").headers["cache-control"] == "public, max-age=300"
    assert fetch("web-full", "/download/plugins/git/5.2.0/git.hpi").headers["cache-control"] == "public, max-age=3600"
    assert fetch("web-full", "/status.json").headers["cache-control"] == "no-store"


def test_json_is_compressed_when_asked():
    response = fetch("web-full", "/current/update-center.actual.json", {"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


def test_private_files_are_not_served():
    assert fetch("web-full", "/.htdownload-map").status == 403
    assert fetch("web-full", "/.htaccess").status == 403


def test_status_landing_page_and_uctest():
    assert json.loads(fetch("web-full", "/status.json").body)["live_release"] == "20260101T000000Z"
    assert b"Jenkins Update Center" in fetch("web-full", "/").body
    assert fetch("web-full", "/uctest.json").body == b"{}\n"


def test_unknown_download_paths_are_404():
    assert fetch("web-full", "/download/something.zip").status == 404
