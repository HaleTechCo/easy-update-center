# Repository adapter: design

Date: 2026-09-22. Extends [2026-09-22-jenkins-update-center-design.md](2026-09-22-jenkins-update-center-design.md)
(the "main spec"), which remains the authority for everything this document does not change. §10 lists
the main-spec sections this design amends.

## 1. Intent

**What the operator said.**

- One environment holds plugins only in Artifact Keeper. Another holds them in Artifactory now and will
  add Artifact Keeper. The update center has to work against either.
- Procurement is manual. The operator's repositories hold `.hpi` files with no `.pom`, and Maven layout
  is unfamiliar ground: one POM "download" was a saved HTTP 404 page, and one plugin was stored under
  the wrong group.
- Configuration keys should be generic rather than product-named.

**Agreed assumptions.**

- Each update center deployment reads **one** repository, of either type. Nothing is merged.
- The update center works with repositories as procurement actually leaves them, true to the main
  spec's goal of plugging into anyone's environment.
- Upstream update-center2 stays unmodified apart from the existing popularity patch.

**Done means:** against an Artifactory or an Artifact Keeper repository that holds only `.hpi`/`.war`
files in Maven layout, `uc generate` publishes every Maven-built plugin with its display name,
description, links and **release date** (as upstream does), and `uc doctor` names everything it could
not include, with the fix.

## 2. Verified facts

Upstream is update-center2 3.18.4 (`b2c8ad2b`); paths below are under `src/main/java/io/jenkins/update_center/`.

- **Three requests, nothing else.** `ArtifactoryRepositoryImpl.java`: the AQL listing (`:57`, `:164-174`,
  sent with preemptive basic auth), files at `<url><repo>/<path>` (`:55`, `:338`), and archive entries
  at `<url><repo>/<path>!/<entry>` (`:54`, `:327`), of which it reads `META-INF/MANIFEST.MF` of each
  `.hpi` (`:53`, `:248`) and `index.jelly` of each plugin's `.jar`.
- **It cannot authenticate file reads.** Only the listing carries credentials (preemptive basic auth).
  Files and archive entries are requested without them, and a `401` challenge crashes the run:
  `AuthenticatorImpl` (`:343-367`) calls `getRequestingSite().getHostName()`, and Java's `HttpClient`
  always passes a null requesting site. Anything upstream reads files from must therefore answer
  unauthenticated reads. repo.jenkins-ci.org does; a private repository may not.
- **Pairing happens in the listing.** A `.hpi`/`.jpi`/`.war` without a `.pom` entry of the same
  coordinates in the AQL result is discarded (`:179-183`). Serving a POM on request is not enough.
- **Descriptions** come from the POM's `/project/description`, replaced by the sanitised text of
  `<artifactId>-<version>.jar!/index.jelly` when that exists (`HPI.java:183-196`). The main spec's §2
  says `index.jelly` is read from the `.hpi`; that is wrong.
- **Timestamps.** An artifact's timestamp is the listing's `modified` (`:228`). It feeds
  `releaseTimestamp`, `buildDate`, recent releases and release history, and tiers are generated only
  for cores whose timestamp is within 400 days (`json/TieredUpdateSitesGenerator.java:78-80,154`).

Artifact Keeper is 1.10.0 (tag `v1.10.0`, `75e8cd5`); paths below are under `backend/src/`.

- **No AQL and no Artifactory-compatible API.** The only AQL code is its client for migrating out of
  Artifactory (`services/artifactory_client.rs`). No server route serves `/api/search/aql` or
  `/api/storage`.
- **Maven** at `/maven/<key>` and `/maven/<key>/<path>`, GET and PUT (`api/handlers/maven.rs:228-230`,
  mounted at `api/routes.rs:70`). It accepts basic auth `username:token` and `Bearer <token>`.
- **Listing** at `GET /api/v1/repositories/<key>/artifacts` (`api/handlers/repositories.rs`), at most
  100 items per page (`per_page` is clamped), with keyset `cursor` paging. Each item carries the full
  `path`, `size_bytes`, `checksum_sha256` and `created_at`; there is no sha1 and no modification time.
  The `/api/v1` routes accept `Bearer <token>` and reject basic auth with 401.
