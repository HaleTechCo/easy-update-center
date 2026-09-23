#!/usr/bin/env bash
# Resolve mode defaults (they must match uc/uc/config.py), wait for the first release, run httpd.
set -euo pipefail

die() {
    echo "entrypoint: $*" >&2
    exit 64
}

[[ -n "${PUBLIC_URL:-}" ]] || die "PUBLIC_URL must be set"
export PUBLIC_URL="${PUBLIC_URL%/}"
MODE="${MODE:-}"
case "${MODE}" in
    online)
        default_backend=mirror
        default_repo_url=https://repo.jenkins-ci.org/
        default_repo_key=releases
        ;;
    airgapped)
        default_backend=artifactory
        default_repo_url=
        default_repo_key=
        ;;
    *) die "MODE must be online or airgapped (got '${MODE}')" ;;
esac
REPO_TYPE="${REPO_TYPE:-artifactory}"
case "${REPO_TYPE}" in
    artifactory) ;;
    artifact-keeper)
        default_repo_url=
        default_repo_key=
        adapter="${REPO_ADAPTER:-on}"
        case "${adapter,,}" in
            0 | false | no | off) die "REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on" ;;
            *) ;; # any other value leaves the adapter on
        esac
        ;;
    *) die "REPO_TYPE must be artifactory or artifact-keeper (got '${REPO_TYPE}')" ;;
esac

DOWNLOAD_BACKEND="${DOWNLOAD_BACKEND:-${default_backend}}"
DOWNLOAD_ACCESS="${DOWNLOAD_ACCESS:-redirect}"
defines=()

case "${DOWNLOAD_BACKEND}" in
    mirror)
        backend_url="${DOWNLOAD_BACKEND_URL:-https://get.jenkins.io}"
        defines+=(-D BACKEND_MIRROR)
        ;;
    artifactory)
        if [[ -n "${DOWNLOAD_BACKEND_URL:-}" ]]; then
            backend_url="${DOWNLOAD_BACKEND_URL}"
        else
            repo_url="${REPO_URL:-${default_repo_url}}"
            repo_key="${REPO_KEY:-${default_repo_key}}"
            [[ -n "${repo_url}" && -n "${repo_key}" ]] || die "set DOWNLOAD_BACKEND_URL, or REPO_URL and REPO_KEY"
            if [[ "${REPO_TYPE}" == artifact-keeper ]]; then
                backend_url="${repo_url%/}/maven/${repo_key}"
            else
                backend_url="${repo_url%/}/${repo_key}"
            fi
        fi
        defines+=(-D BACKEND_ARTIFACTORY)
        ;;
    *) die "DOWNLOAD_BACKEND must be mirror or artifactory (got '${DOWNLOAD_BACKEND}')" ;;
esac
export DOWNLOAD_BACKEND_URL="${backend_url%/}"

case "${DOWNLOAD_ACCESS}" in
    redirect) ;;
    proxy)
        auth_file="${DOWNLOAD_PROXY_AUTH_FILE:-/run/secrets/download_proxy_auth}"
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

# Apache refuses to start without DocumentRoot and the RewriteMap file (spec §8.1).
waited=0
until [[ -e /data/live/.htdownload-map ]]; do
    if ((waited % 60 == 0)); then
        echo "entrypoint: waiting for the first published release in /data/live" >&2
    fi
    sleep 5
    waited=$((waited + 5))
done

exec httpd -DFOREGROUND "${defines[@]}"
