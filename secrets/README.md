# secrets/

Everything here except this file is gitignored. Compose mounts each file under `/run/secrets/`.

| File | Required | Content |
|---|---|---|
| `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | from `make pki-init` (move `root/root-ca.key` offline afterwards) |
| `artifactory_username`, `artifactory_password` | yes | account that can run AQL on the source repository |
| `github_username`, `github_token` | online, recommended | GitHub user and token; empty files mean "not set" |
| `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only Artifactory account |

Create the optional files empty with `make secrets-placeholders`. On Linux the containers read
these files as uid 10001 (uc) and uid 33 (web); see docs/operations.md, "File ownership on Linux".
