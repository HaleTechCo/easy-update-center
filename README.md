# Jenkins update center, self-hosted

The same update center Jenkins uses (`updates.jenkins.io`), in two containers:

- **uc**: runs the upstream `update-center2` generator (3.18.4, one small patch), signs everything
  with your own PKI, validates each release and publishes it atomically. It keeps the last five
  releases for rollback.
- **web**: Apache serving the result, including the `?version=` routing to `dynamic-*` tiers, every
  upstream JSON file, and download redirects to get.jenkins.io or your Artifactory.

Two modes:
- `online` reads `repo.jenkins-ci.org`.
- `airgapped` reads your internal Artifactory and skips whatever it cannot reach.

```bash
make build
cp .env.online.example .env      # or .env.airgapped.example; set PUBLIC_URL
make pki-init secrets-placeholders
# write secrets/artifactory_username and secrets/artifactory_password
docker compose up -d
```

- Operations: [docs/operations.md](docs/operations.md)
- Upgrading upstream: [docs/upgrading.md](docs/upgrading.md)
- Design: [docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md](docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md)
