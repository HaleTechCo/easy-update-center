"""The documentation link checker: GitLab heading anchors, and which tracked files it scans."""

import importlib.util
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location('check_doc_links', TOOLS / 'check_doc_links.py')
links = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(links)


def test_heading_slug_keeps_hyphen_runs_like_gitlab_17():
    assert links.heading_slug('uc.env / web.env') == 'ucenv--webenv'
    assert links.heading_slug('`tools/` - pre-commit shims') == 'tools---pre-commit-shims'
    assert links.heading_slug('Step 5: .htaccess') == 'step-5-htaccess'
    assert links.heading_slug('Outbound: air-gapped') == 'outbound-air-gapped'


def test_heading_slug_keeps_underscores_and_drops_code_marks():
    assert links.heading_slug('A hostname for `PUBLIC_URL`') == 'a-hostname-for-public_url'


def test_repeated_headings_get_numbered_anchors(tmp_path):
    page = tmp_path / 'page.md'
    page.write_text('# Title\n## Logs\n## Logs\n', encoding='utf-8')
    assert links.anchors_in(page) == {'title', 'logs', 'logs-1'}


def _git_tree(root, names):
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x\n', encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True)


def test_tracked_paths_cover_every_file_type_that_carries_references(tmp_path):
    scanned = ['README.md', 'uc/x.py', 'compose.yaml', 'Makefile', 'uc/Dockerfile', 'web/conf/site.conf', 'web/entrypoint.sh', '.env.online.example']
    skipped = ['uc/patches/0001.patch', '.gitattributes', 'uc/bin/uc', 'notes.txt']
    _git_tree(tmp_path, scanned + skipped)
    found = {path.relative_to(tmp_path).as_posix() for path in links.tracked_paths(tmp_path)}
    assert found == set(scanned)


def test_bare_reference_in_a_makefile_comment_is_checked(tmp_path):
    (tmp_path / 'guide').mkdir()
    (tmp_path / 'guide' / 'page.md').write_text('# Page\n## Real\n', encoding='utf-8')
    makefile = tmp_path / 'Makefile'
    makefile.write_text('# see guide/page.md#real and guide/page.md#gone\n', encoding='utf-8')
    assert links.broken_links(makefile, tmp_path) == [f'{makefile}: no such anchor: guide/page.md#gone']
