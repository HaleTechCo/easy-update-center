# Self-Hosted Jenkins Update Center — Design

- **Date:** 2026-09-22
- **Status:** Draft for review
- **Scope:** Sub-project 1 of 3 (core stack). Helm chart and add-ons are outlined in §16.
- **Upstream pinned:** `jenkins-infra/update-center2` tag `update-center2-3.18.4`

## 1. Goal

Run the same Jenkins update center that `updates.jenkins.io` runs, self-hosted in containers, so
Jenkins controllers and `jenkins-plugin-cli` can point at it unchanged. The generated site includes:

- version-aware routing: `/update-center.json?version=2.555.3` redirects to the right tiered site
  (`dynamic-<weekly>/`, `dynamic-stable-<lts>/`, or `current/`)
- every upstream JSON output: `update-center.json` (JSONP), `update-center.actual.json`,
  `plugin-versions.json`, `release-history.json`, `plugin-documentation-urls.json`, `tiers.json`,
  `latestCore.txt`, `pluginCount.txt`, `timestamp.txt`, `experimental/`, `stable/`, `latest/`,
  `download/` index pages, `uctest.json`, and (online) the `updates/` tool-installer metadata
- JSON signed with the operator's own PKI, trusted by controllers via `update-center-rootCAs/`

Two modes:

| | `online` | `airgapped` |
|---|---|---|
| Generator reads plugins from | `repo.jenkins-ci.org` (or any Artifactory) | the internal Artifactory |
| `/download/...` redirects to | `get.jenkins.io` | the internal Artifactory |
| External enrichment (GitHub, reports.jenkins.io, popularity) | used | skipped — the run degrades instead of failing |

In air-gapped mode the stack does everything it can and drops what it cannot (§7.3).

### Non-goals (v1)

- Hosting or copying plugin binaries. Binaries stay where they already are (get.jenkins.io, or
  the internal Artifactory). The update center serves metadata and redirects, as upstream does.
- Reading a plugin store that is not Artifactory (Nexus, a plain directory, S3).
- Kubernetes packaging (sub-project 2), export/import between networks, procurement tooling,
  internal-only plugin sites, caching or HA (§16).

## 2. How the real update center works (verified against upstream source)

Everything below was checked against `update-center2` at `update-center2-3.18.4` and master
(`ebc2b77`, 2026-09-16), and against `jenkinsci/plugin-installation-manager-tool` master.

- **updates.jenkins.io only serves metadata.** Its `.htaccess` redirects `/download/**.hpi|.war`
  to `get.jenkins.io` (`site/generate-htaccess.sh:132-137`). Jenkins downloads each binary from the
  `url` written in the signed JSON.
- **The generator reads only small pieces of each artifact** when it builds metadata. It lists
  artifacts with an Artifactory AQL query (`ArtifactoryRepositoryImpl.java:56,166`), then fetches
  each `.hpi`'s `META-INF/MANIFEST.MF` and each plugin `.jar`'s `index.jelly` (its description)
  through Artifactory's archive-entry URLs, plus the `.pom` and optionally the parent POM. Only the
  listing carries credentials; a `401` on a file read crashes the generator, so a repository it reads
  directly must allow anonymous reads (repo-adapter spec §2, which removes that requirement).
  Each artifact's timestamp is the listing's `modified`, an upload time in an operator's repository. It downloads whole files only when
  `--downloads-directory` is passed (`DirectoryTreeBuilder.java:151`). Upstream passes that flag
  to feed its mirror network (`site/publish.sh` rsyncs new releases to get.jenkins.io); this
  design does not.
- **Download URLs are absolute and set by the `DOWNLOADS_ROOT_URL` env var**, default
  `https://updates.jenkins.io/download` (`MavenArtifact.java:48`, `HPI.java:85`,
  `JenkinsWar.java:43`).
- **The only plugin source is Artifactory, and it needs credentials.** `ARTIFACTORY_USERNAME` and
  `ARTIFACTORY_PASSWORD` are mandatory; the generator throws `IllegalStateException` without them
  (`DefaultMavenRepositoryBuilder.java:16-24`). Anonymous AQL against `repo.jenkins-ci.org`
  returns **HTTP 403** (tested 2026-09-21). URL, API URL, repository name and cache directory are
  env-configurable.
- **A plugin or core release is ignored unless its `.pom` sits beside it**
  (`ArtifactoryRepositoryImpl.java:179-183`).
- **External enrichment sources and what happens when they fail:**

  | Source | URL configurable? | When unreachable |
  |---|---|---|
  | Artifactory (AQL, files, archive entries) | yes | fatal |
  | Popularity (`raw.githubusercontent.com/.../latestNumbers.json`) | **no** (`Popularities.java:25`) | **fatal** (IOException propagates) |
  | GitHub GraphQL (labels, repo existence, default branch) | no | warning, but only if `GITHUB_USERNAME`/`GITHUB_PASSWORD` are set; unset → uncaught `IllegalStateException` (`GitHubSource.java:36-45`) |
  | Issue trackers, maintainers (`reports.jenkins.io`) | yes | warning |
  | Health scores (`reports.jenkins.io`) | no | warning |

  None of these HTTP clients set timeouts, so a host that silently drops packets stalls each
  source for the OS TCP connect timeout (about two minutes on Linux defaults).
- **Security warnings and plugin suspensions are data in the git repo, not the jar**
  (`resources/warnings.json`, `resources/artifact-ignores.properties`). Upstream commits to them
  for every advisory, most recently on 2026-09-16, after the 3.18.4 tag.
- **3.18.4 fixes an XSS in generated download index pages (SECURITY-4038).** Upstream's own
  `site/generate.sh` still pins 3.18.3.
