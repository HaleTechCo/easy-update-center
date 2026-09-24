# `tools/` - pre-commit shims

**This directory is not project code.** Nothing here generates, signs or serves an update center.
It holds the small helper scripts that [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)
invokes, and nothing else imports it. The update center itself is the `uc` package under
[`uc/uc/`](../uc/uc/) and the web image under [`web/`](../web/).

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Linux/Mac: .venv/bin/pip
.venv/Scripts/pre-commit install                    # run the hooks on every git commit
.venv/Scripts/pre-commit run --all-files            # run them now
```

The venv **must** be active when you `git commit`, or every hook fails with "command not found",
because they are all `language: system`:

```bash
source .venv/Scripts/activate    # Windows;  Linux/Mac: source .venv/bin/activate
```

The checker settings live beside the hooks: ruff in [`pyproject.toml`](../pyproject.toml)
(`[tool.ruff]`), yamllint in [`.yamllint`](../.yamllint), shellcheck in
[`.shellcheckrc`](../.shellcheckrc).

## Why these exist at all

Every hook in this repo is `repo: local` + `language: system`, so nothing is cloned at install or
run time. That leaves one problem: a hook sometimes needs to change directory, skip cleanly when a
tool is absent, or redirect stdin - things a plain `entry:` line cannot do.

The obvious answer would be `bash -c`. It is not portable: on Windows the `bash` on PATH can be WSL
bash, with its own PATH (the Windows toolchain is invisible to it) and its own `/mnt/c` view
(staged paths do not resolve). So the wrappers below are Python instead, and run on the venv
interpreter pre-commit already needs.

## What each file does

| File | Role |
| --- | --- |
| `check_doc_links.py` | Checks that every relative Markdown link, and every bare `path/to/page.md#anchor` token in a comment of any other tracked file that can hold one (Python, YAML, shell, `.conf`, Makefile, Dockerfiles, `.env` examples), resolves - file and heading anchor both. Scans the whole tracked tree on every run, so renaming a heading is caught by the links pointing AT it, not only the ones in the files you changed. Anchors follow GitLab's heading algorithm. |
| `check_gremlins.py` | Finds, and with `--fix` rewrites, Unicode "gremlins" in text files - smart quotes, em/en dashes, ellipses, NBSP, zero-width characters, BOMs, bidi controls. Run as a **fixer**, so it reports `Failed` on the run that changes a file. Box drawing, `§`, arrows and middle dots are left alone. |
| `check_secrets.py` | Refuses a commit containing something that looks like a real provider credential - a Google/AWS/GitHub/Slack/Stripe/Postgres shape, an Artifactory API key or reference token, a JWT, or a general `scheme://user:pass@host` credentialed URL. A finding line shows a provider shape as its first twelve characters - enough to say which shape matched - and a credentialed URL as `scheme://user:***@`, the whole password masked. |
| `run_optional.py` | Runs a tool if it is on PATH, otherwise exits 0. **Read the caveat below.** |
| `run_in_dir.py` | Runs a tool with the working directory set to a subdirectory. |
| `run_hadolint.py` | Lints a Dockerfile with hadolint, running it as a container. |
| `run_psscriptanalyzer.py` | Lints staged `.ps1` files with PSScriptAnalyzer, skipping cleanly when it is absent. |
| `run_psscriptanalyzer.ps1` | The PowerShell half of the above, at `-Severity Error`. |

These are ordinary repo code: ruff checks them exactly like the code under `uc/`.

## The thing that will bite you

**`run_optional.py` skips silently, which is a real hazard.** A hook wrapped in it does not fail
when its tool is missing; it passes. So a green commit is not evidence that hook actually ran.

Before concluding a hook is inert, check whether its `entry:` actually routes through
`run_optional.py`, and check PATH **with the venv active**. `shellcheck`, for one, ships as a
precompiled binary in `.venv/Scripts/` via the `shellcheck-py` package, so `which shellcheck`
outside the venv says nothing about whether its hook runs. The hooks wrapped this way here are
`shfmt`, the Go hooks and the two `docker compose config` hooks; `hadolint` is not - it fails when
Docker is unreachable, because a Dockerfile changed and went unlinted.

## Running them

You should not normally need to. They run as part of `pre-commit`:

```bash
pre-commit run --all-files          # everything
pre-commit run unicode-gremlins --all-files   # one hook
```

Fixer hooks (`ruff-format`, the whitespace fixers, `unicode-gremlins`, `shfmt`) rewrite files and
therefore report **Failed** on the run that changes something. Re-run - a second consecutive clean
run is the real signal.

## Dependencies

The shims use the standard library only. The tools they wrap (`shfmt`, `hadolint`, `docker`,
`golangci-lint`, `go`, PSScriptAnalyzer) are installed separately; `shellcheck`, `ruff` and
`yamllint` come from `requirements-dev.txt`.
