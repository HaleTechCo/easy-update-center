Yes. I checked the upstream scripts directly, and there is a much cleaner implementation than the prototype I gave you.

 One important correction first: **the current upstream `site/generate.sh` is pinned to `update-center2` 3.18.3**, not 3.18.4. I’m going to follow the upstream script rather than inventing a version/CLI combination. The upstream script itself downloads the generator, determines the weekly/LTS tiers, generates the `.htaccess`, produces the experimental/current/tiered sites, generates `plugin-versions.json` and the other metadata, and performs sanity checks.  GitHub+1

 There is also a nice detail we can exploit: **we don't need to reimplement the version routing at all.** The upstream `generate-htaccess.sh` generates exactly the `?version=` routing rules you're interested in.  GitHub

 ## The production-ish implementation

 I'd use this structure:

```
jenkins-update-center/
├── compose.yaml
├── .env
├── generator/
│   ├── Dockerfile
│   └── entrypoint.sh
├── apache/
│   ├── Dockerfile
│   └── httpd.conf
├── scripts/
│   └── publish.sh
├── secrets/
│   ├── update-center.key
│   └── update-center.cert
└── data/
    ├── www/
    └── downloads/
```

 There are three containers/jobs conceptually:

```
                  ┌─────────────────────┐
                  │  update-center2     │
                  │  generator          │
                  └──────────┬──────────┘
                             │
                      atomic publish
                             │
                             ▼
                       /data/www
                             │
                             ▼
                  ┌─────────────────────┐
                  │      Apache         │
                  │                     │
                  │ generated .htaccess │
                  │ version routing     │
                  └──────────┬──────────┘
                             │
                             ▼
                         Jenkins
```

 And `/data/downloads` is populated by `update-center2` itself. The upstream generator's current script passes `--downloads-directory` when generating the experimental and current sites.  GitHub

---

 # 1\. Generator Dockerfile

 `generator/Dockerfile`:

```
FROM eclipse-temurin:21-jre

ARG UPDATE_CENTER_VERSION=3.18.3

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        unzip \
        wget \
        coreutils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd \
        --system \
        --create-home \
        --home-dir /opt/update-center \
        --shell /usr/sbin/nologin \
        updatecenter

WORKDIR /opt/update-center

RUN mkdir -p \
        /opt/update-center/site \
        /opt/update-center/tmp \
        /data/www \
        /data/downloads \
        /run/update-center \
    && chown -R updatecenter:updatecenter \
        /opt/update-center \
        /data \
        /run/update-center

#
# Pull the upstream update-center2 repository.
#
RUN git clone \
        --depth 1 \
        --branch "v${UPDATE_CENTER_VERSION}" \
        https://github.com/jenkins-infra/update-center2.git \
        /opt/update-center/upstream

COPY entrypoint.sh /opt/update-center/entrypoint.sh

RUN chmod 0755 /opt/update-center/entrypoint.sh \
    && chown updatecenter:updatecenter /opt/update-center/entrypoint.sh

USER updatecenter

ENTRYPOINT ["/opt/update-center/entrypoint.sh"]
```

 This is intentionally **not building our own implementation of update-center2**.

 We're taking the upstream site generator and wrapping it.

 The upstream README explicitly describes `site/generate.sh` as the wrapper used to produce the real Jenkins site and says it generates the batch arguments before invoking `update-center2`.  GitHub

---

 # 2\. Generator entrypoint

 `generator/entrypoint.sh`:

