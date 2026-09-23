# secrets/

Everything here except this file is gitignored. Compose mounts each file under `/run/secrets/`.

| File | Required | Content |
|---|---|---|
| `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | from `make pki-init` (move `root/root-ca.key` offline afterwards) |
| `repo_username`, `repo_password` | yes | Artifactory: an account that can run AQL on the repository; Artifact Keeper: a user name and an API token. With the repo-adapter on, only its container receives them. |
| `github_username`, `github_token` | online, recommended | GitHub user and token; empty files mean "not set" |
| `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only Artifactory account |

Create the optional files empty with `make secrets-placeholders`. On Linux the containers read
these files as uid 10001 (uc) and uid 33 (web); see docs/operations.md, "File ownership on Linux".
