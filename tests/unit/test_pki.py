"""Unit tests for uc.pki.

The root/signing certificate chain, its validity checks and rotation.
"""

import base64
import datetime as dt
import shutil
import subprocess

import pytest

from uc.pki import (
    ROOT_CERT,
    SIGNING_CERT,
    PkiError,
    cert_der_b64,
    cert_info,
    init_pki,
    key_matches_cert,
    rotate_signing,
    verify_chain,
)


def test_init_creates_a_valid_chain(pki_material):
    assert verify_chain(
        pki_material / 'root-ca.crt', pki_material / 'update-center.cert'
    )
    assert key_matches_cert(
        pki_material / 'update-center.key', pki_material / 'update-center.cert'
    )
    assert (pki_material / 'root' / 'root-ca.key').is_file()
    assert not (pki_material / 'root-ca.key').exists()
    assert (pki_material / 'update-center.key').stat().st_mode & 0o777 == 0o600
    assert (pki_material / 'root' / 'root-ca.key').stat().st_mode & 0o777 == 0o600


def test_cert_info(pki_material):
    info = cert_info(pki_material / 'update-center.cert')
    assert 'CN=Easy Update Center Signing' in info.subject
    assert 43 <= info.days_left(dt.datetime.now(dt.UTC)) <= 45
    assert len(info.fingerprint_sha256.split(':')) == 32


def test_signing_cert_is_not_a_ca_and_root_is(pki_material):
    def text(name):
        return subprocess.run(
            ['openssl', 'x509', '-in', str(pki_material / name), '-noout', '-text'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert 'CA:FALSE' in text('update-center.cert')
    assert 'Digital Signature' in text('update-center.cert')
    assert 'CA:TRUE, pathlen:0' in text('root-ca.crt')


def test_init_refuses_to_overwrite(pki_material):
    with pytest.raises(PkiError, match='refusing to overwrite'):
        init_pki(pki_material)


def test_org_with_slash_is_rejected(tmp_path):
    with pytest.raises(PkiError, match='org'):
        init_pki(tmp_path / 'x', org='a/b')


def test_rotate_issues_a_new_cert_under_the_same_root(pki_material, tmp_path):
    work = tmp_path / 'pki'
    shutil.copytree(pki_material, work)
    previous = {
        name: (work / name).read_bytes()
        for name in ('update-center.key', 'update-center.cert')
    }
    rotate_signing(work, days=5)
    assert (work / 'update-center.cert').read_bytes() != previous['update-center.cert']
    assert verify_chain(work / 'root-ca.crt', work / 'update-center.cert')
    assert key_matches_cert(work / 'update-center.key', work / 'update-center.cert')
    assert (work / 'update-center.key').stat().st_mode & 0o777 == 0o600
    assert {name: (work / f'{name}.old').read_bytes() for name in previous} == previous
    assert sorted(path.name for path in work.iterdir()) == [
        'root',
        'root-ca.crt',
        'update-center.cert',
        'update-center.cert.old',
        'update-center.key',
        'update-center.key.old',
    ]


def test_cert_der_b64_matches_openssl(pki_material):
    der = subprocess.run(
        [
            'openssl',
            'x509',
            '-in',
            str(pki_material / 'update-center.cert'),
            '-outform',
            'DER',
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert (
        cert_der_b64(pki_material / 'update-center.cert')
        == base64.b64encode(der).decode()
    )


def test_key_mismatch_is_detected(pki_material, tmp_path):
    other = tmp_path / 'other.key'
    subprocess.run(
        [
            'openssl',
            'genpkey',
            '-algorithm',
            'RSA',
            '-pkeyopt',
            'rsa_keygen_bits:2048',
            '-out',
            str(other),
        ],
        check=True,
        capture_output=True,
    )
    assert not key_matches_cert(other, pki_material / 'update-center.cert')


def test_cert_info_on_garbage_raises(tmp_path):
    bad = tmp_path / 'bad.crt'
    bad.write_text('not a certificate')
    with pytest.raises(PkiError):
        cert_info(bad)


def test_verify_chain_trusts_only_the_given_root(tmp_path, monkeypatch):
    # update-center2 only warns on a bad chain, so this check is the one that refuses:
    # a CA in openssl's default directory must not count.
    ours, theirs = tmp_path / 'ours', tmp_path / 'theirs'
    init_pki(ours)
    # A different organisation, so the two roots have different subjects: with the
    # same subject, the given root would shadow the planted one and hide the fault.
    init_pki(theirs, org='Another Update Center')
    name = subprocess.run(
        ['openssl', 'x509', '-hash', '-noout', '-in', str(theirs / ROOT_CERT)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    store = tmp_path / 'store'
    store.mkdir()
    shutil.copy(theirs / ROOT_CERT, store / f'{name}.0')
    monkeypatch.setenv('SSL_CERT_DIR', str(store))
    assert verify_chain(ours / ROOT_CERT, ours / SIGNING_CERT)
    assert not verify_chain(ours / ROOT_CERT, theirs / SIGNING_CERT)


def test_a_new_signing_certificate_is_for_code_signing(tmp_path):
    init_pki(tmp_path)
    text = subprocess.run(
        [
            'openssl',
            'x509',
            '-in',
            str(tmp_path / SIGNING_CERT),
            '-noout',
            '-ext',
            'extendedKeyUsage',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert 'Code Signing' in text
