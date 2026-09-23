# syntax=docker/dockerfile:1
# uc image: pinned + patched update-center2 (spec §4, §7.4) and the uc CLI.
ARG UC2_VERSION=3.18.4
ARG UC2_COMMIT=b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd
ARG SUPERCRONIC_VERSION=v0.2.49

FROM maven:3.9-eclipse-temurin-21 AS jar-build
ARG UC2_VERSION
ARG UC2_COMMIT
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --quiet --depth 1 --branch "update-center2-${UC2_VERSION}" \
      https://github.com/jenkins-infra/update-center2.git . \
 && test "$(git rev-parse HEAD)" = "${UC2_COMMIT}"
COPY patches/ /patches/
RUN git apply --check /patches/*.patch && git apply /patches/*.patch
RUN mvn -B -ntp verify
# The runtime image is a JRE, which cannot run single-file source programs, so compile VersionTool here.
COPY java/ /java/
RUN mkdir -p /out/generator /out/site /out/tools \
 && javac -cp "target/update-center2-${UC2_VERSION}-bin/*" -d /out/tools /java/VersionTool.java \
 && cp target/update-center2-${UC2_VERSION}-bin/*.jar /out/generator/ \
 && ln -s update-center2-${UC2_VERSION}.jar /out/generator/update-center2.jar \
 && cp site/generate-htaccess.sh /out/site/ \
 && cp -r resources /out/resources \
 && printf '{"source": "bundled", "ref": "%s", "data_date": "%s"}\n' \
      "$(git rev-parse HEAD)" "$(git log -1 --format=%cI)" > /out/resources/.uc-resources.json

FROM eclipse-temurin:21-jre AS runtime
ARG UC2_VERSION
ARG SUPERCRONIC_VERSION
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 openssl tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    arch="${TARGETARCH:-amd64}"; \
    case "${arch}" in \
      amd64) sum=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1 ;; \
      arm64) sum=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5 ;; \
      *) echo "unsupported architecture ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"; \
    echo "${sum}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/supercronic
COPY --from=jar-build /out/ /opt/uc/
COPY templates/ /opt/uc/templates/
COPY uc/ /opt/uc/python/uc/
COPY bin/uc /usr/local/bin/uc
RUN chmod 0755 /usr/local/bin/uc /opt/uc/site/generate-htaccess.sh \
 && groupadd --system --gid 10001 uc \
 && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin uc \
 && mkdir -p /data /config/overlay \
 && chown 10001:10001 /data
ENV PYTHONPATH=/opt/uc/python \
    PYTHONDONTWRITEBYTECODE=1 \
    UC_APP_DIR=/opt/uc \
    UC2_VERSION=${UC2_VERSION}+patches
USER 10001:10001
WORKDIR /data
ENTRYPOINT ["/usr/bin/tini", "--", "uc"]
CMD ["status"]

FROM runtime AS test
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pytest git \
 && rm -rf /var/lib/apt/lists/*
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python3", "-m", "pytest", "-p", "no:cacheprovider"]
CMD ["-q", "/tests/unit"]
