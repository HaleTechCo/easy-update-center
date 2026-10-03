# Configuration reference

Every setting the update center reads, its default in each mode, and which service reads it.

## How settings are read

Every setting can be set in two places, and most have a default:

- **The environment**: the file Compose loads as each service's `env_file`, `.env` in the project
  directory unless `UC_ENV_FILE` names another. Every service that runs the uc image (`uc`,
  `scheduler`, `repo-adapter` and `web-config`) reads the same file.
- **`config/uc.toml`**: an optional [TOML](https://toml.io) file, which Compose mounts at
  `/config/uc.toml` (`UC_CONFIG_FILE` names another path). `config/uc.example.toml` lists every
  setting. How to: [Configure with uc.toml](../how-to/configure-with-uc-toml.md).
- **The web container** reads neither: the one-shot `web-config` service works out its settings
  and writes them to a volume `web` reads at start, and a variable set in `web`'s own
  environment wins
  ([Where web gets its settings](../architecture/request-routing.md#where-web-gets-its-settings)).

A setting's value is the first of: a command-line option (where the command has one), the
environment, `uc.toml`, the default for the mode, the built-in default.

Run `uc config` to see every value and where it came from ([uc config](cli.md#uc-config)).

- **Names**: the `uc.toml` column gives each setting's key. The rule behind it: a variable whose
  name starts with one of the sections `generate`, `doctor`, `repo`, `download`, `resources`,
  `tls`, `procurement`, `pki`, `signing`, `github` or `uc` and an underscore lives in that section,
  under the rest of its name in lower case; every other variable is a top-level key in lower case.
  So `REPO_URL` is `url` under `[repo]`, and `PUBLIC_URL` is `public_url` at the top.
- **Empty values**: an empty variable means "not set", so `uc.toml` or the default applies,
  except for `POPULARITY_JSON_URL` and `TOOL_INSTALLERS_URL`, where an explicitly empty value
  turns the feature off. An empty string in `uc.toml` works the same way.
- **Types**: booleans accept `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`. In `uc.toml`,
  write numbers and booleans as TOML values (`retain_releases = 5`,
  `allow_local_public_url = true`) or as strings spelled as in the environment (`"5"`, `"yes"`).
- **Mistakes**: an invalid value stops the command that reads it with a message naming the
  setting. An unknown or misplaced key in `uc.toml` stops it too, naming the right key;
  environment variables cannot be checked that way.
- Credentials and keys are files, not settings: see [Secrets](secrets.md). Only their paths are
  settings.
- The settings in the [Compose](#compose) table, `COMPOSE_FILE` and `UC_CONFIG_FILE` can only be
  set in the environment: Compose and uc read them before `uc.toml`.

When each change takes effect: [When changes apply](#when-changes-apply).

In the tables, "generate" means `uc generate`, which the `scheduler` service runs on its schedule
and which you can run by hand; "doctor" means `uc doctor`.

## When changes apply

- Each `uc generate` and `uc doctor` run, scheduled or by hand, reads `config/uc.toml` afresh, so
  a change to it applies at the next run with no restart, except for the settings `uc config` marks
  `restart` (below).
- A container's environment is fixed when it is created, so any change in `.env`, to any setting,
  needs the services recreated. So does a change, wherever it is set, to the settings read when a
  service starts: the schedules (`GENERATE_SCHEDULE`, `DOCTOR_SCHEDULE`) when the scheduler
  starts, the web container's settings when `web-config` runs, and the repo-adapter's when it
  starts. Recreate the services:

  ```bash
  docker compose up -d --force-recreate      # or: make reload
  ```

  `make reload` checks the settings first and recreates nothing when they are invalid. A
  generation in progress restarts, as it always has when the scheduler is recreated.
- `uc config` marks `restart` the settings that are read when a service starts (the schedules,
  the web container's settings, the repo-adapter's, the proxy variables and the data and secret
  paths those use). They need `make reload` wherever they are set; the rest (`RETAIN_RELEASES`,
  `JAVA_TOOL_OPTIONS`, ...) apply at the next run when set in `config/uc.toml`, and need it too
  when set in `.env`. `uc config` ends with that command when the scheduler started with other
  values of the `restart` settings, or in another environment (`.env`), than the current ones.
- `PUBLIC_URL` and the download settings are written into each release: after changing them,
  also publish a new release with `docker compose run --rm uc generate`.

## Required

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `MODE` | `mode` | none | every uc-image service (`web-config` resolves it for web) | `online` or `airgapped`. It only sets defaults; every setting below can still be set explicitly. |
| `PUBLIC_URL` | `public_url` | none | every uc-image service (`web-config` resolves it for web) | The exact origin Jenkins uses, with no path, for example `https://jenkins-updates.example.com`. See [A hostname for PUBLIC_URL](../getting-started/requirements.md#a-hostname-for-public_url). |

## Mode defaults

| Setting | `online` | `airgapped` |
|---|---|---|
| `REPO_TYPE` | `artifactory` | `artifactory` |
| `REPO_URL` | `https://repo.jenkins-ci.org/` | required |
| `REPO_KEY` | `releases` | required |
| `REPO_ADAPTER` | `off` (always `on` for `artifact-keeper`) | `on` |
| `CORE_AGE_DAYS` | `400` | `0` |
| `DOWNLOAD_BACKEND` | `mirror` | `artifactory` |
| `DOWNLOAD_BACKEND_URL` | `https://get.jenkins.io` | `${REPO_URL}${REPO_KEY}`; Artifact Keeper: `${REPO_URL}maven/${REPO_KEY}` |
| `RESOURCES_SOURCE` | `github` | `bundled` |
| `POPULARITY_JSON_URL` | upstream's `latestNumbers.json` on raw.githubusercontent.com | empty (skipped) |
| `TOOL_INSTALLERS_URL` | `https://updates.jenkins.io/updates/` | empty (skipped) |

A missing trailing slash on `REPO_URL` is added, so `${REPO_URL}${REPO_KEY}` always has one between them.
`REPO_URL` and `REPO_KEY` default to repo.jenkins-ci.org only in online mode with
`REPO_TYPE=artifactory`; otherwise both are required. `DOWNLOAD_BACKEND_URL` follows
`DOWNLOAD_BACKEND`, not the mode: `mirror` means get.jenkins.io, `artifactory` means your repository.

## Source repository

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `REPO_TYPE` | `[repo] type` | `artifactory` | generate, doctor, repo-adapter, web-config | `artifactory` or `artifact-keeper`. Artifact Keeper is read only through the repo-adapter. |
| `REPO_URL` | `[repo] url` | see [Mode defaults](#mode-defaults) | generate, doctor, repo-adapter, web-config | Artifactory: the `/artifactory/` base URL. Artifact Keeper: the server's base URL. A trailing slash is optional. Must not contain credentials. |
| `REPO_API_URL` | `[repo] api_url` | `${REPO_URL}api/` | generate, doctor, repo-adapter | Artifactory's API base, for the listing query. Set it only when the API is served elsewhere. |
| `REPO_KEY` | `[repo] key` | see [Mode defaults](#mode-defaults) | generate, doctor, repo-adapter, web-config | The repository key (for Artifact Keeper, the key, not the display name). Letters, digits, `.`, `_` and `-` only. |

## repo-adapter

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `REPO_ADAPTER` | `[repo] adapter` | see [Mode defaults](#mode-defaults) | generate, doctor, repo-adapter | Whether the generator reads the repository through the [repo-adapter](../architecture/repo-adapter.md). `REPO_TYPE=artifact-keeper` with `REPO_ADAPTER=off` is refused. The service stays idle when off. |
| `REPO_ADAPTER_URL` | `[repo] adapter_url` | `http://repo-adapter:${REPO_ADAPTER_PORT}/artifactory/` | generate, doctor | Where `uc` finds the adapter; it follows `REPO_ADAPTER_PORT`. Change it only with a non-default Compose layout. |
| `REPO_ADAPTER_DIR` | `[repo] adapter_dir` | `/data/repo-adapter` | generate, doctor, repo-adapter | The adapter's token, cache and `report.json`. |
| `REPO_ADAPTER_PORT` | `[repo] adapter_port` | `8081` | repo-adapter | The port the repo-adapter listens on; `--port` overrides it. The default `REPO_ADAPTER_URL` follows it, and the `repo-adapter` healthcheck (`uc repo-adapter --healthcheck`) probes the port the running adapter bound; a change applies after `make reload`. Nothing else needs to change with it. Minimum 1. |
| `REPO_CA_FILE` | `[repo] ca_file` | none | repo-adapter, doctor | A PEM bundle added to the system bundle, for a repository behind a private CA. See [Repository behind a private CA](../getting-started/airgapped.md#repository-behind-a-private-ca). `uc doctor --push`, `--clean` and the downloads verify against it too. |
| `REPO_MAX_ARCHIVE_MB` | `[repo] max_archive_mb` | `512` | repo-adapter, doctor | Archives larger than this are skipped and reported. `uc doctor --download-dependencies` and `--download-war` download none larger either. Minimum 1. |

## Downloads

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `DOWNLOAD_BACKEND` | `[download] backend` | see [Mode defaults](#mode-defaults) | generate, web-config | `mirror` (get.jenkins.io layout) or `artifactory` (any Maven-layout repository, looked up through the release's download map). |
| `DOWNLOAD_BACKEND_URL` | `[download] backend_url` | see [Mode defaults](#mode-defaults) | web-config | Where `/download/...` requests are sent. Controllers must be able to read it without credentials unless `DOWNLOAD_ACCESS=proxy`. |
| `DOWNLOAD_ACCESS` | `[download] access` | `redirect` | web-config | `redirect` sends controllers to the backend; `proxy` streams the file through the web container. See [Proxy plugin downloads](../how-to/proxy-downloads.md). |
| `DOWNLOAD_PROXY_CA_FILE` | `[download] proxy_ca_file` | `/etc/ssl/certs/ca-certificates.crt` | web-config | Proxy mode only: the CA bundle the backend's certificate must verify against. |
| `DOWNLOAD_PROXY_AUTH_FILE` | `[download] proxy_auth_file` | `${SECRETS_DIR}/download_proxy_auth` | web-config | Proxy mode only: the file holding `username:password` for the backend. |

## Upstream data

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `RESOURCES_SOURCE` | `[resources] source` | see [Mode defaults](#mode-defaults) | generate | Where security warnings, suspensions and labels come from: `github` (upstream at `RESOURCES_REF`), `url` (a tarball at `RESOURCES_URL`), or `bundled` (the copy in the image). |
| `RESOURCES_REF` | `[resources] ref` | `master` | generate | The update-center2 branch, tag or commit to read with `RESOURCES_SOURCE=github`. |
| `RESOURCES_URL` | `[resources] url` | none | generate | Required with `RESOURCES_SOURCE=url`: a tarball containing `resources/`. |
| `RESOURCES_SHA256` | `[resources] sha256` | none | generate | With `RESOURCES_SOURCE=url`, the tarball must match this checksum. |
| `POPULARITY_JSON_URL` | `popularity_json_url` | see [Mode defaults](#mode-defaults) | generate | Plugin install counts. Set it empty to skip; popularity is then 0. |
| `TOOL_INSTALLERS_URL` | `tool_installers_url` | see [Mode defaults](#mode-defaults) | generate | A directory listing of the `updates/` tool-installer files. Set it empty to skip. |
| `STALE_AFTER_DAYS` | `stale_after_days` | `30` | generate | `status.json` marks the resources stale when they are older than this. |

See [Keep upstream data fresh](../how-to/keep-upstream-data-fresh.md).

## Generation

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `UC_ID` | `[uc] id` | `default` | generate | The update site id written into the metadata. It must match the id of the update site on your controllers. |
| `GENERATE_SCHEDULE` | `[generate] schedule` | `0 */6 * * *` | scheduler | Cron expression for regular generation. The scheduler also generates at once when no release is live. |
| `CORE_AGE_DAYS` | `core_age_days` | see [Mode defaults](#mode-defaults) | generate | Tiers are offered for cores released within this many days; `0` means every core a plugin requires. See [Core age](../architecture/modes.md#core-age). |
| `RETAIN_RELEASES` | `retain_releases` | `5` | generate | Releases kept for rollback. Minimum 1. |
| `MIN_PLUGINS` | `min_plugins` | `1` | generate | A release with fewer plugins is refused. |
| `MIN_PLUGIN_RATIO` | `min_plugin_ratio` | `0.9` | generate | A release with fewer than this share of the live release's plugins is refused, unless run with `uc generate --force`. Between 0 and 1. |

## Doctor

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `DOCTOR_SCHEDULE` | `[doctor] schedule` | empty: none | scheduler | Cron expression (or an `@macro`) for a read-only `uc doctor --record` run, which saves the report and puts its counts in `status.json`. It never stages, pushes or cleans. |
| `DOCTOR_FETCH` | `[doctor] fetch` | `false` | doctor | Download the POMs and manifests missing from the generator's cache, as `--fetch` does; `--no-fetch` turns it off for one run. |
| `DOCTOR_SINCE` | `[doctor] since` | empty: the oldest core in the repository | doctor | The floor for required cores, as `--since`. A Jenkins version such as `2.462.1`; anything else is refused when the settings load. |
| `DOCTOR_JSON` | `[doctor] json` | `false` | doctor | Print the report as JSON, as `--json`; `--no-json` turns it off for one run. |

## Signing

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `CERTIFICATE_MINIMUM_VALID_DAYS` | `certificate_minimum_valid_days` | `30` | generate | Generation stops when the signing certificate expires within this many days. Also passed to the generator. |
| `PKI_ORG` | `[pki] org` | `Jenkins Update Center` | pki | The organization in the subjects of the certificates `uc pki init` and `uc pki rotate` create; `--org` overrides it. |
| `PKI_ROOT_DAYS` | `[pki] root_days` | `3650` | pki | Lifetime of the root CA `uc pki init` creates, in days; `--root-days` overrides it. Minimum 1. |
| `PKI_SIGNING_DAYS` | `[pki] signing_days` | `365` | pki | Lifetime of the signing certificate `uc pki init` and `uc pki rotate` issue, in days; `--signing-days` and `--days` override it. Minimum 1. |

## Web and TLS

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `TLS_CERT_FILE` | `[tls] cert_file` | none | web-config | With `TLS_KEY_FILE`, enables HTTPS on port 8443. Both must be readable. See [Enable TLS](../how-to/enable-tls.md). |
| `TLS_KEY_FILE` | `[tls] key_file` | none | web-config | The private key for `TLS_CERT_FILE`. |

## Compose

These are read by `docker compose` itself, from the shell environment or from `.env` in the project
directory, not by the containers.

| Setting | Default | Notes |
|---|---|---|
| `UC_ENV_FILE` | `.env` | The file every service except `web` loads as `env_file`. Lets several configurations sit side by side. |
| `UC_IMAGE` | `jenkins-uc/uc:local` | The image for `uc`, `scheduler`, `repo-adapter` and `web-config`. |
| `WEB_IMAGE` | `jenkins-uc/web:local` | The image for `web`. |
| `HTTP_PORT` | `8080` | Host port published for the web container's HTTP listener. |
| `HTTPS_PORT` | `8443` | Host port published for the web container's TLS listener. |

## Testing and evaluation

Not for production.

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `ALLOW_LOCAL_PUBLIC_URL` | `allow_local_public_url` | `false` | generate | Allows a `localhost` or loopback `PUBLIC_URL`, which controllers on other hosts cannot reach. |
| `UC_EXTRA_ARGS` | `[uc] extra_args` | empty | generate | Extra update-center2 arguments appended to every run, for example `--max-plugins 25` to cap a trial run. |

## Read by bundled software

These are read from the process environment by the JVM and by Python. Set in `uc.toml` and not in
the environment, uc exports them to its own process when it starts, so the generator and uc's own
requests see them.

| Setting | uc.toml | Read by | Notes |
|---|---|---|---|
| `JAVA_TOOL_OPTIONS` | `java_tool_options` | the JVM running update-center2 | Heap size (`-Xmx6g`) and the generator's outbound HTTP proxy (`-Dhttps.proxyHost=... -Dhttps.proxyPort=...`). See [Host sizing](../getting-started/requirements.md#host-sizing). |
| `HTTPS_PROXY`, `HTTP_PROXY` | `https_proxy`, `http_proxy` | Python in `uc`, `scheduler` and `repo-adapter` | The outbound proxy for everything the generator does not fetch itself: upstream resources, tool installers, listing queries. Behind a proxy, set it **and** the proxy flags in `JAVA_TOOL_OPTIONS`; neither covers the other. |
| `NO_PROXY` | `no_proxy` | Python in `uc`, `scheduler` and `repo-adapter` | Hosts to reach directly. With a proxy set, include `repo-adapter` (and your repository's host if it is internal), for example `NO_PROXY=repo-adapter,artifactory.corp.example`. |
| `COMPOSE_FILE` | environment only | `docker compose`, from `.env` | The Compose files every plain `docker compose` command uses (on Windows, `;` between files). Set it whenever you use an override, in either mode, so later commands keep it: `compose.yaml:compose.airgapped.yaml` air-gapped, plus `compose.tls.yaml`, `compose.repo-ca.yaml` or `compose.proxy-ca.yaml` when you use them. |

## Procurement

| Setting | uc.toml | Default | Read by | Notes |
|---|---|---|---|---|
| `PROCUREMENT_SOURCE_URL` | `[procurement] source_url` | `https://repo.jenkins-ci.org/releases/` | doctor | Where `uc doctor --download-poms`, `--download-dependencies` and `--download-war` fetch files from, by Maven path, each checked against the `.sha1` beside it. Set it to reach repo.jenkins-ci.org through your own proxy repository. Air-gapped, downloads are refused unless it is set. Must not contain credentials. See [Procure missing files](../how-to/procure-missing-files.md). |

## Internal

Set by the image or derived from `SECRETS_DIR`; change them only to run `uc` outside the containers
or with a non-default layout.

| Setting | uc.toml | Default | Notes |
|---|---|---|---|
| `UC_DATA_DIR` | `[uc] data_dir` | `/data` | The data volume. |
| `UC_OVERLAY_DIR` | `[uc] overlay_dir` | `/config/overlay` | The [overlay](../how-to/block-plugins-and-add-warnings.md) directory. |
| `UC_APP_DIR` | `[uc] app_dir` | `/opt/uc` | The generator, templates and bundled resources in the image. |
| `SECRETS_DIR` | `secrets_dir` | `/run/secrets` | Where the secret files below are read from. |
| `SIGNING_KEY_FILE` | `[signing] key_file` | `${SECRETS_DIR}/update-center.key` | |
| `SIGNING_CERT_FILE` | `[signing] cert_file` | `${SECRETS_DIR}/update-center.cert` | |
| `ROOT_CA_FILE` | `root_ca_file` | `${SECRETS_DIR}/root-ca.crt` | |
| `REPO_USERNAME_FILE` | `[repo] username_file` | `${SECRETS_DIR}/repo_username` | |
| `REPO_PASSWORD_FILE` | `[repo] password_file` | `${SECRETS_DIR}/repo_password` | |
| `GITHUB_USERNAME_FILE` | `[github] username_file` | `${SECRETS_DIR}/github_username` | |
| `GITHUB_TOKEN_FILE` | `[github] token_file` | `${SECRETS_DIR}/github_token` | |
| `REPO_PUSH_AUTH_FILE` | `[repo] push_auth_file` | `${SECRETS_DIR}/repo_push_auth` | |
| `UC_CONFIG_FILE` | environment only | `/config/uc.toml` | The settings file. A missing file is fine: every setting then comes from the environment and the defaults. |
