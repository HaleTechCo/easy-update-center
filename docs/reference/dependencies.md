# Dependencies

Everything the update center is built from and runs on, with versions, pins and licenses.

Versions marked "floats" follow their base image or distribution; the value shown is what a build
on 2026-09-24 produced. Pins are enforced at build time.

## uc image

The image behind the `uc`, `scheduler` and `repo-adapter` services. Built by `make build` from
[`uc/Dockerfile`](../../uc/Dockerfile).

| Component | Version or pin | Pinned in | License | Why |
|---|---|---|---|---|
| `eclipse-temurin:21-jre` base image | tag `21-jre`, floats (Ubuntu 26.04, OpenJDK 21.0.12) | `uc/Dockerfile` | GPL-2.0-only WITH Classpath-exception-2.0 | The Java runtime for the generator. |
| update-center2 | `3.18.4`, commit `b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd`, checked at build | `uc/Dockerfile` (`UC2_VERSION`, `UC2_COMMIT`) | none stated for the repository; the files that carry a header are MIT (see [note](#note-on-update-center2s-license)) | The generator Jenkins' own update center runs, built from source. |
| `0001-popularity-configurable-and-optional.patch` | in this repository | `uc/patches/` | MIT | Makes the popularity URL configurable (`POPULARITY_JSON_URL`) and its absence non-fatal. |
| `0002-core-age-configurable.patch` | in this repository | `uc/patches/` | MIT | Makes the 400-day core age for tiers configurable (`CORE_AGE_DAYS`, `0` for no limit). |
| update-center2's Java libraries | resolved by Maven for 3.18.4 (29 jars) | update-center2's `pom.xml` | various; see [below](#list-the-generators-java-libraries) | The generator's own dependencies. |
| supercronic | `v0.2.49`, SHA-256 per architecture (amd64 `a53ae236...0c1`, arm64 `02aa0cb2...dd5`) | `uc/Dockerfile` (`SUPERCRONIC_VERSION`, checksums) | MIT | Runs `uc generate` on `GENERATE_SCHEDULE`. |
| python3 | distribution package, floats (3.14.4) | not pinned | PSF-2.0 | Runs the `uc` command. Standard library only: no pip packages. |
| openssl | distribution package, floats (3.5.5) | not pinned | Apache-2.0 | Signing PKI (`uc pki`) and certificate checks. |
| tini | distribution package, floats | not pinned | MIT | Init process: forwards signals, reaps children. |
| ca-certificates | distribution package, floats | not pinned | MPL-2.0 (certificate data), GPL-2.0-or-later (scripts) | TLS trust for the repository and upstream hosts. |
| curl | distribution package, floats | not pinned | curl | Fetches supercronic during the build; stays installed for troubleshooting. |
| VersionTool | in this repository | `uc/java/VersionTool.java` | MIT | Compares Jenkins versions with the generator's own `VersionNumber`; compiled at build time. |
| `uc` package | in this repository | `uc/uc/` | MIT | Configuration, the generation pipeline, the repo-adapter, `uc doctor`. |

## web image

The image behind the `web` service. Built from [`web/Dockerfile`](../../web/Dockerfile).

| Component | Version or pin | Pinned in | License | Why |
|---|---|---|---|---|
| `httpd:2.4` base image | tag `2.4`, floats (Debian 13, Apache 2.4.68) | `web/Dockerfile` | Apache-2.0 | Serves the release and runs upstream's `.htaccess` routing. |
| ca-certificates | distribution package, floats | not pinned | MPL-2.0 (certificate data), GPL-2.0-or-later (scripts) | Verifies the download backend in proxy mode. |
| curl | distribution package, floats | not pinned | curl | The container health check. |
| openssl | distribution package, floats | not pinned | Apache-2.0 | Only for the configuration check during the build. |
| Apache configuration and scripts | in this repository | `web/conf/`, `web/*.sh` | MIT | Download routing, headers, TLS, the entrypoint and health check. |

## Build time

Used by `make build`, not shipped in the runtime images.

| Component | Version or pin | License | Why |
|---|---|---|---|
| `maven:3.9-eclipse-temurin-21` | tag, floats | Apache-2.0 (Maven), GPL-2.0-only WITH Classpath-exception-2.0 (JDK) | Builds and tests update-center2 with the patches applied, and compiles VersionTool. |
| git | distribution package, floats | GPL-2.0-only | Clones update-center2 at the pinned tag. |

The Maven build downloads update-center2's dependencies; see
[Outbound: image builds](network.md#outbound-image-builds).

## Test only

| Component | Version | License | Why |
|---|---|---|---|
| python3-pytest | distribution package in the `test` image stage | MIT | Runs the unit, routing and integration suites. |
| git | distribution package in the `test` image stage | GPL-2.0-only | Used by tests and by `make upstream-diff`. |
| `jenkins/jenkins` images | `lts` and `latest` tags | MIT | Real controllers in `make it-airgapped` and `make it-online`. |
| Fake Artifactory and Artifact Keeper | in this repository | MIT | Stand-ins for the repository in `make it-airgapped`. |

## Development tools

Installed on the developer's machine for the pre-commit hooks; see [CONTRIBUTING](../../CONTRIBUTING.md).
`requirements-dev.txt` leaves them unpinned on purpose: it is a local lint environment, not a
delivered artifact, and an unpinned one picks up checker fixes without a bump commit.

| Tool | Source | License | Required? |
|---|---|---|---|
| pre-commit, pre-commit-hooks | `requirements-dev.txt` | MIT | yes |
| ruff | `requirements-dev.txt` | MIT | yes |
| yamllint | `requirements-dev.txt` | GPL-3.0-or-later | yes |
| ShellCheck (via shellcheck-py) | `requirements-dev.txt` | GPL-3.0-or-later (shellcheck-py wrapper: MIT) | yes |
| hadolint | run as a container by the hook | GPL-3.0-only | when a Dockerfile changes |
| shfmt | installed separately | BSD-3-Clause | optional (the hook skips when absent) |
| PSScriptAnalyzer | installed separately | MIT | optional |
| golangci-lint | installed separately | GPL-3.0-only | optional (no Go code today) |

## Pinning policy

- **Pinned:** update-center2's tag, with its commit checked during the build, and the two patches,
  which must apply cleanly or the build fails; supercronic's version and per-architecture SHA-256.
  These are what decide the generated metadata and what runs with the signing key.
- **Floating:** the base image tags (`eclipse-temurin:21-jre`, `httpd:2.4`,
  `maven:3.9-eclipse-temurin-21`) and every distribution package. The distributions drop superseded
  package versions from their archives, so a pinned package version breaks the build as soon as the
  base image moves on. Rebuilding (`make build`) picks up their security fixes.
- Upgrading update-center2 is a deliberate step: [Upgrading update-center2](../development/upgrading-upstream.md).

## List the generator's Java libraries

The jars the image ships, update-center2 itself included:

```bash
docker run --rm --entrypoint ls jenkins-uc/uc:local /opt/uc/generator
```

(From Git Bash on Windows, prefix it with `MSYS_NO_PATHCONV=1`.) Each jar's license is in its own
Maven metadata; update-center2's `pom.xml` at the pinned tag lists them.

### Note on update-center2's license

The jenkins-infra/update-center2 repository has no LICENSE file and declares no license in its
`pom.xml`. Some of its source files carry the MIT license header used across the Jenkins project;
the rest carry no notice. The two patches in `uc/patches/` are this project's own work, under this
project's MIT license. If your organization reviews licenses before redistributing images, include
this in the review.
