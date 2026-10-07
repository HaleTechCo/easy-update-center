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
- Every command except `uc pki` reads the [configuration](configuration.md) and fails at once with
  `uc: error: ...` when it is invalid.

Exit codes shared by every command:

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | An expected failure; the message is printed as `uc: error: ...`. For `uc doctor`, also "something in the repository will be dropped". |
| `2` | Invalid command line. |
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
docker compose run --rm uc doctor [--fetch | --no-fetch] [--json | --no-json] [--since VERSION] [--record]
docker compose run --rm uc doctor [--fetch] [--download-poms] [--download-dependencies] [--download-war VERSION|required ...] [--since VERSION] [--staging DIR]
docker compose run --rm uc doctor --push [--dry-run] [--staging DIR]
docker compose run --rm uc doctor --stage-local [--fix] [--staging DIR]
docker compose run --rm uc doctor --clean [--dry-run] [--staging DIR]
```

Reports what the generator will and will not see in the source repository, without generating. It
runs the same listing query the generator runs and prints:

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
| `--fetch`, `--no-fetch` | Download the POMs and manifests missing from the generator's cache, so dependencies and required cores can be checked before the first generation. Without it, unchecked items are counted and reported, and a download run may miss what they need: use it with the download options. The default is `DOCTOR_FETCH` (off). |
| `--json`, `--no-json` | Print the full report as JSON. The default is `DOCTOR_JSON` (off). |
| `--since VERSION` | The floor for required cores, instead of the oldest core in the repository. The default is `DOCTOR_SINCE`. |
| `--record` | Also save the report as the latest: in full as `/data/doctor/report.json` (not served), and its counts in `status.json`'s `doctor` block with `checked_at` and `recorded_at`. The scheduler runs it on `DOCTOR_SCHEDULE`. Not with `--push`, `--stage-local` or `--clean`. |
| `--download-poms` | Stage the real `.pom` of every plugin whose POM is missing, derived or unusable. |
| `--download-dependencies` | Stage missing required dependencies, transitively, each with its `.pom`. |
| `--download-war VERSION` | Stage that core's `.war` and `.pom`; `required` stages every required core the repository lacks at or above the floor. Repeatable. |
| `--staging DIR` | The staging folder (default `/procurement`, `./procurement` on the host). |
| `--push` | Upload the files `procurement.json` lists to the repository, never overwriting. On its own only. |
| `--dry-run` | With `--push` or `--clean`: check each file, change nothing. |
| `--stage-local` | Record the `.hpi`, `.jpi`, `.war` and `.pom` files placed in the staging folder by hand that `procurement.json` does not list yet, after the checks a download gets. A misplaced file is refused with the path it belongs at. Needs no network. On its own only. |
| `--fix` | With `--stage-local`: first move each misplaced file to the Maven path it declares, never onto another file, a listed path or out of the folder. |
| `--clean` | Delete the staged files, listed or placed by hand, that the repository holds with the same content, with their `procurement.json` entries. On its own only. |

Exits 1 when anything will be dropped or a dependency is missing, else 0. A download run exits 0
when everything planned is staged, else 1; a push, 0 when every listed file ends up in the
repository with the recorded content; `--stage-local`, 0 unless it refused a file; `--clean`, 0
unless a file differs from the repository's or could not be checked or deleted. `--push`,
`--stage-local` and `--clean` each run on their own: combined with one another, a download option,
`--fetch`, `--json` or `--since`, they exit 2, as does `--dry-run` without `--push` or `--clean`, and
`--fix` without `--stage-local`. How to use them:
[Procure missing POMs, dependencies and cores](../how-to/procure-missing-files.md). What the
repository must contain: [Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

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
docker compose run --rm --no-deps uc config --example toml|env [--mode online|airgapped]
docker compose run --rm --no-deps uc config --web-env [--out FILE]
```

`uc config` needs no other service, so run it with `--no-deps`. Without it, Compose first starts the
repo-adapter the `uc` service depends on, recreating it when its image or settings changed: after an
upgrade or a settings change, the adapter would then run with settings not yet checked, and a
setting the new revision refuses would show as Compose's `dependency failed to start` instead of
`uc: error: ...`.

