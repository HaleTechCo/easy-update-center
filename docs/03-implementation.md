Yes. Let's make this a **real self-hosted mirror**, not just a JSON proxy:

 - upstream `update-center2` generator
- version-aware `?version=...` routing
- dynamic weekly/LTS tiers
- `plugin-versions.json`
- `release-history.json`
- `plugin-documentation-urls.json`
- `latestCore.txt`
- signed update-center metadata
- plugin + Jenkins WAR downloads stored locally
- Apache serving everything
- scheduled regeneration
- persistent storage
- configurable hostname and mirror URLs

 The upstream project explicitly supports corporate/private update centers, batch generation, tiered sites, and the metadata files above. Its current release is **3.18.4**, which includes a security fix, so I'll pin the implementation to that rather than floating `master`.  GitHub+1

 One caveat before you start: **a complete plugin/WAR mirror is large** and will grow over time. If you don't want to store all artifacts locally, I'll show you the small change to turn this into a metadata-only mirror later.

 ## 1\. Directory structure

 Create:

```
jenkins-update-center/
├── docker-compose.yml
├── .env
├── generator/
│   ├── Dockerfile
│   └── generate.sh
├── httpd/
│   ├── Dockerfile
│   └── httpd.conf
├── secrets/
│   ├── update-center.key
│   └── update-center.crt
└── data/
    ├── www/
    └── downloads/
```

 Don't put the private key in Git.

---

 # 2\. Generator Dockerfile

 `generator/Dockerfile`:

```
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
```

 This deliberately builds the upstream generator rather than implementing the Jenkins metadata format ourselves. The upstream project produces a bundled distribution and documents invoking it with an arguments file.  GitHub

 The generator moved to Java 21 in the 3.18.x line, so the image uses JDK/JRE 21.  GitHub

---

 # 3\. Generator script

 `generator/generate.sh`:

