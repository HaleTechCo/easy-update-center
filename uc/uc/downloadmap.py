"""The .htdownload-map RewriteMap: /download/... keys to Maven paths in Artifactory.

See docs/architecture/generation-pipeline.md#step-8-layout-and-download-map.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .aql import Gav, Listing

_GAV_COLONS = 2  # groupId:artifactId:version


def _choose(options: list[Gav], preferred_group: str | None) -> Gav:
    # The groupId the generator offered first, then .hpi over .jpi, then lexical order.
    return min(
        options,
        key=lambda g: (g.group_id != preferred_group, g.extension != 'hpi', g.group_id),
    )


def build_download_map(
    listing: Listing, preferred_groups: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Map each plugins/<id>/<version> and war/<version> key to one Maven path.

    Return it with the warnings.

    Where one key has several files, the pick is the generator's groupId, then .hpi over
    .jpi, then lexical order; a key found under several groupIds also gets a warning.
    """
    candidates: dict[str, list[Gav]] = {}
    for gav in listing.plugins:
        candidates.setdefault(f'plugins/{gav.artifact_id}/{gav.version}', []).append(
            gav
        )
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for key, options in sorted(candidates.items()):
        chosen = _choose(options, preferred_groups.get(options[0].artifact_id))
        groups = sorted({g.group_id for g in options})
        if len(groups) > 1:
            warnings.append(
                f'{key} exists under several groupIds ({", ".join(groups)}); '
                f'using {chosen.group_id}'
            )
        mapping[key] = chosen.maven_path
    for gav in listing.wars:
        mapping[f'war/{gav.version}'] = gav.maven_path
    return mapping, warnings


def preferred_groups(update_center: Mapping) -> dict[str, str]:
    """Return the groupId of each plugin's `gav`, by plugin id.

    In a generated update-center.json document.
    """
    result: dict[str, str] = {}
    plugins = update_center.get('plugins')
    for plugin_id, entry in (plugins if isinstance(plugins, dict) else {}).items():
        gav = entry.get('gav', '') if isinstance(entry, dict) else ''
        if isinstance(gav, str) and gav.count(':') == _GAV_COLONS:
            result[plugin_id] = gav.split(':', 1)[0]
    return result


def write_download_map(mapping: Mapping[str, str], path: Path) -> None:
    """Write the mapping as sorted `key value` lines, the RewriteMap txt format."""
    path.write_text(
        ''.join(f'{key} {value}\n' for key, value in sorted(mapping.items())),
        encoding='utf-8',
    )


def read_download_map(path: Path) -> dict[str, str]:
    """Read a map written by write_download_map.

    Skips lines without both a key and a value.
    """
    result: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition(' ')
        if key and value:
            result[key] = value
    return result
