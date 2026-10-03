# Network reference

Every port, network and outbound host, by mode and service, ready to paste into a firewall request.

## Ports

| Service | Container port | Published as | Notes |
|---|---|---|---|
| `web` | 8080 | `HTTP_PORT` (default 8080) | HTTP. Always on; put your reverse proxy in front of it, or use 8443. |
| `web` | 8443 | `HTTPS_PORT` (default 8443) | HTTPS, only when `TLS_CERT_FILE` and `TLS_KEY_FILE` are set. |
| `repo-adapter` | 8081 (`REPO_ADAPTER_PORT`) | not published | Reachable only on the internal `repo` network. Its healthcheck and the default `REPO_ADAPTER_URL` follow the port. |

Jenkins controllers need to reach only the web container, at `PUBLIC_URL`.

## Compose networks

| Service | `default` | `repo` (internal) | `repo-egress` |
|---|---|---|---|
| `web` | yes | no | no |
| `web-config` | no | no | no |
| `setup` | no | no | no |
| `scheduler`, `uc` | yes | yes | no |
| `repo-adapter` | no | yes | yes |

`web-config` has no network at all (`network_mode: none`): it only reads settings and writes a file.
`setup`, which the `make pki-init` and `make secrets-*` targets run, has none either.

`repo` is an internal network: nothing on it can reach outside the host, and the web container is
not on it. The repo-adapter reaches the repository through `repo-egress`. `make compose-check`
asserts this layout.

## Outbound: online

| Host | From | Why |
|---|---|---|
| `repo.jenkins-ci.org` | `scheduler`, `uc` | The plugin and core listing, POMs, manifests and descriptions. Needs a Jenkins community account. |
| `api.github.com` | `scheduler`, `uc` | Resolving `RESOURCES_REF` to a commit; GitHub topics for plugin labels. |
| `codeload.github.com` | `scheduler`, `uc` | Downloading upstream `resources/` (security warnings, suspensions) at that commit. |
| `raw.githubusercontent.com` | `scheduler`, `uc` | Plugin popularity (`POPULARITY_JSON_URL`). |
| `updates.jenkins.io` | `scheduler`, `uc` | Tool installer metadata (`TOOL_INSTALLERS_URL`). It redirects to `mirrors.updates.jenkins.io`. |
| `reports.jenkins.io` | `scheduler`, `uc` | Issue trackers, maintainers and health scores. Optional: the run warns and continues without them. |
| `PROCUREMENT_SOURCE_URL`'s host (default `repo.jenkins-ci.org`) | `uc` | Only for `uc doctor --download-*`: the files it stages. |
| `REPO_URL`'s host, when it is your own repository | `scheduler`, `uc` (`REPO_ADAPTER` off); `repo-adapter` (`REPO_ADAPTER` on, always for Artifact Keeper); `uc` for `uc doctor --push` and `--clean` | The listing, POMs and manifests instead of repo.jenkins-ci.org, the uploads of `uc doctor --push`, and the checks of both. |

`uc doctor --fetch` reads from `repo.jenkins-ci.org` too. The air-gapped override also blocks
`github.com` as a precaution. Behind an outbound HTTP proxy, set it for
both halves: `JAVA_TOOL_OPTIONS` (`-Dhttps.proxyHost`, `-Dhttps.proxyPort`) for the generator, and
`HTTPS_PROXY` with `NO_PROXY=repo-adapter` for the `uc` code, which fetches the upstream resources and
tool installers itself.

## Outbound: air-gapped

| Host | From | Why |
|---|---|---|
| `REPO_URL`'s host | `repo-adapter` | The listing, POMs, manifests and archives. |
| `RESOURCES_URL`'s host | `scheduler`, `uc` | Only when `RESOURCES_SOURCE=url`. |
| `POPULARITY_JSON_URL`'s host | `scheduler`, `uc` | Only when set. |
| `TOOL_INSTALLERS_URL`'s host | `scheduler`, `uc` | Only when set. |
| `DOWNLOAD_BACKEND_URL`'s host | `web` | Only with `DOWNLOAD_ACCESS=proxy`. |
| `PROCUREMENT_SOURCE_URL`'s host | `uc` | Only for `uc doctor --download-*`, and only when set. |
| `REPO_URL`'s host | `uc` | Only for `uc doctor --push` and `--clean`. |

`compose.airgapped.yaml` maps `api.github.com`, `github.com`, `codeload.github.com`,
`raw.githubusercontent.com`, `reports.jenkins.io` and `updates.jenkins.io` to 127.0.0.1 in the
`scheduler`, `uc` and `repo-adapter` containers, so any attempt to reach them fails at once instead
of waiting for a firewall that silently drops packets. See
[Fast failure for blocked hosts](../architecture/modes.md#fast-failure-for-blocked-hosts).

## Outbound: controllers

| Mode | Metadata from | Plugin and core downloads from |
|---|---|---|
| online | `PUBLIC_URL` | `get.jenkins.io` (and the mirrors it redirects to) |
| air-gapped, `DOWNLOAD_ACCESS=redirect` | `PUBLIC_URL` | `DOWNLOAD_BACKEND_URL`, without credentials |
| any, `DOWNLOAD_ACCESS=proxy` | `PUBLIC_URL` | `PUBLIC_URL` only: the web container fetches from the backend |

Tool installers in `/updates/` still point at the internet for their downloads.

## Outbound: image builds

`make build` needs internet access. Without it, build elsewhere and move the images: see
[Build the images](../getting-started/airgapped.md#2-build-the-images).

| Host | Why |
|---|---|
| Docker Hub (`registry-1.docker.io` and its CDN) | Base images: `maven:3.9-eclipse-temurin-21`, `eclipse-temurin:21-jre`, `httpd:2.4`. |
| Ubuntu and Debian package archives | Packages installed in each image. |
| `github.com` (and the release-asset host it redirects to) | Cloning update-center2 at its pinned tag; the supercronic release binary. |
| `repo.maven.apache.org`, `repo.jenkins-ci.org` | Dependencies for building update-center2 with Maven. |
