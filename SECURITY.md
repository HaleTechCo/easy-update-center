# Security policy

How to report a vulnerability in this project, and what its security design relies on.

## Reporting a vulnerability

Open a **confidential issue** in this project's GitLab issue tracker (tick "This issue is
confidential" when creating it). Only maintainers can see it. Please include the version (commit),
the mode, what an attacker can do, and how to reproduce it. Do not open a public issue or merge
request for a vulnerability.

This is a volunteer project: reports are handled on a best-effort basis, with no response-time
guarantee. You will get an answer in the issue, and credit in the fix's changelog entry unless you
ask otherwise.

Vulnerabilities in Jenkins itself or in update-center2 belong to the Jenkins project:
https://www.jenkins.io/security/reporting/

## Supported versions

Only the latest commit on `master` is supported until versioned releases exist.

## Security design

- The signing key is mounted only into the `uc` and `scheduler` containers; the internet-facing
  `web` container never receives it. The root CA key is meant to live offline.
- Every container runs as a non-root user with a read-only root filesystem; `web` mounts the data
  volume read-only.
- The repo-adapter's unauthenticated file reads are reachable only on an internal network that
  `web` is not attached to; its listing requires a token `web` cannot read. Air-gapped, only the
  repo-adapter receives repository credentials.
- Plugin archives and POMs from the repository are treated as untrusted: reads are size-capped,
  only named entries are read, and XML with DOCTYPE or entity declarations is refused.
- `status.json` is public and carries no paths, URLs or error messages from the repository.

Details: [docs/architecture/overview.md](docs/architecture/overview.md#networks-and-trust-boundaries)
and [docs/architecture/repo-adapter.md](docs/architecture/repo-adapter.md#security).
