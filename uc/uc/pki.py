"""Signing PKI (docs/architecture/signing.md): an offline root CA and a short-lived signing certificate, via openssl."""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import secrets
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import UcError
from .settings import REGISTRY

ROOT_KEY = 'root-ca.key'
ROOT_CERT = 'root-ca.crt'
SIGNING_KEY = 'update-center.key'
SIGNING_CERT = 'update-center.cert'
DEFAULT_ORG = REGISTRY['PKI_ORG'].default
_NO_PASSPHRASE = ('-passin', 'pass:')  # every openssl call that reads the root key: an encrypted key fails instead of asking
_ENCRYPTED_PEM = (b' ENCRYPTED PRIVATE KEY-----', b'Proc-Type: 4,ENCRYPTED')  # the PEM label of an encrypted PKCS#8 key, and the traditional format's header

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


def _check_root(root_key: Path, root_cert: Path, shown_key: Path, shown_cert: Path) -> None:
    """Refuse a root CA key or certificate that is missing, unreadable or not what openssl can use, an encrypted key, and a key that is not the certificate's; messages name them as shown_key and shown_cert."""
    contents = {}
    for path, shown, what in ((root_key, shown_key, 'root CA key'), (root_cert, shown_cert, 'root CA certificate')):
        try:
            contents[path] = path.read_bytes()
        except OSError as exc:
            msg = f'cannot read the {what} {shown}: {exc.strerror or type(exc).__name__}'
            raise PkiError(msg) from None
    if any(header in contents[root_key] for header in _ENCRYPTED_PEM):
        msg = f'the root CA key {shown_key} is encrypted; decrypt it to a temporary file for the rotation (docs/how-to/rotate-signing-certificate.md)'
        raise PkiError(msg)
    try:
        public_key = _openssl('pkey', '-in', str(root_key), *_NO_PASSPHRASE, '-pubout')
    except PkiError:
        msg = f'the root CA key {shown_key} is not a private key openssl can read'
        raise PkiError(msg) from None
    try:
        cert_key = _openssl('x509', '-in', str(root_cert), '-noout', '-pubkey')
    except PkiError:
        msg = f'the root CA certificate {shown_cert} is not a certificate openssl can read'
        raise PkiError(msg) from None
    if public_key != cert_key:
        msg = f'the root CA key {shown_key} is not the key of the root CA certificate {shown_cert}'
        raise PkiError(msg)


def _refuse_existing(out_dir: Path, display_dir: Path, *names: Path | str) -> None:
    existing = [str(display_dir / name) for name in names if (out_dir / name).exists()]
    if existing:
        msg = f'refusing to overwrite existing files: {", ".join(existing)}'
        raise PkiError(msg)


def _cannot(action: str, shown: Path, exc: OSError) -> str:
    return f'cannot {action} {shown}: {exc.strerror or type(exc).__name__}'