```
#!/usr/bin/env bash

set -Eeuo pipefail

UPSTREAM="/opt/update-center/upstream"
SITE="${UPSTREAM}/site"

WORK="/run/update-center"

WWW_ROOT="/data/www"
DOWNLOAD_ROOT="/data/downloads"

SECRET="${SECRET:-/run/secrets/update-center}"

CERTIFICATE_MINIMUM_VALID_DAYS="${CERTIFICATE_MINIMUM_VALID_DAYS:-30}"
RECENT_RELEASES_MAX_AGE_HOURS="${RECENT_RELEASES_MAX_AGE_HOURS:-3}"

echo "=============================================="
echo " Jenkins Update Center"
echo "=============================================="
echo
echo "Upstream:        ${UPSTREAM}"
echo "WWW root:        ${WWW_ROOT}"
echo "Downloads:       ${DOWNLOAD_ROOT}"
echo "Secrets:         ${SECRET}"
echo

if [[ ! -f "${SECRET}/update-center.key" ]]; then
    echo "ERROR: missing signing key" >&2
    exit 1
fi

if [[ ! -f "${SECRET}/update-center.cert" ]]; then
    echo "ERROR: missing signing certificate" >&2
    exit 1
fi

rm -rf "${WORK}"
mkdir -p "${WORK}"

#
# The upstream script writes directly to its first argument.
#
# We generate into a temporary directory and only publish after
# validation succeeds.
#

GENERATED="${WORK}/www"
mkdir -p "${GENERATED}"

#
# Copy the upstream site scripts into our workspace.
#
cp "${SITE}/generate.sh" \
   "${WORK}/generate.sh"

cp "${SITE}/generate-htaccess.sh" \
   "${WORK}/generate-htaccess.sh"

chmod +x \
    "${WORK}/generate.sh" \
    "${WORK}/generate-htaccess.sh"

#
# We need one customization:
#
# The Jenkins script uses the Jenkins project's root CA when signing.
#
# Our private update center needs its own CA/certificate.
#
# Replace that argument with our certificate.
#

python3 - "${WORK}/generate.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

old = (
    '--root-certificate '
    '$( dirname "$0" )/../resources/certificates/'
    'jenkins-update-center-root-ca-2.crt'
)

new = (
    '--root-certificate '
    '"$SECRET/update-center.cert"'
)

if old not in text:
    raise SystemExit(
        "Could not find upstream root-certificate argument; "
        "the upstream script changed."
    )

text = text.replace(old, new)

path.write_text(text)
PY

#
# The upstream script downloads the generator distribution itself.
#
# Run it from the upstream repository because it expects its
# relative resource paths to exist.
#

cd "${UPSTREAM}"

#
# Point SECRET at our signing material.
#
export SECRET="${SECRET}"

export CERTIFICATE_MINIMUM_VALID_DAYS
export RECENT_RELEASES_MAX_AGE_HOURS

#
# Generate the complete upstream site.
#
# This performs:
#
#   - dynamic tier discovery
#   - .htaccess generation
#   - weekly update sites
#   - LTS update sites
#   - experimental update site
#   - current update site
#   - plugin-versions.json
#   - release-history.json
#   - plugin-documentation-urls.json
#   - latestCore.txt
#   - pluginCount.txt
#   - signatures
#   - download indexes
#
# See upstream/site/generate.sh.
#

echo "Generating upstream update center..."

"${WORK}/generate.sh" \
    "${GENERATED}" \
    "${DOWNLOAD_ROOT}"

#
# IMPORTANT:
#
# The upstream .htaccess redirects downloads to the Jenkins mirror
# network. We are self-hosting those files, so remove those redirects.
#
# We retain every update-center/version-routing rule.
#

python3 - "${GENERATED}/.htaccess" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text().splitlines()

out = []

for line in lines:
    if (
        "get.jenkins.io" in line
        or "updates.jenkins.io/latest" in line
    ):
        continue

    out.append(line)

path.write_text("\n".join(out) + "\n")
PY

#
# The upstream generator creates the download directory inside
# the generated web tree as a link to DOWNLOAD_ROOT.
#
# Make sure the directory exists and is readable.
#

if [[ ! -d "${DOWNLOAD_ROOT}" ]]; then
    echo "ERROR: download directory was not generated" >&2
    exit 1
fi

#
# Validation.
#

required_files=(
    "update-center.json"
    "update-center.actual.json"
    "plugin-versions.json"
    "release-history.json"
    "plugin-documentation-urls.json"
    "latestCore.txt"
    "tiers.json"
    ".htaccess"
)

echo
echo "Validating generated site..."

for file in "${required_files[@]}"; do
    if [[ ! -e "${GENERATED}/${file}" ]]; then
        echo "ERROR: missing ${file}" >&2
        exit 1
    fi
done

#
# Ensure the main metadata is valid JSON.
#
jq empty \
    "${GENERATED}/current/update-center.actual.json"

jq empty \
    "${GENERATED}/current/plugin-versions.json"

jq empty \
    "${GENERATED}/current/release-history.json"

jq empty \
    "${GENERATED}/current/plugin-documentation-urls.json"

#
# The upstream generator has its own sanity check (> 1.5 MB).
# Keep an additional check here.
#

size="$(wc -c < "${GENERATED}/current/update-center.json")"

if (( size < 1500000 )); then
    echo "ERROR: update-center.json is suspiciously small: ${size}" >&2
    exit 1
fi

echo "update-center.json: ${size} bytes"

#
# Validate that the signature metadata exists.
#

if ! grep -q '"signature"' \
    "${GENERATED}/current/update-center.json"; then

    echo "ERROR: update-center.json does not contain a signature" >&2
    exit 1
fi

echo "Signature metadata present."

#
# Publish.
#
# We don't modify the existing live directory.
#

PUBLISH_ROOT="/data/.releases"

mkdir -p "${PUBLISH_ROOT}"

RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"

RELEASE="${PUBLISH_ROOT}/${RELEASE_ID}"

mkdir -p "${RELEASE}"

echo
echo "Publishing release ${RELEASE_ID}..."

cp -a "${GENERATED}/." "${RELEASE}/"

#
# Atomic switch.
#

ln -sfn "${RELEASE}" "${PUBLISH_ROOT}/current.new"
mv -Tf \
    "${PUBLISH_ROOT}/current.new" \
    "${PUBLISH_ROOT}/current"

#
# Apache's document root is /data/www.
#
# Make that another atomic symlink.
#

ln -sfn "${PUBLISH_ROOT}/current" /data/www.new
mv -Tf /data/www.new /data/www

echo
echo "=============================================="
echo " Update center generation successful"
echo "=============================================="
echo
echo "Release: ${RELEASE_ID}"
echo "Latest core:"
cat "${RELEASE}/latestCore.txt"
echo
```

 ### Why this is better

 The actual version-routing logic is **still upstream**.

 The upstream script calls:

