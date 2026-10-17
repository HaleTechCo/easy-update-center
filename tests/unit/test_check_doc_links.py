"""The documentation link checker.

GitHub heading anchors, and which tracked files it scans.
"""

import importlib.util
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location(
    'check_doc_links', TOOLS / 'check_doc_links.py'
)
links = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(links)


def test_heading_slug_keeps_hyphen_runs_like_github():
    assert links.heading_slug('Online / air-gapped') == 'online--air-gapped'
    assert (
        links.heading_slug('`tools/` - pre-commit shims') == 'tools---pre-commit-shims'
    )
    assert links.heading_slug('Step 5: .htaccess') == 'step-5-htaccess'
    assert links.heading_slug('Outbound: air-gapped') == 'outbound-air-gapped'


def test_heading_slug_keeps_accented_letters():
    assert links.heading_slug('Café ünits') == 'café-ünits'
    assert links.heading_slug('Ⅻ → © ½') == 'ⅻ---½'


def test_heading_slug_keeps_underscores_and_drops_code_marks():
    assert (
        links.heading_slug('A hostname for `PUBLIC_URL`') == 'a-hostname-for-public_url'
    )


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
    scanned = [
        'README.md',
        'uc/x.py',
        'compose.yaml',
        'Makefile',
        'uc/Dockerfile',
        'web/conf/site.conf',
        'web/entrypoint.sh',
        '.env.online.example',
    ]
    skipped = ['uc/patches/0001.patch', '.gitattributes', 'uc/bin/uc', 'notes.txt']
    _git_tree(tmp_path, scanned + skipped)
    found = {
        path.relative_to(tmp_path).as_posix() for path in links.tracked_paths(tmp_path)
    }
    assert found == set(scanned)


def test_bare_reference_in_a_makefile_comment_is_checked(tmp_path):
    (tmp_path / 'guide').mkdir()
    (tmp_path / 'guide' / 'page.md').write_text('# Page\n## Real\n', encoding='utf-8')
    makefile = tmp_path / 'Makefile'
    makefile.write_text(
        '# see guide/page.md#real and guide/page.md#gone\n', encoding='utf-8'
    )
    assert links.broken_links(makefile, tmp_path) == [
        f'{makefile}: no such anchor: guide/page.md#gone'
    ]


def _docs(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'guide.md').write_text('# Guide\n## Real\n', encoding='utf-8')
    return tmp_path / 'docs' / 'page.md'


def test_markdown_links_are_checked_relative_to_the_page(tmp_path):
    page = _docs(tmp_path)
    page.write_text(
        '[ok](guide.md#real) [file](gone.md) [anchor](guide.md#gone) '
        '[same page](#top) [dir](.) [web](https://example.com/x.md)\n'
        '```\n[fenced](nope.md)\n```\n',
        encoding='utf-8',
    )
    assert links.broken_links(page, tmp_path) == [
        f'{page}: no such file: gone.md',
        f'{page}: no such anchor: guide.md#gone',
        f'{page}: no such anchor: #top',
    ]


def test_main_exits_1_for_a_broken_link_and_0_for_a_clean_page(
    tmp_path, monkeypatch, capsys
):
    page = _docs(tmp_path)
    monkeypatch.chdir(tmp_path)
    page.write_text('[ok](guide.md#real)\n', encoding='utf-8')
    assert links.main([str(page)]) == 0
    assert capsys.readouterr().out == ''
    page.write_text('[bad](guide.md#gone)\n', encoding='utf-8')
    assert links.main([str(page)]) == 1
    out = capsys.readouterr().out
    assert 'no such anchor: guide.md#gone' in out
    assert '1 broken documentation reference(s).' in out


def test_main_refuses_no_paths_with_exit_2(capsys):
    assert links.main([]) == 2
    assert 'usage: check_doc_links.py' in capsys.readouterr().err


def test_main_refuses_a_tracked_scan_that_lists_nothing(tmp_path, monkeypatch, capsys):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert links.main(['--tracked']) == 2
    assert 'git listed no file to check' in capsys.readouterr().err


def test_a_heading_in_a_fence_is_no_anchor_and_an_explicit_id_is(tmp_path):
    page = tmp_path / 'page.md'
    page.write_text(
        '# Real\n```\n# shell comment\n```\n<a id="custom-id"></a>\n'
        '~~~\n# also not\n~~~\n',
        encoding='utf-8',
    )
    assert links.anchors_in(page) == {'real', 'custom-id'}


def test_a_link_in_angle_brackets_may_hold_a_space(tmp_path):
    page = _docs(tmp_path)
    (tmp_path / 'docs' / 'my guide.md').write_text('# Part\n', encoding='utf-8')
    page.write_text(
        '[ok](<my guide.md#part>) [file](<gone file.md>) '
        '[anchor](<my guide.md#gone>)\n',
        encoding='utf-8',
    )
    assert links.broken_links(page, tmp_path) == [
        f'{page}: no such file: gone file.md',
        f'{page}: no such anchor: my guide.md#gone',
    ]


def test_a_bare_root_level_file_name_is_a_reference_only_when_the_file_exists(
    tmp_path,
):
    (tmp_path / 'SAMPLE.md').write_text('# Sample\n', encoding='utf-8')
    script = tmp_path / 'tool.py'
    # notes.md is prose: no such file at the root, so nothing to check
    script.write_text('# see SAMPLE.md#gone and notes.md\n', encoding='utf-8')
    assert links.broken_links(script, tmp_path) == [
        f'{script}: no such anchor: SAMPLE.md#gone'
    ]


def test_links_to_other_sites_are_not_followed(tmp_path):
    page = _docs(tmp_path)
    page.write_text(
        '[a](https://x.example/gone.md) [b](http://x.example/gone.md#nope) '
        '[c](mailto:me@example.com) [d](ftp://x.example/gone.md)\n',
        encoding='utf-8',
    )
    assert links.broken_links(page, tmp_path) == []
