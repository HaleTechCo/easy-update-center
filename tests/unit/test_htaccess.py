from pathlib import Path

import pytest

from uc.htaccess import (
    UPSTREAM_DOWNLOAD_RULES,
    HtaccessError,
    absolute_targets,
    fix_partial_tiers,
    render_htaccess,
    strip_download_rules,
)
from uc.model import Tiers

SCRIPT = Path('/opt/uc/site/generate-htaccess.sh')
TEMPLATE = Path('/opt/uc/templates/htaccess-static')


def render(weekly=(), stable=()):
    return render_htaccess(Tiers(tuple(weekly), tuple(stable)), SCRIPT, TEMPLATE)


def test_full_tiers_keep_routing_and_drop_download_redirects():
    text = render(['2.540'], ['2.528.1'])
    assert r'/dynamic-2\.540%{REQUEST_URI}?' in text
    assert r'/dynamic-stable-2\.528\.1%{REQUEST_URI}?' in text
    assert '"/dynamic-stable-2.528.1/$1"' in text
    assert 'RewriteCond %{QUERY_STRING} ^.*uctest$' in text
    for rule in UPSTREAM_DOWNLOAD_RULES:
        assert rule not in text
    assert absolute_targets(text) == []  # upstream's comments still mention get.jenkins.io; rules do not


def test_weekly_only_routes_lts_to_current():
    text = render(['2.540', '2.541'])
    assert '/dynamic-stable-%{REQUEST_URI}?' not in text
    assert '"/dynamic-stable-/$1"' not in text
    assert '"/current/$1"' in text
    assert absolute_targets(text) == []


def test_stable_only_routes_weekly_to_current():
    text = render(stable=['2.528.1', '2.528.3'])
    assert '/dynamic-%{REQUEST_URI}?' not in text
    assert absolute_targets(text) == []


def test_zero_tiers_use_the_static_template():
    text = render()
    assert text == TEMPLATE.read_text()
    assert 'RewriteCond %{QUERY_STRING} ^.*uctest$' in text
    assert '"/current/$1"' in text
    assert absolute_targets(text) == []


def test_upstream_changes_are_detected():
    with pytest.raises(HtaccessError, match='upstream generate-htaccess.sh changed'):
        strip_download_rules('RewriteEngine on\n' + '\n'.join(UPSTREAM_DOWNLOAD_RULES[:4]))
    with pytest.raises(HtaccessError, match='found 2'):
        strip_download_rules('\n'.join(UPSTREAM_DOWNLOAD_RULES + UPSTREAM_DOWNLOAD_RULES[:1]))
    with pytest.raises(HtaccessError, match='partial tier list'):
        fix_partial_tiers('RewriteEngine on\n', has_weekly=True, has_stable=False)


def test_script_failure_is_reported(tmp_path):
    script = tmp_path / 'fail.sh'
    script.write_text('echo boom >&2; exit 3\n')
    with pytest.raises(HtaccessError, match='failed \\(3\\): boom'):
        render_htaccess(Tiers(('2.540',)), script, TEMPLATE)


def test_absolute_targets_ignores_comments():
    text = '# See https://github.com/x\nRewriteRule ^a https://evil/ [R]\n'
    assert absolute_targets(text) == ['RewriteRule ^a https://evil/ [R]']
