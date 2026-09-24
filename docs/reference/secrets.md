# Secrets reference

Every secret file, which container receives it, and the ownership and permissions it needs.

## Secret files

Secrets are files in `secrets/`, which is gitignored except for its README. Compose mounts each one
under `/run/secrets/` in the containers that need it. An empty file means "not set";
`make secrets-placeholders` creates every optional file empty, so Compose can mount it.

| File | Required | Content |
|---|---|---|
| `update-center.key`, `update-center.cert` | yes | The signing key and certificate, from `make pki-init`. |
| `root-ca.crt` | yes | The root CA certificate, from `make pki-init`. Not secret: every controller gets a copy. |
| `repo_username`, `repo_password` | yes | Online: a Jenkins community account. Air-gapped: an account that can read the repository; for Artifactory it must be able to run AQL queries (a non-admin account works), for Artifact Keeper `repo_password` is an API token. |
| `github_username`, `github_token` | online, recommended | A GitHub user and token; see [GitHub token](#github-token). |
| `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only account on the download backend. |

`make pki-init` also writes `secrets/root/root-ca.key`, the root CA's private key. It is needed only
to issue the next signing certificate: move it to offline storage. See
[Where the root key lives](../how-to/rotate-signing-certificate.md#where-the-root-key-lives).

Write credential files without a trailing newline, for example:

```bash
printf '%s' 'svc-jenkins-uc' > secrets/repo_username
```

## Which container gets which secret

| Secret | `scheduler`, `uc` | `repo-adapter` | `web` |
|---|---|---|---|
| `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | no | no |
| `repo_username`, `repo_password` | online: yes; air-gapped: no | yes | no |
| `github_username`, `github_token` | yes | no | no |
| `download_proxy_auth` | no | no | yes (read only when `DOWNLOAD_ACCESS=proxy`) |

Air-gapped, `compose.airgapped.yaml` leaves the repository credentials out of `scheduler` and `uc`:
only the repo-adapter talks to the repository, and the others authenticate to the adapter with a
token it creates in `/data/repo-adapter/token`. If you set `REPO_ADAPTER=off` in air-gapped mode,
add `repo_username` and `repo_password` back to the `secrets` lists of `scheduler` and `uc` in
`compose.airgapped.yaml`.

The web container never receives the signing key.

## File ownership on Linux

Compose mounts secret files with their host ownership and permissions. The uc image's containers
(`scheduler`, `uc`, `repo-adapter`) run as uid 10001, the web container as uid 33:

```bash
sudo chown 10001:10001 secrets/update-center.* secrets/root-ca.crt secrets/repo_* secrets/github_*
sudo chmod 0400 secrets/update-center.key secrets/repo_password secrets/github_token
sudo chown 33:33 secrets/download_proxy_auth && sudo chmod 0400 secrets/download_proxy_auth
```

`make pki-init` already gives the PKI files to uid 10001. Docker Desktop (macOS, Windows) needs no
changes.

## GitHub token

Online, the generator asks GitHub's GraphQL API for each plugin repository's topics, which become
the plugin's labels. Give it a classic personal access token with **no scopes**, as
`github_username` and `github_token`; it is sent as basic authentication. Without one the run logs a
warning and labels from GitHub topics are missing; labels from upstream's
`label-definitions.properties` still apply. In air-gapped mode GitHub is unreachable by design, so
labels from topics are missing either way.
