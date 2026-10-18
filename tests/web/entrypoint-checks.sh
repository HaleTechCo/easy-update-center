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

# expect_serves MESSAGE [docker run args...]: the entrypoint accepts the settings and goes on to wait
# for the first release, where timeout stops it (exit 124).
expect_serves() {
    local message="$1"
    shift
    local output rc
    output="$(docker run --rm "$@" --entrypoint timeout "${image}" 2 /usr/local/bin/entrypoint.sh 2>&1)"
    rc=$?
    report 124 "${message}" "${rc}" "${output}"
}

expect_serving() { # expect_serving DOWNLOAD_BACKEND_URL
    expect_serves "entrypoint: serving http://uc; downloads: artifactory $1 (redirect)" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory -e "DOWNLOAD_BACKEND_URL=$1"
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
expect_exit 64 "web must not run as root" --user 0:0 -e PUBLIC_URL=http://uc
expect_exit 64 "MODE must be online or airgapped (got 'airgaped')" -e PUBLIC_URL=http://uc -e MODE=airgaped
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "DOWNLOAD_ACCESS must be redirect or proxy (got 'proxy2')" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy2
expect_exit 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=artifactory
expect_exit 64 "DOWNLOAD_ACCESS=proxy needs DOWNLOAD_BACKEND_URL set explicitly" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "needs username:password" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=https://repo.corp/jenkins
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope
expect_exit 64 "DOWNLOAD_PROXY_CA_FILE /nope cannot be read" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=https://repo.corp/jenkins -e DOWNLOAD_PROXY_CA_FILE=/nope
expect_exit 64 "DOWNLOAD_PROXY_CA_FILE /etc/ssl/certs cannot be read" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=https://repo.corp/jenkins -e DOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e PUBLIC_URL=https://uc -e TLS_CERT_FILE=/etc/ssl/certs -e TLS_KEY_FILE=/etc/ssl/certs/ca-certificates.crt
expect_exit 64 "PUBLIC_URL must start with https://" -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/etc/ssl/certs/ca-certificates.crt -e TLS_KEY_FILE=/etc/ssl/certs/ca-certificates.crt

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
# Every trailing slash goes, as uc strips them.
expect_serves "entrypoint: serving https://h; downloads: artifactory http://b/r (redirect)" -e PUBLIC_URL=https://h// -e DOWNLOAD_BACKEND=artifactory -e DOWNLOAD_BACKEND_URL=http://b/r//

# Compose passes an unset variable as empty: MODE then chooses the backend, as uc does; an explicit
# DOWNLOAD_BACKEND wins over MODE.
mirror="downloads: mirror https://get.jenkins.io (redirect)"
expect_serves "${mirror}" -e PUBLIC_URL=http://uc -e MODE=online -e DOWNLOAD_BACKEND=
expect_serves "${mirror}" -e PUBLIC_URL=http://uc -e MODE= -e DOWNLOAD_BACKEND=
expect_serves "${mirror}" -e PUBLIC_URL=http://uc
expect_serves "downloads: artifactory http://backend/repo (redirect)" -e PUBLIC_URL=http://uc -e MODE=airgapped -e DOWNLOAD_BACKEND= -e DOWNLOAD_BACKEND_URL=http://backend/repo
expect_exit 64 "DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL" -e PUBLIC_URL=http://uc -e MODE=airgapped -e DOWNLOAD_BACKEND= -e DOWNLOAD_BACKEND_URL=
expect_serves "${mirror}" -e PUBLIC_URL=http://uc -e MODE=airgapped -e DOWNLOAD_BACKEND=mirror

# The image deletes /data/live after its syntax check, and httpd -M fails without DocumentRoot.
modules="$(docker run --rm -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND_URL=https://get.jenkins.io --entrypoint sh "${image}" -c 'mkdir -p /data/live && httpd -M -D BACKEND_MIRROR' 2>&1)"
if [[ "${modules}" == *reqtimeout_module* ]]; then
    echo "ok: mod_reqtimeout is loaded"
else
    echo "FAIL: mod_reqtimeout is not loaded: ${modules}"
    failures=$((failures + 1))
fi

# expect_health HEALTHY MESSAGE CONTAINER: runs the image's health check in CONTAINER; HEALTHY is yes
# when it must pass, no when it must fail.
expect_health() {
    local healthy="$1" message="$2" container="$3" output rc
    output="$(docker exec "${container}" /usr/local/bin/healthcheck.sh 2>&1)"
    rc=$?
    if [[ (${healthy} == yes && ${rc} -eq 0) || (${healthy} == no && ${rc} -ne 0) ]]; then
        echo "ok: ${message}"
    else
        echo "FAIL: ${message}: the health check exited ${rc}: ${output}"
        failures=$((failures + 1))
    fi
}

# A live release for the health check: the file it looks for, and uctest.json to fetch.
live='mkdir -p /data/live && touch /data/live/.htdownload-map && printf "{}\n" > /data/live/uctest.json'

name="uc-web-wait-$$"
# Removed on every way out of this script, a failed check or an interrupt included.
trap 'docker rm -f "${name}" "${name}-proxy" "${name}-live" "${name}-tls" > /dev/null 2>&1' EXIT
docker run -d --name "${name}-live" -e PUBLIC_URL=http://uc --entrypoint bash "${image}" \
    -c "${live} && exec /usr/local/bin/entrypoint.sh" > /dev/null
docker run -d --name "${name}-tls" -e PUBLIC_URL=https://uc -e TLS_CERT_FILE=/tmp/tls/c.pem -e TLS_KEY_FILE=/tmp/tls/k.pem \
    --entrypoint bash "${image}" -c "${live} && mkdir -p /tmp/tls && openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj /CN=uc -keyout /tmp/tls/k.pem -out /tmp/tls/c.pem 2> /dev/null && exec /usr/local/bin/entrypoint.sh" > /dev/null
expect_log "${name}-live" "resuming normal operations" "serves a live release"
expect_health yes "the health check passes while the site answers" "${name}-live"
docker exec "${name}-live" rm /data/live/uctest.json
expect_health no "the health check fails when the site does not answer 200" "${name}-live"
expect_log "${name}-tls" "resuming normal operations" "serves a live release with TLS"
expect_health yes "the health check passes with TLS on, over 8443" "${name}-tls"
docker run -d --name "${name}" -e PUBLIC_URL=http://uc "${image}" > /dev/null
expect_log "${name}" "waiting for the first published release" "waits for the first release"
expect_health no "the health check fails while no release is live" "${name}"
docker run -d --name "${name}-proxy" -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy -e DOWNLOAD_BACKEND=artifactory \
    -e DOWNLOAD_BACKEND_URL=http://repo.corp/jenkins --entrypoint sh "${image}" \
    -c 'mkdir -p /tmp/s && printf u:p > /tmp/s/a && sed "s#/run/secrets/download_proxy_auth#/tmp/s/a#" /usr/local/bin/entrypoint.sh > /tmp/e.sh && exec bash /tmp/e.sh' > /dev/null
expect_log "${name}-proxy" "warning: DOWNLOAD_BACKEND_URL uses plain http" "warns that proxy credentials would cross in the clear"
# Guards that web stops promptly while waiting for its first release: a bash that is PID 1 ignores
# TERM without a trap, so this takes the whole -t 10 (Docker's default stop timeout) unless the entrypoint handles it.
start=${SECONDS}
docker stop -t 10 "${name}" > /dev/null
if ((SECONDS - start <= 5)); then
    echo "ok: stops within 5 seconds while waiting for the first release"
else
    echo "FAIL: docker stop took $((SECONDS - start)) s while waiting for the first release"
    failures=$((failures + 1))
fi
exit "${failures}"
