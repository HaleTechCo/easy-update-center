import pytest
from helpers import fetch

UC = "/update-center.json"


@pytest.mark.parametrize(
    "host,path,target",
    [
        ("web-weekly", UC + "?version=2.540", "/dynamic-2.540/update-center.json"),
        ("web-weekly", UC + "?version=2.500", "/dynamic-2.540/update-center.json"),
        ("web-weekly", UC + "?version=2.528.1", "/current/update-center.json"),
        ("web-weekly", "/stable/update-center.json", "/current/update-center.json"),
        ("web-stable", UC + "?version=2.528.2", "/dynamic-stable-2.528.1/update-center.json"),
        ("web-stable", UC + "?version=2.528.3", "/dynamic-stable-2.528.3/update-center.json"),
        ("web-stable", UC + "?version=2.540", "/current/update-center.json"),
        ("web-stable", "/stable/update-center.json", "/dynamic-stable-2.528.3/update-center.json"),
        ("web-zero", UC + "?version=2.528.1", "/current/update-center.json"),
        ("web-zero", UC + "?version=2.540", "/current/update-center.json"),
        ("web-zero", "/stable/update-center.json", "/current/update-center.json"),
    ],
)
def test_partial_and_zero_tier_routing(host, path, target):
    response = fetch(host, path)
    assert response.status in (301, 302)
    assert response.location == f"http://{host}:8080{target}"


def test_zero_tier_uctest():
    assert fetch("web-zero", "/update-center.json?uctest").status == 200
