# Deploy air-gapped

A production deployment that reads plugins from your own Artifactory or Artifact Keeper and needs nothing from the internet at run time.

## Before you start

- Check the [requirements](requirements.md): a Linux Docker host, a hostname for `PUBLIC_URL`, and
  an account that can read your repository.
- Your repository holds the plugins and Jenkins cores to offer (step 1). Controllers download from
  it too, directly or through the update center.
- Building the images needs internet access, once, on any machine with the same CPU architecture as
  the Docker host. Running them needs none.

## 1. Stock the repository

Plugins go in Maven layout under the group their manifest names, cores as
`org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war`. POMs are optional. Details:
[Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

## 2. Build the images

If the Docker host has internet access, build there:

```bash
git clone https://gitlab.com/ZacharySHale95/jenkins-update-center.git
cd jenkins-update-center
make build
```

If it has none, build on a machine that does, with the **same CPU architecture** as the Docker host
(x86-64 or arm64), and carry two files across: the images and the repository itself.

```bash
# on the machine with internet access
git clone https://gitlab.com/ZacharySHale95/jenkins-update-center.git
cd jenkins-update-center
make build
docker save jenkins-uc/uc:local jenkins-uc/web:local | gzip > ../jenkins-uc-images.tar.gz
git bundle create ../jenkins-uc.bundle master

# on the air-gapped Docker host, with both files in the current directory
git clone jenkins-uc.bundle jenkins-update-center
gunzip -c jenkins-uc-images.tar.gz | docker load
cd jenkins-update-center
```

The image archive is about 200 MB. `docker load` prints `Loaded image: jenkins-uc/uc:local` and
`Loaded image: jenkins-uc/web:local`. These two images are all the update center runs; nothing else
is pulled. On that host, skip `make build`; the other `make` targets below use the loaded images and
need no internet.

## 3. Configure

```bash
cp .env.airgapped.example .env
```

Edit `.env`. It already sets `MODE=airgapped`; keep that. Then:

- `PUBLIC_URL`: the origin controllers will use, decided now, because it is written into the signed
  metadata. Include the port if it is not the scheme's default, for example
  `http://uc.corp.example:8080`, or `https://jenkins-updates.corp.example` behind your TLS proxy
  ([Enable TLS](../how-to/enable-tls.md)).
- `REPO_URL` and `REPO_KEY`: for Artifactory, the `/artifactory/` base URL (a trailing slash is
  optional) and the repository key.
- For Artifact Keeper instead: `REPO_TYPE=artifact-keeper`, `REPO_URL` the server's base URL, and
  `REPO_KEY` the repository key (not its display name).
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

## 4. Add credentials

`make secrets-placeholders` creates the credential files with the permissions the containers need;
writing into them keeps those permissions.

```bash
make secrets-placeholders
printf '%s' 'svc-jenkins-uc' > secrets/repo_username
printf '%s' 'password-or-token' > secrets/repo_password
# only with DOWNLOAD_ACCESS=proxy: a read-only account for downloads
printf '%s' 'svc-read:password-or-token' > secrets/download_proxy_auth
```

`repo_password` is the account's password or an access token (Artifactory), or an API token
(Artifact Keeper). Only the repo-adapter container receives these two files. What each file holds:
[Secrets reference](../reference/secrets.md).

## 5. Create the signing keys

```bash
make pki-init
```

This creates the root CA and the signing certificate in `secrets/`, and sets the permissions of
everything there: `secrets/` stays yours, the uc containers read their files through group 10001, and
the web container reads `download_proxy_auth` through group 33
([File ownership on Linux](../reference/secrets.md#file-ownership-on-linux)). Then move the root CA's
key to offline storage right away (it is needed only once a year):

```bash
mv secrets/root /media/offline-usb/jenkins-uc-root      # any offline location
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

If the repository's certificate comes from a private CA, give the repo-adapter that CA's bundle, in
an override file next to `compose.yaml`:

```yaml
# compose.repo-ca.yaml
services:
  repo-adapter:
    volumes:
      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
    environment:
      REPO_CA_FILE: /etc/ssl/uc-repo-ca.crt
```

Copy the CA's PEM file to `secrets/repo-ca.crt` and add the override to `COMPOSE_FILE` in `.env`:

```bash
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.repo-ca.yaml
```

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
