# secrets/

Everything here except this file is gitignored. Compose mounts each file under `/run/secrets/`.

First-time setup, `docker compose run --rm setup`, creates the signing keys and empty credential
files (empty means "not set"). What each file holds, which container reads it, and the modes it
needs on Linux: [docs/reference/secrets.md](../docs/reference/secrets.md).
