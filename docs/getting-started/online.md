# Deploy online

A production deployment with internet access that reads plugins from repo.jenkins-ci.org and sends controllers to get.jenkins.io for downloads, or reads your own repository instead.

## Before you start

- Check the [requirements](requirements.md): a Linux Docker host, a hostname for `PUBLIC_URL`, a
  Jenkins community account, and internet access.
- The first run reads the whole plugin catalog and **takes hours**. Controllers can be pointed at
  the update center only after it finishes.

## 1. Get a release

Clone the release's tag (the latest is on the
[Releases page](https://github.com/HaleTechCo/easy-update-center/releases)) and copy the example
settings:

```bash
git clone --branch v<version> https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
cp .env.online.example .env
```

In `.env`, set the release's images, with the same `<version>` as the tag but without the `v`:

```bash
UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:<version>
WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:<version>
```

Pull them now, before anything else:

```bash
docker compose pull
docker compose run --rm --no-deps uc --version
```

Pull each image explicitly because the services also have `build:` sections: if a pull failed
silently (the packages still private, a mistyped version), `setup` or `up` would build the image from
your checkout and tag the result with the release's name, printing only a warning. `docker compose
pull` fails loudly instead, and the second command must print `uc <version>`, the release you chose.
You can check the signatures too: [Verify a release](../how-to/verify-a-release.md). You edit the
rest of `.env` in the next step.

### Or build from source

To run the current `main` instead, or a change of your own, clone it and build the images. Leave
`UC_IMAGE` and `WEB_IMAGE` unset.

```bash
git clone https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
cp .env.online.example .env
docker compose build
```

Compose reads `.env` for every command, building included, so copy the example first; you edit it
in the next step. This builds `easy-update-center/uc:local` (update-center2 from source, with its
tests) and `easy-update-center/web:local`. The first build takes several minutes. `make build` is
the shortcut: it builds the same images with `docker build`, so it needs no `.env`, and also the
image the test suites use.

## 2. Configure

Edit `.env`, copied from `.env.online.example` in step 1, and set `PUBLIC_URL` to your update
center's origin, for example `https://jenkins-updates.example.com`. Other settings worth knowing
now:

- On Linux, `UC_UID` and `UC_GID`: your `id -u` and `id -g`. Every container runs as that user, so
  the files in `secrets/` and `procurement/` stay yours; Docker Desktop needs neither.
- `GENERATE_SCHEDULE`: when to regenerate (default every six hours);
- `JAVA_TOOL_OPTIONS`: `-Xmx6g` for the full catalog;
- behind an outbound proxy, set it twice, once for the generator and once for the `uc` code:
  `-Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128` in `JAVA_TOOL_OPTIONS`, and
  `HTTPS_PROXY=http://proxy.example.com:3128` with `NO_PROXY=repo-adapter`;
- `HTTP_PORT`: the host port for the web container (default 8080);
- to read your own Artifactory or Artifact Keeper instead of repo.jenkins-ci.org:
  [Your own repository](../architecture/modes.md#your-own-repository).

Every setting: [Configuration reference](../reference/configuration.md).

Once the images are pulled or built, `docker compose run --rm --no-deps uc config` shows what each setting ends up as.

## 3. Add credentials

`docker compose run --rm setup` does first-time setup: it creates the credential files, readable by
you alone, and the signing keys (step 4). Writing into the credential files keeps their permissions.

Setup creates the signing keys on this host. To keep the root CA's key off it from the start,
[create the PKI on an offline machine](../how-to/rotate-signing-certificate.md#create-the-pki-on-an-offline-machine)
before running setup.

```bash
docker compose run --rm setup
printf '%s' 'your-community-user' > secrets/repo_username
printf '%s' 'your-community-password' > secrets/repo_password
# recommended: a GitHub user and a classic token with no scopes, for plugin labels
printf '%s' 'your-github-user' > secrets/github_username
printf '%s' 'ghp_...' > secrets/github_token
```

Reading your own repository, `repo_username` and `repo_password` hold its account instead (for
Artifact Keeper, a user name and an API token). What each file holds:
[Secrets reference](../reference/secrets.md).

`setup` is safe to repeat: it creates only what is missing, and never replaces the keys or a
credential file. Each step: [The setup service](../reference/cli.md#the-setup-service).

## 4. Move the root key offline

`setup` created the root CA and the signing certificate in `secrets/` (`uc pki init`), and set the
modes of everything there: `secrets/` stays yours ([File modes](../reference/secrets.md#file-modes)).
Now **move the root CA's key to offline storage**: it is needed only once a year, to issue the next
signing certificate.

```bash
mv secrets/root /media/offline-usb/easy-update-center-root      # any offline location
```

See [Where the root key lives](../how-to/rotate-signing-certificate.md#where-the-root-key-lives).

## 5. Start

```bash
docker compose up -d
```

This starts `web`, `scheduler` and `repo-adapter` (idle online). The scheduler starts the first
generation at once, because no release is live yet.

## 6. Watch the first run

```bash
docker compose logs -f scheduler
```

The generator logs each manifest and POM it downloads. The first run ends quietly: the generator's
last line is `Finished N executions found in parameters file`, then the scheduler logs
`read crontab`. It is done when `docker compose run --rm uc status` shows a `live_release`. Later
scheduled runs end with `published release <id>`. Until the first release, the web container logs
"waiting for the first published release" and answers nothing. If the run fails, the log shows a
Python traceback saying why; see [Troubleshooting](../troubleshooting.md#generation). A stack trace
from `GitHubSource` saying "This endpoint requires you to be authenticated" is only the warning for
a missing GitHub token.

## 7. Verify

```bash
docker compose run --rm uc status
```

`last_run.result` is `success` and `live_release` names the release. Then, from where your
controllers run:

```bash
curl -sI "https://jenkins-updates.example.com/update-center.json?version=2.528.1" | grep -i '^location'
curl -s  "https://jenkins-updates.example.com/uctest.json"
```

The first redirects to a `dynamic-stable-...` tier; the second prints `{}`.

## 8. Connect Jenkins

On every controller, copy `secrets/root-ca.crt` into `$JENKINS_HOME/update-center-rootCAs/` and set
the update site to `$PUBLIC_URL/update-center.json`:
[Configure Jenkins controllers](../how-to/configure-jenkins.md).

## Production checklist

- [ ] TLS: `PUBLIC_URL` is `https://`, through your proxy or the web container ([Enable TLS](../how-to/enable-tls.md)).
- [ ] The root CA key (`secrets/root/`) is offline, with a backup.
- [ ] `secrets/` is backed up: losing the signing key means issuing a new one; losing the root key
      means re-trusting every controller.
- [ ] Monitoring alerts on failed runs, certificate expiry and stale data ([Monitor](../how-to/monitor.md)).
- [ ] A reminder 45 days before `signing_cert.not_after` ([Rotate the signing certificate](../how-to/rotate-signing-certificate.md)).
- [ ] `GENERATE_SCHEDULE` suits how quickly you want new plugin releases.
