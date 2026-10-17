"""Unit tests for uc.validate.

The checks a staged release must pass before it goes live.
"""

import json
import re
from pathlib import Path

import pytest

from sitefixture import TEST_CERT_B64, write_generator_output
from uc.layout import finalize_layout
from uc.model import Tiers
from uc.validate import (
    ValidationError,
    count_plugins,
    download_key,
    load_site_json,
    validate_release,
)

URL = 'https://uc.example.com'
ROOT = URL + '/download'
TIERS = Tiers(('2.540',), ('2.528.1',))
TEMPLATES = Path('/opt/uc/templates')
OFFERED = {
    'plugins/git/5.2.0',
    'plugins/credentials/1415.v831096eb_5534',
    'war/2.528.1',
}


def build(staging, tiers=TIERS, public_url=URL, **doc_options):
    write_generator_output(staging, public_url, tiers, **doc_options)
    finalize_layout(staging, tiers, TEMPLATES)
    (staging / '.htaccess').write_text('RewriteEngine on\n')
    (staging / '.htdownload-map').write_text('')
    (staging / 'tiers.json').write_text(
        json.dumps(
            {'weeklyCores': list(tiers.weekly), 'stableCores': list(tiers.stable)}
        )
    )
    return staging


def validate(staging, **overrides):
    options = {
        'tiers': TIERS,
        'downloads_root': ROOT,
        'signing_cert_b64': TEST_CERT_B64,
        'previous_plugin_count': None,
        'min_ratio': 0.9,
        'force': False,
        'download_map': None,
    }
    options.update(overrides)
    return validate_release(staging, **options)


def test_valid_release(tmp_path):
    result = validate(build(tmp_path))
    assert result.plugin_count == 2
    assert result.offered == OFFERED


def test_load_site_json_strips_jsonp(tmp_path):
    (tmp_path / 'a.json').write_text('updateCenter.post(\n{"a": 1}\n);')
    (tmp_path / 'b.json').write_text('{"b": 2}')
    assert load_site_json(tmp_path / 'a.json') == {'a': 1}
    assert load_site_json(tmp_path / 'b.json') == {'b': 2}


def test_download_key():
    assert (
        download_key(ROOT + '/plugins/git/5.2.0/git.hpi', ROOT) == 'plugins/git/5.2.0'
    )
    assert download_key(ROOT + '/war/2.528.1/jenkins.war', ROOT) == 'war/2.528.1'
    assert (
        download_key(
            'https://updates.jenkins.io/download/plugins/git/5.2.0/git.hpi', ROOT
        )
        is None
    )
    assert download_key(ROOT + '/plugins/git/5.2.0/other.hpi', ROOT) is None


def test_missing_files(tmp_path):
    staging = build(tmp_path)
    (staging / '.htdownload-map').unlink()
    (staging / 'current' / 'plugin-versions.json').unlink()
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert 'missing .htdownload-map' in err.value.problems
    assert 'current/ is missing plugin-versions.json' in err.value.problems


def test_missing_tier_directory(tmp_path):
    staging = build(tmp_path, tiers=Tiers())
    with pytest.raises(
        ValidationError, match=re.escape('dynamic-2.540/ is missing update-center.json')
    ):
        validate(staging)


def test_wrong_signing_certificate(tmp_path):
    with pytest.raises(
        ValidationError, match='not signed with the configured signing certificate'
    ):
        validate(build(tmp_path), signing_cert_b64='OTHER')


@pytest.mark.parametrize(
    'name', ['current/update-center.actual.json', 'current/plugin-versions.json']
)
def test_a_site_document_that_is_not_an_object_is_refused(tmp_path, name):
    staging = build(tmp_path)
    (staging / name).write_text('[]')
    with pytest.raises(ValidationError, match='does not parse: not a JSON object'):
        validate(staging)


@pytest.mark.parametrize('plugins', [['git'], 'git', 5])
@pytest.mark.parametrize(
    'name', ['current/update-center.actual.json', 'current/plugin-versions.json']
)
def test_a_plugins_section_that_is_not_an_object_is_refused(tmp_path, name, plugins):
    staging = build(tmp_path)
    doc = json.loads((staging / name).read_text())
    doc['plugins'] = plugins
    (staging / name).write_text(json.dumps(doc))
    with pytest.raises(ValidationError, match=f'{name}: plugins is not an object'):
        validate(staging)


def test_a_plugin_versions_entry_that_is_not_an_object_is_refused(tmp_path):
    staging = build(tmp_path)
    name = 'current/plugin-versions.json'
    doc = json.loads((staging / name).read_text())
    doc['plugins'] = {'git': ['5.2.0']}
    (staging / name).write_text(json.dumps(doc))
    with pytest.raises(ValidationError, match=f'{name}: plugins.git is not an object'):
        validate(staging)


def test_download_urls_must_use_public_url(tmp_path):
    with pytest.raises(
        ValidationError,
        match=re.escape('download URLs outside https://uc.example.com/download/'),
    ):
        validate(build(tmp_path, public_url='https://updates.jenkins.io'))


