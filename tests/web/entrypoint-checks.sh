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

expect_exit() { # expect_exit CODE MESSAGE [docker run args...]: settings from the environment only
    local code="$1" message="$2"
    shift 2
    local output rc
    output="$(docker run --rm "$@" "${image}" 2>&1)"
    rc=$?
    report "${code}" "${message}" "${rc}" "${output}"
}

# expect_exit_with_web_env CODE MESSAGE WEB_ENV [docker run args...]: WEB_ENV is written to
# /run/uc-web/web.env first, as the web-config service writes it.
expect_exit_with_web_env() {
    local code="$1" message="$2" web_env="$3"
    shift 3
    local output rc
    # shellcheck disable=SC2016 # expanded by the bash inside the container
    output="$(docker run --rm --tmpfs /run/uc-web:uid=33,gid=33 -e "TEST_WEB_ENV=${web_env}" --entrypoint bash "$@" "${image}" \
        -c 'printf "%s" "${TEST_WEB_ENV}" > /run/uc-web/web.env && exec /usr/local/bin/entrypoint.sh' 2>&1)"
    rc=$?
    report "${code}" "${message}" "${rc}" "${output}"
}

expect_exit 64 "PUBLIC_URL must be set"
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory
expect_exit 64 "needs username:password" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope

# web.env is read; names web does not read and empty values are ignored; the environment wins.
s3_env=$'PUBLIC_URL=http://uc\nMODE=bogus\nTLS_CERT_FILE=\nDOWNLOAD_BACKEND=s3\n'
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND must be mirror or artifactory (got 's3')" "${s3_env}"
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" "${s3_env}" -e DOWNLOAD_BACKEND=artifactory
expect_exit_with_web_env 64 "needs username:password" $'PUBLIC_URL=http://uc\nDOWNLOAD_ACCESS=proxy\nDOWNLOAD_PROXY_AUTH_FILE=/nope\n'

name="uc-web-wait-$$"
docker run -d --name "${name}" -e PUBLIC_URL=http://uc "${image}" > /dev/null
sleep 3
if docker logs "${name}" 2>&1 | grep -q "waiting for the first published release"; then
    echo "ok: waits for the first release"
else
    echo "FAIL: web did not report that it is waiting for the first release"
    failures=$((failures + 1))
fi
docker rm -f "${name}" > /dev/null
exit "${failures}"
