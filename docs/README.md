# Documentation

Which page to read for which job. New here? Start with the [README](../README.md), then a getting-started page.

## Getting started

- [Requirements](getting-started/requirements.md) - host, software, accounts, network, sizing
- [Deploy online](getting-started/online.md) - with internet access; plugins from repo.jenkins-ci.org or your own repository
- [Deploy air-gapped](getting-started/airgapped.md) - nothing fetched from the internet; plugins from your Artifactory or Artifact Keeper

## How-to guides

- [Configure Jenkins controllers](how-to/configure-jenkins.md)
- [Configure with uc.toml](how-to/configure-with-uc-toml.md) - settings in a file, and what each one ends up as
- [Stock an air-gapped repository](how-to/stock-airgapped-repository.md)
- [Procure missing POMs, dependencies and cores](how-to/procure-missing-files.md) - stage, review, push and clean up with `uc procure`
- [Run procurement from a pipeline](how-to/run-procurement-from-a-pipeline.md) - Jenkins, GitLab CI or GitHub Actions, with an approval before the push
- [Keep upstream data fresh](how-to/keep-upstream-data-fresh.md)
- [Enable TLS](how-to/enable-tls.md)
- [Proxy plugin downloads](how-to/proxy-downloads.md)
- [Rotate the signing certificate](how-to/rotate-signing-certificate.md)
- [Block plugins and add warnings](how-to/block-plugins-and-add-warnings.md)
- [Generate and roll back](how-to/generate-and-roll-back.md)
- [Monitor](how-to/monitor.md)
- [Upgrade](how-to/upgrade.md)
- [Verify a release](how-to/verify-a-release.md) - the images' signatures and attestations, and the bundle's checksums

## Reference

- [Configuration](reference/configuration.md) - every setting
- [uc commands](reference/cli.md) - and the setup service
- [Secrets](reference/secrets.md)
- [status.json](reference/status-json.md)
- [Site layout](reference/site-layout.md) - every URL served
- [Network](reference/network.md) - ports and outbound hosts
- [Dependencies](reference/dependencies.md) - what it is built from, with licenses

## Architecture

- [Overview](architecture/overview.md)
- [Generation pipeline](architecture/generation-pipeline.md)
- [Request routing](architecture/request-routing.md)
- [Modes](architecture/modes.md)
- [repo-adapter](architecture/repo-adapter.md)
- [Signing and trust](architecture/signing.md)

## Development

- [Contributing](../CONTRIBUTING.md) - setup, tests, lint, pull requests
- [Upgrading update-center2](development/upgrading-upstream.md)

## Troubleshooting

- [Troubleshooting](troubleshooting.md)