def test_htaccess_must_not_redirect_elsewhere(tmp_path):
    staging = build(tmp_path)
    (staging / '.htaccess').write_text('RewriteRule ^x https://get.jenkins.io/x [R]\n')
    with pytest.raises(ValidationError, match='redirects to an absolute URL'):
        validate(staging)


def test_plugin_count_ratio_and_force(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(
        ValidationError,
        match=re.escape('plugin count 2 < 0.9 \N{MULTIPLICATION SIGN} 10'),
    ):
        validate(staging, previous_plugin_count=10)
    assert validate(staging, previous_plugin_count=10, force=True).plugin_count == 2


def test_a_release_without_plugins_is_refused(tmp_path):
    with pytest.raises(ValidationError, match='the release has no plugins'):
        validate(build(tmp_path, plugins={}))


def test_download_map_must_cover_offered_versions(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(
        ValidationError,
        match=re.escape(
            'no entry for plugins/credentials/1415.v831096eb_5534, war/2.528.1'
        ),
    ):
        validate(staging, download_map={'plugins/git/5.2.0': 'a'})
    validate(staging, download_map=dict.fromkeys(OFFERED, 'x'))


def test_a_site_without_a_core_is_valid(tmp_path):
    """A repository with no jenkins-war publishes plugins only: no core, no tiers.

    See docs/architecture/generation-pipeline.md#step-9-validate.
    """
    result = validate(
        build(tmp_path, tiers=Tiers((), ()), core=None), tiers=Tiers((), ())
    )
    assert result.plugin_count == 2
    assert result.offered == OFFERED - {'war/2.528.1'}


def test_an_offered_core_needs_a_download_url(tmp_path):
    staging = build(tmp_path)
    doc = json.loads((staging / 'current' / 'update-center.actual.json').read_text())
    del doc['core']['url']
    (staging / 'current' / 'update-center.actual.json').write_text(json.dumps(doc))
    with pytest.raises(ValidationError, match='current core None'):
        validate(staging)


def test_an_offered_core_needs_latest_core_txt(tmp_path):
    staging = build(tmp_path)
    (staging / 'current' / 'latestCore.txt').unlink()
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert 'current/ is missing latestCore.txt' in err.value.problems


def test_count_plugins(tmp_path):
    assert count_plugins(build(tmp_path) / 'current') == 2
    assert count_plugins(tmp_path / 'nope') is None


def edit_versions(staging, change):
    path = staging / 'current' / 'plugin-versions.json'
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


def test_plugin_versions_that_does_not_parse_is_refused(tmp_path):
    staging = build(tmp_path)
    (staging / 'current' / 'plugin-versions.json').write_text('{not json')
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert any(
        p.startswith('current/plugin-versions.json does not parse')
        for p in err.value.problems
    )


def test_plugin_versions_must_be_signed_with_the_configured_certificate(tmp_path):
    staging = build(tmp_path)
    edit_versions(staging, lambda doc: doc['signature'].update(certificates=['other']))
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert (
        'current/plugin-versions.json is not signed with the configured signing '
        'certificate'
    ) in err.value.problems


@pytest.mark.parametrize('url', ['https://get.jenkins.io/git.hpi', None, 5])
def test_a_plugin_versions_download_url_outside_the_site_is_refused(tmp_path, url):
    staging = build(tmp_path)

    def change(doc):
        doc['plugins']['git']['5.2.0']['url'] = url

    edit_versions(staging, change)
    with pytest.raises(
        ValidationError, match=re.escape(f'plugin-versions git:5.2.0 {url}')
    ):
        validate(staging)


def test_old_versions_in_plugin_versions_need_no_download_map_entry(tmp_path):
    staging = build(tmp_path)

    def change(doc):
        doc['plugins']['git']['4.0.0'] = {'url': f'{ROOT}/plugins/git/4.0.0/git.hpi'}

    edit_versions(staging, change)
    # only what the site documents offer must be in the map, not every old release
    assert validate(staging, download_map=dict.fromkeys(OFFERED, 'x')).offered == (
        frozenset(OFFERED)
    )
    # the check is live, and of what the site documents offer it names only the
    # missing one: the map lacks 4.0.0 as well
    incomplete = dict.fromkeys(OFFERED - {'plugins/git/5.2.0'}, 'x')
    with pytest.raises(ValidationError) as err:
        validate(staging, download_map=incomplete)
    assert err.value.problems == ['download map has no entry for plugins/git/5.2.0']


@pytest.mark.parametrize('name', ['update-center.json', 'update-center.actual.json'])
def test_a_truncated_site_document_is_refused_and_its_site_checked_no_further(
    tmp_path, name
):
    staging = build(tmp_path)
    good = (staging / 'dynamic-2.540' / name).read_text()
    (staging / 'dynamic-2.540' / name).write_text(good[: len(good) // 2])
    with pytest.raises(ValidationError) as err:
        validate(staging)
    parse = [p for p in err.value.problems if 'does not parse' in p]
    assert len(parse) == 1
    assert parse[0].startswith('dynamic-2.540/ update-center JSON does not parse: ')
    # the other sites are still checked, and this one stops at the parse error
    assert not [p for p in err.value.problems if 'dynamic-2.540' in p and p != parse[0]]
