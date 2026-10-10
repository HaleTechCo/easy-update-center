# Configuration reference

Every setting the update center reads, its default in each mode, and which service reads it.

## How settings are read

Every setting is an environment variable, set in `.env` in the project directory: the file Compose
loads as each service's `env_file` (`UC_ENV_FILE` names another). Every service that runs the uc
image (`uc`, `scheduler`, `repo-adapter` and the on-demand `setup`) reads the same
file. `.env.online.example` and `.env.airgapped.example` list every setting.

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
- Credentials and keys are files, not settings: see [Secrets](secrets.md). Only their paths are
  settings.

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

A generation in progress restarts when the scheduler is recreated. `PUBLIC_URL` and the download
settings are written into each release: after changing them, also publish a new release with
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
| `REPO_API_URL` | `${REPO_URL}api/` | generate, doctor, procure, repo-adapter | Artifactory's API base, for the listing query. Set it only when the API is served elsewhere. |
| `REPO_KEY` | see [Mode defaults](#mode-defaults) | generate, doctor, procure, repo-adapter | The repository key (for Artifact Keeper, the key, not the display name). Letters, digits, `.`, `_` and `-` only. |

## repo-adapter

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `REPO_ADAPTER` | see [Mode defaults](#mode-defaults) | generate, doctor, procure, repo-adapter | Whether the generator reads the repository through the [repo-adapter](../architecture/repo-adapter.md). `REPO_TYPE=artifact-keeper` with `REPO_ADAPTER=off` is refused. The service stays idle when off. |
| `REPO_ADAPTER_URL` | `http://repo-adapter:${REPO_ADAPTER_PORT}/artifactory/` | generate, doctor, procure | Where `uc` finds the adapter; it follows `REPO_ADAPTER_PORT`. Change it only with a non-default Compose layout. |
| `REPO_ADAPTER_DIR` | `/data/repo-adapter` | generate, doctor, procure, repo-adapter | The adapter's token, cache and `report.json`. |
| `REPO_ADAPTER_PORT` | `8081` | generate, doctor, procure, repo-adapter | The port the repo-adapter listens on; `--port` overrides it. The default `REPO_ADAPTER_URL`, which generate and doctor use, follows it, and the `repo-adapter` healthcheck (`uc repo-adapter --healthcheck`) probes the port the running adapter bound; a change applies after `docker compose up -d`. Nothing else needs to change with it. Minimum 1. |
| `REPO_CA_FILE` | none | repo-adapter, procure | A PEM bundle added to the system bundle, for a repository behind a private CA. See [Repository behind a private CA](../getting-started/airgapped.md#repository-behind-a-private-ca): set it in `.env`, and mount the file with `compose.repo-ca.yaml`. `uc procure push` and `clean`, and the downloads of `uc procure stage`, verify against it too. |
| `REPO_MAX_ARCHIVE_MB` | `512` | repo-adapter, procure | Archives larger than this are skipped and reported. `uc procure stage --dependencies` and `--war` download none larger either. Minimum 1. |

## Downloads

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `DOWNLOAD_BACKEND` | see [Mode defaults](#mode-defaults) | generate, web | `mirror` (get.jenkins.io layout) or `artifactory` (any Maven-layout repository, looked up through the release's download map). |
| `DOWNLOAD_BACKEND_URL` | see [Mode defaults](#mode-defaults) | web (every uc-image service checks it) | Where `/download/...` requests are sent. It must not contain credentials or any `@` (uc and web refuse them; for `DOWNLOAD_ACCESS=proxy` put them in `secrets/download_proxy_auth`). Controllers must be able to read it without credentials unless `DOWNLOAD_ACCESS=proxy`. |
| `DOWNLOAD_ACCESS` | `redirect` | web | `redirect` sends controllers to the backend; `proxy` streams the file through the web container. See [Proxy plugin downloads](../how-to/proxy-downloads.md). |
| `DOWNLOAD_PROXY_CA_FILE` | `/etc/ssl/certs/ca-certificates.crt` | web | Proxy mode only: the CA bundle the backend's certificate must verify against. |
| `DOWNLOAD_PROXY_AUTH_FILE` | `${SECRETS_DIR}/download_proxy_auth` | web | Proxy mode only: the file holding `username:password` for the backend. |

## Upstream data

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `RESOURCES_SOURCE` | see [Mode defaults](#mode-defaults) | generate | Where security warnings, suspensions and labels come from: `github` (upstream at `RESOURCES_REF`), `url` (a tarball at `RESOURCES_URL`), or `bundled` (the copy in the image). |
| `RESOURCES_REF` | `master` | generate | The update-center2 branch, tag or commit to read with `RESOURCES_SOURCE=github`. |
| `RESOURCES_URL` | none | generate | Required with `RESOURCES_SOURCE=url`: a tarball containing `resources/`. |
| `RESOURCES_SHA256` | none | generate | With `RESOURCES_SOURCE=url`, the tarball must match this checksum. |
| `POPULARITY_JSON_URL` | see [Mode defaults](#mode-defaults) | generate | Plugin install counts. Set it empty to skip; popularity is then 0. |
| `TOOL_INSTALLERS_URL` | see [Mode defaults](#mode-defaults) | generate | A directory listing of the `updates/` tool-installer files. Set it empty to skip. |
| `STALE_AFTER_DAYS` | `30` | generate | `status.json` marks the resources stale when they are older than this. |

See [Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md).

## Generation

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `UC_ID` | `default` | generate | The update site id written into the metadata. It must match the id of the update site on your controllers. |
| `GENERATE_SCHEDULE` | `0 */6 * * *` | scheduler | Cron expression for regular generation. The scheduler also generates at once when no release is live. |
| `CORE_AGE_DAYS` | see [Mode defaults](#mode-defaults) | generate | Tiers are offered for cores released within this many days; `0` means every core a plugin requires. See [Core age](../architecture/modes.md#core-age). |
| `RETAIN_RELEASES` | `5` | generate | Releases kept for rollback. Minimum 1. |
| `MIN_PLUGINS` | `1` | generate | A release with fewer plugins is refused. |
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
| `PKI_ORG` | `Easy Update Center` | pki | The organization in the subjects of the certificates `uc pki init` and `uc pki rotate` create; `--org` overrides it. |
| `PKI_ROOT_DAYS` | `3650` | pki | Lifetime of the root CA `uc pki init` creates, in days; `--root-days` overrides it. Minimum 1. |
| `PKI_SIGNING_DAYS` | `365` | pki | Lifetime of the signing certificate `uc pki init` and `uc pki rotate` issue, in days; `--signing-days` and `--days` override it. Minimum 1. |

## Web and TLS

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `TLS_CERT_FILE` | none | web | With `TLS_KEY_FILE`, enables HTTPS on port 8443. Both must be readable. See [Enable TLS](../how-to/enable-tls.md). |
| `TLS_KEY_FILE` | none | web | The private key for `TLS_CERT_FILE`. |

## Compose

These are read by `docker compose` itself, from the shell environment or from `.env` in the project
directory, not by the containers. The `make` targets that run your stack (`setup`, `secrets` and
its aliases, `pki-*`, `procure-*`) leave `UC_IMAGE` and `WEB_IMAGE` to `.env` too, unless
you set them on make's command line or in the shell. When the uc image they resolve is missing, they
build it if it is `easy-update-center/uc:local` and pull it otherwise. `make build` does not read `.env`,
and tags the images it builds with these defaults unless you pass `UC_IMAGE=...` or `WEB_IMAGE=...`
to it.

| Setting | Default | Notes |
|---|---|---|
| `UC_ENV_FILE` | `.env` | The file every uc-image service loads as `env_file`; web takes its variables from Compose's own `.env` (or `--env-file`). Lets several configurations sit side by side. |
| `UC_IMAGE` | `easy-update-center/uc:local` | The image for `uc`, `scheduler`, `repo-adapter` and `setup`. |
| `WEB_IMAGE` | `easy-update-center/web:local` | The image for `web`. |
| `HTTP_PORT` | `8080` | Host port published for the web container's HTTP listener. |
| `HTTPS_PORT` | `8443` | Host port published for the web container's TLS listener. |

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
| `COMPOSE_FILE` | `docker compose`, from `.env` | The Compose files every plain `docker compose` command uses (on Windows, `;` between files). Set it whenever you use an override, in either mode, so later commands keep it: `compose.yaml:compose.airgapped.yaml` air-gapped, plus `compose.tls.yaml`, `compose.repo-ca.yaml` or `compose.proxy-ca.yaml` when you use them. |

## Procurement

| Setting | Default | Read by | Notes |
|---|---|---|---|
| `PROCUREMENT_SOURCE_URL` | `https://repo.jenkins-ci.org/releases/` | procure | Where `uc procure stage` fetches files from, by Maven path, each checked against the `.sha1` beside it. Set it to reach repo.jenkins-ci.org through your own proxy repository. Air-gapped, downloads are refused unless it is set. Must not contain credentials or any `@`. See [Procure missing files](../how-to/procure-missing-files.md). |
| `PROCUREMENT_STAGING_DIR` | `/procurement` | procure | The staging folder `uc procure stage` fills and the other `uc procure` commands read (`--staging`), inside the `uc` container. `compose.yaml` mounts `./procurement` there, and the folder must stay on that mount: `/procurement` or a folder in it. `UC_DATA_DIR`, or a folder in it, is refused: the other services mount the data volume. `--staging` is held to the same rule. |

## Internal

Set by the image or derived from `SECRETS_DIR`; change them only to run `uc` outside the containers
or with a non-default layout.

| Setting | Default | Notes |
|---|---|---|
| `UC_DATA_DIR` | `/data` | The data volume. |
| `UC_OVERLAY_DIR` | `/config/overlay` | The [overlay](../how-to/block-plugins-and-add-warnings.md) directory. |
| `UC_APP_DIR` | `/opt/uc` | The generator, templates and bundled resources in the image. |
| `SECRETS_DIR` | `/run/secrets` | Where the secret files below are read from. |
| `SIGNING_KEY_FILE` | `${SECRETS_DIR}/update-center.key` | |
| `SIGNING_CERT_FILE` | `${SECRETS_DIR}/update-center.cert` | |
| `ROOT_CA_FILE` | `${SECRETS_DIR}/root-ca.crt` | |
| `REPO_USERNAME_FILE` | `${SECRETS_DIR}/repo_username` | |
| `REPO_PASSWORD_FILE` | `${SECRETS_DIR}/repo_password` | |
| `GITHUB_USERNAME_FILE` | `${SECRETS_DIR}/github_username` | |
| `GITHUB_TOKEN_FILE` | `${SECRETS_DIR}/github_token` | |
| `REPO_PUSH_AUTH_FILE` | `${SECRETS_DIR}/repo_push_auth` | |