- **Tiers** come from the core versions that plugins declare as dependencies. They cover core
  releases from roughly the last 400 days, plus the first older tier as a floor for older
  controllers (`TieredUpdateSitesGenerator.java:154`). `generate-htaccess.sh`
  turns the tier list into `mod_rewrite` rules. The script exits with an error when given no
  tiers, and emits broken targets when only weekly tiers or only LTS tiers exist.
- **`updates/`** (32 tool-installer JSON files: JDK, Maven, Gradle, NodeJS…) is produced by a
  separate upstream project (`jenkins-infra/crawler`) and is already signed by the Jenkins project.
- **`jenkins-plugin-cli`** reads `JENKINS_UC`, `JENKINS_UC_EXPERIMENTAL` and
  `JENKINS_PLUGIN_INFO`. `JENKINS_UC_DOWNLOAD` is deprecated in favour of
  `JENKINS_UC_DOWNLOAD_URL`. With neither set, it builds download URLs from the host of
  `JENKINS_UC` and ignores any path.

## 3. Corrections to `docs/01`–`docs/05`

The ChatGPT transcripts stay in the repo unchanged as a record of that conversation. Their overall
direction (upstream generator, `dynamic-*` tiers, `.htaccess` routing, own signing key) is correct.
These specifics are not:

| # | Doc(s) | Claim | Reality |
|---|---|---|---|
| 1 | 03, 05, current `generator/Dockerfile` | `git clone --branch v3.18.4` | Tags are named `update-center2-3.18.4`, so the clone fails. |
| 2 | all | No credentials needed | Artifactory credentials are mandatory, and anonymous AQL returns 403. GitHub credentials must be set, even as placeholders. |
| 3 | 03 (current `httpd/httpd.conf`), 05 | Minimal `httpd.conf` | Both fail `httpd -t`: `AH00534 No MPM loaded` (03) and `Invalid command 'CustomLog'` (05). `/etc/mime.types` does not exist in `httpd:2.4`. |
| 4 | 05 | Patches files with `python3` | `eclipse-temurin:21-jre` has no python3, and doc 05 never installs it. |
| 5 | 05 | Copies `site/generate.sh` to `/run/update-center` | `$(dirname $0)/..` then resolves to `/run`, so `--resources-dir /run/resources` is missing, `tmp/` paths split, and the root-owned clone is not writable by the `updatecenter` user. |
| 6 | 03, 05 | Artifacts land in the downloads volume | The generator's cache defaults to a directory inside the container, so every run re-downloads everything, and `ln -f` hard links fail across filesystems. (Moot in this design, which never stages binaries.) |
| 7 | 05 | `mv -Tf /data/www.new /data/www` for atomic publish | A volume mountpoint can't be replaced by a symlink, and Apache mounts neither the `releases` nor the `downloads` volume. |
| 8 | 03 | `--download-url` option | Doesn't exist; args4j rejects it. |
| 9 | 03, 05 | Stripping `.htaccess` redirects makes downloads local | URLs in the JSON are absolute to `updates.jenkins.io/download` unless `DOWNLOADS_ROOT_URL` is set. |
| 10 | 05 | Use 3.18.3 to match upstream's script | 3.18.4 fixes SECURITY-4038. Use 3.18.4. |
| 11 | 03, 05 | Clone upstream at a tag | That freezes security warnings and suspensions at build time. Pin the jar; refresh `resources/` every run. |
| 12 | 04, 05 | Run upstream `site/generate.sh` as-is | It pins 3.18.3, hardcodes Jenkins' root CA, fetches jenkins.io templates and `index.html` (failing the run if that fetch fails), wipes its output dir, and enforces a 1.5 MB size check that vetted catalogs fail. |
| 13 | all | — | `--connection-check-url` defaults to `https://www.google.com/`, which Jenkins uses for its "internet connection" check. |
| 14 | 03, 05 | `RECENT_RELEASES_MAX_AGE_HOURS` controls tier retention or regeneration | It only affects `recent-releases.json`, which upstream uses for mirror rsync. |
| 15 | 03, README | `JENKINS_UC_DOWNLOAD` | Deprecated; see §2. |
| 16 | 03, 05 | 10-year self-signed cert used as both signer and root | Can't be rotated without touching every controller. Use an offline root CA plus a short-lived signing cert (§9). |
| 17 | 01–05 | "versioned proxy" | Means the `?version=` tier routing. No request is proxied to `updates.jenkins.io`. |
| 18 | repo | `.gitignore` = `.env` only | `secrets/*.key` is tracked (currently an empty placeholder). Ignore `secrets/*` except its README, and `data/`. |

## 4. Architecture

```
                         ┌───────────── uc-data volume ──────────────┐
 ┌──────────────────┐    │ releases/<id>/   one complete site each    │    ┌──────────────┐
 │ uc  (one-shot)   │───►│ live ──► releases/<id>   (atomic swap)     │◄───│ web (Apache) │◄── Jenkins
 │ generate, doctor │    │ cache/           generator download cache  │    │ read-only    │   controllers,
 │ rollback, pki    │    │ resources/       last-good upstream copy   │    └──────┬───────┘   plugin CLI
 └────────▲─────────┘    │ status.json      last run, live release    │           │ 302 (or proxy)
          │              └────────────────────────────────────────────┘           ▼
 ┌────────┴─────────┐                                                   get.jenkins.io, or the
 │ scheduler        │  runs `uc generate` on GENERATE_SCHEDULE, both modes   internal Artifactory
 └──────────────────┘
```

