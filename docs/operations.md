# Operating the Jenkins update center

Design and rationale: `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`, and for
reading Artifact Keeper or filling gaps in a repository, `docs/superpowers/specs/2026-09-22-repo-adapter-design.md`.

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

Air-gapped, set `REPO_TYPE`, `REPO_URL` and `REPO_KEY` to your repository:

| Repository | `REPO_TYPE` | `REPO_URL` | `REPO_KEY` |
|---|---|---|---|
| Artifactory | `artifactory` (default) | `https://artifactory.corp.example/artifactory/` | the repository key |
| Artifact Keeper | `artifact-keeper` | the server's base URL, `https://artifacts.corp.example/` | the repository key (not its display name) |

The **repo-adapter** service reads the repository for the generator. It is on by default in
air-gapped mode and always on for Artifact Keeper (`REPO_ADAPTER`). It fills in what procurement
often leaves out: each plugin's POM, description and release date come from inside its `.hpi`
when the repository has no usable copy. See section 9.

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
printf '%s' 'svc-jenkins-uc' > secrets/repo_username
printf '%s' '…password or API token…' > secrets/repo_password   # Artifact Keeper: an API token
# online, recommended: GitHub user and a token without scopes
printf '%s' 'my-bot' > secrets/github_username
printf '%s' 'ghp_…' > secrets/github_token
# only with DOWNLOAD_ACCESS=proxy: a read-only account for streaming downloads
printf '%s' 'svc-read:…' > secrets/download_proxy_auth
```

Online mode uses a free Jenkins community account (https://accounts.jenkins.io) for
`repo.jenkins-ci.org`. Anonymous AQL queries are refused with HTTP 403. With the repo-adapter on
(the air-gapped default), only the `repo-adapter` container receives these two files; `uc` and the
scheduler authenticate to the adapter with a token it creates in `/data/repo-adapter/token`.

### File ownership on Linux

Compose mounts secret files with their host ownership. The uc containers run as uid 10001 and the web
container as uid 33:

```bash
sudo chown 10001:10001 secrets/update-center.* secrets/root-ca.crt secrets/repo_* secrets/github_*
sudo chmod 0400 secrets/update-center.key secrets/repo_password secrets/github_token
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

### DOWNLOAD_ACCESS=proxy against an HTTPS backend

The web container verifies the backend certificate (`SSLProxyVerify require`) against
`DOWNLOAD_PROXY_CA_FILE`, default `/etc/ssl/certs/ca-certificates.crt`. A backend using a private or
corporate CA needs that bundle mounted and the variable pointed at it, or every `/download/**` returns
502:

```yaml
# compose.proxy-ca.yaml
services:
  web:
    volumes:
      - ./secrets/proxy-ca.crt:/etc/ssl/uc-proxy-ca.crt:ro
    environment:
      DOWNLOAD_PROXY_CA_FILE: /etc/ssl/uc-proxy-ca.crt
```

Then `docker compose -f compose.yaml -f compose.proxy-ca.yaml up -d`. `DOWNLOAD_PROXY_AUTH_FILE`
similarly overrides where the `username:password` is read from (default
`/run/secrets/download_proxy_auth`).

## 8. Everyday operations

| Task | Command |
|---|---|
| Generate now | `docker compose run --rm uc generate` |
| Accept a deliberate drop in plugin count | `docker compose run --rm uc generate --force` |
| Roll back to the previous release | `docker compose run --rm uc rollback` |
| Roll back to a specific release | `docker compose run --rm uc rollback 20260922T030000Z` |
| Logs | `docker compose logs -f scheduler web` |

The last `RETAIN_RELEASES` (5) releases are kept under `/data/releases/`. The last failed attempt is
kept as `/data/releases/.failed-<id>`. `status.json` names only the failure's class under
`last_run.error`; the full message goes to the container log: `docker compose logs scheduler` for
scheduled runs, or the terminal for `docker compose run --rm uc generate`.

## 9. Air-gapped specifics

### What the repository must contain

