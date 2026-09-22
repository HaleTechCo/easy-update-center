"""Small value types shared between modules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tiers:
    """Tier list from `update-center2 --dynamic-tier-list-file`, in upstream (ascending) order."""

    weekly: tuple[str, ...] = ()
    stable: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.weekly and not self.stable


@dataclass(frozen=True)
class Credentials:
    artifactory_username: str
    artifactory_password: str
    github_username: str
    github_password: str
    github_placeholder: bool
