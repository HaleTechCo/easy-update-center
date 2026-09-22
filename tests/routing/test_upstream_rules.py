"""Upstream's redirect expectations (site/test/test.sh at 3.18.4), ported so that a mismatch fails."""
import pytest
from helpers import fetch

HOST = "web-full"
BASE = "http://web-full:8080"
UC = "/update-center.json?version="
ACTUAL = "/update-center.actual.json?version="

EXPECTED = [
    ("/update-center.json", "/current/update-center.json"),
    ("/update-center.actual.json", "/current/update-center.actual.json"),
    ("/release-history.json", "/current/release-history.json"),
    ("/plugin-versions.json", "/current/plugin-versions.json"),
    ("/plugin-documentation-urls.json", "/current/plugin-documentation-urls.json"),
    ("/latestCore.txt", "/current/latestCore.txt"),
    ("/stable/update-center.json", "/dynamic-stable-2.222.1/update-center.json"),
    ("/stable/latestCore.txt", "/dynamic-stable-2.222.1/latestCore.txt"),
    (UC + "2.246", "/dynamic-2.240/update-center.json"),
    (UC + "2.246-SNAPSHOT", "/dynamic-2.240/update-center.json"),
    (UC + "2.240", "/dynamic-2.240/update-center.json"),
    (UC + "2.240-SNAPSHOT", "/dynamic-2.240/update-center.json"),
    (UC + "2.225", "/dynamic-2.223/update-center.json"),
    (UC + "2.225-SNAPSHOT", "/dynamic-2.223/update-center.json"),
    (UC + "2.223", "/dynamic-2.223/update-center.json"),
    (UC + "2.223-SNAPSHOT", "/dynamic-2.223/update-center.json"),
    (UC + "2.222", "/dynamic-2.222/update-center.json"),
    (UC + "2.222-SNAPSHOT", "/dynamic-2.222/update-center.json"),
    (UC + "2.222.1", "/dynamic-stable-2.222.1/update-center.json"),
    (UC + "2.222.1-SNAPSHOT", "/dynamic-stable-2.222.1/update-center.json"),
    (UC + "2.55", "/dynamic-2.172/update-center.json"),
    (UC + "2.6", "/dynamic-2.172/update-center.json"),
    (UC + "2.55-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "2.6-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "2.204.1", "/dynamic-stable-2.204.1/update-center.json"),
    (UC + "2.204.2", "/dynamic-stable-2.204.2/update-center.json"),
    (UC + "2.204.3", "/dynamic-stable-2.204.2/update-center.json"),
    (UC + "2.204.4", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.5", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.5-SNAPSHOT", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.6", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.6-SNAPSHOT", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.7", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.7-SNAPSHOT", "/dynamic-stable-2.204.6/update-center.json"),
    (ACTUAL + "2.222", "/dynamic-2.222/update-center.actual.json"),
    (ACTUAL + "2.222.1", "/dynamic-stable-2.222.1/update-center.actual.json"),
    (ACTUAL + "2.222-SNAPSHOT", "/dynamic-2.222/update-center.actual.json"),
    (ACTUAL + "2.222.1-SNAPSHOT", "/dynamic-stable-2.222.1/update-center.actual.json"),
    ("/plugin-documentation-urls.json?version=2.222.1", "/current/plugin-documentation-urls.json"),
    ("/latestCore.txt?version=2.222.1", "/current/latestCore.txt"),
    (UC + "1.650", "/dynamic-2.172/update-center.json"),
    (UC + "1.650-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "1.580", "/dynamic-2.172/update-center.json"),
    (UC + "1.580-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "1.580.1", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "1.580.1-SNAPSHOT", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.46.1", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.46.1-SNAPSHOT", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.200", "/dynamic-2.199/update-center.json"),
    (UC + "3.0", "/dynamic-2.240/update-center.json"),
    (UC + "3.0.1", "/dynamic-stable-2.222.1/update-center.json"),
]


@pytest.mark.parametrize("path,target", EXPECTED)
def test_redirect(path, target):
    response = fetch(HOST, path)
    assert response.status in (301, 302), response.status
    assert response.location == BASE + target


@pytest.mark.parametrize("path", ["/update-center.json?uctest", "/foo/update-center.json?uctest"])
def test_uctest_is_a_tiny_ok(path):
    response = fetch(HOST, path)
    assert response.status == 200
    assert response.headers["content-length"] == "3"
