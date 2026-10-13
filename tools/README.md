# `tools/` - this repository's own hooks and the release helper

**This directory is not project code.** Nothing here generates, signs or serves an update center.
It holds the checks and shims that [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)
runs as `local` hooks, and `release.py`, the release helper. Nothing else imports it. The update
center itself is the `uc` package under [`uc/uc/`](../uc/uc/) and the web image under
[`web/`](../web/). For contributing in general, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Setup

pre-commit and the hooks: [CONTRIBUTING.md](../CONTRIBUTING.md#set-up). The hooks in this
directory are the `local` ones in `.pre-commit-config.yaml`; pre-commit runs them in its own Python
environment, so they need nothing installed.

## Why the shims are Python

A hook sometimes needs to skip cleanly when a tool is absent, or redirect stdin - things a plain
`entry:` line cannot do. The obvious answer would be `bash -c`. It is not portable: on Windows the
`bash` on PATH can be WSL bash, with its own PATH (the Windows toolchain is invisible to it) and its
own `/mnt/c` view (staged paths do not resolve). So the wrappers below are Python instead.

## What each file does

| File | Role |
| --- | --- |
| `check_doc_links.py` | Checks that every relative Markdown link, and every bare `path/to/page.md#anchor` token in a comment of any other tracked file that can hold one (Python, YAML, shell, `.conf`, Makefile, Dockerfiles, `.env` examples), resolves - file and heading anchor both. Scans the whole tracked tree on every run, so renaming a heading is caught by the links pointing AT it, not only the ones in the files you changed. Anchors follow GitHub's heading algorithm. |
| `check_settings_documented.py` | Fails when the settings the code reads (`uc/uc/config.py`, `web/entrypoint.sh`, the compose files) and the settings [`docs/reference/configuration.md`](../docs/reference/configuration.md) documents differ, in either direction. Names under that page's "Read by bundled software" heading need no reader here. |
| `check_gremlins.py` | Finds, and with `--fix` rewrites, Unicode "gremlins" in text files - smart quotes, em/en dashes, ellipses, NBSP, zero-width characters, BOMs, bidi controls. Run as a **fixer**, so it reports `Failed` on the run that changes a file. Box drawing, `§`, arrows and middle dots are left alone. |
| `check_secrets.py` | Refuses a commit containing a repository credential that GitHub's push protection does not catch - an Artifactory API key or reference token, a JWT, or a `scheme://user:pass@host` credentialed URL. A finding line shows a provider shape as its first twelve characters - enough to say which shape matched - and a credentialed URL as `scheme://user:***@`, the whole password masked. |
| `run_optional.py` | Runs a tool if it is on PATH, otherwise exits 0. **Read the caveat below.** |
| `run_hadolint.py` | Lints a Dockerfile with hadolint, running it as a container. |
| `release.py` | The release helper (see [Releasing](../docs/development/releasing.md)): `prepare` makes the release pull request's change (CHANGELOG.md's Unreleased entries under the version, `compose.yaml`'s images set to it), `check` refuses a tag the Release workflow must not publish, `notes` prints a version's section for the GitHub Release, `latest` prints the highest release. |

These are ordinary repo code: ruff checks them exactly like the code under `uc/`.

## The thing that will bite you

**`run_optional.py` skips silently.** A hook wrapped in it does not fail when its tool is missing;
it passes. The hooks wrapped this way are the three `docker compose config` hooks, so on a machine
without Docker a green commit does not mean the compose files were validated (CI runs them).
`hadolint` is not wrapped: it fails when Docker is unreachable, because a Dockerfile changed and
went unlinted.

## Running them

They run as part of `pre-commit`; how to run them by hand, and how fixer hooks report:
[Lint](../CONTRIBUTING.md#lint).

## Dependencies

The shims use the standard library only. The tools they wrap, `docker` and hadolint (run as a
container), come from the host.