```
#!/usr/bin/env bash

set -Eeuo pipefail

WWW_ROOT="/data/www"
DOWNLOAD_ROOT="/data/downloads"
WORK_ROOT="/run/update-center"

GENERATOR="/opt/update-center/update-center2.jar"
RESOURCES="/opt/update-center/resources"

SECRET_DIR="${SECRET_DIR:-/run/secrets/update-center}"

# Public URL used inside generated metadata.
UPDATE_CENTER_URL="${UPDATE_CENTER_URL:?UPDATE_CENTER_URL must be set}"

# How far back we retain tiered Jenkins releases.
#
# The upstream generator determines the actual tiers from Jenkins plugin
# dependencies. This controls how often we regenerate them, not the tier list.
RECENT_RELEASES_MAX_AGE_HOURS="${RECENT_RELEASES_MAX_AGE_HOURS:-720}"

CERTIFICATE_MINIMUM_VALID_DAYS="${CERTIFICATE_MINIMUM_VALID_DAYS:-30}"

mkdir -p "$WWW_ROOT" "$DOWNLOAD_ROOT" "$WORK_ROOT"

KEY="${SECRET_DIR}/update-center.key"
CERT="${SECRET_DIR}/update-center.crt"

if [[ ! -f "$KEY" ]]; then
    echo "ERROR: missing ${KEY}" >&2
    exit 1
fi

if [[ ! -f "$CERT" ]]; then
    echo "ERROR: missing ${CERT}" >&2
    exit 1
fi

echo "=============================================="
echo " Jenkins Update Center Generator"
echo "=============================================="
echo "Generator:       ${GENERATOR}"
echo "Update center:   ${UPDATE_CENTER_URL}"
echo "Output:          ${WWW_ROOT}"
echo "Downloads:       ${DOWNLOAD_ROOT}"
echo

rm -rf "${WORK_ROOT:?}"/*
mkdir -p "$WORK_ROOT"

cd /opt/update-center

echo "Fetching Jenkins tier information..."

java \
    -Dfile.encoding=UTF-8 \
    -jar "$GENERATOR" \
    --dynamic-tier-list-file "${WORK_ROOT}/tiers.json"

mapfile -t WEEKLY_RELEASES < <(
    jq -r '.weeklyCores[]' "${WORK_ROOT}/tiers.json"
)

mapfile -t STABLE_RELEASES < <(
    jq -r '.stableCores[]' "${WORK_ROOT}/tiers.json"
)

if [[ ${#WEEKLY_RELEASES[@]} -eq 0 ]]; then
    echo "ERROR: no weekly Jenkins tiers were returned" >&2
    exit 1
fi

if [[ ${#STABLE_RELEASES[@]} -eq 0 ]]; then
    echo "ERROR: no stable Jenkins tiers were returned" >&2
    exit 1
fi

echo
echo "Weekly tiers:"
printf '  %s\n' "${WEEKLY_RELEASES[@]}"

echo
echo "LTS tiers:"
printf '  %s\n' "${STABLE_RELEASES[@]}"

echo
echo "Generating Apache routing rules..."

cat > "${WWW_ROOT}/.htaccess" <<EOF
# Generated by Jenkins Update Center
# DO NOT EDIT MANUALLY

RewriteEngine On

# Jenkins asks for:
#
#   /update-center.json?version=2.555.3
#
# The rules below route it to the newest compatible tier.

EOF

generate_tier_rules() {
    local version
    local major
    local minor
    local patch

    for version in "$@"; do
        IFS='.' read -r major minor patch <<< "$version"

        if [[ -z "${patch:-}" ]]; then
            cat >> "${WWW_ROOT}/.htaccess" <<EOF
# Weekly Jenkins ${version}
RewriteCond %{QUERY_STRING} (^|&)version=([0-9]+)\.([0-9]+)([-].*)?(&|$) [NC]
RewriteCond %2 =${major}
RewriteCond %3 >=${minor}
RewriteRule ^update-center.*\\.json$ /dynamic-${version}%{REQUEST_URI}? [NC,L,R=302]

EOF
        else
            cat >> "${WWW_ROOT}/.htaccess" <<EOF
# LTS Jenkins ${version}
RewriteCond %{QUERY_STRING} (^|&)version=([0-9]+)\\.([0-9]+)\\.([0-9]+)([-].*)?(&|$) [NC]
RewriteCond %2 =${major}
RewriteCond %3 =${minor}
RewriteCond %4 >=${patch}
RewriteRule ^update-center.*\\.json$ /dynamic-stable-${version}%{REQUEST_URI}? [NC,L,R=302]

EOF
        fi
    done
}

generate_tier_rules "${WEEKLY_RELEASES[@]}"
generate_tier_rules "${STABLE_RELEASES[@]}"

OLDEST_WEEKLY="${WEEKLY_RELEASES[0]}"
OLDEST_STABLE="${STABLE_RELEASES[0]}"
NEWEST_STABLE="${STABLE_RELEASES[-1]}"

cat >> "${WWW_ROOT}/.htaccess" <<EOF

# Older Jenkins releases fall back to the oldest supported tiers.

RewriteCond %{QUERY_STRING} (^|&)version=[0-9]+\\.([0-9]+)\\.([0-9]+)([-].*)?(&|$) [NC]
RewriteRule ^update-center.*\\.json$ /dynamic-stable-${OLDEST_STABLE}%{REQUEST_URI}? [NC,L,R=302]

RewriteCond %{QUERY_STRING} (^|&)version=[0-9]+\\.([0-9]+)([-].*)?(&|$) [NC]
RewriteRule ^update-center.*\\.json$ /dynamic-${OLDEST_WEEKLY}%{REQUEST_URI}? [NC,L,R=302]

# Explicit stable URL.
RewriteRule ^stable/(.*)$ /dynamic-stable-${NEWEST_STABLE}/\$1 [NC,L,R=302]

# No version supplied -> current.
RewriteRule ^update-center.*\\.json$ /current%{REQUEST_URI}? [NC,L,R=302]

RewriteRule ^latestCore\\.txt$ /current/latestCore.txt [NC,L,R=302]
RewriteRule ^release-history\\.json$ /current/release-history.json [NC,L,R=302]
RewriteRule ^plugin-documentation-urls\\.json$ /current/plugin-documentation-urls.json [NC,L,R=302]
RewriteRule ^plugin-versions\\.json$ /current/plugin-versions.json [NC,L,R=302]

# Serve local artifacts instead of redirecting to get.jenkins.io.
#
# update-center2's stock htaccess generator normally redirects these
# externally. We intentionally do not do that here.

DirectoryIndex index.html

EOF

echo
echo "Preparing generator arguments..."

ARGS="${WORK_ROOT}/arguments.txt"

{
    echo "--key ${KEY}"
    echo "--certificate ${CERT}"
    echo "--root-certificate ${CERT}"

    # The URLs embedded in generated metadata.
    echo "--download-links-directory ${WWW_ROOT}/download"

    # The actual local artifact repository.
    echo "--downloads-directory ${DOWNLOAD_ROOT}"

    # Current update center.
    echo "--www-dir ${WWW_ROOT}/current"

    echo "--generate-release-history"
    echo "--generate-recent-releases"
    echo "--generate-plugin-versions"
    echo "--generate-plugin-documentation-urls"
    echo "--write-latest-core"
    echo "--write-timestamp"
    echo "--write-plugin-count"
    echo "--latest-links-directory ${WWW_ROOT}/current/latest"

    # Keep the generator's Jenkins project resource metadata.
    echo "--resources-dir ${RESOURCES}"

    # Public URL used by the generator.
    echo "--download-url ${UPDATE_CENTER_URL}/download"
} > "$ARGS"

#
# Generate the dynamic tiers.
#

for version in "${WEEKLY_RELEASES[@]}"; do
    echo "--limit-plugin-core-dependency ${version}"
    echo "--write-latest-core"
    echo "--write-timestamp"
    echo "--www-dir ${WWW_ROOT}/dynamic-${version}"
done >> "$ARGS"

for version in "${STABLE_RELEASES[@]}"; do
    echo "--limit-plugin-core-dependency ${version}"
    echo "--write-latest-core"
    echo "--write-timestamp"
    echo "--only-stable-core"
    echo "--www-dir ${WWW_ROOT}/dynamic-stable-${version}"
done >> "$ARGS"

#
# Experimental.
#

cat >> "$ARGS" <<EOF
--with-experimental
--generate-recent-releases
--downloads-directory ${DOWNLOAD_ROOT}
--www-dir ${WWW_ROOT}/experimental
--latest-links-directory ${WWW_ROOT}/experimental/latest
EOF

echo
echo "Running update-center2..."
echo

java \
    -DCERTIFICATE_MINIMUM_VALID_DAYS="${CERTIFICATE_MINIMUM_VALID_DAYS}" \
    -DRECENT_RELEASES_MAX_AGE_HOURS="${RECENT_RELEASES_MAX_AGE_HOURS}" \
    -Dfile.encoding=UTF-8 \
    -jar "$GENERATOR" \
    --arguments-file "$ARGS"

#
# Create compatibility links.
#

cd "$WWW_ROOT"

rm -f latestCore.txt
rm -f plugin-versions.json
rm -f plugin-documentation-urls.json
rm -f release-history.json
rm -f update-center.json
rm -f update-center.actual.json

ln -s current/latestCore.txt latestCore.txt
ln -s current/plugin-versions.json plugin-versions.json
ln -s current/plugin-documentation-urls.json plugin-documentation-urls.json
ln -s current/release-history.json release-history.json
ln -s current/update-center.json update-center.json
ln -s current/update-center.actual.json update-center.actual.json

rm -rf stable
ln -s "dynamic-stable-${NEWEST_STABLE}" stable

cp "${WORK_ROOT}/tiers.json" "${WWW_ROOT}/tiers.json"

#
# Make tiered sites able to find /updates metadata.
#
for version in "${WEEKLY_RELEASES[@]}"; do
    ln -sfn ../updates "${WWW_ROOT}/dynamic-${version}/updates"
done

for version in "${STABLE_RELEASES[@]}"; do
    ln -sfn ../updates "${WWW_ROOT}/dynamic-stable-${version}/updates"
done

ln -sfn ../updates "${WWW_ROOT}/current/updates"
ln -sfn ../updates "${WWW_ROOT}/experimental/updates"

#
# Make local download artifacts available below the web root.
#
# Apache will follow this symlink.
#

rm -rf "${WWW_ROOT}/download"
ln -s "${DOWNLOAD_ROOT}" "${WWW_ROOT}/download"

#
# Simple sanity checks.
#

for file in \
    "${WWW_ROOT}/current/update-center.json" \
    "${WWW_ROOT}/current/update-center.actual.json" \
    "${WWW_ROOT}/current/plugin-versions.json" \
    "${WWW_ROOT}/current/release-history.json" \
    "${WWW_ROOT}/current/plugin-documentation-urls.json" \
    "${WWW_ROOT}/current/latestCore.txt"
do
    if [[ ! -s "$file" ]]; then
        echo "ERROR: expected generated file is missing: $file" >&2
        exit 1
    fi
done

echo
echo "=============================================="
echo " Update Center generation complete"
echo "=============================================="
echo
echo "Tiers:"
cat "${WWW_ROOT}/tiers.json" | jq .
echo
echo "Generated:"
find "${WWW_ROOT}/current" -maxdepth 1 -type f -printf '  %f\n' | sort
echo
```

 ### One adjustment

 The upstream generator's exact CLI options can change between releases, and the project explicitly warns that it is primarily optimized for Jenkins infrastructure.  GitHub

 So I would **not blindly deploy that script yet**. There's one particularly important thing to verify against `3.18.4`: the exact download URL option name. The upstream site script currently passes its arguments to the generator and handles the site layout itself.  GitHub

 Rather than give you a plausible-but-brittle command, I'd make the production version use the upstream `site/generate.sh` as the base and patch only the parts that point downloads back to Jenkins.

 That is safer.

