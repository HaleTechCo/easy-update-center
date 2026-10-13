"""Reading untrusted POMs safely, and deciding whether one belongs to a path.

See docs/architecture/repo-adapter.md#filling-gaps.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

Coordinates = tuple[str, str, str]  # groupId, artifactId, version
# Encodings expat reads natively that are ASCII-compatible, so the byte check below
# still sees every declaration. Old POMs sometimes declare ISO-8859-1 for a developer's
# name.
_SINGLE_BYTE = re.compile(
    rb"""^\s*<\?xml[^>]*\sencoding=["'](iso-8859-1|latin-?1|us-ascii)["']""",
    re.IGNORECASE,
)


def declares_entities(data: bytes) -> bool:
    """Return whether the bytes contain a DOCTYPE or ENTITY declaration anywhere."""
    return b'<!DOCTYPE' in data or b'<!ENTITY' in data


def safe_root(data: bytes) -> ET.Element | None:
    """Return the XML root element.

    None for anything that is not plain, well-formed UTF-8 XML.

    Real Maven POMs never declare a DOCTYPE or entities; refusing them avoids
    entity-expansion tricks without a third-party parser (ElementTree never resolves
    external entities). The byte check only sees declarations in an ASCII-compatible
    encoding, so NUL bytes (UTF-16, UTF-32) are refused, and so is anything that is
    neither UTF-8 nor declared ISO-8859-1 or US-ASCII.
    """
    if b'\x00' in data or declares_entities(data):
        return None
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        if not _SINGLE_BYTE.match(data):
            return None
    try:
        # stdlib-only by design; DOCTYPE and ENTITY input is refused above
        return ET.fromstring(data)  # noqa: S314
    except ET.ParseError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or '').strip() or None
    return None


def pom_coordinates(data: bytes) -> Coordinates | None:
    """(groupId, artifactId, version) a POM declares.

    groupId and version may come from <parent>.
    """
    root = safe_root(data)
    if root is None or _local(root.tag) != 'project':
        return None
    parent = next((child for child in root if _local(child.tag) == 'parent'), None)
    group = _child_text(root, 'groupId') or (
        _child_text(parent, 'groupId') if parent is not None else None
    )
    version = _child_text(root, 'version') or (
        _child_text(parent, 'version') if parent is not None else None
    )
    artifact = _child_text(root, 'artifactId')
    if not group or not artifact or not version:
        return None
    return group, artifact, version


def dependency_group(data: bytes | None, artifact: str) -> str | None:
    """Return the groupId a POM's <dependencies> give artifact.

    ${project.groupId} is the POM's own group, and any other property or no entry gives
    None.
    """
    root = safe_root(data) if data else None
    if root is None or _local(root.tag) != 'project':
        return None
    dependencies = next(
        (child for child in root if _local(child.tag) == 'dependencies'), None
    )
    for dependency in dependencies if dependencies is not None else ():
        if (
            _local(dependency.tag) != 'dependency'
            or _child_text(dependency, 'artifactId') != artifact
        ):
            continue
        group = _child_text(dependency, 'groupId') or ''
        if group in {'${project.groupId}', '${pom.groupId}'}:
            own = pom_coordinates(data)
            return own[0] if own else None
        return group if group and '${' not in group else None
    return None


def unusable_reason(data: bytes, expected: Coordinates) -> str | None:
    """None when the POM is usable for the expected coordinates.

    Otherwise a short reason for the report.
    """
    if declares_entities(data):
        return 'declares a DOCTYPE or entities'
    root = safe_root(data)
    if root is None:
        return 'not XML'
    if _local(root.tag) != 'project':
        return f'not a POM (root element <{_local(root.tag)}>)'
    declared = pom_coordinates(data)
    if declared is None:
        return 'does not declare groupId, artifactId and version'
    if declared != expected:
        return f'declares {":".join(declared)}, not {":".join(expected)}'
    return None
