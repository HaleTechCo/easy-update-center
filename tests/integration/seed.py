"""Seed data for the air-gapped integration test; runs on a network with internet access.

seed.py versions   print LTS_OLD, LTS_NEW, WEEKLY (for .versions.env)
seed.py repo ...   build a Maven-layout repository from jenkins-plugin-cli downloads the way operators
                   procure plugins: mostly .hpi files without POMs, one real POM, one saved error page
                   as a POM, a misplaced plugin, POM-less test plugins, and core WARs (two of them
                   released more than 400 days ago, for CORE_AGE_DAYS)
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from uc.doctor import parse_manifest

UPDATES = 'https://updates.jenkins.io'
RELEASES = 'https://repo.jenkins-ci.org/releases'
STUB_POM = b'<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId><artifactId>uc-test-stubpom</artifactId><version>1.0</version><packaging>hpi</packaging></project>'
# What an operator's download tool saved as a .pom (seen in a real repository, 2026-09-22).
HTML_404 = b'<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n'
# LTS releases from early 2024: always more than 400 days old by the time this test runs.
OLD_CORES = ('2.426.3', '2.440.3')


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'jenkins-uc-seed/1'})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def versions() -> None:
    lts_new = get(f'{UPDATES}/stable/latestCore.txt').decode().strip()
    weekly = get(f'{UPDATES}/current/latestCore.txt').decode().strip()
    lts_old = json.loads(get(f'{UPDATES}/tiers.json'))['stableCores'][0]
    print(f'LTS_OLD={lts_old}\nLTS_NEW={lts_new}\nWEEKLY={weekly}')


def put(root: Path, group: str, artifact: str, version: str, extension: str, *, data: bytes) -> None:
    path = root / group.replace('.', '/') / artifact / version / f'{artifact}-{version}.{extension}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def synthetic_hpi(artifact: str, jenkins_version: str, *, embed_pom: bool = False) -> bytes:
    """Build a plugin laid out like a pre-2015 build (index.jelly under WEB-INF/classes).

    With embed_pom, the archive also carries a POM the repo-adapter can serve.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', (f'Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.uctest\r\nArtifact-Id: {artifact}\r\nShort-Name: {artifact}\r\nExtension-Name: {artifact}\r\nLong-Name: UC test {artifact}\r\nPlugin-Version: 1.0\r\nJenkins-Version: {jenkins_version}\r\n\r\n'))
        if embed_pom:
            archive.writestr(f'META-INF/maven/io.jenkins.uctest/{artifact}/pom.xml', (f'<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId><artifactId>{artifact}</artifactId><version>1.0</version><packaging>hpi</packaging><name>UC test {artifact}</name></project>'))
        archive.writestr('WEB-INF/classes/index.jelly', f'<div>uc integration-test plugin {artifact}</div>')
    return buffer.getvalue()


def repo(args: argparse.Namespace) -> None:
    root = args.out / args.repository
    seeded, paths = [], {}
    downloads = sorted(args.downloads.glob('*.[hj]pi'))
    if len(downloads) < 3:
        msg = f'need at least 3 .hpi/.jpi files in {args.downloads}; did the seed-plugins step fail?'
        raise SystemExit(msg)
    others = [p.stem for p in downloads if p.stem != args.top_plugin]
    real_pom, html_pom = others[0], others[1]  # every other plugin gets no POM: the adapter derives it
    for plugin in downloads:
        data = plugin.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = parse_manifest(archive.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace'))
        group = manifest['Group-Id']
        artifact = manifest.get('Artifact-Id') or manifest.get('Short-Name') or manifest['Extension-Name']
        version = manifest['Plugin-Version'].split(' ')[0]
        put(root, group, artifact, version, 'hpi', data=data)
        if plugin.stem == real_pom:
            put(root, group, artifact, version, 'pom', data=get(f'{RELEASES}/{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}.pom'))
        elif plugin.stem == html_pom:
            put(root, group, artifact, version, 'pom', data=HTML_404)
        seeded.append(f'{artifact}:{version}')
        paths[artifact] = f'{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}.hpi'
    cores = args.cores.split(',')
    for core in cores:
        base = f'{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}'
        put(root, 'org.jenkins-ci.main', 'jenkins-war', core, 'war', data=get(base + '.war'))
        put(root, 'org.jenkins-ci.main', 'jenkins-war', core, 'pom', data=get(base + '.pom'))
    for core, plugin_id in zip(OLD_CORES, ('uc-test-old-a', 'uc-test-old-b'), strict=False):
        base = f'{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}'
        put(root, 'org.jenkins-ci.main', 'jenkins-war', core, 'war', data=get(base + '.war'))  # its POM is derived
        put(root, 'io.jenkins.uctest', plugin_id, '1.0', 'hpi', data=synthetic_hpi(plugin_id, core, embed_pom=True))
    put(root, 'io.jenkins.uctest', 'uc-test-nopom', '1.0', 'hpi', data=synthetic_hpi('uc-test-nopom', args.jenkins_version))
    put(root, 'io.jenkins.uctest', 'uc-test-stubpom', '1.0', 'hpi', data=synthetic_hpi('uc-test-stubpom', args.jenkins_version))
    put(root, 'io.jenkins.uctest', 'uc-test-stubpom', '1.0', 'pom', data=STUB_POM)
    # Its manifest says io.jenkins.uctest, but it is stored under another group, as happened in practice.
    put(root, 'org.jenkins-ci.plugins', 'uc-test-misplaced', '1.0', 'hpi', data=synthetic_hpi('uc-test-misplaced', args.jenkins_version, embed_pom=True))
    (args.out / 'seeded.json').write_text(
        json.dumps(
            {
                'plugins': seeded,
                'paths': paths,
                'real_pom': real_pom,
                'html_pom': html_pom,
                'cores': cores + list(OLD_CORES),
                'old_cores': list(OLD_CORES),
            },
            indent=2,
        )
    )
    print(f'seeded {len(seeded)} plugins and cores {", ".join(cores + list(OLD_CORES))} into {root}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('versions')
    build = sub.add_parser('repo')
    build.add_argument('--downloads', type=Path, required=True)
    build.add_argument('--out', type=Path, required=True)
    build.add_argument('--repository', required=True)
    build.add_argument('--cores', required=True)
    build.add_argument('--jenkins-version', required=True)
    build.add_argument('--top-plugin', required=True)
    args = parser.parse_args()
    versions() if args.command == 'versions' else repo(args)


if __name__ == '__main__':
    main()
