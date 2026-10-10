# Dependencies

Everything the update center is built from and runs on, with versions, pins and licenses.

Versions marked "floats" follow their base image or distribution; the value shown is what a build
on 2026-09-24 produced. Pins are enforced at build time.

## uc image

The image behind the `uc`, `scheduler`, `repo-adapter` and `setup` services. Built by
`docker compose build` (or `make build`) from [`uc/Dockerfile`](../../uc/Dockerfile).

| Component | Version or pin | Pinned in | License | Why |
|---|---|---|---|---|
| `eclipse-temurin:21-jre` base image | tag `21-jre`, floats (Ubuntu 26.04, OpenJDK 21.0.12) | `uc/Dockerfile` | GPL-2.0-only WITH Classpath-exception-2.0 | The Java runtime for the generator. |
| update-center2 | `3.18.4`, commit `b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd`, checked at build | `uc/Dockerfile` (`UC2_VERSION`, `UC2_COMMIT`) | MIT, under the Jenkins project's governance (see [note](#note-on-update-center2s-license)) | The generator Jenkins' own update center runs, built from source. |
| `0001-popularity-configurable-and-optional.patch` | in this repository | `uc/patches/` | MIT | Makes the popularity URL configurable (`POPULARITY_JSON_URL`) and its absence non-fatal. |
| `0002-core-age-configurable.patch` | in this repository | `uc/patches/` | MIT | Makes the 400-day core age for tiers configurable (`CORE_AGE_DAYS`, `0` for no limit). |
| update-center2's Java libraries | resolved by Maven for 3.18.4 (28 jars) | update-center2's `pom.xml` | Apache-2.0, MIT, BSD-style, Bouncy Castle; one LGPL-2.1 (see [Third-party notices](#third-party-notices)) | The generator's own dependencies. |
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

Used while building the images, not shipped in the runtime images.

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
  base image moves on. Rebuilding (`docker compose build`, or `make build`) picks up their security
  fixes.
- Upgrading update-center2 is a deliberate step: [Upgrading update-center2](../development/upgrading-upstream.md).

## List the generator's Java libraries

The jars the image ships, update-center2 itself included:

```bash
docker run --rm --entrypoint ls easy-update-center/uc:local /opt/uc/generator
```

(From Git Bash on Windows, prefix it with `MSYS_NO_PATHCONV=1`.) Their licenses are listed in the
image, in `/opt/uc/licenses/THIRD-PARTY.txt`.

## Third-party notices

The uc image carries the notices for everything it ships that is not this project's own code, in
`/opt/uc/licenses/`:

| File | Contents |
|---|---|
| `NOTICE.txt` | What third-party software the image contains, where it is, and under which license. Start here. |
| `THIRD-PARTY.txt` | Every Java library update-center2 depends on, with its Maven coordinates, license and project URL, generated by the build. |
| `update-center2-MIT.txt` | update-center2's MIT License, with the copyright lines of its source files. |
| `Apache-2.0.txt`, `MIT.txt`, `LGPL-2.1.txt`, `jaxen-BSD.txt`, `dom4j.txt`, `BouncyCastle.txt` | The license texts the libraries use, as published by each license's author or project. |
| `supercronic-MIT.txt` | supercronic's license. |

To read them: `docker run --rm --entrypoint cat easy-update-center/uc:local /opt/uc/licenses/NOTICE.txt`.

The build keeps them complete. Maven's license plugin lists each runtime library with the license
its POM declares, and the build fails when a library has no known license, or when a jar the image
ships is not in the list. A library whose POM declares no license is recorded, with the
evidence, in `uc/licenses/THIRD-PARTY.properties`; the committed texts live in `uc/licenses/`. See
[Upgrading update-center2](../development/upgrading-upstream.md#3-update-the-docs) for what to do when
the check fails.

Two libraries need more than a license name:

- `com.github.spotbugs:spotbugs-annotations` is licensed under the GNU LGPL 2.1. It is shipped
  unmodified, as its own jar that can be replaced, with the license text and a pointer to its source
  (https://github.com/spotbugs/spotbugs, and the `-sources.jar` on Maven Central).
- `org.jvnet.hudson:crypto-util` declares no license in its POM or its parents. The two classes its
  1.0 jar ships carry the MIT License header in its source
  (https://github.com/jenkinsci/lib-crypto-util), so it is recorded as MIT.

The web image ships no third-party software beyond its base image. The base images (Eclipse Temurin
on Ubuntu, httpd on Debian) carry their own notices: the Java runtime's in `/opt/java/openjdk/legal/`,
each package's in `/usr/share/doc/<package>/copyright`. Publishing images redistributes that
software too, some of it under the GPL, so source availability for it is part of publishing images.

### Note on update-center2's license

- The jenkins-infra/update-center2 repository has never contained a LICENSE file, in any of its
  1,970 commits since 2009. Neither its `pom.xml` nor its parent POM (`org.jenkins-ci:jenkins`)
  declares a license, and GitHub reports none.
- Of its 80 source files at 3.18.4, 10 carry the MIT License header (Sun Microsystems and other
  contributors, CloudBees, Daniel Beck). The other 70 carry no notice, and none carries any other
  license. Of the two files this project patches, `TieredUpdateSitesGenerator.java` carries the MIT
  header and `Popularities.java` carries none.
- The Jenkins project's governance document states: "Unless otherwise stated, all the code is under
  the MIT license." (https://www.jenkins.io/project/governance/)

This project therefore treats update-center2 as MIT-licensed, and the image's notices say so. The
two patches in `uc/patches/` are this project's own work, under its MIT license. A LICENSE file in
the upstream repository would settle the question beyond the governance statement.
