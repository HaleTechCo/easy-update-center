# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions will follow semantic versioning
once releases are published.

## Unreleased

### Changed

- `PUBLIC_URL`, `REPO_URL`, `DOWNLOAD_BACKEND_URL` and `PROCUREMENT_SOURCE_URL` refuse any `@`
  (`uc: error: <setting> must not contain credentials or an @`), so none of them can carry
  credentials, not even a password holding a `#`, `?` or `/`, which a URL parser takes for the end
  of the host. A `DOWNLOAD_BACKEND_URL` with credentials was accepted before; the web container
  refuses any `@` in it too, with the same message.
- The web container logs what it serves (`entrypoint: serving <PUBLIC_URL>; downloads: ...`) before
  waiting for the first release. `uc config --web-env --out` writes through a unique temporary file
  and reports a failure as `uc: error: cannot write <path>: <reason>`.
- The web container no longer reads `.env`: the one-shot `web-config` service works out its settings
  and writes them to a volume `web` reads at start. A variable set on `web` itself, as the TLS and
  proxy-CA overrides do, still wins. Without that file (the web image run on its own) `web` takes
  resolved values from its environment (`DOWNLOAD_BACKEND`, `DOWNLOAD_BACKEND_URL`, ...) and no
  longer works them out from `MODE` and `REPO_*`. `web-config` checks only the settings the web
  container uses, so an invalid setting web does not use no longer stops it; a uc.toml that is not
  UTF-8, or cannot be read, is reported as such.
- The private-CA recipe (docs/getting-started/airgapped.md) sets `REPO_CA_FILE` in `.env` (or
  `[repo] ca_file` in `config/uc.toml`), and `compose.repo-ca.yaml` only mounts the bundle. Set
  under `environment:` on two services, as the recipe had it, it made `uc config` end with the
  `make reload` note every time: if you followed it, move the variable to `.env`.
- `SIGNING_CERT_DAYS`, which nothing read, is replaced by `PKI_SIGNING_DAYS`, which `uc pki` reads.
- Compose mounts all of `./config` at `/config`, in the repo-adapter too; it mounted only
  `config/overlay/` before.
- `DOWNLOAD_PROXY_AUTH_FILE` defaults to `${SECRETS_DIR}/download_proxy_auth`, as the other secret
  files do.
- `make pki-init`, `make pki-rotate`, `make secrets-placeholders` and `make secrets-permissions` run
  a new `setup` Compose service (root, no network, no Compose secrets; mounts `./secrets`
  read-write, `./config` and `scripts/secrets-permissions.sh`) instead of `docker run`. It reads
  `.env`, or the file `UC_ENV_FILE` names, as the other services do, so quotes in values are handled
  the same way and `PKI_*` settings apply to `uc pki`. The file must exist, or Compose stops.
- `uc pki rotate` (`make pki-rotate`) first checks that the root key and `root-ca.crt` can be read
  and belong together, and that `update-center.key` and `update-center.cert` are both there or both
  missing, then issues the new pair in a temporary directory inside the directory it rotates, and
  only then keeps the previous pair as `*.old` and moves the new one in. When a check, a step or a
  rename fails, it moves back what it moved, so the directory is left as it was. Before, a missing or
  wrong root key left a new key and an empty `update-center.cert`, and running it again moved those
  over the `*.old` pair. The error names the file it cannot use, the root key under the new
  `--root-display-dir`: `make pki-rotate` passes `ROOT`, so it names `<ROOT>/root-ca.key`. An
  encrypted root key is refused without a passphrase prompt, with a message to decrypt it to a
  temporary file for the rotation (docs/how-to/rotate-signing-certificate.md); before, openssl asked
  for the passphrase twice, or without a terminal called the key unreadable. `uc pki init`
  (`make pki-init`) likewise creates the PKI in a temporary directory and moves it in once complete,
  so a failed run leaves nothing for the next one to refuse. A directory either cannot write in is
  reported as `uc: error: cannot write in <dir>: <reason>` instead of a traceback.
- `secrets/` stays owned by you on Linux. `make pki-init`, `make secrets-placeholders` and the new
  `make pki-rotate` and `make secrets-permissions` let each container read its own files through its
  group (uc 10001, web 33) instead of handing the whole directory to uid 10001, so no step needs
  `sudo` and credentials can be written before or after the keys. `make secrets-permissions` also
  repairs a `secrets/` set up the old way.