- One Artifactory repository (local or virtual) or one Artifact Keeper Maven repository.
- Each plugin's `.hpi` (or `.jpi`) in Maven layout, under the group its own manifest names:
  `<group path>/<artifactId>/<version>/<artifactId>-<version>.hpi`. Plugins do not share one group
  (`caffeine-api` is `io.jenkins.plugins`, `git` is `org.jenkins-ci.plugins`). A file downloaded from
  updates.jenkins.io is named `<id>.hpi`; rename it to `<artifactId>-<version>.hpi`. To read the
  coordinates, open the `.hpi` (it is a zip) and look at `META-INF/MANIFEST.MF`:

  ```bash
  unzip -p caffeine-api.hpi META-INF/MANIFEST.MF | grep -E '^(Group-Id|Artifact-Id|Plugin-Version):'
  ```

  ```powershell
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [IO.Compression.ZipFile]::OpenRead("$PWD\caffeine-api.hpi")
  (New-Object IO.StreamReader($zip.GetEntry('META-INF/MANIFEST.MF').Open())).ReadToEnd() -split "`r?`n" |
    Select-String '^(Group-Id|Artifact-Id|Plugin-Version):'
  $zip.Dispose()
  ```

- **POMs are optional** with the repo-adapter: it serves the one embedded in each `.hpi`. Only plugins
  built without Maven (Gradle) have none; `uc doctor` lists them with the repo.jenkins-ci.org URL of
  the real `.pom` to procure. A saved error page is not a POM: the adapter ignores it. `.jar` files
  are optional too.
- Core: `org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war` for every core version
  you want to offer (its POM is optional too). Without them the site offers no core and has no tiers.
  Every stored core that a plugin requires gets a tier (`CORE_AGE_DAYS=0`, the air-gapped default);
  `CORE_AGE_DAYS=400` behaves like updates.jenkins.io, which drops cores released over 400 days ago.
- An account that can read the repository: for Artifactory, one that can run AQL (a non-admin
  account works); for Artifact Keeper, a user name and API token.

A repository behind a private CA needs its bundle in the `repo-adapter` container:

```yaml
# compose.repo-ca.yaml
services:
  repo-adapter:
    volumes:
      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
    environment:
      REPO_CA_FILE: /etc/ssl/uc-repo-ca.crt
```

`docker compose run --rm uc doctor --fetch` checks all of this. With the adapter on it also lists the
POMs it filled in, `.pom` files it ignored and why, **misplaced** plugins with the path to move each
to, and archives whose release date came from the repository instead of the archive.

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
| `Repository credentials missing: provide /run/secrets/repo_username and /run/secrets/repo_password` (preflight), or AQL HTTP 403 | Credentials missing or not allowed to run AQL (spec S1). |
| `PUBLIC_URL must be a bare origin` | Remove the path; give the update center its own hostname. |
| `signing certificate expires in N days` | Rotate the signing certificate (section 3). |
| `plugin count N < 0.9 × M` | The source shrank. Check `uc doctor`; if intended, `uc generate --force`. |
| `upstream generate-htaccess.sh changed` | An upgrade changed upstream's routing script; follow docs/upgrading.md. |
| Jenkins: "signature verification failed" | `root-ca.crt` missing from `update-center-rootCAs/`, or controller clock skew. |
| Plugin downloads fail with 404 | The version is not in the download map; check `uc doctor` and the Artifactory contents. Also check `status.json`'s `download_backend` against the current `DOWNLOAD_BACKEND`: if it changed since the live release was generated, the release's download map matches the old backend and every lookup misses — run `docker compose run --rm uc generate`. |
| `/download/…` returns 502 with `DOWNLOAD_ACCESS=proxy` | The backend's TLS certificate does not verify against `DOWNLOAD_PROXY_CA_FILE` (section 7). |
| web container keeps logging "waiting for the first published release" | No release is live yet; check `docker compose logs scheduler`. |
| `repo-adapter token missing: /data/repo-adapter/token` | The repo-adapter has not started; check `docker compose logs repo-adapter`. |
| `REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on` | Only the adapter speaks to Artifact Keeper; remove `REPO_ADAPTER=off`. |
| repo-adapter logs `listing failed: … HTTP 401` | Wrong repository credentials. For Artifact Keeper, `repo_password` must be an API token. |
| A plugin is missing and `uc doctor` lists it as misplaced | It is stored under another group than its manifest's `Group-Id`; move it to the path shown. |
| `uc doctor`: "no POM in the repository or the archive" | A Gradle-built plugin: procure its real `.pom` from the URL shown and store it beside the `.hpi`. |
