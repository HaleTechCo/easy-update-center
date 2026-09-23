# Self-Hosted Jenkins Update Center (Core Stack) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two container images (`uc` generator/CLI and `web` Apache tier) and a Docker Compose stack that generate, sign, validate and serve a full Jenkins update center, with `?version=` tier routing and every upstream JSON output, in `online` and `airgapped` modes.

**Architecture:** The `uc` image runs the pinned update-center2 3.18.4 jar (built from source with one small patch) through a Python standard-library CLI. The CLI builds each release in a staging directory, validates it and publishes it by atomically swapping a `live` symlink on a shared volume. The `web` image serves `/data/live` with upstream's generated `.htaccess` for version routing, and redirects (or proxies) `/download/**` to get.jenkins.io or the internal Artifactory. No plugin binaries are stored.

**Tech Stack:** Python 3.14 (stdlib only; pytest for tests), Java 21 (Eclipse Temurin), update-center2 3.18.4, Apache httpd 2.4, OpenSSL 3.5, supercronic v0.2.49, Docker Compose v2, GNU make.

**Spec:** `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`. Read it before starting. Section references below (§N) point into it.

## Global Constraints

- update-center2 tag `update-center2-3.18.4`, commit `b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd`. The build fails if the commit differs or the patch doesn't apply.
- The only upstream change is `uc/patches/0001-popularity-configurable-and-optional.patch` (§7.4). Do not modify any other upstream file; `site/generate-htaccess.sh` is used unmodified.
- Python code uses the standard library only (no pip installs). Tests use pytest from the `python3-pytest` Ubuntu package.
- Everything runs in containers through `make`; the host needs only Docker, Compose v2 and GNU make. Makefiles export `MSYS_NO_PATHCONV=1` so paths survive Git Bash on Windows.
- The `uc` process runs as uid/gid 10001; the `web` process runs as `www-data` (uid 33). Files written to `/data` must be world-readable (umask 022).
- `PUBLIC_URL` is a bare origin (no path). Download URLs in generated JSON are `${PUBLIC_URL}/download/...`.
- The web image must never contain `/data/live` (§8.1). Every release always contains `.htdownload-map`, empty unless `DOWNLOAD_BACKEND=artifactory`.
- The generator never receives `--downloads-directory` or `--index-template-url`.
- The jar always receives `ARTIFACTORY_URL` with an explicit port (§10).
- Commit after every task, ending each message with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`. Work on branch `design/self-hosted-update-center` (or a feature branch cut from it).

## Facts verified during planning (2026-09-22)

The planning session checked these against real systems. Rely on them rather than re-deriving them:

- The patched 3.18.4 build passes all 45 upstream tests. `target/update-center2-3.18.4-bin/` is a flat directory of 29 jars, including `update-center2-3.18.4.jar` and `version-number-1.12.jar`.
- `eclipse-temurin:21-jre` is Ubuntu 26.04, with python3 3.14.4, pytest 9.0.2 (`python3-pytest`), OpenSSL 3.5.5 and tini available through apt. It ships without python3, unzip or jq.
- Upstream `generate-htaccess.sh`:
  - exits 1 when given no tiers;
  - with weekly tiers only, emits `/dynamic-stable-%{REQUEST_URI}?` and `"/dynamic-stable-/$1"`;
  - with stable tiers only, emits `/dynamic-%{REQUEST_URI}?`;
  - always ends with the five download `RewriteRule`s quoted in Task 6.
- `httpd -t` fails if `DocumentRoot` doesn't exist, and fails if a `RewriteMap txt:` file doesn't exist.
- With the web config in Task 15:
  - swapping the `live` symlink is picked up without a restart, and so is the `RewriteMap` behind it;
  - redirect `Location`s carry `PUBLIC_URL` (`UseCanonicalName On`);
  - `.ht*` returns 403.
- Tool installers at `https://updates.jenkins.io/updates/` are listed as `*.json.html` and `*.json` hrefs, and answer 307 to `mirrors.updates.jenkins.io`. The `.json` files start with `downloadService.post(`.
- supercronic v0.2.49 SHA-256 checksums:
  - `linux-amd64`: `a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1`
  - `linux-arm64`: `02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5`
- OpenSSL `x509 -noout -subject -enddate -fingerprint -sha256 -nameopt RFC2253 -dateopt iso_8601` prints `subject=CN=...,O=...`, `notAfter=2027-09-22 16:22:19Z` and `sha256 Fingerprint=2A:91:...`. `req -newkey` writes PKCS#8 (a PEM `PRIVATE KEY` block), which update-center2's signer accepts.
- `docker compose config --quiet` succeeds even when secret files are missing, and accepts `volume: {subpath: ...}`.
- `eclipse-temurin:21-jre` cannot run single-file Java source programs (`jdk.compiler` is missing), so `VersionTool` is compiled in the `jar-build` stage.
- `jenkins-plugin-cli` takes `--plugin-download-directory` (alias `-d`); there is no `--download-directory` option.

**The plan was executed before handover.** On 2026-09-22 every file was extracted from this document into a scratch tree, the images were built from it, and these all passed:
- the unit suite: `171 passed`;
- the web entrypoint checks: 7/7;
- the routing suite: `78 passed`;
- `it-airgapped`: driver `11 passed`, plus `jenkins-plugin-cli` exiting 0.

The integration run used real controllers on LTS 2.516.1 and 2.568.3 and weekly 2.583, with 8 seeded plugins. Jenkins 2.568.3 was routed to the generated `dynamic-stable-2.516.1` tier and installed through redirects into the fake Artifactory; the weekly installed through the authenticated proxy. `it-online` was not run, because it needs community credentials (spike S1). If a step here behaves differently for you, suspect the environment first, then the plan.

## File Structure

```
.gitignore                         ignore .env, data/, secrets/* (except README), test scratch
Makefile                           build · test-unit · test-routing · test · it-airgapped · it-online · it-full · upstream-diff · pki-init · secrets-placeholders
compose.yaml                       web + scheduler + uc (profile tools)
compose.airgapped.yaml             extra_hosts fast-fail for uc and scheduler
.env.online.example
.env.airgapped.example
README.md                          short overview, links to docs/operations.md
docs/operations.md                 install, PKI, Jenkins setup, operations, troubleshooting
docs/upgrading.md                  upstream upgrade procedure
docs/spikes/README.md              operator spike procedures and result log (S1, S2, S6, S7)
secrets/README.md                  which files go here
config/overlay/README.md           overlay examples
uc/
  Dockerfile                       jar-build → runtime → test stages
  patches/0001-popularity-configurable-and-optional.patch
  bin/uc                           launcher: exec python3 -m uc
  java/VersionTool.java            batch VersionNumber compare/sort/max (compiled into /opt/uc/tools)
  templates/index.html             neutral landing page
  templates/htaccess-static        zero-tier .htaccess
  uc/__init__.py
  uc/__main__.py                   python -m uc
  uc/errors.py                     UcError, ConfigError
  uc/model.py                      Tiers, Credentials
  uc/timeutil.py                   utcnow, iso, parse_iso
  uc/config.py                     Settings, load_settings, read_secret, with_explicit_port
  uc/versions.py                   VersionTool wrapper
  uc/pki.py                        init/rotate/show, cert_info, key_matches_cert, verify_chain, cert_der_b64
  uc/preflight.py                  check_public_url, check_signing, load_credentials
  uc/http.py                       get()
  uc/resources.py                  fetch_upstream, merge_overlay, prepare_resources, save_last_good
  uc/htaccess.py                   render_htaccess, strip_download_rules, fix_partial_tiers, absolute_targets
  uc/args.py                       build_argument_lines, write_arguments_file
  uc/jar.py                        jar_environment, java_command, run_jar, generate_tiers, generate_sites
  uc/aql.py                        RepoFile, Gav, to_gav, query_repository, classify, Listing
  uc/downloadmap.py                build_download_map, preferred_groups, write/read_download_map
  uc/layout.py                     site_dirs, finalize_layout, mirror_tool_installers
  uc/validate.py                   load_site_json, count_plugins, download_key, validate_release
  uc/publish.py                    RunLock, releases, staging, swap, prune, mark_failed, rollback
  uc/status.py                     read_status, write_status, update_status
  uc/doctor.py                     DoctorReport, ArtifactSource, analyze, run_doctor, format_report
  uc/generate.py                   Toolchain, generate()
  uc/schedule.py                   crontab_line, run_schedule
  uc/cli.py                        argparse entry point
web/
  Dockerfile
  conf/httpd.conf                  global config, site tree, headers, vhost 8080
  conf/uc-site.conf                download rewrite rules (included per vhost)
  conf/uc-tls.conf                 optional 8443 vhost
  entrypoint.sh                    mode defaults → -D defines, wait for first release, exec httpd
  healthcheck.sh
tests/
  support/                         shared helpers (sys.path'd by conftest files)
    sitefixture.py                 minimal generator-output builder
    fakejar.py                     stand-in for update-center2 in generate() tests
    httpstub.py                    threaded canned-response HTTP server
    archives.py                    in-memory tar builder
    ucsupport.py                   make_settings()
  unit/                            pytest, run in the uc-test image
  routing/                         web-tier tests (compose: fixtures, webs, stub backend, tester)
  integration/                     fake_artifactory.py, seed.py, jenkins.py, airgapped.yaml, online.yaml, tests
```

Delete these files from the prototype: `generator/`, `httpd/`, `docker-compose.yml`, `data/`, `secrets/how-to.md`, `secrets/update-center.crt`, `secrets/update-center.key` (the last two are empty placeholders). Keep `docs/01-05`, `todo.txt` and the user's `.env` (it is gitignored).

---

### Task 1: Repository cleanup, `uc` image with the patched generator, and the unit-test harness

**Files:**
- Delete: `generator/Dockerfile`, `generator/generate.sh`, `httpd/Dockerfile`, `httpd/httpd.conf`, `docker-compose.yml`, `data/downloads/.gitkeep`, `data/www/.gitkeep`, `secrets/how-to.md`, `secrets/update-center.crt`, `secrets/update-center.key`
- Create: `.gitignore` (replace), `Makefile`, `uc/Dockerfile`, `uc/patches/0001-popularity-configurable-and-optional.patch`, `uc/bin/uc`, `uc/java/VersionTool.java`, `uc/templates/.gitkeep`, `uc/uc/__init__.py`, `uc/uc/errors.py`, `uc/uc/versions.py`, `tests/unit/conftest.py`, `tests/support/__init__.py`
- Test: `tests/unit/test_versions.py`

**Interfaces:**
- Produces:
  - `uc.errors.UcError(Exception)` and `uc.errors.ConfigError(UcError)`
  - `uc.versions.VersionTool(jar_dir: Path, source: Path, java: str = "java")` with:
    - `compare_many(pairs: Sequence[tuple[str, str]]) -> list[int]` (each value is -1, 0 or 1)
    - `sort(versions: Iterable[str]) -> list[str]` (ascending)
    - `max_by_key(items: Iterable[tuple[str, str]]) -> dict[str, str]`
    - `VersionTool.from_settings(settings)`, added in Task 2
  - `uc.versions.VersionToolError(UcError)`
  - Make targets `build-uc` and `test-unit`, which honour `ARGS=` for extra pytest arguments
  - Image paths: `/opt/uc/generator/update-center2.jar` (a symlink to `update-center2-3.18.4.jar`) plus every dependency jar, `/opt/uc/site/generate-htaccess.sh`, `/opt/uc/resources/` (including `.uc-resources.json`), `/opt/uc/tools/VersionTool.class`, `/opt/uc/templates/`, `/opt/uc/python/uc/`, and `/usr/local/bin/uc`

- [ ] **Step 1: Remove the prototype files and replace `.gitignore`**

```bash
git rm -q -r generator httpd docker-compose.yml data secrets/how-to.md secrets/update-center.crt secrets/update-center.key
```

`.gitignore`:

```
# local configuration and secrets
.env
data/
secrets/*
!secrets/README.md
# integration-test scratch
tests/integration/.versions.env
tests/integration/.results/
```

- [ ] **Step 2: Add the upstream patch exactly as below**

`uc/patches/0001-popularity-configurable-and-optional.patch`:

```diff
diff --git a/src/main/java/io/jenkins/update_center/Popularities.java b/src/main/java/io/jenkins/update_center/Popularities.java
index 71e8a07..d55e520 100644
--- a/src/main/java/io/jenkins/update_center/Popularities.java
+++ b/src/main/java/io/jenkins/update_center/Popularities.java
@@ -1,14 +1,18 @@
 package io.jenkins.update_center;
 
 import com.alibaba.fastjson.JSON;
+import io.jenkins.update_center.util.Environment;
 
 import java.io.IOException;
 import java.net.URI;
 import java.net.http.HttpClient;
 import java.net.http.HttpRequest;
 import java.net.http.HttpResponse;
+import java.util.Collections;
 import java.util.Map;
 import java.util.function.Function;
+import java.util.logging.Level;
+import java.util.logging.Logger;
 import java.util.stream.Collectors;
 
 /**
@@ -21,8 +25,9 @@ import java.util.stream.Collectors;
  * validation in Jenkins due to the JSON normalization involved.
  */
 public class Popularities {
+    private static final Logger LOGGER = Logger.getLogger(Popularities.class.getName());
 
-    private static final String JSON_URL = "https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json";
+    private static final String JSON_URL = Environment.getString("POPULARITY_JSON_URL", "https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json");
     // or https://stats.jenkins.io/plugin-installation-trend/latestNumbers.json
 
     private static Popularities instance;
@@ -34,6 +39,11 @@ public class Popularities {
     }
 
     private static void initialize() throws IOException {
+        if (JSON_URL.isEmpty()) {
+            LOGGER.log(Level.WARNING, "POPULARITY_JSON_URL is empty, popularity data will not be included");
+            instance = new Popularities(Collections.emptyMap());
+            return;
+        }
         HttpRequest request = HttpRequest.newBuilder()
                 .uri(URI.create(JSON_URL))
                 .GET()
@@ -48,6 +58,9 @@ public class Popularities {
             Map<String, Integer> popularities = response.plugins.keySet().stream()
                     .collect(Collectors.toMap(Function.identity(), value -> Integer.valueOf(response.plugins.get(value))));
             instance = new Popularities(popularities);
+        } catch (IOException | RuntimeException e) {
+            LOGGER.log(Level.WARNING, "Failed to retrieve popularity data from " + JSON_URL + ", popularity data will not be included", e);
+            instance = new Popularities(Collections.emptyMap());
         } catch(InterruptedException e) {
             throw new IOException(e);
         }
```

The patch file must end with a newline. `git apply --check` in the Dockerfile rejects it if the whitespace is altered, so do not reformat the context lines, including the blank line that contains only a space after `package ...;`.

- [ ] **Step 3: Add the version helper, launcher and package skeleton**

`uc/java/VersionTool.java`:

```java
import hudson.util.VersionNumber;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Batch Jenkins version comparisons using the generator's own hudson.util.VersionNumber.
 * Reads tab-separated lines on stdin. Modes: compare (a TAB b -> -1|0|1), sort (v -> ascending),
 * max (key TAB v -> key TAB newest).
 */
public class VersionTool {
    private static int cmp(String a, String b) {
        return Integer.signum(new VersionNumber(a).compareTo(new VersionNumber(b)));
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: VersionTool compare|sort|max");
            System.exit(64);
        }
        List<String> lines = new ArrayList<>();
        try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            for (String line; (line = in.readLine()) != null; ) {
                if (!line.isEmpty()) {
                    lines.add(line);
                }
            }
        }
        StringBuilder out = new StringBuilder();
        switch (args[0]) {
            case "compare" -> {
                for (String line : lines) {
                    String[] p = line.split("\t", 2);
                    out.append(cmp(p[0], p[1])).append('\n');
                }
            }
            case "sort" -> lines.stream().sorted(VersionTool::cmp).forEach(v -> out.append(v).append('\n'));
            case "max" -> {
                Map<String, String> best = new LinkedHashMap<>();
                for (String line : lines) {
                    String[] p = line.split("\t", 2);
                    best.merge(p[0], p[1], (a, b) -> cmp(a, b) >= 0 ? a : b);
                }
                best.forEach((k, v) -> out.append(k).append('\t').append(v).append('\n'));
            }
            default -> {
                System.err.println("unknown mode: " + args[0]);
                System.exit(64);
            }
        }
        System.out.print(out);
    }
}
```

`uc/bin/uc`:

```sh
#!/bin/sh
exec python3 -m uc "$@"
```

`uc/uc/__init__.py`:

```python
"""Self-hosted Jenkins update center: generation, validation and publishing (see docs/superpowers/specs)."""
```

`uc/uc/errors.py`:

```python
"""Exception types shared by the uc CLI. Every expected failure is a UcError subclass."""


class UcError(Exception):
    """An expected failure: the CLI prints the message and exits 1."""


class ConfigError(UcError):
    """The environment does not describe a valid configuration."""
```

`uc/templates/.gitkeep`: empty file (the directory gets real templates in Tasks 6 and 9).

`tests/support/__init__.py`: empty file.

- [ ] **Step 4: Write the Dockerfile**

`uc/Dockerfile`:

```dockerfile
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
    UC2_VERSION=${UC2_VERSION}+popularity-patch
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
```

- [ ] **Step 5: Write the Makefile**

`Makefile` (recipe lines are indented with a TAB):

```make
# Local build and test entry points (spec §12, §13). Needs Docker with Compose v2 and GNU make.
export MSYS_NO_PATHCONV := 1

UC_IMAGE ?= jenkins-uc/uc:local
UC_TEST_IMAGE ?= jenkins-uc/uc-test:local
WEB_IMAGE ?= jenkins-uc/web:local
export UC_IMAGE UC_TEST_IMAGE WEB_IMAGE

# Unit tests read the Python and templates straight from the working tree
# (VersionTool.java is compiled into the image, so changing it needs `make build-uc`).
SRC_MOUNTS = -v "$(CURDIR)/tests:/tests:ro" \
             -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" \
             -v "$(CURDIR)/uc/templates:/opt/uc/templates:ro"

.PHONY: build build-uc test-unit

build: build-uc

build-uc:
	docker build --target runtime -t $(UC_IMAGE) uc
	docker build --target test -t $(UC_TEST_IMAGE) uc

test-unit: build-uc
	docker run --rm $(SRC_MOUNTS) $(UC_TEST_IMAGE) -q /tests/unit $(ARGS)
```

- [ ] **Step 6: Write the failing tests**

`tests/unit/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))
```

`tests/unit/test_versions.py`:

```python
import zipfile
from pathlib import Path

from uc.versions import VersionTool

APP = Path("/opt/uc")


def tool() -> VersionTool:
    return VersionTool(APP / "generator", APP / "tools")


def test_compare_uses_jenkins_semantics():
    pairs = [("2.528.1", "2.540"), ("1.10", "1.9"), ("2.0", "2.0"), ("1.0-beta-1", "1.0")]
    assert tool().compare_many(pairs) == [-1, 1, 0, -1]


def test_sort_is_numeric_not_lexical():
    assert tool().sort(["2.540", "2.528.1", "1.10", "1.9", "2.528.10"]) == [
        "1.9", "1.10", "2.528.1", "2.528.10", "2.540",
    ]


def test_max_by_key_keeps_newest_per_key():
    assert tool().max_by_key([("git", "5.2.0"), ("git", "5.10.1"), ("cred", "1.0")]) == {
        "git": "5.10.1",
        "cred": "1.0",
    }


def test_empty_input_returns_without_running_java():
    assert VersionTool(Path("/nonexistent"), Path("/nonexistent")).compare_many([]) == []


def test_generator_jar_is_pinned_and_patched():
    jar = APP / "generator" / "update-center2.jar"
    assert jar.resolve().name == "update-center2-3.18.4.jar"
    with zipfile.ZipFile(jar) as archive:
        compiled = archive.read("io/jenkins/update_center/Popularities.class")
    assert b"POPULARITY_JSON_URL" in compiled


def test_bundled_resources_are_present():
    assert (APP / "resources" / "warnings.json").is_file()
    assert (APP / "resources" / ".uc-resources.json").is_file()
    assert (APP / "site" / "generate-htaccess.sh").is_file()
```

- [ ] **Step 7: Run the tests and watch them fail**

Run: `make test-unit`
Expected: the image builds (the first build runs Maven and upstream's tests, which takes a few minutes), then the tests fail with `ModuleNotFoundError: No module named 'uc.versions'`.

- [ ] **Step 8: Implement `uc/uc/versions.py`**

```python
"""Jenkins version comparisons through the generator's own hudson.util.VersionNumber (VersionTool.java)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from .errors import UcError

if TYPE_CHECKING:
    from .config import Settings


class VersionToolError(UcError):
    pass


class VersionTool:
    """Runs the VersionTool class compiled at image build time (tool_dir) against the generator jars."""

    def __init__(self, jar_dir: Path, tool_dir: Path, java: str = "java") -> None:
        self._cmd = [java, "-cp", f"{jar_dir}/*:{tool_dir}", "VersionTool"]

    def _run(self, mode: str, lines: Sequence[str]) -> list[str]:
        if not lines:
            return []
        for line in lines:
            if "\n" in line:
                raise VersionToolError(f"version input contains a newline: {line!r}")
        proc = subprocess.run(
            [*self._cmd, mode], input="\n".join(lines) + "\n", capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise VersionToolError(f"VersionTool {mode} failed: {proc.stderr.strip()}")
        return proc.stdout.splitlines()

    def compare_many(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        return [int(x) for x in self._run("compare", [f"{a}\t{b}" for a, b in pairs])]

    def sort(self, versions: Iterable[str]) -> list[str]:
        return self._run("sort", list(versions))

    def max_by_key(self, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in self._run("max", [f"{k}\t{v}" for k, v in items]):
            key, _, value = line.partition("\t")
            result[key] = value
        return result

    @classmethod
    def from_settings(cls, settings: "Settings") -> "VersionTool":
        return cls(settings.jar_dir, settings.version_tool_dir)
```

- [ ] **Step 9: Run the tests and confirm they pass**

Run: `make test-unit`
Expected: `6 passed`.

- [ ] **Step 10: Commit**

```bash
git add -A .gitignore Makefile uc tests
git commit -m "Build uc image with pinned, patched update-center2 and test harness

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Settings (`config.py`), shared types and test support

**Files:**
- Create: `uc/uc/config.py`, `uc/uc/model.py`, `uc/uc/timeutil.py`, `tests/support/ucsupport.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_timeutil.py`

**Interfaces:**
- Consumes: `uc.errors.ConfigError`
- Produces:
  - `uc.config.Settings` (frozen dataclass) with fields:
    - `mode`, `public_url`, `uc_id`, `generate_schedule`
    - `resources_source`, `resources_ref`, `resources_url`, `resources_sha256`
    - `popularity_json_url`, `tool_installers_url`
    - `artifactory_url`, `artifactory_url_with_port`, `artifactory_api_url`, `artifactory_repository`
    - `download_backend`, `download_backend_url`, `download_access`
    - `retain_releases: int`, `min_plugins: int`, `min_plugin_ratio: float`
    - `certificate_minimum_valid_days: int`, `signing_cert_days: int`, `stale_after_days: int`
    - `allow_local_public_url: bool`, `extra_args: tuple[str, ...]`
    - `data_dir`, `overlay_dir`, `app_dir` (all `Path`)
    - `signing_key_file`, `signing_cert_file`, `root_ca_file`, `artifactory_username_file`, `artifactory_password_file`, `github_username_file`, `github_token_file` (all `Path`)

    and properties `downloads_root_url`, `jar_dir`, `jar`, `htaccess_script`, `bundled_resources`, `templates_dir`, `version_tool_dir`, `cache_dir`
  - `uc.config.load_settings(env: Mapping[str, str] | None = None) -> Settings`
  - `uc.config.read_secret(path: Path) -> str | None`
  - `uc.config.with_explicit_port(url: str) -> str` and `uc.config.with_trailing_slash(url: str) -> str`
  - constants `UPSTREAM_POPULARITY_URL`, `UPSTREAM_TOOL_INSTALLERS_URL`, `UPSTREAM_MIRROR_URL`, `PUBLIC_ARTIFACTORY_URL`
  - `uc.model.Tiers(weekly: tuple[str, ...] = (), stable: tuple[str, ...] = ())` with property `empty`
  - `uc.model.Credentials(artifactory_username, artifactory_password, github_username, github_password, github_placeholder: bool)`
  - `uc.timeutil.utcnow() -> datetime` (aware UTC, whole seconds), `iso(moment) -> str` (`YYYY-MM-DDTHH:MM:SSZ`), `parse_iso(value) -> datetime`
  - `tests/support/ucsupport.make_settings(tmp_path: Path, secrets: Path, **env) -> Settings`, which defaults to air-gapped with a fake Artifactory at `http://artifactory:8081/artifactory/`, repo `jenkins`, and data/overlay under `tmp_path`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_config.py`:

```python
from pathlib import Path

import pytest

from uc.config import load_settings, read_secret, with_explicit_port
from uc.errors import ConfigError

AIRGAPPED = {
    "MODE": "airgapped",
    "PUBLIC_URL": "https://uc.example.com",
    "ARTIFACTORY_URL": "https://artifactory.corp/artifactory",
    "ARTIFACTORY_REPOSITORY": "jenkins-local",
}


def test_online_defaults():
    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc.example.com/"})
    assert s.public_url == "https://uc.example.com"
    assert s.downloads_root_url == "https://uc.example.com/download"
    assert s.artifactory_url == "https://repo.jenkins-ci.org/"
    assert s.artifactory_url_with_port == "https://repo.jenkins-ci.org:443/"
    assert s.artifactory_api_url == "https://repo.jenkins-ci.org:443/api/"
    assert s.artifactory_repository == "releases"
    assert s.download_backend == "mirror"
    assert s.download_backend_url == "https://get.jenkins.io"
    assert s.download_access == "redirect"
    assert s.resources_source == "github"
    assert s.resources_ref == "master"
    assert s.popularity_json_url.startswith("https://raw.githubusercontent.com/")
    assert s.tool_installers_url == "https://updates.jenkins.io/updates/"
    assert (s.retain_releases, s.min_plugins, s.min_plugin_ratio) == (5, 1, 0.9)
    assert (s.certificate_minimum_valid_days, s.signing_cert_days, s.stale_after_days) == (30, 365, 30)
    assert s.generate_schedule == "0 */6 * * *"
    assert s.uc_id == "default"
    assert s.extra_args == ()
    assert s.signing_key_file == Path("/run/secrets/update-center.key")
    assert s.root_ca_file == Path("/run/secrets/root-ca.crt")
    assert s.data_dir == Path("/data")
    assert s.jar == Path("/opt/uc/generator/update-center2.jar")
    assert s.cache_dir == Path("/data/cache/artifactory")


def test_airgapped_defaults():
    s = load_settings(AIRGAPPED)
    assert s.artifactory_url == "https://artifactory.corp/artifactory/"
    assert s.artifactory_url_with_port == "https://artifactory.corp:443/artifactory/"
    assert s.download_backend == "artifactory"
    assert s.download_backend_url == "https://artifactory.corp/artifactory/jenkins-local"
    assert s.resources_source == "bundled"
    assert s.popularity_json_url == ""
    assert s.tool_installers_url == ""


def test_airgapped_requires_artifactory():
    with pytest.raises(ConfigError, match="ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY"):
        load_settings({"MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com"})


@pytest.mark.parametrize("mode", ["", "offline"])
def test_mode_is_required_and_validated(mode):
    with pytest.raises(ConfigError, match="MODE must be one of online, airgapped"):
        load_settings({"MODE": mode, "PUBLIC_URL": "https://uc.example.com"})


def test_public_url_is_required():
    with pytest.raises(ConfigError, match="PUBLIC_URL must be set"):
        load_settings({"MODE": "online"})


def test_explicit_empty_popularity_url_disables_it_online():
    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "POPULARITY_JSON_URL": ""})
    assert s.popularity_json_url == ""


def test_resources_url_required_for_url_source():
    with pytest.raises(ConfigError, match="RESOURCES_URL must be set"):
        load_settings({**AIRGAPPED, "RESOURCES_SOURCE": "url"})


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("MIN_PLUGIN_RATIO", "1.5", "between 0 and 1"),
        ("RETAIN_RELEASES", "abc", "must be an integer"),
        ("RETAIN_RELEASES", "0", "must be >= 1"),
        ("DOWNLOAD_BACKEND", "s3", "DOWNLOAD_BACKEND must be one of"),
        ("DOWNLOAD_ACCESS", "cache", "DOWNLOAD_ACCESS must be one of"),
        ("ALLOW_LOCAL_PUBLIC_URL", "maybe", "true or false"),
        ("ARTIFACTORY_REPOSITORY", 'a"b', "unsupported characters"),
    ],
)
def test_invalid_values_are_rejected(name, value, message):
    with pytest.raises(ConfigError, match=message):
        load_settings({**AIRGAPPED, name: value})


def test_backend_url_override_and_extra_args():
    s = load_settings({
        **AIRGAPPED,
        "DOWNLOAD_BACKEND_URL": "https://dl.corp/jenkins/",
        "UC_EXTRA_ARGS": "--max-plugins 20",
    })
    assert s.download_backend_url == "https://dl.corp/jenkins"
    assert s.extra_args == ("--max-plugins", "20")


def test_secret_paths_follow_secrets_dir_and_overrides():
    s = load_settings({**AIRGAPPED, "SECRETS_DIR": "/pki", "GITHUB_TOKEN_FILE": "/other/token"})
    assert s.signing_cert_file == Path("/pki/update-center.cert")
    assert s.artifactory_password_file == Path("/pki/artifactory_password")
    assert s.github_token_file == Path("/other/token")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://a.b/x/", "https://a.b:443/x/"),
        ("http://a.b/", "http://a.b:80/"),
        ("https://a.b:8443/", "https://a.b:8443/"),
        ("http://[::1]/", "http://[::1]:80/"),
    ],
)
def test_with_explicit_port(url, expected):
    assert with_explicit_port(url) == expected


def test_credentials_in_artifactory_url_are_rejected():
    with pytest.raises(ConfigError, match="credentials"):
        load_settings({**AIRGAPPED, "ARTIFACTORY_URL": "https://u:p@artifactory.corp/"})  # pragma: allowlist secret


def test_read_secret(tmp_path):
    assert read_secret(tmp_path / "missing") is None
    (tmp_path / "empty").write_text("\n")
    assert read_secret(tmp_path / "empty") is None
    (tmp_path / "value").write_text("s3cret\n")
    assert read_secret(tmp_path / "value") == "s3cret"
```

`tests/unit/test_timeutil.py`:

```python
import datetime as dt

from uc.timeutil import iso, parse_iso, utcnow


def test_iso_roundtrip():
    moment = dt.datetime(2026, 9, 22, 3, 4, 5, tzinfo=dt.timezone.utc)
    assert iso(moment) == "2026-09-22T03:04:05Z"
    assert parse_iso("2026-09-22T03:04:05Z") == moment
    assert parse_iso("2026-09-22T05:04:05+02:00") == moment


def test_utcnow_is_aware_and_whole_seconds():
    now = utcnow()
    assert now.tzinfo is not None and now.microsecond == 0
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'config or timeutil'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.config'`.

- [ ] **Step 3: Implement the modules**

`uc/uc/model.py`:

```python
"""Small value types shared between modules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tiers:
    """Tier list from `update-center2 --dynamic-tier-list-file`, in upstream (ascending) order."""

    weekly: tuple[str, ...] = ()
    stable: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.weekly and not self.stable


@dataclass(frozen=True)
class Credentials:
    artifactory_username: str
    artifactory_password: str
    github_username: str
    github_password: str
    github_placeholder: bool
```

`uc/uc/timeutil.py`:

```python
"""UTC timestamps in the one format used everywhere: YYYY-MM-DDTHH:MM:SSZ."""
from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
```

`uc/uc/config.py`:

```python
"""Settings from environment variables with per-mode defaults (spec §7.1, §11)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

MODES = ("online", "airgapped")
BACKENDS = ("mirror", "artifactory")
ACCESS_MODES = ("redirect", "proxy")
RESOURCE_SOURCES = ("github", "url", "bundled")

PUBLIC_ARTIFACTORY_URL = "https://repo.jenkins-ci.org/"
UPSTREAM_MIRROR_URL = "https://get.jenkins.io"
UPSTREAM_POPULARITY_URL = (
    "https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/"
    "plugin-installation-trend/latestNumbers.json"
)
UPSTREAM_TOOL_INSTALLERS_URL = "https://updates.jenkins.io/updates/"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Settings:
    mode: str
    public_url: str
    uc_id: str
    generate_schedule: str
    resources_source: str
    resources_ref: str
    resources_url: str
    resources_sha256: str
    popularity_json_url: str
    tool_installers_url: str
    artifactory_url: str
    artifactory_url_with_port: str
    artifactory_api_url: str
    artifactory_repository: str
    download_backend: str
    download_backend_url: str
    download_access: str
    retain_releases: int
    min_plugins: int
    min_plugin_ratio: float
    certificate_minimum_valid_days: int
    signing_cert_days: int
    stale_after_days: int
    allow_local_public_url: bool
    extra_args: tuple[str, ...]
    data_dir: Path
    overlay_dir: Path
    app_dir: Path
    signing_key_file: Path
    signing_cert_file: Path
    root_ca_file: Path
    artifactory_username_file: Path
    artifactory_password_file: Path
    github_username_file: Path
    github_token_file: Path

    @property
    def downloads_root_url(self) -> str:
        return f"{self.public_url}/download"

    @property
    def jar_dir(self) -> Path:
        return self.app_dir / "generator"

    @property
    def jar(self) -> Path:
        return self.jar_dir / "update-center2.jar"

    @property
    def htaccess_script(self) -> Path:
        return self.app_dir / "site" / "generate-htaccess.sh"

    @property
    def bundled_resources(self) -> Path:
        return self.app_dir / "resources"

    @property
    def templates_dir(self) -> Path:
        return self.app_dir / "templates"

    @property
    def version_tool_dir(self) -> Path:
        return self.app_dir / "tools"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache" / "artifactory"


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name, "").strip()
    return value if value else default


def _choice(env: Mapping[str, str], name: str, default: str, choices: Sequence[str]) -> str:
    value = _get(env, name, default)
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)} (got {value!r})")
    return value


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = _get(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {value})")
    return value


def _ratio(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _get(env, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number (got {raw!r})") from None
    if not 0 <= value <= 1:
        raise ConfigError(f"{name} must be between 0 and 1 (got {value})")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name, "true" if default else "false").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false (got {raw!r})")


def with_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def with_explicit_port(url: str) -> str:
    """Add :443/:80 when missing, so update-center2's Authenticator port check matches (spec §10)."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ConfigError("URLs must not contain credentials; use the secret files instead")
    if parts.port is not None or parts.scheme not in ("http", "https"):
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = 443 if parts.scheme == "https" else 80
    return urlunsplit((parts.scheme, f"{host}:{port}", parts.path, parts.query, parts.fragment))


def read_secret(path: Path) -> str | None:
    """Contents of a secret file, stripped; None when the file is missing or empty."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    mode = _choice(env, "MODE", "", MODES)
    online = mode == "online"

    public_url = _get(env, "PUBLIC_URL").rstrip("/")
    if not public_url:
        raise ConfigError("PUBLIC_URL must be set")

    artifactory_url = _get(env, "ARTIFACTORY_URL", PUBLIC_ARTIFACTORY_URL if online else "")
    repository = _get(env, "ARTIFACTORY_REPOSITORY", "releases" if online else "")
    if not artifactory_url or not repository:
        raise ConfigError("ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY must be set in airgapped mode")
    if not _REPOSITORY_RE.match(repository):
        raise ConfigError(f"ARTIFACTORY_REPOSITORY contains unsupported characters: {repository!r}")
    artifactory_url = with_trailing_slash(artifactory_url)
    artifactory_url_with_port = with_explicit_port(artifactory_url)
    api_url = with_trailing_slash(_get(env, "ARTIFACTORY_API_URL", artifactory_url_with_port + "api/"))

    backend = _choice(env, "DOWNLOAD_BACKEND", "mirror" if online else "artifactory", BACKENDS)
    default_backend_url = UPSTREAM_MIRROR_URL if backend == "mirror" else artifactory_url + repository
    backend_url = _get(env, "DOWNLOAD_BACKEND_URL", default_backend_url).rstrip("/")

    resources_source = _choice(
        env, "RESOURCES_SOURCE", "github" if online else "bundled", RESOURCE_SOURCES
    )
    resources_url = _get(env, "RESOURCES_URL")
    if resources_source == "url" and not resources_url:
        raise ConfigError("RESOURCES_URL must be set when RESOURCES_SOURCE=url")

    secrets_dir = Path(_get(env, "SECRETS_DIR", "/run/secrets"))

    def secret(var: str, name: str) -> Path:
        return Path(_get(env, var, str(secrets_dir / name)))

    return Settings(
        mode=mode,
        public_url=public_url,
        uc_id=_get(env, "UC_ID", "default"),
        generate_schedule=_get(env, "GENERATE_SCHEDULE", "0 */6 * * *"),
        resources_source=resources_source,
        resources_ref=_get(env, "RESOURCES_REF", "master"),
        resources_url=resources_url,
        resources_sha256=_get(env, "RESOURCES_SHA256").lower(),
        # For these two an explicitly empty value means "disabled", so unset and empty differ.
        popularity_json_url=env.get(
            "POPULARITY_JSON_URL", UPSTREAM_POPULARITY_URL if online else ""
        ).strip(),
        tool_installers_url=env.get(
            "TOOL_INSTALLERS_URL", UPSTREAM_TOOL_INSTALLERS_URL if online else ""
        ).strip(),
        artifactory_url=artifactory_url,
        artifactory_url_with_port=artifactory_url_with_port,
        artifactory_api_url=api_url,
        artifactory_repository=repository,
        download_backend=backend,
        download_backend_url=backend_url,
        download_access=_choice(env, "DOWNLOAD_ACCESS", "redirect", ACCESS_MODES),
        retain_releases=_int(env, "RETAIN_RELEASES", 5, minimum=1),
        min_plugins=_int(env, "MIN_PLUGINS", 1),
        min_plugin_ratio=_ratio(env, "MIN_PLUGIN_RATIO", 0.9),
        certificate_minimum_valid_days=_int(env, "CERTIFICATE_MINIMUM_VALID_DAYS", 30),
        signing_cert_days=_int(env, "SIGNING_CERT_DAYS", 365, minimum=1),
        stale_after_days=_int(env, "STALE_AFTER_DAYS", 30),
        allow_local_public_url=_bool(env, "ALLOW_LOCAL_PUBLIC_URL", False),
        extra_args=tuple(_get(env, "UC_EXTRA_ARGS").split()),
        data_dir=Path(_get(env, "UC_DATA_DIR", "/data")),
        overlay_dir=Path(_get(env, "UC_OVERLAY_DIR", "/config/overlay")),
        app_dir=Path(_get(env, "UC_APP_DIR", "/opt/uc")),
        signing_key_file=secret("SIGNING_KEY_FILE", "update-center.key"),
        signing_cert_file=secret("SIGNING_CERT_FILE", "update-center.cert"),
        root_ca_file=secret("ROOT_CA_FILE", "root-ca.crt"),
        artifactory_username_file=secret("ARTIFACTORY_USERNAME_FILE", "artifactory_username"),
        artifactory_password_file=secret("ARTIFACTORY_PASSWORD_FILE", "artifactory_password"),
        github_username_file=secret("GITHUB_USERNAME_FILE", "github_username"),
        github_token_file=secret("GITHUB_TOKEN_FILE", "github_token"),
    )
```

`tests/support/ucsupport.py`:

```python
"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path."""
from __future__ import annotations

from pathlib import Path

from uc.config import Settings, load_settings


def make_settings(tmp_path: Path, secrets: Path, **env: str) -> Settings:
    base = {
        "MODE": "airgapped",
        "PUBLIC_URL": "https://uc.example.com",
        "ARTIFACTORY_URL": "http://artifactory:8081/artifactory/",
        "ARTIFACTORY_REPOSITORY": "jenkins",
        "SECRETS_DIR": str(secrets),
        "UC_DATA_DIR": str(tmp_path / "data"),
        "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
    }
    base.update(env)
    return load_settings(base)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'config or timeutil'"`
Expected: `25 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc tests
git commit -m "Add settings with per-mode defaults and shared types

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Signing PKI (`pki.py`)

**Files:**
- Create: `uc/uc/pki.py`
- Modify: `tests/unit/conftest.py` (add the session PKI and `secrets_dir` fixtures)
- Test: `tests/unit/test_pki.py`

**Interfaces:**
- Consumes: `uc.errors.UcError`
- Produces:
  - constants `ROOT_KEY = "root-ca.key"`, `ROOT_CERT = "root-ca.crt"`, `SIGNING_KEY = "update-center.key"`, `SIGNING_CERT = "update-center.cert"`
  - `PkiError(UcError)`
  - `CertInfo(subject: str, not_after: datetime, fingerprint_sha256: str)` with `days_left(now) -> int`
  - `cert_info(cert: Path) -> CertInfo`
  - `key_matches_cert(key: Path, cert: Path) -> bool`
  - `verify_chain(root: Path, cert: Path) -> bool`
  - `cert_der_b64(cert: Path) -> str`
  - `init_pki(out_dir: Path, *, root_days=3650, signing_days=365, org="Jenkins Update Center") -> None`, which writes `out_dir/root/root-ca.key`, `out_dir/root-ca.crt`, `out_dir/update-center.key` and `out_dir/update-center.cert`
  - `rotate_signing(root_key: Path, root_cert: Path, out_dir: Path, *, days=365, org=...) -> None`, which keeps the previous pair as `*.old`
  - conftest fixtures:
    - `pki_material` (session): a directory from `init_pki(root_days=60, signing_days=45)`
    - `secrets_dir` (function): a directory holding copies of the signing key, cert and root CA, plus `artifactory_username` = `uc` and `artifactory_password` = `secret`

- [ ] **Step 1: Add the session PKI fixtures**

`tests/unit/conftest.py` (replaces the Task 1 version):

```python
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))


