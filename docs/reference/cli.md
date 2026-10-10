# uc command reference

Every uc command, its options and its exit codes.

## Running uc

`uc` is the entrypoint of the uc image. Run one-shot commands through Compose's `uc` service, which
has the same volume, secrets and settings as the scheduler:

```bash
docker compose run --rm uc <command> [options]
```

Air-gapped, set `COMPOSE_FILE` in `.env` so plain `docker compose` uses the air-gapped override
(Compose reads it from there; on Windows, separate the files with `;`):

```bash
COMPOSE_FILE=compose.yaml:compose.airgapped.yaml
```

Without it, add `-f compose.yaml -f compose.airgapped.yaml` to every command.

- The `uc` service sits in the `tools` profile, so `docker compose up` does not start it; `run`
  does. Running it starts the `repo-adapter` first.
- With no command, the image runs `uc status`.
- `uc --version` prints the release the image was built as, such as `uc 0.1.0`, or `uc dev` for an
  image built from source.
- Every command except `uc pki` reads the [configuration](configuration.md) and fails at once with
  `uc: error: ...` when it is invalid.

Exit codes shared by every command:

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | An expected failure; the message is printed as `uc: error: ...`. For `uc doctor`, also "something in the repository will be dropped". |
| `2` | Invalid command line. For `uc procure stage`, also "nothing selected to stage". |
| `75` | Another `uc generate` or `uc rollback` holds the run lock (`/data/.lock`). Try again later. |

## uc generate

```bash
docker compose run --rm uc generate [--force] [--wait]
```

Builds, validates and publishes one release: the whole [generation pipeline](../architecture/generation-pipeline.md).
On success it prints `published release <id>` and exits 0; the new release is live. On failure the
live release is unchanged, `status.json` records the failed run, and the full error is in the output.

| Option | Meaning |
|---|---|
| `--force` | Skip the plugin-count ratio check (`MIN_PLUGIN_RATIO`) for this run, to accept a deliberate shrink of the catalog. |
| `--wait` | Wait for a running generation or rollback to finish instead of exiting 75. The scheduler's runs use it. |

The scheduler runs the same command on `GENERATE_SCHEDULE`.

## uc doctor

```bash
docker compose run --rm uc doctor [--fetch | --no-fetch] [--json] [--since VERSION] [--record]
```

