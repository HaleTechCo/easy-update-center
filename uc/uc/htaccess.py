"""The root .htaccess: upstream generate-htaccess.sh output minus its download redirects (spec §6 step 5)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .errors import UcError

if TYPE_CHECKING:
    from pathlib import Path

    from .model import Tiers

# The last five rules of upstream's output (site/generate-htaccess.sh:132-137). Downloads are
# handled at server level instead (web/conf/uc-site.conf).
UPSTREAM_DOWNLOAD_RULES = (
    'RewriteRule ^download/war/latest/jenkins[.]war$ https://updates.jenkins.io/latest/jenkins.war [NC,L,R=302]',
    'RewriteRule ^download/plugins/(.*)/latest/(.+)[.]hpi$ https://updates.jenkins.io/latest/$2.hpi [NC,L,R=302]',
    'RewriteRule ^download/war/([0-9]+[.][0-9]+[.][0-9]+/jenkins)[.]war$ https://get.jenkins.io/war-stable/$1.war [NC,L,R=302]',
    'RewriteRule ^download/war/(.+)[.]war$ https://get.jenkins.io/war/$1.war [NC,L,R=302]',
    'RewriteRule ^download/plugins/(.+)[.]hpi$ https://get.jenkins.io/plugins/$1.hpi [NC,L,R=302]',
)
# Targets upstream emits when one tier list is empty; they point nowhere, so send them to current/.
NO_STABLE_FIXES = (
    ('/dynamic-stable-%{REQUEST_URI}?', '/current%{REQUEST_URI}?'),
    ('"/dynamic-stable-/$1"', '"/current/$1"'),
)
NO_WEEKLY_FIXES = (('/dynamic-%{REQUEST_URI}?', '/current%{REQUEST_URI}?'),)


class HtaccessError(UcError):
    """generate-htaccess.sh failed, or its output no longer has the shape uc edits."""


def _changed(detail: str) -> HtaccessError:
    return HtaccessError(f'{detail}; upstream generate-htaccess.sh changed, review it before upgrading (docs/upgrading.md)')


def strip_download_rules(text: str) -> str:
    """Remove upstream's download redirects, each of which must appear exactly once."""
    lines = text.split('\n')
    for rule in UPSTREAM_DOWNLOAD_RULES:
        count = lines.count(rule)
        if count != 1:
            msg = f'expected exactly one download rule {rule!r}, found {count}'
            raise _changed(msg)
        lines.remove(rule)
    return '\n'.join(lines)


def fix_partial_tiers(text: str, *, has_weekly: bool, has_stable: bool) -> str:
    """Point the targets upstream leaves dangling when the weekly or stable tier list is empty at current/."""
    fixes = (() if has_stable else NO_STABLE_FIXES) + (() if has_weekly else NO_WEEKLY_FIXES)
    for old, new in fixes:
        count = text.count(old)
        if count != 1:
            msg = f'expected exactly one {old!r} for a partial tier list, found {count}'
            raise _changed(msg)
        text = text.replace(old, new)
    return text


def render_htaccess(tiers: Tiers, script: Path, template: Path) -> str:
    """Return the root .htaccess: the static template without tiers, else generate-htaccess.sh's output, fixed up."""
    if tiers.empty:
        return template.read_text(encoding='utf-8')
    proc = subprocess.run(['bash', str(script), *tiers.weekly, *tiers.stable], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        msg = f'generate-htaccess.sh failed ({proc.returncode}): {proc.stderr.strip()}'
        raise HtaccessError(msg)
    return strip_download_rules(fix_partial_tiers(proc.stdout, has_weekly=bool(tiers.weekly), has_stable=bool(tiers.stable)))


def absolute_targets(text: str) -> list[str]:
    """Non-comment lines that mention an absolute http(s) URL."""
    return [line for line in text.splitlines() if not line.lstrip().startswith('#') and ('http://' in line or 'https://' in line)]
