#!/usr/bin/env bash
# Entrypoint checks for the web image. Usage: tests/web/entrypoint-checks.sh IMAGE
set -uo pipefail
image="$1"
failures=0

report() { # report CODE MESSAGE RC OUTPUT
    local code="$1" message="$2" rc="$3" output="$4"
    if [[ ${rc} -ne ${code} || "${output}" != *"${message}"* ]]; then
        echo "FAIL: expected exit ${code} with '${message}', got ${rc}: ${output}"
        failures=$((failures + 1))
    else
        echo "ok: ${message}"
    fi
}

# expect_exit CODE MESSAGE [docker run args...]: settings from the environment only. The entrypoint
# runs under timeout, so one that accepts the settings and waits for a release fails the check (exit
# 124) instead of hanging the script.
expect_exit() {
    local code="$1" message="$2"
    shift 2
    local output rc
    output="$(docker run --rm "$@" --entrypoint timeout "${image}" 10 /usr/local/bin/entrypoint.sh 2>&1)"
    rc=$?
    report "${code}" "${message}" "${rc}" "${output}"
}

# expect_exit_with_web_env CODE MESSAGE WEB_ENV [docker run args...]: WEB_ENV is written to
# /run/uc-web/web.env first, as the web-config service writes it; under timeout, as expect_exit.
expect_exit_with_web_env() {
    local code="$1" message="$2" web_env="$3"
    shift 3
    local output rc
    # shellcheck disable=SC2016 # expanded by the bash inside the container
    output="$(docker run --rm --tmpfs /run/uc-web:uid=33,gid=33 -e "TEST_WEB_ENV=${web_env}" --entrypoint bash "$@" "${image}" \
        -c 'printf "%s" "${TEST_WEB_ENV}" > /run/uc-web/web.env && exec timeout 10 /usr/local/bin/entrypoint.sh' 2>&1)"
    rc=$?
    report "${code}" "${message}" "${rc}" "${output}"
}

# expect_serving DOWNLOAD_BACKEND_URL: the entrypoint accepts the URL and goes on to wait for the
# first release, where timeout stops it (exit 124).
expect_serving() {
    local url="$1" output rc
    output="$(docker run --rm -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=${url}" \
        --entrypoint timeout "${image}" 2 /usr/local/bin/entrypoint.sh 2>&1)"
    rc=$?
    report 124 "entrypoint: serving http://uc; downloads: artifactory ${url} (redirect)" "${rc}" "${output}"
}

# expect_log CONTAINER TEXT MESSAGE: polls the container's logs for TEXT for up to 30 seconds; on
# timeout the check fails, showing the logs.
expect_log() {
    local container="$1" text="$2" message="$3" logs deadline=$((SECONDS + 30))
    while
        logs="$(docker logs "${container}" 2>&1)"
        [[ "${logs}" != *"${text}"* ]]
    do
        if ((SECONDS >= deadline)); then
            echo "FAIL: ${message}: no '${text}' in the logs of ${container} after 30 s: ${logs}"
            failures=$((failures + 1))
            return
        fi
        sleep 0.5
    done
    echo "ok: ${message}"
}

expect_exit 64 "PUBLIC_URL must be set"
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory
expect_exit 64 "needs username:password" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope

no_credentials="DOWNLOAD_BACKEND_URL must not contain credentials or an @; for proxied downloads put them in secrets/download_proxy_auth"
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=https://u:pw@art/repo # pragma: allowlist secret
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=http://u@backend.example
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=http://u:p@backend.example?x=1" # pragma: allowlist secret
# Any @ is refused, as uc refuses it: a token holding a # or / would otherwise pass for the host,
# and an @ in the query or fragment goes too.
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=https://tok3n#x@host.corp/"
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=https://tok/en@host.corp/"
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=http://backend.example?x=a@b"
expect_exit 64 "${no_credentials}" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=http://backend.example#a@b"
expect_serving "http://backend.example?x=a#b"
expect_serving "https://repo.corp/artifactory/jenkins"

# web.env is read; empty values are ignored; the environment wins.
s3_env=$'PUBLIC_URL=http://uc\nTLS_CERT_FILE=\nDOWNLOAD_BACKEND=s3\n'
expect_exit_with_web_env 64 "${no_credentials}" $'PUBLIC_URL=http://uc\nDOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=https://u:pw@art/repo\n' # pragma: allowlist secret
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND must be mirror or artifactory (got 's3')" "${s3_env}"
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" "${s3_env}" -e DOWNLOAD_BACKEND=artifactory
expect_exit_with_web_env 64 "needs username:password" $'PUBLIC_URL=http://uc\nDOWNLOAD_ACCESS=proxy\nDOWNLOAD_PROXY_AUTH_FILE=/nope\n'

name="uc-web-wait-$$"
# Removed on every way out of this script, a failed check or an interrupt included.
trap 'docker rm -f "${name}" "${name}-env" > /dev/null 2>&1' EXIT
docker run -d --name "${name}" -e PUBLIC_URL=http://uc "${image}" > /dev/null
# shellcheck disable=SC2016 # expanded by the bash inside the container
docker run -d --name "${name}-env" --tmpfs /run/uc-web:uid=33,gid=33 --entrypoint bash "${image}" \
    -c 'printf "%s\n" PUBLIC_URL=http://uc DOWNLOAD_BACKEND=artifactory "DOWNLOAD_BACKEND_URL=http://backend/repo?a=b=c" > /run/uc-web/web.env && exec /usr/local/bin/entrypoint.sh' > /dev/null
expect_log "${name}" "waiting for the first published release" "waits for the first release"
expect_log "${name}-env" "entrypoint: serving http://uc; downloads: artifactory http://backend/repo?a=b=c (redirect)" "logs what it serves, with the backend URL intact"
exit "${failures}"
