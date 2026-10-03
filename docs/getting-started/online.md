# Deploy online

A production deployment with internet access that reads plugins from repo.jenkins-ci.org and sends controllers to get.jenkins.io for downloads, or reads your own repository instead.

## Before you start

- Check the [requirements](requirements.md): a Linux Docker host, a hostname for `PUBLIC_URL`, a
  Jenkins community account, and internet access.
- The first run reads the whole plugin catalog and **takes hours**. Controllers can be pointed at
  the update center only after it finishes.

## 1. Build the images

```bash
git clone https://gitlab.com/ZacharySHale95/jenkins-update-center.git
cd jenkins-update-center
make build
```

This builds `jenkins-uc/uc:local` (update-center2 from source, with its tests) and
`jenkins-uc/web:local`. The first build takes several minutes.

## 2. Configure

```bash
cp .env.online.example .env
```

Edit `.env` and set `PUBLIC_URL` to your update center's origin, for example
`https://jenkins-updates.example.com`. Other settings worth knowing now:

- `GENERATE_SCHEDULE`: when to regenerate (default every six hours);
- `JAVA_TOOL_OPTIONS`: `-Xmx6g` for the full catalog;
- behind an outbound proxy, set it twice, once for the generator and once for the `uc` code:
  `-Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128` in `JAVA_TOOL_OPTIONS`, and
  `HTTPS_PROXY=http://proxy.example.com:3128` with `NO_PROXY=repo-adapter`;
- `HTTP_PORT`: the host port for the web container (default 8080);
- to read your own Artifactory or Artifact Keeper instead of repo.jenkins-ci.org:
  [Your own repository](../architecture/modes.md#your-own-repository).

Every setting: [Configuration reference](../reference/configuration.md).

The same settings can go in `config/uc.toml` instead, or as well:
[Configure with uc.toml](../how-to/configure-with-uc-toml.md). Once the images are built,
`docker compose run --rm uc config` shows what each setting ends up as.

## 3. Add credentials

`make secrets-placeholders` creates the credential files with the permissions the containers need;
writing into them keeps those permissions.

```bash
make secrets-placeholders
printf '%s' 'your-community-user' > secrets/repo_username
printf '%s' 'your-community-password' > secrets/repo_password
# recommended: a GitHub user and a classic token with no scopes, for plugin labels
printf '%s' 'your-github-user' > secrets/github_username
printf '%s' 'ghp_...' > secrets/github_token
```

Reading your own repository, `repo_username` and `repo_password` hold its account instead (for
Artifact Keeper, a user name and an API token). What each file holds:
[Secrets reference](../reference/secrets.md).

## 4. Create the signing keys

```bash
make pki-init
```

This creates the root CA and the signing certificate in `secrets/`, and sets the permissions of
everything there: `secrets/` stays yours, and each container reads its own files through its group
([File ownership on Linux](../reference/secrets.md#file-ownership-on-linux)). Then **move the root
CA's key to offline storage** right away: it is needed only once a year, to issue the next signing
certificate.

```bash
mv secrets/root /media/offline-usb/jenkins-uc-root      # any offline location
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