Reports what the generator will and will not see in the source repository, without generating. It
changes neither the repository nor the staging folder: [uc procure](#uc-procure) stages, pushes and
cleans up. It runs the same listing query the generator runs and prints:

- the source, the number of plugins with a POM, and the Jenkins cores with a POM (no cores means the
  site offers no core and no tiers);
- **Dropped by the generator**: plugins or cores that will not be published, and why:
  - no POM in the repository or the archive (with the repo.jenkins-ci.org URL of the real `.pom`
    to procure),
  - misplaced (stored under a different path than its manifest names; with the path to move it to),
  - skipped (for example larger than `REPO_MAX_ARCHIVE_MB`),
  - `.hpi`/`.jpi` without a `.pom` (repo-adapter off), `jenkins-war` without a `.pom`, files not in
    Maven layout;
- with the repo-adapter on, what it filled in: POMs derived from archives and `.pom` files it
  ignored as unusable, each with the repo.jenkins-ci.org URL of the real `.pom` (optional: the
  derived one works), and archives whose release date came from the repository;
- stub POMs (no `<name>`: display names, descriptions and SCM links will be degraded);
- dependency problems: plugins whose required plugins are absent, or present only in lower
  versions;
- cores plugin releases require that the repository lacks, at or above the oldest core it holds (or
  `--since`), each with its repo.jenkins-ci.org URL; not for the public repository. Older ones are
  counted, with the oldest of them and the `--since` that stages them.

The repo.jenkins-ci.org URLs are where public plugins and cores are published; a plugin that never
was, such as an internal one, has no `.pom` there.

| Option | Meaning |
|---|---|
| `--fetch`, `--no-fetch` | Download the POMs and manifests missing from the generator's cache, so dependencies and required cores can be checked before the first generation. Without it, unchecked items are counted and reported. The default is `DOCTOR_FETCH` (off). |
| `--json` | Print the full report as JSON. The `next:` line, and the line `--record` prints, then go to standard error, so standard output stays valid JSON. |
| `--since VERSION` | The floor for required cores, instead of the oldest core in the repository. The default is `DOCTOR_SINCE`. |
| `--record` | Also save the report as the latest: in full as `/data/doctor/report.json` (not served), and its counts in `status.json`'s `doctor` block with `checked_at` and `recorded_at`. The scheduler runs it on `DOCTOR_SCHEDULE`. |

Exits 1 when anything will be dropped or a dependency is missing, else 0; required cores the
repository lacks are reported but do not change the exit code. When the report finds something
`uc procure stage` can fetch (POMs to procure, missing dependencies, missing required cores), it ends
with that step, naming the options for what it found and the settings that choose them for every
run:

```text
next: uc procure stage --poms --dependencies --war required
```

Given `--since`, the line carries it to the required cores: `--war required --since 2.462.1`, or
`DOCTOR_SINCE=2.462.1` among the settings. Air-gapped without `PROCUREMENT_SOURCE_URL`, where
`uc procure stage` refuses to download, the line names the other ways in instead: place the files in
the staging folder and record them, or set the source, then stage:

```text
next: place the files the report lists in ./procurement and run uc procure record, or set PROCUREMENT_SOURCE_URL to a copy of repo.jenkins-ci.org the update center can reach, then uc procure stage --poms --dependencies --war required
```

What the repository must contain:
[Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

## uc procure

```bash
docker compose run --rm uc procure stage [--poms] [--dependencies] [--war VERSION|required ...] [--since VERSION] [--fetch | --no-fetch] [--staging DIR]
docker compose run --rm uc procure record [--fix] [--staging DIR]
docker compose run --rm uc procure push [--dry-run] [--staging DIR]
docker compose run --rm uc procure clean [--dry-run] [--staging DIR]
```

One command per stage of procurement: `stage` fetches what `uc doctor` finds missing into a staging
folder for review, `record` adds the files you place there by hand, `push` uploads exactly the files
the folder's `procurement.json` lists to the repository, and `clean` deletes the staged copies the
repository then holds. Nothing reaches the repository without `push`. How to use them, in order:
[Procure missing POMs, dependencies and cores](../how-to/procure-missing-files.md); with an
approval before the push:
[Run procurement from a pipeline](../how-to/run-procurement-from-a-pipeline.md).

The staging folder is `./procurement` on the host, which the `uc` service mounts at `/procurement`
(`PROCUREMENT_STAGING_DIR`). Every command takes `--staging DIR` to use another folder inside the
`uc` container for one run; like the setting, it must not be `UC_DATA_DIR` or a folder in it.
On Linux, the uc containers (uid 10001) can write `./procurement` once
[the setup service](#the-setup-service) has shared it with them
(`docker compose run --rm setup procurement`, part of `make setup`). Each command has a `make`
shortcut, which passes options through `ARGS=`. For `stage`, `record` and `clean`, it also hands the
folder to uid 10001 for the run and back to you after, so it works where the folder cannot be
shared:

| Command | `make` shortcut |
|---|---|
| `uc procure stage` | `make procure-stage ARGS="--poms --dependencies"` |
| `uc procure record` | `make procure-record ARGS=--fix` |
| `uc procure push` | `make procure-push ARGS=--dry-run` |
| `uc procure clean` | `make procure-clean ARGS=--dry-run` |

### uc procure stage

Prints `uc doctor`'s report, then stages the missing files you chose with the options below: each is
downloaded from `PROCUREMENT_SOURCE_URL` (repo.jenkins-ci.org by default), checked against the
`.sha1` published beside it, saved in the folder at its Maven path and listed in `procurement.json`
with its source URL, sha1, size and reason. Staged files are readable by everyone and writable by
their group (mode `664`), so you can edit them under the folder's ACLs. One line per file says what
happened (`staged`, `kept`: already staged and intact, `FAILED`: with the reason), then a summary.
Running it again keeps what is already staged and downloads only what is missing or changed.
Air-gapped, it is refused unless `PROCUREMENT_SOURCE_URL` names a copy the update center can reach.

| Option | Meaning |
|---|---|
| `--poms` | Stage the real `.pom` of every plugin whose POM is missing, derived from its archive, or unusable. |
| `--dependencies` | Stage the required dependencies the repository lacks or holds only in an older version, transitively, each with its `.pom`. |
| `--war VERSION`, `--war required` | Stage that core's `.war` and `.pom`; `required` stages every core plugins require that the repository lacks, at or above the floor. Repeatable. |
| `--since VERSION` | The floor for `required`, instead of the oldest core in the repository. The default is `DOCTOR_SINCE`. |
| `--fetch`, `--no-fetch` | First download the POMs and manifests missing from the generator's cache, so the run finds every missing dependency and required core. On by default. |
| `--staging DIR` | The staging folder. The default is `PROCUREMENT_STAGING_DIR` (`/procurement`). |

Exits 0 when everything planned is staged; 1 when a file could not be staged, or when the run is
refused as `uc: error: ...` (air-gapped without `PROCUREMENT_SOURCE_URL`, a `--war` that is neither a
version nor `required`); 2 when nothing is selected: no POMs, dependencies or cores
(`uc: error: nothing to stage: choose --poms, --dependencies or --war VERSION|required`). Whenever `procurement.json` lists files, also
after a run with problems, it ends with the review:

```text
next: review ./procurement (procurement.json lists 12 files), then uc procure push
```

With `--staging`, the line names that folder instead.

### uc procure record

Records every `.hpi`, `.jpi`, `.war` and `.pom` placed in the folder by hand that `procurement.json`
does not list yet, with the reason `added by hand`, after the checks a download gets. A misplaced
file is refused with the Maven path it belongs at, from the coordinates its manifest or POM
declares. It needs no network.

| Option | Meaning |
|---|---|
| `--fix` | First move each misplaced file to the Maven path it declares, never onto another file, a listed path or out of the folder. |
| `--staging DIR` | The staging folder. The default is `PROCUREMENT_STAGING_DIR` (`/procurement`). |

Exits 0 unless it refused a file, else 1. It ends with the same `next:` line as `stage`.

### uc procure push

Uploads the files `procurement.json` lists that the repository lacks, with
`secrets/repo_push_auth`, never overwriting a path the repository already holds. It first checks
that each file is the one recorded, and reports the files in the folder that `procurement.json` does
not list (`unlisted`), which it does not push. It refuses the public repo.jenkins-ci.org, and a
folder with no `procurement.json`.

| Option | Meaning |
|---|---|
| `--dry-run` | Check each file, upload nothing. |
| `--staging DIR` | The staging folder. The default is `PROCUREMENT_STAGING_DIR` (`/procurement`). |

Exits 0 when every listed file ends up in the repository with the recorded content, else 1;
unlisted files do not change it. After a real push with no problem, it ends with:

```text
next: uc generate (or wait for the schedule), then uc procure clean
```

### uc procure clean

Deletes the staged files, listed or placed by hand, that the repository holds with the same content
(the same sha1), with their `procurement.json` entries and any folder left empty, so the folder keeps
only what still needs pushing. It reads the repository with `secrets/repo_push_auth`, as push does,
and likewise refuses the public repo.jenkins-ci.org.

| Option | Meaning |
|---|---|
| `--dry-run` | Check each file, delete nothing. |
| `--staging DIR` | The staging folder. The default is `PROCUREMENT_STAGING_DIR` (`/procurement`). |

Exits 0 unless a file differs from the repository's or could not be checked or deleted, else 1. It
prints no `next:` line: procurement is done.

## uc rollback

```bash
docker compose run --rm uc rollback [<release-id>]
```

Points `live` at an earlier release: the one before the live release, or the release id given
(`YYYYMMDDTHHMMSSZ`, as listed under `releases/`). Prints `live release is now <id>` and updates
`live_release` and `plugin_count` in `status.json`. It takes the run lock: while a generation runs
it exits 75 instead of waiting. The web container serves the change at once.

## uc status

```bash
docker compose run --rm uc status
```

Prints `status.json`, the same document served at `$PUBLIC_URL/status.json`. Fields:
[status.json reference](status-json.md). Prints `{}` before the first run.

## uc config

```bash
docker compose run --rm --no-deps uc config [--json | --check]
docker compose run --rm --no-deps uc config --example [--mode online|airgapped]
```

`uc config` needs no other service, so run it with `--no-deps`. Without it, Compose first starts the
repo-adapter the `uc` service depends on, recreating it when its image or settings changed: after an
upgrade or a settings change, the adapter would then run with settings not yet checked, and a
setting the new revision refuses would show as Compose's `dependency failed to start` instead of
`uc: error: ...`.

Shows every setting with its value and where it came from: `environment`, `mode`
(the default for the mode), `derived` (worked out from other settings, as `REPO_URL` and the secret
paths are) or `default`. For a secret file it shows the path and whether the file is `present`,
`empty` or `missing`, never its contents. How a change in `.env` takes effect:
[When changes apply](configuration.md#when-changes-apply).
Credentials inside a URL setting are shown as `***`, taken as everything before the URL's last `@`
(after any `scheme://`), so an `@` in a path also hides the part before it:
`https://cdn.example/gh/repo@1.0/data.json` shows as `https://***@1.0/data.json`.
It runs in the `uc` container, which by design does not receive `download_proxy_auth` (web's) nor,
air-gapped, `repo_username` and `repo_password` (the repo-adapter's), so those show as `missing`
there even when they are set up.

| Option | Meaning |
|---|---|
| `--json` | The same as a JSON list of objects with `env`, `value`, `source` and `note`. |
| `--check` | Only check the settings: prints `configuration ok`, or the error. |
| `--example` | Print an example `.env` that lists every setting, with the settings the mode needs set and the rest commented out at their defaults. It reads no settings, so it works before any exist. |
| `--mode` | With `--example`: the mode the example is for, `online` (the default) or `airgapped`. |

An invalid configuration exits 1; without options, after printing every value it could work out.
How settings combine: [How settings are read](configuration.md#how-settings-are-read).

## uc schedule

```bash
uc schedule
```

The `scheduler` service's command; you do not run it by hand. When no release is live it generates
one at once, in its own process: that
first run prints no `published release` line, and a failure is logged as a traceback while
scheduling continues. It then hands over to supercronic, which runs:

- `uc generate --wait` on `GENERATE_SCHEDULE` (default every six hours); those runs print
  `published release <id>`;
- `uc doctor --record` on `DOCTOR_SCHEDULE`, when it is set: a read-only report whose counts go
  into `status.json`.

A bad schedule stops the scheduler at start, with a message naming the setting. supercronic never starts a job while its
previous run is still going, and a scheduled generation that finds another generation or a
rollback running waits for it.

## uc repo-adapter

```bash
uc repo-adapter [--port 8081] [--healthcheck]
```

The `repo-adapter` service's command; you do not run it by hand. Serves the source repository to the
generator on the internal `repo` network, and stays idle when `REPO_ADAPTER` is off. See
[repo-adapter](../architecture/repo-adapter.md).

| Option | Meaning |
|---|---|
| `--port` | Port to listen on (default `REPO_ADAPTER_PORT`, 8081). |
| `--healthcheck` | Do not serve: ask the running adapter on `127.0.0.1` for `/healthz` (3 seconds, proxy settings ignored), on `--port` or else the port the adapter recorded in `/tmp/uc-repo-adapter.port` when it started listening. It reads no settings, so a settings change cannot make a running adapter unhealthy. Exit 0 when it answers 200; otherwise print `uc: error: repo-adapter is not healthy ...` (also when no port is recorded yet) and exit 1. The `repo-adapter` service's Compose healthcheck runs it. |

## uc pki

Manages the signing PKI. It reads no settings, so it runs before anything else is configured. `make pki-init` wraps `init`, and `make pki-rotate ROOT=<dir>` wraps `rotate`; both run
the [`setup` Compose service](#the-setup-service), which reads `.env` (or the file `UC_ENV_FILE`
names, which must exist) as the other services do, and then run its `secrets` step: they create any missing credential file empty and set the
permissions of `secrets/` ([File ownership on Linux](secrets.md#file-ownership-on-linux)).
`make pki-init OUT=<dir>` creates the PKI in `<dir>` instead, and only sets the permissions there,
for [replacing the root CA](../how-to/rotate-signing-certificate.md#replace-the-root-ca).

```bash
uc pki init --out <dir> [--display-dir <dir>] [--root-days 3650] [--signing-days 365] [--org <name>]
uc pki rotate --dir <dir> [--display-dir <dir>] [--root-display-dir <dir>] [--days 365] [--org <name>]
uc pki show --dir <dir>
```

A directory `init` or `rotate` cannot write in is reported as
`uc: error: cannot write in <dir>: <reason>` (one `init` cannot create, as
`uc: error: cannot create <dir>: <reason>`), and nothing changes. While they rename files into
place, both hold SIGINT and SIGTERM back (Ctrl-C, `docker stop`) until every rename is done or
undone, and neither renames onto a file that exists: one that appears meanwhile is reported as
`refusing to overwrite existing files: ...` and kept. If moving back fails too, the error names
what is not back and the `.rotate-*` or `.init-*` directory inside `<dir>` that keeps what was set
aside.

| Command | What it does |
|---|---|
| `init` | Creates a root CA (`root-ca.crt`, and `root/root-ca.key` to move offline) and a signing key and certificate (`update-center.key`, `update-center.cert`) issued by it. It refuses to overwrite any of them. It creates them in a temporary directory inside `<dir>` and moves them in once all four exist; when a step fails, it moves back what it moved, so `<dir>` is left as it was. |
| `rotate` | Issues a new signing certificate from the root key in `<dir>/root/`. The previous pair is kept as `*.old`, replacing an older `*.old` pair. Controllers need no change. It first checks that the root key and `root-ca.crt` can be read and belong together, and that `update-center.key` and `update-center.cert` are both there or both missing. It refuses an encrypted root key without asking for the passphrase: [decrypt it to a temporary file](../how-to/rotate-signing-certificate.md#a-passphrase-protected-root-key) for the rotation. It issues the new pair in a temporary directory inside `<dir>` and only then renames the files; when a check, a step or a rename fails, it moves back what it moved, so `<dir>` is left as it was. |
| `show` | Prints the subject, expiry (with days left) and SHA-256 fingerprint of the root and signing certificates. |

| Option | Meaning |
|---|---|
| `--out`, `--dir` | The directory holding the PKI files (required). |
| `--display-dir` | `init` and `rotate`: the directory `--out` or `--dir` is on the host. Messages name the files under it (`secrets/root/root-ca.key`, not `/setup/secrets/root/root-ca.key`). `make pki-init` and `make pki-rotate` pass `secrets`, or the `OUT` directory. |
| `--root-display-dir` | `rotate`: the directory `<dir>/root/` is on the host (default: `root/` under `--display-dir`, or under `--dir` without it). Messages name the root key under it. `make pki-rotate` passes `ROOT`, which it mounts there. |
| `--root-days` | Lifetime of the root CA (default 3650). |
| `--signing-days`, `--days` | Lifetime of the signing certificate (default 365). |
| `--org` | Organization name in the certificates' subjects (default `Easy Update Center`). |

How to run `rotate` safely: [Rotate the signing certificate](../how-to/rotate-signing-certificate.md).

## The setup service

The `setup` Compose service runs `uc-setup`, not `uc`: first-time setup, and the jobs that write to
`./secrets` and `./procurement`. It is the only Compose service that runs as root, and it runs only
when you call it: it sits in its own Compose profile, so `docker compose up` never starts it. It has
no network and no Compose secrets. It mounts `./secrets` at `/setup/secrets`, `./procurement` at
`/setup/procurement` and `./config` read-only at `/setup/config`, and reads `.env` (or the file
`UC_ENV_FILE` names) as the other services do, so the file must exist. "You" below is the owner of
`./config`: `uc-setup` gives `./secrets` and `./procurement` to it when Docker has just created them
as root. When `./config` is empty and owned by root, Docker created it too, so this is not a
checkout: `uc-setup` then exits 1 and changes nothing.

```bash
docker compose run --rm setup
docker compose run --rm setup secrets
docker compose run --rm setup procurement
docker compose run --rm setup pki-init [--out DIR] [--display-dir NAME]
docker compose run --rm -v /path/to/root:/setup/secrets/root:ro setup pki-rotate [--root-display-dir NAME]
```

| Command | What it does | `make` shortcut |
|---|---|---|
| (none) | First-time setup, safe to repeat: `secrets`, then `pki-init` unless `secrets/update-center.key` exists, then `procurement`, last because it is the one step that can fail on some filesystems. | `make setup` |
| `secrets` | Creates the missing credential files empty and sets the permissions of `secrets/` ([File ownership on Linux](secrets.md#file-ownership-on-linux)). | `make secrets` |
| `procurement` | Shares `./procurement` with the uc containers ([below](#sharing-procurement)). | `make setup` runs it, after the other steps |
| `pki-init` | Runs `uc pki init` in `secrets/`, then the `secrets` step; or in `--out DIR` (a directory you mount for a new PKI), naming the files under `--display-dir NAME` in its messages, then only sets the permissions there. | `make pki-init`, or `make pki-init OUT=<dir>`, which mounts `<dir>` at `/setup/out` |
| `pki-rotate` | Runs `uc pki rotate` on `secrets/`, with the directory holding the root key mounted read-only at `/setup/secrets/root` (named `--root-display-dir NAME` in messages), then the `secrets` step. Without that directory it exits 1, naming `make pki-rotate ROOT=<dir>`. | `make pki-rotate ROOT=<dir>`, which mounts `<dir>` there |

Any other argument exits 2 with the usage line.

### Sharing ./procurement

`procurement` gives `./procurement` to you and sets ACLs on it and on everything in it: you and uid
10001, the user the uc containers run as, can both read, write and delete, and a default ACL on
every directory passes the same on to whatever is created in it later. [uc procure](#uc-procure) can
then stage into the folder, and you can review, edit, move and delete every file in it as yourself,
with no change of ownership around each run. It is safe to repeat: run it again after moving or
copying files in from elsewhere, which can keep their own modes and ACLs.

On a filesystem without ACLs, such as Docker Desktop's bind mounts or some network filesystems, it
sets nothing:

- When uid 10001 can already write the folder, as on Docker Desktop, which ignores ownership, it says
  so and exits 0: nothing is needed.
- Otherwise it exits 1, naming `make procure-stage`, `procure-record` and `procure-clean`. Use those
  instead of `docker compose run --rm uc procure stage`, `record` and `clean`: they hand the folder
  to uid 10001 for each run and back to you after. `push` only reads the folder.

When the ACLs take on the folder but not on everything in it, it exits 1 with setfacl's message;
run it again once that is fixed.
