"""The settings hook.

What counts as read, what counts as documented, and both failure directions.
"""

import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location(
    'check_settings_documented', TOOLS / 'check_settings_documented.py'
)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

SETTINGS_PY = """
SETTINGS = (
    Setting('MODE', 'choice', 'Required', 'online or airgapped.', choices=MODES),
    Setting(
        'RETAIN_RELEASES',
        'int',
        'Generation',
        'Releases kept for rollback.',
    ),
)
setting = REGISTRY.get(name)
value = _get(env, 'NOT_A_REGISTRY_ENTRY')
"""

ENTRYPOINT_SH = """
[[ -n "$PUBLIC_URL" ]] || die "PUBLIC_URL must be set"
export PUBLIC_URL="${PUBLIC_URL%/}"
adapter="${REPO_ADAPTER-on}"
auth_file="${DOWNLOAD_PROXY_AUTH_FILE:-/run/secrets/download_proxy_auth}"
DOWNLOAD_PROXY_AUTH_HEADER="Basic x"
export DOWNLOAD_PROXY_AUTH_HEADER
echo "${DOWNLOAD_PROXY_AUTH_HEADER}" "${local_name:-x}"
"""

COMPOSE_YAML = """
image: ${UC_IMAGE:-easy-update-center/uc:local}
env_file: ${UC_ENV_FILE:-.env}
ports:
  - "${HTTPS_PORT:-8443}:8443"
  - "${HTTP_PORT}:8080"
"""

PAGE = """# Configuration reference

| Setting | Default |
|---|---|
| `MODE` | - |
| `REPO_URL`, `REPO_KEY` | see `DOWNLOAD_BACKEND` |

```text
| `IN_A_FENCE` | ignored |
```

## Read by bundled software

| Setting | Read by |
|---|---|
| `JAVA_TOOL_OPTIONS` | the JVM |
"""


def test_registry_entries_are_read():
    assert hook.registry_names(SETTINGS_PY) == {'MODE', 'RETAIN_RELEASES'}


def test_registry_names_ignore_comments_and_docstrings():
    text = '''"""A docstring: Setting('IN_A_DOCSTRING', 'str')."""
# Setting('IN_A_COMMENT', 'str')
SETTINGS = (Setting('REAL', 'str', 'g', 'x.'),)
note = "Setting('IN_A_STRING'"
'''
    assert hook.registry_names(text) == {'REAL'}


def test_registry_names_need_a_string_constant_first_argument():
    text = (
        "Setting(NAME, 'x')\nSetting(env='KEYWORD')\n"
        "Setting()\nother.Setting('ATTRIBUTE')\n"
    )
    assert hook.registry_names(text) == set()


def test_main_refuses_a_settings_file_that_does_not_parse(
    tmp_path, monkeypatch, capsys
):
    _tree(
        tmp_path, '| Setting |\n|---|\n| `MODE` |\n| `TLS_KEY_FILE` |\n| `UC_IMAGE` |\n'
    )
    (tmp_path / 'uc' / 'uc' / 'settings.py').write_text(
        "Setting('MODE',\n", encoding='utf-8'
    )
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 2
    assert 'uc/uc/settings.py does not parse' in capsys.readouterr().err


def test_shell_reads_are_the_defaulted_expansions_only():
    assert hook.names_read(ENTRYPOINT_SH, hook.SHELL_READ) == {
        'REPO_ADAPTER',
        'DOWNLOAD_PROXY_AUTH_FILE',
    }


def test_every_compose_interpolation_is_read():
    assert hook.names_read(COMPOSE_YAML, hook.COMPOSE_READ) == {
        'UC_IMAGE',
        'UC_ENV_FILE',
        'HTTPS_PORT',
        'HTTP_PORT',
    }


def test_documented_names_come_from_first_cells_outside_fences():
    names, bundled = hook.documented(PAGE)
    assert names == {'MODE', 'REPO_URL', 'REPO_KEY', 'JAVA_TOOL_OPTIONS'}
    assert bundled == {'JAVA_TOOL_OPTIONS'}


def test_undocumented_setting_is_reported_with_its_reader():
    lines = hook.problems(
        {'MODE': 'uc/uc/settings.py', 'TLS_KEY_FILE': 'web/entrypoint.sh'},
        {'MODE'},
        set(),
    )
    assert lines == [
        (
            'TLS_KEY_FILE is read by web/entrypoint.sh but not documented in '
            'docs/reference/configuration.md'
        )
    ]