```
generate-htaccess.sh
```

 with the dynamically discovered weekly/LTS releases.  GitHub+1

 So if Jenkins changes the way it interprets:

```
?version=2.555.3
```

 we aren't maintaining our own approximation.

 The only modifications we're making are:

 1. use our signing certificate
2. don't redirect downloads to Jenkins' mirror network
3. publish atomically
4. validate before publication

---

 # 3\. Apache

 `apache/Dockerfile`:

```
FROM httpd:2.4

COPY httpd.conf /usr/local/apache2/conf/httpd.conf
```

 `apache/httpd.conf`:

```
ServerName localhost

Listen 8080

LoadModule mpm_event_module modules/mod_mpm_event.so
LoadModule authz_core_module modules/mod_authz_core.so
LoadModule authz_host_module modules/mod_authz_host.so
LoadModule dir_module modules/mod_dir.so
LoadModule mime_module modules/mod_mime.so
LoadModule rewrite_module modules/mod_rewrite.so
LoadModule alias_module modules/mod_alias.so

DocumentRoot "/data/www"

<Directory "/data/www">
    Options FollowSymLinks Indexes

    AllowOverride FileInfo Indexes

    Require all granted
</Directory>

DirectoryIndex index.html

TypesConfig /etc/mime.types

ErrorLog /proc/self/fd/2
CustomLog /proc/self/fd/1 combined
```

 The `AllowOverride FileInfo` part is important because the upstream generator creates the version-routing `.htaccess`. The upstream project actually tests those generated rewrite rules in Apache.  GitHub