- `make reload`, `make pki-init`, `make pki-rotate`, `make secrets-placeholders`,
  `make secrets-permissions` and the `make procure-*` targets run the images `.env` names in
  `UC_IMAGE` and `WEB_IMAGE`: they no longer hand Compose make's own `jenkins-uc/*:local` defaults,
  which beat `.env`. A value given on make's command line or in the shell still wins. They build or
  pull that uc image only when it is missing, in a step of its own: they build it if it is
  `jenkins-uc/uc:local` and pull any other, so an offline or air-gapped host that loaded pre-built
  images builds and pulls nothing.
- The `docker compose run`s of those targets no longer print Compose's `Container ... Creating` and
  `Created` lines around the command's output (`COMPOSE_PROGRESS=quiet`; older Compose releases
  ignore it).
- `uc doctor` gives the repo.jenkins-ci.org URL of the real `.pom` for every POM the repo-adapter
  derived from an archive or ignored as unusable, not only for plugins with no POM at all. In the
  adapter's `report.json` and `uc doctor --json`, `derived_poms` entries are objects (`gav`, `url`)
  instead of strings, and `replaced_poms` entries gain a `url`.

### Added

- `config/uc.toml`: every setting can be set there as well as in `.env`, whose variables win.
  `config/uc.example.toml` and both `.env` examples list every setting; they are generated from one
  settings registry (`make examples`).
