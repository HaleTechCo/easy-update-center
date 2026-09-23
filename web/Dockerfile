# Web tier (spec §8): Apache serving /data/live with upstream's .htaccess routing.
FROM httpd:2.4
# Distribution packages are not version-pinned (DL3008): httpd:2.4 tracks Debian's current release,
# and the archive drops superseded versions, so a pin breaks the build the day the base moves.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl openssl \
 && rm -rf /var/lib/apt/lists/*
COPY conf/httpd.conf conf/uc-site.conf conf/uc-tls.conf /usr/local/apache2/conf/
COPY entrypoint.sh healthcheck.sh /usr/local/bin/
# Syntax-check every define combination, then remove the scaffolding: /data/live must never ship
# in the image, or Docker would copy it into an empty volume and break the first symlink swap.
# ${defines} is unquoted on purpose (SC2086): each entry is several -D flags that must split into
# separate httpd arguments, and /bin/sh here is dash, which has no arrays.
# hadolint ignore=SC2086
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
 && mkdir -p /data/live /tmp/tls \
 && touch /data/live/.htdownload-map \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=check \
      -keyout /tmp/tls/k.pem -out /tmp/tls/c.pem 2>/dev/null \
 && export PUBLIC_URL=http://check:8080 DOWNLOAD_BACKEND_URL=http://backend \
      DOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt DOWNLOAD_PROXY_AUTH_HEADER="Basic eDp5" \
      TLS_CERT_FILE=/tmp/tls/c.pem TLS_KEY_FILE=/tmp/tls/k.pem \
 && for defines in "-D BACKEND_MIRROR" "-D BACKEND_ARTIFACTORY" \
      "-D BACKEND_MIRROR -D ACCESS_PROXY -D NEED_SSL" "-D BACKEND_ARTIFACTORY -D ACCESS_PROXY -D NEED_SSL" \
      "-D BACKEND_ARTIFACTORY -D TLS -D NEED_SSL"; do \
      echo "httpd -t ${defines}"; httpd -t ${defines} || exit 1; \
    done \
 && rm -rf /data/live /tmp/tls \
 && chown 10001:10001 /data
# www-data, numerically, so a runAsNonRoot check can verify it without the image's /etc/passwd.
USER 33:33
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