---

 # 4\. Compose

 `compose.yaml`:

```
services:

  generator:
    build:
      context: ./generator
      args:
        UPDATE_CENTER_VERSION: "3.18.3"

    environment:
      SECRET: /run/secrets/update-center

      CERTIFICATE_MINIMUM_VALID_DAYS: "30"

      # Upstream defaults to 3 hours.
      #
      # Increasing this is useful if you're generating from a private
      # environment and don't want every run to aggressively fetch
      # recent release information.
      RECENT_RELEASES_MAX_AGE_HOURS: "24"

    volumes:
      - www:/data/www
      - releases:/data/.releases
      - downloads:/data/downloads

      - ./secrets:/run/secrets/update-center:ro

    restart: "no"

  apache:
    build:
      context: ./apache

    ports:
      - "8080:8080"

    volumes:
      - www:/data/www:ro

    restart: unless-stopped

    depends_on:
      generator:
        condition: service_completed_successfully

volumes:
  www:
  releases:
  downloads:
```

---

 # 5\. Signing certificate

 The upstream generator supports signing and documents the `--key`, `--certificate`, and `--root-certificate` mechanism.  GitHub

 For your own update center:

```
mkdir -p secrets

openssl genrsa \
    -out secrets/update-center.key \
    4096
```

 Then:

```
openssl req \
    -new \
    -x509 \
    -sha256 \
    -days 3650 \
    -key secrets/update-center.key \
    -out secrets/update-center.cert \
    -subj "/C=US/O=Example/OU=Jenkins/CN=Jenkins Update Center"
```

 Protect the private key:

```
chmod 600 secrets/update-center.key
chmod 644 secrets/update-center.cert
```

 And:

```
echo "secrets/update-center.key" >> .gitignore
echo "data/" >> .gitignore
```

---

 # 6\. Build and run

 Build:

```
docker compose build
```

 Generate:

```
docker compose run --rm generator
```

 Then:

```
docker compose up -d apache
```

 Check:

```
curl -I \
    http://localhost:8080/update-center.json
```

 Then the important test:

```
curl -I \
    'http://localhost:8080/update-center.json?version=2.555.3'
```

 You should get a redirect to something resembling:

```
/dynamic-stable-2.xxx.x/update-center.json
```

 or:

```
/dynamic-2.xxx/update-center.json
```

 depending on which tier the current upstream generator selects.

 That behavior comes directly from the upstream `generate-htaccess.sh`, rather than our own implementation.  GitHub

---

 # 7\. The resulting site

 You should end up with essentially the upstream Jenkins layout:

```
/
├── .htaccess
├── index.html
├── tiers.json
├── uctest.json
│
├── update-center.json
├── update-center.actual.json
├── plugin-versions.json
├── release-history.json
├── plugin-documentation-urls.json
├── latestCore.txt
│
├── current/
│   ├── update-center.json
│   ├── update-center.actual.json
│   ├── plugin-versions.json
│   ├── release-history.json
│   ├── plugin-documentation-urls.json
│   ├── latestCore.txt
│   ├── latest/
│   └── updates -> ../updates
│
├── stable/
│
├── experimental/
│
├── dynamic-2.xxx/
│
├── dynamic-stable-2.xxx.x/
│
└── download/
    ├── plugins/
    └── war/
```

 That's the layout documented by the upstream project.  GitHub

---

 # 8\. There's one important distinction about downloads

 I want to call this out because it's easy to get confused here.

 The upstream generator **does download the plugin/WAR artifacts** when invoked with:

```
--downloads-directory
```

 and creates the corresponding download tree.  GitHub+1

 So this design is intended to be a **real mirror**, not merely a metadata proxy.

 However, the upstream generator's normal `.htaccess` deliberately sends those URLs to:

