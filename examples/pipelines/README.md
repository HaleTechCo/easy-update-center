# Example pipelines

Three pipelines that run procurement with an approval before anything is pushed. They are examples
to copy into your own pipeline repository; nothing in this repository runs them. Copy
`github-actions.yml` to `.github/workflows/` of the repository whose runner sits on the update
center's host. The GitLab CI example is two files: copy `gitlab-ci.yml` as `.gitlab-ci.yml` and
`gitlab-ci-procure.yml` beside it, at the root of the repository. The first checks and stages; the
second, the review and what follows it, runs as a child pipeline. Set `UC_DIR` in `gitlab-ci.yml` or
as a CI/CD variable of the project, group or instance: the stage job passes the directory it worked
in to the child pipeline.

Each one runs where the update center runs: a Jenkins agent labelled `uc-host`, a GitLab shell runner
tagged `uc-host`, or a GitHub self-hosted runner labelled `uc-host`. Every command runs in the
deployment directory `UC_DIR` (a pipeline variable, default `/opt/jenkins-update-center`), which holds
`.env`, `config/uc.toml` and `secrets/` and is not a checkout of the pipeline's repository. They are
triggered by hand (a weekly trigger is included, commented out). Jenkins and GitHub allow one run at a
time. GitLab's resource group holds from the job that starts the child pipeline until that pipeline
ends; before that it is taken one job at a time, so the checksum below is the guard there. A GitLab
pipeline left waiting at its review keeps the resource group, so later pipelines wait until someone
approves or cancels it.

Set up the approval before the first run:

- GitHub: create the `procurement` environment with required reviewers. Without them, GitHub creates
  it unprotected and runs the push job without waiting, so the push job's first step checks for
  them through GitHub's API (it needs `curl` on the runner) and stops, pushing nothing. In a private
  repository on GitHub Free, Pro or Team, an environment cannot have required reviewers, so the push
  job always stops. There, use GitHub Enterprise (required reviewers in private repositories), a
  public repository, or the Jenkins or GitLab CI example. Also turn off **Allow administrators to
  bypass configured protection rules** (the push job warns while it is on), and tick **Prevent
  self-review** if the person who starts a run must not approve it.
- GitLab: protect the `procurement` environment and allow only the approvers to deploy to it
  (GitLab Premium or Ultimate). Without that, any developer who can run the pipeline can also run
  the review job.
- Jenkins: the users or groups `submitter` names must exist. Without them, no one but a Jenkins
  administrator can approve, and the run waits at the review until it is aborted.

A run that stages nothing ends without asking anyone to approve. `uc procure stage` prints
`next: review ...` only when `procurement.json` lists files, and the examples key on that line:
without it, Jenkins skips the review and every later stage, GitHub skips the push job and every job
after it, and GitLab's child pipeline is one job that says nothing was staged.

The examples assume the default staging folder (`PROCUREMENT_STAGING_DIR` unset): they read
`procurement/procurement.json` in `UC_DIR`. With another folder, change that path in the stage step.

The stage step records the checksum of the staged list (`procurement.sha256`) beside the artifacts
the reviewers read. The push step runs `sha256sum -c` on it in `UC_DIR` and stops if
`procurement/procurement.json` changed since the review. GitLab's `resource_group` serializes jobs,
not whole pipelines: another pipeline can check and stage between one pipeline's stage job and the
job that starts its child pipeline, so there this check is what guarantees that only reviewed files
are pushed.

| Stage | Runs | Notes |
|---|---|---|
| check | `uc config --check`, `uc doctor` | `uc doctor` exit 1 (something to procure) does not fail the stage; above 1 does. |
| stage | `uc procure stage` | Its output and `procurement.json` become pipeline artifacts for the reviewers. |
| review | none | Jenkins `input` with `submitter`; a GitLab `when: manual` job on a protected environment, in the child pipeline; a GitHub environment with required reviewers on the push job, which checks that it has them. When nothing was staged, no one is asked and no later stage runs. |
| push | `uc procure push` | |
| generate | `uc generate` | Publishes now instead of at the next schedule. |
| verify | `uc status`, `uc doctor` | `uc doctor` exit 1 does not fail the stage. |
| clean | `uc procure clean` | |

How to set them up, the approval in each system and what each exit code means:
[Run procurement from a pipeline](../../docs/how-to/run-procurement-from-a-pipeline.md).