---

 # 4\. Apache container

 `httpd/Dockerfile`:

```
FROM httpd:2.4

RUN sed -i \
        's/^#LoadModule rewrite_module/LoadModule rewrite_module/' \
        /usr/local/apache2/conf/httpd.conf

COPY httpd.conf /usr/local/apache2/conf/httpd.conf
```

 `httpd/httpd.conf`:

```
ServerName localhost

LoadModule rewrite_module modules/mod_rewrite.so

Listen 8080

User daemon
Group daemon

DocumentRoot "/data/www"

<Directory "/data/www">
    Options FollowSymLinks
    AllowOverride FileInfo Indexes
    Require all granted
</Directory>

# Apache needs to be able to traverse the mounted directory.
<Directory "/data">
    Options FollowSymLinks
    AllowOverride None
    Require all granted
</Directory>

DirectoryIndex index.html

ErrorLog /proc/self/fd/2
CustomLog /proc/self/fd/1 combined
```

 The official Jenkins architecture uses `mod_rewrite` to turn the Jenkins `version` query parameter into requests for the appropriate `dynamic-*` update site.  GitHub+1

---

 # 5\. Docker Compose

 `docker-compose.yml`:

```
services:

  generator:
    build:
      context: ./generator
      args:
        UPDATE_CENTER_VERSION: "3.18.4"

    restart: "no"

    environment:
      UPDATE_CENTER_URL: "${UPDATE_CENTER_URL}"
      SECRET_DIR: "/run/secrets/update-center"

      # Regenerate metadata for recently released Jenkins/plugin versions.
      RECENT_RELEASES_MAX_AGE_HOURS: "${RECENT_RELEASES_MAX_AGE_HOURS:-720}"

      # Refuse to use a signing certificate that is about to expire.
      CERTIFICATE_MINIMUM_VALID_DAYS: "${CERTIFICATE_MINIMUM_VALID_DAYS:-30}"

    volumes:
      - update-center-www:/data/www
      - update-center-downloads:/data/downloads

    secrets:
      - update-center

  httpd:
    build:
      context: ./httpd

    restart: unless-stopped

    ports:
      - "8080:8080"

    volumes:
      - update-center-www:/data/www:ro
      - update-center-downloads:/data/downloads:ro

    depends_on:
      generator:
        condition: service_completed_successfully

volumes:
  update-center-www:
  update-center-downloads:

secrets:
  update-center:
    file: ./secrets/update-center.key
```

 We also need the certificate visible to the generator, so change the secret definition to mount both files. Docker Compose secrets can do that, but it's a little cumbersome. For a first deployment, I'd actually use a read-only secrets directory:

