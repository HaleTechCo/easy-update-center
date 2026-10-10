# Deploy air-gapped

A production deployment that reads plugins from your own Artifactory or Artifact Keeper and needs nothing from the internet at run time.

## Before you start

- Check the [requirements](requirements.md): a Linux Docker host, a hostname for `PUBLIC_URL`, and
  an account that can read your repository.
- Your repository holds the plugins and Jenkins cores to offer (step 1). Controllers download from
  it too, directly or through the update center.
- A release needs no build. Download it once on any machine with internet access, then carry it
  across; the Docker host needs none. Building from source instead needs internet access once, on
  a machine with the same CPU architecture as the Docker host.

## 1. Stock the repository

Plugins go in Maven layout under the group their manifest names, cores as
`org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war`. POMs are optional. Details:
[Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

## 2. Get the images

On a machine with internet access, open the
[Releases page](https://github.com/HaleTechCo/easy-update-center/releases) and download, for the
Docker host's architecture (x86-64 or arm64), `easy-update-center-<version>-airgapped-amd64.tar` or
`easy-update-center-<version>-airgapped-arm64.tar`, with `SHA256SUMS` and `SHA256SUMS.sigstore.json`.
Verify them there ([Verify a release](../how-to/verify-a-release.md#the-air-gapped-bundle-and-other-assets)),
then carry the bundle across. On the Docker host:

```bash
tar -xf easy-update-center-<version>-airgapped-amd64.tar          # or -arm64
cd easy-update-center-<version>-airgapped-amd64
docker load -i images.tar
tar -xzf easy-update-center-<version>.tar.gz
cd easy-update-center-<version>
cp .env.airgapped.example .env
```

The bundle holds `images.tar` (the uc and web images), the source as
`easy-update-center-<version>.tar.gz` (Compose files, setting examples and docs), and a `README.txt`
with these steps. `docker load` prints
`Loaded image: ghcr.io/haletechco/easy-update-center-uc:<version>` and
`Loaded image: ghcr.io/haletechco/easy-update-center-web:<version>`. These two images are all the
update center runs; nothing else is pulled. In the new `.env`, set them:

```bash
UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:<version>
WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:<version>
```

Do not run `docker compose build`; the commands below use the loaded images and need no internet.

### Or build from source

To run the current `main`, or a change of your own, build on a machine with internet access and the
**same CPU architecture** as the Docker host (x86-64 or arm64), and carry two files across: the
images and the repository itself. Leave `UC_IMAGE` and `WEB_IMAGE` unset.

If the Docker host has internet access, build there:

```bash
git clone https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
cp .env.airgapped.example .env
docker compose build
```

Compose reads `.env` for every command, building included, so copy the air-gapped example first;
you edit it in step 3. If the Docker host has none, build elsewhere and carry the files:

```bash
# on the machine with internet access
git clone https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
cp .env.airgapped.example .env
docker compose build
docker save easy-update-center/uc:local easy-update-center/web:local \
  | gzip > ../easy-update-center-images.tar.gz
git bundle create ../easy-update-center.bundle main

# on the air-gapped Docker host, with both files in the current directory
git clone easy-update-center.bundle easy-update-center
gunzip -c easy-update-center-images.tar.gz | docker load
cd easy-update-center
cp .env.airgapped.example .env
```

The image archive is about 200 MB. `docker load` prints
`Loaded image: easy-update-center/uc:local` and `Loaded image: easy-update-center/web:local`. On that
host, skip `docker compose build`. `make build` is the shortcut: it builds the same images with
`docker build`, so it needs no `.env`, and also the image the test suites use.

## 3. Configure

Edit `.env`, copied from `.env.airgapped.example` in step 2. It already sets `MODE=airgapped`; keep
that. Then:

- On Linux, `UC_UID` and `UC_GID`: your `id -u` and `id -g`. Every container runs as that user, so
  the files in `secrets/` and `procurement/` stay yours; Docker Desktop needs neither.
- `PUBLIC_URL`: the origin controllers will use, decided now, because it is written into the signed
  metadata. Include the port if it is not the scheme's default, for example
  `http://uc.corp.example:8080`, or `https://jenkins-updates.corp.example` behind your TLS proxy
  ([Enable TLS](../how-to/enable-tls.md)).
- `REPO_URL` and `REPO_KEY`: for Artifactory, the `/artifactory/` base URL (a trailing slash is
  optional) and the repository key.
- For Artifact Keeper instead: `REPO_TYPE=artifact-keeper`, `REPO_URL` the server's base URL, and
  `REPO_KEY` the repository key (not its display name).
- `DOWNLOAD_BACKEND_URL`: where controllers download from, the same repository by Maven path:
  `${REPO_URL}${REPO_KEY}/` for Artifactory, `${REPO_URL}maven/${REPO_KEY}/` for Artifact Keeper.
- `DOWNLOAD_ACCESS=proxy` if controllers cannot read the repository without credentials
  ([Proxy plugin downloads](../how-to/proxy-downloads.md)).
- Uncomment `COMPOSE_FILE=compose.yaml:compose.airgapped.yaml` (on Windows, separate the files with
  `;`). Compose reads it from `.env`, so every `docker compose` command below, and in the other
  pages, uses the air-gapped override without `-f` flags, as long as you run it from the repository
  directory.
- Recommended: fresh security warnings through `RESOURCES_SOURCE=url`. The data built into the image
  is only as recent as its update-center2 commit, and `status.json` marks it stale 30 days after
  that. See [Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md).

If the repository (or the download backend, in proxy mode) uses a certificate from a private CA, set
that up now, before step 6: [Repository behind a private CA](#repository-behind-a-private-ca).

Every setting: [Configuration reference](../reference/configuration.md).

Once the images are loaded or built, `docker compose run --rm --no-deps uc config` shows what each setting ends up as.

## 4. Add credentials

`docker compose run --rm setup` does first-time setup: it creates the credential files, readable by
you alone, and the signing keys (step 5). Writing into the credential files keeps their permissions.

Setup creates the signing keys on this host. To keep the root CA's key off it from the start,
[create the PKI on an offline machine](../how-to/rotate-signing-certificate.md#create-the-pki-on-an-offline-machine)
before running setup.

```bash
docker compose run --rm setup
printf '%s' 'svc-easy-update-center' > secrets/repo_username
printf '%s' 'password-or-token' > secrets/repo_password
# only with DOWNLOAD_ACCESS=proxy: a read-only account for downloads
printf '%s' 'svc-read:password-or-token' > secrets/download_proxy_auth
```

`repo_password` is the account's password or an access token (Artifactory), or an API token
(Artifact Keeper). Only the repo-adapter container receives these two files. What each file holds:
[Secrets reference](../reference/secrets.md).

`setup` is safe to repeat: it creates only what is missing, and never replaces the keys or a
credential file. Each step: [The setup service](../reference/cli.md#the-setup-service).

## 5. Move the root key offline

`setup` created the root CA and the signing certificate in `secrets/` (`uc pki init`), and set the
modes of everything there: `secrets/` stays yours ([File modes](../reference/secrets.md#file-modes)). Now move the root CA's
key to offline storage (it is needed only once a year):

```bash
mv secrets/root /media/offline-usb/easy-update-center-root      # any offline location
```

See [Where the root key lives](../how-to/rotate-signing-certificate.md#where-the-root-key-lives).

## 6. Start

First check what the generator will see:

```bash
docker compose run --rm uc doctor --fetch
```

`--fetch` downloads the manifest and POM of every archive the generator will read, so dependency
problems show up before the first generation; on a large repository it takes a while, and the
results are cached for the generator. Fix what it reports as dropped, or accept it, then start:

```bash
docker compose up -d
```

## 7. Watch the first run

```bash
docker compose logs -f scheduler repo-adapter
```

Its length depends on the repository: the adapter reads each archive's manifest once and caches it.
The first run ends quietly (the scheduler logs `read crontab` after it); it is done when
`docker compose run --rm uc status` shows a `live_release`. Later scheduled runs end with
`published release <id>`. Until the first release, the web container logs "waiting for the first
published release". A failed run logs a Python traceback saying why; repository errors (401,
certificates) also appear in the repo-adapter's log.

## 8. Verify

```bash
docker compose run --rm uc status
```

`last_run.result` is `success`; `warnings` lists what the air gap dropped (GitHub labels, popularity
and tool installers are expected). Then, from a controller's host, with your own URL and a core
version you stocked:

```bash
PUBLIC_URL=http://uc.corp.example:8080
curl -sI "$PUBLIC_URL/update-center.json?version=2.528.1" | grep -i '^location'
curl -sSo /dev/null -w '%{http_code}\n' -L "$PUBLIC_URL/download/plugins/git/latest/git.hpi"
```

The first prints `Location: http://uc.corp.example:8080/dynamic-stable-2.528.1/update-center.json`
(the newest tier at or below that version; `current/` when the repository holds no core). The
second downloads a plugin you stocked (here `git`) and prints `200`.

## 9. Connect Jenkins

On every controller, copy `secrets/root-ca.crt` into `$JENKINS_HOME/update-center-rootCAs/` and set
the update site to `$PUBLIC_URL/update-center.json`:
[Configure Jenkins controllers](../how-to/configure-jenkins.md).

## Repository behind a private CA

If the repository's certificate comes from a private CA, give the repo-adapter that CA's bundle.
Mount it with an override file next to `compose.yaml`:

```yaml
# compose.repo-ca.yaml
services:
  repo-adapter:
    volumes:
      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
  uc:  # uc procure push and clean talk to the repository directly, not through the repo-adapter
    volumes:
      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
```

Copy the CA's PEM file to `secrets/repo-ca.crt`, then point `REPO_CA_FILE` at it and add the override
to `COMPOSE_FILE`, both in `.env`:

```bash
REPO_CA_FILE=/etc/ssl/uc-repo-ca.crt
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.repo-ca.yaml
```

Set it in `.env`, not under `environment:` in the override: every service then has
the same value, which `uc config` needs to tell whether the running services use the current
settings ([When changes apply](../reference/configuration.md#when-changes-apply)). The other
services never open the file, so they need no mount.

This bundle is **added to** the system bundle. The download proxy's CA setting works the other way
round: `DOWNLOAD_PROXY_CA_FILE` **replaces** the system bundle for downloads
([Backend with a private CA](../how-to/proxy-downloads.md#backend-with-a-private-ca)). With both, and
TLS in the web container, `COMPOSE_FILE` lists every override:

```bash
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.repo-ca.yaml:compose.proxy-ca.yaml:compose.tls.yaml
```

## Production checklist

- [ ] TLS: `PUBLIC_URL` is `https://` ([Enable TLS](../how-to/enable-tls.md)); controllers trust its
      certificate.
- [ ] The root CA key is offline, with a backup.
- [ ] `secrets/` is backed up.
- [ ] Monitoring alerts on failed runs, certificate expiry and stale data ([Monitor](../how-to/monitor.md)).
- [ ] A reminder 45 days before `signing_cert.not_after` ([Rotate the signing certificate](../how-to/rotate-signing-certificate.md)).
- [ ] A routine to procure fresh security warnings, and optionally popularity and tool installers
      ([Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md)).
- [ ] `uc doctor` after each procurement of plugins.
