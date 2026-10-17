# Configuration reference

Every setting the update center reads, its default in each mode, and which service reads it.

## How settings are read

Every setting is an environment variable, set in `.env` in the project directory: the file Compose
loads as each service's `env_file` (`UC_ENV_FILE` names another) for `uc`, `scheduler` and
`repo-adapter`. `.env.online.example` and `.env.airgapped.example` list every setting.

The web container gets only its own variables from it, through Compose
([Where web gets its settings](../architecture/request-routing.md#where-web-gets-its-settings)).

A setting's value is the first of: a command-line option (where the command has one), the
environment, the default for the mode, the built-in default.

Run `uc config` to see every value and where it came from ([uc config](cli.md#uc-config)).

- **Empty values**: an empty variable means "not set", so the default applies, except for
  `POPULARITY_JSON_URL` and `TOOL_INSTALLERS_URL`, where an explicitly empty value turns the
  feature off.
- **Types**: booleans accept `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`.
- **Mistakes**: an invalid value stops the command that reads it with a message naming the
  setting.
- Credentials and the signing keys are files, not settings: see [Secrets](secrets.md).

When each change takes effect: [When changes apply](#when-changes-apply).

In the tables, "generate" means `uc generate`, which the `scheduler` service runs on its schedule
and which you can run by hand; "doctor" means `uc doctor`; "procure" means `uc procure`.

## When changes apply

Commands (`uc generate`, `uc doctor`, `uc procure`) read the settings each time they run; the
services read them when they start. After editing `.env`, check the settings, then let Compose
recreate every service whose settings changed:

```bash
docker compose run --rm --no-deps uc config --check
docker compose up -d
```

Recreating the scheduler loses a generation in progress; the next one waits for
`GENERATE_SCHEDULE` (the scheduler generates at once only when no release is live), and
`docker compose run --rm uc generate` publishes now. Web uses a new `PUBLIC_URL` or
`DOWNLOAD_BACKEND` as soon as it is recreated, but the live release keeps the old values (they are
written into it when it is generated): after changing either, publish now with
`docker compose run --rm uc generate`.

## Required

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `MODE` | none | every uc-image service, and web | `online` or `airgapped`. It only sets defaults; every setting below can still be set explicitly. |
| `PUBLIC_URL` | none | every uc-image service, and web | The exact origin Jenkins uses, with no path, for example `https://jenkins-updates.example.com`. Must not contain credentials or any `@`. See [A hostname for PUBLIC_URL](../getting-started/requirements.md#a-hostname-for-public_url). |

## Mode defaults

| Setting | `online` | `airgapped` |
|---|---|---|
| `REPO_TYPE` | `artifactory` | `artifactory` |
| `REPO_URL` | `https://repo.jenkins-ci.org/` | required |
| `REPO_KEY` | `releases` | required |
| `REPO_ADAPTER` | `off` (always `on` for `artifact-keeper`) | `on` |
| `CORE_AGE_DAYS` | `400` | `0` |
| `DOWNLOAD_BACKEND` | `mirror` | `artifactory` |
| `DOWNLOAD_BACKEND_URL` | `https://get.jenkins.io` | required |
| `RESOURCES_SOURCE` | `github` | `bundled` |
| `POPULARITY_JSON_URL` | upstream's `latestNumbers.json` on raw.githubusercontent.com | empty (skipped) |
| `TOOL_INSTALLERS_URL` | `https://updates.jenkins.io/updates/` | empty (skipped) |

A missing trailing slash on `REPO_URL` is added, so `${REPO_URL}${REPO_KEY}` always has one between them.
`REPO_URL` and `REPO_KEY` default to repo.jenkins-ci.org only in online mode with
`REPO_TYPE=artifactory`; otherwise both are required. `DOWNLOAD_BACKEND_URL` follows
`DOWNLOAD_BACKEND`, not the mode: it defaults to get.jenkins.io for `mirror`, and is required for
`artifactory`: your repository by Maven path, `${REPO_URL}${REPO_KEY}/` for Artifactory or
`${REPO_URL}maven/${REPO_KEY}/` for Artifact Keeper.

## Source repository

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `REPO_TYPE` | `artifactory` | generate, doctor, procure, repo-adapter | `artifactory` or `artifact-keeper`. Artifact Keeper is read only through the repo-adapter. |
| `REPO_URL` | see [Mode defaults](#mode-defaults) | generate, doctor, procure, repo-adapter | Artifactory: the `/artifactory/` base URL. Artifact Keeper: the server's base URL. A trailing slash is optional. Must not contain credentials or any `@`. |
| `REPO_API_URL` | `${REPO_URL}api/` | generate, doctor, procure, repo-adapter | Artifactory's API base, for the listing query. Set it only when the API is served elsewhere. Must not contain credentials or any `@`. |
| `REPO_KEY` | see [Mode defaults](#mode-defaults) | generate, doctor, procure, repo-adapter | The repository key (for Artifact Keeper, the key, not the display name). Letters, digits, `.`, `_` and `-` only. |

## repo-adapter

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `REPO_ADAPTER` | see [Mode defaults](#mode-defaults) | generate, doctor, procure, repo-adapter | Whether the generator reads the repository through the [repo-adapter](../architecture/repo-adapter.md). `REPO_TYPE=artifact-keeper` with `REPO_ADAPTER=off` is refused. The service stays idle when off. |
| `REPO_CA_FILE` | none | repo-adapter, procure; uc's own requests (generate, doctor) with `REPO_ADAPTER=off` | A PEM bundle added to the system bundle, for a repository behind a private CA. See [Repository behind a private CA](../getting-started/airgapped.md#repository-behind-a-private-ca): set it in `.env`, and mount the file with `compose.repo-ca.yaml`. The Java generator never uses it, so a private CA needs `REPO_ADAPTER=on`. `uc procure push` and `clean`, and the downloads of `uc procure stage`, verify against it too. |
| `REPO_MAX_ARCHIVE_MB` | `512` | repo-adapter, procure | Archives larger than this are skipped and reported. `uc procure stage --dependencies` and `--war` download none larger either. Minimum 1. |

## Downloads

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `DOWNLOAD_BACKEND` | see [Mode defaults](#mode-defaults) | generate, web | `mirror` (get.jenkins.io layout) or `artifactory` (any Maven-layout repository, looked up through the release's download map). |
| `DOWNLOAD_BACKEND_URL` | see [Mode defaults](#mode-defaults) | web (every uc-image service checks it) | Where `/download/...` requests are sent. It must not contain credentials or any `@` (uc and web refuse them; for `DOWNLOAD_ACCESS=proxy` put them in `secrets/download_proxy_auth`). Controllers must be able to read it without credentials unless `DOWNLOAD_ACCESS=proxy`. |
| `DOWNLOAD_ACCESS` | `redirect` | web | `redirect` sends controllers to the backend; `proxy` streams the file through the web container and needs `DOWNLOAD_BACKEND_URL` set explicitly (web sends `download_proxy_auth` to it; an `http://` backend only draws a warning). See [Proxy plugin downloads](../how-to/proxy-downloads.md). |
| `DOWNLOAD_PROXY_CA_FILE` | `/etc/ssl/certs/ca-certificates.crt` | web | Proxy mode only: the CA bundle the backend's certificate must verify against. |

## Upstream data

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `RESOURCES_SOURCE` | see [Mode defaults](#mode-defaults) | generate | Where security warnings, suspensions and labels come from: `github` (upstream at `RESOURCES_REF`), `url` (a tarball at `RESOURCES_URL`), or `bundled` (the copy in the image). |
| `RESOURCES_REF` | `master` | generate | The update-center2 branch, tag or commit to read with `RESOURCES_SOURCE=github`. |
| `RESOURCES_URL` | none | generate | Required with `RESOURCES_SOURCE=url`: a tarball containing `resources/`. Must not contain credentials or any `@`. |
| `RESOURCES_SHA256` | none | generate | With `RESOURCES_SOURCE=url`, the tarball must match this checksum. |
| `POPULARITY_JSON_URL` | see [Mode defaults](#mode-defaults) | generate | Plugin install counts. Set it empty to skip; popularity is then 0. Must not contain credentials or any `@`. |
| `TOOL_INSTALLERS_URL` | see [Mode defaults](#mode-defaults) | generate | A directory listing of the `updates/` tool-installer files. Set it empty to skip. Must not contain credentials or any `@`. |
| `STALE_AFTER_DAYS` | `30` | generate | `status.json` marks the resources stale when they are older than this. |

See [Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md).

## Generation

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `UC_ID` | `default` | generate | The update site id written into the metadata. It must match the id of the update site on your controllers. |
| `GENERATE_SCHEDULE` | `0 */6 * * *` | scheduler | Cron expression for regular generation. The scheduler also generates at once when no release is live. |
| `CORE_AGE_DAYS` | see [Mode defaults](#mode-defaults) | generate | Tiers are offered for cores released within this many days; `0` means every core a plugin requires. See [Core age](../architecture/modes.md#core-age). |
| `RETAIN_RELEASES` | `5` | generate | Releases kept for rollback. Minimum 1. |
| `MIN_PLUGIN_RATIO` | `0.9` | generate | A release with fewer than this share of the live release's plugins is refused, unless run with `uc generate --force`. Between 0 and 1. |

## Doctor

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `DOCTOR_SCHEDULE` | empty: none | scheduler | Cron expression (or an `@macro`) for a read-only `uc doctor --record` run, which saves the report and puts its counts in `status.json`. It never stages, pushes or cleans. |
| `DOCTOR_FETCH` | `false` | doctor | Download the POMs and manifests missing from the generator's cache, as `--fetch` does; `--no-fetch` turns it off for one run. |
| `DOCTOR_SINCE` | empty: the oldest core in the repository | doctor, procure | The floor for required cores, as `--since`, so also the floor of `uc procure stage --war required`. A Jenkins version such as `2.462.1`; anything else is refused when the settings load. |

## Signing

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `CERTIFICATE_MINIMUM_VALID_DAYS` | `30` | generate | Generation stops when the signing certificate expires within this many days. Also passed to the generator. |

## Web and TLS

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `TLS_CERT_FILE` | none | web | With `TLS_KEY_FILE`, enables HTTPS on port 8443. Both must be readable. See [Enable TLS](../how-to/enable-tls.md). |
| `TLS_KEY_FILE` | none | web | The private key for `TLS_CERT_FILE`. |

## Compose

These are read by `docker compose` itself, from the shell environment or from `.env` in the project
directory, not by the containers.

| Setting | Default | Notes |
|---|---|---|
| `COMPOSE_PATH_SEPARATOR` | `:` (`;` on Windows) | The separator between the files in `COMPOSE_FILE`. Both example files set it to `:`, so the same `COMPOSE_FILE` line works on Windows, where Docker Compose would otherwise split on `;`. |
| `COMPOSE_FILE` | `compose.yaml` | The Compose files every plain `docker compose` command uses. Set it whenever you use an override, in either mode, so later commands keep it: `compose.yaml:compose.airgapped.yaml` air-gapped, plus `compose.tls.yaml`, `compose.repo-ca.yaml`, `compose.proxy-ca.yaml` or `compose.repo-credentials.yaml` when you use them, and `compose.build.yaml` to run images built from your checkout ([Or build from source](../getting-started/online.md#or-build-from-source)), separated by `:` (see `COMPOSE_PATH_SEPARATOR`). Without it, add `-f` for each file to every command. |
| `UC_ENV_FILE` | `.env` | The file every uc-image service loads as `env_file`. Web does not use it: it takes its variables from Compose's own `.env`, or from `--env-file`. To run several configurations side by side, see [Several configurations](#several-configurations). |
| `UC_UID` | `10001` | The user id every container runs as. On Linux, set it to the output of `id -u`, so the files in `secrets/` and `procurement/` stay yours. |
| `UC_GID` | `10001` | The group id every container runs as. On Linux, the output of `id -g`. |
| `UC_IMAGE` | this release's | The image for `uc`, `scheduler`, `repo-adapter` and `setup`. Unset, `compose.yaml` names this release's, `ghcr.io/haletechco/easy-update-center-uc:<version>`, and `compose.build.yaml` names `easy-update-center/uc:local`. |
| `WEB_IMAGE` | this release's | The image for `web`: `ghcr.io/haletechco/easy-update-center-web:<version>`, or `easy-update-center/web:local` with `compose.build.yaml`. |
| `HTTP_PORT` | `8080` | Host port, or `address:port` (`127.0.0.1:8080`), published for the web container's HTTP listener. |
| `HTTPS_PORT` | `8443` | Host port, or `address:port` (`127.0.0.1:8443`), published for the web container's TLS listener. |

Changing `UC_UID` later: give both volumes to the new user once, then `docker compose up -d`. Every
service drops all Linux capabilities, so each command runs as root with just the two that changing
owners needs:

```sh
docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH \
  --entrypoint find uc /data -path /data/repo-adapter -prune -o -exec chown -h <uid>:<gid> '{}' +
docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH \
  --entrypoint chown repo-adapter -R <uid>:<gid> /data/repo-adapter
```

The first gives the data volume to the new user, skipping the repo-adapter's volume, which `uc`
mounts read-only; the second gives the repo-adapter's volume from the repo-adapter service.

## Several configurations

Two configurations on one host (a staging next to production) must not share a Compose project:
`compose.yaml` fixes `name: easy-update-center`, so a second env file, or a copy of this directory run
with plain commands, is the same project. It would replace the running deployment's containers and
use its volumes. Give each configuration its own project name, and `UC_ENV_FILE` alone does not
separate them either: web still reads Compose's `.env`. The simplest way is to put the project name,
`UC_ENV_FILE` and distinct ports in the configuration's own env file:

```bash
# .env.staging, besides the usual settings:
COMPOSE_PROJECT_NAME=staging
UC_ENV_FILE=.env.staging
HTTP_PORT=9080
HTTPS_PORT=9443
```

Then `--env-file` alone selects the configuration on every command:

```bash
docker compose --env-file .env.staging config --quiet
docker compose --env-file .env.staging up -d
docker compose --env-file .env.staging run --rm uc status
```

`COMPOSE_PROJECT_NAME` in an env file passed with `--env-file` renames the project. Without it in
the file, add `-p staging` to every command instead. The volumes (`uc-data`, the repository
adapter's) and networks are then named `staging_...` and are separate. Each configuration needs its
own `HTTP_PORT` and `HTTPS_PORT`. `./secrets`, `./config` and `./procurement` are folders of the
project directory and are shared by every project that runs from it; to keep them apart, put each
configuration in its own copy of the directory, with its own project name as well.

## Testing and evaluation

Not for production.

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `ALLOW_LOCAL_PUBLIC_URL` | `false` | generate | Allows a `localhost` or loopback `PUBLIC_URL`, which controllers on other hosts cannot reach. |
| `UC_EXTRA_ARGS` | empty | generate | Extra update-center2 arguments appended to every run, for example `--max-plugins 25` to cap a trial run. |

## Read by bundled software

These are read from the process environment by the JVM and by Python.

| Setting | Read by | Notes |
|---|---|---|
| `JAVA_TOOL_OPTIONS` | the JVM running update-center2 | Heap size (`-Xmx6g`) and the generator's outbound HTTP proxy (`-Dhttps.proxyHost=... -Dhttps.proxyPort=...`). See [Host sizing](../getting-started/requirements.md#host-sizing). |
| `HTTPS_PROXY`, `HTTP_PROXY` | Python in `uc`, `scheduler` and `repo-adapter` | The outbound proxy for everything the generator does not fetch itself: upstream resources, tool installers, listing queries. Behind a proxy, set it **and** the proxy flags in `JAVA_TOOL_OPTIONS`; neither covers the other. |
| `NO_PROXY` | Python in `uc`, `scheduler` and `repo-adapter` | Hosts to reach directly. With a proxy set, include `repo-adapter` (and your repository's host if it is internal), for example `NO_PROXY=repo-adapter,artifactory.corp.example`. |

## Procurement

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `PROCUREMENT_SOURCE_URL` | `https://repo.jenkins-ci.org/releases/` | procure | Where `uc procure stage` fetches files from, by Maven path, each checked against the `.sha1` beside it. Set it to reach repo.jenkins-ci.org through your own proxy repository. Air-gapped, downloads are refused unless it is set. Must not contain credentials or any `@`. See [Procure missing files](../how-to/procure-missing-files.md). |