```
get.jenkins.io
updates.jenkins.io
```

 We remove those download redirects so Apache serves the files we generated locally.

 That's the crucial self-hosting modification.

---

 # 9\. Jenkins configuration

 On Jenkins, install:

```
$JENKINS_HOME/update-center-rootCAs/update-center.cert
```

 Then configure the update site:

```
https://jenkins-update-center.example.com/update-center.json
```

 The certificate needs to be trusted by Jenkins because we're signing the update-center JSON with our own certificate.

 **Do not disable signature checking.**

 The upstream project explicitly supports signing update-center output and documents installing the signing CA under `update-center-rootCAs`.  GitHub

---

 # 10\. Docker Jenkins / plugin CLI

 For Jenkins images using `jenkins-plugin-cli`, configure:

```
environment:
  JENKINS_UC: https://jenkins-update-center.example.com
  JENKINS_UC_EXPERIMENTAL: https://jenkins-update-center.example.com/experimental
  JENKINS_PLUGIN_INFO: https://jenkins-update-center.example.com/current/plugin-versions.json
  JENKINS_UC_DOWNLOAD_URL: https://jenkins-update-center.example.com/download/plugins
```

 The current Jenkins plugin manager supports a dedicated download URL specifically for mirrors.  GitHub

 This is useful because the plugin manager can override the plugin download location independently of the URLs embedded in the update-center metadata.

---

 # 11\. Automatic regeneration

 I would **not** put cron inside the generator container.

 Keep the generator a one-shot job:

```
generator
   │
   ├── generate
   ├── validate
   └── publish
```

 Then schedule it externally.

 For example, host cron:

```
0 3 * * * cd /opt/jenkins-update-center && docker compose run --rm generator
```

 That gives you:

```
03:00 UTC
   │
   ▼
generator starts
   │
   ├── fetch upstream metadata
   ├── discover tiers
   ├── download new artifacts
   ├── generate JSON
   ├── sign JSON
   ├── generate .htaccess
   ├── validate
   │
   ▼
atomic publish
   │
   ▼
Apache immediately serves new site
```

 The previous release remains available under:

```
/data/.releases/
```

 which is useful if we ever need rollback.

---

 ## One thing I would change before you actually expose this publicly

 There's one remaining issue I'd like to solve rather than sweep under the rug:

 **the upstream generator itself talks to Jenkins infrastructure—Artifactory, GitHub, usage statistics, reports, etc.—while generating the metadata.** The upstream README explicitly documents those information sources.  GitHub

 That's perfectly reasonable for a **connected mirror**, but if your eventual goal is:

 > "My Jenkins environment has no Internet access at all"

 then we need an additional **sync/import layer**.

 For a connected self-hosted mirror, the architecture above is appropriate:

```
                         Internet
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼
       Jenkins sources              Jenkins artifacts
             │                             │
             └──────────────┬──────────────┘
                            ▼
                    update-center2
                            │
                    signed metadata
                            │
                            ▼
                     your mirror
                            │
                            ▼
                         Jenkins
```

 For an **air-gapped** installation, I'd instead make it:

```
Internet-side machine
        │
        │ signed/exported snapshot
        ▼
   transfer media
        │
        ▼
Air-gapped update center
        │
        ▼
      Jenkins
```

 That's a separate problem and worth designing deliberately.

 ### The other thing I'd strongly recommend

 Before deploying this exact container, I'd add an automated test that spins up Apache and verifies the generated rules for several versions:

```
2.555.3
2.516.1
2.462.1
2.414.3
2.346.3
```

 and verifies that each request:

```
/update-center.json?version=X
```

 lands on the same tier that the upstream generator intended.

 The upstream project already has tests for its `generate-htaccess.sh` rules, so we can adapt those tests rather than inventing our own.  GitHub

 That would make this considerably more robust than the first prototype while keeping the actual Jenkins-specific logic upstream.
