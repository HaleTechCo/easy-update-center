import datetime as dt
import hashlib
import json

import pytest

from archives import make_tar
from uc.http import HttpError
from uc.resources import (
    INFO_FILE,
    ResourcesError,
    fetch_upstream,
    merge_overlay,
    prepare_resources,
    save_last_good,
)
from uc.timeutil import iso
from ucsupport import make_settings

NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.UTC)
SHA = 'ebc2b7754082994393232c08c13230a9c6299435'
PREFIX = f'update-center2-{SHA}/'
UPSTREAM = {
    PREFIX + 'resources/warnings.json': b'[{"id": "w1", "message": "upstream"}]',
    PREFIX + 'resources/artifact-ignores.properties': b'bad-plugin-1.0 = https://example/\n',
    PREFIX + 'resources/index-template.html': b'<html>{{ content }}</html>',
    PREFIX + 'src/test/resources/warnings.json': b'[]',
    PREFIX + 'README.adoc': b'readme',
}


def github_getter(tarball):
    def get(url, headers=None, timeout=60):
        if url == 'https://api.github.com/repos/jenkins-infra/update-center2/commits/master':
            return json.dumps({'sha': SHA, 'commit': {'committer': {'date': '2026-09-16T13:29:23Z'}}}).encode()
        if url == f'https://codeload.github.com/jenkins-infra/update-center2/tar.gz/{SHA}':
            return tarball
        msg = f'unexpected GET {url}'
        raise HttpError(msg)

    return get


def offline(url, headers=None, timeout=60):
    msg = f'GET {url} failed: offline'
    raise HttpError(msg)


def test_github_source(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='github')
    dest = tmp_path / 'res'
    info = fetch_upstream(settings, dest, NOW, get=github_getter(make_tar(UPSTREAM)))
    assert (info.source, info.ref, info.data_date, info.fetched_at) == (
        'github',
        SHA,
        '2026-09-16T13:29:23Z',
        '2026-09-22T12:00:00Z',
    )
    assert info.age_days(NOW) == 5
    assert json.loads((dest / 'warnings.json').read_text()) == [{'id': 'w1', 'message': 'upstream'}]
    assert not (dest / 'README.adoc').exists()
    assert json.loads((dest / INFO_FILE).read_text())['ref'] == SHA


def test_url_source_checks_sha256(tmp_path, secrets_dir):
    tarball = make_tar({name[len(PREFIX) :]: data for name, data in UPSTREAM.items()})
    digest = hashlib.sha256(tarball).hexdigest()

    def get(url, headers=None, timeout=60):
        return tarball

    ok = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='url', RESOURCES_URL='https://art/generic/uc2.tgz', RESOURCES_SHA256=digest)
    info = fetch_upstream(ok, tmp_path / 'ok', NOW, get=get)
    assert info.ref == f'sha256:{digest}'
    assert info.data_date == iso(dt.datetime.fromtimestamp(1_758_000_000, dt.UTC))

    bad = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='url', RESOURCES_URL='https://art/generic/uc2.tgz', RESOURCES_SHA256='0' * 64)
    with pytest.raises(ResourcesError, match='checksum mismatch'):
        fetch_upstream(bad, tmp_path / 'bad', NOW, get=get)


def test_bundled_source(tmp_path, secrets_dir):
    info = fetch_upstream(make_settings(tmp_path, secrets_dir), tmp_path / 'b', NOW)
    assert info.source == 'bundled'
    assert len(info.ref) == 40
    assert (tmp_path / 'b' / 'warnings.json').is_file()


def test_missing_required_file(tmp_path, secrets_dir):
    tarball = make_tar({'resources/warnings.json': b'[]'})
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='url', RESOURCES_URL='https://x/r.tgz')
    with pytest.raises(ResourcesError, match='missing artifact-ignores.properties, index-template.html'):
        fetch_upstream(settings, tmp_path / 'r', NOW, get=lambda *a, **k: tarball)


def test_unsafe_archive_path(tmp_path, secrets_dir):
    tarball = make_tar({'resources/warnings.json': b'[]', 'resources/../../evil': b'x'})
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='url', RESOURCES_URL='https://x/r.tgz')
    with pytest.raises(ResourcesError, match='unsafe path'):
        fetch_upstream(settings, tmp_path / 'r', NOW, get=lambda *a, **k: tarball)