```
    volumes:
      - update-center-www:/data/www
      - update-center-downloads:/data/downloads
      - ./secrets:/run/secrets/update-center:ro
```

 and remove the `secrets:` section entirely.

 So the simpler generator service is:

```
  generator:
    build:
      context: ./generator
      args:
        UPDATE_CENTER_VERSION: "3.18.4"

    restart: "no"

    environment:
      UPDATE_CENTER_URL: "${UPDATE_CENTER_URL}"

    volumes:
      - update-center-www:/data/www
      - update-center-downloads:/data/downloads
      - ./secrets:/run/secrets/update-center:ro
```

 That's what I'd use initially.

---

 # 6\. Generate the signing certificate

 For a private update center, **do not reuse Jenkins' public update-center certificate**.

 Generate your own.

```
mkdir -p secrets

openssl genrsa \
  -out secrets/update-center.key \
  4096

openssl req \
  -new \
  -x509 \
  -sha256 \
  -days 3650 \
  -key secrets/update-center.key \
  -out secrets/update-center.crt \
  -subj "/C=US/O=My Organization/OU=Jenkins Update Center/CN=jenkins-update-center"
```

 Then:

```
chmod 600 secrets/update-center.key
chmod 644 secrets/update-center.crt
```

 The upstream project documents this exact general model: sign the generated JSON and install the signing certificate as a trusted root CA in Jenkins under `JENKINS_HOME/update-center-rootCAs/`.  GitHub

