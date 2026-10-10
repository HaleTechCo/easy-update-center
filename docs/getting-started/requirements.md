# Requirements

What a host, a network and a set of accounts need before you deploy the update center.

## Host

- A Linux host running Docker Engine, on x86-64 (amd64) or arm64. Those are the two architectures
  the images are published and built for.
- Docker Desktop on macOS or Windows works for evaluation. On Windows, run the commands from WSL, or
  from Git Bash with `export MSYS_NO_PATHCONV=1` first; otherwise Git Bash rewrites the
  `-v "$PWD/...:/..."` arguments some commands use.
- One host serves one update center. Controllers only need to reach it over HTTP(S).

## Software

- Docker Engine with Compose v2: `docker compose version` must work.
- GNU make is optional: it runs the contributor targets, such as `make build` (`make help` lists
  them).
- git, to clone this repository (not needed to deploy an air-gapped bundle).

Nothing else is installed on the host: the generator, Python and Apache all run in the containers.

## A hostname for PUBLIC_URL

`PUBLIC_URL` is the exact origin Jenkins controllers use to reach the update center, such as
`https://jenkins-updates.example.com`:

- **A bare origin, with its own hostname.** No path: upstream's routing redirects to absolute paths
  such as `/current/...`, and `jenkins-plugin-cli` ignores any path. You cannot serve it under
  `https://tools.example.com/jenkins-updates/`.
- **Reachable by every controller**, so not `localhost`. That includes a controller in a container on
  the update center's own machine: inside a container, `localhost` is the container itself. For a
  trial on Docker Desktop, `http://host.docker.internal:<port>` reaches the host from containers and
  from the host alike.
- **Written into the signed metadata.** Every download URL starts with it, so changing it later
  means publishing a new release (`docker compose run --rm uc generate`).
- `https://` is recommended; see [Enable TLS](../how-to/enable-tls.md).

## Accounts

- **Online:** a free Jenkins community account, created at https://accounts.jenkins.io. The
  generator's listing query against repo.jenkins-ci.org is refused anonymously (HTTP 403). Reading
  [your own repository](../architecture/modes.md#your-own-repository) instead, you need an account
  for it, as air-gapped.
  Recommended: a GitHub classic token with no scopes, for plugin labels; see
  [GitHub token](../reference/secrets.md#github-token).
- **Air-gapped:** an account that can read your repository. Artifactory: an account with read
  permission on the repository; no admin rights are needed, because the generator's listing (an AQL
  query) returns whatever the account can read. It authenticates with its password or an access
  token. Artifact Keeper: a user name and an API token.
- **Air-gapped with `DOWNLOAD_ACCESS=proxy`:** a read-only account for downloads (it can be the same
  one).

## Network access

Every host each service reaches, by mode, is in the [Network reference](../reference/network.md):

- a release's images need no build: Compose pulls them from ghcr.io, or you load them from the
  air-gapped bundle. Only building from source needs internet access
  ([Outbound: image builds](../reference/network.md#outbound-image-builds)), on the host or
  elsewhere, moving the images afterwards;
- online, the generator needs the hosts in [Outbound: online](../reference/network.md#outbound-online);
- air-gapped, only your repository ([Outbound: air-gapped](../reference/network.md#outbound-air-gapped));
- controllers need `PUBLIC_URL` and the download location ([Outbound: controllers](../reference/network.md#outbound-controllers)).

## Host sizing

Measured on 2026-09-25 with the full online catalog (update-center2 3.18.4, no plugin cap):

| | Measured |
|---|---|
| First run | 5 hours 4 minutes |
| Plugins published | 2,112 |
| Tiers | 13 LTS, 20 weekly |
| One release on disk | 303 MB |
| Generator download cache | 49 MB |
| Heap | `-Xmx6g` was enough |

So for an online installation:

- **Memory:** give the generator a 6 GB heap (`JAVA_TOOL_OPTIONS=-Xmx6g`); the host needs 8 GB or
  more.
- **Disk:** the `uc-data` volume holds `RETAIN_RELEASES` (5) releases, the cache and a staging copy
  during each run: about 2 GB. Allow 3 GB for growth.
- **Time:** the first run reads a manifest and a POM for every plugin release, tens of thousands of
  requests, so expect hours; it is mostly network-bound. Later runs fetch only what is new.

Air-gapped sizing scales with what your repository holds, and is far smaller for a curated
repository: a run of 25 plugins produced about 23.5 MB of site.
