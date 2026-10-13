# Releasing

Maintainer guide: cutting a release of Easy Update Center for Jenkins with the Release workflow
(`.github/workflows/release.yml`), and recovering when a run fails. The repository must be set up
first: [GitHub setup](github-setup.md).

## Versions

Versions are SemVer, tagged `vX.Y.Z`. The first release is `0.1.0`. Until 1.0, a minor release may
change the interface (settings, commands, Compose files), and its CHANGELOG entry says how to move.

## Cut a release

1. On an up-to-date `main` with CI green, make the release commit on a branch:

   ```bash
   git switch -c release-X.Y.Z
   git fetch --tags
   python3 tools/release.py prepare X.Y.Z   # no v
   git commit -am "Release X.Y.Z"
   git push -u origin release-X.Y.Z
   ```

   `prepare` moves CHANGELOG's Unreleased entries under `## [X.Y.Z] - <today>` and sets
   `compose.yaml`'s images to `X.Y.Z`. It refuses a malformed version, one that is not above the
   highest release tag, one CHANGELOG already has, and an empty Unreleased section.
2. Open the pull request "Release X.Y.Z", let CI pass, and merge it. Check its diff: the CHANGELOG
   heading and the two image versions, nothing else.
3. Tag the commit the merge put on `main`, and push the tag. Only admins can push release tags
   ([GitHub setup](github-setup.md), step 5):

   ```bash
   git switch main && git pull
   git tag -s vX.Y.Z -m "Release X.Y.Z"     # -s signs it; -a if you have no signing key
   git push origin vX.Y.Z
   ```

The tag starts the Release workflow. Its jobs run in this order; each starts only if the ones before
it succeeded.

| Job | What it does | What it leaves behind |
|---|---|---|
| `check` | Refuses a run that is not on a release tag, a tag on a commit that is not on `main`, and, through `tools/release.py check`, a version not above the highest release, a CHANGELOG without its section, and a `compose.yaml` naming other images; works out whether this is the highest release overall and in its `X.Y`. | Nothing. |
| `ci` | Runs the CI workflow on the tagged commit. | Nothing. |
| `images` | Builds the `uc` and `web` images from the tag on a native runner per architecture (`ubuntu-24.04` for amd64, `ubuntu-24.04-arm` for arm64), with no build cache, and pushes each by digest. | Untagged per-architecture images on GHCR. |
| `manifests` | Joins the two architectures into one manifest list per image. | The images tagged `X.Y.Z`, plus `X.Y` and `latest` when this is the highest release in its `X.Y` and overall. |
| `sign` | Signs the manifest lists with cosign, attests their build provenance and their SBOMs, and writes the SBOM files. | Signatures and attestations on GHCR. |
| `bundle` | Builds the air-gapped bundle for each architecture on its own runner: the images, the source archive and a README. | Workflow artifacts, kept one day. |
| `release` | Writes `SHA256SUMS`, signs it, and creates the GitHub Release with the bundles, SBOMs and checksums, its notes taken from CHANGELOG. | The Release `vX.Y.Z`. |

How operators check what this produces: [Verify a release](../how-to/verify-a-release.md). How they
move to it: [Upgrade](../how-to/upgrade.md).

## When a run fails

- **`check` refused the tag:** nothing was published. Delete the tag
  (`git push --delete origin vX.Y.Z`, then `git tag -d vX.Y.Z`), fix the cause, and tag again:
  usually the tag was on the wrong commit, or the release pull request was not merged first.
- **A later job failed for a transient reason** (a network error, a registry hiccup): Actions >
  Release > Run workflow, and under "Use workflow from" pick the tag `vX.Y.Z`. That run publishes
  the release again from the tag, with the tag's own workflow, and replaces what the failed run left:
  the images, signatures, bundles and the Release's assets. `latest` and `X.Y` move only if the
  version is still the highest.
- **A later job failed because of the workflow or the code:** a run from the tag would fail the same
  way. Fix it on `main` and release the next patch version; say in its CHANGELOG entry that
  `X.Y.Z` was not published.

`manifests` moves `X.Y` and `latest` before signing, bundling and the Release. If a later job fails,
`latest` may point at an image whose signature or Release is missing until a run from the tag
completes.

## Security fixes in the base images

A release's images keep the base images and packages they were built with, so a fix reaches
operators only in a new release. Two things say when one is due:

- Dependabot's weekly `images` pull request moves the base images to their tags' newest digests
  (they are pinned: [Pinning policy](../reference/dependencies.md#pinning-policy)). Merge it when CI
  passes.
- The Scheduled workflow's `image-scan` job scans the latest release's images every week with
  Grype. When it finds a High or Critical vulnerability that has a fix, it opens the issue
  "Fixable vulnerabilities in the published images", listing them, or updates it; a later scan
  that finds none closes it. Before the first release it skips with a notice.

While that issue is open, merge Dependabot's pull request if one is waiting, then cut a patch
release with a CHANGELOG entry under `### Security` naming the vulnerabilities. A fix in a package
the base image ships arrives with the base image's next build, so if the rebuilt images still have
it, wait for Dependabot's next pull request.

## First-release rehearsal (0.1.0)

Only GitHub can show that the workflows work, so the first release is a rehearsal:

- [ ] Open a pull request and see `ci` pass; merge it.
- [ ] Cut `0.1.0` as above: the release pull request, its merge, the tag.
- [ ] `docker pull --platform linux/amd64` and `--platform linux/arm64` of both images work. New
      packages start private, so until step 8 of [GitHub setup](github-setup.md) makes them public,
      sign in first with `docker login ghcr.io`.
- [ ] `docker run --rm ghcr.io/haletechco/easy-update-center-uc:0.1.0 --version` prints `uc 0.1.0`.
- [ ] The commands of [Verify a release](../how-to/verify-a-release.md) pass, with cosign 3 or later
      and `gh` signed in (`gh auth login`), as that page's prerequisites say.
- [ ] Download the bundle for your host and run `docker load -i images.tar`. Then, in a checkout of
      `v0.1.0`, run the following; it builds only the test image.

      ```bash
      make it-airgapped PREBUILT=1 \
          UC_IMAGE=ghcr.io/haletechco/easy-update-center-uc:0.1.0 \
          WEB_IMAGE=ghcr.io/haletechco/easy-update-center-web:0.1.0
      ```

- [ ] Run the released generator end to end, which `make it-airgapped` does not (it runs only `pki`
      from `UC_IMAGE`; the generator and repo-adapter run from the test image built from
      your checkout). Follow [Deploy online](../getting-started/online.md), or
      [Deploy air-gapped](../getting-started/airgapped.md) with the bundle against your test
      repository, then check that `docker compose run --rm uc status` shows a `live_release` and
      `uc_version` 0.1.0.
- [ ] The Release's notes match CHANGELOG's 0.1.0 section, and their links open (they point at the
      files of the `v0.1.0` tag).
- [ ] Run the workflow again from the tag `v0.1.0` and check that it republishes cleanly.
- [ ] Make the packages public ([GitHub setup](github-setup.md), step 8).