def _workdir(out_dir: Path, shown: Path, prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Return a new temporary directory inside out_dir, on its filesystem, so the files made there can be renamed into place."""
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, dir=out_dir)
    except OSError as exc:
        msg = _cannot('write in', shown, exc)
        raise PkiError(msg) from None


def _move_all(moves: list[tuple[Path, Path]], shown: Path) -> None:
    """Rename each source to its target, in order, onto targets that do not exist.

    When a rename fails, or anything else interrupts them, the renames already done are undone in reverse, so the
    directories are left as they were; a failed rename is reported as a PkiError naming shown.
    """
    done: list[tuple[Path, Path]] = []

    def undo() -> None:
        for source, target in reversed(done):
            target.rename(source)

    try:
        for source, target in moves:
            source.rename(target)
            done.append((source, target))
    except OSError as exc:
        undo()
        msg = _cannot('write in', shown, exc)
        raise PkiError(msg) from None
    except BaseException:
        undo()
        raise


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
        *_NO_PASSPHRASE,
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


def init_pki(out_dir: Path, *, root_days: int = 3650, signing_days: int = 365, org: str = DEFAULT_ORG, display_dir: Path | None = None) -> None:
    """Create root/root-ca.key, root-ca.crt, update-center.key and update-center.cert in out_dir.

    They are created in a temporary directory inside out_dir and moved in only once all four exist; when anything fails,
    the files already moved are moved back, so out_dir is left as it was (and removed again when this created it).
    display_dir, when given, is where out_dir is on the host: messages name the files under it.
    """
    _check_org(org)
    shown = display_dir or out_dir
    _refuse_existing(out_dir, shown, Path('root', ROOT_KEY), ROOT_CERT, SIGNING_KEY, SIGNING_CERT)
    created = [path for path in (out_dir, *out_dir.parents) if not path.exists()]  # deepest first
    try:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            msg = _cannot('create', shown, exc)
            raise PkiError(msg) from None
        with _workdir(out_dir, shown, '.init-') as tmp:
            _create_pki(Path(tmp), out_dir, shown, root_days=root_days, signing_days=signing_days, org=org)
    except BaseException:
        for path in created:
            with contextlib.suppress(OSError):
                path.rmdir()
        raise


def _create_pki(work: Path, out_dir: Path, shown: Path, *, root_days: int, signing_days: int, org: str) -> None:
    cnf = work / 'ext.cnf'
    cnf.write_text(_EXTENSIONS)
    (work / 'root').mkdir()
    root_key, root_cert = work / 'root' / ROOT_KEY, work / ROOT_CERT
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
    _issue_signing(root_key, root_cert, work, days=signing_days, org=org, cnf=cnf)
    root_dir = out_dir / 'root'
    root_move = (root_key, root_dir / ROOT_KEY) if root_dir.exists() else (work / 'root', root_dir)
    _move_all([root_move, *((work / name, out_dir / name) for name in (ROOT_CERT, SIGNING_KEY, SIGNING_CERT))], shown)


def rotate_signing(out_dir: Path, *, days: int = 365, org: str = DEFAULT_ORG, display_dir: Path | None = None, root_display_dir: Path | None = None) -> None:
    """Issue a fresh signing key and certificate in out_dir from its root CA (root-ca.crt, root/root-ca.key); the previous pair is kept as *.old.

    Nothing in out_dir changes until the new pair exists: the root files and the current pair are checked first, the
    new pair is issued in a temporary directory inside out_dir (the same filesystem), and only then is any older *.old
    pair set aside there (and deleted with it), the current pair renamed to *.old and the new one moved in; when a
    rename fails, those already done are undone, so out_dir is left as it was. display_dir, when given, is where out_dir
    is on the host, and root_display_dir where its root/ is (default: root/ under display_dir, else under out_dir);
    messages name the files under them.
    """
    _check_org(org)
    shown = display_dir or out_dir
    root_key, root_cert = out_dir / 'root' / ROOT_KEY, out_dir / ROOT_CERT
    _check_root(root_key, root_cert, (root_display_dir or shown / 'root') / ROOT_KEY, shown / ROOT_CERT)
    current = [name for name in (SIGNING_KEY, SIGNING_CERT) if (out_dir / name).exists()]
    if len(current) == 1:
        missing = SIGNING_CERT if current == [SIGNING_KEY] else SIGNING_KEY
        msg = f'found {shown / current[0]} without {shown / missing}: put {missing} back, or move {current[0]} away, then rotate again'
        raise PkiError(msg)
    with _workdir(out_dir, shown, '.rotate-') as tmp:
        work = Path(tmp)
        cnf = work / 'ext.cnf'
        cnf.write_text(_EXTENSIONS)
        _issue_signing(root_key, root_cert, work, days=days, org=org, cnf=cnf)
        older = [(out_dir / f'{name}.old', work / f'{name}.old') for name in current if (out_dir / f'{name}.old').exists()]
        previous = [(out_dir / name, out_dir / f'{name}.old') for name in current]
        _move_all([*older, *previous, *((work / name, out_dir / name) for name in (SIGNING_KEY, SIGNING_CERT))], shown)