- **Checksum side-files** `<file>.sha1`, `.sha256` and `.md5` are served under `/maven/`.
- **No HTTP Range support.** A `Range` request returns 200 and the whole file, so reading one entry of an
  archive means downloading the archive.

The operator's instance behaved this way (2026-09-22).

**Embedded files** (sample of 29 plugins across release years 2012 to 2026, plus core 2.583; one more
was skipped for size):

- **POM:** `META-INF/maven/<groupId>/<artifactId>/pom.xml` is byte-identical to the published `.pom` in
  28 of 29 plugins and in the core WAR. The exception, `synopsys-polaris 1.4.0`, was built with Gradle
  and has no `META-INF/maven/`.
- **`index.jelly`:** identical to the published `.jar`'s in every plugin that has one. It lives in
  `WEB-INF/lib/<artifactId>.jar` (Maven), `WEB-INF/lib/<artifactId>-<version>.jar` (Gradle) or
  `WEB-INF/classes/` (plugins from before about 2015).
- **Group:** the manifest's `Group-Id` matched the published group in every plugin.

**Archive dates** (a second sample, of 30 plugins plus core 2.583, reading only each archive's zip
central directory):

- The `META-INF/MANIFEST.MF` entry's time was within 8 hours of the catalogue's real
  `releaseTimestamp` in **30 of 31**, and within minutes in many. The offsets are whole hours because zip
  (DOS) times carry no time zone, so an archive built at 14:00 in UTC−5 records 14:00. The worst case
  is ±14 hours.
- Reproducible builds, where every entry has the same time, stamped the commit time (6 of 6 within an
  hour), except `cas-plugin 1.9.0`, stamped **1980-02-01**: a placeholder, not a date.
- repo.jenkins-ci.org answers `Range` requests (206, after a redirect to its object store), so an
  Artifactory's central directory can be read without downloading the archive: 2 to 3 requests each,
  even for the 51 MB WAR.

## 3. Architecture

```
  uc generate ─┐                             ┌─▶ Artifactory      (listing, files, archive entries: native)
  generator  ──┼──▶  repo-adapter  ──────────┤
  uc doctor  ──┘     (new service)           └─▶ Artifact Keeper  (v1 listing, /maven files)
                          │
                   /data/repo-adapter/   (token, cache of small derived files, report.json)

  Jenkins downloads ──▶ web tier ──(redirect or proxy)──▶ repository      ← unchanged
```

- `repo-adapter` is a new Compose service running `uc repo-adapter` from the existing `uc` image
  (Python 3.14, standard library only, `http.server.ThreadingHTTPServer`). It listens on port 8081 of
  a new `internal: true` network, `repo`, joined only by `uc`, `scheduler` and itself. It reaches the
  repository through a network of its own, `repo-egress`, and shares no network with `web`, which
  `make compose-check` asserts. In air-gapped mode,
  `compose.airgapped.yaml` gives it the same `extra_hosts` fast-fail entries as `uc` and `scheduler`
  (main spec §7.3).
- It is the only code that knows repository types. The generator and `uc`'s own AQL client
  (`uc/uc/aql.py`) read the repository through it, and the generator stays unmodified.
- Downloads never pass through it. Controllers still fetch binaries from the repository through the
  web tier's redirect or proxy.
- **When it is used:** `REPO_ADAPTER` defaults to `on` in air-gapped mode and `off` online, and is
  always `on` for `REPO_TYPE=artifact-keeper`. With it off, the generator talks to `REPO_URL` directly,
  exactly as before. That is the online default because the public repo.jenkins-ci.org is complete and
  its full listing is too large to route through the adapter for no benefit.
- Compose starts `repo-adapter` before `uc` and `scheduler` (`depends_on` with a healthcheck on
  `GET /healthz`, which needs no authentication and returns 200), and `docker compose run --rm uc …`
  starts it too.

## 4. Behaviour

### 4.1 Requests it answers

The adapter speaks Artifactory's dialect under `http://repo-adapter:8081/artifactory/`. The explicit
port matters: main spec §10 item 4.

