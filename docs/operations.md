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
printf '%s' '…password or API token…' > secrets/artifactory_password
# online, recommended: GitHub user and a token without scopes
printf '%s' 'my-bot' > secrets/github_username
printf '%s' 'ghp_…' > secrets/github_token
# only with DOWNLOAD_ACCESS=proxy: a read-only account for streaming downloads
printf '%s' 'svc-read:…' > secrets/download_proxy_auth
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