- `uc config` shows each setting's value and where it came from; `--check` validates, `--example`
  prints an example file, `--web-env` the web container's settings. Credentials inside a URL setting
  (everything before its last `@`, such as a proxy URL's user:password) show as `***`, in text and
  JSON, including when the configuration is invalid. Settings read only when a service starts are
  marked `restart` (a `"restart"` field in `--json`), and the note to run `make reload` appears
  when one of them changed or a setting in `.env` did (an empty value counts as unset, except for
  the settings where empty means off), not for a `config/uc.toml` change to a setting every run
  re-reads, such as `RETAIN_RELEASES`. The scheduler records the settings it started with in
  `/data/scheduler.json` for that note; when it cannot write the file, it logs a warning and
  carries on, and `uc config` cannot tell when a restart is needed.
- The scheduler can run a read-only `uc doctor --record` on `DOCTOR_SCHEDULE`. `uc doctor --record`
  saves the report as `/data/doctor/report.json` and its counts in `status.json`, with the new
  `doctor.checked_at`, `doctor.missing_cores`, `doctor.cores_unchecked` (plugin releases whose
  manifest was unavailable for the core check: above 0, the counts are incomplete) and
  `doctor.recorded_at` (when the last report was saved). Generations keep `missing_cores`,
  `cores_unchecked` and `recorded_at`, which only the report works out. Overlapping
  `uc doctor --record` runs wait for each other (`doctor/.record.lock`).
- Settings for what were command-line options only: `DOCTOR_FETCH`, `DOCTOR_SINCE`, `DOCTOR_JSON`,
  `PKI_ORG`, `PKI_ROOT_DAYS`, `PKI_SIGNING_DAYS` and `REPO_ADAPTER_PORT`. `--no-fetch` and
  `--no-json` switch a configured default off for one run.
  `REPO_ADAPTER_PORT` also sets the default `REPO_ADAPTER_URL`, and the repo-adapter's healthcheck
  (the new `uc repo-adapter --healthcheck`, which reads no settings) probes the port the running
  adapter bound, so changing the port needs no other edit than `make reload`. `DOCTOR_SINCE` is
  checked when the settings load: it must be a Jenkins version such as
  `2.462.1`.
- The generated example files give the remaining guidance per mode: how to use your own repository
  (online), what `CORE_AGE_DAYS=400` means, procured copies of the popularity and tool-installer
  data, and `COMPOSE_FILE` for air-gapped. A commented line there is not set: it shows the default
  or an example value, and the comment above it says what applies when it is unset.
- A URL setting that `uc` cannot parse (for example `PUBLIC_URL=https://[uc.example.com`, or a port
  that is not a number) is reported as
  `uc: error: <setting> is not a valid URL: its host or port cannot be read` when the settings
  load (for the proxies, with a hint to percent-encode `# ? / [ ] @` in a user name or password),
  so `uc config --check` catches it, instead of a traceback; the message never quotes the value.
  Nor does the one for a URL setting given in `config/uc.toml` as something other than a string,
  which names only its TOML type (`must be a string (got an array)`). Every URL setting is parsed:
  `PUBLIC_URL`, `REPO_URL`, `REPO_API_URL`, `REPO_ADAPTER_URL`, `DOWNLOAD_BACKEND_URL`,
  `PROCUREMENT_SOURCE_URL`, `RESOURCES_URL`, `POPULARITY_JSON_URL` and `TOOL_INSTALLERS_URL`.
  `HTTPS_PROXY` and `HTTP_PROXY` are checked as Python's urllib reads them: they may carry a user
  name and password, which may hold `#`, `?` or `/`, and only the host and port urllib connects to,
  after the last `@`, must be readable. urllib reads a `?` or `#` after them, or a `/` with no `//`
  before it, as part of the host and port, so `http://proxy:3128?x` and `http://proxy?x` are
  refused, as is a host in brackets that is not an IPv6 address (`[v1.x]`). A single `/` where `//`
  belongs (`http:/proxy:3128`, `/proxy:3128`), which urllib refuses, and an `@`, a `/` and another
  `@` in that order after the `//` (`http://user:secret@a/b@proxy:3128`), where urllib takes the
  host from before that `/`, are reported as
  `uc: error: <setting> is not a valid URL: write it as scheme://host:port`, with the same hint.
- `uc generate --wait` waits for a running generation or rollback instead of exiting 75; scheduled
  generations use it.
- `make reload` checks the settings, then recreates the services so they read them; with invalid
  settings it recreates nothing.
- `make pki-init OUT=<dir>` creates a new PKI in `<dir>` instead of `secrets/`, through the `setup`
  service as `make pki-init` does. Replacing the root CA (docs/how-to/rotate-signing-certificate.md)
  uses it, with `newpki/`, which git ignores, instead of `docker run`. `OUT`, like `ROOT` for
  `make pki-rotate`, counts only on make's command line, and either may hold spaces, but not quotes,
  `$` or backticks; both targets check that, and that `ROOT` is given, before they build or pull an
  image or create anything. `make pki-init` and `make pki-rotate` pass the new `--display-dir` of
  `uc pki init` and `uc pki rotate`, the directory `--out` or `--dir` is on the host, so their
  messages name `secrets/...` or `<dir>/...`, not the container's `/pki/...` or `/out/...`.
- Procurement with `uc doctor`, through a staging folder, `./procurement/`, whose `procurement.json`
  records each file's source, sha1 and reason for review:
  - `--download-poms`, `--download-dependencies` and `--download-war VERSION|required` stage the
    real POMs, the missing required dependencies (transitively, each with its POM) and Jenkins cores
    from repo.jenkins-ci.org (`PROCUREMENT_SOURCE_URL`). `make procure-download` wraps them.
  - `--stage-local` records files placed in the folder by hand, after the checks a download gets: a
    plugin's manifest coordinates and a core's `Jenkins-Version` must match the path. It names the
    Maven path a misplaced file belongs at, and `--fix` moves it there.
  - `--push` (`make procure-push`) uploads exactly the listed files with the new
    `secrets/repo_push_auth`, never overwriting. It reports files in the folder that
    `procurement.json` does not list, and the repository's reason when it refuses an upload (with a
    pointer to Artifact Keeper's storage garbage collection for its 409 on a deleted release). On
    Artifact Keeper, it records a file that an upload cut off by the request timeout left stored but
    unrecorded, when the stored bytes are the staged file's.
  - `--clean` (`make procure-clean`) deletes the staged files the repository already holds with the
    same content, with their entries, so the folder keeps only what still needs pushing.
  - The report lists the cores plugins require that the repository lacks, from the oldest core it
    holds (or `--since`), and names the oldest one below that floor with the `--since` that stages
    it.
  - After upgrading, run `make secrets-placeholders` once so Compose finds `secrets/repo_push_auth`.
- Third-party notices in the uc image, `/opt/uc/licenses/`: update-center2's MIT license with the
  Jenkins project's governance statement, every bundled Java library with its license (generated
  by the build, which fails on a library with no known license), and the license texts.
- A self-hosted Jenkins update center in containers: `uc` (generation and tools), `web` (Apache),
  `scheduler` and `repo-adapter`, run with Docker Compose.
- Online mode (upstream data fetched live; plugins from repo.jenkins-ci.org and downloads from
  get.jenkins.io by default, or from your own Artifactory or Artifact Keeper) and air-gapped mode
  (nothing fetched from the internet; plugins and downloads from your Artifactory or Artifact
  Keeper).
- Version routing to tiered sites, every upstream JSON file, and download index pages, generated by
  the pinned update-center2 3.18.4 with two small patches.
- Metadata signed with your own PKI (`uc pki init | rotate | show`).
- Atomic publishing with validation, five retained releases and `uc rollback`.
- `uc doctor` and a public `status.json`.
- The repo-adapter: Artifact Keeper support, and POMs, descriptions and release dates filled in
  from inside each plugin archive.
- An overlay for your own blocklist and security warnings.
- TLS in the web container, and a download proxy mode for repositories controllers cannot read.
- Unit, routing and end-to-end tests; pre-commit lint tooling; this documentation.
