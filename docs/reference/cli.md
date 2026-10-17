# uc command reference

Every uc command, its options and its exit codes.

## Running uc

`uc` is the entrypoint of the uc image. Run one-shot commands through Compose's `uc` service, which
has the same volume, secrets and settings as the scheduler:

```bash
docker compose run --rm uc <command> [options]
```

Air-gapped, these commands assume `COMPOSE_FILE` is set in `.env` ([Compose](configuration.md#compose)).

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
with that step, naming the options for what it found:

```text
next: uc procure stage --poms --dependencies --war required
```

Given `--since`, the line carries it to the required cores: `--war required --since 2.462.1`.
Air-gapped without `PROCUREMENT_SOURCE_URL`, where
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

`stage` fetches what `uc doctor` finds missing into a staging folder for review, `record` adds the
files you place there by hand, `push` uploads exactly what the folder's `procurement.json` lists, and
`clean` deletes the staged copies the repository then holds. How each behaves, in order:
[Procure missing POMs, dependencies and cores](../how-to/procure-missing-files.md).

The staging folder is `./procurement` on the host, mounted at `/procurement`; on Linux it must be
writable by `UC_UID`. `--staging DIR` uses another folder inside the `uc` container for one run; it
must not be the data volume, `/data`, or a folder in it.

### uc procure stage

Prints `uc doctor`'s report, then downloads each file you chose from `PROCUREMENT_SOURCE_URL`, checks
it against its `.sha1` and lists it in `procurement.json` ([2. Stage](../how-to/procure-missing-files.md#2-stage)).
Air-gapped, it is refused unless `PROCUREMENT_SOURCE_URL` names a copy the update center can reach.

| Option | Meaning |
|---|---|
| `--poms` | Stage the real `.pom` of every plugin whose POM is missing, derived from its archive, or unusable. |
| `--dependencies` | Stage the required dependencies the repository lacks or holds only in an older version, transitively, each with its `.pom`. |
| `--war VERSION`, `--war required` | Stage that core's `.war` and `.pom`; `required` stages every core plugins require that the repository lacks, at or above the floor. Repeatable. |
| `--since VERSION` | The floor for `required`, instead of the oldest core in the repository. The default is `DOCTOR_SINCE`. |
| `--fetch`, `--no-fetch` | First download the POMs and manifests missing from the generator's cache, so the run finds every missing dependency and required core. On by default. |
| `--staging DIR` | The staging folder (default `/procurement`). |

Exits 0 when everything planned is staged; 1 when a file could not be staged, or the run is refused
(`uc: error: ...`: air-gapped without `PROCUREMENT_SOURCE_URL`); 2 when nothing is selected
(`uc: error: nothing to stage: choose --poms, --dependencies or --war VERSION|required`) or a
`--war` or `--since` is not a Jenkins version (and, for `--war`, not `required`). Whenever
`procurement.json` lists files, it ends with
`next: review ./procurement (procurement.json lists N files), then uc procure push`.

### uc procure record

Records the `.hpi`, `.jpi`, `.war` and `.pom` files placed in the folder by hand, after the checks a
download gets, and refuses a misplaced file with the Maven path it belongs at. It needs no network
([Files you place by hand](../how-to/procure-missing-files.md#files-you-place-by-hand)).

| Option | Meaning |
|---|---|
| `--fix` | First move each misplaced file to the Maven path it declares, never onto another file, a listed path or out of the folder. |
| `--staging DIR` | The staging folder (default `/procurement`). |

Exits 0 unless it refused a file, else 1. It ends with the same `next:` line as `stage`.

### uc procure push

Uploads the files `procurement.json` lists that the repository lacks, with `secrets/repo_push_auth`,
never overwriting a path the repository holds; files it does not list are reported as `unlisted` and
not pushed. It refuses the public repo.jenkins-ci.org, and a folder with no `procurement.json`
([4. Push](../how-to/procure-missing-files.md#4-push)).

| Option | Meaning |
|---|---|
| `--dry-run` | Check each file, upload nothing. |
| `--staging DIR` | The staging folder (default `/procurement`). |

Exits 0 when every listed file ends up in the repository with the recorded content, else 1;
unlisted files do not change it.

### uc procure clean

Deletes the staged files the repository holds with the same sha1, with their `procurement.json`
entries. It reads the repository with `secrets/repo_push_auth` and refuses the public
repo.jenkins-ci.org, as push does ([6. Clean up](../how-to/procure-missing-files.md#6-clean-up)).

| Option | Meaning |
|---|---|
| `--dry-run` | Check each file, delete nothing. |
| `--staging DIR` | The staging folder (default `/procurement`). |

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

Shows every setting with its value and where it came from: `environment`, `mode` (the default for
the mode), `derived` (worked out from other settings, as `REPO_URL` is) or `default`. Secret files
are not settings: `uc config` never reads them. Credentials inside a URL setting are shown as `***`,
taken as everything before the URL's last `@` (after any `scheme://`), so an `@` in a path also hides
the part before it: `https://cdn.example/gh/repo@1.0/data.json` shows as `https://***@1.0/data.json`.

Run it with `--no-deps`: it needs no other service, and without it Compose first starts (or
recreates) the repo-adapter, which would then run with settings not yet checked, and a refused
setting would show as `dependency failed to start` instead of `uc: error: ...`. How a change in
`.env` takes effect: [When changes apply](configuration.md#when-changes-apply).

| Option | Meaning |
|---|---|
| `--json` | The same as a JSON list of objects with `env`, `value` and `source` (`env` where the text says `environment`). |
| `--check` | Only check the settings: prints `configuration ok`, or the error. |
| `--example` | Print an example `.env` that lists every setting, with the settings the mode needs set and the rest commented out at their defaults. It reads no settings, so it works before any exist. |
| `--mode` | With `--example`: the mode the example is for, `online` (the default) or `airgapped`. |

An invalid configuration exits 1; without options, after printing every value it could work out.
When the environment still holds an internal path an older release documented, such as
`REPO_ADAPTER_URL`, with a value other than its default, every form also prints a `uc: note:` line
naming it on standard error: uc still follows those names, so remove them from `.env`.
How settings combine: [How settings are read](configuration.md#how-settings-are-read).

## uc schedule

```bash
uc schedule
```

The `scheduler` service's command; you do not run it by hand. It hands over to supercronic, which
runs:

- `uc generate --wait` on `GENERATE_SCHEDULE` (default every six hours);
- `uc doctor --record` on `DOCTOR_SCHEDULE`, when it is set: a read-only report whose counts go
  into `status.json`.

supercronic never starts a job while its previous run is still going, and a scheduled generation
waits for another generation or a rollback. When no release is live, `uc schedule` first generates
one in its own process: that run prints no `published release` line, and a failure is logged as a
traceback while scheduling continues. A bad schedule stops the scheduler at start, with a message
naming the setting.

## uc repo-adapter

```bash
uc repo-adapter [--port 8081] [--healthcheck]
```

The `repo-adapter` service's command; you do not run it by hand. Serves the source repository to the
generator on the internal `repo` network, and stays idle when `REPO_ADAPTER` is off. See
[repo-adapter](../architecture/repo-adapter.md).

| Option | Meaning |
|---|---|
| `--port` | Port to listen on (default 8081). |
| `--healthcheck` | Do not serve: ask the running adapter on `127.0.0.1` for `/healthz` (3 seconds, proxy settings ignored), on `--port` or else the port the adapter recorded in `/tmp/uc-repo-adapter.port` when it started listening. It reads no settings, so a settings change cannot make a running adapter unhealthy. Exit 0 when it answers 200; otherwise print `uc: error: repo-adapter is not healthy ...` (also when no port is recorded yet) and exit 1. The `repo-adapter` service's Compose healthcheck runs it. |

## uc pki

Manages the signing PKI. It reads no settings, so it runs before anything else is configured. The
[`setup` Compose service](#the-setup-service) runs `init` and `rotate` on `secrets/`, and then its
`secrets` step.

```bash
uc pki init --out <dir> [--display-dir <dir>] [--root-days 3650] [--signing-days 180] [--org <name>]
uc pki rotate --dir <dir> [--root <dir>] [--display-dir <dir>] [--days 180] [--org <name>]
uc pki show --dir <dir>
```

| Command | What it does |
|---|---|
| `init` | Creates a root CA (`root-ca.crt`, and `root/root-ca.key` to move offline) and a signing key and certificate (`update-center.key`, `update-center.cert`) issued by it. It refuses to overwrite any of them. |
| `rotate` | Issues a new signing certificate from the root key in `<dir>/root/` (or `--root`), keeping the previous pair as `*.old` in place of an older one. It first checks that the root key and `root-ca.crt` can be read and belong together, and that `update-center.key` and `update-center.cert` are both there or both missing. It refuses an encrypted root key without asking for the passphrase: [decrypt it on the offline machine](../how-to/rotate-signing-certificate.md#issue-a-new-signing-certificate) for the rotation. |
| `show` | Prints the subject, expiry (with days left) and SHA-256 fingerprint of the root and signing certificates. With neither in `<dir>`, it exits 1 with `uc: error: no certificate in <dir>`. |

When `init` or `rotate` fails, `<dir>` is left as it was. Both build the new files in a temporary
directory inside `<dir>` and only then rename them into place, moving back what they moved when a
step fails; meanwhile they hold SIGINT and SIGTERM back (Ctrl-C, `docker stop`), and they never
rename onto a file that exists (`refusing to overwrite existing files: ...`). A directory they cannot
write in (or that `init` cannot create) is reported as `uc: error: cannot write in <dir>: <reason>`
(`cannot create`). If moving back fails too, the error names what is not back and the `.rotate-*` or
`.init-*` directory inside `<dir>` that keeps it.

| Option | Meaning |
|---|---|
| `--out`, `--dir` | The directory holding the PKI files (required). |
| `--display-dir` | `init` and `rotate`: the directory `--out` or `--dir` is on the host. Messages name the files under it (`secrets/root/root-ca.key`, not `/setup/secrets/root/root-ca.key`). `setup` passes `secrets`. |
| `--root` | `rotate`: the directory holding `root-ca.key` (default: `root/` under `--dir`). Messages name the root key there. |
| `--root-days` | Lifetime of the root CA (default 3650). |
| `--signing-days`, `--days` | Lifetime of the signing certificate (default 180). |
| `--org` | Organization name in the certificates' subjects (default `Easy Update Center`). |

How to run `rotate` safely: [Rotate the signing certificate](../how-to/rotate-signing-certificate.md).

## The setup service

The `setup` Compose service runs `uc-setup`, not `uc`: first-time setup, and the jobs that write to
`./secrets`. It runs only when you call it: it sits in its own Compose profile, so
`docker compose up` never starts it. Like every other service it runs as `UC_UID`:`UC_GID`; it has
no network, no Compose secrets and no settings, and it mounts only `./secrets`, at `/setup/secrets`.

```bash
docker compose run --rm setup
docker compose run --rm setup secrets
docker compose run --rm setup pki-init [--org ORG] [--root-days N] [--signing-days N]
docker compose run --rm -v "<empty folder>:/setup/out" setup pki-init --out /setup/out [--org ORG] [--root-days N] [--signing-days N]
docker compose run --rm -v "<folder holding root-ca.key>:/setup/root:ro" setup pki-rotate [--days N] [--org ORG]
```

| Command | What it does |
|---|---|
| (none) | First-time setup, safe to repeat: `secrets`, then `pki-init` unless `secrets/update-center.key` exists. |
| `secrets` | Creates the missing credential files empty and sets the modes of the files in `secrets/` ([File modes](secrets.md#file-modes)). |
| `pki-init` | Runs `uc pki init` in `secrets/`, then the `secrets` step; or with `--out DIR` in that directory (one you mount), then sets only the key and certificate modes there. `--org`, `--root-days` and `--signing-days` pass through. Either way the new root key is on this host, which suits a trial: for production, or a new PKI after a leak, create it on an offline machine instead ([Create the PKI on an offline machine](../how-to/rotate-signing-certificate.md#create-the-pki-on-an-offline-machine)). |
| `pki-rotate` | Runs `uc pki rotate` on `secrets/` with the root key from `/setup/root`, which you mount read-only, then the `secrets` step. Without that mount, or with a file there, it exits 1 and says how to mount the folder. `--days` and `--org` pass through. |

When `secrets/` (or the `--out` directory) is not writable by the user it runs as, `uc-setup` exits 1:
`secrets/ is not writable by uid N: set UC_UID and UC_GID in .env to the output of id -u and id -g`.
Any other argument exits 2 with the usage line.