def test_documented_setting_that_nothing_reads_is_reported():
    lines = hook.problems({'MODE': 'uc/uc/settings.py'}, {'MODE', 'OLD_SETTING'}, set())
    assert lines == [
        (
            'OLD_SETTING is documented in docs/reference/configuration.md '
            'but nothing reads it'
        )
    ]


def test_bundled_software_settings_need_no_reader():
    assert (
        hook.problems(
            {'MODE': 'uc/uc/settings.py'},
            {'MODE', 'JAVA_TOOL_OPTIONS'},
            {'JAVA_TOOL_OPTIONS'},
        )
        == []
    )


def _tree(root, page):
    (root / 'uc' / 'uc').mkdir(parents=True)
    (root / 'web').mkdir()
    (root / 'docs' / 'reference').mkdir(parents=True)
    (root / 'uc' / 'uc' / 'settings.py').write_text(
        "SETTINGS = (Setting('MODE', 'choice', 'Required', 'x.'),)\n", encoding='utf-8'
    )
    (root / 'web' / 'entrypoint.sh').write_text(
        'key="${TLS_KEY_FILE:-}"\nimage="${UC_IMAGE:-x}"\n', encoding='utf-8'
    )
    # every name is read by a later source too: the first one is the reader named
    (root / 'compose.yaml').write_text(
        'image: ${UC_IMAGE:-x}\nmode: ${MODE:-x}\n', encoding='utf-8'
    )
    (root / 'compose.airgapped.yaml').write_text(
        'tls: ${TLS_KEY_FILE:-}\n', encoding='utf-8'
    )
    (root / 'docs' / 'reference' / 'configuration.md').write_text(
        page, encoding='utf-8'
    )


def test_code_settings_names_the_first_reader(tmp_path):
    _tree(tmp_path, '')
    assert hook.code_settings(tmp_path) == {
        'MODE': 'uc/uc/settings.py',
        'TLS_KEY_FILE': 'web/entrypoint.sh',
        'UC_IMAGE': 'web/entrypoint.sh',
    }


def test_main_passes_when_both_sides_agree(tmp_path, monkeypatch):
    _tree(
        tmp_path, '| Setting |\n|---|\n| `MODE` |\n| `TLS_KEY_FILE` |\n| `UC_IMAGE` |\n'
    )
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 0


def test_main_parses_the_registry_once(tmp_path, monkeypatch):
    _tree(
        tmp_path, '| Setting |\n|---|\n| `MODE` |\n| `TLS_KEY_FILE` |\n| `UC_IMAGE` |\n'
    )
    monkeypatch.chdir(tmp_path)
    parsed = []
    parse = hook.ast.parse

    def recording_parse(source, *args, **kwargs):
        parsed.append(source)
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(hook.ast, 'parse', recording_parse)
    assert hook.main([]) == 0
    assert len(parsed) == 1


def test_main_fails_and_names_the_gap(tmp_path, monkeypatch, capsys):
    _tree(tmp_path, '| Setting |\n|---|\n| `MODE` |\n| `UC_IMAGE` |\n')
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 1
    assert 'TLS_KEY_FILE is read by web/entrypoint.sh' in capsys.readouterr().out


def test_main_refuses_when_the_page_is_missing(tmp_path, monkeypatch):
    _tree(tmp_path, '')
    (tmp_path / 'docs' / 'reference' / 'configuration.md').unlink()
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 2


def test_main_refuses_a_registry_with_no_settings(tmp_path, monkeypatch, capsys):
    # The other readers still find names, so a renamed Setting class would otherwise
    # read as a flood of "documented but nothing reads it" findings rather than a
    # pattern that stopped matching.
    _tree(
        tmp_path, '| Setting |\n|---|\n| `MODE` |\n| `TLS_KEY_FILE` |\n| `UC_IMAGE` |\n'
    )
    (tmp_path / 'uc' / 'uc' / 'settings.py').write_text(
        "SETTINGS = (Option('MODE', 'choice', 'Required', 'x.'),)\n", encoding='utf-8'
    )
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 2
    captured = capsys.readouterr()
    assert 'found no settings in uc/uc/settings.py' in captured.err
    assert not captured.out


def test_main_refuses_when_nothing_is_documented(tmp_path, monkeypatch):
    _tree(tmp_path, '# Configuration reference\n')
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 2


def test_main_refuses_arguments():
    assert hook.main(['docs/reference/configuration.md']) == 2
