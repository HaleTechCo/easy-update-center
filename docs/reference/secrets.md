# Secrets reference

Every secret file, which container receives it, and the ownership and permissions it needs.

## Secret files

Secrets are files in `secrets/`, which is gitignored except for its README. Compose mounts each one
under `/run/secrets/` in the containers that need it. An empty file means "not set"; first-time
setup (`docker compose run --rm setup`) and `docker compose run --rm setup secrets` create every
missing credential file empty, so Compose can mount it.

| File | Required | Content |
|---|---|---|
| `update-center.key`, `update-center.cert` | yes | The signing key and certificate, from first-time setup (`uc pki init`). |
| `root-ca.crt` | yes | The root CA certificate, from first-time setup. Not secret: every controller gets a copy. |
| `repo_username`, `repo_password` | yes | Online: a Jenkins community account. Air-gapped, Artifactory: an account with read permission on the repository (no admin rights needed; its listing queries return what it can read), with its password or an access token as `repo_password`. Air-gapped, Artifact Keeper: a user name and an API token. |
| `github_username`, `github_token` | online, recommended | A GitHub user and token; see [GitHub token](#github-token). |
| `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only account on the download backend. |
| `repo_push_auth` | only for `uc procure push` and `clean` | `user:token` of an account that can write to the repository (push writes with it, clean only reads) (Artifact Keeper: a user name and an API token with `write:artifacts`). See [Procure missing files](../how-to/procure-missing-files.md). |
| `root/root-ca.key` | no: move it offline | The root CA's private key, from first-time setup. Needed only to issue the next signing certificate. See [Where the root key lives](../how-to/rotate-signing-certificate.md#where-the-root-key-lives). |
| `repo-ca.crt` | only for a repository behind a private CA | The CA bundle mounted by the `compose.repo-ca.yaml` override ([Repository behind a private CA](../getting-started/airgapped.md#repository-behind-a-private-ca)). |
| `proxy-ca.crt` | only for a proxy-mode backend behind a private CA | The CA bundle mounted by the `compose.proxy-ca.yaml` override ([Backend with a private CA](../how-to/proxy-downloads.md#backend-with-a-private-ca)). |

Write credential files with `printf '%s'`, which adds no newline (a trailing newline is tolerated):

```bash
printf '%s' 'svc-easy-update-center' > secrets/repo_username
```

## Which container gets which secret

| Secret | `scheduler`, `uc` | `repo-adapter` | `web` |
|---|---|---|---|
| `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | no | no |
| `repo_username`, `repo_password` | online: yes; air-gapped: no | yes | no |
| `github_username`, `github_token` | yes | no | no |
| `download_proxy_auth` | no | no | yes (read only when `DOWNLOAD_ACCESS=proxy`) |
| `repo_push_auth` | `uc` only (never `scheduler`) | no | no |
| `repo-ca.crt` (with `compose.repo-ca.yaml`) | `uc` only | yes | no |
| `proxy-ca.crt` (with `compose.proxy-ca.yaml`) | no | no | yes |

Only the one-shot `uc` service, which runs when you call it, receives `repo_push_auth`; the
scheduler, which runs unattended, never does.

Air-gapped, `compose.airgapped.yaml` leaves the repository credentials out of `scheduler` and `uc`:
only the repo-adapter talks to the repository, and the others authenticate to the adapter with a
token it creates in `/data/repo-adapter/token`. If you set `REPO_ADAPTER=off` in air-gapped mode,
add `repo_username` and `repo_password` back to the `secrets` lists of `scheduler` and `uc` in
`compose.airgapped.yaml`.

The web container never receives the signing key.

## File modes

Every file in `secrets/` is yours, and every container runs as `UC_UID`:`UC_GID`: on Linux, set them
to your `id -u` and `id -g` in `.env`, so the containers read the files as you. `setup` sets the
modes:

| Files | Mode |
|---|---|
| `update-center.key`, `repo_username`, `repo_password`, `github_username`, `github_token`, `download_proxy_auth`, `repo_push_auth` | 0600 |
| `update-center.cert`, `root-ca.crt`, `repo-ca.crt`, `proxy-ca.crt` | 0644 |
| `root/`, `root/root-ca.key` | 0700, 0600 |

Writing into an existing file, as `printf '%s' '...' > secrets/repo_password` does, keeps its mode.
After **replacing** a file (copying a new one over it, or saving it from an editor that writes a new
file), run:

```bash
docker compose run --rm setup secrets
```

It is idempotent: it creates any missing credential file empty and sets the modes again. Docker
Desktop (macOS, Windows) ignores modes and ownership, so they do not matter there. How `setup` runs,
and its other commands: [The setup service](cli.md#the-setup-service).

## GitHub token

Online, the generator asks GitHub's GraphQL API for each plugin repository's topics, which become
the plugin's labels. Give it a classic personal access token with **no scopes**, as
`github_username` and `github_token`; it is sent as basic authentication. Without one the run logs a
warning and labels from GitHub topics are missing; labels from upstream's
`label-definitions.properties` still apply. In air-gapped mode GitHub is unreachable by design, so
labels from topics are missing either way.