- **`uc` image:** Eclipse Temurin 21 JRE on Ubuntu, plus python3 (stdlib only), openssl, tini and a
  pinned `supercronic`. It contains:
  - the update-center2 jar, built from the pinned tag with the patch in §7.4 (multi-stage Maven
    build; the tag's commit SHA is pinned and checked; the build fails if the patch doesn't apply)
  - upstream `site/generate-htaccess.sh` and `resources/`, both from the same tag (`resources/` is
    the fallback copy)
  - the `uc` CLI
  - `VersionTool.java`, a small Java helper compiled at image build time against the generator's
    jars (the JRE base image cannot run single-file source programs), so version comparisons use
    `hudson.util.VersionNumber`

  Runs as uid 10001 with a read-only root filesystem; only `/data` and `/tmp` are writable.
- **`web` image:** `httpd:2.4` with our config (§8). Mounts `/data` read-only and never sees the
  signing key.
- **`scheduler`:** the `uc` image running `uc schedule`. Runs in both modes, because air-gapped
  sites regenerate from the internal repository.
- **`repo-adapter`:** the `uc` image running `uc repo-adapter`, on an internal `repo` network with
  `uc` and the scheduler. It answers the generator's Artifactory requests from Artifactory or
  Artifact Keeper, and fills in POMs, descriptions and release dates from the archives. On by
  default in air-gapped mode; see `2026-09-22-repo-adapter-design.md`.
- **One volume:** all releases, the cache and `live` share a filesystem, so each publish is a
  single `rename(2)`.
- **Release ids** are UTC timestamps, `YYYYMMDDTHHMMSSZ`.

## 5. `uc` CLI

| Command | Purpose |
|---|---|
| `uc generate` | The full pipeline (§6). Exit code 0 means published. |
| `uc doctor` | Report what the generator will and won't see in the source repository (§10). |
| `uc rollback [id]` | Point `live` at the previous release, or at `id`. |
| `uc status` | Print `status.json`. |
| `uc schedule` | Run `uc generate` once immediately if no release is live, then run `supercronic` with `GENERATE_SCHEDULE` → `uc generate`. |
| `uc repo-adapter [--port 8081]` | The repo-adapter service (repo-adapter spec §3); idle when `REPO_ADAPTER` is off. |
| `uc pki init \| rotate \| show` | Signing PKI (§9). |

The CLI is Python 3, standard library only. All logic that isn't glue is unit-tested (§13).

## 6. Generation pipeline (`uc generate`)

Every run builds `releases/<id>.staging/`. It only becomes `live` if every validation passes; a
failed run leaves the current site untouched. The last failed staging directory is kept as
`releases/.failed-<id>` for debugging.

1. **Preflight.**
   - Take an exclusive lock (`flock /data/.lock`); a concurrent run exits 75.
   - Signing key and cert present, the key matches the cert, and the cert is valid for at least
     `CERTIFICATE_MINIMUM_VALID_DAYS`.
   - Artifactory credentials present.
   - `PUBLIC_URL` is a bare origin (scheme, host, optional port, no path) and is not
     `localhost`/`127.0.0.1` unless `ALLOW_LOCAL_PUBLIC_URL=true`. Plain `http` logs a warning:
     signatures protect integrity, but TLS is recommended.
2. **Resources.** Obtain upstream `resources/` from `RESOURCES_SOURCE`:
   - `github`: resolve `RESOURCES_REF` (default `master`) to a commit SHA, then download that
     commit's tarball.
   - `url`: download `RESOURCES_URL`, a tarball containing `resources/`, for example a procured
     copy of the upstream archive in an Artifactory generic repository. If `RESOURCES_SHA256` is
     set, verify it.
   - `bundled`: the copy in the image, from the pinned tag.

   On failure, use `/data/resources/last-good/` and record a warning. Then apply the overlay from
   `/config/overlay/`:
   - `*.properties`: overlay lines are appended after upstream lines. `java.util.Properties`
     keeps the last value for a duplicate key, so the overlay wins.
   - `warnings.json`: merged by `id`; overlay entries replace or add.
   - Any other file (e.g. `index-template.html`, `platform-plugins.json`) replaces upstream's
     wholesale. `certificates/` is ignored.

   Record source, commit or SHA-256, and timestamp in `status.json`. On success, save the result
   as `last-good`.
3. **Environment for the jar.**
   - `DOWNLOADS_ROOT_URL=${PUBLIC_URL}/download`
   - `ARTIFACTORY_CACHEDIR=/data/cache/artifactory`
   - `ARTIFACTORY_*` from settings and secrets
   - GitHub credentials from secrets, or the placeholder `uc`/`uc` when absent (a warning;
     GitHub-sourced labels will be missing)
   - `POPULARITY_JSON_URL` (§7.4)
   - `-Dfile.encoding=UTF-8`, `CERTIFICATE_MINIMUM_VALID_DAYS`, `JAVA_TOOL_OPTIONS` passed through
   - `UC_EXTRA_ARGS`, appended to every argument line and the tier run (like upstream's
     `EXTRA_ARGS`); used by tests, e.g. `--max-plugins 20`
4. **Tiers.** `java -jar … --dynamic-tier-list-file <staging>/tiers.json`. Empty weekly and/or
   stable lists are legal.
5. **`.htaccess`.**
   - **Both lists non-empty:** run upstream `generate-htaccess.sh` unmodified on weekly + stable.
   - **Only one list empty:** run upstream, then rewrite its known-broken empty targets
     (`/dynamic-stable-%{REQUEST_URI}?`, `/dynamic-%{REQUEST_URI}?`, `"/dynamic-stable-/$1"`) to
     `/current%{REQUEST_URI}?` / `/current/$1`. Each must be found exactly once.
   - **Both empty:** render `templates/htaccess-static`, which holds upstream's static rules only
     (uctest, top-level → `current/`, `latestCore.txt`, `release-history.json`,
     `plugin-documentation-urls.json`, `plugin-versions.json`, `DirectoryIndex`).

   Then remove upstream's five download-redirect `RewriteRule`s (`generate-htaccess.sh:132-137`).
   Each must be found exactly once, or the run fails; that is how upstream changes get noticed.
   Downloads are handled at server level (§8.2).
