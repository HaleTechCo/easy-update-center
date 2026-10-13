#!/bin/sh
set -e
test -e /data/live/.htdownload-map
if [ -n "${TLS_CERT_FILE:-}" ]; then
    # With TLS on, 8080 only redirects: the site answers on 8443. -k: loopback only.
    curl -fsS -k -o /dev/null --max-time 4 https://127.0.0.1:8443/uctest.json
else
    curl -fsS -o /dev/null --max-time 4 http://127.0.0.1:8080/uctest.json
fi
