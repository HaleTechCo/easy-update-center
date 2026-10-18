# syntax=docker/dockerfile:1
# Web tier (docs/architecture/request-routing.md): Apache serving /data/live with upstream's .htaccess routing.
# Built in a full stage, then copied into an empty one, so the image is a single layer holding only
# what it runs (docs/architecture/hardening.md#images).

# Pinned by digest, so a build starts from a known image; Dependabot proposes the new digest when the
# tag moves (docs/reference/dependencies.md#pinning-policy).
FROM httpd:2.4@sha256:c842ac797bd1fe8b3cd26d002ac9d948aa2814097544600600b35aef42e6785a AS root
# pipefail: a failure anywhere in a pipe below fails the build.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Every package is brought up to the archive's newest version, so a release ships the fixes its base
# image has not picked up yet. Distribution packages are not version-pinned (DL3008): the Debian
# archive drops superseded versions, so a pin breaks the build as soon as the base image moves on.
# hadolint ignore=DL3008
RUN set -eu; \
    apt-get update; \
    apt-get full-upgrade -y; \
    apt-get install -y --no-install-recommends ca-certificates openssl; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*
COPY conf/httpd.conf conf/uc-site.conf conf/uc-tls.conf /usr/local/apache2/conf/
COPY entrypoint.sh healthcheck.sh /usr/local/bin/
# Keep only what Apache runs here: httpd itself and the modules the configuration loads (the base
# ships 130); its other programs (suexec among them, setuid root), headers, build files, icons and
# sample pages go. Then keep only the packages those files, openssl and the CA bundle need, as the
# base image's own build does, so the libraries of the removed modules (LDAP, Lua, libxml2, curl and
# its protocols) go too.
# Then syntax-check all 8 define combinations the entrypoint can pass (2 backends x redirect or proxy x
# plain or TLS; proxy and TLS each add NEED_SSL): httpd -t loads every module, so it also proves no
# library a kept module needs was removed. Last, remove the scaffolding: /data/live must never ship in
# the image, or Docker would copy it into an empty volume and break the first symlink swap.
# ${defines} is unquoted on purpose (SC2086): each entry is several -D flags that must split into
# separate httpd arguments.
WORKDIR /usr/local/apache2
# hadolint ignore=SC2086
RUN set -eu; \
    for module in modules/*.so; do \
      grep -qF "${module}" conf/httpd.conf conf/uc-site.conf conf/uc-tls.conf || rm "${module}"; \
    done; \
    rm modules/httpd.exp; \
    find bin -mindepth 1 ! -name httpd -delete; \
    rm -rf build cgi-bin error htdocs icons include logs; \
    apt-mark auto '.*' > /dev/null; \
    apt-mark manual ca-certificates openssl > /dev/null; \
    find /usr/local -type f -executable -exec ldd '{}' ';' 2>/dev/null \
      | awk '/=>/ { so = $(NF-1); if (index(so, "/usr/local/") == 1) next; gsub("^/(usr/)?", "", so); printf "*%s\n", so }' \
      | sort -u | xargs -r dpkg-query --search | cut -d: -f1 | sort -u | xargs -r apt-mark manual > /dev/null; \
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false; \
    chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh; \
    mkdir -p /data/live /tmp/tls; \
    touch /data/live/.htdownload-map; \
    openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=check \
      -keyout /tmp/tls/k.pem -out /tmp/tls/c.pem 2>/dev/null; \
    export PUBLIC_URL=http://check:8080 DOWNLOAD_BACKEND_URL=http://backend \
      DOWNLOAD_PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt DOWNLOAD_PROXY_AUTH_HEADER="Basic eDp5" \
      TLS_CERT_FILE=/tmp/tls/c.pem TLS_KEY_FILE=/tmp/tls/k.pem; \
    for backend in BACKEND_MIRROR BACKEND_ARTIFACTORY; do \
      for access in "" "-D ACCESS_PROXY -D NEED_SSL"; do \
        for tls in "" "-D TLS -D NEED_SSL"; do \
          defines="-D ${backend} ${access} ${tls}"; \
          echo "httpd -t ${defines}"; bin/httpd -t ${defines}; \
        done; \
      done; \
    done; \
    rm -rf /data/live /tmp/tls /var/lib/apt/lists/* /var/cache/debconf/*-old /var/log/apt /var/log/dpkg.log; \
    find / -xdev -type f -perm /6000 -exec chmod a-s '{}' +; \
    chmod 1777 /data
# 1777 on /data, as in the uc image: Docker copies this mode into an empty uc-data volume, and web
# (which mounts it too) can be the container that creates it, so any UC_UID must still be able to
# write it from uc. No file keeps a setuid or setgid bit: nothing here runs as anyone but its user.

FROM scratch
# The whole filesystem on purpose (DL3067): copying it into an empty stage is what makes the image
# one layer, with nothing the stage above deleted still present in a lower layer.
# hadolint ignore=DL3067
COPY --from=root / /
LABEL org.opencontainers.image.title="Easy Update Center for Jenkins: web" \
      org.opencontainers.image.description="Serves the update center (Apache httpd)" \
      org.opencontainers.image.url="https://github.com/HaleTechCo/easy-update-center" \
      org.opencontainers.image.documentation="https://github.com/HaleTechCo/easy-update-center/tree/main/docs" \
      org.opencontainers.image.source="https://github.com/HaleTechCo/easy-update-center" \
      org.opencontainers.image.vendor="HaleTechCo" \
      org.opencontainers.image.licenses="MIT"
# What the base image set, which the copy into an empty stage does not carry over. Its WORKDIR is left
# out: httpd finds everything through its ServerRoot, and BuildKit would add a layer for it.
ENV PATH=/usr/local/apache2/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HTTPD_PREFIX=/usr/local/apache2
STOPSIGNAL SIGWINCH
# The uc image's default user, numerically; compose.yaml runs it as UC_UID:UC_GID.
USER 10001:10001
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
