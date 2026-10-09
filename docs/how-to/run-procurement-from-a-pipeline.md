# Run procurement from a pipeline

How to run the stages of [Procure missing POMs, dependencies and cores](procure-missing-files.md)
from Jenkins, GitLab CI or GitHub Actions, with an approval before anything is pushed.

## Why a pipeline

`uc procure` already keeps the stages apart: nothing reaches the repository until someone runs
`uc procure push`. A pipeline adds two things to running them by hand:

- **An approval before the push.** The pipeline stages the files, then stops until one of the people
  you named has read what was staged and approved it. Only then does it push, and only the list they
  approved; a run that stages nothing asks no one. The GitHub Actions example pushes only where the
  `procurement` environment has required reviewers, which a private repository on GitHub Free, Pro
  or Team cannot have ([The approval](#the-approval)).
- **A record.** Each run keeps who approved it, the stage report and `procurement.json` as it was
  approved, for as long as your CI system keeps runs and their artifacts.

`examples/pipelines/` holds one example for each system: a
[Jenkinsfile](../../examples/pipelines/Jenkinsfile), two GitLab CI files
([gitlab-ci.yml](../../examples/pipelines/gitlab-ci.yml) and
[gitlab-ci-procure.yml](../../examples/pipelines/gitlab-ci-procure.yml)) and a
[GitHub Actions workflow](../../examples/pipelines/github-actions.yml). Copy one into your own
pipeline repository and adapt it ([The examples](#the-examples)); nothing in this repository runs
them.

## Where it runs

The pipeline runs the same `docker compose run --rm uc ...` commands you would, so it must run on the
update center's Docker host, in the deployment directory: the directory you run `docker compose` in,
which holds `compose.yaml`, `.env`, `config/uc.toml` and `secrets/`. It is not a checkout of the
pipeline's repository. The examples call it `UC_DIR`, a pipeline variable that defaults to
`/opt/jenkins-update-center`, and run on:

| System | Runs on |
|---|---|
| Jenkins | An agent on the host, labelled `uc-host`. |
| GitLab CI | A shell runner on the host, tagged `uc-host`. |
| GitHub Actions | A self-hosted runner on the host, labelled `uc-host`. GitHub-hosted runners cannot reach a host inside your network. |

The agent's or runner's user needs:

- Docker, with Compose v2 (`docker compose`);
- membership of the `docker` group, or an equivalent way to reach the Docker daemon (which gives that
  user root on the host, so treat the runner accordingly);
- read access to `UC_DIR`;
- `curl`, for the GitHub Actions example, which checks the approval through GitHub's API;
- GNU make, only if it runs the `make procure-*` targets instead (below).

Before the first run:

- **Prepare the host** once, as for running the stages by hand
  ([Before you start](procure-missing-files.md#before-you-start)): fill in
  `secrets/repo_push_auth`, and run `docker compose run --rm setup procurement` (or `make setup`) so
  the uc containers can write `./procurement`. Where that step fails (a filesystem without ACLs),
  the uc containers cannot write the folder: change the pipeline's `uc procure stage` and
  `uc procure clean` commands to `make procure-stage` and `make procure-clean`, which hand the
  folder to the uc containers for each run and back to the user running them.
- **Set up the approval** ([The approval](#the-approval)):
  - GitHub Actions: create the `procurement` environment with required reviewers. Without them,
    GitHub creates the environment on the first run, unprotected, and runs the push job without
    waiting; the example's push job then stops at its first step, pushing nothing. Also turn off
    **Allow administrators to bypass configured protection rules** (the push job warns while it is
    on), and tick **Prevent self-review** if the person who starts a run must not approve it.
  - GitLab CI: protect the `procurement` environment and allow only the approvers to deploy to it
    (GitLab Premium or Ultimate). Without that, any developer who can run the pipeline can run the
    review job too, and so approve their own run.
  - Jenkins: the users or groups `submitter` names must exist. Without them, no one but a Jenkins
    administrator can approve, and each run waits at the review until it is aborted.

## Choose what to stage

The stage step runs `uc procure stage` with no options, so what it stages comes from the
`PROCUREMENT_*` settings in `UC_DIR`'s `.env` or `config/uc.toml`. Set them before the first run, for
example:

```toml
# config/uc.toml
[procurement]
poms = true
dependencies = true
wars = ["required"]
```

or `PROCUREMENT_POMS=true`, `PROCUREMENT_DEPENDENCIES=true` and `PROCUREMENT_WARS=required` in
`.env`. With none of them set, the stage step exits 2 and the run stops before the review. What each
one stages: [2. Stage](procure-missing-files.md#2-stage).

The examples assume the default staging folder: they read the staged list as
`procurement/procurement.json` in `UC_DIR`, where `PROCUREMENT_STAGING_DIR`'s default,
`/procurement`, is mounted. If you set another folder, change that path in the stage step.

## The stages

Each `uc ...` command runs as `docker compose run --rm uc ...` in `UC_DIR` (`uc config` with
`--no-deps`):

| Stage | Runs | Notes |
|---|---|---|
| check | `uc config --check`, `uc doctor` | `uc doctor`'s exit 1 (something to procure) does not fail the stage; above 1 does. |
| stage | `uc procure stage` | Its output and `procurement.json` become pipeline artifacts for the reviewers, with the checksum of `procurement.json`. In GitLab CI the review and every stage after it run as a child pipeline that the stage job prepares. |
| review | none | The approval ([below](#the-approval)). |
| push | `sha256sum -c`, then `uc procure push` | Stops, pushing nothing, if `procurement.json` changed since the review. On GitHub it first checks that the `procurement` environment has required reviewers. |
| generate | `uc generate` | Publishes now instead of at the next schedule. |
| verify | `uc status`, `uc doctor` | `uc doctor`'s exit 1 does not fail it. |
| clean | `uc procure clean` | Deletes the staged copies the repository now holds. |

When a stage fails, the run stops there: the later stages, the push included, do not run.

When nothing was staged, the run skips the review and every stage after it, so no one is asked to
approve, and it succeeds. The examples key on one line of `stage-report.txt`, which
`uc procure stage` prints only when `procurement.json` lists files:

```text
next: review ./procurement (procurement.json lists N files), then uc procure push
```

Without it:

- **Jenkins** skips the review and every later stage (a `when` on each, decided before the stage
  takes an agent). A **Restart from Stage** at `review` or later skips them too, and pushes
  nothing: the restarted run lacks the `UC_STAGED` flag the `stage` stage sets. Restart from `stage`
  instead.
- **GitHub Actions** skips the push job (its `if:` reads the stage job's `staged` output), and with
  it generate, verify and clean, which need it in turn.
- **GitLab CI** runs, as the child pipeline, one job that says nothing was staged, in place of
  `gitlab-ci-procure.yml`.

## The approval

| System | How the example asks | What to set up |
|---|---|---|
| Jenkins | The `review` stage's `input` step, with `submitter: 'uc-approvers'`. It holds no agent while it waits. | Set `submitter` to the users or groups allowed to approve, comma-separated. |
| GitLab CI | The `review` job of the child pipeline: `when: manual` on the `procurement` environment, with `allow_failure: false`, so the later jobs wait for it; the `push` job lists it in `needs` for the same reason. | Protect the `procurement` environment (Settings > CI/CD > Protected environments) and allow only the approvers to deploy to it. Protected environments need GitLab Premium or Ultimate; without them, any developer who can run the pipeline can also run the review job. |
| GitHub Actions | The `procurement` environment on the `push` job, which waits until a reviewer approves it. Without required reviewers GitHub would run the job without waiting, so its first step reads the environment through GitHub's API, with the job's token, and stops the job, pushing nothing, unless it has them. It warns, and carries on, where administrators can bypass the environment's protection rules. | Give the `procurement` environment required reviewers (Settings > Environments). In a private repository on GitHub Free, Pro or Team, an environment cannot have required reviewers, so the push job always stops. Use instead a plan that offers required reviewers in private repositories (GitHub Enterprise), a public repository (where every plan offers them, though GitHub recommends self-hosted runners only for private repositories), or the Jenkins or GitLab CI example. |

Each system records who approved the run: the Jenkins build log, the GitLab review job (it prints
`approved by <user>`) and the GitHub run's deployment review.

## What reviewers read

The stage step saves three files as artifacts of the run, also when it fails, so the reviewers can
read why (GitLab's also saves the child pipeline's configuration, `procure-pipeline.yml`, and
`uc-dir.env`, which records the `UC_DIR` it ran in):

- `stage-report.txt`: the output of `uc procure stage`, the doctor report and then one line per
  file, with why it was staged or why it failed;
- `procurement.json`: every staged file with its source URL, sha1, size and reason
  ([3. Review](procure-missing-files.md#3-review) explains each field);
- `procurement.sha256`: the checksum of that `procurement.json`.

The push step first runs `sha256sum -c` on `procurement.sha256` in `UC_DIR`, and stops if
`procurement/procurement.json` is not the list the reviewers read. The approval then covers exactly
what is pushed: a list that someone edited, or that another run or a stage run by hand replaced
after the review, is not pushed. Run the pipeline again, and review the new list.

Each file `procurement.json` lists is checked again by `uc procure push`, which refuses one whose
content changed after it was staged.

## One run at a time

The staging folder is shared, so the examples keep runs apart:

- **Jenkins**: `disableConcurrentBuilds()`: a second run waits until the first ends.
- **GitHub Actions**: a `concurrency` group: a second run waits too, but GitHub keeps only one
  pending run and cancels older pending ones, so a run queued behind another may be dropped.
- **GitLab CI**: the `jenkins-update-center` resource group on each job of `gitlab-ci.yml`. The
  `procure` job starts the child pipeline with `strategy: depend`, so it runs, and holds the
  resource group, until that pipeline ends, the review included: a second pipeline's check and
  stage wait for it. Cancel a pipeline whose review no one will run, or the next one waits forever.
  A resource group serializes jobs, not whole pipelines, though: between the first pipeline's stage
  job and its `procure` job, a second pipeline can still check and stage into the folder. The
  checksum is the guard there: the first pipeline's push then stops, because the list changed after
  it was staged. The child pipeline's jobs take no resource group: the `procure` job holds it until
  they end, so one waiting for it would wait forever.

## Triggers

The examples run when you start them: Jenkins' **Build with Parameters**, GitLab's **Build >
Pipelines > New pipeline** (**Run pipeline** before GitLab 17.7), or GitHub's **Run workflow** on the
workflow's Actions page. Each includes a weekly trigger, commented out:

- **Jenkins**: uncomment `triggers { cron('H 6 * * 1') }`.
- **GitLab CI**: uncomment the `schedule` rule under `workflow:`, then create the schedule in
  **Build > Pipeline schedules**.
- **GitHub Actions**: uncomment the `schedule:` block.

A scheduled run still waits for the approval before it pushes.

## Exit codes in a pipeline

| Step | Exit code | What it means |
|---|---|---|
| `uc config --check` | 1 | The settings are invalid; the message names the setting. Fix `.env` or `config/uc.toml` in `UC_DIR`. |
| `uc doctor` | 1 | Expected: something will be dropped or a dependency is missing, which is what the run is for. The examples let it pass in the check and verify stages. An error, such as an unreachable repository, also exits 1 (`uc: error: ...`); the stage step then fails on it too. |
| `uc doctor` | 2 or more | An invalid command line: the stage fails. |
| `uc procure stage` | 1 | A file could not be staged (`FAILED` in `stage-report.txt`), or the run was refused (`uc: error: ...`, for example air-gapped without `PROCUREMENT_SOURCE_URL`). |
| `uc procure stage` | 2 | Nothing selected to stage: set `PROCUREMENT_POMS`, `PROCUREMENT_DEPENDENCIES` or `PROCUREMENT_WARS` ([above](#choose-what-to-stage)). |
| GitHub's reviewers check | 1 | The `procurement` environment has no required reviewers, or could not be read; nothing was pushed. |
| `sha256sum -c` | 1 | `procurement.json` changed after the review; nothing was pushed. |
| `uc procure push` | 1 | A file did not end up in the repository; its line says why ([4. Push](procure-missing-files.md#4-push)). Run the pipeline again once it is fixed: files already pushed show as `present`. |
| `uc generate` | 1 | The generation failed; the live release is unchanged. The next scheduled generation publishes the pushed files. |
| `uc generate` | 75 | Another generation or rollback was running. The pushed files go out with the next generation. |
| `uc procure clean` | 1 | A staged file differs from the repository's or could not be checked or deleted; it is kept. |

## The examples

- [Jenkinsfile](../../examples/pipelines/Jenkinsfile): a Pipeline job (or a multibranch one in your
  pipeline repository). Set `UC_DIR` as the job's parameter default and `submitter` to your
  approvers.
- [gitlab-ci.yml](../../examples/pipelines/gitlab-ci.yml): the `.gitlab-ci.yml` of a project whose
  shell runner sits on the host, with
  [gitlab-ci-procure.yml](../../examples/pipelines/gitlab-ci-procure.yml) beside it at the root of
  the repository: copy both. The first checks and stages; its `procure` job then starts the second,
  the review and what follows it, as a child pipeline. Set `UC_DIR` under `variables:` of
  `gitlab-ci.yml`, or as a CI/CD variable of the project, group or instance: the stage job passes
  the directory it worked in to the child pipeline, so both work in the same one. Protect the
  `procurement` environment.
- [github-actions.yml](../../examples/pipelines/github-actions.yml): a workflow in
  `.github/workflows/` of a private repository whose self-hosted runner sits on the host. Set
  `UC_DIR` under `env:` and give the `procurement` environment required reviewers, which a private
  repository has only with GitHub Enterprise ([The approval](#the-approval)); without them, the push
  job stops.

[examples/pipelines/README.md](../../examples/pipelines/README.md) sums them up, with a shorter stage
table.
