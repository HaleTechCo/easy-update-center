"""Procurement: stage what uc doctor finds missing for review, then push exactly that."""

from __future__ import annotations

from uc.errors import UcError


class ProcureError(UcError):
    """Staging or pushing cannot go on: bad input, an unreachable server, an unwritable folder."""
