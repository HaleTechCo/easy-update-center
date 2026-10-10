# Verify a release

How to check that the images and files of an Easy Update Center for Jenkins release were built by
this project's Release workflow and have not been changed since.

## What is signed

Every release is built and signed by one workflow, `release.yml` on `main` of
`HaleTechCo/easy-update-center`, and by nothing else. It signs:

- **Each image** (`ghcr.io/haletechco/easy-update-center-uc` and `-web`), with cosign, keyless: the
  signature proves which workflow built it, with no key to manage.
- **Each image's build provenance and its SPDX SBOM**, as GitHub attestations. The SBOM lists the
  contents of the linux/amd64 image; both are attached to the multi-architecture image's digest.
- **`SHA256SUMS`**, which lists the checksum of every file a Release carries besides itself: the two
  air-gapped bundles and the SBOM files. It is signed with cosign `sign-blob`, and the signature is
  `SHA256SUMS.sigstore.json`.

The identity every check below expects is the workflow and its issuer:

```bash
IDENTITY=https://github.com/HaleTechCo/easy-update-center/.github/workflows/release.yml@refs/heads/main
ISSUER=https://token.actions.githubusercontent.com
```

## The images

You need [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3 or later (the
workflow signs with a cosign that writes the newer bundle format, which older versions cannot read)
and the [GitHub CLI](https://cli.github.com/) (`gh`), signed in with `gh auth login`, which
`gh attestation verify` needs. While the packages are still private (until the owner makes them
public), `docker login ghcr.io` is needed too. For each image, with the release's version in place
of `<version>` (for example `1.0.0`):

```bash
cosign verify --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" \
  ghcr.io/haletechco/easy-update-center-uc:<version>
gh attestation verify oci://ghcr.io/haletechco/easy-update-center-uc:<version> \
  --repo HaleTechCo/easy-update-center --signer-workflow HaleTechCo/easy-update-center/.github/workflows/release.yml
gh attestation verify oci://ghcr.io/haletechco/easy-update-center-uc:<version> \
  --repo HaleTechCo/easy-update-center --predicate-type https://spdx.dev/Document/v2.3
```

Then the same three commands with `easy-update-center-web` in place of `easy-update-center-uc`.

Success: `cosign verify` prints the checks it made and the signature's JSON, and each
`gh attestation verify` prints `Verification succeeded!` with the attestation it matched. The first
`gh` command checks the build provenance; the second the SBOM, which you can also read from the
Release as `easy-update-center-uc-<version>.spdx.json`.

A failure, whatever the message, means **do not run the image**: it was not signed by this
project's workflow, or it is not the image that was signed. Check the version and the image name for
a typo first; if they are right, report it as a security issue ([SECURITY.md](../../SECURITY.md)).

## The air-gapped bundle and other assets

Do this on the connected machine, before you carry the bundle across. From the
[Releases page](https://github.com/HaleTechCo/easy-update-center/releases), download the bundle for
the Docker host's architecture (`easy-update-center-<version>-airgapped-amd64.tar` or `-arm64.tar`),
`SHA256SUMS` and `SHA256SUMS.sigstore.json` into one folder, then, with `IDENTITY` and `ISSUER` set
as above:

```bash
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
```

The first command prints `Verified OK`: `SHA256SUMS` is the one the workflow signed. The second prints
`<file>: OK` for each file you downloaded and ignores the ones you did not. A `FAILED` line, or an
error from `cosign`, means the file is not the one the release published: delete it and download it
again, and if it still fails, do not use it.

The bundle also carries these commands in its `README.txt`.
