#!/bin/sh
set -e
test -e /data/live/.htdownload-map
curl -fsS -o /dev/null --max-time 4 http://127.0.0.1:8080/uctest.json