| Request | `REPO_TYPE=artifactory` | `REPO_TYPE=artifact-keeper` |
|---|---|---|
| `POST /artifactory/api/search/aql` | forwarded verbatim to `REPO_API_URL`, then gap-filled | pages through `/api/v1/repositories/<REPO_KEY>/artifacts`, returns `.hpi`/`.jpi`/`.war`/`.pom` (and records `.jar` entries in the index for §4.2 rule 6), then gap-filled |
| `GET /artifactory/<REPO_KEY>/<path>`, POMs only | forwarded | `GET /maven/<REPO_KEY>/<path>` |
| `GET /artifactory/<REPO_KEY>/<path>!/<entry>` | forwarded (Artifactory extracts) | served from the cache (§5.1) |
| `GET /healthz` | 200 | 200 |

Anything else returns 404, including whole archives: upstream fetches whole files only for POMs
(`resolve()`, for a plugin's POM and its parent), so the adapter never buffers an archive for a client.
The AQL endpoint accepts only the exact query upstream and `uc` send (`uc/uc/aql.py`'s `AQL_TEMPLATE`
for `REPO_KEY`); any other query gets 400.

**The index.** Each listing leaves an in-memory index (the rows, derived POMs, manifests and
descriptions) that GETs are resolved against. A GET that arrives before any listing, as after an
adapter restart, builds one first. Otherwise a path the index does not know is a 404: upstream asks
for a parent POM for nearly every plugin, and most are absent, so rebuilding on a miss would mean a
full listing per plugin.

**Artifact Keeper listing fields**, mapped into Artifactory's result shape:

- `path`: split into directory and file name.
- `created` and `modified`: the archive's release date (§4.2 rule 9), or `created_at` as its fallback,
  formatted as `%Y-%m-%dT%H:%M:%S.%fZ`, truncated to milliseconds.
- `sha256`: from `checksum_sha256`.
- `actual_sha1`: from the `.sha1` side-file, cached by sha256. Accepted only if it is 40 hex digits.
- `size`: from `size_bytes`.

**Artifactory timestamps** are replaced the same way (§4.2 rule 9), falling back to the listing's own
`created`/`modified`.

### 4.2 Filling gaps

The repository's own file always wins over a derived one. Derivation happens while the listing is
built, so the listing never advertises a file the adapter cannot serve.

1. **Coordinates** of each `.hpi`/`.jpi` come from its manifest (`Group-Id`, `Artifact-Id`,
   `Plugin-Version`). A `.war`, which has no `Group-Id`, uses its path, as does any archive whose
   manifest lacks one of those three attributes. Such an archive is never "misplaced".
2. **Misplaced archive.** When the manifest's coordinates disagree with the path, the archive is left
   out of the listing (dropped) and reported with its correct path. Nothing is derived for it, and a
   `.pom` beside it is ignored. Publishing it would advertise wrong coordinates; the operator's fix is
   to move the file.
3. **Usable POM.** A `.pom` in the repository is usable when it is well-formed XML with DOCTYPE and
   entity declarations refused (the existing guard in `uc/uc/doctor.py`), has a `<project>` root, and
   declares the same groupId (its own, or its `<parent>`'s), artifactId and version as its path. A
   `.pom` that fails this is left out of the listing and reported as replaced. The HTML 404 page is
   caught here.
4. **Derived POM.** An archive with no usable `.pom` gets one derived from its embedded
   `META-INF/maven/<groupId>/<artifactId>/pom.xml`, if that is itself usable by rule 3. The listing
   gains a `.pom` entry beside the archive: same directory, name `<artifactId>-<version>.pom`, the
   archive's timestamps, and the derived bytes' size, sha256 and sha1. A GET for that path serves those
   bytes.
5. **No POM at all** (for example, Gradle builds): nothing is added, the generator drops the archive
   as it does today, and it is reported under "needs a real `.pom`" with its repo.jenkins-ci.org URL.
6. **Descriptions.** `GET …/<artifactId>-<version>.jar!/index.jelly` is served from the repository's
   `.jar` when there is one. For Artifactory the request is forwarded, and only a 404 falls through;
   for Artifact Keeper the index says whether the `.jar` exists, and the adapter extracts from it.
   Otherwise the adapter serves `index.jelly` from the `.hpi` of the same coordinates, trying
   `WEB-INF/lib/<artifactId>.jar`, then `WEB-INF/lib/<artifactId>-<version>.jar`, then
   `WEB-INF/classes/index.jelly`. When none exists it returns 404, and the generator falls back to the
   POM's description.
7. **Archive entries** served: `META-INF/MANIFEST.MF` of any `.hpi`/`.jpi`/`.war`, and `index.jelly` of
   a plugin `.jar` path. Any other entry returns 404 and is logged.
8. **Parent POMs** (`org/jenkins-ci/plugins/plugin/<v>/plugin-<v>.pom` and others) are forwarded when
   the repository has them, and return 404 otherwise. They are not derivable, and the main spec already
   makes them optional.
9. **Release dates, as upstream uses them.** In an operator's repository the listing's times are upload
   times. Upstream reads them as release dates (§2), so the adapter replaces them with each archive's
   own date: the `META-INF/MANIFEST.MF` entry's time in the zip central directory. It takes the UTC
   extended timestamp (`0x5455`) when present, and otherwise the DOS time read as UTC.
   - **Where it applies:** the archive's `created` and `modified`, and those of its `.pom` entry, real or
     derived. That covers plugins and cores alike, so it drives release dates, recent releases, release
     history and the tier age gate (§12).
   - **Plausibility check:** the date is used only if it is on or after 2004-01-01 and no more than one
     day after the repository's time. The day covers the time-zone error; a build cannot follow its
     upload. Otherwise, or when the date cannot be read, the repository's time is used and the archive
     is reported under "dates from the repository".
   - **Reading it:** from Artifactory, with `Range` requests for the central directory. A server that
     ignores `Range` falls back to the repository's time, rather than downloading whole archives. From
     Artifact Keeper, it is read from the archive the adapter already downloads.

## 5. Caching, limits, security

### 5.1 Cache

- **Layout.** `/data/repo-adapter/cache/<sha256[:2]>/<sha256>/` holds what is known about that
  content. For an archive: `MANIFEST.MF`, `coordinates`, `pom.xml`, `index.jelly` and `released`, each
  replaced by an empty marker (`MANIFEST.none`, `coordinates.none`, `pom.none`, `index.none`,
  `released.none`) when absent. `released`, written last, marks the entry complete; it holds rule 9's
  archive date before the plausibility check, which depends on the repository's time and so runs on
  every listing. For any file whose listing has no sha1: `sha1`. For a repository `.pom`:
  `pom-verdict` (empty when usable, else the reason). For a repository `.jar`: `jar-index.jelly` or
  `jar-index.none`. Content addressed by sha256 never changes, so entries never go stale.
- **Writes** go to a temporary file followed by `os.replace`, so concurrent requests are safe.
- **Artifact Keeper.** An archive whose sha256 is not cached is streamed to a temporary file under
  `/data/repo-adapter/tmp/`, its entries are extracted in one pass, and the temporary file is deleted.
  Each archive is downloaded once over the life of the cache.
- **Artifactory.** Entries are read with `!/` requests. The inner jar comes from
  `<hpi>!/WEB-INF/lib/<jar>`, and the adapter extracts `index.jelly` from it.
- **Pruning.** Entries are touched when used, and at the end of each listing, entries untouched for 30
  days are removed.

### 5.2 Limits

Archives are treated as untrusted input.

- The archive download cap is `REPO_MAX_ARCHIVE_MB`, default 512. An oversized archive is skipped
  (dropped) and reported.
- Entry caps: 1 MiB for a POM, manifest or `index.jelly`, and 64 MiB for an inner jar. Reads are
  bounded at the cap plus one byte whatever the declared size, so a zip bomb or a lying header cannot
  exhaust memory or disk.
- A zip central directory (rule 9) is read only up to 16 MiB. A larger one falls back to the
  repository's time.
- Only named entries are read. Nothing is extracted to a path taken from an archive.
- Every backend request has a timeout: 60 s, or 600 s for archive downloads and the AQL forward.
- Every fetched file must return HTTP 200 and pass its own check (XML rules for POMs, a zip central
  directory for archives, 40 hex digits for sha1), so an error page is never cached as content.

### 5.3 Security

- **Credentials.** The adapter reads the repository credentials, `repo_username` and `repo_password`,
  via the existing `*_FILE` convention. For Artifact Keeper the password is the API token, sent as
  `Bearer` to `/api/v1` and as basic auth to `/maven`. With the adapter on, `uc` never reads them, and
  `compose.airgapped.yaml` (where the adapter is on by default) does not mount them into `uc` or the
  scheduler at all.
- **The listing needs a token:** a random 32-byte token that the adapter creates on first start at
  `/data/repo-adapter/token`, mode 0600, owned by the `uc` user (uid 10001). `uc` passes it to the jar
  as `ARTIFACTORY_PASSWORD` with username `uc`, and the jar sends it preemptively with the listing. A
  listing without it gets `401` with `WWW-Authenticate: Basic`. The web container runs as uid 33 and
  cannot read the token.
- **File reads need none**, because upstream cannot answer a challenge (§2). They serve only POMs,
  manifests and descriptions, never archives, and only on the internal `repo` network, which the
  internet-facing web container is not attached to.
- **Redirects** from the repository to another host (Artifactory's object storage), or from https to
  http, are followed without the `Authorization` header.
- **TLS** to the repository is verified against the system CA bundle, plus `REPO_CA_FILE` when set, so
  a redirect to object storage with a public certificate still verifies.
- **Logging.** Credentials are never logged. URLs are logged without userinfo.
- **Trust is unchanged.** The adapter never adds content from outside the repository. Every derived
  file comes from inside an archive the operator vetted, and signing still happens afterwards with the
  operator's key.

## 6. Configuration

These generic keys replace the product-named ones throughout. Nothing has been released, so there are
no compatibility aliases.

| Setting | Online default | Air-gapped default | Replaces |
|---|---|---|---|
| `REPO_TYPE` | `artifactory` | `artifactory` | new |
| `REPO_URL` | `https://repo.jenkins-ci.org/` | **required** | `ARTIFACTORY_URL` |
| `REPO_KEY` | `releases` | **required** | `ARTIFACTORY_REPOSITORY` |
| `REPO_API_URL` | `${REPO_URL}api/` (Artifactory only) | same | `ARTIFACTORY_API_URL` |
| `REPO_ADAPTER` | `off` | `on` | new; forced `on` for `artifact-keeper` |
| `REPO_CA_FILE` | system bundle | system bundle | new |
| `REPO_MAX_ARCHIVE_MB` | `512` | `512` | new |
| `REPO_ADAPTER_URL`, `REPO_ADAPTER_DIR` | `http://repo-adapter:8081/artifactory/`, `/data/repo-adapter` | same | new: where uc finds the adapter and its state (tests and non-default layouts) |
| `CORE_AGE_DAYS` | `400` | `0` (no age limit) | new (§12) |
| secrets `repo_username`, `repo_password` | Jenkins community account | service account | `artifactory_username`, `artifactory_password` |

- `REPO_TYPE=artifact-keeper` with `REPO_ADAPTER=off` is a configuration error: the preflight fails and
  the web entrypoint exits 64.
- `DOWNLOAD_BACKEND=artifactory` keeps its name and now means "a Maven-layout repository".
  `DOWNLOAD_BACKEND_URL` defaults to `${REPO_URL}${REPO_KEY}` for Artifactory and
  `${REPO_URL%/}/maven/${REPO_KEY}` for Artifact Keeper. `uc/uc/config.py` and `web/entrypoint.sh` both
  compute it and must agree, as today.
- **Environment passed to the jar.** With the adapter on: `ARTIFACTORY_URL=http://repo-adapter:8081/artifactory/`,
  `ARTIFACTORY_API_URL=http://repo-adapter:8081/artifactory/api/`, `ARTIFACTORY_REPOSITORY=${REPO_KEY}`,
  `ARTIFACTORY_USERNAME=uc`, `ARTIFACTORY_PASSWORD=<token>`. With it off: `REPO_URL` (explicit port
  added), `REPO_API_URL`, `REPO_KEY` and the repository credentials, as today.
- `make it-online` reads `JENKINS_COMMUNITY_USERNAME` and `JENKINS_COMMUNITY_PASSWORD` instead of the
  `ARTIFACTORY_*` pair. They are test-only and always a community account for repo.jenkins-ci.org; a
  deployment reads `secrets/repo_*`. Existing local `.env` files need those two keys renamed.

## 7. Reporting

After each listing, the adapter writes `/data/repo-adapter/report.json` atomically, mode 0600:

```json
{
  "generated_at": "2026-09-22T23:40:00Z",
  "repo": {"type": "artifact-keeper", "key": "jenkins-prod-local"},
  "archives": 3,
  "derived_poms": ["io.jenkins.plugins:caffeine-api:3.2.4-208.v7e2da_a_7db_82b_"],
  "replaced_poms": [{"path": "org/…/active-directory-2.967.vf4e254546a_b_3.pom", "reason": "not XML"}],
  "needs_pom": [{"gav": "…", "url": "https://repo.jenkins-ci.org/releases/…/….pom"}],
  "misplaced": [{"path": "org/jenkins-ci/plugins/caffeine-api/…", "correct_path": "io/jenkins/plugins/caffeine-api/…"}],
  "skipped": [{"path": "…", "reason": "archive larger than REPO_MAX_ARCHIVE_MB"}],
  "dates_from_repository": [{"path": "…/cas-plugin-1.9.0.hpi", "reason": "archive date 1980-02-01 is before 2004"}]
}
```

- `uc doctor` reads it after its listing and adds five sections: POMs filled in from archives, **needs
  a real `.pom`** (with URLs), `.pom` files ignored, **misplaced** (with correct paths), and dates taken
  from the repository.
- The exit code stays non-zero when anything is dropped. That now includes misplaced, needs-a-POM and
  skipped archives. Derived and replaced POMs, and repository dates, are informational.
- `status.json` is public (main spec §8.4), so it gets **counts only**, under `doctor.repo_adapter`,
  plus one warning line when anything was dropped. Paths and URLs stay in `report.json` and `uc doctor`.
- With `REPO_ADAPTER=off` there is no report. `uc doctor` behaves exactly as today and says the adapter
  is off.

## 8. Errors

- **Repository unreachable,** or the listing fails: the AQL request returns 502, the generator fails,
  `uc generate` fails, and nothing is published (main spec §6).
- **One archive fails** (too large, unreadable, a download error): that archive is dropped and reported.
  The rest of the listing proceeds.
- **The adapter fails to start** (bad configuration, missing secrets): it exits non-zero with the
  preflight's message, and dependent services do not start.

## 9. Testing

- **Unit** (`tests/unit/test_repo_adapter_*.py`):
  - both backends against stub servers (`tests/support/httpstub.py`);
  - every rule in §4.2, with generated fixtures: a Maven `.hpi`, a Gradle-style `.hpi` (versioned
    inner jar, no `META-INF/maven/`), a pre-2015 `.hpi` (`WEB-INF/classes/index.jelly`), an HTML
    "POM", a POM for another artifact, and a misplaced group;
  - rule 9: an extended timestamp, a DOS-only time, the 1980-02-01 placeholder, a date more than a
    day after the upload, an oversized central directory, and an Artifactory stub that ignores
    `Range`, each with the expected date or fallback;
  - the §5.2 limits (oversized archive, oversized entry, a lying size header, DOCTYPE), token
    authentication, the 401 challenge, the query-shape check, and cache reuse and pruning.
- **A fake Artifact Keeper,** `tests/integration/fake_artifact_keeper.py`, beside the fake Artifactory.
  It reproduces §2: cursor paging capped at 100, bearer-only `/api/v1`, basic or bearer `/maven`,
  checksum side-files, and no Range support.
- **`make it-airgapped` runs once per repository type,** with real controllers as today. The seeded
  repository holds `.hpi`/`.war` files with some POMs removed, one HTML "POM", one misplaced plugin and
  one Gradle-style plugin. Assertions:
  - the derived plugins publish and install;
  - the misplaced and Gradle-style plugins are absent and reported;
  - `update-center.json` has a non-empty `excerpt` for plugins seeded without a `.jar`;
  - `plugin-versions.json` gives each seeded plugin its archive's release date, not the time it was
    seeded.
- **Spike S8**, needing an operator: `uc doctor`, then `uc generate`, against the operator's real
  Artifact Keeper (`jenkins-prod-local`). Expected: caffeine-api's POM derived, active-directory's HTML
  POM replaced, and both plugins published.

## 10. Amendments to the main spec and docs

- **§2:** descriptions come from the POM and `<artifactId>-<version>.jar!/index.jelly`. Artifact
  timestamps come from the listing's `modified`, which in an operator's repository is the upload time;
  the adapter substitutes each archive's own date. Timestamps gate tiers at `CORE_AGE_DAYS`.
- **§4, §12:** the `repo-adapter` service, the `repo` network, `uc/uc/repo_adapter/` and the new tests.
- **§7.1, §9.2, §11:** the §6 keys and secrets.
- **§7.3:** the blocked-hosts override also covers `repo-adapter`.
- **§7.1, §7.4, §11:** the `CORE_AGE_DAYS` setting and the second patch (§12).
- **§10:** with the adapter, the repository needs `.hpi`/`.jpi`/`.war` files in Maven layout under the
  group named in each manifest. A real `.pom` is needed only for archives without an embedded one, and
  `.jar` files are optional.
- **§13, §15:** the §9 tests and spike S8.
- **Docs:** `docs/operations.md` §2, §4, §9 (including how to find a plugin's group) and §11; both
  `.env.*.example` files; `secrets/README.md`; `README.md`; and the embedded-file sample result in
  `docs/spikes/README.md`.

## 11. Known limitations

- **Release dates are accurate to within about a day.** Zip times carry no time zone (±14 hours).
  Archives without a usable date, such as the 1980-02-01 placeholder, fall back to the upload time and
  are reported. With `REPO_ADAPTER=off`, dates are whatever the repository reports: correct for the
  public repo.jenkins-ci.org, upload times for an operator's repository.
- **Parent POMs are not derivable.** Plugins that inherit `<scm>` from a parent POM the repository
  lacks miss their SCM link, as in the main spec.
- **Gradle-built plugins** still need their real `.pom` procured.
- **Artifact Keeper** downloads each archive once, because it has no Range support.

## 12. Configurable core age (independent of the adapter)

**Upstream's rule** (`json/TieredUpdateSitesGenerator.java:96-146`). The candidates are the core
versions that plugins declare as their minimum (`Jenkins-Version`) and whose WAR is in the repository.
They are walked newest first, separately for LTS and weekly lines. Every core inside the
`CORE_AGE_DAYS = 400` window (`:154`) gets a tier, then one older core as the floor, then the walk
stops.

- **The setting.** `CORE_AGE_DAYS` is an integer ≥ 0, where `0` means no age limit: every declared core
  whose WAR is in the repository gets a tier.
  - **Online default `400`,** upstream's value. With `0`, the public repository's full history would
    produce a tier for every baseline ever declared.
  - **Air-gapped default `0`.** The operator chose which WARs to store, air-gapped installations often
    trail the current LTS line by more than 400 days, and the number of tiers cannot exceed the number
    of WARs stored. Set `400` to match updates.jenkins.io exactly.
- **A second upstream patch,** `uc/patches/0002-core-age-configurable.patch`, in
  `json/TieredUpdateSitesGenerator.java`, adding the import of
  `io.jenkins.update_center.util.Environment`:

  ```java
  -    private static final int CORE_AGE_DAYS = 400;
  +    private static final int CORE_AGE_DAYS = Environment.getInteger("CORE_AGE_DAYS", 400);
  ```

  `isReleaseRecentEnough` returns `true` when `CORE_AGE_DAYS == 0`. The Dockerfile already applies every
  `uc/patches/*.patch`. It is submitted upstream as a PR, like the popularity patch, and main spec §7.4
  applies once a release contains both.
- **Passing it on.** `uc` validates the setting (a negative or non-integer value is a configuration
  error) and passes it in the jar's environment. The image's version label becomes `<version>+patches`,
  and `make upstream-diff` also watches `TieredUpdateSitesGenerator.java`.
- **Testing.**
  - The unit tests cover validation and the per-mode defaults.
  - `make it-airgapped` seeds two real LTS cores, both released more than 400 days ago, that plugins
    declare as their minimum. Their dates come from the WARs themselves (§4.2 rule 9). It asserts that
    the default `0` gives both a tier, and that a run with `CORE_AGE_DAYS=400` drops the older one:
    upstream keeps at most one core older than 400 days per line, as the floor, and which one depends on
    the other cores present.

## 13. Out of scope

- Writing, moving or fixing files in the operator's repositories.
- A helper that lays downloaded `.hpi` files out in Maven layout.
- Other repository types (Nexus and others), and merging several repositories.
