# Security policy

How to report a vulnerability in this project, and what its security design relies on.

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: the **Security** tab of
[the repository](https://github.com/HaleTechCo/easy-update-center), **Report a vulnerability**
(https://github.com/HaleTechCo/easy-update-center/security/advisories/new). Only maintainers can see
it. Please include the version (commit), the mode, what an attacker can do, and how to reproduce it.
Do not open a public issue or pull request for a vulnerability.

This is a volunteer project: reports are handled on a best-effort basis, with no response-time
guarantee. You will get an answer in the report, and credit in the fix's changelog entry unless you
ask otherwise.

Vulnerabilities in Jenkins itself or in update-center2 belong to the Jenkins project:
https://www.jenkins.io/security/reporting/

## Supported versions

The latest release. Fixes ship in a new release; earlier releases get no backports.

## Security design

- The signing key is mounted only into the `uc` and `scheduler` containers, and into the on-demand
  `setup` service, which mounts `./secrets` while it runs; the internet-facing `web` container never
  receives it. The root CA key is meant to live offline.
- Every container runs as a non-root user with a read-only root filesystem, except the setup steps
  an operator starts, which run as root and receive no Compose secrets: the `setup` service (with no
  network) and the containers `make procure-stage`, `procure-record` and `procure-clean` use to hand
  `./procurement` over. `web` mounts the data volume read-only.
- The repo-adapter's unauthenticated file reads are reachable only on an internal network that
  `web` is not attached to; its listing requires a token `web` cannot read. Air-gapped, only the
  repo-adapter receives repository credentials.
- Plugin archives and POMs from the repository are treated as untrusted: reads are size-capped,
  only named entries are read, and XML with DOCTYPE or entity declarations is refused.
- `status.json` is public. It never holds credentials, and a failed run shows only the error's
  class, never its message. Its `warnings` are short messages that can name plugin coordinates or
  the URL of an upstream-data source that failed to download; restrict `/status.json` at your proxy
  if those URLs are sensitive.

Details: [docs/architecture/overview.md](docs/architecture/overview.md#networks-and-trust-boundaries)
and [docs/architecture/repo-adapter.md](docs/architecture/repo-adapter.md#security).
