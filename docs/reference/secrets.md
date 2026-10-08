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
printf '%s' 'svc-jenkins-uc' > secrets/repo_username
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

## File ownership on Linux

Compose mounts secret files with their host ownership and permissions. The uc image's containers
(`scheduler`, `uc`, `repo-adapter`) run as uid 10001 with group 10001, the web container as uid 33
with group 33. `secrets/` stays yours, and each container reads its own files through its group:

| Files | Owner | Group | Mode |
|---|---|---|---|
| `update-center.key`, `repo_username`, `repo_password`, `github_username`, `github_token`, `repo_push_auth` | you | 10001 | 0640 |
| `update-center.cert`, `root-ca.crt` | you | 10001 | 0644 |
| `download_proxy_auth` | you | 33 | 0640 |
| `repo-ca.crt`, `proxy-ca.crt`, `README.md` | you | yours | unchanged (0644) |
| `root/`, `root/root-ca.key` | you | yours | 0700, 0600 |

The `setup` Compose service sets this up as root, so no step needs `sudo`, and you can write
credentials before or after creating the keys. Its entrypoint, `uc-setup`, runs
`uc/bin/secrets-permissions.sh` (in the uc image) in first-time setup
(`docker compose run --rm setup`) and in its `secrets`, `pki-init` and `pki-rotate` commands. "You"
is the owner of `./config`: every checkout has that directory, so `setup` takes the host user from
it. Writing into an existing file, as `printf '%s' '...' > secrets/repo_password` does, keeps its
permissions. After **replacing** a file (copying a new one over it, or saving it from an editor that
writes a new file), run:

```bash
docker compose run --rm setup secrets      # or: make secrets
```

It is idempotent: it creates any missing credential file empty and sets the permissions again. It
also hands back a `secrets/` that an older version of `make pki-init` gave wholesale to uid 10001.
Docker Desktop (macOS, Windows) ignores ownership, so the owners and modes above do not matter
there.

`setup` runs only when you call it, with no network and no Compose secrets, and mounts only
`./secrets`, `./procurement` and `./config` (at `/setup/secrets`, `/setup/procurement` and
`/setup/config`), and the directory `make pki-rotate ROOT=<dir>` or `make pki-init OUT=<dir>`
names. Its commands and their `make` shortcuts (`make setup`, `make secrets`, `make pki-init`,
`make pki-rotate ROOT=<dir>`; `make secrets-placeholders` and `make secrets-permissions` are
aliases of `make secrets`): [The setup service](cli.md#the-setup-service). It reads `.env` (or the
file `UC_ENV_FILE` names) as the other services do, so quotes in it are handled the same way, and
the file must exist: create it first (`cp .env.online.example .env`), or Compose stops with
`env file ... not found`.

## GitHub token

Online, the generator asks GitHub's GraphQL API for each plugin repository's topics, which become
the plugin's labels. Give it a classic personal access token with **no scopes**, as
`github_username` and `github_token`; it is sent as basic authentication. Without one the run logs a
warning and labels from GitHub topics are missing; labels from upstream's
`label-definitions.properties` still apply. In air-gapped mode GitHub is unreachable by design, so
labels from topics are missing either way.
