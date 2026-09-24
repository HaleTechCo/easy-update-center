# secrets/

Everything here except this file is gitignored. Compose mounts each file under `/run/secrets/`.

`make pki-init` creates the signing keys and `make secrets-placeholders` creates empty credential
files (empty means "not set"). What each file holds, which container reads it, and the ownership it
needs on Linux: [docs/reference/secrets.md](../docs/reference/secrets.md).