@pytest.fixture(scope="session")
def pki_material(tmp_path_factory):
    from uc.pki import init_pki

    directory = tmp_path_factory.mktemp("pki")
    init_pki(directory, root_days=60, signing_days=45)
    return directory


@pytest.fixture
def secrets_dir(tmp_path, pki_material):
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name in ("update-center.key", "update-center.cert", "root-ca.crt"):
        shutil.copy(pki_material / name, directory / name)
    (directory / "artifactory_username").write_text("uc\n")
    (directory / "artifactory_password").write_text("secret\n")
    return directory
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_pki.py`:

```python
import base64
import datetime as dt
import shutil
import subprocess

import pytest

from uc.pki import (
    PkiError,
    cert_der_b64,
    cert_info,
    init_pki,
    key_matches_cert,
    rotate_signing,
    verify_chain,
)


def test_init_creates_a_valid_chain(pki_material):
    assert verify_chain(pki_material / "root-ca.crt", pki_material / "update-center.cert")
    assert key_matches_cert(pki_material / "update-center.key", pki_material / "update-center.cert")
    assert (pki_material / "root" / "root-ca.key").is_file()
    assert not (pki_material / "root-ca.key").exists()
    assert (pki_material / "update-center.key").stat().st_mode & 0o777 == 0o600
    assert (pki_material / "root" / "root-ca.key").stat().st_mode & 0o777 == 0o600


def test_cert_info(pki_material):
    info = cert_info(pki_material / "update-center.cert")
    assert "CN=Jenkins Update Center Signing" in info.subject
    assert 43 <= info.days_left(dt.datetime.now(dt.timezone.utc)) <= 45
    assert len(info.fingerprint_sha256.split(":")) == 32


def test_signing_cert_is_not_a_ca_and_root_is(pki_material):
    def text(name):
        return subprocess.run(
            ["openssl", "x509", "-in", str(pki_material / name), "-noout", "-text"],
            capture_output=True, text=True, check=True,
        ).stdout

    assert "CA:FALSE" in text("update-center.cert")
    assert "Digital Signature" in text("update-center.cert")
    assert "CA:TRUE, pathlen:0" in text("root-ca.crt")


def test_init_refuses_to_overwrite(pki_material):
    with pytest.raises(PkiError, match="refusing to overwrite"):
        init_pki(pki_material)


def test_org_with_slash_is_rejected(tmp_path):
    with pytest.raises(PkiError, match="org"):
        init_pki(tmp_path / "x", org="a/b")


def test_rotate_issues_a_new_cert_under_the_same_root(pki_material, tmp_path):
    work = tmp_path / "pki"
    shutil.copytree(pki_material, work)
    before = cert_info(work / "update-center.cert").fingerprint_sha256
    rotate_signing(work / "root" / "root-ca.key", work / "root-ca.crt", work, days=5)
    assert cert_info(work / "update-center.cert").fingerprint_sha256 != before
    assert verify_chain(work / "root-ca.crt", work / "update-center.cert")
    assert key_matches_cert(work / "update-center.key", work / "update-center.cert")
    assert (work / "update-center.cert.old").is_file()
    assert (work / "update-center.key.old").is_file()


def test_cert_der_b64_matches_openssl(pki_material):
    der = subprocess.run(
        ["openssl", "x509", "-in", str(pki_material / "update-center.cert"), "-outform", "DER"],
        capture_output=True, check=True,
    ).stdout
    assert cert_der_b64(pki_material / "update-center.cert") == base64.b64encode(der).decode()


def test_key_mismatch_is_detected(pki_material, tmp_path):
    other = tmp_path / "other.key"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(other)],
        check=True, capture_output=True,
    )
    assert not key_matches_cert(other, pki_material / "update-center.cert")


def test_cert_info_on_garbage_raises(tmp_path):
    bad = tmp_path / "bad.crt"
    bad.write_text("not a certificate")
    with pytest.raises(PkiError):
        cert_info(bad)
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k pki"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.pki'`.

- [ ] **Step 4: Implement `uc/uc/pki.py`**

```python
"""Signing PKI (spec §9.1): an offline root CA and a short-lived signing certificate, via openssl."""
from __future__ import annotations

import base64
import datetime as dt
import secrets
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import UcError

ROOT_KEY = "root-ca.key"
ROOT_CERT = "root-ca.crt"
SIGNING_KEY = "update-center.key"
SIGNING_CERT = "update-center.cert"
DEFAULT_ORG = "Jenkins Update Center"

_EXTENSIONS = """\
[req]
distinguished_name = dn
[dn]
[v3_root]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[v3_signing]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


class PkiError(UcError):
    pass


@dataclass(frozen=True)
class CertInfo:
    subject: str
    not_after: dt.datetime
    fingerprint_sha256: str

    def days_left(self, now: dt.datetime) -> int:
        return (self.not_after - now).days


def _openssl(*args: str) -> str:
    proc = subprocess.run(["openssl", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise PkiError(f"openssl {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


def cert_info(cert: Path) -> CertInfo:
    out = _openssl(
        "x509", "-in", str(cert), "-noout", "-subject", "-enddate", "-fingerprint", "-sha256",
        "-nameopt", "RFC2253", "-dateopt", "iso_8601",
    )
    fields: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    try:
        not_after = dt.datetime.strptime(fields["notAfter"], "%Y-%m-%d %H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        return CertInfo(fields["subject"], not_after, fields["sha256 Fingerprint"])
    except (KeyError, ValueError) as exc:
        raise PkiError(f"could not parse certificate {cert}: {out!r}") from exc


def key_matches_cert(key: Path, cert: Path) -> bool:
    return _openssl("pkey", "-in", str(key), "-pubout") == _openssl(
        "x509", "-in", str(cert), "-noout", "-pubkey"
    )


def verify_chain(root: Path, cert: Path) -> bool:
    proc = subprocess.run(
        ["openssl", "verify", "-CAfile", str(root), str(cert)], capture_output=True, text=True
    )
    return proc.returncode == 0


def cert_der_b64(cert: Path) -> str:
    """Base64 DER, as update-center2 embeds the signer in signature.certificates[0]."""
    der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
    return base64.b64encode(der).decode("ascii")


def _check_org(org: str) -> None:
    if not org or "/" in org or "=" in org:
        raise PkiError(f"org must be non-empty and contain no '/' or '=' (got {org!r})")


def _refuse_existing(*paths: Path) -> None:
    existing = [str(p) for p in paths if p.exists()]
    if existing:
        raise PkiError(f"refusing to overwrite existing files: {', '.join(existing)}")


def _issue_signing(
    root_key: Path, root_cert: Path, out_dir: Path, days: int, org: str, cnf: Path
) -> None:
    key, cert = out_dir / SIGNING_KEY, out_dir / SIGNING_CERT
    csr = cnf.parent / "signing.csr"
    _openssl(
        "req", "-new", "-config", str(cnf), "-newkey", "rsa:4096", "-nodes", "-sha256",
        "-subj", f"/O={org}/CN={org} Signing", "-keyout", str(key), "-out", str(csr),
    )
    key.chmod(0o600)
    _openssl(
        "x509", "-req", "-in", str(csr), "-CA", str(root_cert), "-CAkey", str(root_key),
        "-set_serial", "0x" + secrets.token_hex(16), "-days", str(days), "-sha256",
        "-extfile", str(cnf), "-extensions", "v3_signing", "-out", str(cert),
    )


def init_pki(
    out_dir: Path, *, root_days: int = 3650, signing_days: int = 365, org: str = DEFAULT_ORG
) -> None:
    """Create root/root-ca.key, root-ca.crt, update-center.key and update-center.cert in out_dir."""
    _check_org(org)
    root_dir = out_dir / "root"
    root_key, root_cert = root_dir / ROOT_KEY, out_dir / ROOT_CERT
    _refuse_existing(root_key, root_cert, out_dir / SIGNING_KEY, out_dir / SIGNING_CERT)
    root_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / "ext.cnf"
        cnf.write_text(_EXTENSIONS)
        _openssl(
            "req", "-x509", "-new", "-config", str(cnf), "-extensions", "v3_root",
            "-newkey", "rsa:4096", "-nodes", "-sha256", "-days", str(root_days),
            "-subj", f"/O={org}/CN={org} Root CA", "-keyout", str(root_key), "-out", str(root_cert),
        )
        root_key.chmod(0o600)
        _issue_signing(root_key, root_cert, out_dir, signing_days, org, cnf)


def rotate_signing(
    root_key: Path, root_cert: Path, out_dir: Path, *, days: int = 365, org: str = DEFAULT_ORG
) -> None:
    """Issue a fresh signing key and certificate; the previous pair is kept as *.old."""
    _check_org(org)
    for name in (SIGNING_KEY, SIGNING_CERT):
        current = out_dir / name
        if current.exists():
            current.replace(out_dir / f"{name}.old")
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / "ext.cnf"
        cnf.write_text(_EXTENSIONS)
        _issue_signing(root_key, root_cert, out_dir, days, org, cnf)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k pki"`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add uc/uc/pki.py tests/unit
git commit -m "Add signing PKI with offline root and rotatable signing cert

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Preflight checks (`preflight.py`)

**Files:**
- Create: `uc/uc/preflight.py`
- Test: `tests/unit/test_preflight.py`

**Interfaces:**
- Consumes: `Settings`, `read_secret`, `Credentials`, `CertInfo`, `cert_info`, `key_matches_cert`, `verify_chain`
- Produces:
  - `PreflightError(UcError)`
  - `GITHUB_PLACEHOLDER = "uc"`
  - `check_public_url(url: str, allow_local: bool) -> list[str]`, which returns warnings and raises on errors
  - `check_signing(settings, now) -> CertInfo`
  - `load_credentials(settings) -> Credentials`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_preflight.py`:

```python
import datetime as dt
import shutil

import pytest
from ucsupport import make_settings

from uc.pki import init_pki
from uc.preflight import PreflightError, check_public_url, check_signing, load_credentials

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.mark.parametrize("url", ["https://uc.example.com", "https://uc.example.com:8443", "http://web:8080"])
def test_bare_origins_are_accepted(url):
    check_public_url(url, allow_local=False)


def test_plain_http_warns():
    assert check_public_url("http://web:8080", allow_local=False) == [
        "PUBLIC_URL uses plain http; signatures protect integrity, but TLS is recommended"
    ]
    assert check_public_url("https://uc.example.com", allow_local=False) == []


@pytest.mark.parametrize(
    "url,message",
    [
        ("https://host.example.com/jenkins", "bare origin"),
        ("https://host.example.com/?x=1", "bare origin"),
        ("ftp://host", "http\\(s\\) URL"),
        ("https://user:pw@host", "credentials"),  # pragma: allowlist secret
        ("http://localhost:8080", "cannot reach"),
        ("http://127.0.0.1:8080", "cannot reach"),
    ],
)
def test_bad_public_urls_are_rejected(url, message):
    with pytest.raises(PreflightError, match=message):
        check_public_url(url, allow_local=False)


def test_local_urls_allowed_for_tests():
    check_public_url("http://localhost:8080", allow_local=True)


def test_check_signing_ok(tmp_path, secrets_dir):
    info = check_signing(make_settings(tmp_path, secrets_dir), NOW)
    assert "Signing" in info.subject


def test_check_signing_missing_file(tmp_path, secrets_dir):
    (secrets_dir / "root-ca.crt").unlink()
    with pytest.raises(PreflightError, match="missing signing material"):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_check_signing_expiring_cert(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, CERTIFICATE_MINIMUM_VALID_DAYS="50")
    with pytest.raises(PreflightError, match="expires in 4[45] days \\(minimum 50\\)"):
        check_signing(settings, NOW)


def test_check_signing_key_mismatch(tmp_path, secrets_dir):
    other = tmp_path / "other-pki"
    init_pki(other, root_days=10, signing_days=5)
    shutil.copy(other / "update-center.key", secrets_dir / "update-center.key")
    with pytest.raises(PreflightError, match="does not match"):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_check_signing_wrong_root(tmp_path, secrets_dir):
    other = tmp_path / "other-pki"
    init_pki(other, root_days=10, signing_days=5)
    shutil.copy(other / "root-ca.crt", secrets_dir / "root-ca.crt")
    with pytest.raises(PreflightError, match="is not issued by"):
        check_signing(make_settings(tmp_path, secrets_dir), NOW)


def test_load_credentials_with_github_placeholder(tmp_path, secrets_dir):
    creds = load_credentials(make_settings(tmp_path, secrets_dir))
    assert (creds.artifactory_username, creds.artifactory_password) == ("uc", "secret")
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == ("uc", "uc", True)


def test_load_credentials_with_github_token(tmp_path, secrets_dir):
    (secrets_dir / "github_username").write_text("bot\n")
    (secrets_dir / "github_token").write_text("ghp_x\n")
    creds = load_credentials(make_settings(tmp_path, secrets_dir))
    assert (creds.github_username, creds.github_password, creds.github_placeholder) == ("bot", "ghp_x", False)


def test_load_credentials_requires_artifactory(tmp_path, secrets_dir):
    (secrets_dir / "artifactory_password").write_text("")
    with pytest.raises(PreflightError, match="Artifactory credentials missing"):
        load_credentials(make_settings(tmp_path, secrets_dir))
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k preflight"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.preflight'`.

- [ ] **Step 3: Implement `uc/uc/preflight.py`**

```python
"""Checks run before every generation (spec §6 step 1)."""
from __future__ import annotations

import datetime as dt
import ipaddress
from urllib.parse import urlsplit

from .config import Settings, read_secret
from .errors import UcError
from .model import Credentials
from .pki import CertInfo, cert_info, key_matches_cert, verify_chain

GITHUB_PLACEHOLDER = "uc"


class PreflightError(UcError):
    pass


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_public_url(url: str, allow_local: bool) -> list[str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PreflightError(f"PUBLIC_URL must be an http(s) URL with a host (got {url!r})")
    if parts.username or parts.password:
        raise PreflightError("PUBLIC_URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PreflightError(
            f"PUBLIC_URL must be a bare origin such as https://uc.example.com (got {url!r}); "
            "upstream routing and jenkins-plugin-cli do not support a path prefix"
        )
    if not allow_local and _is_local(parts.hostname):
        raise PreflightError(
            f"PUBLIC_URL points at {parts.hostname}, which Jenkins controllers cannot reach; "
            "set ALLOW_LOCAL_PUBLIC_URL=true for tests"
        )
    if parts.scheme == "http":
        return ["PUBLIC_URL uses plain http; signatures protect integrity, but TLS is recommended"]
    return []


def check_signing(settings: Settings, now: dt.datetime) -> CertInfo:
    for path in (settings.signing_key_file, settings.signing_cert_file, settings.root_ca_file):
        if not path.is_file():
            raise PreflightError(f"missing signing material: {path} (see 'uc pki init')")
    if not key_matches_cert(settings.signing_key_file, settings.signing_cert_file):
        raise PreflightError(
            f"{settings.signing_key_file} does not match {settings.signing_cert_file}"
        )
    info = cert_info(settings.signing_cert_file)
    days = info.days_left(now)
    if days < settings.certificate_minimum_valid_days:
        raise PreflightError(
            f"signing certificate expires in {days} days "
            f"(minimum {settings.certificate_minimum_valid_days}); run 'uc pki rotate'"
        )
    if not verify_chain(settings.root_ca_file, settings.signing_cert_file):
        raise PreflightError(
            f"{settings.signing_cert_file} is not issued by {settings.root_ca_file}"
        )
    return info


def load_credentials(settings: Settings) -> Credentials:
    user = read_secret(settings.artifactory_username_file)
    password = read_secret(settings.artifactory_password_file)
    if not user or not password:
        raise PreflightError(
            "Artifactory credentials missing: provide "
            f"{settings.artifactory_username_file} and {settings.artifactory_password_file}"
        )
    gh_user = read_secret(settings.github_username_file)
    gh_token = read_secret(settings.github_token_file)
    if gh_user and gh_token:
        return Credentials(user, password, gh_user, gh_token, github_placeholder=False)
    return Credentials(user, password, GITHUB_PLACEHOLDER, GITHUB_PLACEHOLDER, github_placeholder=True)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k preflight"`
Expected: `19 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/preflight.py tests/unit/test_preflight.py
git commit -m "Add preflight checks for PUBLIC_URL, signing material and credentials

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---
### Task 5: HTTP helper and upstream resources with overlay (`http.py`, `resources.py`)

**Files:**
- Create: `uc/uc/http.py`, `uc/uc/resources.py`, `tests/support/archives.py`, `tests/support/httpstub.py`
- Test: `tests/unit/test_http.py`, `tests/unit/test_resources.py`

**Interfaces:**
- Consumes: `Settings` (`resources_source`, `resources_ref`, `resources_url`, `resources_sha256`, `bundled_resources`, `overlay_dir`, `data_dir`), `uc.timeutil.iso`, `uc.timeutil.parse_iso`
- Produces:
  - `uc.http.USER_AGENT`, `uc.http.HttpError(UcError)` and `uc.http.Getter`, the type `Callable[..., bytes]` called as `get(url, headers=None, timeout=60)`
  - `uc.http.get(url: str, headers: Mapping[str, str] | None = None, timeout: float = 60) -> bytes` (follows redirects)
  - `uc.resources.ResourcesError(UcError)`
  - `ResourcesInfo(source, ref, data_date, fetched_at)` with `age_days(now) -> int`
  - `PreparedResources(path, upstream, info, overlay: list[str], warnings: list[str])`
  - `INFO_FILE = ".uc-resources.json"` and `REQUIRED_FILES`
  - `fetch_upstream(settings, dest, now, get=http.get) -> ResourcesInfo`
  - `merge_overlay(upstream: Path, overlay: Path, dest: Path) -> list[str]`
  - `prepare_resources(settings, work: Path, now, get=http.get) -> PreparedResources`
  - `save_last_good(prepared, data_dir) -> None`
  - `tests/support/archives.make_tar(files: dict[str, bytes], mtime: int = 1_758_000_000) -> bytes`
  - `tests/support/httpstub.StubServer(routes)`: a context manager whose routes map `(method, path)` to `(status, headers, body)`. It exposes `.url` and records `.requests` as `(method, path, headers: dict, body: bytes)`.

- [ ] **Step 1: Add the test-support helpers**

`tests/support/archives.py`:

```python
"""In-memory tar.gz archives for resources tests."""
from __future__ import annotations

import io
import tarfile


def make_tar(files: dict[str, bytes], mtime: int = 1_758_000_000) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = mtime
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
```

`tests/support/httpstub.py`:

```python
"""A threaded HTTP server that answers with canned responses and records every request."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubServer:
    def __init__(self, routes: dict[tuple[str, str], tuple[int, dict[str, str], bytes]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                stub.requests.append((self.command, self.path, dict(self.headers), body))
                status, headers, payload = stub.routes.get((self.command, self.path), (404, {}, b""))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_http.py`:

```python
import pytest
from httpstub import StubServer

from uc.http import HttpError, get


def test_get_follows_redirects():
    routes = {
        ("GET", "/start"): (307, {"Location": "/final"}, b""),
        ("GET", "/final"): (200, {}, b"payload"),
    }
    with StubServer(routes) as stub:
        assert get(stub.url + "/start") == b"payload"
        assert stub.requests[0][2]["User-Agent"].startswith("jenkins-uc/")


def test_get_wraps_errors():
    with StubServer({}) as stub:
        with pytest.raises(HttpError, match="GET .*/missing failed: HTTP Error 404"):
            get(stub.url + "/missing")
```

`tests/unit/test_resources.py`:

```python
import datetime as dt
import hashlib
import json

import pytest
from archives import make_tar
from ucsupport import make_settings

from uc.http import HttpError
from uc.resources import (
    INFO_FILE,
    ResourcesError,
    fetch_upstream,
    merge_overlay,
    prepare_resources,
    save_last_good,
)
from uc.timeutil import iso

NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)
SHA = "ebc2b7754082994393232c08c13230a9c6299435"
PREFIX = f"update-center2-{SHA}/"
UPSTREAM = {
    PREFIX + "resources/warnings.json": b'[{"id": "w1", "message": "upstream"}]',
    PREFIX + "resources/artifact-ignores.properties": b"bad-plugin-1.0 = https://example/\n",
    PREFIX + "resources/index-template.html": b"<html>{{ content }}</html>",
    PREFIX + "src/test/resources/warnings.json": b"[]",
    PREFIX + "README.adoc": b"readme",
}


def github_getter(tarball):
    def get(url, headers=None, timeout=60):
        if url == "https://api.github.com/repos/jenkins-infra/update-center2/commits/master":
            return json.dumps({"sha": SHA, "commit": {"committer": {"date": "2026-09-16T13:29:23Z"}}}).encode()
        if url == f"https://codeload.github.com/jenkins-infra/update-center2/tar.gz/{SHA}":
            return tarball
        raise HttpError(f"unexpected GET {url}")

    return get


def offline(url, headers=None, timeout=60):
    raise HttpError(f"GET {url} failed: offline")


def test_github_source(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="github")
    dest = tmp_path / "res"
    info = fetch_upstream(settings, dest, NOW, get=github_getter(make_tar(UPSTREAM)))
    assert (info.source, info.ref, info.data_date, info.fetched_at) == (
        "github", SHA, "2026-09-16T13:29:23Z", "2026-09-22T12:00:00Z",
    )
    assert info.age_days(NOW) == 5
    assert json.loads((dest / "warnings.json").read_text()) == [{"id": "w1", "message": "upstream"}]
    assert not (dest / "README.adoc").exists()
    assert json.loads((dest / INFO_FILE).read_text())["ref"] == SHA


def test_url_source_checks_sha256(tmp_path, secrets_dir):
    tarball = make_tar({name[len(PREFIX):]: data for name, data in UPSTREAM.items()})
    digest = hashlib.sha256(tarball).hexdigest()

    def get(url, headers=None, timeout=60):
        return tarball

    ok = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="url",
                       RESOURCES_URL="https://art/generic/uc2.tgz", RESOURCES_SHA256=digest)
    info = fetch_upstream(ok, tmp_path / "ok", NOW, get=get)
    assert info.ref == f"sha256:{digest}"
    assert info.data_date == iso(dt.datetime.fromtimestamp(1_758_000_000, dt.timezone.utc))

    bad = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="url",
                        RESOURCES_URL="https://art/generic/uc2.tgz", RESOURCES_SHA256="0" * 64)
    with pytest.raises(ResourcesError, match="checksum mismatch"):
        fetch_upstream(bad, tmp_path / "bad", NOW, get=get)


def test_bundled_source(tmp_path, secrets_dir):
    info = fetch_upstream(make_settings(tmp_path, secrets_dir), tmp_path / "b", NOW)
    assert info.source == "bundled"
    assert len(info.ref) == 40
    assert (tmp_path / "b" / "warnings.json").is_file()


def test_missing_required_file(tmp_path, secrets_dir):
    tarball = make_tar({"resources/warnings.json": b"[]"})
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="url", RESOURCES_URL="https://x/r.tgz")
    with pytest.raises(ResourcesError, match="missing artifact-ignores.properties, index-template.html"):
        fetch_upstream(settings, tmp_path / "r", NOW, get=lambda *a, **k: tarball)


def test_unsafe_archive_path(tmp_path, secrets_dir):
    tarball = make_tar({"resources/warnings.json": b"[]", "resources/../../evil": b"x"})
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="url", RESOURCES_URL="https://x/r.tgz")
    with pytest.raises(ResourcesError, match="unsafe path"):
        fetch_upstream(settings, tmp_path / "r", NOW, get=lambda *a, **k: tarball)


def test_not_a_tarball(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="url", RESOURCES_URL="https://x/r.tgz")
    with pytest.raises(ResourcesError, match="not a tar"):
        fetch_upstream(settings, tmp_path / "r", NOW, get=lambda *a, **k: b"<html>")


def test_overlay_merge(tmp_path):
    upstream = tmp_path / "up"
    upstream.mkdir()
    (upstream / "warnings.json").write_text(json.dumps([
        {"id": "w1", "message": "upstream"}, {"id": "w2", "message": "keep"},
    ]))
    (upstream / "artifact-ignores.properties").write_bytes(b"old-plugin = https://x/")
    (upstream / "index-template.html").write_text("upstream")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "warnings.json").write_text(json.dumps([
        {"id": "w1", "message": "ours"}, {"id": "corp-1", "message": "new"},
    ]))
    (overlay / "artifact-ignores.properties").write_text("internal-bad-plugin = https://corp/why\n")
    (overlay / "index-template.html").write_text("ours")
    (overlay / "README.md").write_text("ignored")

    dest = tmp_path / "merged"
    assert merge_overlay(upstream, overlay, dest) == [
        "artifact-ignores.properties", "index-template.html", "warnings.json",
    ]
    assert [w["message"] for w in json.loads((dest / "warnings.json").read_text())] == ["ours", "keep", "new"]
    props = (dest / "artifact-ignores.properties").read_text()
    assert props.startswith("old-plugin = https://x/\n")
    assert props.rstrip().endswith("internal-bad-plugin = https://corp/why")
    assert (dest / "index-template.html").read_text() == "ours"
    assert not (dest / "README.md").exists()
    assert json.loads((upstream / "warnings.json").read_text())[0]["message"] == "upstream"


def test_overlay_warning_without_id(tmp_path):
    upstream = tmp_path / "up"
    upstream.mkdir()
    (upstream / "warnings.json").write_text("[]")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "warnings.json").write_text('[{"message": "no id"}]')
    with pytest.raises(ResourcesError, match="needs an 'id'"):
        merge_overlay(upstream, overlay, tmp_path / "merged")


def test_missing_overlay_dir_is_fine(tmp_path):
    upstream = tmp_path / "up"
    upstream.mkdir()
    (upstream / "warnings.json").write_text("[]")
    assert merge_overlay(upstream, tmp_path / "none", tmp_path / "merged") == []


def test_prepare_falls_back_to_last_good(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="github")
    first = prepare_resources(settings, tmp_path / "w1", NOW, get=github_getter(make_tar(UPSTREAM)))
    save_last_good(first, settings.data_dir)
    second = prepare_resources(settings, tmp_path / "w2", NOW, get=offline)
    assert second.info.ref == SHA
    assert "using last-good copy" in second.warnings[0]
    assert (second.path / "warnings.json").is_file()


def test_prepare_without_last_good_fails(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="github")
    with pytest.raises(ResourcesError, match="no last-good copy"):
        prepare_resources(settings, tmp_path / "w", NOW, get=offline)


def test_prepare_applies_overlay_but_keeps_upstream_pristine(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RESOURCES_SOURCE="github")
    settings.overlay_dir.mkdir(parents=True)
    (settings.overlay_dir / "artifact-ignores.properties").write_text("corp-bad = https://corp/\n")
    prepared = prepare_resources(settings, tmp_path / "w", NOW, get=github_getter(make_tar(UPSTREAM)))
    assert prepared.overlay == ["artifact-ignores.properties"]
    assert "corp-bad" in (prepared.path / "artifact-ignores.properties").read_text()
    assert "corp-bad" not in (prepared.upstream / "artifact-ignores.properties").read_text()
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'http or resources'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.http'`.

- [ ] **Step 4: Implement `uc/uc/http.py`**

```python
"""Minimal HTTP GET used for resources and tool installers (urllib follows 301/302/303/307/308)."""
from __future__ import annotations

import urllib.request
from typing import Callable, Mapping

from .errors import UcError

USER_AGENT = "jenkins-uc/1"
Getter = Callable[..., bytes]


class HttpError(UcError):
    pass


def get(url: str, headers: Mapping[str, str] | None = None, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, ValueError) as exc:  # URLError and HTTPError are OSError subclasses
        raise HttpError(f"GET {url} failed: {exc}") from exc
```

- [ ] **Step 5: Implement `uc/uc/resources.py`**

```python
"""Upstream resources/ (security warnings, suspensions, labels) plus the operator overlay (spec §6 step 2)."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import shutil
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import http
from .config import Settings
from .errors import UcError
from .timeutil import iso, parse_iso

GITHUB_REPO = "jenkins-infra/update-center2"
REQUIRED_FILES = ("warnings.json", "artifact-ignores.properties", "index-template.html")
INFO_FILE = ".uc-resources.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class ResourcesError(UcError):
    pass


@dataclass(frozen=True)
class ResourcesInfo:
    source: str
    ref: str
    data_date: str  # when upstream last changed this data (ISO 8601 UTC)
    fetched_at: str

    def age_days(self, now: dt.datetime) -> int:
        return (now - parse_iso(self.data_date)).days


@dataclass(frozen=True)
class PreparedResources:
    path: Path  # merged copy passed to --resources-dir
    upstream: Path  # upstream copy before the overlay; saved as last-good after publish
    info: ResourcesInfo
    overlay: list[str]
    warnings: list[str]


def _resources_prefix(members: list[tarfile.TarInfo]) -> str:
    candidates = [
        m.name[: -len("warnings.json")]
        for m in members
        if m.isfile()
        and PurePosixPath(m.name).name == "warnings.json"
        and PurePosixPath(m.name).parent.name == "resources"
    ]
    if not candidates:
        raise ResourcesError("archive does not contain resources/warnings.json")
    return min(candidates, key=len)  # the top-level resources/, not src/test/resources/


