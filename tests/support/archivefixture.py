"""Plugin and core archives shaped like Maven, Gradle and pre-2015 builds (docs/architecture/repo-adapter.md#filling-gaps)."""

from __future__ import annotations

import io
import struct
import zipfile

DATE = (2024, 5, 1, 12, 30, 0)


def pom_xml(group: str, artifact: str, version: str, name: str = 'Test plugin') -> bytes:
    return (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>{version}</version><packaging>hpi</packaging><name>{name}</name></project>').encode()


def manifest(group: str | None, artifact: str | None, version: str, extra: str = '') -> str:
    lines = ['Manifest-Version: 1.0']
    if group:
        lines.append(f'Group-Id: {group}')
    if artifact:
        lines.append(f'Artifact-Id: {artifact}')
    lines += [f'Short-Name: {artifact or "x"}', f'Plugin-Version: {version}', 'Jenkins-Version: 2.479.1']
    return '\r\n'.join(lines) + '\r\n' + extra + '\r\n'


def extended_timestamp(seconds: int) -> bytes:
    """Return a 0x5455 (extended timestamp) extra field carrying a UTC mtime."""
    return struct.pack('<HHB', 0x5455, 5, 1) + struct.pack('<i', seconds)


def _jar(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as jar:
        for name, data in entries.items():
            jar.writestr(name, data)
    return buffer.getvalue()


def make_hpi(
    group: str, artifact: str, version: str, *, layout: str = 'maven', embed_pom: bool = True, pom: bytes | None = None, manifest_group: str | None = '', manifest_artifact: str | None = '', description: bytes | None = b'<div>An excellent plugin.</div>', date: tuple = DATE, unix_time: int | None = None
) -> bytes:
    """Build a plugin archive (.hpi) in one of three layouts.

    layout: maven (WEB-INF/lib/<a>.jar), gradle (WEB-INF/lib/<a>-<v>.jar, no META-INF/maven) or
    classes (index.jelly under WEB-INF/classes). manifest_group/artifact default to the real ones.
    """
    group_in_manifest = group if manifest_group == '' else manifest_group
    artifact_in_manifest = artifact if manifest_artifact == '' else manifest_artifact
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        info = zipfile.ZipInfo('META-INF/MANIFEST.MF', date_time=date)
        if unix_time is not None:
            info.extra = extended_timestamp(unix_time)
        archive.writestr(info, manifest(group_in_manifest, artifact_in_manifest, version))
        if embed_pom and layout != 'gradle':
            archive.writestr(f'META-INF/maven/{group}/{artifact}/pom.xml', pom or pom_xml(group, artifact, version))
        inner = {'index.jelly': description} if description is not None else {}
        if layout == 'maven':
            archive.writestr(f'WEB-INF/lib/{artifact}.jar', _jar({'x/Plugin.class': b'\xca\xfe', **inner}))
        elif layout == 'gradle':
            archive.writestr(f'WEB-INF/lib/{artifact}-{version}.jar', _jar({'x/Plugin.class': b'\xca\xfe', **inner}))
        elif layout == 'classes' and description is not None:
            archive.writestr('WEB-INF/classes/index.jelly', description)
    return buffer.getvalue()


def make_war(version: str, *, embed_pom: bool = True, date: tuple = DATE) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('META-INF/MANIFEST.MF', date_time=date), f'Manifest-Version: 1.0\r\nJenkins-Version: {version}\r\n\r\n')
        if embed_pom:
            archive.writestr('META-INF/maven/org.jenkins-ci.main/jenkins-war/pom.xml', pom_xml('org.jenkins-ci.main', 'jenkins-war', version, 'Jenkins war'))
        archive.writestr('WEB-INF/lib/jenkins-core.jar', _jar({'hudson/Main.class': b'\xca\xfe'}))
    return buffer.getvalue()


def make_jar(description: bytes | None) -> bytes:
    return _jar({'x/Plugin.class': b'\xca\xfe', **({'index.jelly': description} if description else {})})
