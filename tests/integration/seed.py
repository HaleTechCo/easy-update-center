"""Seed data for the air-gapped integration test; runs on a network with internet access.

  seed.py versions   print LTS_OLD, LTS_NEW, WEEKLY (for .versions.env)
  seed.py repo ...   build a Maven-layout repository from jenkins-plugin-cli downloads, with the
                     real POMs, three core WARs and two deliberately broken test plugins
"""
from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from uc.doctor import parse_manifest

UPDATES = "https://updates.jenkins.io"
RELEASES = "https://repo.jenkins-ci.org/releases"
STUB_POM = (b"<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId>"
            b"<artifactId>uc-test-stubpom</artifactId><version>1.0</version><packaging>hpi</packaging></project>")


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "jenkins-uc-seed/1"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def versions() -> None:
    lts_new = get(f"{UPDATES}/stable/latestCore.txt").decode().strip()
    weekly = get(f"{UPDATES}/current/latestCore.txt").decode().strip()
    lts_old = json.loads(get(f"{UPDATES}/tiers.json"))["stableCores"][0]
    print(f"LTS_OLD={lts_old}\nLTS_NEW={lts_new}\nWEEKLY={weekly}")


def put(root: Path, group: str, artifact: str, version: str, extension: str, data: bytes) -> None:
    path = root / group.replace(".", "/") / artifact / version / f"{artifact}-{version}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def synthetic_hpi(artifact: str, jenkins_version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", (
            "Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.uctest\r\n"
            f"Short-Name: {artifact}\r\nExtension-Name: {artifact}\r\nLong-Name: UC test {artifact}\r\n"
            f"Plugin-Version: 1.0\r\nJenkins-Version: {jenkins_version}\r\n\r\n"
        ))
        archive.writestr("index.jelly", f"<div>uc integration-test plugin {artifact}</div>")
    return buffer.getvalue()


def repo(args: argparse.Namespace) -> None:
    root = args.out / args.repository
    seeded = []
    for plugin in sorted(args.downloads.glob("*.[hj]pi")):
        data = plugin.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = parse_manifest(archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace"))
        group = manifest["Group-Id"]
        artifact = manifest.get("Short-Name") or manifest["Extension-Name"]
        version = manifest["Plugin-Version"].split(" ")[0]
        put(root, group, artifact, version, "hpi", data)
        put(root, group, artifact, version, "pom",
            get(f"{RELEASES}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"))
        seeded.append(f"{artifact}:{version}")
    if not seeded:
        raise SystemExit(f"no .hpi/.jpi files in {args.downloads}; did the seed-plugins step fail?")
    cores = args.cores.split(",")
    for core in cores:
        base = f"{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}"
        put(root, "org.jenkins-ci.main", "jenkins-war", core, "war", get(base + ".war"))
        put(root, "org.jenkins-ci.main", "jenkins-war", core, "pom", get(base + ".pom"))
    put(root, "io.jenkins.uctest", "uc-test-nopom", "1.0", "hpi", synthetic_hpi("uc-test-nopom", args.jenkins_version))
    put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "hpi", synthetic_hpi("uc-test-stubpom", args.jenkins_version))
    put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "pom", STUB_POM)
    (args.out / "seeded.json").write_text(json.dumps({"plugins": seeded, "cores": cores}, indent=2))
    print(f"seeded {len(seeded)} plugins and cores {', '.join(cores)} into {root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("versions")
    build = sub.add_parser("repo")
    build.add_argument("--downloads", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--repository", required=True)
    build.add_argument("--cores", required=True)
    build.add_argument("--jenkins-version", required=True)
    args = parser.parse_args()
    versions() if args.command == "versions" else repo(args)


if __name__ == "__main__":
    main()