def _extract_resources(archive: bytes, dest: Path) -> dt.datetime:
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except tarfile.TarError as exc:
        raise ResourcesError(f"resources archive is not a tar file: {exc}") from exc
    with tar:
        members = tar.getmembers()
        prefix = _resources_prefix(members)
        dest.mkdir(parents=True)
        newest = 0.0
        for member in members:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts:
                raise ResourcesError(f"unsafe path in resources archive: {member.name}")
            target = dest.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            assert extracted is not None
            target.write_bytes(extracted.read())
            newest = max(newest, float(member.mtime))
    return dt.datetime.fromtimestamp(newest, dt.timezone.utc)


def fetch_upstream(
    settings: Settings, dest: Path, now: dt.datetime, get: http.Getter = http.get
) -> ResourcesInfo:
    if settings.resources_source == "bundled":
        shutil.copytree(settings.bundled_resources, dest)
        meta = json.loads((dest / INFO_FILE).read_text(encoding="utf-8"))
        info = ResourcesInfo("bundled", meta["ref"], iso(parse_iso(meta["data_date"])), iso(now))
    elif settings.resources_source == "github":
        commit = json.loads(get(
            f"https://api.github.com/repos/{GITHUB_REPO}/commits/{settings.resources_ref}",
            {"Accept": "application/vnd.github+json"},
        ))
        sha = commit.get("sha", "")
        if not _SHA.match(sha):
            raise ResourcesError(f"unexpected GitHub response for {settings.resources_ref!r}")
        _extract_resources(get(f"https://codeload.github.com/{GITHUB_REPO}/tar.gz/{sha}"), dest)
        info = ResourcesInfo(
            "github", sha, iso(parse_iso(commit["commit"]["committer"]["date"])), iso(now)
        )
    else:
        archive = get(settings.resources_url)
        digest = hashlib.sha256(archive).hexdigest()
        if settings.resources_sha256 and digest != settings.resources_sha256:
            raise ResourcesError(
                f"RESOURCES_URL checksum mismatch: expected {settings.resources_sha256}, got {digest}"
            )
        newest = _extract_resources(archive, dest)
        info = ResourcesInfo("url", f"sha256:{digest}", iso(newest), iso(now))
    missing = [name for name in REQUIRED_FILES if not (dest / name).is_file()]
    if missing:
        raise ResourcesError(f"resources are missing {', '.join(missing)}")
    (dest / INFO_FILE).write_text(json.dumps(asdict(info)) + "\n", encoding="utf-8")
    return info


def _merge_warnings(base: object, extra: object) -> list:
    if not isinstance(base, list) or not isinstance(extra, list):
        raise ResourcesError("warnings.json must be a JSON array")
    by_id = {w["id"]: w for w in extra if isinstance(w, dict) and "id" in w}
    if len(by_id) != len(extra):
        raise ResourcesError("every overlay warning needs an 'id'")
    merged = [by_id.pop(w.get("id"), w) if isinstance(w, dict) else w for w in base]
    merged.extend(by_id.values())
    return merged


def merge_overlay(upstream: Path, overlay: Path, dest: Path) -> list[str]:
    """Copy upstream to dest and apply the overlay: .properties append (later keys win),
    warnings.json merges by id, any other file replaces upstream's."""
    shutil.copytree(upstream, dest)
    applied: list[str] = []
    if not overlay.is_dir():
        return applied
    for item in sorted(overlay.iterdir()):
        if not item.is_file() or item.name.startswith(".") or item.name.lower().startswith("readme"):
            continue
        target = dest / item.name
        if item.suffix == ".properties":
            base = target.read_bytes() if target.exists() else b""
            if base and not base.endswith(b"\n"):
                base += b"\n"
            banner = f"\n# ---- overlay: {item.name} (later keys win) ----\n".encode("ascii")
            target.write_bytes(base + banner + item.read_bytes())
        elif item.name == "warnings.json":
            merged = _merge_warnings(
                json.loads(target.read_text(encoding="utf-8")),
                json.loads(item.read_text(encoding="utf-8")),
            )
            target.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            shutil.copyfile(item, target)
        applied.append(item.name)
    return applied


def prepare_resources(
    settings: Settings, work: Path, now: dt.datetime, get: http.Getter = http.get
) -> PreparedResources:
    upstream = work / "upstream-resources"
    last_good = settings.data_dir / "resources" / "last-good"
    warnings: list[str] = []
    try:
        info = fetch_upstream(settings, upstream, now, get)
    except (ResourcesError, http.HttpError, OSError, ValueError, KeyError) as exc:
        if not (last_good / INFO_FILE).is_file():
            raise ResourcesError(
                f"could not obtain resources ({exc}) and no last-good copy exists"
            ) from exc
        shutil.rmtree(upstream, ignore_errors=True)
        shutil.copytree(last_good, upstream)
        info = ResourcesInfo(**json.loads((last_good / INFO_FILE).read_text(encoding="utf-8")))
        warnings.append(f"resources fetch failed ({exc}); using last-good copy from {info.fetched_at}")
    merged = work / "resources"
    applied = merge_overlay(upstream, settings.overlay_dir, merged)
    return PreparedResources(merged, upstream, info, applied, warnings)


