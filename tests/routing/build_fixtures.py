"""Build web-tier fixture trees into /fixtures/<scenario>.

Run by the fixtures service.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, '/tests/support')

from sitefixture import write_generator_output
from uc.downloadmap import write_download_map
from uc.htaccess import render_htaccess
from uc.layout import finalize_layout
from uc.model import Tiers

APP = Path('/opt/uc')
R1, R2 = '20260101T000000Z', '20260102T000000Z'
# The tier list used by upstream's site/test/test.sh at 3.18.4.
UPSTREAM_TEST_TIERS = Tiers(
    weekly=(
        '2.172',
        '2.173',
        '2.176',
        '2.177',
        '2.181',
        '2.184',
        '2.185',
        '2.191',
        '2.195',
        '2.199',
        '2.204',
        '2.205',
        '2.212',
        '2.217',
        '2.222',
        '2.223',
        '2.240',
    ),
    stable=(
        '2.164.2',
        '2.164.3',
        '2.176.1',
        '2.176.2',
        '2.176.3',
        '2.176.4',
        '2.190.1',
        '2.190.3',
        '2.204.1',
        '2.204.2',
        '2.204.4',
        '2.204.6',
        '2.222.1',
    ),
)
SMALL = Tiers(('2.540',), ('2.528.1',))
MAPS = {
    R1: {
        'plugins/git/5.2.0': 'org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi',
        'war/2.528.1': (
            'org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war'
        ),
    },
}
MAPS[R2] = {
    **MAPS[R1],
    'plugins/git/5.3.0': 'org/jenkins-ci/plugins/git/5.3.0/git-5.3.0.hpi',
}


def release(data: Path, release_id: str, public_url: str, tiers: Tiers) -> None:
    staging = data / 'releases' / release_id
    write_generator_output(staging, public_url, tiers)
    (staging / '.htaccess').write_text(
        render_htaccess(
            tiers,
            APP / 'site' / 'generate-htaccess.sh',
            APP / 'templates' / 'htaccess-static',
        )
    )
    finalize_layout(staging, tiers, APP / 'templates')
    (staging / 'tiers.json').write_text(
        json.dumps(
            {'weeklyCores': list(tiers.weekly), 'stableCores': list(tiers.stable)}
        )
    )
    write_download_map(MAPS[release_id], staging / '.htdownload-map')
    (staging / 'current' / 'marker.txt').write_text(release_id)


def scenario(
    root: Path, name: str, public_url: str, tiers: Tiers, releases=(R1,)
) -> None:
    data = root / name
    shutil.rmtree(data, ignore_errors=True)
    for index, release_id in enumerate(releases):
        release(data, release_id, public_url, tiers)
        stamp = (
            1_767_225_600 + index * 60
        )  # distinct map mtimes, so RewriteMap reloads after a swap
        os.utime(data / 'releases' / release_id / '.htdownload-map', (stamp, stamp))
    (data / 'live').symlink_to(f'releases/{releases[0]}')
    (data / 'status.json').write_text(json.dumps({'live_release': releases[0]}) + '\n')


def tls_backend(tls: Path, hostname: str) -> None:
    """Create a root CA, an intermediate and a leaf certificate for hostname.

    Public CAs sign through an intermediate, so a real repository sends leaf +
    intermediate and is trusted through the root alone.
    """
    shutil.rmtree(tls, ignore_errors=True)
    tls.mkdir(parents=True)

    def openssl(*args: str) -> None:
        subprocess.run(['openssl', *args], cwd=tls, check=True, capture_output=True)

    def extensions(name: str, text: str) -> str:
        (tls / name).write_text(text)
        return name

    openssl(
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        'root.key',
        '-out',
        'root.crt',
        '-days',
        '30',
        '-subj',
        '/CN=Routing Root CA',
        '-addext',
        'basicConstraints=critical,CA:TRUE',
        '-addext',
        'keyUsage=critical,keyCertSign,cRLSign',
    )
    openssl(
        'req',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        'intermediate.key',
        '-out',
        'intermediate.csr',
        '-subj',
        '/CN=Routing Intermediate CA',
    )
    openssl(
        'x509',
        '-req',
        '-in',
        'intermediate.csr',
        '-CA',
        'root.crt',
        '-CAkey',
        'root.key',
        '-CAcreateserial',
        '-days',
        '30',
        '-out',
        'intermediate.crt',
        '-extfile',
        extensions(
            'intermediate.ext',
            'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n',
        ),
    )
    openssl(
        'req',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        'leaf.key',
        '-out',
        'leaf.csr',
        '-subj',
        f'/CN={hostname}',
    )
    openssl(
        'x509',
        '-req',
        '-in',
        'leaf.csr',
        '-CA',
        'intermediate.crt',
        '-CAkey',
        'intermediate.key',
        '-CAcreateserial',
        '-days',
        '30',
        '-out',
        'leaf.crt',
        '-extfile',
        extensions(
            'leaf.ext',
            f'subjectAltName=DNS:{hostname}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n',
        ),
    )
    (tls / 'chain.pem').write_text(
        (tls / 'leaf.crt').read_text() + (tls / 'intermediate.crt').read_text()
    )
    # web-tls reads the key as uid 10001; a test fixture, never a deployment's key
    (tls / 'leaf.key').chmod(0o644)


def main(root: Path) -> None:
    tls_backend(root / 'tls', 'backend-tls')
    scenario(root, 'full', 'http://web-full:8080', UPSTREAM_TEST_TIERS)
    scenario(
        root, 'artifactory', 'http://web-artifactory:8080', SMALL, releases=(R1, R2)
    )
    scenario(root, 'weekly', 'http://web-weekly:8080', Tiers(('2.540', '2.541'), ()))
    scenario(
        root, 'stable', 'http://web-stable:8080', Tiers((), ('2.528.1', '2.528.3'))
    )
    scenario(root, 'zero', 'http://web-zero:8080', Tiers())
    # A file at a path the download map does not list: the 404 rule must win over it.
    for release_id in (R1, R2):
        stray = (
            root / 'artifactory/releases' / release_id / 'download/plugins/git/9.9.9'
        )
        stray.mkdir(parents=True)
        (stray / 'git.hpi').write_text('not a plugin')
    for path in root.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    print(f'fixtures ready in {root}')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
