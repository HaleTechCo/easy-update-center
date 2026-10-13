"""Procurement: stage what uc doctor finds missing for review, then push exactly that.

See docs/how-to/procure-missing-files.md.
"""

from __future__ import annotations

from uc.errors import UcError


class ProcureError(UcError):
    """Staging or pushing cannot go on.

    Bad input, an unreachable server, an unwritable folder.
    """