6. **Arguments file**, mirroring upstream `site/generate.sh:110-130`. Every line carries
   `--key <key> --certificate <signing cert> --root-certificate <root CA> --connection-check-url
   ${PUBLIC_URL}/uctest.json --id ${UC_ID}`.
   - one line per weekly tier:
     `--limit-plugin-core-dependency V --write-latest-core --write-timestamp --www-dir <s>/dynamic-V`
   - one line per stable tier: the same with `--only-stable-core` and `--www-dir <s>/dynamic-stable-V`
   - experimental:
     `--www-dir <s>/experimental --generate-recent-releases --with-experimental --latest-links-directory <s>/experimental/latest`
   - current:
     `--generate-release-history --generate-recent-releases --generate-plugin-versions --generate-plugin-documentation-urls --write-latest-core --write-timestamp --write-plugin-count --www-dir <s>/current --download-links-directory <s>/download --latest-links-directory <s>/current/latest`

   Deliberately omitted: `--downloads-directory` (never stage binaries) and
   `--index-template-url` (use `resources/index-template.html`; no jenkins.io fetch).
7. **Run** `java … -jar update-center2.jar --resources-dir <merged resources> --arguments-file <args>`.
   The jar checks each signature it produces, including the chain to the root CA.
8. **Layout**, the same as upstream `site/generate.sh:135-166`:
   - `updates → ../updates` links in every site
   - `stable → dynamic-stable-<newest>`, or `→ current` when there are no stable tiers
   - top-level links for `latest`, `latestCore.txt`, `plugin-documentation-urls.json`,
     `release-history.json`, `plugin-versions.json`, `update-center.json`,
     `update-center.actual.json`
   - `uctest.json` (`{}`), `tiers.json`, and our neutral `index.html`
   - `updates/`: if `TOOL_INSTALLERS_URL` is set, download every `*.json` and `*.json.html` its
     index lists, following redirects (upstream answers with 307 to `mirrors.updates.jenkins.io`),
     unmodified (they keep the Jenkins project's signatures). Each `.json` must start with
     `downloadService.post(`. On failure, copy from the previous release with a warning.
   - **Download map**, always written because Apache refuses to start without the map file (it
     is empty unless `DOWNLOAD_BACKEND=artifactory`). For `artifactory`: run the generator's AQL
     query with the same credentials, keep only `.hpi`/`.jpi`/`.war` entries that have a `.pom`,
     and write `<staging>/.htdownload-map` in Apache `RewriteMap txt` format:
     `plugins/<artifactId>/<version> <maven path>` and `war/<version> <maven path>`. When one
     artifactId/version exists under several groupIds, prefer the groupId in
     `current/update-center.actual.json` (`plugins.<id>.gav`) and log a warning.
9. **Validate.** Any failure aborts the run and leaves `live` unchanged.
   - Required files exist in every site; every JSON parses (JSONP wrapper stripped from
     `update-center.json`); a directory exists for every tier in `tiers.json`.
   - Every `url` in every site's `update-center.actual.json` (`core.url`, `plugins.*.url`) and in
     `current/plugin-versions.json` starts with `${PUBLIC_URL}/download/`.
   - Every `signature` block is present and names our signing cert.
   - `.htaccess` contains no absolute `http(s)://` targets.
   - Plugin count ≥ `MIN_PLUGINS` (default 1), and ≥ `MIN_PLUGIN_RATIO` (default 0.9) × the live
     release's count. `--force` skips the ratio check once, for deliberate shrinkage.
   - `artifactory` backend: the map has an entry for every offered plugin version and core,
     across all sites.
10. **Publish.** Rename the staging directory to `releases/<id>`, then
    `ln -s releases/<id> live.tmp && mv -T live.tmp live`. Prune all but the newest
    `RETAIN_RELEASES` (default 5), never the live one. Write `status.json` (§8.4).

The first online run fetches a manifest and POM for every plugin release (tens of thousands of
requests) and caches them. Later runs fetch only what's new (spike S6 measures this).

## 7. Modes and upstream dependencies

### 7.1 Mode defaults

`MODE` only sets defaults. Every setting can be overridden.

| Setting | `online` default | `airgapped` default |
|---|---|---|
| `REPO_TYPE` | `artifactory` | `artifactory` (or `artifact-keeper`) |
| `REPO_URL` | `https://repo.jenkins-ci.org/` | **required** |
| `REPO_API_URL` | `${REPO_URL}api/` (Artifactory) | same |
| `REPO_KEY` | `releases` | **required** |
| `REPO_ADAPTER` | `off` | `on` (always on for Artifact Keeper) |
| `CORE_AGE_DAYS` | `400` | `0` (every stored core a plugin requires gets a tier) |
| `DOWNLOAD_BACKEND` | `mirror` | `artifactory` (any Maven-layout repository) |
| `DOWNLOAD_BACKEND_URL` | `https://get.jenkins.io` | `${REPO_URL}${REPO_KEY}`; Artifact Keeper: `${REPO_URL}maven/${REPO_KEY}` |
| `DOWNLOAD_ACCESS` | `redirect` | `redirect` |
| `RESOURCES_SOURCE` | `github` | `bundled` (set `url` for procured copies) |
| `POPULARITY_JSON_URL` | upstream's URL | empty (skip) |
| `TOOL_INSTALLERS_URL` | `https://updates.jenkins.io/updates/` | empty (skip) |
| Blocked hosts (§7.3, set in `compose.airgapped.yaml`) | none | `api.github.com`, `raw.githubusercontent.com`, `reports.jenkins.io`, `github.com`, `codeload.github.com`, `updates.jenkins.io` |

### 7.2 Online mode

Needs a Jenkins community account (free at accounts.jenkins.io) for the Artifactory credentials,
subject to spike S1. A GitHub token is recommended; without one, GitHub-topic labels are missing. A
private online Artifactory works by overriding the `ARTIFACTORY_*` settings and setting
`DOWNLOAD_BACKEND=artifactory`.

### 7.3 Air-gapped mode — what is kept and dropped

| Output or source | Behaviour |
|---|---|
| Plugin and core catalog, tiers, routing, every JSON, signatures | kept; generated from the internal Artifactory |
| Security warnings, suspensions | kept, from `RESOURCES_SOURCE` (bundled or procured); `status.json` shows their age |
| Popularity | dropped (0), or from a procured copy via `POPULARITY_JSON_URL` |
| GitHub topic labels, repo-existence checks | dropped (warning); labels from `label-definitions.properties` still apply |
| Issue trackers, maintainers, health scores | dropped (warning) |
| `updates/` tool installers | dropped unless `TOOL_INSTALLERS_URL` points at a procured copy; their download links still point at the internet |
| Links to plugins.jenkins.io and the wiki | present in the JSON, dead in the air gap (cosmetic) |

**Fast-fail for blocked hosts.** `compose.airgapped.yaml` adds `extra_hosts` entries mapping every
blocked host to `127.0.0.1` for the `uc`, `scheduler` and `repo-adapter` services. Connections are refused
immediately instead of stalling on a firewall that drops packets, and each source's own error
handling produces a warning. The container's normal resolver still handles every other name,
including the internal Artifactory. (`-Djdk.net.hosts.file` was rejected: it replaces the JVM's
resolver entirely, so every unlisted host, including Artifactory, would fail to resolve.)

### 7.4 The upstream patches

`patches/0001-popularity-configurable-and-optional.patch`, against `Popularities.java`:

- `JSON_URL` becomes `Environment.getString("POPULARITY_JSON_URL", <current default>)`, the same
  pattern the file already uses for five other URLs.
- An empty URL, or any `IOException`/`RuntimeException` while fetching, logs a warning and uses an
  empty map (popularity 0), matching how `HealthScores` behaves.

`patches/0002-core-age-configurable.patch`, against `json/TieredUpdateSitesGenerator.java`:
`CORE_AGE_DAYS` becomes `Environment.getInteger("CORE_AGE_DAYS", 400)`, and `0` means no age limit
(repo-adapter spec §12).

Both are submitted upstream as PRs. When a release contains them, the patches and the `git apply` step
are dropped and the jar can come from the released `-bin.zip` (pinned by SHA-256) instead of a
source build. The pinned clone stays either way: the `-bin` assembly contains only jars, while
`site/generate-htaccess.sh` and `resources/` exist only in the git tree.

## 8. Web tier

### 8.1 Base configuration

- `httpd:2.4`, modules: `mpm_event`, `unixd`, `authz_core`, `authz_host`, `dir`, `mime`
  (`TypesConfig conf/mime.types`), `log_config`, `rewrite`, `alias`, `headers`, `deflate`,
  `filter`, `autoindex`, plus `proxy`, `proxy_http` and `ssl` when those features are enabled.
  `httpd -t` runs during the image build.
- Listens on 8080, non-root. `ServerName ${PUBLIC_URL}` with `UseCanonicalName On`, so the
  absolute-path redirects in upstream's `.htaccess` carry the public scheme, host and port behind
  reverse proxies.
- `DocumentRoot /data/live`. `<Directory /data>`: `Options FollowSymLinks Indexes`,
  `AllowOverride FileInfo Indexes` (exactly what upstream's `.htaccess` needs), `Require all granted`.
- `<Files ".ht*">` denied. `ServerTokens Prod`. `TraceEnable off`.
- The entrypoint turns `DOWNLOAD_BACKEND`, `DOWNLOAD_ACCESS` and `TLS_*` into `-D` defines, so one
  config file covers every combination.
- Apache refuses to start while `DocumentRoot` or the `RewriteMap` file is missing, so the
  entrypoint waits until `/data/live/.htdownload-map` exists (the first published release) before
  starting `httpd`. The image must not contain `/data/live`: Docker would copy that directory into
  the empty volume, and the later rename of a symlink over it would fail.

### 8.2 Download handling (server level, before the site's `.htaccess`)

1. `^/download/war/latest/jenkins\.war$` → 302 `/latest/jenkins.war`, and
   `^/download/plugins/[^/]+/latest/([^/]+)\.hpi$` → 302 `/latest/$1.hpi`. `/latest/` is the
   generated `current/latest/.htaccess`, which redirects to versioned `${PUBLIC_URL}/download/…`
   URLs. This replaces upstream's `updates.jenkins.io/latest` workaround.
2. `^/download/plugins/([^/]+)/([^/]+)/[^/]+\.hpi$` and `^/download/war/([^/]+)/jenkins\.war$`:
   - **`mirror`:** `plugins/$1/$2/$1.hpi`; war to `war-stable/$1/jenkins.war` when `$1` has three
     components, else `war/$1/jenkins.war` (the same split upstream uses).
   - **`artifactory`:** look up `RewriteMap dl txt:/data/live/.htdownload-map`, key
     `plugins/$1/$2` or `war/$1`. A miss returns 404.
3. `DOWNLOAD_ACCESS=redirect`: 302 to `${DOWNLOAD_BACKEND_URL}/<path>`.
   `DOWNLOAD_ACCESS=proxy`: reverse-proxy to the same URL with
   `RequestHeader set Authorization` from a read-only download secret. Streamed, not cached, and
   with a timeout. `SSLProxyEngine` is enabled for https backends. The `web` service gets this one
   secret only in proxy mode. The proxy verifies the backend's certificate against
   `DOWNLOAD_PROXY_CA_FILE`, which must be overridden and mounted when the backend uses a private CA.
4. Index pages under `/download/plugins/<id>/` and `/download/war/` are static files in the release.

### 8.3 Headers

| Path | Header |
|---|---|
| `*.json`, `*.txt`, `.htaccess`-served redirects | `Cache-Control: public, max-age=300` |
| `/download/**` redirects | `Cache-Control: public, max-age=3600` |
| `/status.json` | `Cache-Control: no-store` |

`deflate` compresses JSON, text and HTML.

### 8.4 Health and status

- `/uctest.json` answers from upstream's rules. Jenkins uses it for its connection check; it is
  also the health endpoint.
- `/status.json` is aliased to `/data/status.json`, which is written on every run, including
  failures:

  ```json
  {
    "mode": "airgapped",
    "live_release": "20260922T030000Z",
    "last_run": {"id": "20260922T060000Z", "result": "failed", "error": "plugin count 412 < 0.9 × 520"},
    "generated_at": "2026-09-22T03:04:12Z",
    "update_center2": "3.18.4+popularity-patch",
    "resources": {"source": "url", "ref": "sha256:…", "fetched_at": "…", "age_days": 12, "stale": false},
    "signing_cert": {"subject": "…", "not_after": "…", "days_left": 301},
    "tiers": {"weekly": ["2.540"], "stable": ["2.528.1"]},
    "plugin_count": 520,
    "warnings": ["GitHub unavailable: labels from topics omitted"],
    "doctor": {"hpi_without_pom": 3, "missing_dependencies": 1, "stub_poms": 0}
  }
  ```

  `stale` becomes true when the resources are older than `STALE_AFTER_DAYS`.
- Docker `HEALTHCHECK` (curl is installed in the image): `/uctest.json` returns 200 and `/data/live`
  resolves.
- Access logs go to stdout (combined format). Jenkins' user agent includes its version.

### 8.5 TLS

Plain HTTP on 8080 by default, for use behind an existing proxy or ingress. When `TLS_CERT_FILE` and
`TLS_KEY_FILE` are mounted, it also serves 8443 with mod_ssl using Mozilla's "intermediate"
settings.

## 9. Signing, secrets, Jenkins configuration

### 9.1 PKI (`uc pki`)

- `init` creates:
  - `root-ca.crt` / `root-ca.key`: RSA 4096, 10 years, `CA:TRUE, pathlen:0`,
    `keyCertSign, cRLSign`. Distribute the cert; keep the key offline.
  - `update-center.key` / `update-center.cert`: RSA 4096, `SIGNING_CERT_DAYS` (default 365),
    `digitalSignature`, issued by the root.
- `rotate` issues a new signing cert from the root. Controllers trust the root, so no Jenkins change
  is needed.
- `show` prints subjects, fingerprints and expiry dates.
- **Root rotation** (documented overlap procedure): add the new root to
  `update-center-rootCAs/` on every controller, since Jenkins trusts all files there; switch
  signing to a cert issued by the new root; remove the old root later.
- Jenkins validates the certificate chain, including expiry, when it loads the metadata. The
  generator refuses to sign within `CERTIFICATE_MINIMUM_VALID_DAYS` (default 30) of expiry, and
  `status.json` reports the days left.

### 9.2 Secrets

Compose file-based secrets, mounted under `/run/secrets/`:

| Secret | Used by |
|---|---|
| `update-center.key`, `update-center.cert` | `uc` generate |
| `root-ca.crt` | `uc` generate (not secret, but distributed alongside) |
| `repo_username`, `repo_password` | `repo-adapter`; also `uc` generate and doctor when `REPO_ADAPTER` is off |
| `github_username`, `github_token` | `uc` generate (optional) |
| `download_proxy_auth` | `web` (mounted always; read only when `DOWNLOAD_ACCESS=proxy`) |

`uc` reads them using the `*_FILE` convention and passes them only to the jar's process
environment, never through Compose `environment:`.

### 9.3 Jenkins controllers (documented, and exercised in integration tests)

- Copy `root-ca.crt` to `$JENKINS_HOME/update-center-rootCAs/`.
- Set the update site URL to `${PUBLIC_URL}/update-center.json` with id `default` (matching
  `UC_ID`), through the UI or a Configuration-as-Code or `init.groovy.d` snippet.
- `jenkins-plugin-cli` / the Docker image:
  ```
  JENKINS_UC=${PUBLIC_URL}
  JENKINS_UC_EXPERIMENTAL=${PUBLIC_URL}/experimental
  JENKINS_PLUGIN_INFO=${PUBLIC_URL}/current/plugin-versions.json
  ```
  No download variable is needed.
- **`PUBLIC_URL` must be a bare origin.** Upstream's rules redirect to absolute paths like
  `/current/…`, and the plugin CLI strips paths from `JENKINS_UC`. Use a dedicated hostname.
- `DOWNLOAD_ACCESS=redirect` requires controllers to be able to read `DOWNLOAD_BACKEND_URL` without
  authentication. Otherwise use `proxy`.

## 10. Source ingest contract and `uc doctor`

What the source Artifactory repository must contain:

1. A **single local repository** (`ARTIFACTORY_REPOSITORY`). Whether AQL works on virtual
   repositories is spike S2.
2. **Maven layout:** `<groupPath>/<artifactId>/<version>/<artifactId>-<version>.hpi` (or `.jpi`),
   with the **real `.pom` beside it**. POMs are published on `repo.jenkins-ci.org` at the same
   path. Files downloaded from updates.jenkins.io or get.jenkins.io are `.hpi` only.
3. **Core:** `org/jenkins-ci/main/jenkins-war/<v>/jenkins-war-<v>.war` and `.pom` for each core
   version to offer. Without them the site offers no core and has no tiers.
4. A **service account** that can run AQL against the repository (spike S1).
   The generator sends the AQL query with credentials up front but cannot authenticate file reads:
   `AuthenticatorImpl` throws on the null requesting site Java's `HttpClient` passes (§2; found by
   the repo-adapter's integration test, 2026-09-23). With the repo-adapter on, the air-gapped
   default, this does not matter. With it off, the repository must allow anonymous reads of POMs
   and archive entries. `uc` still passes `REPO_URL` to the jar with an explicit port (`:443` / `:80`
   added when missing), which that authenticator's port check would need. Redirects to
   `DOWNLOAD_BACKEND_URL` still use the URL as configured.
   With the repo-adapter, items 2 and 3 relax: POMs are optional wherever the archive embeds one
   (repo-adapter spec §4.2).
5. Parent POMs are optional. Without them, SCM links for some plugins may be missing (a warning
   from the generator).

`uc doctor` runs the generator's AQL query itself and reports:

- `.hpi`/`.jpi` without a `.pom` (the generator drops these)
- `.war` without a `.pom`
- files outside Maven layout (rejected by the generator's path parser)
- POMs without `<name>`, which are likely Artifactory-generated stubs (display names, descriptions
  and SCM links will be degraded)
- core versions present, and the tiers that would result
- plugins whose non-optional dependencies are absent, or present only in versions lower than
  required (compared with `VersionTool`), using manifests from the generator cache after a
  generation, or `--fetch` to read them directly

It exits non-zero when anything will be dropped. `uc generate` includes a summary in `status.json`.

## 11. Configuration reference

| Setting | Default | Notes |
|---|---|---|
| `MODE` | — | `online` \| `airgapped` (required) |
| `PUBLIC_URL` | — | bare origin (required) |
| `UC_ID` | `default` | must match the controllers' update site id |
| `GENERATE_SCHEDULE` | `0 */6 * * *` | cron, used by the scheduler |
| `RESOURCES_SOURCE`, `RESOURCES_REF`, `RESOURCES_URL`, `RESOURCES_SHA256` | §6, §7.1 | |
| `POPULARITY_JSON_URL`, `TOOL_INSTALLERS_URL` | §7.1 | |
| `REPO_TYPE`, `REPO_URL`, `REPO_API_URL`, `REPO_KEY` | §7.1 | the repository; the jar gets the adapter's URL when it is on |
| `REPO_ADAPTER`, `REPO_ADAPTER_URL`, `REPO_ADAPTER_DIR`, `REPO_CA_FILE`, `REPO_MAX_ARCHIVE_MB` | repo-adapter spec §6 | |
| `CORE_AGE_DAYS` | §7.1 | passed to the jar (patch 0002) |
| `DOWNLOAD_BACKEND`, `DOWNLOAD_BACKEND_URL`, `DOWNLOAD_ACCESS` | §7.1 | |
| `DOWNLOAD_PROXY_CA_FILE`, `DOWNLOAD_PROXY_AUTH_FILE` | system CA bundle, `/run/secrets/download_proxy_auth` | `web`, only with `DOWNLOAD_ACCESS=proxy` |
| `RETAIN_RELEASES` | `5` | |
| `MIN_PLUGINS`, `MIN_PLUGIN_RATIO` | `1`, `0.9` | |
| `CERTIFICATE_MINIMUM_VALID_DAYS` | `30` | also read by the jar |
| `SIGNING_CERT_DAYS` | `365` | `uc pki` |
| `STALE_AFTER_DAYS` | `30` | resources age |
| `ALLOW_LOCAL_PUBLIC_URL` | `false` | tests only |
| `JAVA_TOOL_OPTIONS` | — | heap (`-Xmx`), egress proxy (`-Dhttps.proxyHost` …) |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | — | enables 8443 |

## 12. Repository layout

Replaces the current `generator/`, `httpd/`, `docker-compose.yml`, `README.md` and `data/`.

```
Makefile                 build · test · it-online · it-airgapped · it-full · upstream-diff
compose.yaml             web + scheduler + repo-adapter; uc under profile "tools" for one-shot commands
compose.airgapped.yaml   override: extra_hosts fast-fail entries; repository credentials for the adapter only
.env.online.example
.env.airgapped.example
uc/
  Dockerfile             multi-stage: patched jar build → runtime
  patches/0001-popularity-configurable-and-optional.patch, 0002-core-age-configurable.patch
  uc/                    Python package (cli, preflight, resources, htaccess, args, layout,
                         downloadmap, validate, publish, status, doctor, pki, pom, schedule)
    repo_adapter/        archive, cache, backends, listing, server
  java/VersionTool.java
  templates/             index.html, htaccess-static
web/
  Dockerfile
  conf/httpd.conf, conf/download.conf, conf/tls.conf
  entrypoint.sh
config/
  overlay/README.md      examples of blocklist and warning overrides
tests/
  unit/                  pytest
  routing/               fixture tree + generated .htaccess against the web image
  integration/           compose files, fake Artifactory and Artifact Keeper, seed script, Jenkins controllers
docs/
  01–05 (ChatGPT, unchanged) · operations.md · superpowers/specs/
secrets/README.md        everything else in secrets/ is gitignored
```

`.gitignore`: `.env`, `secrets/*` except `secrets/README.md`, and `data/`.

## 13. Testing

All tests run in containers through `make`, so no local Python or Java is needed.

- **Unit (`make test`, pytest):**
  - `.htaccess` post-processing: the five-rule removal and its exactly-once check; the
    partial-tier target fixes; the zero-tier template
  - overlay merge semantics
  - every validation rule, both passing and failing
  - download-map generation, including groupId conflicts and `.jpi`
  - `PUBLIC_URL` validation
  - status writing
  - doctor classification (fixtures: POM missing, stub POM, non-Maven path, unmet dependency)
  - `VersionTool` against upstream `VersionNumber` cases
- **Routing (`make test`):** build the web image and serve a fixture release whose `.htaccess`
  comes from upstream `generate-htaccess.sh` with the tier list used by upstream
  `site/test/test.sh`.
  - Every expectation from `test.sh` is ported, and a mismatch fails the test (upstream's script
    only prints).
  - Our rules: mirror split between `war/` and `war-stable/`; artifactory map hit and miss; proxy
    mode against a stub backend that checks the auth header; `latest` rewrites; `uctest`;
    `status.json`; headers; weekly-only, stable-only and zero-tier layouts.
  - A `live` symlink swap takes effect **without a restart**, and the `RewriteMap` reloads through
    the swapped symlink.
- **Integration, online (`make it-online`; needs community Artifactory credentials):** generate
  from `repo.jenkins-ci.org`, restricted with `--max-plugins`, then check redirects to
  get.jenkins.io, signatures and tier selection.
- **Integration, air-gapped (`make it-airgapped`):** run on a Docker `internal: true` network.
  A test-only fake Artifactory (`tests/integration/fake_artifactory.py`) serves the three calls
  the generator makes (AQL listing, file download, archive entry) over a Maven-layout directory.
  The real protocol is covered by `it-online` against `repo.jenkins-ci.org` and by the operator
  checks against the internal instance (S1, S2). The seed step, run before the network is cut,
  resolves a small plugin set plus dependencies with `jenkins-plugin-cli`, adds each plugin's real
  `.pom` from `repo.jenkins-ci.org` and three core WARs with POMs, plus one plugin missing its POM
  and one with a stub POM.
  - `uc doctor` reports exactly the seeded defects.
  - `uc generate` publishes, with the expected drop warnings.
  - Jenkins controllers (current LTS, an older LTS, a weekly), configured through `init.groovy.d`
    with our root CA, accept the signature and install a plugin, in both redirect and proxy
    modes. Tier correctness is checked as a property: every plugin in the site a controller is
    routed to has `requiredCore` ≤ that controller's version. `jenkins-plugin-cli` installs the
    same plugin.
  - `uc rollback` restores the previous release.
  - It runs once per repository type, through the repo-adapter, against the same seeded repository
    served by a fake Artifactory and a fake Artifact Keeper (repo-adapter spec §9).
- **Full catalog (`make it-full`, run by hand):** a complete online generation that records
  duration, heap use, cache size and site size.

## 14. Upgrading upstream

1. Bump `UC2_VERSION`, the tag's commit SHA and (once the patch is upstream) the zip's SHA-256.
2. Run `make upstream-diff`, which shows changes to `site/generate.sh`, `site/generate-htaccess.sh`,
   `site/test/test.sh` and `Main.java` options between the old and new tags. Review them against
   §6.5–6.8.
3. Run `make test it-airgapped`, then `it-online`.

Security releases of update-center2 affect generated HTML and JSON, so upgrade promptly. The
resource data (warnings) is already refreshed on every run in online mode.

## 15. Spikes and risks

Each spike ends in a written result that either confirms or amends this spec. The spikes that need
real credentials (S1, S2, S6, S7) use `uc doctor` and `uc generate` themselves, so they run as an
operator checkpoint once those commands exist.

| # | Question | If the answer is no |
|---|---|---|
| S1 | Can a community account (online) and a non-admin service account (internal) run the generator's AQL query? | Online mode requires a private Artifactory mirror; document the required permissions. |
| S2 | Does AQL work on virtual repositories? | Require a single local repository (already the documented contract). |
| S3 | ~~Artifactory OSS for tests~~ Replaced by the test-only fake Artifactory (§13). | — |
| S4 | Do the popularity patch and `extra_hosts` fast-fail produce a clean air-gapped run with only warnings? (Covered by `it-airgapped`.) | Extend the patch or the blocked-host list. |
| S5 | ~~Apache symlink swap~~ **Resolved during planning (2026-09-22):** a `live` swap and the `RewriteMap` behind it are picked up with no restart. Kept as a routing test. | — |
| S6 | Full-catalog first-run duration, heap and cache size. | Tune `JAVA_TOOL_OPTIONS` defaults; document sizing. |
| S7 | Does GitHub GraphQL accept username plus token over basic auth (upstream's method)? | Document the token type needed. |
| S8 | Does the repo-adapter publish from the operator's real Artifact Keeper (repo-adapter spec §9)? | Fix the Artifact Keeper backend against what the real server returns. |

Also verified during planning: the patched 3.18.4 build passes all 45 upstream tests.

Risks:
- `resources/` on master may start using a format the pinned jar doesn't understand. Validation
  catches the failure, and the run falls back to last-good resources.
- `--connection-check-url` is not used by upstream's own `generate.sh`, and the README warns that
  unused options may be dropped. `make upstream-diff` watches `Main.java` options.

## 16. Later sub-projects

2. **Helm chart:** the same images and configuration contract; the scheduler becomes a CronJob;
   the data volume becomes a ReadWriteMany PVC (or generator and web share one pod); Ingress;
   Secrets or ExternalSecrets.
3. **Procurement tool:** from a `plugins.txt`, resolve the dependency closure, download `.hpi` plus
   `.pom` and `jenkins-war` `.war` plus `.pom` from `repo.jenkins-ci.org`, and write a
   Maven-layout bundle or deploy it to Artifactory. This makes the §10 contract easy to meet.

Candidates beyond those: export/import for gaps without an internal Artifactory; an internal-only
plugin update site generated inside the gap; binary caching; HA.
