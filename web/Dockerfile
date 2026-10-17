# Web tier (docs/architecture/request-routing.md): Apache serving /data/live with upstream's .htaccess routing.
# Pinned by digest, so a build is reproducible; Dependabot proposes the new digest when the tag moves
# (docs/reference/dependencies.md#pinning-policy).
FROM httpd:2.4@sha256:c842ac797bd1fe8b3cd26d002ac9d948aa2814097544600600b35aef42e6785a
# Distribution packages are not version-pinned (DL3008): the Debian archive drops superseded
# versions, so a pin breaks the build as soon as the base image moves on.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl openssl \
 && rm -rf /var/lib/apt/lists/*
COPY conf/httpd.conf conf/uc-site.conf conf/uc-tls.conf /usr/local/apache2/conf/
COPY entrypoint.sh healthcheck.sh /usr/local/bin/
# Syntax-check all 8 define combinations the entrypoint can pass (2 backends x redirect or proxy x
# plain or TLS; proxy and TLS each add NEED_SSL), then remove the scaffolding: /data/live must never ship
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
 && for backend in BACKEND_MIRROR BACKEND_ARTIFACTORY; do \
      for access in "" "-D ACCESS_PROXY -D NEED_SSL"; do \
        for tls in "" "-D TLS -D NEED_SSL"; do \
          defines="-D ${backend} ${access} ${tls}"; \
          echo "httpd -t ${defines}"; httpd -t ${defines} || exit 1; \
        done; \
      done; \
    done \
 && rm -rf /data/live /tmp/tls \
 && chmod 1777 /data
# 1777 on /data, as in the uc image: Docker copies this mode into an empty uc-data volume, and web
# (which mounts it too) can be the container that creates it, so any UC_UID must still be able to
# write it from uc.
# The uc image's default user, numerically; compose.yaml runs it as UC_UID:UC_GID.
USER 10001:10001
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