Shows every setting with its value and where it came from: `environment`, `uc.toml: <key>`, `mode`
(the default for the mode), `derived` (worked out from other settings, as `REPO_URL` and the secret
paths are) or `default`. For a secret file it shows the path and whether the file is `present`,
`empty` or `missing`, never its contents. A setting read only when a service starts shows `restart`
after its source (`environment; restart`): changing it needs `make reload` wherever it is set. For
the others, a change in `config/uc.toml` applies at the next run, but one in `.env` also needs
`make reload`, because a container's environment is fixed when it is created. When the scheduler
started with other values of the `restart` settings, or in another environment, than these, it
ends with the command that applies them. Set settings in `.env` or `config/uc.toml`, not under one
service's `environment:` in a Compose override: `uc config` compares its own container's settings
with the scheduler's, so a value only one of them receives makes that note permanent (the TLS and
proxy-CA overrides set variables on `web` alone, which neither compares).
Credentials inside a URL setting are shown as `***`, taken as everything before the URL's last `@`
(after any `scheme://`), so an `@` in a path also hides the part before it:
`https://cdn.example/gh/repo@1.0/data.json` shows as `https://***@1.0/data.json`.
It runs in the `uc` container, which by design does not receive `download_proxy_auth` (web's) nor,
air-gapped, `repo_username` and `repo_password` (the repo-adapter's), so those show as `missing`
there even when they are set up.

| Option | Meaning |
|---|---|
| `--json` | The same as a JSON list of objects with `env`, `key`, `value`, `source`, `note` and `restart` (true or false). |
| `--check` | Only check the settings: prints `configuration ok`, or the error. |
| `--example toml\|env` | Print an example `config/uc.toml` or `.env` that lists every setting, with the settings the mode needs set and the rest commented out at their defaults. It reads no settings, so it works before any exist. |
| `--mode` | With `--example`: the mode the example is for, `online` (the default) or `airgapped`. |
| `--web-env` | Print the web container's settings as `NAME=value` lines, worked out from the settings and the mode: what the `web-config` service writes for the web container. |
| `--out FILE` | With `--web-env`: write them to `FILE` instead, atomically. |

An invalid configuration exits 1; without options, after printing every value it could work out.
How settings combine: [How settings are read](configuration.md#how-settings-are-read).

## uc schedule

```bash
uc schedule
```

The `scheduler` service's command; you do not run it by hand. It records when it started, and
with which settings, in `/data/scheduler.json` (so `uc config` can tell when the running services
use older settings). When no release is live it generates one at once, in its own process: that
first run prints no `published release` line, and a failure is logged as a traceback while
scheduling continues. It then hands over to supercronic, which runs:

- `uc generate --wait` on `GENERATE_SCHEDULE` (default every six hours); those runs print
  `published release <id>`;
- `uc doctor --record` on `DOCTOR_SCHEDULE`, when it is set: a read-only report whose counts go
  into `status.json`.

A bad schedule stops the scheduler at start, with a message naming the setting. A start it cannot
record (an unwritable data directory) is reported on one line, and the scheduler runs anyway; `uc
config` then cannot tell when a restart is needed. supercronic never starts a job while its
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
| `--healthcheck` | Do not serve: ask the running adapter on `127.0.0.1` for `/healthz` (3 seconds, proxy settings ignored), on `--port` or else the port the adapter recorded in `/tmp/uc-repo-adapter.port` when it started listening. It reads no settings, so editing `uc.toml` cannot make a running adapter unhealthy. Exit 0 when it answers 200; otherwise print `uc: error: repo-adapter is not healthy ...` (also when no port is recorded yet) and exit 1. The `repo-adapter` service's Compose healthcheck runs it. |

## uc pki

Manages the signing PKI. It reads only the `PKI_*` settings (from the environment, and
`config/uc.toml` when there is one) and needs no others, so it runs before anything else is
configured. `make pki-init` wraps `init`, and `make pki-rotate ROOT=<dir>` wraps `rotate`; both run
the `setup` Compose service, which reads `.env` (or the file `UC_ENV_FILE` names, which must exist)
and `config/` as the other services do, so `uc pki` sees the settings they see, and then set the permissions of
`secrets/`
([File ownership on Linux](secrets.md#file-ownership-on-linux)). `make pki-init OUT=<dir>` creates
the PKI in `<dir>` instead, for
[replacing the root CA](../how-to/rotate-signing-certificate.md#replace-the-root-ca).

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
| `--display-dir` | `init` and `rotate`: the directory `--out` or `--dir` is on the host. Messages name the files under it (`secrets/root/root-ca.key`, not `/pki/root/root-ca.key`). `make pki-init` and `make pki-rotate` pass `secrets`, or the `OUT` directory. |
| `--root-display-dir` | `rotate`: the directory `<dir>/root/` is on the host (default: `root/` under `--display-dir`, or under `--dir` without it). Messages name the root key under it. `make pki-rotate` passes `ROOT`, which it mounts there. |
| `--root-days` | Lifetime of the root CA (default `PKI_ROOT_DAYS`, 3650). |
| `--signing-days`, `--days` | Lifetime of the signing certificate (default `PKI_SIGNING_DAYS`, 365). |
| `--org` | Organization name in the certificates' subjects (default `PKI_ORG`). |

How to run `rotate` safely: [Rotate the signing certificate](../how-to/rotate-signing-certificate.md).
