# Releasing

Maintainer guide: cutting a release of Easy Update Center for Jenkins with the Release workflow
(`.github/workflows/release.yml`), and recovering when a run fails. The repository must be set up
first: [GitHub setup](github-setup.md).

## Versions

Versions are SemVer, tagged `vX.Y.Z`. The first release is `0.1.0`. Until 1.0, a minor release may
change the interface (settings, commands, Compose files), and its CHANGELOG entry says how to move.

## Cut a release

1. `CHANGELOG.md`'s Unreleased section holds the release's entries, and CI is green on `main`.
2. Actions > Release > Run workflow: branch `main`, version `X.Y.Z` (no `v`), publish-only
   unchecked.

The jobs run in this order; each starts only if the ones before it succeeded.

| Job | What it does | What it leaves behind |
|---|---|---|
| `check` | `tools/release.py check`: refuses a run off `main`, a malformed version, a tag that already exists, a version not above the highest release, and an empty Unreleased section; works out whether this is the highest release overall and in its `X.Y`. | Nothing. |
| `ci` | Runs the CI workflow on the commit being released. | Nothing. |
| `prepare` | Moves CHANGELOG's Unreleased entries under the version and pushes the commit and the tag together. | The "Release X.Y.Z" commit and the `vX.Y.Z` tag on `main`. |
| `images` | Builds the `uc` and `web` images from the tag on a native runner per architecture (`ubuntu-24.04` for amd64, `ubuntu-24.04-arm` for arm64) and pushes each by digest. | Untagged per-architecture images on GHCR. |
| `manifests` | Joins the two architectures into one manifest list per image. | The images tagged `X.Y.Z`, plus `X.Y` and `latest` when this is the highest release in its `X.Y` and overall. |
| `sign` | Signs the manifest lists with cosign, attests their build provenance and their SBOMs, and writes the SBOM files. | Signatures and attestations on GHCR. |
| `bundle` | Builds the air-gapped bundle for each architecture on its own runner: the images, the source archive and a README. | Workflow artifacts, kept one day. |
| `release` | Writes `SHA256SUMS`, signs it, and creates the GitHub Release with the bundles, SBOMs and checksums, its notes taken from CHANGELOG. | The Release `vX.Y.Z`. |

How operators check what this produces: [Verify a release](../how-to/verify-a-release.md). How they
move to it: [Upgrade](../how-to/upgrade.md).

## When a run fails

- **Before `prepare` (`check` or `ci` failed):** nothing has changed. Fix the cause and run again.
- **`prepare`'s push refused because `main` moved:** run again. The commit and tag are pushed
  atomically, so neither landed.
- **A push refused naming a ruleset:** the GitHub Actions app is missing from that ruleset's bypass
  list, or its bypass mode is not Always ([GitHub setup](github-setup.md), steps 5 and 6).
- **After `prepare` (the tag exists):** fix the cause and run again with **publish-only** and the
  same version. It rebuilds and republishes the images, bundles and Release from the tag, and
  leaves CHANGELOG alone. `latest` and `X.Y` move only if the version is still the highest.

Two things to know about a run that failed after `manifests`:

- `manifests` moves `X.Y` and `latest` before signing, bundling and the Release. If a later job
  fails, `latest` may point at an image whose signature or Release is missing until the publish-only
  re-run completes.
- Publish-only builds the tag's tree but runs `main`'s current `ci.yml` and `release.yml`. A CI job
  or make target added after that tag can make the republish of an old release fail.

## First-release rehearsal (0.1.0)

Only GitHub can show that the workflows work, so the first release is a rehearsal. After the move
([GitHub setup](github-setup.md)):

- [ ] Open a pull request and see `ci` pass; merge it.
- [ ] Run Release with `0.1.0`.
- [ ] `docker pull --platform linux/amd64` and `--platform linux/arm64` of both images work. New
      packages start private, so until step 9 of [GitHub setup](github-setup.md) makes them public,
      sign in first with `docker login ghcr.io`.
- [ ] `docker run --rm ghcr.io/haletechco/easy-update-center-uc:0.1.0 --version` prints `uc 0.1.0`.
- [ ] The commands of [Verify a release](../how-to/verify-a-release.md) pass.
- [ ] Download the bundle for your host and run `docker load -i images.tar`. Then, in a checkout of
      `v0.1.0`, run the following; it builds only the test image.

      ```bash
      make it-airgapped PREBUILT=1 \
          UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:0.1.0 \
          WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:0.1.0
      ```

- [ ] The Release's notes match CHANGELOG's 0.1.0 section.
- [ ] Make the packages public ([GitHub setup](github-setup.md), step 9).
