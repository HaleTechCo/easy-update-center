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
description and links, and `uc doctor` names everything it could not include, with the fix.

## 2. Verified facts

Upstream is update-center2 3.18.4 (`b2c8ad2b`); paths below are under `src/main/java/io/jenkins/update_center/`.

- **Three requests, nothing else.** `ArtifactoryRepositoryImpl.java`: the AQL listing (`:57`, `:164-174`,
  sent with preemptive basic auth), files at `<url><repo>/<path>` (`:55`, `:338`), and archive entries
  at `<url><repo>/<path>!/<entry>` (`:54`, `:327`), of which it reads `META-INF/MANIFEST.MF` of each
  `.hpi` (`:53`, `:248`) and `index.jelly` of each plugin's `.jar`.
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
  a new `internal: true` network, `repo`, joined only by `uc`, `scheduler` and itself. It also joins
  the default network to reach the repository. `web` is not attached to `repo`. In air-gapped mode,
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
| `GET /artifactory/<REPO_KEY>/<path>` | forwarded | `GET /maven/<REPO_KEY>/<path>` |
| `GET /artifactory/<REPO_KEY>/<path>!/<entry>` | forwarded (Artifactory extracts) | served from the cache (§5.1) |
| `GET /healthz` | 200 | 200 |

Anything else returns 404. The AQL endpoint accepts only the query shape upstream and `uc` send (a
`repo` `$eq` plus the four extension matches), and only for `REPO_KEY`; any other query gets 400.

**The index.** Each listing leaves an in-memory index (path to sha256 and size, plus the derived
files) that GETs are resolved against. A GET for a path the index does not know rebuilds the listing
once before answering 404, so an adapter restart between upstream's listing and its GETs is harmless.

**Artifact Keeper listing fields**, mapped into Artifactory's result shape:

- `path`: split into directory and file name.
- `created` and `modified`: both from `created_at`, formatted as `%Y-%m-%dT%H:%M:%S.%fZ`, truncated
  to milliseconds.
- `sha256`: from `checksum_sha256`.
- `actual_sha1`: from the `.sha1` side-file, cached by sha256. Accepted only if it is 40 hex digits.
- `size`: from `size_bytes`.

**Artifactory timestamps** pass through unchanged.

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

## 5. Caching, limits, security

### 5.1 Cache

- **Layout.** `/data/repo-adapter/cache/<sha256[:2]>/<sha256>/`, keyed by the archive's sha256, holds
  `MANIFEST.MF`, `pom.xml` or the marker `pom.none`, `index.jelly` or `index.none`, and `sha1`.
  Verdicts on the repository's own POMs are kept at `/data/repo-adapter/cache/poms/<sha256>`. Content
  addressed by sha256 never changes, so entries never go stale.
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
- Only named entries are read. Nothing is extracted to a path taken from an archive.
- Every backend request has a timeout: 60 s, or 600 s for archive downloads and the AQL forward.
- Every fetched file must return HTTP 200 and pass its own check (XML rules for POMs, a zip central
  directory for archives, 40 hex digits for sha1), so an error page is never cached as content.

### 5.3 Security

- **Credentials.** Only the adapter holds the repository credentials: `repo_username` and
  `repo_password`, via the existing `*_FILE` convention. For Artifact Keeper the password is the API
  token, sent as `Bearer` to `/api/v1` and as basic auth to `/maven`.
- **The generator's credentials** are a random 32-byte token that the adapter creates on first start at
  `/data/repo-adapter/token`, mode 0600, owned by the `uc` user. `uc` passes it to the jar as
  `ARTIFACTORY_PASSWORD` with username `uc`. The web container runs as uid 33 and cannot read it. The
  implementation verifies that the `uc` image's uid differs from 33.
- **Unauthenticated requests** other than `/healthz` get `401` with `WWW-Authenticate: Basic`, which
  upstream needs before it sends credentials for files.
- **TLS** to the repository is verified against the system CA bundle, or `REPO_CA_FILE` when set.
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
- `make it-online` reads `REPO_USERNAME` and `REPO_PASSWORD` instead of the `ARTIFACTORY_*` pair.
  Existing local `.env` files need those two keys renamed.

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
  "skipped": [{"path": "…", "reason": "archive larger than REPO_MAX_ARCHIVE_MB"}]
}
```

- `uc doctor` reads it after its listing and adds four sections: POMs filled in from archives, **needs
  a real `.pom`** (with URLs), `.pom` files ignored, and **misplaced** (with correct paths).
- The exit code stays non-zero when anything is dropped. That now includes misplaced, needs-a-POM and
  skipped archives. Derived and replaced POMs are informational.
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
  - `update-center.json` has a non-empty `excerpt` for plugins seeded without a `.jar`.
- **Spike S8**, needing an operator: `uc doctor`, then `uc generate`, against the operator's real
  Artifact Keeper (`jenkins-prod-local`). Expected: caffeine-api's POM derived, active-directory's HTML
  POM replaced, and both plugins published.

## 10. Amendments to the main spec and docs

- **§2:** descriptions come from the POM and `<artifactId>-<version>.jar!/index.jelly`; artifact
  timestamps come from the listing's `modified` and gate tiers at 400 days.
- **§4, §12:** the `repo-adapter` service, the `repo` network, `uc/uc/repo_adapter/` and the new tests.
- **§7.1, §9.2, §11:** the §6 keys and secrets.
- **§7.3:** the blocked-hosts override also covers `repo-adapter`.
- **§10:** with the adapter, the repository needs `.hpi`/`.jpi`/`.war` files in Maven layout under the
  group named in each manifest. A real `.pom` is needed only for archives without an embedded one, and
  `.jar` files are optional.
- **§13, §15:** the §9 tests and spike S8.
- **Docs:** `docs/operations.md` §2, §4, §9 (including how to find a plugin's group) and §11; both
  `.env.*.example` files; `secrets/README.md`; `README.md`; and the embedded-file sample result in
  `docs/spikes/README.md`.

## 11. Known limitations

- **Timestamps are upload times** in operator repositories, for both types (§2). Plugin release dates
  show as upload dates, and a core gets tiers until 400 days after it was **uploaded**, not released.
  This is the same with or without the adapter.
- **Parent POMs are not derivable.** Plugins that inherit `<scm>` from a parent POM the repository
  lacks miss their SCM link, as in the main spec.
- **Gradle-built plugins** still need their real `.pom` procured.
- **Artifact Keeper** downloads each archive once, because it has no Range support.

## 12. Out of scope

- Writing, moving or fixing files in the operator's repositories.
- A helper that lays downloaded `.hpi` files out in Maven layout.
- Other repository types (Nexus and others), and merging several repositories.
