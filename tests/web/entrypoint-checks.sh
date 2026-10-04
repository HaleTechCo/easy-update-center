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

# expect_serving DOWNLOAD_BACKEND_URL: the entrypoint accepts the URL and goes on to wait for the
# first release, where timeout stops it (exit 124).
expect_serving() {
    local url="$1" output rc
    output="$(docker run --rm -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=${url}" \
        --entrypoint timeout "${image}" 2 /usr/local/bin/entrypoint.sh 2>&1)"
    rc=$?
    report 124 "entrypoint: serving http://uc; downloads: artifactory ${url} (redirect)" "${rc}" "${output}"
}

expect_exit 64 "PUBLIC_URL must be set"
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory
expect_exit 64 "needs username:password" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope

expect_exit 64 "DOWNLOAD_BACKEND_URL must not contain credentials" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=https://u:pw@art/repo # pragma: allowlist secret
expect_exit 64 "DOWNLOAD_BACKEND_URL must not contain credentials" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=http://u@backend.example
expect_exit 64 "DOWNLOAD_BACKEND_URL must not contain credentials" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=http://u:p@backend.example?x=1" # pragma: allowlist secret
# Only the authority holds credentials: an @ in the query or fragment of a URL with no path is not one.
expect_serving "http://backend.example?x=a@b"
expect_serving "http://backend.example#a@b"

# web.env is read; empty values are ignored; the environment wins.
s3_env=$'PUBLIC_URL=http://uc\nTLS_CERT_FILE=\nDOWNLOAD_BACKEND=s3\n'
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND_URL must not contain credentials" $'PUBLIC_URL=http://uc\nDOWNLOAD_BACKEND=artifactory\nDOWNLOAD_BACKEND_URL=https://u:pw@art/repo\n' # pragma: allowlist secret
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND must be mirror or artifactory (got 's3')" "${s3_env}"
expect_exit_with_web_env 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" "${s3_env}" -e DOWNLOAD_BACKEND=artifactory
expect_exit_with_web_env 64 "needs username:password" $'PUBLIC_URL=http://uc\nDOWNLOAD_ACCESS=proxy\nDOWNLOAD_PROXY_AUTH_FILE=/nope\n'

name="uc-web-wait-$$"
docker run -d --name "${name}" -e PUBLIC_URL=http://uc "${image}" > /dev/null
# shellcheck disable=SC2016 # expanded by the bash inside the container
docker run -d --name "${name}-env" --tmpfs /run/uc-web:uid=33,gid=33 --entrypoint bash "${image}" \
    -c 'printf "%s\n" PUBLIC_URL=http://uc DOWNLOAD_BACKEND=artifactory "DOWNLOAD_BACKEND_URL=http://backend/repo?a=b=c" > /run/uc-web/web.env && exec /usr/local/bin/entrypoint.sh' > /dev/null
sleep 3
if docker logs "${name}" 2>&1 | grep -q "waiting for the first published release"; then
    echo "ok: waits for the first release"
else
    echo "FAIL: web did not report that it is waiting for the first release"
    failures=$((failures + 1))
fi
env_logs="$(docker logs "${name}-env" 2>&1)"
if [[ "${env_logs}" == *"entrypoint: serving http://uc; downloads: artifactory http://backend/repo?a=b=c (redirect)"* ]]; then
    echo "ok: logs what it serves, with the backend URL intact"
else
    echo "FAIL: web did not log what it serves: ${env_logs}"
    failures=$((failures + 1))
fi
docker rm -f "${name}" "${name}-env" > /dev/null
exit "${failures}"
