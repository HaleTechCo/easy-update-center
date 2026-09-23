"""A live symlink swap, and the RewriteMap behind it, take effect without restarting Apache (spec S5)."""

from pathlib import Path

from helpers import fetch

DATA = Path('/fixtures/artifactory')
R1, R2 = '20260101T000000Z', '20260102T000000Z'
NEW = '/download/plugins/git/5.3.0/git.hpi'


def swap(release_id: str) -> None:
    tmp = DATA / 'live.tmp'
    if tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(f'releases/{release_id}')
    tmp.replace(DATA / 'live')


def test_live_swap_needs_no_restart():
    assert fetch('web-artifactory', '/current/marker.txt').body == R1.encode()
    assert fetch('web-artifactory', NEW).status == 404
    swap(R2)
    try:
        assert fetch('web-artifactory', '/current/marker.txt').body == R2.encode()
        response = fetch('web-artifactory', NEW)
        assert response.status == 302
        assert response.location.endswith('/org/jenkins-ci/plugins/git/5.3.0/git-5.3.0.hpi')
    finally:
        swap(R1)
    assert fetch('web-artifactory', NEW).status == 404
