# Contributing

How to set up, test and submit changes to this project.

## Prerequisites

- Docker with Compose v2, and GNU make. Every test suite runs in containers built from this tree.
- Python 3 on the host, only for the lint tools behind the pre-commit hooks.
- git.

On Windows, use Git Bash or WSL; the Makefile sets `MSYS_NO_PATHCONV=1` for its own Docker commands.

## Set up

```bash
git clone https://github.com/HaleTechCo/easy-update-center.git
cd easy-update-center
make build                                         # both images, and the uc test image

python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt  # Linux/macOS: .venv/bin/pip
.venv/Scripts/pre-commit install                   # run the hooks on every commit
```

The hooks run tools from the venv (`language: system`), so **activate the venv in every shell you
commit from**: `source .venv/Scripts/activate` (Linux/macOS: `source .venv/bin/activate`). Optional
tools and the details of each hook: [tools/README.md](tools/README.md).

The checker settings live beside the hooks: ruff in `pyproject.toml`, yamllint in `.yamllint`,
shellcheck in `.shellcheckrc`.

Plain `make` runs `make help`: the contributor targets, each with one line on what it does. Operators
use `docker compose` alone. `make build` runs `docker build`, not `docker compose build`: Compose
refuses to load the project without a `.env`, and `make build` must work on a fresh clone.

## Repository layout

```text
uc/                   the uc image: Dockerfile, update-center2 patches, VersionTool, templates,
                      licenses/ (third-party notices shipped in the image)
uc/bin/               the image's commands: uc, and uc-setup, the setup service's entrypoint
uc/uc/                the uc Python package (standard library only): CLI, generation, doctor,
                      PKI, status; repo_adapter/ is the repo-adapter service
web/                  the web image: Apache configuration, entrypoint, health check
compose.yaml          the stack; compose.airgapped.yaml is the air-gapped override
config/overlay/       operator overlay merged into upstream's resources/
examples/pipelines/   an example Jenkins pipeline for procurement, to copy
secrets/              keys and credentials (gitignored except its README)
tests/unit/           pytest, run in the uc test image
tests/routing/        the web image's routing against generated fixture sites
tests/web/            the web entrypoint's configuration checks
tests/compose/        network and secret isolation of the Compose files
tests/setup/          uc-setup: first-time setup, the modes in secrets/, pki-init and pki-rotate
tests/integration/    end to end: fake Artifactory and Artifact Keeper, real Jenkins controllers
tests/support/        helpers shared by the suites
tools/                pre-commit hook scripts, and the Release workflow's helper
.github/              workflows, their scripts, Dependabot, issue and pull request templates
docs/                 the documentation
```

How the pieces fit: [Architecture overview](docs/architecture/overview.md).

## Tests

| Command | What it runs | Needs |
|---|---|---|
| `make test` | Unit, web entrypoint, routing and setup tests. The gate for every change. | Docker |
| `make test-unit ARGS='-k name'` | A subset of the unit tests. | Docker |
| `make compose-check` | Both Compose configurations, and the network and secret isolation the repo-adapter relies on. | Docker |
| `make it-airgapped` | End to end against a fake Artifactory and a fake Artifact Keeper, with real Jenkins controllers installing plugins. | Docker |
| `make it-online` | End to end against repo.jenkins-ci.org, capped at 25 plugins. | `JENKINS_COMMUNITY_USERNAME` and `JENKINS_COMMUNITY_PASSWORD` exported (a free account); optional `GITHUB_USERNAME`, `GITHUB_TOKEN` |
| `make it-full` | The online run with the whole catalog. Takes hours. | as above |
| `make test-setup` | `uc/bin/uc-setup`, the setup service's entrypoint, run as uid 1000 inside the uc image with real Linux permissions: first-time setup, the modes, `pki-init` and `pki-rotate` (part of `make test`). | Docker |
| `make test-pipelines` | The example pipeline in `examples/pipelines/`: it runs the procurement stages in the order of the how-to, with the approval before the push and none when nothing was staged, and checks before pushing that `procurement.json` is the one reviewed (`tests/unit/test_pipeline_examples.py`, so part of `make test-unit`). | Docker |
| `make it-clean` | Removes the integration suites' containers and volumes. | Docker |

**CI** (`.github/workflows/ci.yml`) runs on every pull request and push to `main`: the pre-commit
hooks, the suites of `make test`, `make compose-check` and `make it-airgapped` for both repository
types, with the images built once and loaded by each job (`make <target> PREBUILT=1` keeps an image
that exists instead of building it). The `ci` check must pass before a pull request can merge.
`it-online` runs weekly (`.github/workflows/scheduled.yml`).

Unit tests read `uc/uc/`, `uc/templates/`, `tools/`, `examples/pipelines/` and `.github/` straight
from the working tree, so they need no rebuild. Changing `uc/java/VersionTool.java` or a Dockerfile
needs `make build` first.

The example settings files (`.env.online.example`, `.env.airgapped.example`) are generated from the settings registry, `uc/uc/settings.py`. After
changing a setting there, run `make examples` and commit what it rewrites; `make test-unit` fails
while a committed copy is out of date.

## Lint

```bash
pre-commit run --all-files
```

Fixer hooks (formatting, whitespace, typographic characters) rewrite files and report `Failed` on the
run that changes something; run again, and a second clean run is the real signal. Actionlint checks
the workflows (installed in the venv as `actionlint-py`). Two hooks guard the documentation:

- `check-doc-links`: every relative link and every `docs/<page>.md#anchor` reference in a comment
  resolves, with GitHub's heading anchors;
- `check-settings-documented`: every setting the code reads is in
  [docs/reference/configuration.md](docs/reference/configuration.md), and nothing documented there is
  unread.

## Commits and pull requests

- One logical change per commit. Subjects in the imperative mood, with an optional area prefix:
  `repo-adapter: ...`, `web: ...`, `docs: ...`.
- Tests with every behavior change: a failing test first, then the fix.
- Before opening a pull request: `make test` passes (and `make it-airgapped` if generation, the
  repo-adapter or the web tier changed), `pre-commit run --all-files` passes, the docs describe the
  change (a new setting fails the settings hook until it is documented), and `CHANGELOG.md` has an
  entry under "Unreleased".
- Security issues go through [SECURITY.md](SECURITY.md), never a public issue or pull request.

By contributing you agree that your contribution is licensed under the project's
[MIT license](LICENSE), and to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Maintainer tasks

- Moving to a new update-center2 release: [Upgrading update-center2](docs/development/upgrading-upstream.md).
- The GitHub repository's settings: [GitHub setup](docs/development/github-setup.md).
- Cutting a release: [Releasing](docs/development/releasing.md).
- Keep [Dependencies](docs/reference/dependencies.md) in step with the Dockerfiles, and
  `uc/licenses/` in step with what the uc image ships (the build checks the Java libraries:
  [Third-party notices](docs/reference/dependencies.md#third-party-notices)).
