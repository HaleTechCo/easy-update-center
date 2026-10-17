# Easy Update Center for Jenkins

[![CI](https://github.com/HaleTechCo/easy-update-center/actions/workflows/ci.yml/badge.svg)](https://github.com/HaleTechCo/easy-update-center/actions/workflows/ci.yml)

**The upstream Jenkins update-center2, containerized and extended, with support for Artifact Keeper
and air-gapped networks.**

The same update center Jenkins uses (`updates.jenkins.io`), in containers on your own host. Jenkins
controllers and `jenkins-plugin-cli` point at it with no plugin or code change. It reads plugins
from the public plugin repository or from your own, and runs online or fully air-gapped.

## How this was built

I built this with AI, and want to be upfront about how. It started because I wanted the Jenkins
update-center2 in containers; later I needed it to run in air-gapped networks, and later still to
work with Artifact Keeper. I set the requirements, chose the features and made the design
decisions. Claude, Anthropic's AI model, working in Claude Code, wrote the code, the tests and the
documentation. Reviews of that work were also done by Claude, with me deciding what to act on, and
I tested it against my own private Artifact Keeper. The git history shows this too: commits carry a
`Co-Authored-By` line naming the Claude model that wrote them.

## What you get

### From upstream

[update-center2](https://github.com/jenkins-infra/update-center2) is the generator the Jenkins
project runs to build updates.jenkins.io. This repository builds it from a pinned release, so you
get what Jenkins controllers already expect:

- **Every upstream file:** `update-center.json`, `plugin-versions.json`, `release-history.json`,
  the experimental and stable sites, download index pages, and tool installers.
- **Version routing:** `/update-center.json?version=2.528.1` redirects each controller to the tiered
  site that only offers plugins its core can run, exactly as updates.jenkins.io does.

### What this repository adds

On its own, update-center2 is a Java program that reads repo.jenkins-ci.org's Artifactory and
writes files. Around it, this repository adds:

- **Containers and a scheduler:** a uc image (the generator and the `uc` command) and an Apache web
  image, run with Docker Compose. The scheduler regenerates the site on your schedule, and Apache
  serves it, sending downloads to get.jenkins.io or to your repository.
- **Artifact Keeper, and your own Artifactory:** update-center2 only speaks Artifactory's API and
  expects repo.jenkins-ci.org's layout. The [repo-adapter](docs/architecture/repo-adapter.md)
  answers its requests from Artifactory or Artifact Keeper, and fills in the POMs, descriptions and
  release dates a private repository lacks from the plugins themselves.
- **Air-gapped mode:** the whole stack runs with no internet access, from your own repository. What
  each mode keeps and drops: [Modes](docs/architecture/modes.md).
- **Two small generator patches:** popularity data becomes optional (upstream fails without it,
  which rules out air-gapped runs), and the 400-day core age for tiers becomes a setting.
  [The patches](docs/development/upgrading-upstream.md#the-patches) are meant to go upstream.
- **Your own signing keys:** `uc pki` creates and rotates the PKI that signs the metadata;
  controllers trust your root CA.
- **Safe publishing:** each release is validated before it goes live, the switch is atomic, the last
  five releases are kept, and `uc rollback` goes back in one command.
- **Procurement help:** `uc procure` stages the real POMs, missing dependencies and cores your
  repository lacks for review, records files you add by hand (moving misplaced ones), pushes exactly
  what you approved, and cleans up what the repository then holds.
- **Your own policy:** hide plugins or releases, and publish your own security warnings.
- **Monitoring:** `status.json` reports the last run, the certificate's expiry and the age of the
  security data.
- **Settings in one file:** every setting in `.env`; `uc config` shows
  each value and where it came from, and the scheduler can run a read-only `uc doctor` report on its
  own schedule.

## How it works

A scheduler regenerates the site with Jenkins' own generator, update-center2, and an Apache
container serves it. Plugin binaries are not copied: downloads go to get.jenkins.io or to your
repository. More in the [architecture overview](docs/architecture/overview.md).

## Quick start

You need a Linux host with Docker (Docker Compose 2.24.4 or later), a hostname for the update center, and a Jenkins
community account (online) or an account on your repository (air-gapped):
[Requirements](docs/getting-started/requirements.md). Take the latest version from the
[Releases page](https://github.com/HaleTechCo/easy-update-center/releases).

Online:

```bash
git clone --branch v<version> https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
cp .env.online.example .env
# in .env: PUBLIC_URL, and on Linux UC_UID and UC_GID (id -u, id -g)
docker compose pull                                            # the release's images
docker compose run --rm setup                                  # credential files, signing keys
printf '%s' 'community-user' > secrets/repo_username           # free: https://accounts.jenkins.io
printf '%s' 'community-password' > secrets/repo_password
docker compose up -d
```

Air-gapped, from the release's verified bundle and with your repository
[stocked](docs/how-to/stock-airgapped-repository.md):

```bash
tar -xf easy-update-center-<version>-airgapped-amd64.tar && cd easy-update-center-<version>-airgapped-amd64
docker load -i images.tar
tar -xzf easy-update-center-<version>.tar.gz && cd easy-update-center-<version>
cp .env.airgapped.example .env
# in .env: PUBLIC_URL, REPO_URL, REPO_KEY, DOWNLOAD_BACKEND_URL, on Linux UC_UID and UC_GID;
#   uncomment COMPOSE_FILE
docker compose run --rm setup
printf '%s' 'svc-easy-update-center' > secrets/repo_username
printf '%s' 'password-or-token' > secrets/repo_password
docker compose up -d
```

When `docker compose run --rm uc status` shows a `live_release` (online, the first run takes hours),
trust `secrets/root-ca.crt` on every controller and point its update site at the update center:
[Configure Jenkins controllers](docs/how-to/configure-jenkins.md). Every step, building from source,
and the production checklist: [Deploy online](docs/getting-started/online.md),
[Deploy air-gapped](docs/getting-started/airgapped.md).

## Documentation

The [documentation index](docs/README.md) has everything:

- **Getting started:** requirements, and a production deployment per mode.
- **How-to guides:** configuring controllers, stocking a repository, procuring missing files, keeping
  upstream data fresh, TLS, proxied downloads, key rotation, blocking plugins, rollback, monitoring,
  upgrades, verifying a release.
- **Reference:** every setting, `uc` command, secret, `status.json` field, URL, network host and
  dependency.
- **Architecture:** how the pieces fit, with diagrams.

Something wrong? See [Troubleshooting](docs/troubleshooting.md).

## Contributing

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues
privately, as described in [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). The update-center2 generator this project builds and patches is MIT
under the Jenkins project's governance, though its repository has no license file of its own; see
[Note on update-center2's license](docs/reference/dependencies.md#note-on-update-center2s-license).
The uc image carries the notices for all the third-party software it ships in `/opt/uc/licenses/`
([Third-party notices](docs/reference/dependencies.md#third-party-notices)).
