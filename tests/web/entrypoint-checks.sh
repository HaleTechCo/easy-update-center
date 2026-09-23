#!/usr/bin/env bash
# Entrypoint checks for the web image. Usage: tests/web/entrypoint-checks.sh IMAGE
set -uo pipefail
image="$1"
failures=0

expect_exit() {  # expect_exit CODE MESSAGE [docker run args...]
  local code="$1" message="$2"
  shift 2
  local output rc
  output="$(docker run --rm "$@" "${image}" 2>&1)"
  rc=$?
  if [[ ${rc} -ne ${code} || "${output}" != *"${message}"* ]]; then
    echo "FAIL: expected exit ${code} with '${message}', got ${rc}: ${output}"
    failures=$((failures + 1))
  else
    echo "ok: ${message}"
  fi
}

expect_exit 64 "PUBLIC_URL must be set" -e MODE=online
expect_exit 64 "MODE must be online or airgapped" -e MODE=bogus -e PUBLIC_URL=http://uc
expect_exit 64 "set DOWNLOAD_BACKEND_URL, or REPO_URL and REPO_KEY" -e MODE=airgapped -e PUBLIC_URL=http://uc
expect_exit 64 "REPO_TYPE must be artifactory or artifact-keeper" -e MODE=online -e PUBLIC_URL=http://uc -e REPO_TYPE=nexus
expect_exit 64 "REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on" -e MODE=airgapped -e PUBLIC_URL=http://uc \
  -e REPO_TYPE=artifact-keeper -e REPO_URL=http://ak -e REPO_KEY=jenkins -e REPO_ADAPTER=off
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "needs username:password" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e MODE=online -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope

name="uc-web-wait-$$"
docker run -d --name "${name}" -e MODE=online -e PUBLIC_URL=http://uc "${image}" >/dev/null
sleep 3
if docker logs "${name}" 2>&1 | grep -q "waiting for the first published release"; then
  echo "ok: waits for the first release"
else
  echo "FAIL: web did not report that it is waiting for the first release"
  failures=$((failures + 1))
fi
docker rm -f "${name}" >/dev/null
exit "${failures}"
