# Example pipelines

Three pipelines that run procurement with an approval before anything is pushed. They are examples
to copy into your own pipeline repository; nothing in this repository runs them. Copy
`github-actions.yml` to `.github/workflows/` of the repository whose runner sits on the update
center's host.

Each one runs where the update center runs: a Jenkins agent labelled `uc-host`, a GitLab shell runner
tagged `uc-host`, or a GitHub self-hosted runner labelled `uc-host`. Every command runs in the
deployment directory `UC_DIR` (a pipeline variable, default `/opt/jenkins-update-center`), which holds
`.env`, `config/uc.toml` and `secrets/` and is not a checkout of the pipeline's repository. They are
triggered by hand (a weekly trigger is included, commented out). Jenkins and GitHub allow one run at a
time; GitLab allows one job at a time, so the checksum below is the guard there.

The stage step records the checksum of the staged list (`procurement.sha256`) beside the artifacts
the reviewers read. The push step runs `sha256sum -c` on it in `UC_DIR` and stops if
`procurement/procurement.json` changed since the review. GitLab's `resource_group` only serializes
jobs, not whole pipelines, so this check is what guarantees that only reviewed files are pushed.

| Stage | Runs | Notes |
|---|---|---|
| check | `uc config --check`, `uc doctor` | `uc doctor` exit 1 (something to procure) does not fail the stage; above 1 does. |
| stage | `uc procure stage` | Its output and `procurement.json` become pipeline artifacts for the reviewers. |
| review | none | Jenkins `input` with `submitter`; a GitLab `when: manual` job on a protected environment; a GitHub environment with required reviewers on the push job. |
| push | `uc procure push` | |
| generate | `uc generate` | Publishes now instead of at the next schedule. |
| verify | `uc status`, `uc doctor` | `uc doctor` exit 1 does not fail the stage. |
| clean | `uc procure clean` | |

How to set them up, the approval in each system and what each exit code means:
[Run procurement from a pipeline](../../docs/how-to/run-procurement-from-a-pipeline.md).
