"""The Unicode gremlin checker.

Which characters it replaces, what it leaves alone, and its exit codes.
"""

import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
_spec = importlib.util.spec_from_file_location(
    'check_gremlins', TOOLS / 'check_gremlins.py'
)
gremlins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gremlins)

# built with chr() because ruff would turn an escape back into the character, and the
# gremlins hook would then rewrite this file
LSQ, RSQ, LDQ, RDQ = (chr(0x2018), chr(0x2019), chr(0x201C), chr(0x201D))
NBSP, EM_DASH, ELLIPSIS = chr(0xA0), chr(0x2014), chr(0x2026)
ZERO_WIDTH_SPACE, BOM = chr(0x200B), chr(0xFEFF)
SMART = f'{LSQ}quoted{RSQ} {LDQ}phrase{RDQ}'
EXOTIC = f'a{NBSP}b{EM_DASH}c{ELLIPSIS} z{ZERO_WIDTH_SPACE}w{BOM}'


def write(tmp_path, text, name='sample.txt'):
    path = tmp_path / name
    path.write_bytes(text.encode('utf-8'))
    return path


def test_smart_quotes_are_replaced_by_ascii(tmp_path, capsys):
    path = write(tmp_path, f'say {SMART}\n')
    assert gremlins.main(['--fix', str(path)]) == 1
    assert path.read_text(encoding='utf-8') == 'say \'quoted\' "phrase"\n'
    assert 'U+2018' in capsys.readouterr().out


def test_spaces_dashes_ellipsis_and_invisibles_are_replaced_or_removed(tmp_path):
    path = write(tmp_path, EXOTIC)
    assert gremlins.main(['--fix', str(path)]) == 1
    assert path.read_text(encoding='utf-8') == 'a b-c... zw'


def test_ascii_and_intentional_glyphs_are_untouched(tmp_path, capsys):
    original = 'plain \'text\' "here" - ok\n→ ° · │\n'
    path = write(tmp_path, original)
    assert gremlins.main(['--fix', str(path)]) == 0
    assert path.read_bytes() == original.encode('utf-8')
    assert capsys.readouterr().out == ''


def test_without_fix_it_reports_the_position_and_changes_nothing(tmp_path, capsys):
    path = write(tmp_path, f'ok\nsay {SMART}\n')
    before = path.read_bytes()
    assert gremlins.main([str(path)]) == 1
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert f'{path}:2:5: U+2018' in out
    assert '4 unicode gremlin(s) found' in out


def test_a_directory_walk_skips_virtualenvs_but_a_named_file_is_always_checked(
    tmp_path,
):
    write(tmp_path, f'x{EM_DASH}y', 'top.txt')
    venv = tmp_path / '.venv313'
    venv.mkdir()
    hidden = write(venv, f'x{EM_DASH}y')
    assert {p.name for p in gremlins.iter_files([str(tmp_path)])} == {'top.txt'}
    assert list(gremlins.iter_files([str(hidden)])) == [hidden]


def test_a_binary_file_is_skipped(tmp_path):
    path = tmp_path / 'blob.bin'
    path.write_bytes(b'\xff\xfe\x00\xe2\x80')
    assert gremlins.main([str(path)]) == 0
