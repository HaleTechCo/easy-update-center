"""Unit tests for uc.versions, plus the image's pinned generator jar and bundled resources."""

import zipfile
from pathlib import Path

from uc.versions import VersionTool

APP = Path('/opt/uc')


def tool() -> VersionTool:
    return VersionTool(APP / 'generator', APP / 'tools')


def test_compare_uses_jenkins_semantics():
    pairs = [('2.528.1', '2.540'), ('1.10', '1.9'), ('2.0', '2.0'), ('1.0-beta-1', '1.0')]
    assert tool().compare_many(pairs) == [-1, 1, 0, -1]


def test_sort_is_numeric_not_lexical():
    assert tool().sort(['2.540', '2.528.1', '1.10', '1.9', '2.528.10']) == [
        '1.9',
        '1.10',
        '2.528.1',
        '2.528.10',
        '2.540',
    ]


def test_max_by_key_keeps_newest_per_key():
    assert tool().max_by_key([('git', '5.2.0'), ('git', '5.10.1'), ('cred', '1.0')]) == {
        'git': '5.10.1',
        'cred': '1.0',
    }


def test_empty_input_returns_without_running_java():
    assert VersionTool(Path('/nonexistent'), Path('/nonexistent')).compare_many([]) == []


def test_generator_jar_is_pinned_and_patched():
    jar = APP / 'generator' / 'update-center2.jar'
    assert jar.resolve().name == 'update-center2-3.18.4.jar'
    with zipfile.ZipFile(jar) as archive:
        compiled = archive.read('io/jenkins/update_center/Popularities.class')
    assert b'POPULARITY_JSON_URL' in compiled


def test_bundled_resources_are_present():
    assert (APP / 'resources' / 'warnings.json').is_file()
    assert (APP / 'resources' / '.uc-resources.json').is_file()
    assert (APP / 'site' / 'generate-htaccess.sh').is_file()
