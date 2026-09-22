"""The .htdownload-map RewriteMap: /download/... keys to Maven paths in Artifactory (spec §6 step 8, §8.2)."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .aql import Gav, Listing


def _choose(options: list[Gav], preferred_group: str | None) -> Gav:
    # The groupId the generator offered first, then .hpi over .jpi, then lexical order.
    return sorted(options, key=lambda g: (g.group_id != preferred_group, g.extension != "hpi", g.group_id))[0]


def build_download_map(
    listing: Listing, preferred_groups: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    candidates: dict[str, list[Gav]] = {}
    for gav in listing.plugins:
        candidates.setdefault(f"plugins/{gav.artifact_id}/{gav.version}", []).append(gav)
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for key, options in sorted(candidates.items()):
        chosen = _choose(options, preferred_groups.get(options[0].artifact_id))
        groups = sorted({g.group_id for g in options})
        if len(groups) > 1:
            warnings.append(f"{key} exists under several groupIds ({', '.join(groups)}); using {chosen.group_id}")
        mapping[key] = chosen.maven_path
    for gav in listing.wars:
        mapping[f"war/{gav.version}"] = gav.maven_path
    return mapping, warnings


def preferred_groups(update_center: Mapping) -> dict[str, str]:
    result: dict[str, str] = {}
    for plugin_id, entry in (update_center.get("plugins") or {}).items():
        gav = entry.get("gav", "") if isinstance(entry, dict) else ""
        if isinstance(gav, str) and gav.count(":") == 2:
            result[plugin_id] = gav.split(":", 1)[0]
    return result


def write_download_map(mapping: Mapping[str, str], path: Path) -> None:
    path.write_text("".join(f"{key} {value}\n" for key, value in sorted(mapping.items())), encoding="utf-8")


def read_download_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            result[key] = value
    return result