def test_not_a_tarball(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='url', RESOURCES_URL='https://x/r.tgz')
    with pytest.raises(ResourcesError, match='not a tar'):
        fetch_upstream(settings, tmp_path / 'r', NOW, get=lambda *a, **k: b'<html>')


def test_overlay_merge(tmp_path):
    upstream = tmp_path / 'up'
    upstream.mkdir()
    (upstream / 'warnings.json').write_text(
        json.dumps(
            [
                {'id': 'w1', 'message': 'upstream'},
                {'id': 'w2', 'message': 'keep'},
            ]
        )
    )
    (upstream / 'artifact-ignores.properties').write_bytes(b'old-plugin = https://x/')
    (upstream / 'index-template.html').write_text('upstream')
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    (overlay / 'warnings.json').write_text(
        json.dumps(
            [
                {'id': 'w1', 'message': 'ours'},
                {'id': 'corp-1', 'message': 'new'},
            ]
        )
    )
    (overlay / 'artifact-ignores.properties').write_text('internal-bad-plugin = https://corp/why\n')
    (overlay / 'index-template.html').write_text('ours')
    (overlay / 'README.md').write_text('ignored')

    dest = tmp_path / 'merged'
    assert merge_overlay(upstream, overlay, dest) == [
        'artifact-ignores.properties',
        'index-template.html',
        'warnings.json',
    ]
    assert [w['message'] for w in json.loads((dest / 'warnings.json').read_text())] == ['ours', 'keep', 'new']
    props = (dest / 'artifact-ignores.properties').read_text()
    assert props.startswith('old-plugin = https://x/\n')
    assert props.rstrip().endswith('internal-bad-plugin = https://corp/why')
    assert (dest / 'index-template.html').read_text() == 'ours'
    assert not (dest / 'README.md').exists()
    assert json.loads((upstream / 'warnings.json').read_text())[0]['message'] == 'upstream'


def test_overlay_warning_without_id(tmp_path):
    upstream = tmp_path / 'up'
    upstream.mkdir()
    (upstream / 'warnings.json').write_text('[]')
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    (overlay / 'warnings.json').write_text('[{"message": "no id"}]')
    with pytest.raises(ResourcesError, match="needs an 'id'"):
        merge_overlay(upstream, overlay, tmp_path / 'merged')


def test_overlay_warning_with_duplicate_id(tmp_path):
    upstream = tmp_path / 'up'
    upstream.mkdir()
    (upstream / 'warnings.json').write_text('[]')
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    (overlay / 'warnings.json').write_text(
        json.dumps(
            [
                {'id': 'SECURITY-1234', 'message': 'one'},
                {'id': 'SECURITY-1234', 'message': 'two'},
            ]
        )
    )
    with pytest.raises(ResourcesError, match=r'duplicate warning id\(s\) in the overlay: SECURITY-1234'):
        merge_overlay(upstream, overlay, tmp_path / 'merged')


def test_missing_overlay_dir_is_fine(tmp_path):
    upstream = tmp_path / 'up'
    upstream.mkdir()
    (upstream / 'warnings.json').write_text('[]')
    assert merge_overlay(upstream, tmp_path / 'none', tmp_path / 'merged') == []


def test_prepare_falls_back_to_last_good(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='github')
    first = prepare_resources(settings, tmp_path / 'w1', NOW, get=github_getter(make_tar(UPSTREAM)))
    save_last_good(first, settings.data_dir)
    second = prepare_resources(settings, tmp_path / 'w2', NOW, get=offline)
    assert second.info.ref == SHA
    assert 'using last-good copy' in second.warnings[0]
    assert (second.path / 'warnings.json').is_file()


def test_prepare_without_last_good_fails(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='github')
    with pytest.raises(ResourcesError, match='no last-good copy'):
        prepare_resources(settings, tmp_path / 'w', NOW, get=offline)


def test_prepare_applies_overlay_but_keeps_upstream_pristine(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE='github')
    settings.overlay_dir.mkdir(parents=True)
    (settings.overlay_dir / 'artifact-ignores.properties').write_text('corp-bad = https://corp/\n')
    prepared = prepare_resources(settings, tmp_path / 'w', NOW, get=github_getter(make_tar(UPSTREAM)))
    assert prepared.overlay == ['artifact-ignores.properties']
    assert 'corp-bad' in (prepared.path / 'artifact-ignores.properties').read_text()
    assert 'corp-bad' not in (prepared.upstream / 'artifact-ignores.properties').read_text()
