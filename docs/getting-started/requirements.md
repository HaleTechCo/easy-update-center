# Requirements

What a host, a network and a set of accounts need before you deploy the update center.

## Host

- A Linux host running Docker Engine, on x86-64 (amd64) or arm64. Those are the two architectures
  the images build for.
- Docker Desktop on macOS or Windows works for evaluation. On Windows, run the commands from WSL, or
  from Git Bash with `export MSYS_NO_PATHCONV=1` first; otherwise Git Bash rewrites the
  `-v "$PWD/...:/..."` arguments some commands use.
- One host serves one update center. Controllers only need to reach it over HTTP(S).

## Software

- Docker Engine with Compose v2: `docker compose version` must work.
- GNU make, for `make build`, `make pki-init` and the other targets.
- git, to clone this repository.

Nothing else is installed on the host: the generator, Python and Apache all run in the containers.

## A hostname for PUBLIC_URL

`PUBLIC_URL` is the exact origin Jenkins controllers use to reach the update center, such as
`https://jenkins-updates.example.com`:

- **A bare origin, with its own hostname.** No path: upstream's routing redirects to absolute paths
  such as `/current/...`, and `jenkins-plugin-cli` ignores any path. You cannot serve it under
  `https://tools.example.com/jenkins-updates/`.
- **Reachable by every controller**, so not `localhost`.
- **Written into the signed metadata.** Every download URL starts with it, so changing it later
  means publishing a new release (`docker compose run --rm uc generate`).
- `https://` is recommended; see [Enable TLS](../how-to/enable-tls.md).

## Accounts

- **Online:** a free Jenkins community account, created at https://accounts.jenkins.io. The
  generator's listing query against repo.jenkins-ci.org is refused anonymously (HTTP 403).
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

- the host needs internet access to **build** the images ([Outbound: image builds](../reference/network.md#outbound-image-builds)),
  or you build elsewhere and move them;
- online, the generator needs the hosts in [Outbound: online](../reference/network.md#outbound-online);
- air-gapped, only your repository ([Outbound: air-gapped](../reference/network.md#outbound-air-gapped));
- controllers need `PUBLIC_URL` and the download location ([Outbound: controllers](../reference/network.md#outbound-controllers)).

## Host sizing

The first full-catalog online run has not been measured yet. A run capped at 25 plugins produced
about 23.5 MB of site with 5 stable and 5 weekly tiers. For the full catalog, give the generator a
6 GB heap (`JAVA_TOOL_OPTIONS=-Xmx6g`, so the host needs 8 GB of memory or more) and leave room on
the `uc-data` volume for five retained releases plus the generator's download cache.

The first online run reads a manifest and a POM for every plugin release (tens of thousands of
requests) and takes hours; later runs fetch only what is new. Air-gapped sizing scales with what
your repository holds, and is far smaller for a curated repository.
