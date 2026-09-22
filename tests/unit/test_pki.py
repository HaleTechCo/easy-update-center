import base64
import datetime as dt
import shutil
import subprocess

import pytest

from uc.pki import (
    PkiError,
    cert_der_b64,
    cert_info,
    init_pki,
    key_matches_cert,
    rotate_signing,
    verify_chain,
)


def test_init_creates_a_valid_chain(pki_material):
    assert verify_chain(pki_material / "root-ca.crt", pki_material / "update-center.cert")
    assert key_matches_cert(pki_material / "update-center.key", pki_material / "update-center.cert")
    assert (pki_material / "root" / "root-ca.key").is_file()
    assert not (pki_material / "root-ca.key").exists()
    assert (pki_material / "update-center.key").stat().st_mode & 0o777 == 0o600
    assert (pki_material / "root" / "root-ca.key").stat().st_mode & 0o777 == 0o600


def test_cert_info(pki_material):
    info = cert_info(pki_material / "update-center.cert")
    assert "CN=Jenkins Update Center Signing" in info.subject
    assert 43 <= info.days_left(dt.datetime.now(dt.timezone.utc)) <= 45
    assert len(info.fingerprint_sha256.split(":")) == 32


def test_signing_cert_is_not_a_ca_and_root_is(pki_material):
    def text(name):
        return subprocess.run(
            ["openssl", "x509", "-in", str(pki_material / name), "-noout", "-text"],
            capture_output=True, text=True, check=True,
        ).stdout

    assert "CA:FALSE" in text("update-center.cert")
    assert "Digital Signature" in text("update-center.cert")
    assert "CA:TRUE, pathlen:0" in text("root-ca.crt")


def test_init_refuses_to_overwrite(pki_material):
    with pytest.raises(PkiError, match="refusing to overwrite"):
        init_pki(pki_material)


def test_org_with_slash_is_rejected(tmp_path):
    with pytest.raises(PkiError, match="org"):
        init_pki(tmp_path / "x", org="a/b")


def test_rotate_issues_a_new_cert_under_the_same_root(pki_material, tmp_path):
    work = tmp_path / "pki"
    shutil.copytree(pki_material, work)
    before = cert_info(work / "update-center.cert").fingerprint_sha256
    rotate_signing(work / "root" / "root-ca.key", work / "root-ca.crt", work, days=5)
    assert cert_info(work / "update-center.cert").fingerprint_sha256 != before
    assert verify_chain(work / "root-ca.crt", work / "update-center.cert")
    assert key_matches_cert(work / "update-center.key", work / "update-center.cert")
    assert (work / "update-center.cert.old").is_file()
    assert (work / "update-center.key.old").is_file()


def test_cert_der_b64_matches_openssl(pki_material):
    der = subprocess.run(
        ["openssl", "x509", "-in", str(pki_material / "update-center.cert"), "-outform", "DER"],
        capture_output=True, check=True,
    ).stdout
    assert cert_der_b64(pki_material / "update-center.cert") == base64.b64encode(der).decode()


def test_key_mismatch_is_detected(pki_material, tmp_path):
    other = tmp_path / "other.key"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(other)],
        check=True, capture_output=True,
    )
    assert not key_matches_cert(other, pki_material / "update-center.cert")


def test_cert_info_on_garbage_raises(tmp_path):
    bad = tmp_path / "bad.crt"
    bad.write_text("not a certificate")
    with pytest.raises(PkiError):
        cert_info(bad)
