# Verify a release

How to check that the images and files of an Easy Update Center for Jenkins release were built by
this project's Release workflow and have not been changed since.

## What is signed

Every release is built and signed by one workflow, `release.yml` of
`HaleTechCo/easy-update-center`, running on the release's tag `v<version>`, and by nothing else. It
signs:

- **Each image** (`ghcr.io/haletechco/easy-update-center-uc` and `-web`), with cosign, keyless: the
  signature proves which workflow built it, with no key to manage.
- **Each image's build provenance and its SPDX SBOM**, as attestations in the registry next to the
  image, signed by the same workflow. The SBOM lists the contents of the linux/amd64 image; both are
  attached to the multi-architecture image's digest.
- **`SHA256SUMS`**, which lists the checksum of every file a Release carries besides itself: the two
  air-gapped bundles and the SBOM files. It is signed with cosign `sign-blob`, and the signature is
  `SHA256SUMS.sigstore.json`.

Release candidates (`v<version>` such as `v0.2.0-rc.1`) are signed the same way; verify them with
their own tag.

The identity every check below expects is the workflow on that tag, and its issuer. With the
release's version in place of `<version>` (for example `1.0.0`):

```bash
IDENTITY=https://github.com/HaleTechCo/easy-update-center/.github/workflows/release.yml@refs/tags/v<version>
ISSUER=https://token.actions.githubusercontent.com
```

## The images

You need only [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3 or later (the
workflow signs with a cosign that writes the newer bundle format, which older versions cannot read).
No GitHub account and no `gh` are needed. On Windows, `winget install Sigstore.Cosign` installs it as
`cosign-windows-amd64.exe`: run the commands below with that name, or copy it to `cosign.exe`.

A tag can be moved later; a digest cannot. So read each image's digest from its tag once, then
verify and run that digest, never the tag again. For the uc image, with the release's version in
place of `<version>` (for example `1.0.0`):

```bash
docker buildx imagetools inspect ghcr.io/haletechco/easy-update-center-uc:<version> --format '{{.Manifest.Digest}}'
```

It prints the digest: `sha256:` followed by 64 hex digits. Put all of it, `sha256:` included, in
place of `<digest>`:

```bash
UC=ghcr.io/haletechco/easy-update-center-uc@<digest>
cosign verify --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" "$UC"
cosign verify-attestation --type slsaprovenance1 --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" "$UC"
cosign verify-attestation --type https://spdx.dev/Document/v2.3 --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" "$UC"
```

Then the same for the web image: its digest from `ghcr.io/haletechco/easy-update-center-web:<version>`,
and the three checks against `ghcr.io/haletechco/easy-update-center-web@<digest>`.

Success: each command exits 0. `cosign verify` prints the checks it made and the signature's JSON;
each `cosign verify-attestation` prints a "Verification for ..." line and then the verified
attestation's payload as JSON. The first `verify-attestation` command checks the build provenance;
the second the SBOM, which you can also read from the Release as
`easy-update-center-uc-<version>.spdx.json`.

A non-zero exit or any error, whatever the message, means **do not run the image**: it was not
signed by this project's workflow, or it is not the image that was signed. Check the version and the image name for
a typo first; if they are right, report it as a security issue ([SECURITY.md](../../SECURITY.md)).

## Run the digests you verified

Pin the two digests you verified in `.env`, so `docker compose pull` fetches exactly those images:

```bash
UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc@<digest>
WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web@<digest>
```

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
