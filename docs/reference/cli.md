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
docker compose run --rm uc generate [--force]
```

Builds, validates and publishes one release: the whole [generation pipeline](../architecture/generation-pipeline.md).
On success it prints `published release <id>` and exits 0; the new release is live. On failure the
live release is unchanged, `status.json` records the failed run, and the full error is in the output.

| Option | Meaning |
|---|---|
| `--force` | Skip the plugin-count ratio check (`MIN_PLUGIN_RATIO`) for this run, to accept a deliberate shrink of the catalog. |

The scheduler runs the same command on `GENERATE_SCHEDULE`.

## uc doctor

```bash
docker compose run --rm uc doctor [--fetch] [--json]
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
- with the repo-adapter on, what it filled in: POMs derived from archives, `.pom` files it ignored as
  unusable, and archives whose release date came from the repository;
- stub POMs (no `<name>`: display names, descriptions and SCM links will be degraded);
- dependency problems: plugins whose required plugins are absent, or present only in lower
  versions.

| Option | Meaning |
|---|---|
| `--fetch` | Download the POMs and manifests missing from the generator's cache, so dependencies can be checked before the first generation. Without it, unchecked items are counted and reported. |
| `--json` | Print the full report as JSON. |

Exits 1 when anything will be dropped or a dependency is missing, else 0. What the repository must
contain: [Stock an air-gapped repository](../how-to/stock-airgapped-repository.md).

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

## uc schedule

```bash
uc schedule
```

The `scheduler` service's command; you do not run it by hand. When no release is live it generates
one at once, in its own process: that first run prints no `published release` line, and a failure
is logged as a traceback while scheduling continues. It then hands over to supercronic, which runs
`uc generate` on `GENERATE_SCHEDULE` (default every six hours); those runs print
`published release <id>`.

## uc repo-adapter

```bash
uc repo-adapter [--port 8081]
```

The `repo-adapter` service's command; you do not run it by hand. Serves the source repository to the
generator on the internal `repo` network, and stays idle when `REPO_ADAPTER` is off. See
[repo-adapter](../architecture/repo-adapter.md).

| Option | Meaning |
|---|---|
| `--port` | Port to listen on (default 8081). |

## uc pki

Manages the signing PKI. It does not read the configuration, so it runs with `docker run` against
the image. `make pki-init` wraps `init`, and `make pki-rotate ROOT=<dir>` wraps `rotate`; both then
set the permissions of `secrets/` ([File ownership on Linux](secrets.md#file-ownership-on-linux)).

```bash
uc pki init --out <dir> [--root-days 3650] [--signing-days 365] [--org <name>]
uc pki rotate --dir <dir> [--days 365] [--org <name>]
uc pki show --dir <dir>
```

| Command | What it does |
|---|---|
| `init` | Creates a root CA (`root-ca.crt`, and `root/root-ca.key` to move offline) and a signing key and certificate (`update-center.key`, `update-center.cert`) issued by it. |
| `rotate` | Issues a new signing certificate from the root key in `<dir>/root/`. The previous pair is kept as `*.old`. Controllers need no change. |
| `show` | Prints the subject, expiry (with days left) and SHA-256 fingerprint of the root and signing certificates. |

| Option | Meaning |
|---|---|
| `--out`, `--dir` | The directory holding the PKI files (required). |
| `--root-days` | Lifetime of the root CA (default 3650). |
| `--signing-days`, `--days` | Lifetime of the signing certificate (default 365). |
| `--org` | Organization name in the certificates' subjects. |

How to run `rotate` safely: [Rotate the signing certificate](../how-to/rotate-signing-certificate.md).
