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

## How fixes are announced

Each fixed vulnerability is published as a GitHub Security Advisory on the repository, with a CVE
requested through GitHub, and ships in a release whose CHANGELOG entry is under `### Security`.
Watch the repository's releases and security advisories to hear of them.

We aim to publish a fix and its advisory within 90 days of confirming a report, coordinated with the
reporter; this is a volunteer project, so that is a goal, not a guarantee.

## Security design

The internet-facing `web` container never receives the signing key, and the root CA key is meant to
live offline. Every container runs as the operator's own user, none as root, on a read-only root
filesystem. Air-gapped, only the repo-adapter receives repository credentials, and it is reachable
only on an internal network. Repository content is untrusted input, and the public `status.json`
never holds credentials. Details: [Networks and trust boundaries](docs/architecture/overview.md#networks-and-trust-boundaries)
and [repo-adapter security](docs/architecture/repo-adapter.md#security).