def save_last_good(prepared: PreparedResources, data_dir: Path) -> None:
    """Called only after a successful publish, so a bad upstream change never becomes the fallback."""
    target = data_dir / "resources" / "last-good"
    staging = target.with_name("last-good.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(prepared.upstream, staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'http or resources'"`
Expected: `14 passed`.

- [ ] **Step 7: Commit**

```bash
git add uc/uc/http.py uc/uc/resources.py tests
git commit -m "Fetch upstream resources (github, url, bundled) and apply overlay

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Root `.htaccess` rendering (`htaccess.py`, `templates/htaccess-static`)

**Files:**
- Create: `uc/uc/htaccess.py`, `uc/templates/htaccess-static`
- Delete: `uc/templates/.gitkeep`
- Test: `tests/unit/test_htaccess.py`

**Interfaces:**
- Consumes: `uc.model.Tiers`, and the image's `/opt/uc/site/generate-htaccess.sh`
- Produces:
  - `HtaccessError(UcError)`
  - `UPSTREAM_DOWNLOAD_RULES` (a tuple of the 5 exact lines)
  - `strip_download_rules(text) -> str`
  - `fix_partial_tiers(text, has_weekly: bool, has_stable: bool) -> str`
  - `render_htaccess(tiers: Tiers, script: Path, template: Path) -> str`
  - `absolute_targets(text) -> list[str]`

- [ ] **Step 1: Add the zero-tier template**

`uc/templates/htaccess-static` (static rules from upstream `site/generate-htaccess.sh` at 3.18.4, plus a `stable/` rule pointing at `current/`):

```apache
# THIS FILE IS GENERATED by uc: this catalog has no dynamic tiers.
#
# Static rules copied from upstream site/generate-htaccess.sh (update-center2 3.18.4).
RewriteEngine on

# https://github.com/jenkinsci/jenkins/blob/56b6623915c50b4a2ef994a143a8fe0829587f3c/core/src/main/java/hudson/model/UpdateCenter.java#L1182-L1207
RewriteCond %{QUERY_STRING} ^.*uctest$
RewriteRule ^(|.+/)(update\-center.*\.(json|html)+) /uctest.json [NC,L]

# Without LTS tiers, /stable/ is the current site
RewriteRule ^stable/(.+) "/current/$1" [NC,L,R]

# If that all failed, but we have an update center, let's go to current
RewriteRule ^update\-center.*\.(json|html)+ /current%{REQUEST_URI}? [NC,L,R=301]

RewriteRule ^latestCore\.txt /current%{REQUEST_URI}? [NC,L,R=301]

# Ensure /release-history.json goes to the right place
RewriteRule ^release\-history\.json$ /current%{REQUEST_URI}? [NC,L,R=301]

# Ensure /plugin-documentation-urls.json goes to the right place
RewriteRule ^plugin\-documentation\-urls\.json$ /current%{REQUEST_URI}? [NC,L,R=301]

# Ensure /plugin-versions.json goes to the right place
RewriteRule ^plugin\-versions\.json$ /current%{REQUEST_URI}? [NC,L,R=301]

DirectoryIndex index.html
```

Run `git rm -q uc/templates/.gitkeep`.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_htaccess.py`:

```python
from pathlib import Path

import pytest

from uc.htaccess import (
    UPSTREAM_DOWNLOAD_RULES,
    HtaccessError,
    absolute_targets,
    fix_partial_tiers,
    render_htaccess,
    strip_download_rules,
)
from uc.model import Tiers

SCRIPT = Path("/opt/uc/site/generate-htaccess.sh")
TEMPLATE = Path("/opt/uc/templates/htaccess-static")


def render(weekly=(), stable=()):
    return render_htaccess(Tiers(tuple(weekly), tuple(stable)), SCRIPT, TEMPLATE)


def test_full_tiers_keep_routing_and_drop_download_redirects():
    text = render(["2.540"], ["2.528.1"])
    assert r"/dynamic-2\.540%{REQUEST_URI}?" in text
    assert r"/dynamic-stable-2\.528\.1%{REQUEST_URI}?" in text
    assert '"/dynamic-stable-2.528.1/$1"' in text
    assert "RewriteCond %{QUERY_STRING} ^.*uctest$" in text
    for rule in UPSTREAM_DOWNLOAD_RULES:
        assert rule not in text
    assert absolute_targets(text) == []  # upstream's comments still mention get.jenkins.io; rules do not


def test_weekly_only_routes_lts_to_current():
    text = render(["2.540", "2.541"])
    assert "/dynamic-stable-%{REQUEST_URI}?" not in text
    assert '"/dynamic-stable-/$1"' not in text
    assert '"/current/$1"' in text
    assert absolute_targets(text) == []


def test_stable_only_routes_weekly_to_current():
    text = render(stable=["2.528.1", "2.528.3"])
    assert "/dynamic-%{REQUEST_URI}?" not in text
    assert absolute_targets(text) == []


def test_zero_tiers_use_the_static_template():
    text = render()
    assert text == TEMPLATE.read_text()
    assert "RewriteCond %{QUERY_STRING} ^.*uctest$" in text
    assert '"/current/$1"' in text
    assert absolute_targets(text) == []


def test_upstream_changes_are_detected():
    with pytest.raises(HtaccessError, match="upstream generate-htaccess.sh changed"):
        strip_download_rules("RewriteEngine on\n" + "\n".join(UPSTREAM_DOWNLOAD_RULES[:4]))
    with pytest.raises(HtaccessError, match="found 2"):
        strip_download_rules("\n".join(UPSTREAM_DOWNLOAD_RULES + UPSTREAM_DOWNLOAD_RULES[:1]))
    with pytest.raises(HtaccessError, match="partial tier list"):
        fix_partial_tiers("RewriteEngine on\n", has_weekly=True, has_stable=False)


def test_script_failure_is_reported(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("echo boom >&2; exit 3\n")
    with pytest.raises(HtaccessError, match="failed \\(3\\): boom"):
        render_htaccess(Tiers(("2.540",)), script, TEMPLATE)


def test_absolute_targets_ignores_comments():
    text = "# See https://github.com/x\nRewriteRule ^a https://evil/ [R]\n"
    assert absolute_targets(text) == ["RewriteRule ^a https://evil/ [R]"]
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k htaccess"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.htaccess'`.

- [ ] **Step 4: Implement `uc/uc/htaccess.py`**

```python
"""The root .htaccess: upstream generate-htaccess.sh output minus its download redirects (spec §6 step 5)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import UcError
from .model import Tiers

# The last five rules of upstream's output (site/generate-htaccess.sh:132-137). Downloads are
# handled at server level instead (web/conf/uc-site.conf).
UPSTREAM_DOWNLOAD_RULES = (
    "RewriteRule ^download/war/latest/jenkins[.]war$ https://updates.jenkins.io/latest/jenkins.war [NC,L,R=302]",
    "RewriteRule ^download/plugins/(.*)/latest/(.+)[.]hpi$ https://updates.jenkins.io/latest/$2.hpi [NC,L,R=302]",
    "RewriteRule ^download/war/([0-9]+[.][0-9]+[.][0-9]+/jenkins)[.]war$ https://get.jenkins.io/war-stable/$1.war [NC,L,R=302]",
    "RewriteRule ^download/war/(.+)[.]war$ https://get.jenkins.io/war/$1.war [NC,L,R=302]",
    "RewriteRule ^download/plugins/(.+)[.]hpi$ https://get.jenkins.io/plugins/$1.hpi [NC,L,R=302]",
)
# Targets upstream emits when one tier list is empty; they point nowhere, so send them to current/.
NO_STABLE_FIXES = (
    ("/dynamic-stable-%{REQUEST_URI}?", "/current%{REQUEST_URI}?"),
    ('"/dynamic-stable-/$1"', '"/current/$1"'),
)
NO_WEEKLY_FIXES = (("/dynamic-%{REQUEST_URI}?", "/current%{REQUEST_URI}?"),)


class HtaccessError(UcError):
    pass


def _changed(detail: str) -> HtaccessError:
    return HtaccessError(
        f"{detail}; upstream generate-htaccess.sh changed, review it before upgrading (docs/upgrading.md)"
    )


def strip_download_rules(text: str) -> str:
    lines = text.split("\n")
    for rule in UPSTREAM_DOWNLOAD_RULES:
        count = lines.count(rule)
        if count != 1:
            raise _changed(f"expected exactly one download rule {rule!r}, found {count}")
        lines.remove(rule)
    return "\n".join(lines)


def fix_partial_tiers(text: str, has_weekly: bool, has_stable: bool) -> str:
    fixes = (() if has_stable else NO_STABLE_FIXES) + (() if has_weekly else NO_WEEKLY_FIXES)
    for old, new in fixes:
        count = text.count(old)
        if count != 1:
            raise _changed(f"expected exactly one {old!r} for a partial tier list, found {count}")
        text = text.replace(old, new)
    return text


def render_htaccess(tiers: Tiers, script: Path, template: Path) -> str:
    if tiers.empty:
        return template.read_text(encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(script), *tiers.weekly, *tiers.stable], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise HtaccessError(f"generate-htaccess.sh failed ({proc.returncode}): {proc.stderr.strip()}")
    return strip_download_rules(fix_partial_tiers(proc.stdout, bool(tiers.weekly), bool(tiers.stable)))


def absolute_targets(text: str) -> list[str]:
    """Non-comment lines that mention an absolute http(s) URL."""
    return [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and ("http://" in line or "https://" in line)
    ]
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k htaccess"`
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add -A uc/uc/htaccess.py uc/templates tests/unit/test_htaccess.py
git commit -m "Render root .htaccess from upstream script without download redirects

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Generator arguments and jar runner (`args.py`, `jar.py`)

**Files:**
- Create: `uc/uc/args.py`, `uc/uc/jar.py`
- Test: `tests/unit/test_args.py`, `tests/unit/test_jar.py`

**Interfaces:**
- Consumes: `Settings`, `Tiers`, `Credentials`
- Produces:
  - `uc.args.ArgumentsError(UcError)`
  - `common_arguments(settings) -> list[str]`
  - `build_argument_lines(settings, staging: Path, tiers: Tiers) -> list[str]`
  - `write_arguments_file(lines: list[str], path: Path) -> None`
  - `uc.jar.JarError(UcError)`
  - `JarRunner`, the type `Callable[[list[str], Mapping[str, str], Path], None]`
  - `jar_environment(settings, creds, base_env) -> dict[str, str]`
  - `java_command(settings, *args) -> list[str]`
  - `run_jar(cmd, env, log_path) -> None`
  - `generate_tiers(settings, env, resources: Path, out: Path, log: Path, run: JarRunner = run_jar) -> Tiers`
  - `generate_sites(settings, env, resources: Path, arguments_file: Path, log: Path, run: JarRunner = run_jar) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_args.py`:

```python
import pytest
from ucsupport import make_settings

from uc.args import ArgumentsError, build_argument_lines, write_arguments_file
from uc.model import Tiers


def test_argument_lines_mirror_upstream(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, UC_EXTRA_ARGS="--max-plugins 20")
    staging = tmp_path / "data" / "releases" / "R.staging"
    lines = build_argument_lines(settings, staging, Tiers(("2.540",), ("2.528.1",)))
    common = (
        f"--key {secrets_dir}/update-center.key --certificate {secrets_dir}/update-center.cert "
        f"--root-certificate {secrets_dir}/root-ca.crt "
        "--connection-check-url https://uc.example.com/uctest.json --id default --max-plugins 20"
    )
    s = str(staging)
    assert lines == [
        f"{common} --limit-plugin-core-dependency 2.540 --write-latest-core --write-timestamp --www-dir {s}/dynamic-2.540",
        f"{common} --limit-plugin-core-dependency 2.528.1 --write-latest-core --write-timestamp --www-dir {s}/dynamic-stable-2.528.1 --only-stable-core",
        f"{common} --www-dir {s}/experimental --generate-recent-releases --with-experimental --latest-links-directory {s}/experimental/latest",
        f"{common} --generate-release-history --generate-recent-releases --generate-plugin-versions "
        f"--generate-plugin-documentation-urls --write-latest-core --write-timestamp --write-plugin-count "
        f"--www-dir {s}/current --download-links-directory {s}/download --latest-links-directory {s}/current/latest",
    ]
    for line in lines:
        assert "--downloads-directory" not in line
        assert "--index-template-url" not in line


def test_zero_tiers_build_experimental_and_current_only(tmp_path, secrets_dir):
    lines = build_argument_lines(make_settings(tmp_path, secrets_dir), tmp_path / "s", Tiers())
    assert len(lines) == 2
    assert "--with-experimental" in lines[0]
    assert "--generate-plugin-versions" in lines[1]


def test_paths_with_spaces_are_rejected(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(ArgumentsError, match="splits argument lines on spaces"):
        build_argument_lines(settings, tmp_path / "has space", Tiers())


def test_write_arguments_file(tmp_path):
    path = tmp_path / "args.lst"
    write_arguments_file(["--a 1", "--b 2"], path)
    assert path.read_text() == "# one update site per line (generated by uc)\n--a 1\n--b 2\n"
```

`tests/unit/test_jar.py`:

```python
import os
import sys
from pathlib import Path

import pytest
from ucsupport import make_settings

from uc.jar import JarError, generate_sites, generate_tiers, jar_environment, java_command, run_jar
from uc.model import Credentials, Tiers

CREDS = Credentials("uc", "secret", "uc", "uc", True)


def test_jar_environment(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    env = jar_environment(settings, CREDS, {"PATH": "/usr/bin", "ARTIFACTORY_URL": "ignored"})
    assert env["PATH"] == "/usr/bin"
    assert env["DOWNLOADS_ROOT_URL"] == "https://uc.example.com/download"
    assert env["ARTIFACTORY_URL"] == "http://artifactory:8081/artifactory/"
    assert env["ARTIFACTORY_API_URL"] == "http://artifactory:8081/artifactory/api/"
    assert env["ARTIFACTORY_REPOSITORY"] == "jenkins"
    assert (env["ARTIFACTORY_USERNAME"], env["ARTIFACTORY_PASSWORD"]) == ("uc", "secret")
    assert env["ARTIFACTORY_CACHEDIR"] == str(tmp_path / "data" / "cache" / "artifactory")
    assert (env["GITHUB_USERNAME"], env["GITHUB_PASSWORD"]) == ("uc", "uc")
    assert env["POPULARITY_JSON_URL"] == ""
    assert env["CERTIFICATE_MINIMUM_VALID_DAYS"] == "30"


def test_jar_environment_uses_explicit_port(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, ARTIFACTORY_URL="https://art.corp/artifactory")
    assert jar_environment(settings, CREDS, {})["ARTIFACTORY_URL"] == "https://art.corp:443/artifactory/"


def test_java_command(tmp_path, secrets_dir):
    assert java_command(make_settings(tmp_path, secrets_dir), "--x") == [
        "java", "-Dfile.encoding=UTF-8", "-DCERTIFICATE_MINIMUM_VALID_DAYS=30",
        "-jar", "/opt/uc/generator/update-center2.jar", "--x",
    ]


def test_run_jar_streams_and_logs(tmp_path, capfd):
    log = tmp_path / "logs" / "uc2.log"
    run_jar([sys.executable, "-c", "print('hello from jar')"], dict(os.environ), log)
    assert "hello from jar" in log.read_text()
    assert "hello from jar" in capfd.readouterr().out


def test_run_jar_failure(tmp_path):
    with pytest.raises(JarError, match="exited with status 4"):
        run_jar([sys.executable, "-c", "import sys; sys.exit(4)"], dict(os.environ), tmp_path / "log")


def test_generate_tiers_reads_upstream_json(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = []

    def fake(cmd, env, log):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--dynamic-tier-list-file") + 1])
        out.write_text('{"stableCores": ["2.528.1"], "weeklyCores": ["2.540", "2.541"]}')

    tiers = generate_tiers(settings, {}, tmp_path / "res", tmp_path / "tiers.json", tmp_path / "log", run=fake)
    assert tiers == Tiers(("2.540", "2.541"), ("2.528.1",))
    assert calls[0][-4:] == [
        "--resources-dir", str(tmp_path / "res"), "--dynamic-tier-list-file", str(tmp_path / "tiers.json"),
    ]


def test_generate_tiers_without_output_fails(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(JarError, match="tier list"):
        generate_tiers(settings, {}, tmp_path / "res", tmp_path / "tiers.json", tmp_path / "log",
                       run=lambda cmd, env, log: None)


def test_generate_sites_command(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = []
    generate_sites(settings, {"A": "1"}, tmp_path / "res", tmp_path / "args.lst", tmp_path / "log",
                   run=lambda cmd, env, log: calls.append((cmd, env)))
    cmd, env = calls[0]
    assert cmd[-4:] == ["--resources-dir", str(tmp_path / "res"), "--arguments-file", str(tmp_path / "args.lst")]
    assert env == {"A": "1"}
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'args or jar'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.args'`.

- [ ] **Step 3: Implement `uc/uc/args.py`**

```python
"""Arguments file for update-center2 batch mode, mirroring upstream site/generate.sh:110-130 (spec §6 step 6)."""
from __future__ import annotations

from pathlib import Path

from .config import Settings
from .errors import UcError
from .model import Tiers


class ArgumentsError(UcError):
    pass


def common_arguments(settings: Settings) -> list[str]:
    return [
        "--key", str(settings.signing_key_file),
        "--certificate", str(settings.signing_cert_file),
        "--root-certificate", str(settings.root_ca_file),
        "--connection-check-url", f"{settings.public_url}/uctest.json",
        "--id", settings.uc_id,
        *settings.extra_args,
    ]


def build_argument_lines(settings: Settings, staging: Path, tiers: Tiers) -> list[str]:
    s = str(staging)
    sites: list[list[str]] = []
    for version in tiers.weekly:
        sites.append(["--limit-plugin-core-dependency", version, "--write-latest-core",
                      "--write-timestamp", "--www-dir", f"{s}/dynamic-{version}"])
    for version in tiers.stable:
        sites.append(["--limit-plugin-core-dependency", version, "--write-latest-core",
                      "--write-timestamp", "--www-dir", f"{s}/dynamic-stable-{version}",
                      "--only-stable-core"])
    sites.append(["--www-dir", f"{s}/experimental", "--generate-recent-releases",
                  "--with-experimental", "--latest-links-directory", f"{s}/experimental/latest"])
    sites.append(["--generate-release-history", "--generate-recent-releases",
                  "--generate-plugin-versions", "--generate-plugin-documentation-urls",
                  "--write-latest-core", "--write-timestamp", "--write-plugin-count",
                  "--www-dir", f"{s}/current", "--download-links-directory", f"{s}/download",
                  "--latest-links-directory", f"{s}/current/latest"])
    lines = []
    for site in sites:
        tokens = [*common_arguments(settings), *site]
        bad = [t for t in tokens if not t or any(c.isspace() for c in t)]
        if bad:
            raise ArgumentsError(
                f"update-center2 splits argument lines on spaces; these values cannot be used: {bad}"
            )
        lines.append(" ".join(tokens))
    return lines


def write_arguments_file(lines: list[str], path: Path) -> None:
    path.write_text("# one update site per line (generated by uc)\n" + "\n".join(lines) + "\n",
                    encoding="utf-8")
```

- [ ] **Step 4: Implement `uc/uc/jar.py`**

```python
"""Running the update-center2 jar (spec §6 steps 3, 4, 7)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from .config import Settings
from .errors import UcError
from .model import Credentials, Tiers

JarRunner = Callable[[list[str], Mapping[str, str], Path], None]


class JarError(UcError):
    pass


def jar_environment(settings: Settings, creds: Credentials, base_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(base_env)
    env.update({
        "DOWNLOADS_ROOT_URL": settings.downloads_root_url,
        "ARTIFACTORY_URL": settings.artifactory_url_with_port,
        "ARTIFACTORY_API_URL": settings.artifactory_api_url,
        "ARTIFACTORY_REPOSITORY": settings.artifactory_repository,
        "ARTIFACTORY_USERNAME": creds.artifactory_username,
        "ARTIFACTORY_PASSWORD": creds.artifactory_password,
        "ARTIFACTORY_CACHEDIR": str(settings.cache_dir),
        "GITHUB_USERNAME": creds.github_username,
        "GITHUB_PASSWORD": creds.github_password,
        "POPULARITY_JSON_URL": settings.popularity_json_url,
        "CERTIFICATE_MINIMUM_VALID_DAYS": str(settings.certificate_minimum_valid_days),
    })
    return env


def java_command(settings: Settings, *args: str) -> list[str]:
    return [
        "java", "-Dfile.encoding=UTF-8",
        f"-DCERTIFICATE_MINIMUM_VALID_DAYS={settings.certificate_minimum_valid_days}",
        "-jar", str(settings.jar), *args,
    ]


def run_jar(cmd: list[str], env: Mapping[str, str], log_path: Path) -> None:
    """Run the jar, streaming its output to stdout and appending it to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log, subprocess.Popen(
        cmd, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
    if proc.returncode != 0:
        raise JarError(f"update-center2 exited with status {proc.returncode}; see {log_path}")


def generate_tiers(
    settings: Settings, env: Mapping[str, str], resources: Path, out: Path, log: Path,
    run: JarRunner = run_jar,
) -> Tiers:
    run(java_command(settings, "--resources-dir", str(resources), *settings.extra_args,
                     "--dynamic-tier-list-file", str(out)), env, log)
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JarError(f"tier list {out} is missing or invalid: {exc}") from exc
    return Tiers(tuple(data.get("weeklyCores") or ()), tuple(data.get("stableCores") or ()))


def generate_sites(
    settings: Settings, env: Mapping[str, str], resources: Path, arguments_file: Path, log: Path,
    run: JarRunner = run_jar,
) -> None:
    run(java_command(settings, "--resources-dir", str(resources), "--arguments-file",
                     str(arguments_file)), env, log)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'args or jar'"`
Expected: `12 passed`.

- [ ] **Step 6: Commit**

```bash
git add uc/uc/args.py uc/uc/jar.py tests/unit/test_args.py tests/unit/test_jar.py
git commit -m "Build update-center2 argument lines and run the jar

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: AQL listing and download map (`aql.py`, `downloadmap.py`)

**Files:**
- Create: `uc/uc/aql.py`, `uc/uc/downloadmap.py`
- Test: `tests/unit/test_aql.py`, `tests/unit/test_downloadmap.py`

**Interfaces:**
- Consumes: `uc.http.USER_AGENT`, `tests/support/httpstub.StubServer`
- Produces:
  - `uc.aql.AQL_TEMPLATE` (use it as `% repository`), `WAR_GROUP`, `WAR_ARTIFACT`, and `AqlError(UcError)`
  - `RepoFile(path, name, sha256=None, actual_sha1=None, size=0, modified="")`
  - `Gav(group_id, artifact_id, version, extension)` with `.maven_path` and `.with_extension(ext)`
  - `to_gav(RepoFile) -> Gav | None`
  - `query_repository(api_url, repository, username, password, timeout=600) -> list[RepoFile]`
  - `Listing(plugins, plugins_without_pom, wars, wars_without_pom, rejected)`, where every field is a tuple
  - `classify(files) -> Listing`
  - `uc.downloadmap.build_download_map(listing, preferred_groups: Mapping[str, str]) -> tuple[dict[str, str], list[str]]`
  - `preferred_groups(update_center: Mapping) -> dict[str, str]`
  - `write_download_map(mapping, path) -> None` and `read_download_map(path) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_aql.py`:

```python
import base64
import json

import pytest
from httpstub import StubServer

from uc.aql import AQL_TEMPLATE, AqlError, Gav, RepoFile, classify, query_repository, to_gav


@pytest.mark.parametrize(
    "path,name,expected",
    [
        ("org/jenkins-ci/plugins/git/5.2.0", "git-5.2.0.hpi", Gav("org.jenkins-ci.plugins", "git", "5.2.0", "hpi")),
        ("org/jenkins-ci/main/jenkins-war/2.528.1", "jenkins-war-2.528.1.war",
         Gav("org.jenkins-ci.main", "jenkins-war", "2.528.1", "war")),
        ("io/jenkins/plugins/foo/1.0", "foo-1.0.pom", Gav("io.jenkins.plugins", "foo", "1.0", "pom")),
        ("plugins/git/5.2.0", "git.hpi", None),
        ("git", "git-5.2.0.hpi", None),
        ("org/x/git/5.2.0", "git-5.2.0-sources.hpi", None),
        ("org/x/git/5.2.0", "git 5.2.0.hpi", None),
    ],
)
def test_to_gav(path, name, expected):
    assert to_gav(RepoFile(path, name)) == expected


def test_maven_path_and_with_extension():
    gav = Gav("org.jenkins-ci.plugins", "git", "5.2.0", "hpi")
    assert gav.maven_path == "org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
    assert gav.with_extension("pom").maven_path == "org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"


def test_classify():
    listing = classify([
        RepoFile("org/jenkins-ci/plugins/git/5.2.0", "git-5.2.0.hpi"),
        RepoFile("org/jenkins-ci/plugins/git/5.2.0", "git-5.2.0.pom"),
        RepoFile("org/jenkins-ci/plugins/lonely/1.0", "lonely-1.0.hpi"),
        RepoFile("org/jenkins-ci/main/jenkins-war/2.528.1", "jenkins-war-2.528.1.war"),
        RepoFile("org/jenkins-ci/main/jenkins-war/2.528.1", "jenkins-war-2.528.1.pom"),
        RepoFile("org/jenkins-ci/main/jenkins-war/2.540", "jenkins-war-2.540.war"),
        RepoFile("org/other/something-war/1.0", "something-war-1.0.war"),
        RepoFile("org/other/something-war/1.0", "something-war-1.0.pom"),
        RepoFile("plugins/git/5.2.0", "git.hpi"),
    ])
    assert [g.artifact_id for g in listing.plugins] == ["git"]
    assert [g.maven_path for g in listing.plugins_without_pom] == ["org/jenkins-ci/plugins/lonely/1.0/lonely-1.0.hpi"]
    assert [g.version for g in listing.wars] == ["2.528.1"]
    assert [g.version for g in listing.wars_without_pom] == ["2.540"]
    assert listing.rejected == (RepoFile("plugins/git/5.2.0", "git.hpi"),)


def test_query_repository_sends_the_upstream_query():
    payload = {"results": [{
        "repo": "jenkins", "path": "org/jenkins-ci/plugins/git/5.2.0", "name": "git-5.2.0.hpi",
        "sha256": "ab", "actual_sha1": "cd", "size": 10, "modified": "2026-09-01T00:00:00.000Z",
    }]}
    routes = {("POST", "/artifactory/api/search/aql"): (200, {"Content-Type": "application/json"}, json.dumps(payload).encode())}
    with StubServer(routes) as stub:
        files = query_repository(stub.url + "/artifactory/api/", "jenkins", "uc", "secret")
    assert files == [RepoFile("org/jenkins-ci/plugins/git/5.2.0", "git-5.2.0.hpi", "ab", "cd", 10, "2026-09-01T00:00:00.000Z")]
    _, _, headers, body = stub.requests[0]
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"uc:secret").decode()
    assert body.decode() == AQL_TEMPLATE % "jenkins"


def test_query_repository_http_error():
    routes = {("POST", "/api/search/aql"): (403, {}, b"forbidden")}
    with StubServer(routes) as stub:
        with pytest.raises(AqlError, match="HTTP 403: forbidden"):
            query_repository(stub.url + "/api/", "jenkins", "uc", "secret")


def test_query_repository_rejects_odd_repository_names():
    with pytest.raises(AqlError, match="repository name"):
        query_repository("http://127.0.0.1:1/api/", 'a"b', "u", "p")
```

`tests/unit/test_downloadmap.py`:

```python
from uc.aql import RepoFile, classify
from uc.downloadmap import build_download_map, preferred_groups, read_download_map, write_download_map


def files(group_path, artifact, version, *extensions):
    return [RepoFile(f"{group_path}/{artifact}/{version}", f"{artifact}-{version}.{ext}") for ext in extensions]


def test_map_covers_plugins_and_wars_with_poms():
    listing = classify(
        files("org/jenkins-ci/plugins", "git", "5.2.0", "hpi", "pom")
        + files("org/jenkins-ci/main", "jenkins-war", "2.528.1", "war", "pom")
        + files("org/jenkins-ci/plugins", "lonely", "1.0", "hpi")
    )
    mapping, warnings = build_download_map(listing, {})
    assert mapping == {
        "plugins/git/5.2.0": "org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi",
        "war/2.528.1": "org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war",
    }
    assert warnings == []


def test_group_conflict_prefers_the_offered_group():
    listing = classify(
        files("org/jenkins-ci/plugins", "foo", "1.0", "hpi", "pom")
        + files("io/jenkins/plugins", "foo", "1.0", "hpi", "pom")
    )
    mapping, warnings = build_download_map(listing, {"foo": "io.jenkins.plugins"})
    assert mapping["plugins/foo/1.0"] == "io/jenkins/plugins/foo/1.0/foo-1.0.hpi"
    assert warnings == [
        "plugins/foo/1.0 exists under several groupIds (io.jenkins.plugins, org.jenkins-ci.plugins); using io.jenkins.plugins"
    ]


def test_hpi_is_preferred_over_jpi():
    listing = classify(files("org/jenkins-ci/plugins", "foo", "1.0", "jpi", "hpi", "pom"))
    assert build_download_map(listing, {})[0]["plugins/foo/1.0"].endswith("foo-1.0.hpi")


def test_jpi_only_plugin_maps_to_jpi():
    listing = classify(files("org/jenkins-ci/plugins", "old", "1.0", "jpi", "pom"))
    assert build_download_map(listing, {})[0]["plugins/old/1.0"] == "org/jenkins-ci/plugins/old/1.0/old-1.0.jpi"


def test_preferred_groups():
    doc = {"plugins": {"git": {"gav": "org.jenkins-ci.plugins:git:5.2.0"}, "odd": {"gav": "broken"}, "none": {}}}
    assert preferred_groups(doc) == {"git": "org.jenkins-ci.plugins"}


def test_write_and_read_roundtrip(tmp_path):
    path = tmp_path / ".htdownload-map"
    write_download_map({"war/2.528.1": "w.war", "plugins/git/5.2.0": "g.hpi"}, path)
    assert path.read_text() == "plugins/git/5.2.0 g.hpi\nwar/2.528.1 w.war\n"
    assert read_download_map(path) == {"war/2.528.1": "w.war", "plugins/git/5.2.0": "g.hpi"}
    write_download_map({}, path)
    assert path.read_text() == ""
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'aql or downloadmap'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.aql'`.

- [ ] **Step 3: Implement `uc/uc/aql.py`**

```python
"""Artifactory listing through the same AQL query update-center2 runs (ArtifactoryRepositoryImpl.java)."""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from . import http
from .errors import UcError

AQL_TEMPLATE = (
    'items.find({"repo":{"$eq":"%s"},"$or":[{"name":{"$match":"*.hpi"}},{"name":{"$match":"*.jpi"}},'
    '{"name":{"$match":"*.war"}},{"name":{"$match":"*.pom"}}]})'
    '.include("repo", "path", "name", "modified", "created", "sha256", "actual_sha1", "size")'
)
WAR_GROUP = "org.jenkins-ci.main"
WAR_ARTIFACT = "jenkins-war"
_ALLOWED = re.compile(r"^[0-9A-Za-z+\-./_]*$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9._-]+$")


class AqlError(UcError):
    pass


@dataclass(frozen=True)
class RepoFile:
    path: str
    name: str
    sha256: str | None = None
    actual_sha1: str | None = None
    size: int = 0
    modified: str = ""


@dataclass(frozen=True)
class Gav:
    group_id: str
    artifact_id: str
    version: str
    extension: str

    @property
    def maven_path(self) -> str:
        return (f"{self.group_id.replace('.', '/')}/{self.artifact_id}/{self.version}/"
                f"{self.artifact_id}-{self.version}.{self.extension}")

    def with_extension(self, extension: str) -> "Gav":
        return Gav(self.group_id, self.artifact_id, self.version, extension)


def to_gav(f: RepoFile) -> Gav | None:
    """Port of ArtifactoryRepositoryImpl.toGav: None when the file is not in Maven layout."""
    if not _ALLOWED.match(f.name) or not _ALLOWED.match(f.path):
        return None
    cut = f.path.rfind("/")
    if cut <= 0:
        return None
    version, ga = f.path[cut + 1:], f.path[:cut]
    cut = ga.rfind("/")
    if cut <= 0:
        return None
    artifact, group = ga[cut + 1:], ga[:cut].replace("/", ".")
    dot = f.name.rfind(".")
    if dot <= 0:
        return None
    extension = f.name[dot + 1:]
    if f.name != f"{artifact}-{version}.{extension}":
        return None
    return Gav(group, artifact, version, extension)


def query_repository(
    api_url: str, repository: str, username: str, password: str, timeout: float = 600
) -> list[RepoFile]:
    if not _REPOSITORY.match(repository):
        raise AqlError(f"unsupported repository name {repository!r}")
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(
        api_url + "search/aql",
        data=(AQL_TEMPLATE % repository).encode(),
        method="POST",
        headers={"Content-Type": "text/plain", "Accept": "application/json",
                 "Authorization": f"Basic {token}", "User-Agent": http.USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise AqlError(f"AQL query to {api_url} failed with HTTP {exc.code}: {detail}") from exc
    except (OSError, ValueError) as exc:
        raise AqlError(f"AQL query to {api_url} failed: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise AqlError("AQL response has no results list")
    return [
        RepoFile(r.get("path", ""), r.get("name", ""), r.get("sha256"), r.get("actual_sha1"),
                 int(r.get("size") or 0), r.get("modified") or "")
        for r in results
    ]


@dataclass(frozen=True)
class Listing:
    plugins: tuple[Gav, ...]  # .hpi/.jpi with a matching .pom
    plugins_without_pom: tuple[Gav, ...]
    wars: tuple[Gav, ...]  # org.jenkins-ci.main:jenkins-war with a matching .pom
    wars_without_pom: tuple[Gav, ...]
    rejected: tuple[RepoFile, ...]  # not in Maven layout


def classify(files: Iterable[RepoFile]) -> Listing:
    gavs: list[Gav] = []
    rejected: list[RepoFile] = []
    for f in files:
        gav = to_gav(f)
        if gav is None:
            rejected.append(f)
        else:
            gavs.append(gav)
    poms = {(g.group_id, g.artifact_id, g.version) for g in gavs if g.extension == "pom"}

    def has_pom(g: Gav) -> bool:
        return (g.group_id, g.artifact_id, g.version) in poms

    def ordered(items: Iterable[Gav]) -> tuple[Gav, ...]:
        return tuple(sorted(items, key=lambda g: (g.artifact_id, g.version, g.group_id, g.extension)))

    plugins = [g for g in gavs if g.extension in ("hpi", "jpi")]
    wars = [g for g in gavs
            if g.extension == "war" and g.group_id == WAR_GROUP and g.artifact_id == WAR_ARTIFACT]
    return Listing(
        ordered(g for g in plugins if has_pom(g)),
        ordered(g for g in plugins if not has_pom(g)),
        ordered(g for g in wars if has_pom(g)),
        ordered(g for g in wars if not has_pom(g)),
        tuple(sorted(rejected, key=lambda f: (f.path, f.name))),
    )
```

- [ ] **Step 4: Implement `uc/uc/downloadmap.py`**

```python
"""The .htdownload-map RewriteMap: /download/... keys to Maven paths in Artifactory (spec §6 step 8, §8.2)."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .aql import Gav, Listing


def _choose(options: list[Gav], preferred_group: str | None) -> Gav:
    # The groupId the generator offered first, then .hpi over .jpi, then lexical order.
    return sorted(options, key=lambda g: (g.group_id != preferred_group, g.extension != "hpi", g.group_id))[0]


def build_download_map(
    listing: Listing, preferred_groups: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    candidates: dict[str, list[Gav]] = {}
    for gav in listing.plugins:
        candidates.setdefault(f"plugins/{gav.artifact_id}/{gav.version}", []).append(gav)
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for key, options in sorted(candidates.items()):
        chosen = _choose(options, preferred_groups.get(options[0].artifact_id))
        groups = sorted({g.group_id for g in options})
        if len(groups) > 1:
            warnings.append(f"{key} exists under several groupIds ({', '.join(groups)}); using {chosen.group_id}")
        mapping[key] = chosen.maven_path
    for gav in listing.wars:
        mapping[f"war/{gav.version}"] = gav.maven_path
    return mapping, warnings


def preferred_groups(update_center: Mapping) -> dict[str, str]:
    result: dict[str, str] = {}
    for plugin_id, entry in (update_center.get("plugins") or {}).items():
        gav = entry.get("gav", "") if isinstance(entry, dict) else ""
        if isinstance(gav, str) and gav.count(":") == 2:
            result[plugin_id] = gav.split(":", 1)[0]
    return result


def write_download_map(mapping: Mapping[str, str], path: Path) -> None:
    path.write_text("".join(f"{key} {value}\n" for key, value in sorted(mapping.items())), encoding="utf-8")


def read_download_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            result[key] = value
    return result
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'aql or downloadmap'"`
Expected: `18 passed`.

- [ ] **Step 6: Commit**

```bash
git add uc/uc/aql.py uc/uc/downloadmap.py tests/unit/test_aql.py tests/unit/test_downloadmap.py
git commit -m "Add AQL listing with upstream's Maven-layout rules and download map

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Site layout and tool installers (`layout.py`, `templates/index.html`, site fixture)

**Files:**
- Create: `uc/uc/layout.py`, `uc/templates/index.html`, `tests/support/sitefixture.py`
- Test: `tests/unit/test_layout.py`

**Interfaces:**
- Consumes: `Tiers`, `uc.http`
- Produces:
  - `uc.layout.TOP_LEVEL_LINKS` and `ToolInstallersError(UcError)`
  - `site_dirs(root: Path, tiers: Tiers) -> list[Path]`, in the order: weekly tiers, stable tiers, `experimental`, `current`
  - `finalize_layout(staging: Path, tiers: Tiers, templates: Path) -> None`
  - `mirror_tool_installers(index_url: str, dest: Path, previous: Path | None, get=http.get) -> list[str]`, which returns warnings
  - `tests/support/sitefixture`:
    - `TEST_CERT_B64`
    - `uc_document(public_url, *, core="2.528.1", plugins=None, cert_b64=TEST_CERT_B64, required_core="2.479.1") -> dict`
    - `write_site(site: Path, doc: dict, *, current=False) -> None`
    - `write_generator_output(staging: Path, public_url: str, tiers: Tiers, **doc_options) -> None`

- [ ] **Step 1: Add the landing page and the site fixture**

`uc/templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Jenkins Update Center</title>
</head>
<body>
<h1>Jenkins Update Center</h1>
<p>Point Jenkins at <code>update-center.json</code> on this server; it is redirected to the site that matches the Jenkins version.</p>
<ul>
<li><a href="update-center.json">update-center.json</a> · <a href="tiers.json">tiers.json</a> · <a href="/status.json">status.json</a></li>
<li><a href="current/">current/</a> · <a href="stable/">stable/</a> · <a href="experimental/">experimental/</a></li>
<li><a href="download/">download/</a></li>
</ul>
</body>
</html>
```

`tests/support/sitefixture.py`:

```python
"""Minimal, schema-realistic update-center2 output for tests (what the jar writes per --www-dir)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from uc.model import Tiers

DEFAULT_PLUGINS: Mapping[str, str] = {"git": "5.2.0", "credentials": "1415.v831096eb_5534"}
TEST_CERT_B64 = "VEVTVC1DRVJU"


def uc_document(
    public_url: str,
    *,
    core: str | None = "2.528.1",
    plugins: Mapping[str, str] | None = None,
    cert_b64: str = TEST_CERT_B64,
    required_core: str = "2.479.1",
) -> dict:
    plugins = DEFAULT_PLUGINS if plugins is None else plugins
    download = f"{public_url}/download"
    doc = {
        "connectionCheckUrl": f"{public_url}/uctest.json",
        "deprecations": {},
        "generationTimestamp": "2026-09-22T00:00:00Z",
        "id": "default",
        "plugins": {
            pid: {
                "name": pid,
                "version": version,
                "url": f"{download}/plugins/{pid}/{version}/{pid}.hpi",
                "gav": f"org.jenkins-ci.plugins:{pid}:{version}",
                "requiredCore": required_core,
                "dependencies": [],
                "sha256": "c2hh",
            }
            for pid, version in plugins.items()
        },
        "signature": {
            "certificates": [cert_b64],
            "correct_digest": "ZA==",
            "correct_digest512": "ZA==",
            "correct_signature": "cw==",
            "correct_signature512": "cw==",
        },
        "updateCenterVersion": "1",
        "warnings": [],
    }
    if core:
        doc["core"] = {
            "buildDate": "Sep 01, 2026",
            "name": "core",
            "sha256": "c2hh",
            "url": f"{download}/war/{core}/jenkins.war",
            "version": core,
        }
    return doc


def write_site(site: Path, doc: dict, *, current: bool = False) -> None:
    site.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2)
    (site / "update-center.actual.json").write_text(text)
    (site / "update-center.json").write_text(f"updateCenter.post(\n{text}\n);")
    (site / "latestCore.txt").write_text(doc.get("core", {}).get("version", ""))
    (site / "timestamp.txt").write_text(doc["generationTimestamp"])
    if not current:
        return
    versions = {
        "plugins": {
            pid: {p["version"]: {"url": p["url"], "version": p["version"],
                                 "requiredCore": p["requiredCore"], "sha256": p["sha256"]}}
            for pid, p in doc["plugins"].items()
        },
        "signature": doc["signature"],
        "updateCenterVersion": "1",
    }
    (site / "plugin-versions.json").write_text(json.dumps(versions, indent=2))
    (site / "release-history.json").write_text(json.dumps({"releaseHistory": []}))
    (site / "plugin-documentation-urls.json").write_text("{}")
    (site / "pluginCount.txt").write_text(str(len(doc["plugins"])))
    latest = site / "latest"
    latest.mkdir(exist_ok=True)
    rules = "".join(f"RewriteRule ^{pid}\\.hpi$ {p['url']} [R=302,L]\n" for pid, p in doc["plugins"].items())
    if "core" in doc:
        rules += f"RewriteRule ^jenkins\\.war$ {doc['core']['url']} [R=302,L]\n"
    (latest / ".htaccess").write_text("# GENERATED. DO NOT MODIFY.\nRewriteEngine on\n" + rules)


def write_generator_output(staging: Path, public_url: str, tiers: Tiers, **doc_options) -> None:
    for version in tiers.weekly:
        write_site(staging / f"dynamic-{version}", uc_document(public_url, **doc_options))
    for version in tiers.stable:
        write_site(staging / f"dynamic-stable-{version}", uc_document(public_url, **doc_options))
    write_site(staging / "experimental", uc_document(public_url, **doc_options))
    doc = uc_document(public_url, **doc_options)
    write_site(staging / "current", doc, current=True)
    for pid in doc["plugins"]:
        index = staging / "download" / "plugins" / pid
        index.mkdir(parents=True, exist_ok=True)
        (index / "index.html").write_text(f"<html><body>{pid}</body></html>")
    (staging / "download" / "war").mkdir(parents=True, exist_ok=True)
    (staging / "download" / "war" / "index.html").write_text("<html><body>jenkins.war</body></html>")
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_layout.py`:

```python
import os
from pathlib import Path

from sitefixture import write_generator_output

from uc.http import HttpError
from uc.layout import finalize_layout, mirror_tool_installers, site_dirs
from uc.model import Tiers

TEMPLATES = Path("/opt/uc/templates")
URL = "https://uc.example.com"
INDEX = "https://updates.jenkins.io/updates/"
MAVEN = "hudson.tasks.Maven.MavenInstaller.json"


def test_finalize_layout_matches_upstream(tmp_path):
    tiers = Tiers(("2.540",), ("2.528.1", "2.528.3"))
    write_generator_output(tmp_path, URL, tiers)
    finalize_layout(tmp_path, tiers, TEMPLATES)
    assert [p.name for p in site_dirs(tmp_path, tiers)] == [
        "dynamic-2.540", "dynamic-stable-2.528.1", "dynamic-stable-2.528.3", "experimental", "current",
    ]
    for site in site_dirs(tmp_path, tiers):
        assert os.readlink(site / "updates") == "../updates"
    assert (tmp_path / "updates").is_dir()
    assert os.readlink(tmp_path / "stable") == "dynamic-stable-2.528.3"
    assert os.readlink(tmp_path / "update-center.json") == "current/update-center.json"
    assert os.readlink(tmp_path / "latest") == "current/latest"
    assert (tmp_path / "latestCore.txt").read_text() == "2.528.1"
    assert (tmp_path / "uctest.json").read_bytes() == b"{}\n"
    assert "Jenkins Update Center" in (tmp_path / "index.html").read_text()


def test_without_stable_tiers_stable_is_current(tmp_path):
    write_generator_output(tmp_path, URL, Tiers())
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    assert os.readlink(tmp_path / "stable") == "current"


def test_finalize_layout_is_idempotent(tmp_path):
    write_generator_output(tmp_path, URL, Tiers())
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    finalize_layout(tmp_path, Tiers(), TEMPLATES)
    assert os.readlink(tmp_path / "current" / "updates") == "../updates"


def getter(files):
    def get(url, headers=None, timeout=60):
        if url not in files:
            raise HttpError(f"GET {url} failed: HTTP Error 404")
        return files[url]
    return get


LISTING = (
    b'<a href="?C=N;O=A">Name</a><a href="/">Parent</a>'
    b'<a href="hudson.tasks.Maven.MavenInstaller.json">x</a>'
    b'<a href="hudson.tasks.Maven.MavenInstaller.json.html">y</a>'
)


def test_tool_installers_are_copied_verbatim(tmp_path):
    files = {
        INDEX: LISTING,
        INDEX + MAVEN: b"downloadService.post('hudson.tasks.Maven.MavenInstaller',{\"list\":[]})",
        INDEX + MAVEN + ".html": b"<html>postMessage</html>",
    }
    assert mirror_tool_installers(INDEX, tmp_path / "updates", None, get=getter(files)) == []
    assert (tmp_path / "updates" / MAVEN).read_bytes() == files[INDEX + MAVEN]
    assert (tmp_path / "updates" / (MAVEN + ".html")).is_file()


def test_bad_tool_installer_keeps_previous_copy(tmp_path):
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / MAVEN).write_text("downloadService.post('old',{})")
    files = {INDEX: LISTING, INDEX + MAVEN: b"<html>error page</html>", INDEX + MAVEN + ".html": b"x"}
    warnings = mirror_tool_installers(INDEX, tmp_path / "updates", previous, get=getter(files))
    assert "kept the previous copy" in warnings[0]
    assert (tmp_path / "updates" / MAVEN).read_text() == "downloadService.post('old',{})"


def test_unreachable_tool_installers_without_previous(tmp_path):
    warnings = mirror_tool_installers(INDEX, tmp_path / "updates", None, get=getter({}))
    assert warnings[0].startswith("tool installers not mirrored")
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k layout"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.layout'`.

- [ ] **Step 4: Implement `uc/uc/layout.py`**

```python
"""Everything update-center2 does not write itself (spec §6 step 8), as upstream site/generate.sh:135-166."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

from . import http
from .errors import UcError
from .model import Tiers

TOP_LEVEL_LINKS = (
    "latest", "latestCore.txt", "plugin-documentation-urls.json", "release-history.json",
    "plugin-versions.json", "update-center.json", "update-center.actual.json",
)
_INSTALLER_HREF = re.compile(r'href="([A-Za-z0-9._-]+\.json(?:\.html)?)"')
_JSONP_PREFIX = b"downloadService.post("


class ToolInstallersError(UcError):
    pass


def site_dirs(root: Path, tiers: Tiers) -> list[Path]:
    return (
        [root / f"dynamic-{v}" for v in tiers.weekly]
        + [root / f"dynamic-stable-{v}" for v in tiers.stable]
        + [root / "experimental", root / "current"]
    )


def _symlink(target: str, link: Path) -> None:
    if link.is_symlink() or link.is_file():
        link.unlink()
    os.symlink(target, link)


def finalize_layout(staging: Path, tiers: Tiers, templates: Path) -> None:
    (staging / "updates").mkdir(exist_ok=True)
    for site in site_dirs(staging, tiers):
        _symlink("../updates", site / "updates")
    _symlink(f"dynamic-stable-{tiers.stable[-1]}" if tiers.stable else "current", staging / "stable")
    for name in TOP_LEVEL_LINKS:
        _symlink(f"current/{name}", staging / name)
    (staging / "uctest.json").write_text("{}\n")
    shutil.copyfile(templates / "index.html", staging / "index.html")


def mirror_tool_installers(
    index_url: str, dest: Path, previous: Path | None, get: http.Getter = http.get
) -> list[str]:
    """Copy upstream's signed tool-installer metadata unmodified; fall back to the previous copy."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        names = sorted(set(_INSTALLER_HREF.findall(get(index_url).decode("utf-8", "replace"))))
        if not names:
            raise ToolInstallersError(f"{index_url} lists no tool installer files")
        fetched: dict[str, bytes] = {}
        for name in names:
            body = get(urljoin(index_url, name))
            if name.endswith(".json") and not body.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(_JSONP_PREFIX):
                raise ToolInstallersError(f"{name} does not look like tool installer metadata")
            fetched[name] = body
    except (http.HttpError, ToolInstallersError) as exc:
        if previous is not None and previous.is_dir():
            for item in previous.iterdir():
                if item.is_file():
                    shutil.copyfile(item, dest / item.name)
            return [f"tool installers not refreshed ({exc}); kept the previous copy"]
        return [f"tool installers not mirrored ({exc})"]
    for name, body in fetched.items():
        (dest / name).write_bytes(body)
    return []
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k layout"`
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add uc/uc/layout.py uc/templates/index.html tests/support/sitefixture.py tests/unit/test_layout.py
git commit -m "Finish site layout like upstream and mirror tool installers

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---
### Task 10: Release validation (`validate.py`)

**Files:**
- Create: `uc/uc/validate.py`
- Test: `tests/unit/test_validate.py`

**Interfaces:**
- Consumes: `uc.htaccess.absolute_targets`, `uc.layout.site_dirs`, `Tiers`, `tests/support/sitefixture`
- Produces:
  - `ValidationError(UcError)`, which carries `.problems: list[str]`
  - `ValidationResult(plugin_count: int, offered: frozenset[str])`
  - `ROOT_FILES`, `SITE_FILES`, `CURRENT_FILES`
  - `load_site_json(path) -> Any`, which strips the `updateCenter.post(...);` wrapper
  - `count_plugins(site_dir) -> int | None`
  - `download_key(url, downloads_root) -> str | None`, which returns `"plugins/<id>/<ver>"` or `"war/<ver>"`
  - `validate_release(staging, *, tiers, downloads_root, signing_cert_b64, previous_plugin_count, min_plugins, min_ratio, force, download_map) -> ValidationResult`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_validate.py`:

```python
import json
from pathlib import Path

import pytest
from sitefixture import TEST_CERT_B64, write_generator_output

from uc.layout import finalize_layout
from uc.model import Tiers
from uc.validate import ValidationError, count_plugins, download_key, load_site_json, validate_release

URL = "https://uc.example.com"
ROOT = URL + "/download"
TIERS = Tiers(("2.540",), ("2.528.1",))
TEMPLATES = Path("/opt/uc/templates")
OFFERED = {"plugins/git/5.2.0", "plugins/credentials/1415.v831096eb_5534", "war/2.528.1"}


def build(staging, tiers=TIERS, public_url=URL, **doc_options):
    write_generator_output(staging, public_url, tiers, **doc_options)
    finalize_layout(staging, tiers, TEMPLATES)
    (staging / ".htaccess").write_text("RewriteEngine on\n")
    (staging / ".htdownload-map").write_text("")
    (staging / "tiers.json").write_text(json.dumps({"weeklyCores": list(tiers.weekly), "stableCores": list(tiers.stable)}))
    return staging


def validate(staging, **overrides):
    options = dict(tiers=TIERS, downloads_root=ROOT, signing_cert_b64=TEST_CERT_B64,
                   previous_plugin_count=None, min_plugins=1, min_ratio=0.9, force=False, download_map=None)
    options.update(overrides)
    return validate_release(staging, **options)


def test_valid_release(tmp_path):
    result = validate(build(tmp_path))
    assert result.plugin_count == 2
    assert result.offered == OFFERED


def test_load_site_json_strips_jsonp(tmp_path):
    (tmp_path / "a.json").write_text('updateCenter.post(\n{"a": 1}\n);')
    (tmp_path / "b.json").write_text('{"b": 2}')
    assert load_site_json(tmp_path / "a.json") == {"a": 1}
    assert load_site_json(tmp_path / "b.json") == {"b": 2}


def test_download_key():
    assert download_key(ROOT + "/plugins/git/5.2.0/git.hpi", ROOT) == "plugins/git/5.2.0"
    assert download_key(ROOT + "/war/2.528.1/jenkins.war", ROOT) == "war/2.528.1"
    assert download_key("https://updates.jenkins.io/download/plugins/git/5.2.0/git.hpi", ROOT) is None
    assert download_key(ROOT + "/plugins/git/5.2.0/other.hpi", ROOT) is None


def test_missing_files(tmp_path):
    staging = build(tmp_path)
    (staging / ".htdownload-map").unlink()
    (staging / "current" / "plugin-versions.json").unlink()
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert "missing .htdownload-map" in err.value.problems
    assert "current/ is missing plugin-versions.json" in err.value.problems


def test_missing_tier_directory(tmp_path):
    staging = build(tmp_path, tiers=Tiers())
    with pytest.raises(ValidationError, match="dynamic-2.540/ is missing update-center.json"):
        validate(staging)


def test_wrong_signing_certificate(tmp_path):
    with pytest.raises(ValidationError, match="not signed with the configured signing certificate"):
        validate(build(tmp_path), signing_cert_b64="OTHER")


def test_download_urls_must_use_public_url(tmp_path):
    with pytest.raises(ValidationError, match="download URLs outside https://uc.example.com/download/"):
        validate(build(tmp_path, public_url="https://updates.jenkins.io"))


def test_htaccess_must_not_redirect_elsewhere(tmp_path):
    staging = build(tmp_path)
    (staging / ".htaccess").write_text("RewriteRule ^x https://get.jenkins.io/x [R]\n")
    with pytest.raises(ValidationError, match="redirects to an absolute URL"):
        validate(staging)


def test_plugin_count_ratio_and_force(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(ValidationError, match="plugin count 2 < 0.9 × 10"):
        validate(staging, previous_plugin_count=10)
    assert validate(staging, previous_plugin_count=10, force=True).plugin_count == 2


def test_min_plugins(tmp_path):
    with pytest.raises(ValidationError, match="below MIN_PLUGINS=5"):
        validate(build(tmp_path), min_plugins=5)


def test_download_map_must_cover_offered_versions(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(ValidationError, match="no entry for plugins/credentials/1415.v831096eb_5534, war/2.528.1"):
        validate(staging, download_map={"plugins/git/5.2.0": "a"})
    validate(staging, download_map={key: "x" for key in OFFERED})


def test_current_without_core(tmp_path):
    with pytest.raises(ValidationError, match="offers no Jenkins core"):
        validate(build(tmp_path, core=None))


def test_count_plugins(tmp_path):
    assert count_plugins(build(tmp_path) / "current") == 2
    assert count_plugins(tmp_path / "nope") is None
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k validate"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.validate'`.

- [ ] **Step 3: Implement `uc/uc/validate.py`**

```python
"""Checks a staged release must pass before it becomes live (spec §6 step 9)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import UcError
from .htaccess import absolute_targets
from .layout import site_dirs
from .model import Tiers

JSONP_PREFIX = "updateCenter.post("
SITE_FILES = ("update-center.json", "update-center.actual.json")
CURRENT_FILES = SITE_FILES + (
    "plugin-versions.json", "release-history.json", "plugin-documentation-urls.json",
    "latestCore.txt", "pluginCount.txt", "timestamp.txt",
)
ROOT_FILES = (".htaccess", ".htdownload-map", "tiers.json", "uctest.json", "index.html")
MAX_LISTED = 5


class ValidationError(UcError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("release failed validation: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ValidationResult:
    plugin_count: int
    offered: frozenset[str]


def load_site_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith(JSONP_PREFIX):
        text = text[len(JSONP_PREFIX):].rstrip()
        if text.endswith(");"):
            text = text[:-2]
    return json.loads(text)


def count_plugins(site: Path) -> int | None:
    try:
        return len(load_site_json(site / "update-center.actual.json").get("plugins") or {})
    except (OSError, ValueError, AttributeError):
        return None


def download_key(url: str, downloads_root: str) -> str | None:
    prefix = downloads_root.rstrip("/") + "/"
    if not url.startswith(prefix):
        return None
    parts = url[len(prefix):].split("/")
    if len(parts) == 4 and parts[0] == "plugins" and parts[3] == f"{parts[1]}.hpi":
        return f"plugins/{parts[1]}/{parts[2]}"
    if len(parts) == 3 and parts[0] == "war" and parts[2] == "jenkins.war":
        return f"war/{parts[1]}"
    return None


def _listed(items: Iterable[str]) -> str:
    ordered = sorted(items)
    more = len(ordered) - MAX_LISTED
    return ", ".join(ordered[:MAX_LISTED]) + (f" (+{more} more)" if more > 0 else "")


def validate_release(
    staging: Path,
    *,
    tiers: Tiers,
    downloads_root: str,
    signing_cert_b64: str,
    previous_plugin_count: int | None,
    min_plugins: int,
    min_ratio: float,
    force: bool,
    download_map: Mapping[str, str] | None,
) -> ValidationResult:
    problems: list[str] = []
    offered: set[str] = set()
    bad_urls: set[str] = set()

    for name in ROOT_FILES:
        if not (staging / name).is_file():
            problems.append(f"missing {name}")

    def check_signature(doc: Any, label: str) -> None:
        signature = doc.get("signature") if isinstance(doc, dict) else None
        certificates = signature.get("certificates") if isinstance(signature, dict) else None
        if not certificates or certificates[0] != signing_cert_b64:
            problems.append(f"{label} is not signed with the configured signing certificate")

    def check_url(url: Any, label: str, collect: bool) -> None:
        key = download_key(url, downloads_root) if isinstance(url, str) else None
        if key is None:
            bad_urls.add(f"{label} {url}")
        elif collect:
            offered.add(key)

    for site in site_dirs(staging, tiers):
        required = CURRENT_FILES if site.name == "current" else SITE_FILES
        missing = [name for name in required if not (site / name).is_file()]
        if missing:
            problems.append(f"{site.name}/ is missing {', '.join(missing)}")
            continue
        try:
            doc = load_site_json(site / "update-center.actual.json")
            load_site_json(site / "update-center.json")
        except ValueError as exc:
            problems.append(f"{site.name}/ update-center JSON does not parse: {exc}")
            continue
        check_signature(doc, f"{site.name}/update-center.actual.json")
        core = doc.get("core")
        if isinstance(core, dict):
            check_url(core.get("url"), f"{site.name} core", collect=True)
        elif site.name == "current":
            problems.append(
                "current/ offers no Jenkins core (the source needs jenkins-war .war and .pom; see 'uc doctor')"
            )
        for plugin_id, plugin in (doc.get("plugins") or {}).items():
            url = plugin.get("url") if isinstance(plugin, dict) else None
            check_url(url, f"{site.name} {plugin_id}", collect=True)

    versions_file = staging / "current" / "plugin-versions.json"
    if versions_file.is_file():
        try:
            versions = load_site_json(versions_file)
        except ValueError as exc:
            problems.append(f"current/plugin-versions.json does not parse: {exc}")
        else:
            check_signature(versions, "current/plugin-versions.json")
            for plugin_id, by_version in (versions.get("plugins") or {}).items():
                for version, entry in (by_version or {}).items():
                    url = entry.get("url") if isinstance(entry, dict) else None
                    check_url(url, f"plugin-versions {plugin_id}:{version}", collect=False)

    if bad_urls:
        problems.append(f"download URLs outside {downloads_root}/: {_listed(bad_urls)}")

    htaccess = staging / ".htaccess"
    if htaccess.is_file():
        for line in absolute_targets(htaccess.read_text(encoding="utf-8")):
            problems.append(f".htaccess redirects to an absolute URL: {line}")

    count = count_plugins(staging / "current") or 0
    if count < min_plugins:
        problems.append(f"plugin count {count} is below MIN_PLUGINS={min_plugins}")
    if previous_plugin_count and not force and count < min_ratio * previous_plugin_count:
        problems.append(
            f"plugin count {count} < {min_ratio} × {previous_plugin_count} of the live release "
            "(use 'uc generate --force' if the shrinkage is intended)"
        )
    if download_map is not None:
        missing_keys = offered - set(download_map)
        if missing_keys:
            problems.append(f"download map has no entry for {_listed(missing_keys)}")

    if problems:
        raise ValidationError(problems)
    return ValidationResult(count, frozenset(offered))
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k validate"`
Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/validate.py tests/unit/test_validate.py
git commit -m "Validate staged releases before publishing

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Publishing, rollback and status (`publish.py`, `status.py`)

**Files:**
- Create: `uc/uc/publish.py`, `uc/uc/status.py`
- Test: `tests/unit/test_publish.py`, `tests/unit/test_status.py`

**Interfaces:**
- Produces:
  - `uc.publish`:
    - `RELEASE_RE`, `PublishError(UcError)` and `LockBusy(UcError)`
    - `RunLock(data_dir)`, a context manager
    - `releases_dir(data_dir)`, `new_release_id(now) -> str`, `staging_path(data_dir, rid)` and `create_staging(data_dir, rid) -> Path`
    - `live_release(data_dir) -> str | None` and `live_path(data_dir) -> Path | None`
    - `list_releases(data_dir) -> list[str]`
    - `swap_live(data_dir, rid)`, `publish(data_dir, staging, rid) -> Path`, `prune(data_dir, retain) -> list[str]`
    - `mark_failed(data_dir, rid)` and `rollback(data_dir, target=None) -> str`
  - `uc.status`: `STATUS_FILE`, `read_status(data_dir) -> dict`, `write_status(data_dir, status: dict)`, `update_status(data_dir, **fields) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_publish.py`:

```python
import datetime as dt
import os

import pytest

from uc.publish import (
    LockBusy,
    PublishError,
    RunLock,
    create_staging,
    list_releases,
    live_path,
    live_release,
    mark_failed,
    new_release_id,
    prune,
    publish,
    releases_dir,
    rollback,
    swap_live,
)

R1, R2, R3, R4 = "20260922T010000Z", "20260922T020000Z", "20260922T030000Z", "20260922T040000Z"


def make_release(data, rid):
    path = releases_dir(data) / rid
    path.mkdir(parents=True)
    (path / "marker").write_text(rid)
    return path


def test_release_id():
    assert new_release_id(dt.datetime(2026, 9, 22, 3, 4, 5, tzinfo=dt.timezone.utc)) == "20260922T030405Z"


def test_publish_swaps_live_atomically(tmp_path):
    staging = create_staging(tmp_path, R1)
    (staging / "marker").write_text("first")
    publish(tmp_path, staging, R1)
    assert os.readlink(tmp_path / "live") == f"releases/{R1}"
    assert (tmp_path / "live" / "marker").read_text() == "first"
    assert live_release(tmp_path) == R1
    assert live_path(tmp_path) == releases_dir(tmp_path) / R1
    assert not staging.exists()

    second = create_staging(tmp_path, R2)
    (second / "marker").write_text("second")
    publish(tmp_path, second, R2)
    assert (tmp_path / "live" / "marker").read_text() == "second"
    assert not (tmp_path / "live.tmp").exists()


def test_create_staging_replaces_leftovers(tmp_path):
    (create_staging(tmp_path, R1) / "leftover").write_text("x")
    assert not (create_staging(tmp_path, R1) / "leftover").exists()


def test_list_releases_ignores_staging_failed_and_junk(tmp_path):
    make_release(tmp_path, R2)
    make_release(tmp_path, R1)
    create_staging(tmp_path, R3)
    (releases_dir(tmp_path) / f".failed-{R4}").mkdir()
    (releases_dir(tmp_path) / "junk").mkdir()
    assert list_releases(tmp_path) == [R1, R2]


def test_prune_keeps_newest_and_live(tmp_path):
    for rid in (R1, R2, R3, R4):
        make_release(tmp_path, rid)
    swap_live(tmp_path, R1)  # e.g. after a rollback
    assert prune(tmp_path, 2) == [R2]
    assert list_releases(tmp_path) == [R1, R3, R4]


def test_mark_failed_keeps_only_the_latest_failure(tmp_path):
    create_staging(tmp_path, R1)
    mark_failed(tmp_path, R1)
    create_staging(tmp_path, R2)
    mark_failed(tmp_path, R2)
    assert sorted(p.name for p in releases_dir(tmp_path).glob(".failed-*")) == [f".failed-{R2}"]


def test_rollback(tmp_path):
    for rid in (R1, R2, R3):
        make_release(tmp_path, rid)
    swap_live(tmp_path, R3)
    assert rollback(tmp_path) == R2
    assert live_release(tmp_path) == R2
    assert rollback(tmp_path, R3) == R3
    with pytest.raises(PublishError, match="unknown release"):
        rollback(tmp_path, "nope")
    swap_live(tmp_path, R1)
    with pytest.raises(PublishError, match="no earlier release"):
        rollback(tmp_path)


def test_swap_to_missing_release(tmp_path):
    with pytest.raises(PublishError, match="does not exist"):
        swap_live(tmp_path, R1)


def test_run_lock_is_exclusive(tmp_path):
    with RunLock(tmp_path):
        with pytest.raises(LockBusy, match="another generation"):
            with RunLock(tmp_path):
                pass
    with RunLock(tmp_path):
        pass
```

`tests/unit/test_status.py`:

```python
from uc.status import read_status, update_status, write_status


def test_status_roundtrip(tmp_path):
    assert read_status(tmp_path) == {}
    write_status(tmp_path, {"live_release": "a"})
    assert read_status(tmp_path) == {"live_release": "a"}
    assert (tmp_path / "status.json").stat().st_mode & 0o777 == 0o644
    assert update_status(tmp_path, plugin_count=3) == {"live_release": "a", "plugin_count": 3}
    assert read_status(tmp_path)["plugin_count"] == 3


def test_corrupt_status_reads_as_empty(tmp_path):
    (tmp_path / "status.json").write_text("{not json")
    assert read_status(tmp_path) == {}
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'publish or status'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.publish'`.

- [ ] **Step 3: Implement `uc/uc/publish.py`**

```python
"""Releases on the data volume and the atomic live swap (spec §4, §6 step 10)."""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import shutil
from pathlib import Path

from .errors import UcError

RELEASE_RE = re.compile(r"^\d{8}T\d{6}Z$")


class PublishError(UcError):
    pass


class LockBusy(UcError):
    pass


class RunLock:
    """Exclusive, non-blocking lock on DATA/.lock held for a whole generation."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / ".lock"
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockBusy(f"another generation holds {self.path}") from None
        self._fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._fd is not None
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def releases_dir(data_dir: Path) -> Path:
    return data_dir / "releases"


def new_release_id(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def staging_path(data_dir: Path, release_id: str) -> Path:
    return releases_dir(data_dir) / f"{release_id}.staging"


def create_staging(data_dir: Path, release_id: str) -> Path:
    path = staging_path(data_dir, release_id)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def live_release(data_dir: Path) -> str | None:
    link = data_dir / "live"
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def live_path(data_dir: Path) -> Path | None:
    release_id = live_release(data_dir)
    return releases_dir(data_dir) / release_id if release_id else None


def list_releases(data_dir: Path) -> list[str]:
    root = releases_dir(data_dir)
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and RELEASE_RE.match(p.name)
    )


def swap_live(data_dir: Path, release_id: str) -> None:
    if not (releases_dir(data_dir) / release_id).is_dir():
        raise PublishError(f"release {release_id} does not exist")
    tmp = data_dir / "live.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(f"releases/{release_id}", tmp)
    os.replace(tmp, data_dir / "live")  # rename(2): readers see the old or the new release, never neither


def publish(data_dir: Path, staging: Path, release_id: str) -> Path:
    final = releases_dir(data_dir) / release_id
    os.rename(staging, final)
    swap_live(data_dir, release_id)
    return final


def prune(data_dir: Path, retain: int) -> list[str]:
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    keep = set(releases[-retain:]) | ({live} if live else set())
    removed = [r for r in releases if r not in keep]
    for release_id in removed:
        shutil.rmtree(releases_dir(data_dir) / release_id)
    return removed


def mark_failed(data_dir: Path, release_id: str) -> None:
    """Keep the failed staging directory for debugging, replacing any older failure."""
    root = releases_dir(data_dir)
    if not root.is_dir():
        return
    for old in root.glob(".failed-*"):
        shutil.rmtree(old, ignore_errors=True)
    staging = staging_path(data_dir, release_id)
    if staging.exists():
        os.rename(staging, root / f".failed-{release_id}")


def rollback(data_dir: Path, target: str | None = None) -> str:
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    if target is None:
        earlier = [r for r in releases if live is None or r < live]
        if not earlier:
            raise PublishError("no earlier release to roll back to")
        target = earlier[-1]
    elif target not in releases:
        raise PublishError(f"unknown release {target}; available: {', '.join(releases) or 'none'}")
    swap_live(data_dir, target)
    return target
```

- [ ] **Step 4: Implement `uc/uc/status.py`**

```python
"""status.json on the data volume, served at /status.json (spec §8.4)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATUS_FILE = "status.json"


def read_status(data_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((data_dir / STATUS_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_status(data_dir: Path, status: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / ".status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, data_dir / STATUS_FILE)


def update_status(data_dir: Path, **fields: Any) -> dict[str, Any]:
    status = read_status(data_dir)
    status.update(fields)
    write_status(data_dir, status)
    return status
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'publish or status'"`
Expected: `11 passed`.

- [ ] **Step 6: Commit**

```bash
git add uc/uc/publish.py uc/uc/status.py tests/unit/test_publish.py tests/unit/test_status.py
git commit -m "Add atomic publish, pruning, rollback and status.json

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Source ingest checks (`doctor.py`)

**Files:**
- Create: `uc/uc/doctor.py`
- Test: `tests/unit/test_doctor.py`

**Interfaces:**
- Consumes:
  - `uc.aql`: `Gav`, `Listing`, `RepoFile`, `classify`, `query_repository`
  - `uc.versions.VersionTool`
  - `Settings` (`artifactory_url`, `artifactory_url_with_port`, `artifactory_repository`, `artifactory_api_url`, `cache_dir`, `data_dir`)
  - `Credentials`
- Produces:
  - `DoctorError(UcError)`
  - `DoctorReport`, whose fields are:
    - `plugins: int`, `cores: list[str]`
    - `hpi_without_pom`, `war_without_pom`, `non_maven`, `stub_poms`, `missing_dependencies` (all `list[str]`)
    - `unchecked: int`, `tiers: dict | None`

    It provides `dropped` (a property), `exit_code()`, `summary()` and `to_dict()`.
  - `ArtifactSource(artifactory_url, repository, username, password, cache_dir, allow_network, timeout=60)` with:
    - `from_settings(settings, creds, *, allow_network)`
    - `pom(gav) -> bytes | None` and `manifest(gav) -> str | None`
    - `cache_file(url) -> Path | None`
  - `parse_manifest(text) -> dict[str, str]`
  - `parse_dependencies(value) -> list[tuple[str, str, bool]]`
  - `is_stub_pom(data: bytes) -> bool`
  - `analyze(listing, versions, source) -> DoctorReport`
  - `run_doctor(settings, creds, *, fetch: bool, versions, query=aql.query_repository) -> DoctorReport`
  - `format_report(report, settings) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_doctor.py`:

```python
import base64
import hashlib
import os
from pathlib import Path

from httpstub import StubServer
from ucsupport import make_settings

from uc.aql import Gav, RepoFile, classify
from uc.doctor import (
    ArtifactSource,
    analyze,
    format_report,
    is_stub_pom,
    parse_dependencies,
    parse_manifest,
    run_doctor,
)
from uc.model import Credentials
from uc.versions import VersionTool

CREDS = Credentials("uc", "secret", "uc", "uc", True)
GIT = Gav("org.jenkins-ci.plugins", "git", "5.2.0", "hpi")


def versions():
    return VersionTool(Path("/opt/uc/generator"), Path("/opt/uc/tools"))


def plugin(artifact, version, *extensions, group="org/jenkins-ci/plugins"):
    return [RepoFile(f"{group}/{artifact}/{version}", f"{artifact}-{version}.{ext}") for ext in extensions]


class FakeSource:
    def __init__(self, poms, manifests):
        self.poms, self.manifests = poms, manifests

    def pom(self, gav):
        return self.poms.get(gav.artifact_id)

    def manifest(self, gav):
        return self.manifests.get(gav.artifact_id)


def test_parse_manifest_joins_continuation_lines_and_stops_at_first_section():
    text = ("Manifest-Version: 1.0\r\nShort-Name: git\r\nPlugin-Dependencies: credentials:2.6.1,scm-api:600.v\r\n"
            " 1;resolution:=optional\r\n\r\nName: extra\r\nShort-Name: other\r\n")
    assert parse_manifest(text) == {
        "Manifest-Version": "1.0",
        "Short-Name": "git",
        "Plugin-Dependencies": "credentials:2.6.1,scm-api:600.v1;resolution:=optional",
    }


def test_parse_dependencies():
    assert parse_dependencies("a:1.0,b:2.0;resolution:=optional, c:3") == [
        ("a", "1.0", False), ("b", "2.0", True), ("c", "3", False),
    ]
    assert parse_dependencies("") == []


def test_is_stub_pom():
    assert not is_stub_pom(b'<project xmlns="http://maven.apache.org/POM/4.0.0"><name>Git plugin</name></project>')
    assert is_stub_pom(b"<project><artifactId>stub</artifactId></project>")
    assert is_stub_pom(b"<project><name>  </name></project>")
    assert is_stub_pom(b"not xml")
    assert is_stub_pom(b'<!DOCTYPE p [<!ENTITY x "y">]><project><name>&x;</name></project>')


def test_analyze_reports_every_defect():
    listing = classify(
        plugin("git", "5.2.0", "hpi", "pom")
        + plugin("git", "5.10.0", "hpi", "pom")
        + plugin("credentials", "2.0", "hpi", "pom")
        + plugin("orphan", "1.0", "hpi")
        + plugin("stub", "1.0", "hpi", "pom")
        + plugin("jenkins-war", "2.528.1", "war", "pom", group="org/jenkins-ci/main")
        + plugin("jenkins-war", "2.540", "war", group="org/jenkins-ci/main")
        + [RepoFile("plugins/x/1.0", "x.hpi")]
    )
    source = FakeSource(
        poms={"git": b"<project><name>Git</name></project>", "credentials": b"<project><name>C</name></project>",
              "stub": b"<project/>"},
        manifests={"git": "Plugin-Dependencies: credentials:2.6.1,scm-api:1;resolution:=optional,workflow-step-api:1.0\n",
                   "stub": "Short-Name: stub\n"},
    )
    report = analyze(listing, versions(), source)
    assert report.plugins == 3
    assert report.cores == ["2.528.1"]
    assert report.hpi_without_pom == ["org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi"]
    assert report.war_without_pom == ["org/jenkins-ci/main/jenkins-war/2.540/jenkins-war-2.540.war"]
    assert report.non_maven == ["plugins/x/1.0/x.hpi"]
    assert report.stub_poms == ["stub:1.0"]
    assert report.missing_dependencies == [
        "git:5.10.0 requires workflow-step-api 1.0 (not in the repository)",
        "git:5.10.0 requires credentials >= 2.6.1 (newest available: 2.0)",
    ]
    assert report.unchecked == 1  # credentials has no manifest
    assert report.dropped == 3
    assert report.exit_code() == 1
    assert report.summary()["missing_dependencies"] == 2


def test_artifact_source_reads_the_update_center2_cache(tmp_path):
    source = ArtifactSource("http://artifactory:8081/artifactory/", "jenkins", "uc", "secret",
                            tmp_path / "cache", allow_network=False)
    manifest_path = "/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi!/META-INF/MANIFEST.MF"
    digest = hashlib.sha256(manifest_path.encode()).hexdigest()
    cached = tmp_path / "cache" / digest[:2] / digest
    cached.parent.mkdir(parents=True)
    cached.write_text("Short-Name: git\n")
    assert source.cache_file("http://artifactory:8081" + manifest_path) == cached
    assert source.manifest(GIT) == "Short-Name: git\n"
    assert source.pom(GIT) is None  # not cached and network not allowed
    pom_digest = hashlib.sha256(b"/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom").hexdigest()
    (tmp_path / "cache" / pom_digest[:2] / pom_digest).mkdir(parents=True)  # update-center2's cached failure
    assert source.pom(GIT) is None


def test_artifact_source_fetches_with_basic_auth():
    routes = {("GET", "/artifactory/jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"): (200, {}, b"<project/>")}
    with StubServer(routes) as stub:
        source = ArtifactSource(stub.url + "/artifactory/", "jenkins", "uc", "secret", None, allow_network=True)
        assert source.pom(GIT) == b"<project/>"
        assert source.manifest(GIT) is None  # 404
    assert stub.requests[0][2]["Authorization"] == "Basic " + base64.b64encode(b"uc:secret").decode()


def test_run_doctor_includes_live_tiers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    release = settings.data_dir / "releases" / "20260922T000000Z"
    release.mkdir(parents=True)
    (release / "tiers.json").write_text('{"weeklyCores": [], "stableCores": ["2.528.1"]}')
    os.symlink("releases/20260922T000000Z", settings.data_dir / "live")
    files = plugin("git", "5.2.0", "hpi", "pom") + plugin("jenkins-war", "2.528.1", "war", "pom", group="org/jenkins-ci/main")
    report = run_doctor(settings, CREDS, fetch=False, versions=VersionTool.from_settings(settings),
                        query=lambda *args: files)
    assert report.tiers == {"weeklyCores": [], "stableCores": ["2.528.1"]}
    assert report.unchecked == 1
    assert report.exit_code() == 0


def test_format_report(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    listing = classify(plugin("orphan", "1.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
    text = format_report(analyze(listing, versions(), FakeSource({}, {})), settings)
    assert "Source: http://artifactory:8081/artifactory/jenkins" in text
    assert "Jenkins cores with a POM: none" in text
    assert "Dropped by the generator: 1" in text
    assert "org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi" in text
    assert "Not checked (POM or manifest unavailable; run with --fetch): 1" in text
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k doctor"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.doctor'`.

- [ ] **Step 3: Implement `uc/uc/doctor.py`**

```python
"""uc doctor: what the generator will and won't see in the source repository (spec §10)."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from . import aql, http
from .aql import Gav, Listing
from .config import Settings
from .errors import UcError
from .model import Credentials
from .versions import VersionTool


class DoctorError(UcError):
    pass


@dataclass
class DoctorReport:
    plugins: int = 0
    cores: list[str] = field(default_factory=list)
    hpi_without_pom: list[str] = field(default_factory=list)
    war_without_pom: list[str] = field(default_factory=list)
    non_maven: list[str] = field(default_factory=list)
    stub_poms: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    unchecked: int = 0  # newest releases whose POM or manifest was unavailable
    tiers: dict | None = None

    @property
    def dropped(self) -> int:
        return len(self.hpi_without_pom) + len(self.war_without_pom) + len(self.non_maven)

    def exit_code(self) -> int:
        return 1 if self.dropped or self.missing_dependencies else 0

    def summary(self) -> dict:
        return {
            "plugins": self.plugins, "cores": self.cores,
            "hpi_without_pom": len(self.hpi_without_pom), "war_without_pom": len(self.war_without_pom),
            "non_maven": len(self.non_maven), "stub_poms": len(self.stub_poms),
            "missing_dependencies": len(self.missing_dependencies), "unchecked": self.unchecked,
        }

    def to_dict(self) -> dict:
        return asdict(self)


class Source(Protocol):
    def pom(self, gav: Gav) -> bytes | None: ...
    def manifest(self, gav: Gav) -> str | None: ...


class ArtifactSource:
    """Reads POMs and MANIFEST.MF the way update-center2 does, from its cache first."""

    def __init__(self, artifactory_url: str, repository: str, username: str, password: str,
                 cache_dir: Path | None, allow_network: bool, timeout: float = 60) -> None:
        self.base = f"{artifactory_url}{repository}/"
        self.cache_dir = cache_dir
        self.allow_network = allow_network
        self.timeout = timeout
        self._auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    @classmethod
    def from_settings(cls, settings: Settings, creds: Credentials, *, allow_network: bool) -> "ArtifactSource":
        # Same URL the jar uses, so cache keys (sha256 of the URL path) match.
        return cls(settings.artifactory_url_with_port, settings.artifactory_repository,
                   creds.artifactory_username, creds.artifactory_password, settings.cache_dir, allow_network)

    def pom(self, gav: Gav) -> bytes | None:
        return self._get(self.base + gav.with_extension("pom").maven_path)

    def manifest(self, gav: Gav) -> str | None:
        data = self._get(self.base + gav.maven_path + "!/META-INF/MANIFEST.MF")
        return data.decode("utf-8", "replace") if data is not None else None

    def cache_file(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(urlsplit(url).path.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _get(self, url: str) -> bytes | None:
        cached = self.cache_file(url)
        if cached is not None:
            if cached.is_file():
                return cached.read_bytes()
            if cached.is_dir():  # update-center2 records failed downloads as directories
                return None
        if not self.allow_network:
            return None
        request = urllib.request.Request(url, headers={"Authorization": self._auth, "User-Agent": http.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise DoctorError(f"GET {url} failed with HTTP {exc.code}") from exc
        except OSError as exc:
            raise DoctorError(f"GET {url} failed: {exc}") from exc


def parse_manifest(text: str) -> dict[str, str]:
    """Main section of a JAR manifest, joining 72-column continuation lines."""
    attributes: dict[str, str] = {}
    key: str | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw:
            if attributes:
                break
            continue
        if raw.startswith(" ") and key:
            attributes[key] += raw[1:]
            continue
        name, sep, value = raw.partition(": ")
        key = name if sep else None
        if sep:
            attributes[name] = value
    return attributes


def parse_dependencies(value: str) -> list[tuple[str, str, bool]]:
    dependencies = []
    for item in filter(None, (part.strip() for part in value.split(","))):
        spec, _, params = item.partition(";")
        name, _, version = spec.partition(":")
        dependencies.append((name, version, "resolution:=optional" in params))
    return dependencies


def is_stub_pom(data: bytes) -> bool:
    # Real Maven POMs never declare a DOCTYPE or entities; refusing them avoids entity-expansion
    # tricks without a third-party parser (ElementTree never resolves external entities).
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return True
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return True
    for child in root:
        if child.tag.rsplit("}", 1)[-1] == "name" and (child.text or "").strip():
            return False
    return True


def analyze(listing: Listing, versions: VersionTool, source: Source) -> DoctorReport:
    report = DoctorReport()
    report.non_maven = sorted(f"{f.path}/{f.name}" for f in listing.rejected)
    report.hpi_without_pom = sorted(g.maven_path for g in listing.plugins_without_pom)
    report.war_without_pom = sorted(g.maven_path for g in listing.wars_without_pom)
    report.cores = versions.sort({g.version for g in listing.wars})
    newest = versions.max_by_key((g.artifact_id, g.version) for g in listing.plugins)
    report.plugins = len(newest)
    latest: dict[str, Gav] = {}
    for gav in listing.plugins:
        if newest.get(gav.artifact_id) == gav.version and (gav.artifact_id not in latest or gav.extension == "hpi"):
            latest[gav.artifact_id] = gav

    checks: list[tuple[str, str, str, str, str]] = []  # plugin, version, dependency, required, available
    for artifact_id, gav in sorted(latest.items()):
        pom, manifest = source.pom(gav), source.manifest(gav)
        if pom is None or manifest is None:
            report.unchecked += 1
        if pom is not None and is_stub_pom(pom):
            report.stub_poms.append(f"{artifact_id}:{gav.version}")
        if manifest is None:
            continue
        for name, required, optional in parse_dependencies(parse_manifest(manifest).get("Plugin-Dependencies", "")):
            if optional:
                continue
            if name not in latest:
                report.missing_dependencies.append(
                    f"{artifact_id}:{gav.version} requires {name} {required} (not in the repository)"
                )
            else:
                checks.append((artifact_id, gav.version, name, required, latest[name].version))
    results = versions.compare_many([(available, required) for *_, required, available in checks])
    for (artifact_id, version, name, required, available), cmp in zip(checks, results):
        if cmp < 0:
            report.missing_dependencies.append(
                f"{artifact_id}:{version} requires {name} >= {required} (newest available: {available})"
            )
    return report


def run_doctor(
    settings: Settings, creds: Credentials, *, fetch: bool, versions: VersionTool,
    query: Callable[..., list[aql.RepoFile]] = aql.query_repository,
) -> DoctorReport:
    files = query(settings.artifactory_api_url, settings.artifactory_repository,
                  creds.artifactory_username, creds.artifactory_password)
    report = analyze(aql.classify(files), versions,
                     ArtifactSource.from_settings(settings, creds, allow_network=fetch))
    tiers_file = settings.data_dir / "live" / "tiers.json"
    if tiers_file.is_file():
        try:
            report.tiers = json.loads(tiers_file.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return report


def format_report(report: DoctorReport, settings: Settings) -> str:
    lines = [
        f"Source: {settings.artifactory_url}{settings.artifactory_repository}",
        f"Plugins with a POM: {report.plugins}",
        "Jenkins cores with a POM: "
        + (", ".join(report.cores) or "none (the site will offer no core and no tiers)"),
    ]
    if report.tiers is not None:
        lines.append(
            f"Tiers in the live release: weekly {report.tiers.get('weeklyCores', [])}, "
            f"stable {report.tiers.get('stableCores', [])}"
        )

    def section(title: str, items: list[str], indent: str = "") -> None:
        if items:
            lines.append(f"{indent}{title} ({len(items)}):")
            lines.extend(f"{indent}  {item}" for item in items)

    lines.append("")
    if report.dropped:
        lines.append(f"Dropped by the generator: {report.dropped}")
        section(".hpi/.jpi without a .pom", report.hpi_without_pom, "  ")
        section("jenkins-war without a .pom", report.war_without_pom, "  ")
        section("not in Maven layout", report.non_maven, "  ")
    else:
        lines.append("Nothing in the source is dropped by the generator.")
    section("Stub POMs (degraded names, descriptions, SCM links)", report.stub_poms)
    section("Dependency problems", report.missing_dependencies)
    if report.unchecked:
        lines.append(f"Not checked (POM or manifest unavailable; run with --fetch): {report.unchecked}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k doctor"`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/doctor.py tests/unit/test_doctor.py
git commit -m "Add uc doctor ingest checks against the generator's own rules

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: The generation pipeline and CLI (`generate.py`, `cli.py`, `__main__.py`)

**Files:**
- Create: `uc/uc/generate.py`, `uc/uc/cli.py`, `uc/uc/__main__.py`, `tests/support/fakejar.py`
- Test: `tests/unit/test_generate.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2-12
- Produces:
  - `uc.generate.Toolchain(run_jar=jar.run_jar, aql_query=aql.query_repository, http_get=http.get, versions=None)`
  - `uc.generate.generate(settings, *, force=False, tools=None, now=None, base_env=None) -> str`, which returns the release id
  - `uc.cli.main(argv: list[str] | None = None) -> int` and `uc.cli.EXIT_LOCKED = 75`
  - `tests/support/fakejar.FakeJar(tiers, cert_b64, *, url_root=None, fail_sites=False, **doc_options)`, a callable jar runner that records `.calls` and `.envs`
  - `tests/support/fakejar.SimpleVersions`, which offers the same three methods as `VersionTool` without a JVM

- [ ] **Step 1: Add the fake jar**

`tests/support/fakejar.py`:

```python
"""Stand-ins for update-center2 and VersionTool in generate() tests."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from sitefixture import uc_document, write_site

from uc.jar import JarError
from uc.model import Tiers


class SimpleVersions:
    """Dotted-number comparisons without starting a JVM; the real VersionTool is covered by test_versions."""

    @staticmethod
    def _key(version: str) -> tuple:
        return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.-]", version))

    def sort(self, versions: Iterable[str]) -> list[str]:
        return sorted(versions, key=self._key)

    def max_by_key(self, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        best: dict[str, str] = {}
        for key, version in items:
            if key not in best or self._key(version) > self._key(best[key]):
                best[key] = version
        return best

    def compare_many(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        return [(self._key(a) > self._key(b)) - (self._key(a) < self._key(b)) for a, b in pairs]


class FakeJar:
    def __init__(self, tiers: Tiers, cert_b64: str, *, url_root: str | None = None,
                 fail_sites: bool = False, **doc_options) -> None:
        self.tiers = tiers
        self.cert_b64 = cert_b64
        self.url_root = url_root
        self.fail_sites = fail_sites
        self.doc_options = doc_options
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(self, cmd: list[str], env: Mapping[str, str], log: Path) -> None:
        self.calls.append(list(cmd))
        self.envs.append(dict(env))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as out:
            out.write("fake update-center2\n")
        if "--dynamic-tier-list-file" in cmd:
            target = Path(cmd[cmd.index("--dynamic-tier-list-file") + 1])
            target.write_text(json.dumps({"weeklyCores": list(self.tiers.weekly),
                                          "stableCores": list(self.tiers.stable)}))
            return
        if self.fail_sites:
            raise JarError("update-center2 exited with status 1; see log")
        root = self.url_root or env["DOWNLOADS_ROOT_URL"][: -len("/download")]
        arguments = Path(cmd[cmd.index("--arguments-file") + 1])
        for line in arguments.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            tokens = line.split(" ")
            www = Path(tokens[tokens.index("--www-dir") + 1])
            doc = uc_document(root, cert_b64=self.cert_b64, **self.doc_options)
            write_site(www, doc, current=www.name == "current")
            if "--download-links-directory" in tokens:
                links = Path(tokens[tokens.index("--download-links-directory") + 1])
                for plugin_id in doc["plugins"]:
                    (links / "plugins" / plugin_id).mkdir(parents=True, exist_ok=True)
                    (links / "plugins" / plugin_id / "index.html").write_text(plugin_id)
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_generate.py`:

```python
import datetime as dt
import os

import pytest
from fakejar import FakeJar, SimpleVersions
from sitefixture import DEFAULT_PLUGINS
from ucsupport import make_settings

from uc.aql import RepoFile
from uc.generate import Toolchain, generate
from uc.htaccess import absolute_targets
from uc.jar import JarError
from uc.model import Tiers
from uc.pki import cert_der_b64
from uc.preflight import PreflightError
from uc.publish import LockBusy, RunLock, list_releases, live_release
from uc.status import read_status
from uc.validate import ValidationError

T1 = dt.datetime(2026, 9, 22, 3, 0, tzinfo=dt.timezone.utc)
T2 = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.timezone.utc)
TIERS = Tiers(("2.540",), ("2.528.1",))


def repo_files():
    files = []
    for plugin_id, version in DEFAULT_PLUGINS.items():
        for ext in ("hpi", "pom"):
            files.append(RepoFile(f"org/jenkins-ci/plugins/{plugin_id}/{version}", f"{plugin_id}-{version}.{ext}"))
    for ext in ("war", "pom"):
        files.append(RepoFile("org/jenkins-ci/main/jenkins-war/2.528.1", f"jenkins-war-2.528.1.{ext}"))
    return files


def no_network(url, headers=None, timeout=60):
    raise AssertionError(f"unexpected network access: {url}")


def no_query(*args):
    raise AssertionError("AQL must not be queried with the mirror backend")


def tools_for(settings, **jar_options):
    return Toolchain(
        run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file), **jar_options),
        aql_query=lambda *args: repo_files(),
        http_get=no_network,
        versions=SimpleVersions(),
    )


def test_airgapped_generation_publishes(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    rid = generate(settings, tools=tools_for(settings), now=T1, base_env={"UC2_VERSION": "3.18.4+popularity-patch"})
    data = settings.data_dir
    live = data / "live"
    assert rid == "20260922T030000Z"
    assert live_release(data) == rid
    assert (live / "current" / "update-center.json").is_file()
    assert os.readlink(live / "stable") == "dynamic-stable-2.528.1"
    assert absolute_targets((live / ".htaccess").read_text()) == []
    mapping = (live / ".htdownload-map").read_text()
    assert "plugins/git/5.2.0 org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi\n" in mapping
    assert "war/2.528.1 org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war\n" in mapping
    status = read_status(data)
    assert status["live_release"] == rid
    assert status["last_run"]["result"] == "success"
    assert status["update_center2"] == "3.18.4+popularity-patch"
    assert status["resources"]["source"] == "bundled"
    assert status["tiers"] == {"weekly": ["2.540"], "stable": ["2.528.1"]}
    assert status["plugin_count"] == 2
    assert status["doctor"]["plugins"] == 2
    assert any("GitHub credentials not set" in w for w in status["warnings"])
    assert (data / "resources" / "last-good" / "warnings.json").is_file()
    assert not list((data / "releases").glob("*.staging"))


def test_jar_receives_download_root_and_credentials(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    tools = tools_for(settings)
    generate(settings, tools=tools, now=T1, base_env={})
    env = tools.run_jar.envs[-1]
    assert env["DOWNLOADS_ROOT_URL"] == "https://uc.example.com/download"
    assert env["ARTIFACTORY_PASSWORD"] == "secret"
    assert env["POPULARITY_JSON_URL"] == ""
    assert "--arguments-file" in tools.run_jar.calls[-1]


def test_failed_validation_keeps_the_live_release(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    first = generate(settings, tools=tools_for(settings), now=T1, base_env={})
    with pytest.raises(ValidationError, match="download URLs outside"):
        generate(settings, tools=tools_for(settings, url_root="https://updates.jenkins.io"), now=T2, base_env={})
    data = settings.data_dir
    assert live_release(data) == first
    status = read_status(data)
    assert status["live_release"] == first
    assert status["last_run"]["result"] == "failed"
    assert "download URLs outside" in status["last_run"]["error"]
    assert (data / "releases" / ".failed-20260922T090000Z").is_dir()


def test_jar_failure_is_recorded(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(JarError):
        generate(settings, tools=tools_for(settings, fail_sites=True), now=T1, base_env={})
    assert read_status(settings.data_dir)["last_run"]["result"] == "failed"
    assert live_release(settings.data_dir) is None


def test_preflight_failure_creates_no_staging(tmp_path, secrets_dir):
    (secrets_dir / "update-center.key").unlink()
    settings = make_settings(tmp_path, secrets_dir)
    with pytest.raises(PreflightError):
        generate(settings, tools=tools_for(settings), now=T1, base_env={})
    releases = settings.data_dir / "releases"
    assert not releases.exists() or list(releases.iterdir()) == []


def test_plugin_count_drop_needs_force(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    generate(settings, tools=tools_for(settings), now=T1, base_env={})
    shrunk = {"plugins": {"git": "5.2.0"}}
    with pytest.raises(ValidationError, match="plugin count 1 < 0.9"):
        generate(settings, tools=tools_for(settings, **shrunk), now=T2, base_env={})
    rid = generate(settings, force=True, tools=tools_for(settings, **shrunk),
                   now=T2 + dt.timedelta(minutes=1), base_env={})
    assert live_release(settings.data_dir) == rid


def test_online_generation_uses_mirror_and_tool_installers(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, MODE="online", ARTIFACTORY_URL="", ARTIFACTORY_REPOSITORY="",
                             RESOURCES_SOURCE="bundled", TOOL_INSTALLERS_URL="https://updates.example/updates/")
    files = {
        "https://updates.example/updates/": b'<a href="hudson.tasks.Maven.MavenInstaller.json">m</a>',
        "https://updates.example/updates/hudson.tasks.Maven.MavenInstaller.json": b"downloadService.post('m',{})",
    }
    tools = Toolchain(run_jar=FakeJar(TIERS, cert_der_b64(settings.signing_cert_file)), aql_query=no_query,
                      http_get=lambda url, headers=None, timeout=60: files[url])
    generate(settings, tools=tools, now=T1, base_env={})
    live = settings.data_dir / "live"
    assert (live / ".htdownload-map").read_text() == ""
    assert (live / "current" / "updates" / "hudson.tasks.Maven.MavenInstaller.json").is_file()
    status = read_status(settings.data_dir)
    assert status["download_backend"] == "mirror"
    assert status["doctor"] is None
    assert tools.run_jar.envs[-1]["POPULARITY_JSON_URL"].startswith("https://raw.githubusercontent.com/")
    assert tools.run_jar.envs[-1]["ARTIFACTORY_URL"] == "https://repo.jenkins-ci.org:443/"


def test_lock_prevents_concurrent_runs(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    settings.data_dir.mkdir(parents=True)
    with RunLock(settings.data_dir):
        with pytest.raises(LockBusy):
            generate(settings, tools=tools_for(settings), now=T1, base_env={})


def test_old_releases_are_pruned(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, RETAIN_RELEASES="2")
    for hour in range(4):
        generate(settings, tools=tools_for(settings), now=T1 + dt.timedelta(hours=hour), base_env={})
    assert list_releases(settings.data_dir) == ["20260922T050000Z", "20260922T060000Z"]
```

`tests/unit/test_cli.py`:

```python
import json
import os

from sitefixture import uc_document, write_site

from uc.cli import EXIT_LOCKED, main
from uc.publish import RunLock
from uc.status import read_status, write_status


def use_env(monkeypatch, tmp_path, secrets_dir):
    values = {
        "MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com",
        "ARTIFACTORY_URL": "http://artifactory:8081/artifactory/", "ARTIFACTORY_REPOSITORY": "jenkins",
        "SECRETS_DIR": str(secrets_dir), "UC_DATA_DIR": str(tmp_path / "data"),
        "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path / "data"


def test_status_prints_json(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    write_status(data, {"live_release": "x"})
    assert main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"live_release": "x"}


def test_configuration_errors_exit_1(monkeypatch, capsys):
    monkeypatch.setenv("MODE", "bogus")
    assert main(["status"]) == 1
    assert "uc: error: MODE must be one of" in capsys.readouterr().err


def test_rollback_updates_status(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ("20260922T010000Z", "20260922T020000Z"):
        write_site(data / "releases" / rid / "current", uc_document("https://uc.example.com"))
    os.symlink("releases/20260922T020000Z", data / "live")
    assert main(["rollback"]) == 0
    assert "20260922T010000Z" in capsys.readouterr().out
    assert read_status(data)["live_release"] == "20260922T010000Z"
    assert read_status(data)["plugin_count"] == 2


def test_generate_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    data.mkdir(parents=True)
    with RunLock(data):
        assert main(["generate"]) == EXIT_LOCKED


def test_pki_init_and_show(tmp_path, capsys):
    out = tmp_path / "pki"
    assert main(["pki", "init", "--out", str(out), "--root-days", "30", "--signing-days", "10"]) == 0
    assert "root-ca.crt" in capsys.readouterr().out
    assert main(["pki", "show", "--dir", str(out)]) == 0
    shown = capsys.readouterr().out
    assert "update-center.cert: CN=Jenkins Update Center Signing" in shown
    assert "root-ca.crt: CN=Jenkins Update Center Root CA" in shown
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k 'generate or cli'"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.generate'`.

- [ ] **Step 4: Implement `uc/uc/generate.py`**

```python
"""uc generate: build, validate and publish one release (spec §6)."""
from __future__ import annotations

import datetime as dt
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from . import aql, args, doctor, downloadmap, htaccess, http, jar, layout, pki, preflight, publish, resources, status, validate
from .config import Settings
from .timeutil import iso, utcnow
from .versions import VersionTool

AqlQuery = Callable[[str, str, str, str], list[aql.RepoFile]]


@dataclass
class Toolchain:
    """External effects, replaceable in tests."""

    run_jar: jar.JarRunner = jar.run_jar
    aql_query: AqlQuery = aql.query_repository
    http_get: http.Getter = http.get
    versions: VersionTool | None = None


def generate(
    settings: Settings,
    *,
    force: bool = False,
    tools: Toolchain | None = None,
    now: dt.datetime | None = None,
    base_env: Mapping[str, str] | None = None,
) -> str:
    tools = tools or Toolchain()
    now = now or utcnow()
    base_env = os.environ if base_env is None else base_env
    data = settings.data_dir
    release_id = publish.new_release_id(now)
    data.mkdir(parents=True, exist_ok=True)
    with publish.RunLock(data):
        warnings: list[str] = []
        work = data / "work" / release_id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            _build_and_publish(settings, release_id, now, work, force, tools, base_env, warnings)
        except Exception as exc:
            publish.mark_failed(data, release_id)
            status.update_status(
                data,
                mode=settings.mode,
                last_run={"id": release_id, "result": "failed", "error": str(exc), "finished_at": iso(utcnow())},
                warnings=warnings,
            )
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return release_id


def _build_and_publish(
    settings: Settings, release_id: str, now: dt.datetime, work: Path, force: bool,
    tools: Toolchain, base_env: Mapping[str, str], warnings: list[str],
) -> None:
    data = settings.data_dir
    warnings += preflight.check_public_url(settings.public_url, settings.allow_local_public_url)
    cert = preflight.check_signing(settings, now)
    creds = preflight.load_credentials(settings)
    if creds.github_placeholder:
        warnings.append("GitHub credentials not set: plugin labels from GitHub topics are omitted")
    staging = publish.create_staging(data, release_id)

    prepared = resources.prepare_resources(settings, work, now, get=tools.http_get)
    warnings += prepared.warnings
    env = jar.jar_environment(settings, creds, base_env)
    log = work / "update-center2.log"
    tiers = jar.generate_tiers(settings, env, prepared.path, work / "tiers.json", log, run=tools.run_jar)

    (staging / ".htaccess").write_text(
        htaccess.render_htaccess(tiers, settings.htaccess_script, settings.templates_dir / "htaccess-static"),
        encoding="utf-8",
    )
    arguments = work / "args.lst"
    args.write_arguments_file(args.build_argument_lines(settings, staging, tiers), arguments)
    jar.generate_sites(settings, env, prepared.path, arguments, log, run=tools.run_jar)

    layout.finalize_layout(staging, tiers, settings.templates_dir)
    shutil.copyfile(work / "tiers.json", staging / "tiers.json")
    live = publish.live_path(data)
    if settings.tool_installers_url:
        warnings += layout.mirror_tool_installers(
            settings.tool_installers_url, staging / "updates",
            previous=live / "updates" if live else None, get=tools.http_get,
        )

    listing = None
    mapping: dict[str, str] | None = None
    if settings.download_backend == "artifactory":
        listing = aql.classify(tools.aql_query(
            settings.artifactory_api_url, settings.artifactory_repository,
            creds.artifactory_username, creds.artifactory_password,
        ))
        current = validate.load_site_json(staging / "current" / "update-center.actual.json")
        mapping, map_warnings = downloadmap.build_download_map(listing, downloadmap.preferred_groups(current))
        warnings += map_warnings
    downloadmap.write_download_map(mapping or {}, staging / ".htdownload-map")

    result = validate.validate_release(
        staging,
        tiers=tiers,
        downloads_root=settings.downloads_root_url,
        signing_cert_b64=pki.cert_der_b64(settings.signing_cert_file),
        previous_plugin_count=validate.count_plugins(live / "current") if live else None,
        min_plugins=settings.min_plugins,
        min_ratio=settings.min_plugin_ratio,
        force=force,
        download_map=mapping,
    )

    doctor_summary = None
    if listing is not None:
        versions = tools.versions or VersionTool.from_settings(settings)
        report = doctor.analyze(listing, versions, doctor.ArtifactSource.from_settings(settings, creds, allow_network=False))
        doctor_summary = report.summary()
        if report.dropped:
            warnings.append(
                f"{report.dropped} artifact(s) in the source are dropped by the generator; run 'uc doctor'"
            )

    publish.publish(data, staging, release_id)
    resources.save_last_good(prepared, data)
    publish.prune(data, settings.retain_releases)
    finished = utcnow()
    age = prepared.info.age_days(finished)
    status.write_status(data, {
        "mode": settings.mode,
        "public_url": settings.public_url,
        "download_backend": settings.download_backend,
        "live_release": release_id,
        "last_run": {"id": release_id, "result": "success", "error": None, "finished_at": iso(finished)},
        "generated_at": iso(now),
        "update_center2": base_env.get("UC2_VERSION", "unknown"),
        "resources": {
            "source": prepared.info.source, "ref": prepared.info.ref,
            "data_date": prepared.info.data_date, "fetched_at": prepared.info.fetched_at,
            "age_days": age, "stale": age > settings.stale_after_days, "overlay": prepared.overlay,
        },
        "signing_cert": {
            "subject": cert.subject, "not_after": iso(cert.not_after), "days_left": cert.days_left(finished),
        },
        "tiers": {"weekly": list(tiers.weekly), "stable": list(tiers.stable)},
        "plugin_count": result.plugin_count,
        "warnings": warnings,
        "doctor": doctor_summary,
    })
```

- [ ] **Step 5: Implement `uc/uc/cli.py` and `uc/uc/__main__.py`**

`uc/uc/cli.py`:

```python
"""Command-line entry point: uc generate | doctor | rollback | status | schedule | pki (spec §5)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import publish, status
from .config import load_settings
from .errors import UcError
from .pki import DEFAULT_ORG, ROOT_CERT, ROOT_KEY, SIGNING_CERT, SIGNING_KEY, cert_info, init_pki, rotate_signing
from .timeutil import iso, utcnow

EXIT_LOCKED = 75


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uc", description="Self-hosted Jenkins update center")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="build, validate and publish a new release")
    generate.add_argument("--force", action="store_true", help="skip the plugin-count ratio check once")

    doctor = sub.add_parser("doctor", help="report what the generator will see in the source repository")
    doctor.add_argument("--fetch", action="store_true", help="download POMs and manifests missing from the cache")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")

    rollback = sub.add_parser("rollback", help="point live at an earlier release")
    rollback.add_argument("release", nargs="?", help="release id (default: the one before live)")

    sub.add_parser("status", help="print status.json")
    sub.add_parser("schedule", help="run generate on GENERATE_SCHEDULE (the scheduler service)")

    pki = sub.add_parser("pki", help="manage the signing PKI").add_subparsers(dest="pki_command", required=True)
    init = pki.add_parser("init", help="create a root CA and a signing certificate")
    init.add_argument("--out", type=Path, required=True)
    init.add_argument("--root-days", type=int, default=3650)
    init.add_argument("--signing-days", type=int, default=365)
    init.add_argument("--org", default=DEFAULT_ORG)
    rotate = pki.add_parser("rotate", help="issue a new signing certificate from the root in --dir/root/")
    rotate.add_argument("--dir", type=Path, required=True)
    rotate.add_argument("--days", type=int, default=365)
    rotate.add_argument("--org", default=DEFAULT_ORG)
    show = pki.add_parser("show", help="print subjects, expiry and fingerprints")
    show.add_argument("--dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except publish.LockBusy as exc:
        print(f"uc: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except UcError as exc:
        print(f"uc: error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "pki":
        return _pki(args)
    settings = load_settings()
    if args.command == "generate":
        from .generate import generate

        print(f"published release {generate(settings, force=args.force)}")
        return 0
    if args.command == "doctor":
        from .doctor import format_report, run_doctor
        from .preflight import load_credentials
        from .versions import VersionTool

        report = run_doctor(settings, load_credentials(settings), fetch=args.fetch,
                            versions=VersionTool.from_settings(settings))
        print(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report, settings))
        return report.exit_code()
    if args.command == "rollback":
        from .validate import count_plugins

        target = publish.rollback(settings.data_dir, args.release)
        live = publish.live_path(settings.data_dir)
        status.update_status(settings.data_dir, live_release=target,
                             plugin_count=count_plugins(live / "current") if live else None)
        print(f"live release is now {target}")
        return 0
    if args.command == "status":
        print(json.dumps(status.read_status(settings.data_dir), indent=2, sort_keys=True))
        return 0
    if args.command == "schedule":
        from .schedule import run_schedule

        run_schedule(settings)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def _pki(args: argparse.Namespace) -> int:
    if args.pki_command == "init":
        init_pki(args.out, root_days=args.root_days, signing_days=args.signing_days, org=args.org)
        print(f"created {args.out / ROOT_CERT}: install it in $JENKINS_HOME/update-center-rootCAs/ on every controller")
        print(f"created {args.out / SIGNING_KEY} and {args.out / SIGNING_CERT}: provide them as secrets")
        print(f"move {args.out / 'root' / ROOT_KEY} to offline storage; only 'uc pki rotate' needs it")
    elif args.pki_command == "rotate":
        rotate_signing(args.dir / "root" / ROOT_KEY, args.dir / ROOT_CERT, args.dir, days=args.days, org=args.org)
        print(f"issued a new {args.dir / SIGNING_CERT}; the previous pair is kept as *.old")
    else:
        now = utcnow()
        for name in (ROOT_CERT, SIGNING_CERT):
            path = args.dir / name
            if path.is_file():
                info = cert_info(path)
                print(f"{name}: {info.subject}")
                print(f"  expires {iso(info.not_after)} ({info.days_left(now)} days)")
                print(f"  sha256 {info.fingerprint_sha256}")
    return 0
```

`uc/uc/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k 'generate or cli'"`
Expected: `14 passed`.

- [ ] **Step 7: Run the whole unit suite**

Run: `make test-unit`
Expected: `162 passed`.

- [ ] **Step 8: Commit**

```bash
git add uc/uc/generate.py uc/uc/cli.py uc/uc/__main__.py tests/support/fakejar.py tests/unit/test_generate.py tests/unit/test_cli.py
git commit -m "Add the generation pipeline and the uc CLI

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Scheduler (`schedule.py`)

**Files:**
- Create: `uc/uc/schedule.py`
- Test: `tests/unit/test_schedule.py`

**Interfaces:**
- Consumes: `Settings.generate_schedule`, `Settings.data_dir`, `uc.publish.live_release`, `uc.generate.generate`
- Produces:
  - `ScheduleError(UcError)`
  - `CRONTAB = Path("/tmp/uc-crontab")`
  - `crontab_line(schedule: str) -> str`
  - `run_schedule(settings, *, generate_once=None, exec_=os.execvp, crontab=CRONTAB) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_schedule.py`:

```python
import os

import pytest
from ucsupport import make_settings

from uc.schedule import ScheduleError, crontab_line, run_schedule


def test_crontab_line():
    assert crontab_line("0 */6 * * *") == "0 */6 * * * uc generate\n"
    assert crontab_line("@hourly") == "@hourly uc generate\n"


def test_crontab_line_rejects_nonsense():
    with pytest.raises(ScheduleError, match="5-field cron expression"):
        crontab_line("every six hours")


def run(settings, tmp_path, generate_once):
    calls = []
    run_schedule(settings, generate_once=lambda: (calls.append("generate"), generate_once()),
                 exec_=lambda file, argv: calls.append(argv), crontab=tmp_path / "crontab")
    return calls


def test_first_run_generates_then_hands_over_to_supercronic(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = run(settings, tmp_path, lambda: None)
    assert calls == ["generate", ["supercronic", "-passthrough-logs", str(tmp_path / "crontab")]]
    assert (tmp_path / "crontab").read_text() == "0 */6 * * * uc generate\n"


def test_existing_release_skips_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    (settings.data_dir / "releases" / "20260922T000000Z").mkdir(parents=True)
    os.symlink("releases/20260922T000000Z", settings.data_dir / "live")
    assert run(settings, tmp_path, lambda: None) == [["supercronic", "-passthrough-logs", str(tmp_path / "crontab")]]


def test_failed_first_run_still_schedules(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)

    def boom():
        raise RuntimeError("generator crashed")

    calls = run(settings, tmp_path, boom)
    assert calls[-1][0] == "supercronic"
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k schedule"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.schedule'`.

- [ ] **Step 3: Implement `uc/uc/schedule.py`**

```python
"""The scheduler service: a first release straight away, then supercronic on GENERATE_SCHEDULE (spec §5)."""
from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Callable

from . import publish
from .config import Settings
from .errors import UcError

CRONTAB = Path("/tmp/uc-crontab")


class ScheduleError(UcError):
    pass


def crontab_line(schedule: str) -> str:
    fields = schedule.split()
    if not (len(fields) == 5 or (len(fields) == 1 and fields[0].startswith("@"))):
        raise ScheduleError(f"GENERATE_SCHEDULE must be a 5-field cron expression or an @macro (got {schedule!r})")
    return f"{' '.join(fields)} uc generate\n"


def run_schedule(
    settings: Settings,
    *,
    generate_once: Callable[[], object] | None = None,
    exec_: Callable[[str, list[str]], object] = os.execvp,
    crontab: Path = CRONTAB,
) -> None:
    line = crontab_line(settings.generate_schedule)
    if publish.live_release(settings.data_dir) is None:
        print("uc schedule: no release is live yet, generating now", flush=True)
        try:
            if generate_once is None:
                from .generate import generate

                generate(settings)
            else:
                generate_once()
        except Exception:  # keep scheduling; status.json records the failure
            traceback.print_exc()
    crontab.write_text(line, encoding="utf-8")
    exec_("supercronic", ["supercronic", "-passthrough-logs", str(crontab)])
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k schedule"`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/schedule.py tests/unit/test_schedule.py
git commit -m "Add scheduler: first release immediately, then supercronic

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---
### Task 15: The `web` image

**Files:**
- Create: `web/Dockerfile`, `web/conf/httpd.conf`, `web/conf/uc-site.conf`, `web/conf/uc-tls.conf`, `web/entrypoint.sh`, `web/healthcheck.sh`, `tests/web/entrypoint-checks.sh`
- Modify: `Makefile` (add `build-web`, `test-web`; `build` depends on both images)

**Interfaces:**
- Consumes: the data-volume layout from Tasks 9-11: `/data/live` (symlink), `/data/live/.htdownload-map`, `/data/status.json`
- Produces:
  - a web image listening on 8080, plus 8443 when TLS is configured
  - environment contract:
    - `MODE` and `PUBLIC_URL` (required)
    - `DOWNLOAD_BACKEND`, `DOWNLOAD_BACKEND_URL`, `DOWNLOAD_ACCESS`
    - `ARTIFACTORY_URL` and `ARTIFACTORY_REPOSITORY` (for the default backend URL)
    - `DOWNLOAD_PROXY_AUTH_FILE` (default `/run/secrets/download_proxy_auth`, content `username:password`)
    - `DOWNLOAD_PROXY_CA_FILE`
    - `TLS_CERT_FILE` and `TLS_KEY_FILE`
  - entrypoint exit code 64 for configuration errors
  - `HEALTHCHECK` via `/usr/local/bin/healthcheck.sh`

The defaults in `entrypoint.sh` must match `uc/uc/config.py`:
- online mode uses the `mirror` backend with `https://get.jenkins.io`;
- air-gapped mode uses the `artifactory` backend with `${ARTIFACTORY_URL%/}/${ARTIFACTORY_REPOSITORY}`.

- [ ] **Step 1: Write the failing entrypoint checks**

`tests/web/entrypoint-checks.sh`:

```bash
#!/usr/bin/env bash
# Entrypoint checks for the web image. Usage: tests/web/entrypoint-checks.sh IMAGE
set -uo pipefail
image="$1"
failures=0

expect_exit() {  # expect_exit CODE MESSAGE [docker run args...]
  local code="$1" message="$2"
  shift 2
  local output rc
  output="$(docker run --rm "$@" "${image}" 2>&1)"
  rc=$?
  if [[ ${rc} -ne ${code} || "${output}" != *"${message}"* ]]; then
    echo "FAIL: expected exit ${code} with '${message}', got ${rc}: ${output}"
    failures=$((failures + 1))
  else
    echo "ok: ${message}"
  fi
}

expect_exit 64 "PUBLIC_URL must be set" -e MODE=online
expect_exit 64 "MODE must be online or airgapped" -e MODE=bogus -e PUBLIC_URL=http://uc
expect_exit 64 "set DOWNLOAD_BACKEND_URL, or ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY" -e MODE=airgapped -e PUBLIC_URL=http://uc
expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
expect_exit 64 "needs username:password" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e MODE=online -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope

name="uc-web-wait-$$"
docker run -d --name "${name}" -e MODE=online -e PUBLIC_URL=http://uc "${image}" >/dev/null
sleep 3
if docker logs "${name}" 2>&1 | grep -q "waiting for the first published release"; then
  echo "ok: waits for the first release"
else
  echo "FAIL: web did not report that it is waiting for the first release"
  failures=$((failures + 1))
fi
docker rm -f "${name}" >/dev/null
exit "${failures}"
```

Add to the `Makefile`: make `build` depend on both images, extend `.PHONY`, and add the targets:

```make
.PHONY: build build-uc build-web test-unit test-web

build: build-uc build-web

build-web:
	docker build -t $(WEB_IMAGE) web

test-web: build-web
	bash tests/web/entrypoint-checks.sh $(WEB_IMAGE)
```

- [ ] **Step 2: Run the checks and watch them fail**

Run: `make test-web`
Expected: FAIL. `docker build` errors because `web/Dockerfile` does not exist.

- [ ] **Step 3: Write the Apache configuration**

`web/conf/httpd.conf`:

```apache
# Jenkins update center web tier (spec §8). Variables come from web/entrypoint.sh.
ServerRoot "/usr/local/apache2"
Listen 8080
PidFile /tmp/httpd.pid
DefaultRuntimeDir /tmp

LoadModule mpm_event_module modules/mod_mpm_event.so
LoadModule unixd_module modules/mod_unixd.so
LoadModule authz_core_module modules/mod_authz_core.so
LoadModule authz_host_module modules/mod_authz_host.so
LoadModule dir_module modules/mod_dir.so
LoadModule mime_module modules/mod_mime.so
LoadModule log_config_module modules/mod_log_config.so
LoadModule rewrite_module modules/mod_rewrite.so
LoadModule alias_module modules/mod_alias.so
LoadModule headers_module modules/mod_headers.so
LoadModule filter_module modules/mod_filter.so
LoadModule deflate_module modules/mod_deflate.so
LoadModule autoindex_module modules/mod_autoindex.so
<IfDefine ACCESS_PROXY>
    LoadModule proxy_module modules/mod_proxy.so
    LoadModule proxy_http_module modules/mod_proxy_http.so
</IfDefine>
<IfDefine NEED_SSL>
    LoadModule socache_shmcb_module modules/mod_socache_shmcb.so
    LoadModule ssl_module modules/mod_ssl.so
</IfDefine>

# Redirects from upstream's .htaccess carry the public scheme, host and port.
ServerName ${PUBLIC_URL}
UseCanonicalName On
ServerTokens Prod
ServerSignature Off
TraceEnable off

ErrorLog /proc/self/fd/2
LogLevel warn
LogFormat "%h %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\"" combined
CustomLog /proc/self/fd/1 combined

TypesConfig conf/mime.types
AddOutputFilterByType DEFLATE application/json application/javascript text/plain text/html text/css

# /data/live is a symlink swapped atomically by `uc generate`; Apache follows it per request.
DocumentRoot "/data/live"

<Directory />
    AllowOverride None
    Options None
    Require all denied
</Directory>
<Directory "/data">
    AllowOverride None
    Options FollowSymLinks
    Require all denied
</Directory>
<Directory "/data/live">
    # Exactly what upstream's generated .htaccess needs: Rewrite* (FileInfo) and DirectoryIndex (Indexes).
    Options FollowSymLinks Indexes
    AllowOverride FileInfo Indexes
    Require all granted
</Directory>
<Files ".ht*">
    Require all denied
</Files>

Alias "/status.json" "/data/status.json"
<Location "/status.json">
    Require all granted
    Header always set Cache-Control "no-store"
</Location>

<If "%{REQUEST_URI} =~ m#\.(json|txt)$# && %{REQUEST_URI} != '/status.json'">
    Header always set Cache-Control "public, max-age=300"
</If>
<If "%{REQUEST_URI} =~ m#^/download/.+\.(hpi|war)$#">
    Header always set Cache-Control "public, max-age=3600"
</If>

<VirtualHost *:8080>
    Include conf/uc-site.conf
</VirtualHost>

<IfDefine TLS>
    Include conf/uc-tls.conf
</IfDefine>
```

`web/conf/uc-site.conf`:

```apache
# Download handling at server level (spec §8.2); runs before the release's .htaccess.
RewriteEngine On

<IfDefine ACCESS_PROXY>
    Define DL_FLAGS "P,L"
    SSLProxyEngine on
    SSLProxyVerify require
    SSLProxyCheckPeerName on
    SSLProxyCACertificateFile "${DOWNLOAD_PROXY_CA_FILE}"
    ProxyTimeout 300
    <Proxy "${DOWNLOAD_BACKEND_URL}/*">
        RequestHeader set Authorization "${DOWNLOAD_PROXY_AUTH_HEADER}"
    </Proxy>
</IfDefine>
<IfDefine !ACCESS_PROXY>
    Define DL_FLAGS "R=302,L"
</IfDefine>

# latest links resolve through the generated current/latest/.htaccess
RewriteRule "^/download/war/latest/jenkins\.war$" "/latest/jenkins.war" [R=302,L]
RewriteRule "^/download/plugins/[^/]+/latest/([^/]+)\.hpi$" "/latest/$1.hpi" [R=302,L]

<IfDefine BACKEND_MIRROR>
    # get.jenkins.io layout, including upstream's war/ vs war-stable/ split
    RewriteRule "^/download/plugins/([^/]+)/([^/]+)/([^/]+)\.hpi$" "${DOWNLOAD_BACKEND_URL}/plugins/$1/$2/$3.hpi" [${DL_FLAGS}]
    RewriteRule "^/download/war/([0-9]+\.[0-9]+\.[0-9]+)/jenkins\.war$" "${DOWNLOAD_BACKEND_URL}/war-stable/$1/jenkins.war" [${DL_FLAGS}]
    RewriteRule "^/download/war/([^/]+)/jenkins\.war$" "${DOWNLOAD_BACKEND_URL}/war/$1/jenkins.war" [${DL_FLAGS}]
</IfDefine>

<IfDefine BACKEND_ARTIFACTORY>
    # Maven paths from the release's download map (written by uc generate)
    RewriteMap dl "txt:/data/live/.htdownload-map"
    RewriteCond "${dl:plugins/$1/$2}" "^(.+)$"
    RewriteRule "^/download/plugins/([^/]+)/([^/]+)/[^/]+\.hpi$" "${DOWNLOAD_BACKEND_URL}/%1" [${DL_FLAGS}]
    RewriteCond "${dl:war/$1}" "^(.+)$"
    RewriteRule "^/download/war/([^/]+)/jenkins\.war$" "${DOWNLOAD_BACKEND_URL}/%1" [${DL_FLAGS}]
</IfDefine>

# Anything else that looks like a binary is not available here.
RewriteRule "^/download/(plugins/[^/]+/[^/]+/[^/]+\.hpi|war/[^/]+/jenkins\.war)$" "-" [R=404,L]
```

`web/conf/uc-tls.conf`:

```apache
# Optional TLS listener (spec §8.5), Mozilla "intermediate" settings.
Listen 8443
SSLProtocol -all +TLSv1.2 +TLSv1.3
SSLCipherSuite ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:DHE-RSA-CHACHA20-POLY1305
SSLHonorCipherOrder off
SSLSessionCache "shmcb:/tmp/ssl_scache(512000)"
<VirtualHost *:8443>
    SSLEngine on
    SSLCertificateFile "${TLS_CERT_FILE}"
    SSLCertificateKeyFile "${TLS_KEY_FILE}"
    Include conf/uc-site.conf
</VirtualHost>
```

- [ ] **Step 4: Write the entrypoint, the healthcheck and the Dockerfile**

`web/entrypoint.sh`:

```bash
#!/usr/bin/env bash
# Resolve mode defaults (they must match uc/uc/config.py), wait for the first release, run httpd.
set -euo pipefail

die() { echo "entrypoint: $*" >&2; exit 64; }

[[ -n "${PUBLIC_URL:-}" ]] || die "PUBLIC_URL must be set"
export PUBLIC_URL="${PUBLIC_URL%/}"
MODE="${MODE:-}"
case "${MODE}" in
  online)    default_backend=mirror; default_artifactory=https://repo.jenkins-ci.org/; default_repository=releases ;;
  airgapped) default_backend=artifactory; default_artifactory=; default_repository= ;;
  *) die "MODE must be online or airgapped (got '${MODE}')" ;;
esac

DOWNLOAD_BACKEND="${DOWNLOAD_BACKEND:-${default_backend}}"
DOWNLOAD_ACCESS="${DOWNLOAD_ACCESS:-redirect}"
defines=()

case "${DOWNLOAD_BACKEND}" in
  mirror)
    backend_url="${DOWNLOAD_BACKEND_URL:-https://get.jenkins.io}"
    defines+=(-D BACKEND_MIRROR) ;;
  artifactory)
    if [[ -n "${DOWNLOAD_BACKEND_URL:-}" ]]; then
      backend_url="${DOWNLOAD_BACKEND_URL}"
    else
      artifactory="${ARTIFACTORY_URL:-${default_artifactory}}"
      repository="${ARTIFACTORY_REPOSITORY:-${default_repository}}"
      [[ -n "${artifactory}" && -n "${repository}" ]] \
        || die "set DOWNLOAD_BACKEND_URL, or ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY"
      backend_url="${artifactory%/}/${repository}"
    fi
    defines+=(-D BACKEND_ARTIFACTORY) ;;
  *) die "DOWNLOAD_BACKEND must be mirror or artifactory (got '${DOWNLOAD_BACKEND}')" ;;
esac
export DOWNLOAD_BACKEND_URL="${backend_url%/}"

case "${DOWNLOAD_ACCESS}" in
  redirect) ;;
  proxy)
    auth_file="${DOWNLOAD_PROXY_AUTH_FILE:-/run/secrets/download_proxy_auth}"
    credentials="$(tr -d '\r\n' < "${auth_file}" 2>/dev/null || true)"
    [[ "${credentials}" == *:* ]] || die "DOWNLOAD_ACCESS=proxy needs username:password in ${auth_file}"
    export DOWNLOAD_PROXY_AUTH_HEADER="Basic $(printf '%s' "${credentials}" | base64 -w0)"
    export DOWNLOAD_PROXY_CA_FILE="${DOWNLOAD_PROXY_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    defines+=(-D ACCESS_PROXY -D NEED_SSL) ;;
  *) die "DOWNLOAD_ACCESS must be redirect or proxy (got '${DOWNLOAD_ACCESS}')" ;;
esac

if [[ -n "${TLS_CERT_FILE:-}" || -n "${TLS_KEY_FILE:-}" ]]; then
  [[ -r "${TLS_CERT_FILE:-}" && -r "${TLS_KEY_FILE:-}" ]] || die "TLS_CERT_FILE and TLS_KEY_FILE must both be readable"
  defines+=(-D TLS -D NEED_SSL)
fi

# Apache refuses to start without DocumentRoot and the RewriteMap file (spec §8.1).
waited=0
until [[ -e /data/live/.htdownload-map ]]; do
  if (( waited % 60 == 0 )); then
    echo "entrypoint: waiting for the first published release in /data/live" >&2
  fi
  sleep 5
  waited=$((waited + 5))
done

exec httpd -DFOREGROUND "${defines[@]}"
```

`web/healthcheck.sh`:

```sh
#!/bin/sh
set -e
test -e /data/live/.htdownload-map
curl -fsS -o /dev/null --max-time 4 http://127.0.0.1:8080/uctest.json
```

`web/Dockerfile`:

```dockerfile
# Web tier (spec §8): Apache serving /data/live with upstream's .htaccess routing.
FROM httpd:2.4
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl openssl \
 && rm -rf /var/lib/apt/lists/*
COPY conf/httpd.conf conf/uc-site.conf conf/uc-tls.conf /usr/local/apache2/conf/
COPY entrypoint.sh healthcheck.sh /usr/local/bin/
# Syntax-check every define combination, then remove the scaffolding: /data/live must never ship
# in the image, or Docker would copy it into an empty volume and break the first symlink swap.
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
USER www-data
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

- [ ] **Step 5: Run the checks and confirm they pass**

Run: `make test-web`
Expected: the image build prints `Syntax OK` five times, then the script prints seven `ok:` lines and exits 0.

- [ ] **Step 6: Commit**

```bash
git add web tests/web Makefile
git commit -m "Add the Apache web tier with download backends and TLS

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Web-tier routing tests

**Files:**
- Create: `tests/routing/compose.yaml`, `tests/routing/build_fixtures.py`, `tests/routing/stub_backend.py`, `tests/routing/proxy-auth.txt`, `tests/routing/helpers.py`, `tests/routing/test_upstream_rules.py`, `tests/routing/test_downloads.py`, `tests/routing/test_tier_layouts.py`, `tests/routing/test_live_swap.py`
- Modify: `Makefile` (add `test-routing`; `test` runs `test-unit test-web test-routing`)

**Interfaces:**
- Consumes:
  - the web image (Task 15) and the uc-test image (Task 1)
  - `uc.htaccess.render_htaccess`, `uc.layout.finalize_layout`, `uc.downloadmap.write_download_map`
  - `tests/support/sitefixture.write_generator_output`
- Produces:
  - `helpers.fetch(host, path, headers=None) -> Response(status, headers, body)`, which never follows redirects; header names are lower-cased
  - the scenario trees `/fixtures/{full,artifactory,weekly,stable,zero}`

- [ ] **Step 1: Write the fixtures builder, stub backend and helpers**

`tests/routing/build_fixtures.py`:

```python
"""Build web-tier fixture trees into /fixtures/<scenario> (run by the fixtures service)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/tests/support")

from sitefixture import write_generator_output  # noqa: E402

from uc.downloadmap import write_download_map  # noqa: E402
from uc.htaccess import render_htaccess  # noqa: E402
from uc.layout import finalize_layout  # noqa: E402
from uc.model import Tiers  # noqa: E402

APP = Path("/opt/uc")
R1, R2 = "20260101T000000Z", "20260102T000000Z"
# The tier list used by upstream's site/test/test.sh at 3.18.4.
UPSTREAM_TEST_TIERS = Tiers(
    weekly=("2.172", "2.173", "2.176", "2.177", "2.181", "2.184", "2.185", "2.191", "2.195", "2.199",
            "2.204", "2.205", "2.212", "2.217", "2.222", "2.223", "2.240"),
    stable=("2.164.2", "2.164.3", "2.176.1", "2.176.2", "2.176.3", "2.176.4", "2.190.1", "2.190.3",
            "2.204.1", "2.204.2", "2.204.4", "2.204.6", "2.222.1"),
)
SMALL = Tiers(("2.540",), ("2.528.1",))
MAPS = {
    R1: {
        "plugins/git/5.2.0": "org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi",
        "war/2.528.1": "org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war",
    },
}
MAPS[R2] = {**MAPS[R1], "plugins/git/5.3.0": "org/jenkins-ci/plugins/git/5.3.0/git-5.3.0.hpi"}


def release(data: Path, release_id: str, public_url: str, tiers: Tiers) -> None:
    staging = data / "releases" / release_id
    write_generator_output(staging, public_url, tiers)
    (staging / ".htaccess").write_text(
        render_htaccess(tiers, APP / "site" / "generate-htaccess.sh", APP / "templates" / "htaccess-static")
    )
    finalize_layout(staging, tiers, APP / "templates")
    (staging / "tiers.json").write_text(
        json.dumps({"weeklyCores": list(tiers.weekly), "stableCores": list(tiers.stable)})
    )
    write_download_map(MAPS[release_id], staging / ".htdownload-map")
    (staging / "current" / "marker.txt").write_text(release_id)


def scenario(root: Path, name: str, public_url: str, tiers: Tiers, releases=(R1,)) -> None:
    data = root / name
    shutil.rmtree(data, ignore_errors=True)
    for index, release_id in enumerate(releases):
        release(data, release_id, public_url, tiers)
        stamp = 1_767_225_600 + index * 60  # distinct map mtimes, so RewriteMap reloads after a swap
        os.utime(data / "releases" / release_id / ".htdownload-map", (stamp, stamp))
    os.symlink(f"releases/{releases[0]}", data / "live")
    (data / "status.json").write_text(json.dumps({"live_release": releases[0]}) + "\n")


def main(root: Path) -> None:
    scenario(root, "full", "http://web-full:8080", UPSTREAM_TEST_TIERS)
    scenario(root, "artifactory", "http://web-artifactory:8080", SMALL, releases=(R1, R2))
    scenario(root, "weekly", "http://web-weekly:8080", Tiers(("2.540", "2.541"), ()))
    scenario(root, "stable", "http://web-stable:8080", Tiers((), ("2.528.1", "2.528.3")))
    scenario(root, "zero", "http://web-zero:8080", Tiers())
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    print(f"fixtures ready in {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
```

`tests/routing/stub_backend.py`:

```python
"""Echo backend for proxy-mode tests: answers with the request path and Authorization header."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Echo(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"path": self.path, "authorization": self.headers.get("Authorization")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Echo).serve_forever()
```

`tests/routing/proxy-auth.txt` (a single line, test credentials only):

```
routing:secret
```

`tests/routing/helpers.py`:

```python
"""HTTP helper for web-tier routing tests; redirects are never followed."""
from __future__ import annotations

import http.client
from dataclasses import dataclass


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        return self.headers.get("location")


def fetch(host: str, path: str, headers: dict[str, str] | None = None) -> Response:
    connection = http.client.HTTPConnection(host, 8080, timeout=10)
    try:
        connection.request("GET", path, headers={"User-Agent": "uc-routing-test", **(headers or {})})
        response = connection.getresponse()
        return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        connection.close()
```

- [ ] **Step 2: Write the compose file and Makefile target**

`tests/routing/compose.yaml`:

```yaml
# Web-tier routing tests (spec §13). Run with `make test-routing`.
name: uc-routing

x-web: &web
  image: ${WEB_IMAGE:-jenkins-uc/web:local}
  read_only: true
  tmpfs:
    - /tmp
  depends_on:
    fixtures:
      condition: service_completed_successfully
  healthcheck:
    test: ["CMD", "/usr/local/bin/healthcheck.sh"]
    interval: 2s
    timeout: 3s
    retries: 30
    start_period: 1s

x-uc-test: &uc-test
  image: ${UC_TEST_IMAGE:-jenkins-uc/uc-test:local}
  user: "0:0"

services:
  fixtures:
    <<: *uc-test
    entrypoint: ["python3", "/tests/routing/build_fixtures.py", "/fixtures"]
    volumes:
      - fixtures:/fixtures
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro
      - ../../uc/templates:/opt/uc/templates:ro

  backend:
    <<: *uc-test
    entrypoint: ["python3", "/tests/routing/stub_backend.py", "9000"]
    volumes:
      - ../../tests:/tests:ro

  web-full:
    <<: *web
    environment: {MODE: online, PUBLIC_URL: "http://web-full:8080"}
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: full}}

  web-artifactory:
    <<: *web
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web-artifactory:8080"
      ARTIFACTORY_URL: "http://artifactory.internal/artifactory/"
      ARTIFACTORY_REPOSITORY: jenkins
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: artifactory}}

  web-proxy:
    <<: *web
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web-proxy:8080"
      DOWNLOAD_ACCESS: proxy
      DOWNLOAD_BACKEND_URL: "http://backend:9000/repo"
    secrets:
      - download_proxy_auth
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: artifactory}}

  web-weekly:
    <<: *web
    environment: {MODE: online, PUBLIC_URL: "http://web-weekly:8080"}
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: weekly}}

  web-stable:
    <<: *web
    environment: {MODE: online, PUBLIC_URL: "http://web-stable:8080"}
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: stable}}

  web-zero:
    <<: *web
    environment: {MODE: online, PUBLIC_URL: "http://web-zero:8080"}
    volumes:
      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: zero}}

  tester:
    <<: *uc-test
    entrypoint: ["python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "/tests/routing"]
    depends_on:
      backend: {condition: service_started}
      web-full: {condition: service_healthy}
      web-artifactory: {condition: service_healthy}
      web-proxy: {condition: service_healthy}
      web-weekly: {condition: service_healthy}
      web-stable: {condition: service_healthy}
      web-zero: {condition: service_healthy}
    volumes:
      - fixtures:/fixtures
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

volumes:
  fixtures:

secrets:
  download_proxy_auth:
    file: ./proxy-auth.txt
```

Add to the `Makefile`: include `test-routing` and `test` in `.PHONY`, replace the `test:` line, and add:

```make
ROUTING = docker compose -f tests/routing/compose.yaml

test: test-unit test-web test-routing

test-routing: build-uc build-web
	$(ROUTING) run --rm tester $(ARGS); rc=$$?; \
	  $(ROUTING) down -v --remove-orphans >/dev/null 2>&1; exit $$rc
```

- [ ] **Step 3: Write the routing tests**

`tests/routing/test_upstream_rules.py`:

```python
"""Upstream's redirect expectations (site/test/test.sh at 3.18.4), ported so that a mismatch fails."""
import pytest
from helpers import fetch

HOST = "web-full"
BASE = "http://web-full:8080"
UC = "/update-center.json?version="
ACTUAL = "/update-center.actual.json?version="

EXPECTED = [
    ("/update-center.json", "/current/update-center.json"),
    ("/update-center.actual.json", "/current/update-center.actual.json"),
    ("/release-history.json", "/current/release-history.json"),
    ("/plugin-versions.json", "/current/plugin-versions.json"),
    ("/plugin-documentation-urls.json", "/current/plugin-documentation-urls.json"),
    ("/latestCore.txt", "/current/latestCore.txt"),
    ("/stable/update-center.json", "/dynamic-stable-2.222.1/update-center.json"),
    ("/stable/latestCore.txt", "/dynamic-stable-2.222.1/latestCore.txt"),
    (UC + "2.246", "/dynamic-2.240/update-center.json"),
    (UC + "2.246-SNAPSHOT", "/dynamic-2.240/update-center.json"),
    (UC + "2.240", "/dynamic-2.240/update-center.json"),
    (UC + "2.240-SNAPSHOT", "/dynamic-2.240/update-center.json"),
    (UC + "2.225", "/dynamic-2.223/update-center.json"),
    (UC + "2.225-SNAPSHOT", "/dynamic-2.223/update-center.json"),
    (UC + "2.223", "/dynamic-2.223/update-center.json"),
    (UC + "2.223-SNAPSHOT", "/dynamic-2.223/update-center.json"),
    (UC + "2.222", "/dynamic-2.222/update-center.json"),
    (UC + "2.222-SNAPSHOT", "/dynamic-2.222/update-center.json"),
    (UC + "2.222.1", "/dynamic-stable-2.222.1/update-center.json"),
    (UC + "2.222.1-SNAPSHOT", "/dynamic-stable-2.222.1/update-center.json"),
    (UC + "2.55", "/dynamic-2.172/update-center.json"),
    (UC + "2.6", "/dynamic-2.172/update-center.json"),
    (UC + "2.55-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "2.6-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "2.204.1", "/dynamic-stable-2.204.1/update-center.json"),
    (UC + "2.204.2", "/dynamic-stable-2.204.2/update-center.json"),
    (UC + "2.204.3", "/dynamic-stable-2.204.2/update-center.json"),
    (UC + "2.204.4", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.5", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.5-SNAPSHOT", "/dynamic-stable-2.204.4/update-center.json"),
    (UC + "2.204.6", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.6-SNAPSHOT", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.7", "/dynamic-stable-2.204.6/update-center.json"),
    (UC + "2.204.7-SNAPSHOT", "/dynamic-stable-2.204.6/update-center.json"),
    (ACTUAL + "2.222", "/dynamic-2.222/update-center.actual.json"),
    (ACTUAL + "2.222.1", "/dynamic-stable-2.222.1/update-center.actual.json"),
    (ACTUAL + "2.222-SNAPSHOT", "/dynamic-2.222/update-center.actual.json"),
    (ACTUAL + "2.222.1-SNAPSHOT", "/dynamic-stable-2.222.1/update-center.actual.json"),
    ("/plugin-documentation-urls.json?version=2.222.1", "/current/plugin-documentation-urls.json"),
    ("/latestCore.txt?version=2.222.1", "/current/latestCore.txt"),
    (UC + "1.650", "/dynamic-2.172/update-center.json"),
    (UC + "1.650-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "1.580", "/dynamic-2.172/update-center.json"),
    (UC + "1.580-SNAPSHOT", "/dynamic-2.172/update-center.json"),
    (UC + "1.580.1", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "1.580.1-SNAPSHOT", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.46.1", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.46.1-SNAPSHOT", "/dynamic-stable-2.164.2/update-center.json"),
    (UC + "2.200", "/dynamic-2.199/update-center.json"),
    (UC + "3.0", "/dynamic-2.240/update-center.json"),
    (UC + "3.0.1", "/dynamic-stable-2.222.1/update-center.json"),
]


@pytest.mark.parametrize("path,target", EXPECTED)
def test_redirect(path, target):
    response = fetch(HOST, path)
    assert response.status in (301, 302), response.status
    assert response.location == BASE + target


@pytest.mark.parametrize("path", ["/update-center.json?uctest", "/foo/update-center.json?uctest"])
def test_uctest_is_a_tiny_ok(path):
    response = fetch(HOST, path)
    assert response.status == 200
    assert response.headers["content-length"] == "3"
```

`tests/routing/test_downloads.py`:

```python
import base64
import json

import pytest
from helpers import fetch

ARTIFACTORY = "http://artifactory.internal/artifactory/jenkins"


def test_mirror_plugin_redirect():
    response = fetch("web-full", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 302
    assert response.location == "https://get.jenkins.io/plugins/git/5.2.0/git.hpi"


@pytest.mark.parametrize(
    "version,target",
    [
        ("2.222.1", "https://get.jenkins.io/war-stable/2.222.1/jenkins.war"),
        ("2.240", "https://get.jenkins.io/war/2.240/jenkins.war"),
    ],
)
def test_mirror_war_redirects_split_like_upstream(version, target):
    assert fetch("web-full", f"/download/war/{version}/jenkins.war").location == target


def test_latest_links_resolve_locally():
    assert fetch("web-full", "/download/plugins/git/latest/git.hpi").location == "http://web-full:8080/latest/git.hpi"
    assert fetch("web-full", "/download/war/latest/jenkins.war").location == "http://web-full:8080/latest/jenkins.war"
    response = fetch("web-full", "/latest/git.hpi")
    assert response.status == 302
    assert response.location == "http://web-full:8080/download/plugins/git/5.2.0/git.hpi"


def test_artifactory_map_hit_and_miss():
    response = fetch("web-artifactory", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 302
    assert response.location == f"{ARTIFACTORY}/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
    assert fetch("web-artifactory", "/download/plugins/git/9.9.9/git.hpi").status == 404
    war = fetch("web-artifactory", "/download/war/2.528.1/jenkins.war")
    assert war.location == f"{ARTIFACTORY}/org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war"
    assert fetch("web-artifactory", "/download/war/2.540/jenkins.war").status == 404


def test_proxy_mode_streams_with_credentials():
    response = fetch("web-proxy", "/download/plugins/git/5.2.0/git.hpi")
    assert response.status == 200
    echoed = json.loads(response.body)
    assert echoed["path"] == "/repo/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
    assert echoed["authorization"] == "Basic " + base64.b64encode(b"routing:secret").decode()
    assert fetch("web-proxy", "/download/plugins/git/9.9.9/git.hpi").status == 404


def test_download_index_pages_are_served():
    assert fetch("web-full", "/download/plugins/git/").status == 200


def test_cache_headers():
    assert fetch("web-full", "/update-center.json?version=2.240").headers["cache-control"] == "public, max-age=300"
    assert fetch("web-full", "/download/plugins/git/5.2.0/git.hpi").headers["cache-control"] == "public, max-age=3600"
    assert fetch("web-full", "/status.json").headers["cache-control"] == "no-store"


def test_json_is_compressed_when_asked():
    response = fetch("web-full", "/current/update-center.actual.json", {"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


def test_private_files_are_not_served():
    assert fetch("web-full", "/.htdownload-map").status == 403
    assert fetch("web-full", "/.htaccess").status == 403


def test_status_landing_page_and_uctest():
    assert json.loads(fetch("web-full", "/status.json").body)["live_release"] == "20260101T000000Z"
    assert b"Jenkins Update Center" in fetch("web-full", "/").body
    assert fetch("web-full", "/uctest.json").body == b"{}\n"


def test_unknown_download_paths_are_404():
    assert fetch("web-full", "/download/something.zip").status == 404
```

`tests/routing/test_tier_layouts.py`:

```python
import pytest
from helpers import fetch

UC = "/update-center.json"


@pytest.mark.parametrize(
    "host,path,target",
    [
        ("web-weekly", UC + "?version=2.540", "/dynamic-2.540/update-center.json"),
        ("web-weekly", UC + "?version=2.500", "/dynamic-2.540/update-center.json"),
        ("web-weekly", UC + "?version=2.528.1", "/current/update-center.json"),
        ("web-weekly", "/stable/update-center.json", "/current/update-center.json"),
        ("web-stable", UC + "?version=2.528.2", "/dynamic-stable-2.528.1/update-center.json"),
        ("web-stable", UC + "?version=2.528.3", "/dynamic-stable-2.528.3/update-center.json"),
        ("web-stable", UC + "?version=2.540", "/current/update-center.json"),
        ("web-stable", "/stable/update-center.json", "/dynamic-stable-2.528.3/update-center.json"),
        ("web-zero", UC + "?version=2.528.1", "/current/update-center.json"),
        ("web-zero", UC + "?version=2.540", "/current/update-center.json"),
        ("web-zero", "/stable/update-center.json", "/current/update-center.json"),
    ],
)
def test_partial_and_zero_tier_routing(host, path, target):
    response = fetch(host, path)
    assert response.status in (301, 302)
    assert response.location == f"http://{host}:8080{target}"


def test_zero_tier_uctest():
    assert fetch("web-zero", "/update-center.json?uctest").status == 200
```

`tests/routing/test_live_swap.py`:

```python
"""A live symlink swap, and the RewriteMap behind it, take effect without restarting Apache (spec S5)."""
import os
from pathlib import Path

from helpers import fetch

DATA = Path("/fixtures/artifactory")
R1, R2 = "20260101T000000Z", "20260102T000000Z"
NEW = "/download/plugins/git/5.3.0/git.hpi"


def swap(release_id: str) -> None:
    tmp = DATA / "live.tmp"
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(f"releases/{release_id}", tmp)
    os.replace(tmp, DATA / "live")


def test_live_swap_needs_no_restart():
    assert fetch("web-artifactory", "/current/marker.txt").body == R1.encode()
    assert fetch("web-artifactory", NEW).status == 404
    swap(R2)
    try:
        assert fetch("web-artifactory", "/current/marker.txt").body == R2.encode()
        response = fetch("web-artifactory", NEW)
        assert response.status == 302
        assert response.location.endswith("/org/jenkins-ci/plugins/git/5.3.0/git-5.3.0.hpi")
    finally:
        swap(R1)
    assert fetch("web-artifactory", NEW).status == 404
```

- [ ] **Step 4: Run the routing tests**

Run: `make test-routing`
Expected: `78 passed`, then the compose project is removed. If a redirect test fails, compare with upstream's `site/test/test.sh`; the expected targets are copied from it verbatim.

- [ ] **Step 5: Commit**

```bash
git add tests/routing Makefile
git commit -m "Add web-tier routing tests, including upstream's redirect expectations

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Compose stack, configuration examples and operator documentation

**Files:**
- Create: `compose.yaml`, `compose.airgapped.yaml`, `.env.online.example`, `.env.airgapped.example`, `secrets/README.md`, `config/overlay/README.md`, `docs/operations.md`
- Replace: `README.md`
- Modify: `Makefile` (add `pki-init`, `secrets-placeholders`, `compose-check`)

**Interfaces:**
- Consumes: both images, `uc schedule`, `uc pki init`, and every setting in `uc/uc/config.py`
- Produces:
  - Compose services `web`, `scheduler` and `uc` (profile `tools`), plus volume `uc-data`
  - secrets `update-center.key`, `update-center.cert`, `root-ca.crt`, `artifactory_username`, `artifactory_password`, `github_username`, `github_token`, `download_proxy_auth`

- [ ] **Step 1: Write the compose files**

`compose.yaml`:

```yaml
# Jenkins update center (spec §4).
#   online:     docker compose up -d
#   air-gapped: docker compose -f compose.yaml -f compose.airgapped.yaml up -d
#   one-shot:   docker compose run --rm uc doctor | generate | rollback | status
name: jenkins-update-center

x-uc: &uc
  image: ${UC_IMAGE:-jenkins-uc/uc:local}
  env_file: ${UC_ENV_FILE:-.env}
  read_only: true
  tmpfs:
    - /tmp
  volumes:
    - uc-data:/data
    - ./config/overlay:/config/overlay:ro
  secrets:
    - update-center.key
    - update-center.cert
    - root-ca.crt
    - artifactory_username
    - artifactory_password
    - github_username
    - github_token

services:
  web:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    env_file: ${UC_ENV_FILE:-.env}
    read_only: true
    tmpfs:
      - /tmp
    ports:
      - "${HTTP_PORT:-8080}:8080"
      - "${HTTPS_PORT:-8443}:8443"
    volumes:
      - uc-data:/data:ro
    secrets:
      - download_proxy_auth
    restart: unless-stopped

  scheduler:
    <<: *uc
    command: ["schedule"]
    restart: unless-stopped

  uc:
    <<: *uc
    profiles: ["tools"]

volumes:
  uc-data:

secrets:
  update-center.key:
    file: ./secrets/update-center.key
  update-center.cert:
    file: ./secrets/update-center.cert
  root-ca.crt:
    file: ./secrets/root-ca.crt
  artifactory_username:
    file: ./secrets/artifactory_username
  artifactory_password:
    file: ./secrets/artifactory_password
  github_username:
    file: ./secrets/github_username
  github_token:
    file: ./secrets/github_token
  download_proxy_auth:
    file: ./secrets/download_proxy_auth
```

`compose.airgapped.yaml`:

```yaml
# Air-gapped override (spec §7.3): these outside hosts resolve to 127.0.0.1, so their clients fail
# at once instead of waiting for TCP timeouts. Every other name still uses the normal resolver.
x-blocked-hosts: &blocked-hosts
  - "api.github.com:127.0.0.1"
  - "github.com:127.0.0.1"
  - "codeload.github.com:127.0.0.1"
  - "raw.githubusercontent.com:127.0.0.1"
  - "reports.jenkins.io:127.0.0.1"
  - "updates.jenkins.io:127.0.0.1"

services:
  scheduler:
    extra_hosts: *blocked-hosts
  uc:
    extra_hosts: *blocked-hosts
```

- [ ] **Step 2: Write the configuration examples**

`.env.online.example`:

```
# Copy to .env. Online: the generator reads repo.jenkins-ci.org (a free Jenkins community account
# is required) and downloads redirect to get.jenkins.io. Every setting: spec §11.
MODE=online
PUBLIC_URL=https://jenkins-updates.example.com
# GENERATE_SCHEDULE=0 */6 * * *
# RESOURCES_REF=master
# JAVA_TOOL_OPTIONS=-Xmx4g -Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128
# HTTP_PORT=8080
```

`.env.airgapped.example`:

```
# Copy to .env. Air-gapped: the generator reads your internal Artifactory (see docs/operations.md,
# "What the Artifactory repository must contain") and downloads redirect to it. Every setting: spec §11.
MODE=airgapped
PUBLIC_URL=https://jenkins-updates.corp.example
ARTIFACTORY_URL=https://artifactory.corp.example/artifactory/
ARTIFACTORY_REPOSITORY=jenkins-plugins-local
# Controllers cannot read Artifactory anonymously? Stream downloads through the web tier instead:
# DOWNLOAD_ACCESS=proxy
# Procured copies of upstream data (optional; otherwise the image's bundled resources are used):
# RESOURCES_SOURCE=url
# RESOURCES_URL=https://artifactory.corp.example/artifactory/generic-local/update-center2-master.tar.gz
# RESOURCES_SHA256=
# POPULARITY_JSON_URL=https://artifactory.corp.example/artifactory/generic-local/latestNumbers.json
# TOOL_INSTALLERS_URL=https://artifactory.corp.example/artifactory/generic-local/updates/
```

`secrets/README.md`:

```markdown
# secrets/

Everything here except this file is gitignored. Compose mounts each file under `/run/secrets/`.

| File | Required | Content |
|---|---|---|
| `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | from `make pki-init` (move `root/root-ca.key` offline afterwards) |
| `artifactory_username`, `artifactory_password` | yes | account that can run AQL on the source repository |
| `github_username`, `github_token` | online, recommended | GitHub user and token; empty files mean "not set" |
| `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only Artifactory account |

Create the optional files empty with `make secrets-placeholders`. On Linux the containers read
these files as uid 10001 (uc) and uid 33 (web); see docs/operations.md, "File ownership on Linux".
```

`config/overlay/README.md`:

```markdown
# config/overlay/

Files placed here are merged into upstream's `resources/` on every generation (spec §6 step 2).
This README is ignored.

- `*.properties`: your lines are appended after upstream's, and later keys win. For example, to hide
  a plugin or one release of it everywhere, create `artifact-ignores.properties`:

      # suspended internally, see CHANGE-1234
      some-plugin
      git@5.2.0

- `warnings.json`: a JSON array of warnings, merged by `id` (yours replace upstream's entries with
  the same id). Jenkins shows these on the plugin manager and the admin monitor:

      [{"id": "CORP-2026-01", "type": "plugin", "name": "some-plugin",
        "message": "Blocked by security review", "url": "https://wiki.corp.example/CORP-2026-01",
        "versions": [{"lastVersion": "1.4", "pattern": "1[.][0-4](|[.-].*)"}]}]

- Any other file (for example `index-template.html`, `platform-plugins.json`) replaces upstream's.
```

- [ ] **Step 3: Add the Makefile helpers**

Add `pki-init secrets-placeholders compose-check` to `.PHONY`, then add:

```make
# Creates the PKI in ./secrets as uid 10001 (the uc user). Refuses to overwrite existing files.
pki-init: build-uc
	@mkdir -p secrets
	docker run --rm --user 0:0 --entrypoint sh -v "$(CURDIR)/secrets:/pki" $(UC_IMAGE) \
	  -c 'uc pki init --out /pki && chown -R 10001:10001 /pki'

secrets-placeholders:
	@mkdir -p secrets
	@for f in artifactory_username artifactory_password github_username github_token download_proxy_auth; do \
	  [ -e secrets/$$f ] || : > secrets/$$f; done
	@echo "secrets/: created any missing credential files (empty means 'not set')"

# UC_ENV_FILE selects the services' env_file; --env-file only feeds compose's own interpolation.
compose-check:
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example config --quiet
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml config --quiet
```

- [ ] **Step 4: Write `docs/operations.md`**

````markdown
# Operating the Jenkins update center

Design and rationale: `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`.

## 1. Build

```bash
make build        # jenkins-uc/uc:local and jenkins-uc/web:local
make test         # unit, web entrypoint and routing tests
```

## 2. Configure

Copy `.env.online.example` or `.env.airgapped.example` to `.env` and set `PUBLIC_URL` to the exact
origin Jenkins will use, with no path (for example `https://jenkins-updates.example.com`). The URL is
baked into the signed metadata, so changing it requires a new generation. To keep several
configurations side by side, set `UC_ENV_FILE=<file>` when running `docker compose`.

## 3. Signing keys

```bash
make pki-init
```

This writes `secrets/root-ca.crt`, `secrets/update-center.key`, `secrets/update-center.cert` and
`secrets/root/root-ca.key`. **Move `secrets/root/` to offline storage**; it is only needed to issue
the next signing certificate:

```bash
# once a year, or when status.json shows signing_cert.days_left getting low
docker run --rm --user 0:0 --entrypoint sh -v "$PWD/secrets:/pki" -v /offline/root:/pki/root \
  jenkins-uc/uc:local -c 'uc pki rotate --dir /pki && chown -R 10001:10001 /pki'
```

The generator refuses to sign within `CERTIFICATE_MINIMUM_VALID_DAYS` (30) days of expiry, so an
expiring certificate stops publication before Jenkins would reject the signature.

## 4. Credentials

```bash
make secrets-placeholders
printf '%s' 'svc-jenkins-uc' > secrets/artifactory_username
printf '%s' '...password or API token...' > secrets/artifactory_password
# online, recommended: GitHub user and a token without scopes
printf '%s' 'my-bot' > secrets/github_username
printf '%s' 'ghp_...' > secrets/github_token
# only with DOWNLOAD_ACCESS=proxy: a read-only account for streaming downloads
printf '%s' 'svc-read:...' > secrets/download_proxy_auth
```

Online mode uses a free Jenkins community account (https://accounts.jenkins.io) for
`repo.jenkins-ci.org`. Anonymous AQL queries are refused with HTTP 403.

### File ownership on Linux

Compose mounts secret files with their host ownership. The uc containers run as uid 10001 and the web
container as uid 33:

```bash
sudo chown 10001:10001 secrets/update-center.* secrets/root-ca.crt secrets/artifactory_* secrets/github_*
sudo chmod 0400 secrets/update-center.key secrets/artifactory_password secrets/github_token
sudo chown 33:33 secrets/download_proxy_auth && sudo chmod 0400 secrets/download_proxy_auth
```

Docker Desktop (macOS, Windows) needs no changes.

## 5. Start

```bash
docker compose up -d                                                  # online
docker compose -f compose.yaml -f compose.airgapped.yaml up -d        # air-gapped
```

The scheduler generates a release straight away when none is live; the first online run fetches a
manifest and POM for every plugin release and can take hours. The web container waits for that first
release, then serves it. Later runs follow `GENERATE_SCHEDULE` (every 6 hours by default).

## 6. Check

```bash
docker compose run --rm uc status              # the same as $PUBLIC_URL/status.json
docker compose run --rm uc doctor --fetch      # what the generator sees in the source repository
curl -sI "$PUBLIC_URL/update-center.json?version=2.528.1"   # expect a redirect to a dynamic-* site
```

`status.json` fields to monitor:

- `last_run.result` and `last_run.error`
- `signing_cert.days_left`
- `resources.age_days` and `resources.stale` (stale after `STALE_AFTER_DAYS`)
- `warnings`

## 7. Configure Jenkins controllers

1. Copy `secrets/root-ca.crt` to `$JENKINS_HOME/update-center-rootCAs/root-ca.crt`.
2. Point the default update site at `$PUBLIC_URL/update-center.json`: use *Manage Jenkins → Plugins →
   Advanced settings → Update Site*, or `$JENKINS_HOME/init.groovy.d/update-site.groovy`:

   ```groovy
   import hudson.model.UpdateSite
   import jenkins.model.Jenkins

   def uc = Jenkins.get().updateCenter
   uc.sites.replaceBy([new UpdateSite("default", "https://jenkins-updates.example.com/update-center.json")])
   uc.save()
   ```

3. For `jenkins-plugin-cli` (the Jenkins Docker image), set:

   ```
   JENKINS_UC=https://jenkins-updates.example.com
   JENKINS_UC_EXPERIMENTAL=https://jenkins-updates.example.com/experimental
   JENKINS_PLUGIN_INFO=https://jenkins-updates.example.com/current/plugin-versions.json
   ```

With `DOWNLOAD_ACCESS=redirect`, controllers download plugins from `DOWNLOAD_BACKEND_URL` directly
and must be able to read it without credentials; otherwise use `DOWNLOAD_ACCESS=proxy`.

## 8. Everyday operations

| Task | Command |
|---|---|
| Generate now | `docker compose run --rm uc generate` |
| Accept a deliberate drop in plugin count | `docker compose run --rm uc generate --force` |
| Roll back to the previous release | `docker compose run --rm uc rollback` |
| Roll back to a specific release | `docker compose run --rm uc rollback 20260922T030000Z` |
| Logs | `docker compose logs -f scheduler web` |

The last `RETAIN_RELEASES` (5) releases are kept under `/data/releases/`. The last failed attempt is
kept as `/data/releases/.failed-<id>`, and its error is in `status.json` under `last_run.error`. The
generator's full output goes to the container log: `docker compose logs scheduler` for scheduled runs,
or the terminal for `docker compose run --rm uc generate`.

## 9. Air-gapped specifics

### What the Artifactory repository must contain

- One **local** repository in Maven layout: `<group path>/<artifactId>/<version>/<artifactId>-<version>.hpi`
  plus the **real `.pom`** from `repo.jenkins-ci.org` at the same path. Plugins without a POM are
  dropped.
- Core: `org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war` and `.pom` for every core
  version you want to offer. Without them the site offers no core and has no tiers.
- An account that can run AQL on the repository.

`uc doctor --fetch` checks all of this and lists anything the generator will drop.

### Keeping upstream data fresh

Security warnings, suspensions, popularity and tool installers come from outside. To keep them
current, procure them alongside plugins:

- `RESOURCES_URL`: a tarball of upstream `jenkins-infra/update-center2` (any tarball containing
  `resources/warnings.json`), with `RESOURCES_SHA256`.
- `POPULARITY_JSON_URL`: a copy of
  `https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json`.
- `TOOL_INSTALLERS_URL`: a directory listing that contains the `*.json` and `*.json.html` files from
  `https://updates.jenkins.io/updates/`.

Without them the site still works; the bundled resources age, and `status.json` shows how old they are.

## 10. TLS in the web container

Mount a certificate and key, then set `TLS_CERT_FILE` and `TLS_KEY_FILE`:

```yaml
# compose.tls.yaml
services:
  web:
    environment:
      TLS_CERT_FILE: /tls/tls.crt
      TLS_KEY_FILE: /tls/tls.key
    volumes:
      - ./tls:/tls:ro
```

Then run `docker compose -f compose.yaml -f compose.tls.yaml up -d`. Port 8443 serves TLS; 8080 stays
available for a proxy in front.

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ARTIFACTORY_USERNAME and ARTIFACTORY_PASSWORD need to be set` / AQL HTTP 403 | Credentials missing or not allowed to run AQL (spec S1). |
| `PUBLIC_URL must be a bare origin` | Remove the path; give the update center its own hostname. |
| `signing certificate expires in N days` | Rotate the signing certificate (section 3). |
| `plugin count N < 0.9 × M` | The source shrank. Check `uc doctor`; if intended, `uc generate --force`. |
| `upstream generate-htaccess.sh changed` | An upgrade changed upstream's routing script; follow docs/upgrading.md. |
| Jenkins: "signature verification failed" | `root-ca.crt` missing from `update-center-rootCAs/`, or controller clock skew. |
| Plugin downloads fail with 404 | The version is not in the download map; check `uc doctor` and the Artifactory contents. |
| web container keeps logging "waiting for the first published release" | No release is live yet; check `docker compose logs scheduler`. |
````

- [ ] **Step 5: Replace `README.md`**

```markdown
# Jenkins update center, self-hosted

The same update center Jenkins uses (`updates.jenkins.io`), in two containers:

- **uc**: runs the upstream `update-center2` generator (3.18.4, one small patch), signs everything
  with your own PKI, validates each release and publishes it atomically. It keeps the last five
  releases for rollback.
- **web**: Apache serving the result, including the `?version=` routing to `dynamic-*` tiers, every
  upstream JSON file, and download redirects to get.jenkins.io or your Artifactory.

Two modes:
- `online` reads `repo.jenkins-ci.org`.
- `airgapped` reads your internal Artifactory and skips whatever it cannot reach.

```bash
make build
cp .env.online.example .env      # or .env.airgapped.example; set PUBLIC_URL
make pki-init secrets-placeholders
# write secrets/artifactory_username and secrets/artifactory_password
docker compose up -d
```

- Operations: [docs/operations.md](docs/operations.md)
- Upgrading upstream: [docs/upgrading.md](docs/upgrading.md)
- Design: [docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md](docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md)
```

- [ ] **Step 6: Validate the compose files**

Run: `make compose-check`
Expected: exit 0 with no output. `docker compose config` accepts missing secret files, and `UC_ENV_FILE` points the services at the example files, so a local `.env` is neither needed nor touched.

- [ ] **Step 7: Smoke-test the stack locally**

```bash
make pki-init secrets-placeholders
UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example run --rm uc status
```

Expected: prints `{}`, because no release exists yet. This proves the image, secrets and settings load together. The existing `.env` (the prototype's, which uses old variable names) is not read. Replace it with a copy of an example when you deploy.

- [ ] **Step 8: Commit**

```bash
git add compose.yaml compose.airgapped.yaml .env.online.example .env.airgapped.example \
        secrets/README.md config/overlay/README.md docs/operations.md README.md Makefile
git commit -m "Add compose stack, configuration examples and operator docs

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---
### Task 18: Fake Artifactory and seed tooling for integration tests

**Files:**
- Create: `tests/integration/fake_artifactory.py`, `tests/integration/seed.py`, `tests/integration/netutil.py`
- Test: `tests/unit/test_fake_artifactory.py`

**Interfaces:**
- Consumes: `uc.aql.query_repository`, `uc.aql.classify`, `uc.doctor.ArtifactSource`, `uc.doctor.parse_manifest`
- Produces:
  - `fake_artifactory`:
    - `PREFIX = "/artifactory/"`
    - `aql_results(root, repository) -> list[dict]`
    - `read_artifact(root, relative) -> bytes | None`
    - `make_handler(root, credentials, anonymous_read)`
    - CLI: `fake_artifactory.py --root DIR --port N --credentials user:pass [--anonymous-read]`
  - `seed.py versions` prints the three lines `LTS_OLD=...`, `LTS_NEW=...` and `WEEKLY=...`
  - `seed.py repo --downloads DIR --out ROOT --repository NAME --cores A,B,C --jenkins-version V` writes a Maven tree under `ROOT/NAME/` plus `ROOT/seeded.json`
  - `netutil`:
    - `fetch(url, headers=None) -> Response(status, headers, body)`, which never follows redirects
    - `get(url) -> bytes`, which follows redirects
    - `wait_for(url, timeout=300)`

- [ ] **Step 1: Write the failing contract tests**

`tests/unit/test_fake_artifactory.py`:

```python
"""The test-only fake speaks the same three calls update-center2 makes, as seen by our own clients."""
import io
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from fake_artifactory import aql_results, make_handler  # noqa: E402

from uc.aql import Gav, classify, query_repository  # noqa: E402
from uc.doctor import ArtifactSource  # noqa: E402


def hpi(short_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\r\nShort-Name: {short_name}\r\n\r\n")
    return buffer.getvalue()


@pytest.fixture
def server(tmp_path):
    base = tmp_path / "jenkins" / "org" / "jenkins-ci" / "plugins" / "git" / "5.2.0"
    base.mkdir(parents=True)
    (base / "git-5.2.0.hpi").write_bytes(hpi("git"))
    (base / "git-5.2.0.pom").write_text("<project><name>Git</name></project>")
    (tmp_path / "jenkins" / "README.txt").write_text("ignored")

    def start(anonymous_read):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, "uc:pw", anonymous_read))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}/artifactory/"

    servers = []
    yield tmp_path, start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def test_aql_results_match_the_generator_schema(server):
    root, _ = server
    results = aql_results(root, "jenkins")
    assert [r["name"] for r in results] == ["git-5.2.0.hpi", "git-5.2.0.pom"]
    assert set(results[0]) == {"repo", "path", "name", "modified", "created", "sha256", "actual_sha1", "size"}
    assert results[0]["path"] == "org/jenkins-ci/plugins/git/5.2.0"
    assert results[0]["modified"].endswith(".000Z")


def test_our_aql_client_reads_the_fake(server):
    _, start = server
    url = start(anonymous_read=False)
    listing = classify(query_repository(url + "api/", "jenkins", "uc", "pw"))
    assert [g.artifact_id for g in listing.plugins] == ["git"]


def test_archive_entries_and_files(server):
    _, start = server
    source = ArtifactSource(start(anonymous_read=False), "jenkins", "uc", "pw", None, allow_network=True)
    gav = Gav("org.jenkins-ci.plugins", "git", "5.2.0", "hpi")
    assert "Short-Name: git" in source.manifest(gav)
    assert source.pom(gav) == b"<project><name>Git</name></project>"


def test_private_reads_challenge_for_basic_auth(server):
    _, start = server
    url = start(anonymous_read=False) + "jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url)
    assert err.value.code == 401
    assert err.value.headers["WWW-Authenticate"] == 'Basic realm="Artifactory Realm"'
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `make test-unit ARGS="-k fake_artifactory"`
Expected: FAIL with `ModuleNotFoundError: No module named 'fake_artifactory'`.

- [ ] **Step 3: Implement `tests/integration/fake_artifactory.py`**

```python
"""Test-only stand-in for the three Artifactory calls update-center2 makes (spec §13):
POST /artifactory/api/search/aql, GET /artifactory/<repo>/<path>, GET /artifactory/<repo>/<path>!/<entry>.
update-center2 answers 401 challenges with credentials, so private reads use WWW-Authenticate: Basic."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PREFIX = "/artifactory/"
EXTENSIONS = (".hpi", ".jpi", ".war", ".pom")
_REPO = re.compile(r'"repo":\{"\$eq":"([A-Za-z0-9._-]+)"\}')


def aql_results(root: Path, repository: str) -> list[dict]:
    base = root / repository
    results = []
    for directory, _, names in os.walk(base):
        for name in names:
            if not name.endswith(EXTENSIONS):
                continue
            path = Path(directory) / name
            data = path.read_bytes()
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            results.append({
                "repo": repository, "path": path.parent.relative_to(base).as_posix(), "name": name,
                "modified": stamp, "created": stamp, "sha256": hashlib.sha256(data).hexdigest(),
                "actual_sha1": hashlib.sha1(data).hexdigest(), "size": len(data),
            })
    return sorted(results, key=lambda r: (r["path"], r["name"]))


def read_artifact(root: Path, relative: str) -> bytes | None:
    archive, separator, entry = relative.partition("!/")
    target = (root / archive).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        return None
    if not separator:
        return target.read_bytes()
    try:
        with zipfile.ZipFile(target) as zipped:
            return zipped.read(entry)
    except (KeyError, zipfile.BadZipFile):
        return None


def make_handler(root: Path, credentials: str, anonymous_read: bool):
    expected = "Basic " + base64.b64encode(credentials.encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _challenge(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Artifactory Realm"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == expected

        def do_POST(self) -> None:
            if self.path != PREFIX + "api/search/aql":
                return self._send(404)
            if not self._authorized():
                return self._challenge()
            query = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            match = _REPO.search(query)
            if not match:
                return self._send(400, b"unsupported query", "text/plain")
            self._send(200, json.dumps({"results": aql_results(root, match.group(1))}).encode(), "application/json")

        def do_GET(self) -> None:
            if not self.path.startswith(PREFIX):
                return self._send(404)
            if not anonymous_read and not self._authorized():
                return self._challenge()
            relative = urllib.parse.unquote(self.path[len(PREFIX):].split("?", 1)[0])
            data = read_artifact(root, relative)
            if data is None:
                return self._send(404)
            self._send(200, data)

        do_HEAD = do_GET

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="directory containing one folder per repository")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--credentials", required=True, help="username:password")
    parser.add_argument("--anonymous-read", action="store_true", help="allow file reads without credentials")
    args = parser.parse_args()
    handler = make_handler(args.root, args.credentials, args.anonymous_read)
    ThreadingHTTPServer(("0.0.0.0", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement the seed script and network helpers**

`tests/integration/seed.py`:

```python
"""Seed data for the air-gapped integration test; runs on a network with internet access.

  seed.py versions   print LTS_OLD, LTS_NEW, WEEKLY (for .versions.env)
  seed.py repo ...   build a Maven-layout repository from jenkins-plugin-cli downloads, with the
                     real POMs, three core WARs and two deliberately broken test plugins
"""
from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from uc.doctor import parse_manifest

UPDATES = "https://updates.jenkins.io"
RELEASES = "https://repo.jenkins-ci.org/releases"
STUB_POM = (b"<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId>"
            b"<artifactId>uc-test-stubpom</artifactId><version>1.0</version><packaging>hpi</packaging></project>")


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "jenkins-uc-seed/1"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def versions() -> None:
    lts_new = get(f"{UPDATES}/stable/latestCore.txt").decode().strip()
    weekly = get(f"{UPDATES}/current/latestCore.txt").decode().strip()
    lts_old = json.loads(get(f"{UPDATES}/tiers.json"))["stableCores"][0]
    print(f"LTS_OLD={lts_old}\nLTS_NEW={lts_new}\nWEEKLY={weekly}")


def put(root: Path, group: str, artifact: str, version: str, extension: str, data: bytes) -> None:
    path = root / group.replace(".", "/") / artifact / version / f"{artifact}-{version}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def synthetic_hpi(artifact: str, jenkins_version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", (
            "Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.uctest\r\n"
            f"Short-Name: {artifact}\r\nExtension-Name: {artifact}\r\nLong-Name: UC test {artifact}\r\n"
            f"Plugin-Version: 1.0\r\nJenkins-Version: {jenkins_version}\r\n\r\n"
        ))
        archive.writestr("index.jelly", f"<div>uc integration-test plugin {artifact}</div>")
    return buffer.getvalue()


def repo(args: argparse.Namespace) -> None:
    root = args.out / args.repository
    seeded = []
    for plugin in sorted(args.downloads.glob("*.[hj]pi")):
        data = plugin.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = parse_manifest(archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace"))
        group = manifest["Group-Id"]
        artifact = manifest.get("Short-Name") or manifest["Extension-Name"]
        version = manifest["Plugin-Version"].split(" ")[0]
        put(root, group, artifact, version, "hpi", data)
        put(root, group, artifact, version, "pom",
            get(f"{RELEASES}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"))
        seeded.append(f"{artifact}:{version}")
    if not seeded:
        raise SystemExit(f"no .hpi/.jpi files in {args.downloads}; did the seed-plugins step fail?")
    cores = args.cores.split(",")
    for core in cores:
        base = f"{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}"
        put(root, "org.jenkins-ci.main", "jenkins-war", core, "war", get(base + ".war"))
        put(root, "org.jenkins-ci.main", "jenkins-war", core, "pom", get(base + ".pom"))
    put(root, "io.jenkins.uctest", "uc-test-nopom", "1.0", "hpi", synthetic_hpi("uc-test-nopom", args.jenkins_version))
    put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "hpi", synthetic_hpi("uc-test-stubpom", args.jenkins_version))
    put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "pom", STUB_POM)
    (args.out / "seeded.json").write_text(json.dumps({"plugins": seeded, "cores": cores}, indent=2))
    print(f"seeded {len(seeded)} plugins and cores {', '.join(cores)} into {root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("versions")
    build = sub.add_parser("repo")
    build.add_argument("--downloads", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--repository", required=True)
    build.add_argument("--cores", required=True)
    build.add_argument("--jenkins-version", required=True)
    args = parser.parse_args()
    versions() if args.command == "versions" else repo(args)


if __name__ == "__main__":
    main()
```

`tests/integration/netutil.py`:

```python
"""HTTP helpers for integration tests."""
from __future__ import annotations

import http.client
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        return self.headers.get("location")


def fetch(url: str, headers: dict[str, str] | None = None) -> Response:
    """GET without following redirects."""
    parts = urlsplit(url)
    connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parts.hostname, parts.port, timeout=30)
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path or "/", headers={"User-Agent": "uc-integration-test", **(headers or {})})
        response = connection.getresponse()
        return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        connection.close()


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "uc-integration-test"}),
                                timeout=120) as response:
        return response.read()


def wait_for(url: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if fetch(url).status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"{url} did not answer 200 within {timeout}s")
        time.sleep(2)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `make test-unit ARGS="-k fake_artifactory"`
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/integration tests/unit/test_fake_artifactory.py
git commit -m "Add fake Artifactory and seed tooling for integration tests

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: Air-gapped end-to-end test with real Jenkins controllers

**Files:**
- Create: `tests/integration/airgapped.yaml`, `tests/integration/jenkins.py`, `tests/integration/jenkins-init.groovy`, `tests/integration/test_airgapped.py`
- Modify: `Makefile` (add `it-airgapped`, `it-clean`)

**Interfaces:**
- Consumes:
  - both images, and the uc CLI (`python3 -m uc` in the uc-test image)
  - `fake_artifactory.py`, `seed.py`, `netutil`
  - `uc.versions.VersionTool`
- Produces:
  - `jenkins.Jenkins(base_url)` with `wait_ready()`, `script(groovy) -> str` and `version() -> str`
  - `make it-airgapped`, which proves S4 (air-gapped generation succeeds with only warnings) end to end

This test downloads real plugins and three core WARs (about 300 MB) during seeding. It needs internet for the seed steps only; everything after the `pki` step runs on an `internal: true` network.

- [ ] **Step 1: Write the Jenkins client and init script**

`tests/integration/jenkins.py`:

```python
"""Minimal Jenkins script-console client for integration tests (unsecured controller, CSRF crumb + session)."""
from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.parse
import urllib.request


class Jenkins:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def wait_ready(self, timeout: float = 600) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with self.opener.open(self.base + "/login", timeout=10) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.base} did not become ready")
            time.sleep(5)

    def version(self) -> str:
        with self.opener.open(self.base + "/login", timeout=10) as response:
            return response.headers["X-Jenkins"]

    def script(self, groovy: str, timeout: float = 900) -> str:
        with self.opener.open(self.base + "/crumbIssuer/api/json", timeout=30) as response:
            crumb = json.loads(response.read())
        request = urllib.request.Request(
            self.base + "/scriptText",
            data=urllib.parse.urlencode({"script": groovy}).encode(),
            headers={crumb["crumbRequestField"]: crumb["crumb"]},
        )
        with self.opener.open(request, timeout=timeout) as response:
            return response.read().decode()
```

`tests/integration/jenkins-init.groovy`:

```groovy
// Test controllers only: no authentication, so the script console is reachable with a CSRF crumb.
import hudson.security.AuthorizationStrategy
import hudson.security.SecurityRealm
import jenkins.model.Jenkins

def jenkins = Jenkins.get()
jenkins.setSecurityRealm(SecurityRealm.NO_AUTHENTICATION)
jenkins.setAuthorizationStrategy(AuthorizationStrategy.UNSECURED)
jenkins.save()
```

- [ ] **Step 2: Write the compose file**

`tests/integration/airgapped.yaml`:

```yaml
# Air-gapped integration test (spec §13). Run with `make it-airgapped`.
name: uc-it-airgapped

networks:
  airgap:
    internal: true
  internet: {}

x-uc-test: &uc-test
  image: ${UC_TEST_IMAGE:-jenkins-uc/uc-test:local}
  user: "0:0"

x-jenkins: &jenkins
  networks: [airgap]
  environment:
    JAVA_OPTS: "-Djenkins.install.runSetupWizard=false"
  volumes:
    - trust:/var/jenkins_home/update-center-rootCAs:ro
    - ./jenkins-init.groovy:/var/jenkins_home/init.groovy.d/uc-test.groovy:ro
  healthcheck:
    test: ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://localhost:8080/login"]
    interval: 5s
    timeout: 5s
    retries: 60
    start_period: 20s

services:
  # ---- seeding: profile "seed", internet access ----
  seed-versions:
    <<: *uc-test
    profiles: ["seed"]
    networks: [internet]
    entrypoint: ["python3", "/tests/integration/seed.py", "versions"]
    volumes:
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  seed-plugins:
    image: jenkins/jenkins:${LTS_OLD:-lts}
    profiles: ["seed"]
    user: "0:0"
    networks: [internet]
    entrypoint: ["jenkins-plugin-cli", "--plugins", "${SEED_PLUGINS:-timestamper}", "--plugin-download-directory", "/seed/downloads"]
    volumes:
      - seed:/seed

  seed-repo:
    <<: *uc-test
    profiles: ["seed"]
    networks: [internet]
    entrypoint: ["python3", "/tests/integration/seed.py", "repo", "--downloads", "/seed/downloads",
                 "--out", "/seed/repo", "--repository", "jenkins",
                 "--cores", "${LTS_OLD:-x},${LTS_NEW:-x},${WEEKLY:-x}", "--jenkins-version", "${LTS_OLD:-x}"]
    volumes:
      - seed:/seed
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  pki:
    image: ${UC_IMAGE:-jenkins-uc/uc:local}
    profiles: ["seed"]
    user: "0:0"
    network_mode: none
    entrypoint:
      - sh
      - -c
      - >-
        uc pki init --out /pki && cp /pki/root-ca.crt /trust/ &&
        printf uc > /pki/artifactory_username && printf uc-secret > /pki/artifactory_password &&
        printf uc:uc-secret > /pki/download_proxy_auth && chmod -R a+rX /pki /trust
    volumes:
      - pki:/pki
      - trust:/trust

  # ---- the air-gapped side: no route to the internet ----
  artifactory:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifactory.py", "--root", "/seed/repo", "--port", "8081",
                 "--credentials", "uc:uc-secret", "--anonymous-read"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  artifactory-private:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifactory.py", "--root", "/seed/repo", "--port", "8081",
                 "--credentials", "uc:uc-secret"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  web:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    networks: [airgap]
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web:8080"
      ARTIFACTORY_URL: "http://artifactory:8081/artifactory/"
      ARTIFACTORY_REPOSITORY: jenkins
    volumes:
      - uc-data:/data:ro

  web-proxy:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    networks: [airgap]
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web-proxy:8080"
      DOWNLOAD_ACCESS: proxy
      DOWNLOAD_BACKEND_URL: "http://artifactory-private:8081/artifactory/jenkins"
      DOWNLOAD_PROXY_AUTH_FILE: /pki/download_proxy_auth
    volumes:
      - uc-data-proxy:/data:ro
      - pki:/pki:ro

  jenkins-lts-old:
    <<: *jenkins
    image: jenkins/jenkins:${LTS_OLD:-lts}

  jenkins-lts-new:
    <<: *jenkins
    image: jenkins/jenkins:${LTS_NEW:-lts}

  jenkins-weekly:
    <<: *jenkins
    image: jenkins/jenkins:${WEEKLY:-latest}

  driver:
    <<: *uc-test
    networks: [airgap]
    extra_hosts:
      - "api.github.com:127.0.0.1"
      - "github.com:127.0.0.1"
      - "codeload.github.com:127.0.0.1"
      - "raw.githubusercontent.com:127.0.0.1"
      - "reports.jenkins.io:127.0.0.1"
      - "updates.jenkins.io:127.0.0.1"
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web:8080"
      ARTIFACTORY_URL: "http://artifactory:8081/artifactory/"
      ARTIFACTORY_REPOSITORY: jenkins
      SECRETS_DIR: /pki
      UC_DATA_DIR: /data
      LTS_OLD: ${LTS_OLD:-x}
      LTS_NEW: ${LTS_NEW:-x}
      WEEKLY: ${WEEKLY:-x}
      SEED_TOP_PLUGIN: ${SEED_TOP_PLUGIN:-timestamper}
    entrypoint: ["python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-x", "/tests/integration/test_airgapped.py"]
    depends_on:
      artifactory: {condition: service_started}
      artifactory-private: {condition: service_started}
      web: {condition: service_started}
      web-proxy: {condition: service_started}
      jenkins-lts-old: {condition: service_healthy}
      jenkins-lts-new: {condition: service_healthy}
      jenkins-weekly: {condition: service_healthy}
    volumes:
      - uc-data:/data
      - uc-data-proxy:/data-proxy
      - pki:/pki:ro
      - seed:/seed:ro
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  plugin-cli:
    image: jenkins/jenkins:${LTS_NEW:-lts}
    networks: [airgap]
    user: "0:0"
    environment:
      JENKINS_UC: "http://web:8080"
      JENKINS_UC_EXPERIMENTAL: "http://web:8080/experimental"
      JENKINS_PLUGIN_INFO: "http://web:8080/current/plugin-versions.json"
    entrypoint: ["jenkins-plugin-cli", "--verbose", "--plugins", "${SEED_TOP_PLUGIN:-timestamper}",
                 "--plugin-download-directory", "/tmp/plugins"]

volumes:
  seed:
  pki:
  trust:
  uc-data:
  uc-data-proxy:
```

Add to the `Makefile` (and to `.PHONY`):

```make
IT_DIR = tests/integration
IT_AIR = docker compose -f $(IT_DIR)/airgapped.yaml --env-file $(IT_DIR)/.versions.env

it-airgapped: build
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed run --rm -T seed-versions > $(IT_DIR)/.versions.env
	cat $(IT_DIR)/.versions.env
	$(IT_AIR) --profile seed run --rm seed-plugins
	$(IT_AIR) --profile seed run --rm seed-repo
	$(IT_AIR) --profile seed run --rm pki
	$(IT_AIR) run --rm driver $(ARGS); rc=$$?; \
	  if [ $$rc -eq 0 ]; then $(IT_AIR) run --rm plugin-cli; rc=$$?; fi; \
	  $(IT_AIR) --profile seed down -v --remove-orphans >/dev/null 2>&1; exit $$rc

it-clean:
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
	docker compose -f $(IT_DIR)/online.yaml down -v --remove-orphans
```

- [ ] **Step 3: Write the end-to-end test**

`tests/integration/test_airgapped.py`:

```python
"""Air-gapped end to end (spec §13): doctor, generate, serve, and real Jenkins controllers installing plugins."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jenkins import Jenkins  # noqa: E402
from netutil import fetch, get, wait_for  # noqa: E402

from uc.versions import VersionTool  # noqa: E402

WEB = "http://web:8080"
WEB_PROXY = "http://web-proxy:8080"
CONTROLLERS = {
    "jenkins-lts-old": os.environ["LTS_OLD"],
    "jenkins-lts-new": os.environ["LTS_NEW"],
    "jenkins-weekly": os.environ["WEEKLY"],
}
TOP_PLUGIN = os.environ["SEED_TOP_PLUGIN"]
SEEDED = json.loads(Path("/seed/repo/seeded.json").read_text())
VERSIONS = VersionTool(Path("/opt/uc/generator"), Path("/opt/uc/tools"))

CONFIGURE = """
import hudson.model.UpdateSite
import jenkins.model.Jenkins
def uc = Jenkins.get().updateCenter
uc.sites.replaceBy([new UpdateSite("default", "%(site)s/update-center.json")])
uc.save()
def site = uc.getSite("default")
def result = site.updateDirectlyNow()
println("UPDATE=" + result.kind)
println("MESSAGE=" + (result.message ?: ""))
println("CORE=" + site.data?.core?.version)
println("PLUGINS=" + (site.data?.plugins?.size() ?: 0))
"""

INSTALL = """
import jenkins.model.Jenkins
def futures = Jenkins.get().pluginManager.install(["%(plugin)s"], true)
println("INSTALL=" + futures.collect { it.get().status.getClass().simpleName }.join(","))
"""


def uc(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "uc", *args], env={**os.environ, **(env or {})},
                          capture_output=True, text=True)


def parse(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def test_doctor_reports_the_seeded_defects():
    result = uc("doctor", "--fetch", "--json")
    assert result.returncode == 1, result.stderr  # something is dropped, by design
    report = json.loads(result.stdout)
    assert report["hpi_without_pom"] == ["io/jenkins/uctest/uc-test-nopom/1.0/uc-test-nopom-1.0.hpi"]
    assert "uc-test-stubpom:1.0" in report["stub_poms"]
    assert report["missing_dependencies"] == []
    assert sorted(report["cores"]) == sorted(set(SEEDED["cores"]))


@pytest.fixture(scope="module")
def release() -> dict:
    result = uc("generate")
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB + "/uctest.json")
    return json.loads(Path("/data/status.json").read_text())


def test_generation_succeeds_offline_with_only_warnings(release):
    assert release["last_run"]["result"] == "success"
    assert release["mode"] == "airgapped"
    assert any("GitHub" in warning for warning in release["warnings"])
    assert release["doctor"]["hpi_without_pom"] == 1
    plugins = json.loads(get(WEB + "/current/update-center.actual.json"))["plugins"]
    assert "uc-test-stubpom" in plugins  # a stub POM degrades metadata but is still published
    assert "uc-test-nopom" not in plugins  # no POM: dropped by the generator, as uc doctor predicted
    assert TOP_PLUGIN in plugins
    assert release["plugin_count"] == release["doctor"]["plugins"] == len(plugins)


def test_signed_metadata_points_at_the_update_center(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    urls = [doc["core"]["url"]] + [p["url"] for p in doc["plugins"].values()]
    assert all(url.startswith(WEB + "/download/") for url in urls)
    assert doc["connectionCheckUrl"] == WEB + "/uctest.json"


def test_downloads_redirect_into_artifactory(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    plugin = doc["plugins"][TOP_PLUGIN]
    response = fetch(plugin["url"])
    assert response.status == 302
    assert response.location.startswith("http://artifactory:8081/artifactory/jenkins/")
    assert len(get(response.location)) > 0


@pytest.mark.parametrize("controller", sorted(CONTROLLERS))
def test_routing_offers_only_compatible_plugins(release, controller):
    version = CONTROLLERS[controller]
    response = fetch(f"{WEB}/update-center.actual.json?version={version}")
    assert response.status in (301, 302)
    assert response.location.startswith(WEB + "/")
    doc = json.loads(get(response.location))
    required = [p["requiredCore"] for p in doc["plugins"].values()]
    assert all(c <= 0 for c in VERSIONS.compare_many([(r, version) for r in required]))


@pytest.mark.parametrize("controller", ["jenkins-lts-old", "jenkins-lts-new"])
def test_controller_trusts_the_signature_and_installs(release, controller):
    version = CONTROLLERS[controller]
    jenkins = Jenkins(f"http://{controller}:8080")
    jenkins.wait_ready()
    assert jenkins.version() == version
    configured = parse(jenkins.script(CONFIGURE % {"site": WEB}))
    assert configured["UPDATE"] == "OK", configured.get("MESSAGE")
    tier = json.loads(get(fetch(f"{WEB}/update-center.actual.json?version={version}").location))
    assert int(configured["PLUGINS"]) == len(tier["plugins"])  # Jenkins loaded the tier for its version
    installed = parse(jenkins.script(INSTALL % {"plugin": TOP_PLUGIN}))["INSTALL"].split(",")
    assert installed and all(status.startswith("Success") for status in installed), installed


def test_proxy_mode_installs_without_anonymous_artifactory_access(release):
    result = uc("generate", env={"PUBLIC_URL": WEB_PROXY, "UC_DATA_DIR": "/data-proxy"})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    wait_for(WEB_PROXY + "/uctest.json")
    jenkins = Jenkins("http://jenkins-weekly:8080")
    jenkins.wait_ready()
    configured = parse(jenkins.script(CONFIGURE % {"site": WEB_PROXY}))
    assert configured["UPDATE"] == "OK", configured.get("MESSAGE")
    installed = parse(jenkins.script(INSTALL % {"plugin": TOP_PLUGIN}))["INSTALL"].split(",")
    assert installed and all(status.startswith("Success") for status in installed), installed


def test_rollback_restores_the_previous_release(release):
    first = release["live_release"]
    result = uc("generate", "--force")
    assert result.returncode == 0, result.stderr[-4000:]
    second = json.loads(Path("/data/status.json").read_text())["live_release"]
    assert second != first
    assert uc("rollback").returncode == 0
    assert json.loads(Path("/data/status.json").read_text())["live_release"] == first
    assert os.readlink("/data/live") == f"releases/{first}"
```

- [ ] **Step 4: Run the air-gapped integration test**

Run: `make it-airgapped`
Expected:
- the seed steps print the chosen versions and the seeded count;
- the driver reports `11 passed`, with the Jenkins tests taking several minutes;
- `jenkins-plugin-cli` exits 0 after downloading the top plugin and its dependencies through `http://web:8080`;
- the project is torn down.

If `jenkins-plugin-cli` rejects the metadata, rerun `$(IT_AIR) run --rm plugin-cli` with `--verbose` output captured and compare the URLs it requests with the routing rules.

- [ ] **Step 5: Commit**

```bash
git add tests/integration Makefile
git commit -m "Add air-gapped end-to-end test with real Jenkins controllers

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Online integration, full-catalog measurement and upstream upgrade tooling

**Files:**
- Create: `tests/integration/online.yaml`, `tests/integration/test_online.py`, `docs/upgrading.md`
- Modify: `Makefile` (add `it-online`, `it-full`, `upstream-diff`)

**Interfaces:**
- Consumes: both images, `netutil`, and the uc CLI
- Produces:
  - `make it-online`, which needs `ARTIFACTORY_USERNAME` and `ARTIFACTORY_PASSWORD` for a Jenkins community account, plus optional `GITHUB_USERNAME` and `GITHUB_TOKEN`
  - `make it-full`, which writes measurements to `tests/integration/.results/online-<timestamp>.json`
  - `make upstream-diff TO=<version>`

- [ ] **Step 1: Write the compose file and the test**

`tests/integration/online.yaml`:

```yaml
# Online integration test (spec §13). Run with `make it-online` (capped) or `make it-full`.
name: uc-it-online

services:
  pki:
    image: ${UC_IMAGE:-jenkins-uc/uc:local}
    user: "0:0"
    network_mode: none
    environment:
      ARTIFACTORY_USERNAME: ${ARTIFACTORY_USERNAME:?set ARTIFACTORY_USERNAME (a Jenkins community account)}
      ARTIFACTORY_PASSWORD: ${ARTIFACTORY_PASSWORD:?set ARTIFACTORY_PASSWORD}
      GITHUB_USERNAME: ${GITHUB_USERNAME:-}
      GITHUB_TOKEN: ${GITHUB_TOKEN:-}
    entrypoint:
      - sh
      - -c
      - >-
        uc pki init --out /pki &&
        printf %s "$$ARTIFACTORY_USERNAME" > /pki/artifactory_username &&
        printf %s "$$ARTIFACTORY_PASSWORD" > /pki/artifactory_password &&
        printf %s "$$GITHUB_USERNAME" > /pki/github_username &&
        printf %s "$$GITHUB_TOKEN" > /pki/github_token &&
        chmod -R a+rX /pki
    volumes:
      - pki:/pki

  web:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    environment:
      MODE: online
      PUBLIC_URL: "http://web:8080"
    volumes:
      - uc-data:/data:ro

  driver:
    image: ${UC_TEST_IMAGE:-jenkins-uc/uc-test:local}
    user: "0:0"
    environment:
      MODE: online
      PUBLIC_URL: "http://web:8080"
      SECRETS_DIR: /pki
      UC_DATA_DIR: /data
      UC_EXTRA_ARGS: ${UC_EXTRA_ARGS---max-plugins 25}
      JAVA_TOOL_OPTIONS: ${JAVA_TOOL_OPTIONS:--Xmx2g}
    entrypoint: ["python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-x", "-s", "/tests/integration/test_online.py"]
    depends_on:
      web: {condition: service_started}
    volumes:
      - uc-data:/data
      - pki:/pki:ro
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro
      - ./.results:/results

volumes:
  pki:
  uc-data:
```

`tests/integration/test_online.py`:

```python
"""Online end to end (spec §13): generate from repo.jenkins-ci.org, serve, redirect to get.jenkins.io."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netutil import fetch, get, wait_for  # noqa: E402

from uc.timeutil import iso, utcnow  # noqa: E402

WEB = "http://web:8080"


def tree_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


@pytest.fixture(scope="module")
def release() -> dict:
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "uc", "generate"])
    duration = round(time.monotonic() - started)
    assert result.returncode == 0
    wait_for(WEB + "/uctest.json")
    status = json.loads(Path("/data/status.json").read_text())
    measurements = {
        "finished_at": iso(utcnow()),
        "duration_seconds": duration,
        "uc_extra_args": os.environ.get("UC_EXTRA_ARGS", ""),
        "java_tool_options": os.environ.get("JAVA_TOOL_OPTIONS", ""),
        "plugin_count": status["plugin_count"],
        "tiers": status["tiers"],
        "cache_bytes": tree_size(Path("/data/cache")),
        "release_bytes": tree_size(Path("/data/live").resolve()),
    }
    out = Path("/results") / f"online-{measurements['finished_at'].replace(':', '')}.json"
    out.write_text(json.dumps(measurements, indent=2) + "\n")
    print(json.dumps(measurements, indent=2))
    return status


def test_published_from_the_public_repository(release):
    assert release["last_run"]["result"] == "success"
    assert release["download_backend"] == "mirror"
    assert release["resources"]["source"] == "github"
    assert len(release["resources"]["ref"]) == 40
    if "--max-plugins" in os.environ.get("UC_EXTRA_ARGS", ""):
        assert 1 <= release["plugin_count"] <= 25


def test_metadata_points_at_the_update_center(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    assert all(p["url"].startswith(WEB + "/download/plugins/") for p in doc["plugins"].values())
    assert doc["core"]["url"].startswith(WEB + "/download/war/")


def test_downloads_redirect_to_get_jenkins_io(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    plugin_id, plugin = sorted(doc["plugins"].items())[0]
    response = fetch(plugin["url"])
    assert response.status == 302
    assert response.location == f"https://get.jenkins.io/plugins/{plugin_id}/{plugin['version']}/{plugin_id}.hpi"
    assert fetch(response.location).status in (200, 302)


def test_popularity_is_included_online(release):
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    assert any(p.get("popularity", 0) > 0 for p in doc["plugins"].values())


def test_tool_installers_are_mirrored(release):
    body = get(WEB + "/updates/hudson.tasks.Maven.MavenInstaller.json")
    assert body.lstrip(b"\xef\xbb\xbf").startswith(b"downloadService.post(")


def test_version_routing_uses_generated_tiers(release):
    stable = release["tiers"]["stable"]
    version = stable[-1] if stable else "2.999.1"
    response = fetch(f"{WEB}/update-center.json?version={version}")
    expected = f"{WEB}/dynamic-stable-{version}/update-center.json" if stable else f"{WEB}/current/update-center.json"
    assert response.location == expected


def test_github_labels_when_a_token_is_given(release):
    if release["warnings"] and any("GitHub" in w for w in release["warnings"]):
        pytest.skip("no GitHub token provided (spec S7 needs one)")
    doc = json.loads(get(WEB + "/current/update-center.actual.json"))
    assert any(p.get("labels") for p in doc["plugins"].values())
```

- [ ] **Step 2: Add the Makefile targets and the upgrade guide**

Add to the `Makefile` (and to `.PHONY`):

```make
IT_ONLINE = docker compose -f $(IT_DIR)/online.yaml

it-online: build
	@test -n "$$ARTIFACTORY_USERNAME" -a -n "$$ARTIFACTORY_PASSWORD" || \
	  { echo "export ARTIFACTORY_USERNAME and ARTIFACTORY_PASSWORD (a Jenkins community account)"; exit 2; }
	@mkdir -p $(IT_DIR)/.results
	$(IT_ONLINE) run --rm pki
	$(IT_ONLINE) run --rm driver $(ARGS); rc=$$?; \
	  $(IT_ONLINE) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Full catalog, no plugin cap: records duration, cache and site size (spec S6). Takes hours on first run.
it-full:
	$(MAKE) it-online UC_EXTRA_ARGS= JAVA_TOOL_OPTIONS=-Xmx6g

UC2_VERSION ?= 3.18.4
UPSTREAM_FILES = site/generate.sh site/generate-htaccess.sh site/test/test.sh \
  src/main/java/io/jenkins/update_center/Main.java \
  src/main/java/io/jenkins/update_center/DirectoryTreeBuilder.java \
  src/main/java/io/jenkins/update_center/MetadataWriter.java \
  src/main/java/io/jenkins/update_center/Signer.java \
  src/main/java/io/jenkins/update_center/Popularities.java \
  src/main/java/io/jenkins/update_center/ArtifactoryRepositoryImpl.java \
  src/main/java/io/jenkins/update_center/MavenArtifact.java

# Usage: make upstream-diff TO=3.19   (compares the pinned UC2_VERSION with TO; see docs/upgrading.md)
upstream-diff: build-uc
	@test -n "$(TO)" || { echo "usage: make upstream-diff TO=<update-center2 version>"; exit 2; }
	docker run --rm --entrypoint sh -v "$(CURDIR)/uc/patches:/patches:ro" $(UC_TEST_IMAGE) -c '\
	  set -e; git clone -q https://github.com/jenkins-infra/update-center2.git /tmp/uc2; cd /tmp/uc2; \
	  echo "== files changed between $(UC2_VERSION) and $(TO)"; \
	  git diff --stat update-center2-$(UC2_VERSION) update-center2-$(TO); \
	  echo "== files uc depends on"; \
	  git diff update-center2-$(UC2_VERSION) update-center2-$(TO) -- $(UPSTREAM_FILES); \
	  git checkout -q update-center2-$(TO); \
	  if git apply --check /patches/*.patch 2>/dev/null; then echo "== patch applies to $(TO)"; \
	  else echo "== PATCH DOES NOT APPLY to $(TO); check whether Popularities.java already reads POPULARITY_JSON_URL"; fi'
```

`docs/upgrading.md`:

````markdown
# Upgrading update-center2

The jar is pinned in `uc/Dockerfile` (`UC2_VERSION`, `UC2_COMMIT`). Security releases change
generated HTML and JSON, so upgrade promptly. Resource data (warnings, suspensions) is refreshed on
every online run regardless.

1. See what changed in the files this project depends on:

   ```bash
   make upstream-diff TO=3.19
   ```

   Review each diff against the spec:

   | Upstream change | What to check |
   |---|---|
   | `site/generate.sh` | argument lines in `uc/uc/args.py` (§6 step 6) and the layout in `uc/uc/layout.py` (§6 step 8) |
   | `site/generate-htaccess.sh` | `uc/uc/htaccess.py`: the five download rules and the partial-tier strings must still match exactly (the run fails loudly if not) |
   | `site/test/test.sh` | port new expectations into `tests/routing/test_upstream_rules.py` |
   | `Main.java`, `DirectoryTreeBuilder.java`, `MetadataWriter.java`, `Signer.java` | removed or renamed options used in `args.py` (`--connection-check-url` is the one upstream's own script does not use) |
   | `MavenArtifact.java` | `DOWNLOADS_ROOT_URL` still controls download URLs |
   | `ArtifactoryRepositoryImpl.java` | AQL query (`uc/uc/aql.py`), cache layout (`uc/uc/doctor.py`), the Authenticator port check (§10) |
   | `Popularities.java` | whether the patch still applies, or is no longer needed |

2. Update `uc/Dockerfile`: set `UC2_VERSION`, and set `UC2_COMMIT` to `git rev-parse update-center2-<version>^{commit}`.
   If upstream has merged the popularity change, delete the patch and switch the `jar-build` stage
   to downloading `update-center2-<version>-bin.zip` from `repo.jenkins-ci.org`, pinned by SHA-256.
3. Update the verified-facts list in the implementation plan, and the version in the README and spec header.
4. Run `make test`, `make it-airgapped`, then `make it-online` (needs community credentials).
5. Deploy: `docker compose build` is not used (images are built by `make build`), so
   `make build && docker compose up -d`. The next scheduled run uses the new generator; run
   `docker compose run --rm uc generate` to publish immediately.
````

- [ ] **Step 3: Run the online integration test**

This step needs a Jenkins community account. Run:

```bash
export ARTIFACTORY_USERNAME=... ARTIFACTORY_PASSWORD=...
make it-online
```

Expected: `7 passed`, or `6 passed, 1 skipped` without a GitHub token. If the first test fails with `AQL query ... failed with HTTP 403`, community accounts cannot run AQL. That answers spike S1 negatively: record it in `docs/spikes/README.md` (Task 21) and stop, because online mode then needs a private Artifactory.

If you have no credentials, mark this step blocked, record it in `docs/spikes/README.md` under S1, and continue.

- [ ] **Step 4: Check the upgrade tooling**

Run: `make upstream-diff TO=3.18.4`
Expected: an empty diff and `== patch applies to 3.18.4`.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/online.yaml tests/integration/test_online.py docs/upgrading.md Makefile
git commit -m "Add online integration test, full-catalog measurement and upgrade tooling

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: Operator spike checklist (S1, S2, S6, S7)

These spikes need real credentials and a real internal Artifactory, so they are an **operator checkpoint**. The task produces the procedure and a results log. The operator runs it; any "no" answer amends the spec (§15) before production use.

**Files:**
- Create: `docs/spikes/README.md`

- [ ] **Step 1: Write the checklist**

`docs/spikes/README.md`:

````markdown
# Spikes needing an operator (spec §15)

S3 was replaced by the fake Artifactory. S4 is covered by `make it-airgapped`. S5 was resolved
during planning. The rest need real systems.

| # | Question | Procedure | Result (date, who, outcome) |
|---|---|---|---|
| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export ARTIFACTORY_USERNAME=... ARTIFACTORY_PASSWORD=...; make it-online`. The first test fails with `HTTP 403` if not. | |
| S1b | Can a **non-admin** service account run it on the internal Artifactory? | With `.env` from `.env.airgapped.example` and the service account in `secrets/`: `docker compose run --rm uc doctor --fetch`. An AQL error names the HTTP status. | |
| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `ARTIFACTORY_REPOSITORY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | |
| S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
| S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=... GITHUB_TOKEN=...` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | |

## If an answer is "no"

- **S1a no:** online mode needs a private Artifactory remote repository in front of repo.jenkins-ci.org.
  Test whether AQL on a remote repository lists more than its cache (S2-style) before recommending it.
- **S1b no:** document the Artifactory permission the service account needs, then repeat S1b.
- **S2 no:** keep the documented contract (a single local repository).
- **S6:** set the `JAVA_TOOL_OPTIONS` guidance and volume sizing in docs/operations.md from the numbers.
- **S7 no:** document the token type that works, or accept that GitHub labels are omitted.
````

- [ ] **Step 2: Commit**

```bash
git add docs/spikes/README.md
git commit -m "Add operator spike checklist

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Hand over to the operator**

Stop here and ask the operator to run S1a, S1b, S2, S6 and S7 and fill in the table. Nothing else in the plan depends on the results, but production use does.

---

## Spec coverage

| Spec section | Implemented by |
|---|---|
| §1 outputs and modes | Tasks 7, 9, 13 (outputs); Tasks 2 and 17 (modes) |
| §2 upstream facts | Global Constraints; Tasks 1, 7, 8 |
| §3 corrections | Task 1 (prototype removed, `.gitignore`); every later task implements the corrected behaviour |
| §4 architecture | Tasks 1, 11, 15, 17 |
| §5 CLI | Tasks 13, 14 |
| §6 pipeline | step 1 preflight and lock: Tasks 4, 11 · step 2 resources: Task 5 · steps 3, 4, 6, 7 jar environment, tiers, arguments, run: Task 7 · step 5 `.htaccess`: Task 6 · step 8 layout and download map: Tasks 9, 8 · step 9 validation: Task 10 · step 10 publish: Task 11 · all joined up in Task 13 |
| §7.1-7.3 modes, air-gapped behaviour | Tasks 2, 13, 17 (`compose.airgapped.yaml`), 19 |
| §7.4 patch | Task 1 |
| §8 web tier | Tasks 15, 16 |
| §9 PKI, secrets, Jenkins setup | Tasks 3, 4, 17 (docs), 19 (proved with real controllers) |
| §10 ingest contract, `uc doctor` | Tasks 2 (explicit port), 12, 17 (docs), 19 |
| §11 configuration | Task 2 |
| §12 repository layout | all tasks; File Structure above |
| §13 testing | Tasks 1-14 (unit), 15-16 (web and routing), 18-20 (integration) |
| §14 upgrades | Task 20 |
| §15 spikes | Tasks 16 (S5), 19 (S4), 21 (S1, S2, S6, S7) |
| §16 later sub-projects | out of scope for this plan |
