# Run procurement from a pipeline

How to run the stages of [Procure missing POMs, dependencies and cores](procure-missing-files.md)
from Jenkins, with an approval before anything is pushed.

## Why a pipeline

`uc procure` already keeps the stages apart: nothing reaches the repository until someone runs
`uc procure push`. A pipeline adds two things to running them by hand:

- **An approval before the push.** The pipeline stages the files, then stops until one of the people
  you named has read what was staged and approved it. Only then does it push, and only the list they
  approved; a run that stages nothing asks no one.
- **A record.** Each run keeps who approved it, the stage report and `procurement.json` as it was
  approved, for as long as Jenkins keeps builds and their artifacts.

[examples/pipelines/Jenkinsfile](../../examples/pipelines/Jenkinsfile) is the example. Copy it into
your own pipeline repository and adapt it ([The example](#the-example)); nothing in this repository
runs it.

## Where it runs

The pipeline runs the same `docker compose run --rm uc ...` commands you would, so it must run on the
update center's Docker host, in the deployment directory: the directory you run `docker compose` in,
which holds `compose.yaml`, `.env` and `secrets/`. It is not a checkout of the
pipeline's repository. The example calls it `UC_DIR`, a parameter that defaults to
`/opt/easy-update-center`, and runs on a Jenkins agent on the host, labelled `uc-host`.

The agent's user needs:

- Docker, with Compose v2 (`docker compose`);
- membership of the `docker` group, or an equivalent way to reach the Docker daemon (which gives that
  user root on the host, so treat the agent accordingly);
- read access to `UC_DIR`;
- GNU make, only if it runs the `make procure-*` targets instead (below).

Before the first run:

- **Prepare the host** once, as for running the stages by hand
  ([Before you start](procure-missing-files.md#before-you-start)): fill in
  `secrets/repo_push_auth`, and run `docker compose run --rm setup procurement` (or `make setup`) so
  the uc containers can write `./procurement`. Where that step fails (a filesystem without ACLs),
  the uc containers cannot write the folder: change the pipeline's `uc procure stage` and
  `uc procure clean` commands to `make procure-stage` and `make procure-clean`, which hand the
  folder to the uc containers for each run and back to the user running them.
- **Set up the approval** ([The approval](#the-approval)): the users or groups `submitter` names must
  exist. Without them, no one but a Jenkins administrator can approve, and each run waits at the
  review until it is aborted.

## Choose what to stage

The stage step runs `uc procure stage` with no options, so what it stages comes from the
`PROCUREMENT_*` settings in `UC_DIR`'s `.env`. Set them before the first run, for
example `PROCUREMENT_POMS=true`, `PROCUREMENT_DEPENDENCIES=true` and `PROCUREMENT_WARS=required` in
`.env`. With none of them set, the stage step exits 2 and the run stops before the review. What each
one stages: [2. Stage](procure-missing-files.md#2-stage).

The example assumes the default staging folder: it reads the staged list as
`procurement/procurement.json` in `UC_DIR`, where `PROCUREMENT_STAGING_DIR`'s default,
`/procurement`, is mounted. If you set another folder, change that path in the stage step.

## The stages

Each `uc ...` command runs as `docker compose run --rm uc ...` in `UC_DIR` (`uc config` with
`--no-deps`):

| Stage | Runs | Notes |
|---|---|---|
| check | `uc config --check`, `uc doctor` | `uc doctor`'s exit 1 (something to procure) does not fail the stage; above 1 does. |
| stage | `uc procure stage` | Its output and `procurement.json` become build artifacts for the reviewers, with the checksum of `procurement.json`. |
| review | none | The approval ([below](#the-approval)). |
| push | `sha256sum -c`, then `uc procure push` | Stops, pushing nothing, if `procurement.json` changed since the review. |
| generate | `uc generate` | Publishes now instead of at the next schedule. |
| verify | `uc status`, `uc doctor` | `uc doctor`'s exit 1 does not fail it. |
| clean | `uc procure clean` | Deletes the staged copies the repository now holds. |

When a stage fails, the run stops there: the later stages, the push included, do not run.

When nothing was staged, the run skips the review and every stage after it, so no one is asked to
approve, and it succeeds. The example keys on one line of `stage-report.txt`, which
`uc procure stage` prints only when `procurement.json` lists files:

```text
next: review ./procurement (procurement.json lists N files), then uc procure push
```

Without it, each later stage's `when` skips it, decided before the stage takes an agent. A
**Restart from Stage** at `review` or later skips them too, and pushes nothing: the restarted run
lacks the `UC_STAGED` flag the `stage` stage sets. Restart from `stage` instead.

## The approval

The `review` stage's `input` step asks, with `submitter: 'uc-approvers'`, and holds no agent while it
waits. Set `submitter` to the users or groups allowed to approve, comma-separated. The build log
records who approved the run.

## What reviewers read

The stage step saves three files as artifacts of the build, also when it fails, so the reviewers can
read why:

- `stage-report.txt`: the output of `uc procure stage`, the doctor report and then one line per
  file, with why it was staged or why it failed;
- `procurement.json`: every staged file with its source URL, sha1, size and reason
  ([3. Review](procure-missing-files.md#3-review) explains each field);
- `procurement.sha256`: the checksum of that `procurement.json`.

The push step first runs `sha256sum -c` on `procurement.sha256` in `UC_DIR`, and stops if
`procurement/procurement.json` is not the list the reviewers read. The approval then covers exactly
what is pushed: a list that someone edited, or that a stage run by hand replaced after the review,
is not pushed. Run the pipeline again, and review the new list.

Each file `procurement.json` lists is checked again by `uc procure push`, which refuses one whose
content changed after it was staged.

## One run at a time

The staging folder is shared, so the example keeps runs apart with `disableConcurrentBuilds()`: a
second run waits until the first ends.

## Triggers

The example runs when you start it with **Build with Parameters**. A weekly trigger is included,
commented out: uncomment `triggers { cron('H 6 * * 1') }`. A scheduled run still waits for the
approval before it pushes.

## Exit codes in a pipeline

| Step | Exit code | What it means |
|---|---|---|
| `uc config --check` | 1 | The settings are invalid; the message names the setting. Fix `.env` in `UC_DIR`. |
| `uc doctor` | 1 | Expected: something will be dropped or a dependency is missing, which is what the run is for. The example lets it pass in the check and verify stages. An error, such as an unreachable repository, also exits 1 (`uc: error: ...`); the stage step then fails on it too. |
| `uc doctor` | 2 or more | An invalid command line: the stage fails. |
| `uc procure stage` | 1 | A file could not be staged (`FAILED` in `stage-report.txt`), or the run was refused (`uc: error: ...`, for example air-gapped without `PROCUREMENT_SOURCE_URL`). |
| `uc procure stage` | 2 | Nothing selected to stage: set `PROCUREMENT_POMS`, `PROCUREMENT_DEPENDENCIES` or `PROCUREMENT_WARS` ([above](#choose-what-to-stage)). |
| `sha256sum -c` | 1 | `procurement.json` changed after the review; nothing was pushed. |
| `uc procure push` | 1 | A file did not end up in the repository; its line says why ([4. Push](procure-missing-files.md#4-push)). Run the pipeline again once it is fixed: files already pushed show as `present`. |
| `uc generate` | 1 | The generation failed; the live release is unchanged. The next scheduled generation publishes the pushed files. |
| `uc generate` | 75 | Another generation or rollback was running. The pushed files go out with the next generation. |
| `uc procure clean` | 1 | A staged file differs from the repository's or could not be checked or deleted; it is kept. |

## The example

[Jenkinsfile](../../examples/pipelines/Jenkinsfile) is a Pipeline job (or a multibranch one in your
pipeline repository). Set `UC_DIR` as the job's parameter default and `submitter` to your approvers.
