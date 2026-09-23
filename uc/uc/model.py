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
    """What uc and the jar present to Settings.source_url: the repository's account, or the adapter token."""

    repo_username: str
    repo_password: str
    github_username: str
    github_password: str
    github_placeholder: bool