---

 # 7\. `.env`

 For example:

```
UPDATE_CENTER_URL=https://jenkins-update-center.example.com
RECENT_RELEASES_MAX_AGE_HOURS=720
CERTIFICATE_MINIMUM_VALID_DAYS=30
```

---

 # 8\. Start it

 First build:

```
docker compose build
```

 Then generate:

```
docker compose run --rm generator
```

 Then start Apache:

```
docker compose up -d httpd
```

 You should then have:

```
http://localhost:8080/update-center.json
http://localhost:8080/update-center.json?version=2.555.3
http://localhost:8080/current/update-center.json
http://localhost:8080/current/plugin-versions.json
http://localhost:8080/current/release-history.json
http://localhost:8080/current/plugin-documentation-urls.json
http://localhost:8080/current/latestCore.txt
```

 The important bit is that `update-center.json?version=...` gets routed to a tiered site. That's the same basic mechanism used by `updates.jenkins.io`.  GitHub+1

---

 # 9\. Tell Jenkins to trust it

 On the Jenkins controller:

```
$JENKINS_HOME/
└── update-center-rootCAs/
    └── update-center.crt
```

 Then configure the update site as:

```
https://jenkins-update-center.example.com/update-center.json
```

 The Jenkins documentation for the generator specifically describes putting the signing certificate into `update-center-rootCAs/`.  GitHub

 After restarting Jenkins, it should be able to validate your signed update-center metadata.

---

 # 10\. Docker-based Jenkins controllers

 There's another useful part here.

 The Jenkins Docker image's plugin CLI recognizes:

```
JENKINS_UC
JENKINS_UC_DOWNLOAD
JENKINS_PLUGIN_INFO
JENKINS_UC_EXPERIMENTAL
```

 and the default values point at `updates.jenkins.io`.  GitHub

 So for a Jenkins Docker controller, you'd want:

```
environment:
  JENKINS_UC: https://jenkins-update-center.example.com
  JENKINS_UC_DOWNLOAD: https://jenkins-update-center.example.com/download
  JENKINS_PLUGIN_INFO: https://jenkins-update-center.example.com/current/plugin-versions.json
  JENKINS_UC_EXPERIMENTAL: https://jenkins-update-center.example.com/experimental
```

 This is especially useful because it makes both **Jenkins runtime and `jenkins-plugin-cli`** use your infrastructure.

---

 ## One thing I'd change before calling this "production ready"

 I don't want to leave you with a subtly broken implementation: **the upstream `site/generate.sh` contains quite a bit of logic beyond simply invoking the Java generator**-including obtaining the dynamic tier list, generating the official rewrite rules, generating all the different site variants, and creating compatibility links.  GitHub+1

 So my recommendation is to make the final image **package the upstream `site/generate.sh` and `generate-htaccess.sh` directly**, then add a very small wrapper that changes the Jenkins-specific URLs/download behavior. That gets us much closer to "the Jenkins Update Center, but self-hosted" instead of "an implementation inspired by it."

 Also, the current upstream release is **3.18.4**, newer than the `3.18.2` version still referenced by the upstream `site/generate.sh`, so pinning the generator and wrapper independently is worth doing.  GitHub+1

 If you want the **proper production version**, I'd use that approach next: essentially a Dockerized fork of the upstream `site/generate.sh`, with only these substitutions:

```
updates.jenkins.io       → your domain
get.jenkins.io           → your local downloads
Jenkins signing cert     → your signing cert
Jenkins CI secrets       → Docker secrets
Jenkins CI publishing    → local persistent volume
```

 That will give you the closest possible reproduction of the real Jenkins Update Center infrastructure, including its tier generation and routing behavior.
