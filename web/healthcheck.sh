#!/bin/bash
# Healthy once a release is live and the site answers 200. Bash's /dev/tcp (and openssl for TLS) make
# the request, so the image needs no HTTP client.
set -euo pipefail
test -e /data/live/.htdownload-map
request=$'GET /uctest.json HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n'
if [[ -n "${TLS_CERT_FILE:-}" ]]; then
    # With TLS on, 8080 only redirects: the site answers on 8443. The certificate is not checked:
    # this is loopback.
    status="$(printf '%s' "${request}" | timeout 4 openssl s_client -quiet -connect 127.0.0.1:8443 2> /dev/null | head -n 1)"
else
    exec 3<> /dev/tcp/127.0.0.1/8080
    printf '%s' "${request}" >&3
    IFS= read -r -t 4 status <&3
fi
[[ "${status}" == "HTTP/1."?" 200 "* ]]
