"""HTTP for uc: GETs of upstream data, and every request to the repository.

Credentials never follow a redirect to another host, or from https to http
(docs/architecture/repo-adapter.md#security). urllib follows 301/302/303/307/308.
"""

from __future__ import annotations

import hashlib
import http.client
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO
from urllib.parse import urlsplit

from .errors import UcError
from .version import uc_version

if TYPE_CHECKING:
    from pathlib import Path

USER_AGENT = f'easy-update-center/{uc_version()} (+https://github.com/HaleTechCo/easy-update-center)'
Getter = Callable[..., bytes]
TIMEOUT = 60
LONG_TIMEOUT = 600


class HttpError(UcError):
    """A request failed.

    A connection error, a timeout, an invalid URL, an HTTP error status (get) or a body
    over its limit.
    """


@dataclass
class Response:
    """An HTTP response.

    Its status, its headers with lower-cased names, and its body.
    """

    status: int
    headers: dict[str, str]
    body: bytes


_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or _DEFAULT_PORTS.get(parts.scheme)


def forwards_credentials(old_url: str, new_url: str) -> bool:
    """Return whether a redirect from old_url to new_url may carry the credentials.

    Only within one origin (scheme, host and port), or from http to https on the same
    host and the default ports; never from https to http, where they would cross the
    network in the clear.
    """
    old, new = _origin(old_url), _origin(new_url)
    if old == new:
        return True
    return (
        (old[0], new[0]) == ('http', 'https')
        and old[1] == new[1]
        and (old[2], new[2]) == (80, 443)
    )


class _NoCredentialsAcrossHosts(urllib.request.HTTPRedirectHandler):
    """Artifactory redirects downloads to object storage.

    Repository credentials must not follow.
    """

    # urllib.request.HTTPRedirectHandler's signature
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0917
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not forwards_credentials(req.full_url, newurl):
            new.remove_header('Authorization')
        return new


_GET_OPENER = urllib.request.build_opener(_NoCredentialsAcrossHosts())


def get(
    url: str, headers: Mapping[str, str] | None = None, timeout: float = TIMEOUT
) -> bytes:
    """Return the body of url, sent with the uc User-Agent and any extra headers.

    An Authorization header does not follow a redirect to another host.
    """
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT, **(headers or {})}
    )
    try:
        with _GET_OPENER.open(request, timeout=timeout) as response:
            return response.read()
    # URLError and HTTPError are OSError subclasses; HTTPException is a non-HTTP
    # answer or a body cut short
    except (OSError, ValueError, http.client.HTTPException) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()  # it holds the response
        msg = f'GET {url} failed: {exc}'
        raise HttpError(msg) from exc


class Http:
    """HTTP to the repository.

    Its credentials never follow a redirect to another host.
    """

    def __init__(self, ca_file: Path | None) -> None:
        """Verify TLS against the system bundle, plus ca_file when given."""
        # REPO_CA_FILE adds to the system bundle: a repository behind a private CA may
        # still redirect downloads to object storage with a public certificate.
        self.context = ssl.create_default_context()
        if ca_file:
            try:
                self.context.load_verify_locations(cafile=str(ca_file))
            except (OSError, ssl.SSLError) as exc:
                msg = f'REPO_CA_FILE {ca_file} cannot be read: {exc}'
                raise HttpError(msg) from exc
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.context),
            _NoCredentialsAcrossHosts(),
        )

    def request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        limit: int,
        data: bytes | None = None,
        method: str | None = None,
        timeout: float = TIMEOUT,
        truncate: bool = False,
    ) -> Response:
        """One request; the body is read up to limit bytes.

        HttpError beyond, unless truncate.
        """
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={'User-Agent': USER_AGENT, **headers},
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(limit + 1)
                status, got = response.status, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status, got = exc.code, dict(exc.headers.items())
            try:
                body = exc.read(4096)
            except (OSError, http.client.HTTPException):
                body = b''
            finally:
                exc.close()  # it holds the response
        except (OSError, ValueError, http.client.HTTPException) as exc:
            msg = f'{method or "GET"} {url} failed: {exc!r}'
            raise HttpError(msg) from exc
        if len(body) > limit and not truncate:
            msg = f'{url} is larger than {limit} bytes'
            raise HttpError(msg)
        return Response(
            status,
            {k.lower(): v for k, v in got.items()},
            body[:limit] if truncate else body,
        )

    def download(
        self,
        url: str,
        *,
        headers: dict[str, str],
        out: BinaryIO,
        limit: int,
        sha256: str | None = None,
    ) -> None:
        """Stream url's body into out.

        HttpError on an HTTP error, beyond limit bytes, or when the body's sha256 is not
        sha256.
        """
        request = urllib.request.Request(
            url, headers={'User-Agent': USER_AGENT, **headers}
        )
        digest = hashlib.sha256()
        try:
            with self.opener.open(request, timeout=LONG_TIMEOUT) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        msg = f'{url} is larger than {limit} bytes'
                        raise HttpError(msg)
                    digest.update(chunk)
                    out.write(chunk)
        except urllib.error.HTTPError as exc:
            exc.close()  # it holds the response
            msg = f'GET {url} failed with HTTP {exc.code}'
            raise HttpError(msg) from exc
        except (OSError, ValueError, http.client.HTTPException) as exc:
            msg = f'GET {url} failed: {exc!r}'
            raise HttpError(msg) from exc
        if sha256 is not None and digest.hexdigest() != sha256:
            msg = f'{url} does not match its listed sha256'
            raise HttpError(msg)
