"""Signing PKI (docs/architecture/signing.md): an offline root CA and a short-lived signing certificate, via openssl."""

from __future__ import annotations

import base64
import datetime as dt
import secrets
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import UcError

ROOT_KEY = 'root-ca.key'
ROOT_CERT = 'root-ca.crt'
SIGNING_KEY = 'update-center.key'
SIGNING_CERT = 'update-center.cert'
DEFAULT_ORG = 'Jenkins Update Center'

_EXTENSIONS = """\
[req]
distinguished_name = dn
[dn]
[v3_root]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[v3_signing]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


class PkiError(UcError):
    """An openssl step failed, or its input or output was unusable."""


@dataclass(frozen=True)
class CertInfo:
    """A certificate's subject (RFC 2253), expiry and SHA-256 fingerprint."""

    subject: str
    not_after: dt.datetime
    fingerprint_sha256: str

    def days_left(self, now: dt.datetime) -> int:
        """Return the whole days from now until the certificate expires (negative once expired)."""
        return (self.not_after - now).days


def _openssl(*args: str) -> str:
    proc = subprocess.run(['openssl', *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        msg = f'openssl {args[0]} failed: {proc.stderr.strip()}'
        raise PkiError(msg)
    return proc.stdout


def cert_info(cert: Path) -> CertInfo:
    """Return the subject, expiry and fingerprint of the PEM certificate at cert."""
    out = _openssl(
        'x509',
        '-in',
        str(cert),
        '-noout',
        '-subject',
        '-enddate',
        '-fingerprint',
        '-sha256',
        '-nameopt',
        'RFC2253',
        '-dateopt',
        'iso_8601',
    )
    fields: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            fields[key.strip()] = value.strip()
    try:
        not_after = dt.datetime.strptime(fields['notAfter'], '%Y-%m-%d %H:%M:%SZ').replace(tzinfo=dt.UTC)
        return CertInfo(fields['subject'], not_after, fields['sha256 Fingerprint'])
    except (KeyError, ValueError) as exc:
        msg = f'could not parse certificate {cert}: {out!r}'
        raise PkiError(msg) from exc


def key_matches_cert(key: Path, cert: Path) -> bool:
    """Return True when the private key and the certificate share the same public key."""
    return _openssl('pkey', '-in', str(key), '-pubout') == _openssl('x509', '-in', str(cert), '-noout', '-pubkey')


def verify_chain(root: Path, cert: Path) -> bool:
    """Return True when openssl verifies cert against the root CA certificate."""
    proc = subprocess.run(['openssl', 'verify', '-CAfile', str(root), str(cert)], capture_output=True, text=True, check=False)
    return proc.returncode == 0


def cert_der_b64(cert: Path) -> str:
    """Base64 DER, as update-center2 embeds the signer in signature.certificates[0]."""
    der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii'))
    return base64.b64encode(der).decode('ascii')


def _check_org(org: str) -> None:
    if not org or '/' in org or '=' in org:
        msg = f"org must be non-empty and contain no '/' or '=' (got {org!r})"
        raise PkiError(msg)


def _refuse_existing(*paths: Path) -> None:
    existing = [str(p) for p in paths if p.exists()]
    if existing:
        msg = f'refusing to overwrite existing files: {", ".join(existing)}'
        raise PkiError(msg)


def _issue_signing(root_key: Path, root_cert: Path, out_dir: Path, *, days: int, org: str, cnf: Path) -> None:
    key, cert = out_dir / SIGNING_KEY, out_dir / SIGNING_CERT
    csr = cnf.parent / 'signing.csr'
    _openssl(
        'req',
        '-new',
        '-config',
        str(cnf),
        '-newkey',
        'rsa:4096',
        '-nodes',
        '-sha256',
        '-subj',
        f'/O={org}/CN={org} Signing',
        '-keyout',
        str(key),
        '-out',
        str(csr),
    )
    key.chmod(0o600)
    _openssl(
        'x509',
        '-req',
        '-in',
        str(csr),
        '-CA',
        str(root_cert),
        '-CAkey',
        str(root_key),
        '-set_serial',
        '0x' + secrets.token_hex(16),
        '-days',
        str(days),
        '-sha256',
        '-extfile',
        str(cnf),
        '-extensions',
        'v3_signing',
        '-out',
        str(cert),
    )


def init_pki(out_dir: Path, *, root_days: int = 3650, signing_days: int = 365, org: str = DEFAULT_ORG) -> None:
    """Create root/root-ca.key, root-ca.crt, update-center.key and update-center.cert in out_dir."""
    _check_org(org)
    root_dir = out_dir / 'root'
    root_key, root_cert = root_dir / ROOT_KEY, out_dir / ROOT_CERT
    _refuse_existing(root_key, root_cert, out_dir / SIGNING_KEY, out_dir / SIGNING_CERT)
    root_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / 'ext.cnf'
        cnf.write_text(_EXTENSIONS)
        _openssl(
            'req',
            '-x509',
            '-new',
            '-config',
            str(cnf),
            '-extensions',
            'v3_root',
            '-newkey',
            'rsa:4096',
            '-nodes',
            '-sha256',
            '-days',
            str(root_days),
            '-subj',
            f'/O={org}/CN={org} Root CA',
            '-keyout',
            str(root_key),
            '-out',
            str(root_cert),
        )
        root_key.chmod(0o600)
        _issue_signing(root_key, root_cert, out_dir, days=signing_days, org=org, cnf=cnf)


def rotate_signing(root_key: Path, root_cert: Path, out_dir: Path, *, days: int = 365, org: str = DEFAULT_ORG) -> None:
    """Issue a fresh signing key and certificate; the previous pair is kept as *.old."""
    _check_org(org)
    for name in (SIGNING_KEY, SIGNING_CERT):
        current = out_dir / name
        if current.exists():
            current.replace(out_dir / f'{name}.old')
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / 'ext.cnf'
        cnf.write_text(_EXTENSIONS)
        _issue_signing(root_key, root_cert, out_dir, days=days, org=org, cnf=cnf)
