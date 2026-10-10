#!/usr/bin/env bash
# Check web's settings, which Compose passes from .env (compose.yaml), wait for the first release,
# then run httpd (docs/architecture/request-routing.md#where-web-gets-its-settings). Compose passes
# an unset variable as empty, so empty means unset throughout.
set -euo pipefail

die() {
    echo "entrypoint: $*" >&2
    exit 64
}

[[ -n "${PUBLIC_URL:-}" ]] || die "PUBLIC_URL must be set"
export PUBLIC_URL="${PUBLIC_URL%/}"
# The mode's default backend, the rule uc's settings registry has (uc/uc/settings.py: DOWNLOAD_BACKEND).
if [[ -z "${DOWNLOAD_BACKEND:-}" ]]; then
    if [[ "${MODE:-}" == airgapped ]]; then DOWNLOAD_BACKEND=artifactory; else DOWNLOAD_BACKEND=mirror; fi
fi
DOWNLOAD_ACCESS="${DOWNLOAD_ACCESS:-redirect}"
defines=()

case "${DOWNLOAD_BACKEND}" in
    mirror)
        backend_url="${DOWNLOAD_BACKEND_URL:-https://get.jenkins.io}"
        defines+=(-D BACKEND_MIRROR)
        ;;
    artifactory)
        backend_url="${DOWNLOAD_BACKEND_URL:-}"
        [[ -n "${backend_url}" ]] || die "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL"
        defines+=(-D BACKEND_ARTIFACTORY)
        ;;
    *) die "DOWNLOAD_BACKEND must be mirror or artifactory (got '${DOWNLOAD_BACKEND}')" ;;
esac
# Any @ is refused, as uc refuses it (uc/uc/config.py): a URL parser ends the user info at a /, ? or
# #, so a token holding one would pass for the host.
[[ "${backend_url}" != *@* ]] || die "DOWNLOAD_BACKEND_URL must not contain credentials or an @; for proxied downloads put them in secrets/download_proxy_auth"
export DOWNLOAD_BACKEND_URL="${backend_url%/}"

case "${DOWNLOAD_ACCESS}" in
    redirect) ;;
    proxy)
        auth_file=/run/secrets/download_proxy_auth
        credentials="$(tr -d '\r\n' < "${auth_file}" 2> /dev/null || true)"
        [[ "${credentials}" == *:* ]] || die "DOWNLOAD_ACCESS=proxy needs username:password in ${auth_file}"
        DOWNLOAD_PROXY_AUTH_HEADER="Basic $(printf '%s' "${credentials}" | base64 -w0)"
        export DOWNLOAD_PROXY_AUTH_HEADER
        export DOWNLOAD_PROXY_CA_FILE="${DOWNLOAD_PROXY_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
        defines+=(-D ACCESS_PROXY -D NEED_SSL)
        ;;
    *) die "DOWNLOAD_ACCESS must be redirect or proxy (got '${DOWNLOAD_ACCESS}')" ;;
esac

if [[ -n "${TLS_CERT_FILE:-}" || -n "${TLS_KEY_FILE:-}" ]]; then
    [[ -r "${TLS_CERT_FILE:-}" && -r "${TLS_KEY_FILE:-}" ]] || die "TLS_CERT_FILE and TLS_KEY_FILE must both be readable"
    defines+=(-D TLS -D NEED_SSL)
fi

echo "entrypoint: serving ${PUBLIC_URL}; downloads: ${DOWNLOAD_BACKEND} ${DOWNLOAD_BACKEND_URL} (${DOWNLOAD_ACCESS})" >&2

# Apache refuses to start without DocumentRoot and the RewriteMap file.
waited=0
until [[ -e /data/live/.htdownload-map ]]; do
    if ((waited % 60 == 0)); then
        echo "entrypoint: waiting for the first published release in /data/live" >&2
    fi
    sleep 5
    waited=$((waited + 5))
done

exec httpd -DFOREGROUND "${defines[@]}"
