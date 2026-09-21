FROM maven:3.9-eclipse-temurin-21 AS build

ARG UPDATE_CENTER_VERSION=3.18.4

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone \
        --depth 1 \
        --branch "v${UPDATE_CENTER_VERSION}" \
        https://github.com/jenkins-infra/update-center2.git \
        .

RUN mvn -B -DskipTests clean verify


FROM eclipse-temurin:21-jre

ARG UPDATE_CENTER_VERSION=3.18.4

ENV UPDATE_CENTER_VERSION=${UPDATE_CENTER_VERSION}
ENV JAVA_TOOL_OPTIONS="-Dfile.encoding=UTF-8"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        jq \
        openssl \
        rsync \
    && rm -rf /var/lib/apt/lists/*

RUN useradd \
        --system \
        --create-home \
        --home-dir /opt/update-center \
        --shell /usr/sbin/nologin \
        updatecenter

WORKDIR /opt/update-center

COPY --from=build \
    /src/target/update-center2-${UPDATE_CENTER_VERSION}-bin/update-center2-${UPDATE_CENTER_VERSION}.jar \
    /opt/update-center/update-center2.jar

COPY --from=build \
    /src/resources \
    /opt/update-center/resources

COPY generate.sh /opt/update-center/generate.sh

RUN chmod +x /opt/update-center/generate.sh \
    && mkdir -p /data/www /data/downloads /run/update-center \
    && chown -R updatecenter:updatecenter \
        /opt/update-center \
        /data \
        /run/update-center

USER updatecenter

ENTRYPOINT ["/opt/update-center/generate.sh"]
