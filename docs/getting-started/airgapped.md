# Deploy air-gapped

A production deployment that reads plugins from your own Artifactory or Artifact Keeper and needs nothing from the internet at run time.

## Before you start

- Check the [requirements](requirements.md): a Linux Docker host, a hostname for `PUBLIC_URL`, and
  an account that can read your repository.
- Your repository holds the plugins and Jenkins cores to offer (next step). Controllers download
  from it too, directly or through the update center.
- Building the images needs internet access, once; running them does not.

To save typing `-f compose.yaml -f compose.airgapped.yaml` on every command, set this in the shell
you work in (on Windows, separate the files with `;` instead of `:`):

```bash
export COMPOSE_FILE=compose.yaml:compose.airgapped.yaml
```

The commands below include the `-f` flags anyway.

## 1. Stock the repository

Plugins go in Maven layout under the group their manifest names, cores as
`org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war`. POMs are optional. Details:
[Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

## 2. Build the images

On a host with internet access:

```bash
git clone https://gitlab.com/ZacharySHale95/jenkins-update-center.git
cd jenkins-update-center
make build
```

If the Docker host that will run the update center has no internet access, build on a machine that
does and move the images, with a clone of this repository, across:

```bash
# on the machine with internet access
make build
docker save jenkins-uc/uc:local jenkins-uc/web:local | gzip > jenkins-uc-images.tar.gz

# on the air-gapped Docker host, in its clone of this repository
gunzip -c jenkins-uc-images.tar.gz | docker load
```

The archive is about 200 MB. `docker load` prints `Loaded image: jenkins-uc/uc:local` and
`Loaded image: jenkins-uc/web:local`. Skip `make build` on that host.

## 3. Configure

```bash
cp .env.airgapped.example .env
```

Edit `.env`:

- `PUBLIC_URL`: your update center's origin, for example `https://jenkins-updates.corp.example`.
- `REPO_URL` and `REPO_KEY`: for Artifactory, the `/artifactory/` base URL and the repository key.
- For Artifact Keeper instead: `REPO_TYPE=artifact-keeper`, `REPO_URL` the server's base URL, and
  `REPO_KEY` the repository key (not its display name).
- If controllers cannot read the repository without credentials: `DOWNLOAD_ACCESS=proxy`
  ([Proxy plugin downloads](../how-to/proxy-downloads.md)).

Every setting: [Configuration reference](../reference/configuration.md).

## 4. Create the signing keys

```bash
make pki-init
```

Then **move `secrets/root/` to offline storage**: see
[Where the root key lives](../how-to/rotate-signing-certificate.md#where-the-root-key-lives).

## 5. Add credentials

```bash
make secrets-placeholders
printf '%s' 'svc-jenkins-uc' > secrets/repo_username
printf '%s' 'password-or-api-token' > secrets/repo_password    # Artifact Keeper: an API token
```

Only the repo-adapter container receives these. On Linux, give them to the container user:

```bash
sudo chown 10001:10001 secrets/repo_*
sudo chmod 0400 secrets/repo_password
```

With `DOWNLOAD_ACCESS=proxy`, also write `secrets/download_proxy_auth`; see
[Secrets reference](../reference/secrets.md).

## 6. Start

First check what the generator will see:

```bash
docker compose -f compose.yaml -f compose.airgapped.yaml run --rm uc doctor --fetch
```

Fix what it reports as dropped, or accept it, then start:

```bash
docker compose -f compose.yaml -f compose.airgapped.yaml up -d
```

## 7. Watch the first run

```bash
docker compose -f compose.yaml -f compose.airgapped.yaml logs -f scheduler repo-adapter
```

The run ends with `published release <id>`. Its length depends on the repository: the adapter reads
each archive's manifest once and caches it. Until the first release, the web container logs "waiting
for the first published release".

## 8. Verify

```bash
docker compose -f compose.yaml -f compose.airgapped.yaml run --rm uc status
curl -sI "https://jenkins-updates.corp.example/update-center.json?version=2.528.1" | grep -i '^location'
```

`last_run.result` is `success`; `warnings` lists what was dropped (GitHub labels, popularity and
tool installers are expected). The `curl` shows a redirect to a tier for a core your repository
holds, or to `current/` when it holds none.

## 9. Connect Jenkins

On every controller, copy `secrets/root-ca.crt` into `$JENKINS_HOME/update-center-rootCAs/` and set
the update site to `$PUBLIC_URL/update-center.json`:
[Configure Jenkins controllers](../how-to/configure-jenkins.md).

## Repository behind a private CA

If the repository's certificate comes from a private CA, give the repo-adapter that CA's bundle. It
is added to the system bundle, not a replacement for it:

```yaml
# compose.repo-ca.yaml
services:
  repo-adapter:
    volumes:
      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
    environment:
      REPO_CA_FILE: /etc/ssl/uc-repo-ca.crt
```

Then use three files: `-f compose.yaml -f compose.airgapped.yaml -f compose.repo-ca.yaml`, or
`COMPOSE_FILE=compose.yaml:compose.airgapped.yaml:compose.repo-ca.yaml`. The download proxy has its
own CA setting: [Backend with a private CA](../how-to/proxy-downloads.md#backend-with-a-private-ca).

## Production checklist

- [ ] TLS: `PUBLIC_URL` is `https://` ([Enable TLS](../how-to/enable-tls.md)).
- [ ] The root CA key (`secrets/root/`) is offline, with a backup.
- [ ] `secrets/` is backed up.
- [ ] Monitoring alerts on failed runs, certificate expiry and stale data ([Monitor](../how-to/monitor.md)).
- [ ] A reminder 45 days before `signing_cert.not_after` ([Rotate the signing certificate](../how-to/rotate-signing-certificate.md)).
- [ ] A routine to procure fresh security warnings, and optionally popularity and tool installers
      ([Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md)).
- [ ] `uc doctor` after each procurement of plugins.
