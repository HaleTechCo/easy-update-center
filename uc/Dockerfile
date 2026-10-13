# syntax=docker/dockerfile:1
# uc image: pinned + patched update-center2 and the uc CLI.
# See docs/architecture/overview.md and docs/development/upgrading-upstream.md#the-patches.
ARG UC2_VERSION=3.18.4
ARG UC2_COMMIT=b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd
ARG SUPERCRONIC_VERSION=v0.2.49
# The release uc reports (uc --version, status.json uc_version): the Release workflow sets it; dev for a
# build from source (make build UC_VERSION=... to set it).
ARG UC_VERSION=dev

# Base images are pinned by digest, so a build is reproducible; Dependabot proposes the new digest
# when a tag moves (docs/reference/dependencies.md#pinning-policy).
FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS jar-build
ARG UC2_VERSION
ARG UC2_COMMIT
# Distribution packages are not version-pinned (DL3008) in any stage: the Ubuntu archive drops
# superseded versions, so a pin breaks the build as soon as the base image moves on.
# hadolint ignore=DL3008
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
# Third-party notices for everything the image ships, in /opt/uc/licenses/: Maven lists each runtime
# library with its license, and the build fails when a library has no known license
# (THIRD-PARTY.properties supplies the ones whose POM declares none) or when a shipped jar is not
# listed. See docs/reference/dependencies.md#third-party-notices.
COPY licenses/ /licenses/
RUN mvn -B -ntp -q org.codehaus.mojo:license-maven-plugin:2.4.0:add-third-party \
      -Dlicense.excludedScopes=test,provided,system -Dlicense.failOnMissing=true \
      -Dlicense.useMissingFile=true -Dlicense.missingFile=/licenses/THIRD-PARTY.properties \
      -Dlicense.outputDirectory=/out/licenses -Dlicense.thirdPartyFilename=THIRD-PARTY.txt \
 && cp /licenses/*.txt /out/licenses/ \
 && sed -n 's/.*(\([^:( ]*\):\([^: ]*\):\([^ ]*\) - .*/\2-\3.jar/p' /out/licenses/THIRD-PARTY.txt > /tmp/listed \
 && for jar in /out/generator/*.jar; do \
      name="${jar##*/}"; \
      case "${name}" in update-center2*) continue ;; *) ;; esac; \
      grep -qxF "${name}" /tmp/listed || { echo "no third-party notice for ${name}" >&2; exit 1; }; \
    done

FROM eclipse-temurin:21-jre@sha256:cff19e6215689161eb6162c11b86b0c60ddf802164f2eaf48d570f8fb79a36c5 AS runtime
ARG UC2_VERSION
ARG SUPERCRONIC_VERSION
ARG TARGETARCH
ARG UC_VERSION
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 openssl tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
# pipefail, so a failed download cannot hide behind the checksum pipe below.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
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
# The setup service's entrypoint (compose.yaml).
COPY bin/uc-setup /usr/local/bin/
RUN chmod 0755 /usr/local/bin/uc /usr/local/bin/uc-setup /opt/uc/site/generate-htaccess.sh \
 && groupadd --system --gid 10001 uc \
 && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin uc \
 && mkdir -p /data/repo-adapter /config/overlay \
 && chmod 1777 /data /data/repo-adapter
# Any UC_UID can write a new data volume (Docker copies /data's mode into it), and HOME is writable
# for a uid with no entry in /etc/passwd. PYTHONSAFEPATH: python3 -m uc never imports from the
# working directory, /data, which other containers can write. /data/repo-adapter too, for
# the adapter's own volume.
ENV PYTHONPATH=/opt/uc/python \
    PYTHONSAFEPATH=1 \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    UC2_VERSION=${UC2_VERSION}+patches \
    UC_VERSION=${UC_VERSION}
USER 10001:10001
WORKDIR /data
ENTRYPOINT ["/usr/bin/tini", "--", "uc"]
CMD ["status"]

FROM runtime AS test
USER 0:0
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pytest python3-yaml git jq \
 && rm -rf /var/lib/apt/lists/*
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python3", "-m", "pytest", "-p", "no:cacheprovider"]
CMD ["-q", "/tests/unit"]
